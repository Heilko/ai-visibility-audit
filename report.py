#!/usr/bin/env python3
"""
Turn results.json into what gets shown to people.

  report.html    for you: sector summary, who the assistants recommend, every target
  targets.csv    the same per-target numbers, for the tracker
  audits/*.html  one page per target, in Spanish, for the owner. Print to PDF from the browser.

  python report.py out/dental-2026-09-29/results.json
"""
import csv
import html
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

from auditlib.match import Registry

ENGINE_LABEL = {"claude": "Claude", "openai": "ChatGPT", "perplexity": "Perplexity",
                "gemini": "Gemini", "fake": "Fake"}

CSS = """
:root{--ink:#1c1c1e;--muted:#6b6b70;--line:#e3e3e6;--bg:#fff;--accent:#0b5cad;--bad:#b3261e;--good:#1e7d34}
*{box-sizing:border-box}body{font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
color:var(--ink);background:var(--bg);max-width:860px;margin:32px auto;padding:0 16px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.muted{color:var(--muted)}.big{font-size:40px;font-weight:700;line-height:1.1}.bad{color:var(--bad)}.good{color:var(--good)}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted)}td.n{text-align:right;font-variant-numeric:tabular-nums}
.quote{border-left:3px solid var(--line);padding:4px 12px;color:#333;white-space:pre-wrap;font-size:14px}
.kpis{display:flex;gap:28px;flex-wrap:wrap;margin:12px 0}.kpis div{min-width:140px}
ol li{margin-bottom:8px}a{color:var(--accent)}
@media print{body{margin:0;max-width:none;font-size:12.5px}h2{break-after:avoid}.noprint{display:none}}
"""


def pct(x):
    return f"{round(100 * x)}%"


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60] or "negocio"


def plain(md):
    """AI answers are markdown; the owner should read prose, not asterisks."""
    md = re.sub(r"\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)", r"\1", md)   # [text](url) -> text
    md = re.sub(r"\[\d+\]", "", md)                              # citation markers [1]
    md = re.sub(r"(\*\*|__|`)", "", md)
    md = re.sub(r"^#+\s*", "", md, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def page(title, body):
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>")


def analyse(r):
    reg = Registry(r["generic"])
    targets = r["targets"]
    for t in targets:
        t["entity"] = reg.resolve(t["name"], t["domain"])

    valid = [a for a in r["answers"] if a["text"]]
    for a in valid:
        ids = []
        for b in a["businesses"]:
            eid = reg.resolve(b["name"], b.get("website", ""))
            if eid not in ids:
                ids.append(eid)
        a["entities"] = ids

    mentions = Counter(e for a in valid for e in a["entities"])
    by_engine = defaultdict(Counter)
    engine_n = Counter(a["engine"] for a in valid)
    for a in valid:
        for e in a["entities"]:
            by_engine[e][a["engine"]] += 1

    n = len(valid) or 1
    board = [{"id": e["id"], "name": e["name"], "domain": e["domain"], "count": mentions[e["id"]],
              "rate": mentions[e["id"]] / n,
              "engines": {k: by_engine[e["id"]][k] / engine_n[k] for k in engine_n}}
             for e in reg.entities if mentions[e["id"]]]
    board.sort(key=lambda x: -x["count"])

    total = sum(mentions.values()) or 1
    top3 = sum(b["count"] for b in board[:3]) / total
    target_ids = {t["entity"] for t in targets}
    seen_targets = sum(1 for t in targets if mentions[t["entity"]])

    for t in targets:
        own = [a for a in valid if not t["area"] or a["area"] == t["area"] or a["area"].lower() == "madrid"]
        t["asked"] = len(own)
        t["hits"] = [a for a in own if t["entity"] in a["entities"]]
        t["rate"] = len(t["hits"]) / (len(own) or 1)
        comp = Counter(e for a in own for e in a["entities"] if e != t["entity"])
        t["competitors"] = [(reg.entities[e]["name"], c / (len(own) or 1)) for e, c in comp.most_common(3)]
        t["example"] = next((a for a in own if a["entities"] and t["entity"] not in a["entities"]), None)

    summary = {"answers": len(valid), "failed": len(r["answers"]) - len(valid),
               "businesses": len(board), "top3_share": top3,
               "targets": len(targets), "targets_seen": seen_targets,
               "targets_in_board": sum(1 for b in board if b["id"] in target_ids)}
    return board, summary, engine_n


def fixes(t):
    """Up to three fixes, most important first, each one something the owner can act on."""
    s = t.get("site") or {}
    out = []
    if s.get("error") and not s.get("reachable"):
        out.append(f"No hemos podido leer la web ({html.escape(s['error'])}). Si los robots de IA tampoco "
                   "pueden, el negocio no existe para ellos más allá de los directorios.")
    if s.get("blocked_bots"):
        out.append("La web <b>bloquea a los robots de búsqueda con IA</b> ("
                   + ", ".join(html.escape(b) for b in s["blocked_bots"]) +
                   ") en su robots.txt. Mientras estén bloqueados, esos asistentes no pueden leerla. "
                   "Se corrige con unas pocas líneas en ese archivo.")
    if s.get("reachable") and not s.get("schema_type"):
        out.append("La web <b>no declara sus datos en formato estructurado</b> (schema.org): dirección, "
                   "teléfono y horario. Son los datos que los asistentes citan al recomendar. Se corrige con "
                   "un bloque de código en la página de inicio.")
    elif s.get("schema_type"):
        miss = [lbl for key, lbl in (("address", "dirección"), ("telephone", "teléfono"))
                if key not in s["schema_has"]]
        if not {"openingHours", "openingHoursSpecification"} & set(s["schema_has"]):
            miss.append("horario")
        if miss:
            out.append("Los datos estructurados de la web no incluyen: <b>" + ", ".join(miss) + "</b>.")
    if s.get("reachable") and s.get("text_chars", 0) < 500:
        out.append(f"Sin JavaScript, la página de inicio muestra solo <b>{s['text_chars']} caracteres de texto</b>. "
                   "Muchos robots de IA no ejecutan JavaScript: ven una página casi vacía.")
    out.append("ChatGPT presenta cada recomendación con su <b>nota y número de reseñas de Google</b> y cita "
               "directorios del sector (en medicina estética, Doctoralia). Conviene tener la ficha de Google y esos "
               "directorios completos, y pedir reseña a cada cliente después de su visita.")
    return out[:3]


def one_pager(r, t, engines_used):
    eng = ", ".join(ENGINE_LABEL.get(e, e) for e in engines_used)
    areas = t["area"] or ", ".join(r["areas"])
    rate_cls = "good" if t["rate"] >= 0.3 else "bad"
    b = [f"<h1>{html.escape(t['name'])}</h1>",
         f"<p class='muted'>Visibilidad en buscadores de IA · {html.escape(r['label'])} · "
         f"{html.escape(areas)} · {r['generated'][:10]}</p>",
         f"<div class='kpis'><div><div class='big {rate_cls}'>{pct(t['rate'])}</div>"
         f"de las respuestas lo recomiendan</div>"
         f"<div><div class='big'>{t['asked']}</div>preguntas hechas a {html.escape(eng)}</div></div>"]

    b.append("<h2>Quién aparece en su lugar</h2>")
    if t["competitors"]:
        b.append("<table><tr><th>Negocio</th><th class='n'>Recomendado en</th></tr>")
        for name, rate in t["competitors"]:
            b.append(f"<tr><td>{html.escape(name)}</td><td class='n'>{pct(rate)}</td></tr>")
        b.append("</table>")
    else:
        b.append("<p>Los asistentes no nombraron ningún negocio concreto en estas preguntas.</p>")

    if t["example"]:
        ex = t["example"]
        text = plain(ex["text"])
        text = text if len(text) < 700 else text[:700].rsplit(" ", 1)[0] + " […]"
        b.append("<h2>Un ejemplo real</h2>"
                 f"<p class='muted'>Pregunta a {ENGINE_LABEL.get(ex['engine'], ex['engine'])}: "
                 f"«{html.escape(ex['question'])}»</p><div class='quote'>{html.escape(text)}</div>")

    s = t.get("site")
    if s:
        rows = [("Robots de IA con acceso",
                 "Todos" if not s["blocked_bots"] else "Bloqueados: " + ", ".join(s["blocked_bots"])),
                ("Datos estructurados de negocio local",
                 f"Sí ({s['schema_type']}: {', '.join(s['schema_has']) or 'sin datos clave'})"
                 if s["schema_type"] else "No"),
                ("Texto visible sin JavaScript", f"{s['text_chars']} caracteres")]
        b.append("<h2>Qué ve un robot de IA en la web</h2><table>"
                 + "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>" for k, v in rows)
                 + "</table>")

    b.append("<h2>Qué arreglar</h2><ol>" + "".join(f"<li>{f}</li>" for f in fixes(t)) + "</ol>")
    manual = any("manual" in str(m) for m in r["engines"].values())
    how = ("en la app, cada vez en un chat temporal nuevo y desde Madrid" if manual
           else "con búsqueda web activada y ubicación Madrid, a través de sus API")
    b.append(f"<p class='muted' style='margin-top:28px;font-size:12px'>Método: cada pregunta se hizo "
             f"{r['runs']} veces a cada asistente, {how}. Las respuestas pueden variar por usuario; por eso "
             "se da un porcentaje y no un sí o un no. Solo se han usado datos públicos.</p>")
    return page(f"{t['name']}: visibilidad en IA", "".join(b))


def build(path):
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    out = os.path.dirname(path)
    board, s, engine_n = analyse(r)
    engines_used = list(engine_n)

    failed = f" ({s['failed']} failed)" if s["failed"] else ""
    b = [f"<h1>{html.escape(r['label'])}: AI visibility</h1>",
         f"<p class='muted'>{html.escape(', '.join(r['areas']))} · {r['generated']} · "
         + html.escape(", ".join(f"{ENGINE_LABEL.get(k, k)} ({v})" for k, v in r["engines"].items()))
         + f" · {r['runs']} runs</p>",
         "<div class='kpis'>"
         f"<div><div class='big'>{s['answers']}</div>answers{failed}</div>"
         f"<div><div class='big'>{s['businesses']}</div>businesses named</div>"
         f"<div><div class='big'>{pct(s['top3_share'])}</div>of mentions go to the top 3</div>"
         + (f"<div><div class='big'>{s['targets_seen']}/{s['targets']}</div>targets mentioned at least once</div>"
            if s["targets"] else "")
         + "</div>",
         "<p class='muted'>The sector test reads the last two numbers: high top-3 share and few targets "
         "mentioned means most businesses are invisible and a few take everything.</p>"]

    b.append("<h2>Who the assistants recommend</h2><table><tr><th>#</th><th>Business</th>"
             "<th class='n'>Answers</th>" + "".join(f"<th class='n'>{ENGINE_LABEL.get(e, e)}</th>" for e in engines_used)
             + "</tr>")
    for i, x in enumerate(board[:40], 1):
        b.append(f"<tr><td>{i}</td><td>{html.escape(x['name'])}"
                 + (f" <span class='muted'>{html.escape(x['domain'])}</span>" if x["domain"] else "")
                 + f"</td><td class='n'>{pct(x['rate'])}</td>"
                 + "".join(f"<td class='n'>{pct(x['engines'].get(e, 0))}</td>" for e in engines_used) + "</tr>")
    b.append("</table>")

    if r["targets"]:
        os.makedirs(os.path.join(out, "audits"), exist_ok=True)
        b.append("<h2>Targets</h2><table><tr><th>Business</th><th>Area</th><th class='n'>Recommended</th>"
                 "<th>Top competitor</th><th>Website</th></tr>")
        rows = []
        for t in sorted(r["targets"], key=lambda t: t["rate"]):
            fn = f"audits/{slug(t['name'])}.html"
            with open(os.path.join(out, fn), "w", encoding="utf-8") as f:
                f.write(one_pager(r, t, engines_used))
            st = t.get("site") or {}
            web = ("blocks AI bots · " if st.get("blocked_bots") else "") + \
                  ("schema ✓" if st.get("schema_type") else "no schema") if st.get("reachable") else (st.get("error") or "—")
            comp = t["competitors"][0][0] if t["competitors"] else ""
            b.append(f"<tr><td><a href='{fn}'>{html.escape(t['name'])}</a></td><td>{html.escape(t['area'])}</td>"
                     f"<td class='n'>{pct(t['rate'])}</td><td>{html.escape(comp)}</td><td>{html.escape(web)}</td></tr>")
            rows.append({"name": t["name"], "domain": t["domain"], "area": t["area"],
                         "asked": t["asked"], "recommended": len(t["hits"]), "rate": round(t["rate"], 3),
                         "top_competitor": comp,
                         "blocked_bots": " ".join(st.get("blocked_bots", [])),
                         "schema": st.get("schema_type", ""), "text_chars": st.get("text_chars", ""),
                         "site_error": st.get("error", ""), "audit": fn})
        b.append("</table>")
        with open(os.path.join(out, "targets.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    with open(os.path.join(out, "report.html"), "w", encoding="utf-8") as f:
        f.write(page(f"{r['label']}: AI visibility", "".join(b)))
    print(f"wrote {os.path.join(out, 'report.html')}"
          + (f", targets.csv and {len(r['targets'])} audits" if r["targets"] else ""))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(sys.argv[1])
