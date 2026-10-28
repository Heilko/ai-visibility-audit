#!/usr/bin/env python3
"""
Ask AI assistants the questions a customer would, and record who they recommend.

  python audit.py dental --area Chamberí --targets clinics.csv
  python audit.py fitness --area Chamberí --target "Estudio Ejemplo,estudioejemplo.es"   # one business
  python audit.py fitness --area Madrid --area Chamberí --engines claude,openai --runs 3
  python audit.py dental --areas-from Barrio --targets clinics.csv --dry-run
  python audit.py dental --area Madrid --engines fake --extract heuristic      # offline test

Writes out/<sector>-<date>/results.json, then report.html, targets.csv and one Spanish
one-page audit per target under audits/. Every paid call is cached under out/cache/.
"""
import argparse
import csv
import datetime
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from auditlib import cache, extract, site
from auditlib.engines import ENGINES
from auditlib.match import domain

import report

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path=os.path.join(HERE, ".env")):
    """KEY=value lines from .env, so keys are set once. Real env vars win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    os.environ.setdefault(k.strip(), v)


load_env()
NAME_COLS = ("name", "nombre", "company name", "business", "negocio")
DOMAIN_COLS = ("domain", "company domain name", "website", "web", "dominio", "url")


def where(area):
    return area if area.strip().lower() == "madrid" else f"{area} (Madrid)"


def load_targets(path, area_col):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    cols = {c.lower().strip(): c for c in rows[0]}
    ncol = next((cols[c] for c in NAME_COLS if c in cols), None)
    dcol = next((cols[c] for c in DOMAIN_COLS if c in cols), None)
    if not ncol:
        sys.exit(f"{path}: no name column (looked for {', '.join(NAME_COLS)})")
    acol = cols.get(area_col.lower()) if area_col else None
    return [{"name": r[ncol].strip(), "domain": domain(r.get(dcol, "") if dcol else ""),
             "area": (r.get(acol) or "").strip() if acol else ""}
            for r in rows if r.get(ncol, "").strip()]


def ask_cached(engine, question, run):
    key = {"engine": engine.name, "model": engine.model, "q": question, "run": run}
    hit = cache.get("answer", key)
    if hit is not None:
        return hit
    try:
        ans = engine.ask(question).to_dict()
    except Exception as e:                       # one failed call must not sink the batch
        return {"text": "", "sources": [], "model": engine.model, "note": f"error: {type(e).__name__}: {e}"[:300]}
    if ans["note"] == "refusal":
        return ans                               # not cached: retry next run
    return cache.put("answer", key, ans)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sector")
    ap.add_argument("--area", action="append", default=[], help="neighbourhood or 'Madrid'; repeatable")
    ap.add_argument("--targets", help="CSV of businesses to score (name + domain columns)")
    ap.add_argument("--target", action="append", default=[], metavar='"NAME,domain.com"',
                    help="one business to score, no CSV needed; repeatable")
    ap.add_argument("--areas-from", metavar="COLUMN", help="also query every distinct value of this targets column")
    ap.add_argument("--engines", default="claude,openai,perplexity,gemini")
    ap.add_argument("--runs", type=int, default=3, help="times each question is asked per engine")
    ap.add_argument("--extract", choices=["claude", "heuristic"], default="claude")
    ap.add_argument("--no-site", action="store_true", help="skip the website checks")
    ap.add_argument("--max-calls", type=int, default=300, help="refuse to spend more AI calls than this")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()

    with open(os.path.join(HERE, "sectors.json"), encoding="utf-8") as f:
        sectors = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    if a.sector not in sectors:
        sys.exit(f"unknown sector {a.sector!r}; one of: {', '.join(sectors)}")
    sec = sectors[a.sector]

    targets = load_targets(a.targets, a.areas_from) if a.targets else []
    for t in a.target:
        name, _, dom = t.partition(",")
        targets.append({"name": name.strip(), "domain": domain(dom), "area": ""})
    areas = list(dict.fromkeys(a.area + sorted({t["area"] for t in targets if t["area"]})))
    if not areas:
        sys.exit("give at least one --area (or --areas-from with --targets)")

    wanted = [e.strip() for e in a.engines.split(",") if e.strip()]
    missing = [e for e in wanted if e not in ENGINES]
    if missing:
        sys.exit(f"unknown engine(s): {', '.join(missing)}; one of: {', '.join(ENGINES)}")
    engines = [ENGINES[e] for e in wanted if ENGINES[e].available()]
    skipped = [e for e in wanted if not ENGINES[e].available()]
    if skipped:
        print("skipping (no key): " + ", ".join(f"{e} (set {ENGINES[e].env})" for e in skipped))
    if not engines:
        sys.exit("no engine available. Set at least one API key, or use --engines fake to test.")

    questions = [(area, q.format(where=where(area))) for area in areas for q in sec["queries"]]
    jobs = [(e, area, q, run) for e in engines for area, q in questions for run in range(1, a.runs + 1)]
    todo = sum(1 for e, _, q, run in jobs
               if cache.get("answer", {"engine": e.name, "model": e.model, "q": q, "run": run}) is None)
    print(f"{sec['label']}: {len(areas)} area(s) × {len(sec['queries'])} questions × {a.runs} runs × "
          f"{len(engines)} engine(s) [{', '.join(f'{e.name}:{e.model}' for e in engines)}]")
    print(f"= {len(jobs)} answers, {todo} not cached yet (each new one is a paid call"
          f"{', plus one extraction call' if a.extract == 'claude' else ''}). {len(targets)} targets.")
    if a.dry_run:
        return
    if todo > a.max_calls:
        sys.exit(f"{todo} new calls exceeds --max-calls {a.max_calls}; raise it if that's intended.")

    def work(job):
        e, area, q, run = job
        ans = ask_cached(e, q, run)
        if ans["text"]:
            biz = extract.with_claude(ans) if a.extract == "claude" else extract.heuristic(ans)
        else:
            biz = []
        return {"engine": e.name, "area": area, "question": q, "run": run, **ans, "businesses": biz}

    answers = []
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for i, res in enumerate(pool.map(work, jobs), 1):
            answers.append(res)
            flag = f"  [{res['note']}]" if res["note"] else ""
            print(f"  {i:>4}/{len(jobs)} {res['engine']:<10} {len(res['businesses'])} named  {res['question'][:60]}{flag}")

    if targets and not a.no_site:
        doms = sorted({t["domain"] for t in targets if t["domain"]})
        print(f"checking {len(doms)} websites (polite: one request every {site.DELAY}s)…")
        checks = {d: site.check(d) for d in doms}
        for t in targets:
            t["site"] = checks.get(t["domain"])

    out = a.out or os.path.join("out", f"{a.sector}-{datetime.date.today().isoformat()}")
    os.makedirs(out, exist_ok=True)
    results = {
        "sector": a.sector, "label": sec["label"], "generic": sec["generic"],
        "generated": datetime.datetime.now().isoformat(timespec="minutes"),
        "engines": {e.name: e.model for e in engines}, "runs": a.runs, "areas": areas,
        "questions": [q for _, q in questions], "answers": answers, "targets": targets,
    }
    path = os.path.join(out, "results.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"wrote {path}")
    report.build(path)


if __name__ == "__main__":
    main()
