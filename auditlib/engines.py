"""
The AI assistants we ask, each with web search switched on.

Every engine takes a question and returns an Answer: the text a customer would read,
plus the sources it cited. An engine is used only when its API key is set, so the
audit runs with whatever subset you have.

API answers are not identical to what the consumer apps show (the apps add memory,
personalisation and their own prompts). The API with web search is the closest
reproducible proxy, which is why every question is asked several times and reported
as a rate, never as "you are / aren't recommended".

  claude      ANTHROPIC_API_KEY (or an `ant auth login` profile)   AUDIT_CLAUDE_MODEL
  openai      OPENAI_API_KEY                                       AUDIT_OPENAI_MODEL
  perplexity  PERPLEXITY_API_KEY                                   AUDIT_PERPLEXITY_MODEL
  gemini      GEMINI_API_KEY                                       AUDIT_GEMINI_MODEL
  fake        nothing — canned answers, for testing the pipeline offline
"""
import hashlib
import os
from dataclasses import asdict, dataclass, field

import requests

TIMEOUT = 120
MADRID = {"city": "Madrid", "region": "Madrid", "country": "ES", "timezone": "Europe/Madrid"}


@dataclass
class Answer:
    text: str
    sources: list = field(default_factory=list)     # [{"url": ..., "title": ...}]
    model: str = ""
    note: str = ""                                  # refusal, truncation, etc.

    def to_dict(self):
        return asdict(self)


def _dedupe(sources):
    seen, out = set(), []
    for s in sources:
        u = s.get("url") or ""
        if u and u not in seen:
            seen.add(u)
            out.append({"url": u, "title": s.get("title") or ""})
    return out


class Engine:
    name = ""
    env = ""
    model_env = ""
    default_model = ""

    @property
    def model(self):
        return os.environ.get(self.model_env, self.default_model)

    def available(self):
        return bool(os.environ.get(self.env))

    def ask(self, question: str) -> Answer:
        raise NotImplementedError


class Claude(Engine):
    name, env, model_env, default_model = "claude", "ANTHROPIC_API_KEY", "AUDIT_CLAUDE_MODEL", "claude-opus-5-5"

    def available(self):
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                    or os.path.isdir(os.path.expanduser("~/.config/anthropic")))

    def ask(self, question):
        import anthropic
        client = anthropic.Anthropic()
        messages = [{"role": "user", "content": question}]
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5,
                  "user_location": {"type": "approximate", **MADRID}}]
        # Web search can pause a long turn; resume it by sending the partial turn back.
        for _ in range(4):
            resp = client.beta.messages.create(
                model=self.model, max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                output_config={"effort": "medium"},
                tools=tools, messages=messages,
            )
            if resp.stop_reason != "pause_turn":
                break
            messages = messages + [{"role": "assistant", "content": resp.content}]

        if resp.stop_reason == "refusal":
            return Answer("", [], resp.model, "refusal")
        text, sources = [], []
        for b in resp.content:
            if b.type == "text":
                text.append(b.text)
                for c in getattr(b, "citations", None) or []:
                    sources.append({"url": getattr(c, "url", ""), "title": getattr(c, "title", "")})
            elif b.type == "web_search_tool_result" and isinstance(b.content, list):
                for r in b.content:
                    sources.append({"url": getattr(r, "url", ""), "title": getattr(r, "title", "")})
        note = "max_tokens" if resp.stop_reason == "max_tokens" else ""
        return Answer("".join(text).strip(), _dedupe(sources), resp.model, note)


class OpenAI(Engine):
    """ChatGPT's search via the Responses API. Set AUDIT_OPENAI_MODEL to whatever model
    ChatGPT currently answers with; the default is only a starting point."""
    name, env, model_env, default_model = "openai", "OPENAI_API_KEY", "AUDIT_OPENAI_MODEL", "gpt-5"

    def ask(self, question):
        r = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {os.environ[self.env]}"},
            json={"model": self.model, "input": question,
                  "tools": [{"type": "web_search", "user_location": {"type": "approximate", **MADRID}}]},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()
        text, sources = [], []
        for item in d.get("output", []):
            if item.get("type") != "message":
                continue
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text.append(c.get("text", ""))
                    for a in c.get("annotations", []):
                        if a.get("type") == "url_citation":
                            sources.append({"url": a.get("url"), "title": a.get("title")})
        return Answer("".join(text).strip(), _dedupe(sources), d.get("model", self.model))


class Perplexity(Engine):
    name, env, model_env, default_model = "perplexity", "PERPLEXITY_API_KEY", "AUDIT_PERPLEXITY_MODEL", "sonar"

    def ask(self, question):
        r = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {os.environ[self.env]}"},
            json={"model": self.model, "messages": [{"role": "user", "content": question}]},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()
        text = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
        sources = [{"url": s.get("url"), "title": s.get("title")} for s in d.get("search_results") or []]
        sources += [{"url": u, "title": ""} for u in d.get("citations") or [] if isinstance(u, str)]
        return Answer(text.strip(), _dedupe(sources), d.get("model", self.model))


class Gemini(Engine):
    """Gemini with Google Search grounding. Source URLs come back as Google redirect
    links; the title field carries the real domain, which is what matching uses."""
    name, env, model_env, default_model = "gemini", "GEMINI_API_KEY", "AUDIT_GEMINI_MODEL", "gemini-2.5-flash"

    def ask(self, question):
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            headers={"x-goog-api-key": os.environ[self.env]},
            json={"contents": [{"role": "user", "parts": [{"text": question}]}],
                  "tools": [{"google_search": {}}]},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        cand = (r.json().get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        sources = [{"url": ch.get("web", {}).get("uri"), "title": ch.get("web", {}).get("title")}
                   for ch in cand.get("groundingMetadata", {}).get("groundingChunks", [])]
        return Answer(text.strip(), _dedupe(sources), self.model)


class Fake(Engine):
    """Deterministic canned answers so the whole pipeline can be tested without keys."""
    name, env, default_model = "fake", "", "fake-1"
    POOL = [
        ("Clínica Dental Ferrocarril", "clinicadentalferrocarril.com"),
        ("Vitaldent", "vitaldent.com"),
        ("Clínica Dental Sonrisa Madrid", ""),
        ("Centro Odontológico Arganzuela", "centroodontologicoarganzuela.com"),
        ("Sanitas Dental", "sanitas.es"),
        ("Clínica Smiling", ""),
    ]

    def available(self):
        return True

    def ask(self, question):
        h = int(hashlib.sha256(question.encode()).hexdigest(), 16)
        picks = [self.POOL[(h >> (4 * i)) % len(self.POOL)] for i in range(4)]
        picks = list(dict.fromkeys(picks))[:3]
        lines = ["Estas son algunas opciones bien valoradas:", ""]
        lines += [f"{i}. **{n}**: buenas opiniones en Google." for i, (n, _) in enumerate(picks, 1)]
        lines += ["", "Te recomiendo consultar Doctoralia para comparar precios."]
        return Answer("\n".join(lines), [{"url": f"https://{d}/", "title": n} for n, d in picks if d], self.model)


ENGINES = {e.name: e for e in (Claude(), OpenAI(), Perplexity(), Gemini(), Fake())}
