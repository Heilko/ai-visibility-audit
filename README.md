# AI visibility audit

Asks ChatGPT, Claude, Perplexity and Gemini the questions a customer would type
("¿cuál es la mejor clínica dental en Chamberí?"), several times each, and records
which businesses they recommend. It then checks what an AI crawler sees on each target's
website and writes a one-page Spanish audit per business.

This is layer 1 of the AI-visibility idea: measurement only. Its first job is the
sector test: 5 sectors × 10 businesses, then show audits to 3 owners per sector and
count signed letters of intent.

```bash
# setup (once)
python3 -m venv .venv && .venv/bin/pip install anthropic requests lxml

# keys: any subset works; engines without a key are skipped
export ANTHROPIC_API_KEY=...    # also used to read the answers (extraction)
export OPENAI_API_KEY=...       # ChatGPT
export PERPLEXITY_API_KEY=...
export GEMINI_API_KEY=...

# plan first: how many paid calls, nothing spent
.venv/bin/python audit.py dental --area Chamberí --targets clinics.csv --dry-run

# run
.venv/bin/python audit.py dental --area Chamberí --targets clinics.csv
.venv/bin/python audit.py dental --areas-from Barrio --targets clinics.csv --runs 2
.venv/bin/python audit.py fitness --area Madrid --engines openai,perplexity   # sector scan, no targets

# rebuild the report from saved results (free)
.venv/bin/python report.py out/dental-2026-09-29/results.json

# offline test of the whole pipeline
.venv/bin/python audit.py dental --area Madrid --targets clinics.csv --engines fake --extract heuristic
```

Sectors (`sectors.json`, edit the questions freely): `dental`, `estetica`, `abogados`,
`gestorias`, `idiomas`, `fitness`, `moda`.

**Targets CSV:** needs a name column (`name`, `nombre`, `Company name`…) and ideally a
domain column (`domain`, `website`, `Company Domain Name`…). `--areas-from COLUMN`
queries each distinct neighbourhood in that column, and each target is then scored on the
questions for its own area plus any city-wide (`Madrid`) questions.

## Output (`out/<sector>-<date>/`)

| | |
|---|---|
| `report.html` | Sector summary, who gets recommended (overall and per engine), every target |
| `targets.csv` | Per target: times asked, times recommended, rate, top competitor, website findings |
| `audits/*.html` | The owner's one-pager, in Spanish. Open it in a browser, then Print → Save as PDF |
| `results.json` | Every answer, source and extracted name; the report is rebuilt from this |

The sector test reads two numbers in `report.html`: **share of mentions going to the top 3**
(high = a few businesses take everything) and **targets mentioned at least once** (low = most
are invisible). Both high concentration and few targets seen means the most pain, which is
the best sector to sell into.

## Things to know before quoting a number

- **API answers are a proxy, not what the ChatGPT app shows a given person.** The apps add
  memory and personalisation. That's why every question runs several times and the result
  is a rate. Never tell an owner "ChatGPT doesn't recommend you"; say "in N of M answers".
- **Set `AUDIT_OPENAI_MODEL`** to the model ChatGPT currently answers with (the default,
  `gpt-5`, is a starting point). Same for `AUDIT_PERPLEXITY_MODEL` / `AUDIT_GEMINI_MODEL`.
- **The OpenAI, Perplexity and Gemini request shapes haven't been tested against live keys
  yet.** Run one question with `--runs 1` per engine first. A wrong shape shows up as
  `[error: …]` next to that answer, and the run continues.
- **Cost:** each new answer is one paid call with web search, plus one Claude call to
  extract names. Everything is cached in `out/cache/`, so re-runs and report rebuilds are
  free. `--max-calls` (default 300) stops runaway spend. Check the provider dashboards after
  the first small run before scaling up.
- **Claude calls** use `claude-opus-5-5` (override with `AUDIT_CLAUDE_MODEL` /
  `AUDIT_EXTRACT_MODEL`), with server-side refusal fallback enabled.
- **Public data only.** Website checks read `robots.txt` and the homepage, obey robots.txt,
  make one request every 1.5 s, and send an honest User-Agent. Listings (Bing Places,
  Foursquare, Google) are not checked automatically; the one-pager tells the owner to verify them.
