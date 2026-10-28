"""
Turn an AI answer into the list of businesses it recommends, in order.

Claude does this with a fixed JSON schema, because answers mix lists, prose and
tables and a regex misses half of them. The heuristic extractor is the fallback
for offline tests and for running without Anthropic credentials; it only
understands numbered or bulleted lists.
"""
import json
import os
import re

from . import cache

MODEL = os.environ.get("AUDIT_EXTRACT_MODEL", "claude-opus-5-5")

SCHEMA = {
    "type": "object",
    "properties": {
        "businesses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "website": {"type": "string"},
                },
                "required": ["name", "website"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["businesses"],
    "additionalProperties": False,
}

PROMPT = """Below is an AI assistant's answer to a customer asking for a local business.

List every specific, named business the answer recommends or presents as an option, in the
order they first appear. Rules:
- Only real business names (a clinic, office, shop, school, studio). Not platforms or
  directories (Google Maps, Doctoralia, TripAdvisor, Yelp, Idealista), not generic advice.
- A national chain counts once, by its brand name.
- "website": the business's domain (e.g. "clinicaejemplo.es") only if the answer or its
  sources clearly tie it to that business; otherwise "".
- If the answer names no businesses, return an empty list.

<answer>
{answer}
</answer>

<sources>
{sources}
</sources>"""


def with_claude(answer: dict) -> list:
    key = {"model": MODEL, "text": answer["text"], "sources": answer["sources"]}
    hit = cache.get("extract", key)
    if hit is not None:
        return hit
    if not answer["text"]:
        return cache.put("extract", key, [])

    import anthropic
    client = anthropic.Anthropic()
    sources = "\n".join(f"- {s['title']} {s['url']}" for s in answer["sources"][:30]) or "(none)"
    resp = client.beta.messages.create(
        model=MODEL, max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": PROMPT.format(answer=answer["text"], sources=sources)}],
    )
    if resp.stop_reason == "refusal":
        return []                     # not cached: worth retrying next run
    text = next((b.text for b in resp.content if b.type == "text"), '{"businesses": []}')
    return cache.put("extract", key, json.loads(text)["businesses"])


_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def heuristic(answer: dict) -> list:
    out = []
    for line in answer["text"].splitlines():
        m = _ITEM.match(line)
        if not m:
            continue
        body = m.group(1)
        b = _BOLD.search(body)
        name = b.group(1) if b else re.split(r"\s[-–—:]\s|:\s", body, maxsplit=1)[0]
        name = name.strip(" *:.-").strip()
        if 2 < len(name) < 80:
            out.append({"name": name, "website": ""})
    return out
