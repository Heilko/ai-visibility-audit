"""
Decide when two names are the same business.

AI answers say "Clínica Dental Ferrocarril", "Dental Ferrocarril" and "Ferrocarril
Dental Clinic" for the same place. Names are compared after stripping accents,
legal suffixes and the sector's generic words; a shared website domain wins over
any name comparison.
"""
import difflib
import re
import unicodedata
from urllib.parse import urlparse

ALWAYS_GENERIC = {"sl", "slu", "sa", "slp", "cb", "madrid", "de", "del", "la", "el", "los",
                  "las", "y", "e", "en", "the", "and", "of", "&"}


def domain(s: str) -> str:
    s = (s or "").strip().lower()
    if not s:
        return ""
    host = urlparse(s if "://" in s else "https://" + s).netloc
    return host.removeprefix("www.")


def norm(name: str, generic=()) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", s)
    drop = ALWAYS_GENERIC | set(generic)
    kept = [w for w in words if w not in drop]
    return " ".join(kept) or " ".join(words)


def same(a_name, a_dom, b_name, b_dom, generic=()) -> bool:
    if a_dom and b_dom:
        return a_dom == b_dom
    a, b = norm(a_name, generic), norm(b_name, generic)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 5 and re.search(rf"\b{re.escape(short)}\b", long_):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.88


class Registry:
    """Clusters every business mentioned across all answers into one entity each."""

    def __init__(self, generic=()):
        self.generic = generic
        self.entities = []        # [{"id", "name", "domain", "aliases": set()}]

    def resolve(self, name, dom="") -> int:
        dom = domain(dom)
        for e in self.entities:
            if same(name, dom, e["name"], e["domain"], self.generic) or any(
                    same(name, "", a, "", self.generic) for a in e["aliases"]):
                e["aliases"].add(name)
                if dom and not e["domain"]:
                    e["domain"] = dom
                return e["id"]
        self.entities.append({"id": len(self.entities), "name": name, "domain": dom, "aliases": {name}})
        return len(self.entities) - 1
