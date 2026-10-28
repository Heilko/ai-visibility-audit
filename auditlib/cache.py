"""
Disk cache for every paid call.

An AI answer costs money and a re-run of the report should not. Each call is keyed
on everything that could change its answer (engine, model, question, run number),
so re-running the same audit is free and a new run number buys a fresh sample.
"""
import hashlib
import json
import os

CACHE_DIR = os.environ.get("AUDIT_CACHE", "out/cache")


def _path(kind, key):
    h = hashlib.sha256(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    return os.path.join(CACHE_DIR, kind, f"{h}.json")


def get(kind, key):
    p = _path(kind, key)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def put(kind, key, value):
    p = _path(kind, key)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    return value
