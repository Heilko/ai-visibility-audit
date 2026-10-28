"""
What an AI crawler sees when it visits a business's website.

Public pages only, fetched the way modelia-scanner does it: robots.txt obeyed, one
request at a time per host, an honest User-Agent, everything cached. Three checks,
each one a concrete fix the owner can act on:

  * robots.txt blocks the bots that feed AI search (GPTBot, OAI-SearchBot,
    PerplexityBot, ClaudeBot, Google-Extended, bingbot)
  * the homepage has schema.org structured data for a local business, with the
    facts assistants quote: address, phone, opening hours
  * the homepage has real text without JavaScript (many AI crawlers don't run it)
"""
import json
import re
import time
import urllib.robotparser

import requests
from lxml import html as lxml_html

from . import cache

UA = "AIVisibilityAudit/1.0 (+market research; reads public homepages only)"
DELAY = 1.5
AI_BOTS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "ClaudeBot",
           "Claude-SearchBot", "Google-Extended", "bingbot"]
LOCAL_TYPES = {"localbusiness", "dentist", "medicalbusiness", "medicalclinic", "physician",
               "legalservice", "attorney", "accountingservice", "professionalservice",
               "store", "clothingstore", "healthandbeautybusiness", "beautysalon",
               "sportsactivitylocation", "exercisegym", "healthclub", "educationalorganization",
               "school", "organization", "medicalorganization"}

_last = 0.0


def _get(url):
    global _last
    wait = DELAY - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()
    return requests.get(url, headers={"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"},
                        timeout=15, allow_redirects=True)


def _types(node, out):
    if isinstance(node, list):
        for n in node:
            _types(n, out)
    elif isinstance(node, dict):
        t = node.get("@type")
        for x in (t if isinstance(t, list) else [t]):
            if isinstance(x, str):
                out.append((x.lower(), node))
        for v in node.values():
            if isinstance(v, (list, dict)):
                _types(v, out)


def check(dom: str) -> dict:
    hit = cache.get("site", {"domain": dom, "v": 1})
    if hit is not None:
        return hit
    res = {"domain": dom, "reachable": False, "blocked_bots": [], "schema_type": "",
           "schema_has": [], "text_chars": 0, "error": ""}
    base = f"https://{dom}"
    try:
        rp = urllib.robotparser.RobotFileParser()
        r = _get(base + "/robots.txt")
        rp.parse(r.text.splitlines() if r.ok else [])
        res["blocked_bots"] = [b for b in AI_BOTS if not rp.can_fetch(b, base + "/")]
        if not rp.can_fetch(UA, base + "/"):
            res["error"] = "robots.txt disallows this audit; homepage not read"
            return cache.put("site", {"domain": dom, "v": 1}, res)

        r = _get(base + "/")
        res["reachable"] = r.ok
        if not r.ok:
            res["error"] = f"HTTP {r.status_code}"
            return cache.put("site", {"domain": dom, "v": 1}, res)
        doc = lxml_html.fromstring(r.content)

        found = []
        for s in doc.xpath('//script[@type="application/ld+json"]/text()'):
            try:
                _types(json.loads(s), found)
            except ValueError:
                continue
        local = [(t, n) for t, n in found if t in LOCAL_TYPES]
        if local:
            t, n = local[0]
            res["schema_type"] = t
            res["schema_has"] = [k for k in ("address", "telephone", "openingHoursSpecification",
                                             "openingHours", "geo", "sameAs", "priceRange")
                                 if n.get(k)]

        for bad in doc.xpath("//script|//style|//noscript|//svg"):
            bad.drop_tree()
        res["text_chars"] = len(re.sub(r"\s+", " ", doc.text_content()).strip())
    except (requests.RequestException, ValueError) as e:
        res["error"] = type(e).__name__
    return cache.put("site", {"domain": dom, "v": 1}, res)
