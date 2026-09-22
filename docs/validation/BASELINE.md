# Step 0 — Baseline Audit (Branch `improve/fkl-polish`)

Reproducible baseline recorded **before any functional edit**. Everything below was
measured on a fresh clone of `main` (commit `a939957`), a clean virtual environment
built from `requirements.txt`, and a clean SQLite database. Companion artifacts:
[`baseline_facts.json`](./baseline_facts.json),
[`baseline_relationships.json`](./baseline_relationships.json),
[`baseline_metrics.json`](./baseline_metrics.json).

Reproduce with:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -q          # -> 198 passed
```

---

## 1. Test suite tripwire

**Baseline: `198 passed, 0 failed` in ~32s.** This exact pass count is the regression
tripwire for the rest of the branch. Any branding/config change must keep this number
**equal or greater** (the plan adds `tests/test_demo_loader.py` in Step 7, so the final
count should be `> 198`).

Per-file breakdown (from `pytest -v`):

| Test file | Purpose |
|---|---|
| `test_gate1.py` | PDF parsing, page scoring, candidate prioritization |
| `test_gate2.py` | Fact extraction, schema validation, parsing edge cases |
| `test_gate3.py` | Normalization, canonicalization, unit conversion, matching |
| `test_gate4.py` | Relationship reasoning, heuristic classifier, provider fallback |
| `test_gate5.py` | API endpoints, UI, file upload, persistence (incl. branding assertions) |
| `test_pipeline_integration.py` | End-to-end pipeline, DB round-trips, serialization |
| `test_table_regression.py` | Table extraction regression fixtures |

Note: `tests/test_gate5.py` asserts the literal branding string
`"Superjoin Fact Knowledge Layer"` (the `/health` `service` field and the UI HTML).
Those assertions are updated in Step 1 as part of the deliberate product-identity move.

---

## 2. Clean starter-data ingest metrics

Both starter datasets ingested through the **existing** pipeline
(`process_document_pipeline(pdf_path, provider=None, db=db, reasoning_mode="heuristic")`),
one clean DB, offline/heuristic mode. Wall-clock ingest ≈ **29 s** for 6 PDFs / 511 pages.

| Metric | Value |
|---|---|
| Documents | **6** |
| Pages ingested | **511** |
| Grounded facts | **13** |
| Cross-document relationships | **9** |
| Aggregate candidate pairs (final fact set) | **9** |

**Relationship-type distribution:**

| Relationship | Count |
|---|---|
| CORROBORATES | 4 |
| CONTRADICTS | 3 |
| RECONCILABLE | 1 |
| UNCERTAIN | 1 |

**Facts / pages per document:**

| Document | Pages | Candidate pages | Facts |
|---|---|---|---|
| `01-india-economic-survey-2024-25-excerpt.pdf` | 89 | 55 | 1 |
| `02-rbi-annual-report-2024-25-excerpt.pdf` | 100 | 88 | 0 |
| `03-imf-india-2025-article-iv-excerpt.pdf` | 95 | 44 | 1 |
| `01-delhivery-prospectus-2022-excerpt.pdf` | 100 | 64 | 0 |
| `02-delhivery-annual-report-fy24-excerpt.pdf` | 100 | 80 | 11 |
| `03-delhivery-q4-fy24-earnings-presentation.pdf` | 27 | 16 | 0 |

### Discrepancy vs. prior docs (important)

The `DEMO_RUNBOOK.md` and some README prose describe a run of **"6 documents, 511 pages,
47 facts, 100 relationships."** The **current** clean pipeline produces **13 facts / 9
relationships** on the same 6 documents / 511 pages. This gap is the result of the
precision-focused refactors in the git history (`ea59682 Refine offline extraction and
reasoning`, `a8d0288 Optimize Real PDF Parsing And Ingestion Performance`): the extractor
now emits fewer, higher-precision facts rather than the aggressive coverage of the earlier
spike-era code. **The 47/100 figures are stale and must not be presented as current.**
All Week-1 UI/README claims are reconciled to the 13/9 reality below.

---

## 3. What the clean run actually produces (ground truth)

The 13 facts (full list in `baseline_facts.json`) are:

- `delhivery revenue` — 74,540.82 (standalone, FY24) and 81,415.38 (consolidated, FY24),
  both `scope=None` (the broad revenue pattern does not capture the standalone/consolidated
  basis — see Limitation L1).
- Director "board status" facts, all `ceased to be a Director` except one
  `resigned from the Board`:
  - Suvir Suren Sujan — **resigned** (p33) and **ceased** (p40 ×2, p43)
  - Donald Francis Colleran — ceased (p40 ×2, p43)
  - Sandeep Kumar Barasia — ceased (p40 ×2)
- `India real GDP growth` — 6.5 (IMF, FY2024/25) and 6.4 (Economic Survey, FY25,
  qualifiers `[First Advance Estimate, estimated]`).

The 9 relationships (full list in `baseline_relationships.json`):

| # | Facts | Relationship | Why |
|---|---|---|---|
| 1 | Suvir ceased (p40) ↔ Suvir ceased (p43) | CORROBORATES | duplicate of same fact (Defect A) |
| 2 | Suvir ceased (p40 #2) ↔ Suvir ceased (p43) | CORROBORATES | duplicate of same fact (Defect A) |
| 3 | Colleran ceased (p40) ↔ Colleran ceased (p43) | CORROBORATES | duplicate of same fact (Defect A) |
| 4 | Colleran ceased (p40 #2) ↔ Colleran ceased (p43) | CORROBORATES | duplicate of same fact (Defect A) |
| 5 | India GDP 6.5 (IMF) ↔ India GDP 6.4 (Survey) | **RECONCILABLE** | estimate-vintage difference |
| 6 | Suvir resigned (p33) ↔ Suvir ceased (p40) | **CONTRADICTS** | resigned-vs-ceased (Defect B) |
| 7 | Suvir resigned (p33) ↔ Suvir ceased (p40 #2) | **CONTRADICTS** | resigned-vs-ceased (Defect B) |
| 8 | Suvir resigned (p33) ↔ Suvir ceased (p43) | **CONTRADICTS** | resigned-vs-ceased (Defect B) |
| 9 | delhivery rev standalone ↔ delhivery rev consolidated | UNCERTAIN | scope not captured (Limitation L1) |

---

## 4. Demonstration-case reality check (README/UI vs. live run)

The README lists six "demonstration cases" and the UI exposes five case filters
(`gdp_corroborate`, `cpi_investigate`, `vintage_reconcile`, `director_reconcile`,
`scope_failure`). Reconciled against the live 9-relationship run:

| UI case (filter) | README claim | Live run result | Honest status |
|---|---|---|---|
| `gdp_corroborate` (CORROBORATES + both GDP) | "Case 1 GDP Corroboration (RBI↔IMF 6.5%)" | The only GDP pair is **RECONCILABLE** (6.5 vs 6.4), not CORROBORATES | **REGRESSION SCENARIO** (filter finds 0 live) |
| `cpi_investigate` (CPI/inflation subject) | "Case 2 CPI Potential Conflict" | **No** CPI/inflation fact is extracted in the clean run | **KNOWN LIMITATION** |
| `vintage_reconcile` (RECONCILABLE + vintage) | "Case 3a Estimate Vintage Reconciliation" | **LIVE** — India GDP 6.5 vs 6.4, RECONCILABLE, vintage rationale | **VERIFIED LIVE** |
| `director_reconcile` (RECONCILABLE + temporal/date) | "Case 3b Director Temporal Reconciliation" | Director pairs are CONTRADICTS/CORROBORATES, **no** RECONCILABLE director pair | **REGRESSION SCENARIO** |
| `scope_failure` (RECONCILABLE + scope=different) | "Case 4 Scope Distinction (Standalone vs Consolidated)" | The standalone/consolidated pair is **UNCERTAIN** with scope=`same` (L1), not RECONCILABLE | **KNOWN LIMITATION** |

**Bottom line:** exactly **one** of the five UI cases is genuinely reproduced by the
current clean run (`vintage_reconcile`). The rest are either covered by unit/integration
tests but not reproduced live (`REGRESSION SCENARIO`), or document a real gap
(`KNOWN LIMITATION`). Week 1 (Step 5) labels each case accordingly instead of implying all
are live.

---

## 5. Repository inventory (settled)

Authoritative tracked-file list (from `git ls-files`); 35 tracked files.

**`app/` modules (actual):** `__init__.py`, `database.py`, `extraction.py`, `main.py`,
`matcher.py`, `models.py`, `normalizer.py`, `pdf_parser.py`, `pipeline.py`,
`providers.py`, `reasoner.py`.

**Provider status — confirmed:** `app/providers.py` defines **only**
`LLMProvider(ABC)` (abstract `extract_facts` / `compare_facts`) and
`MockProvider(LLMProvider)` (deterministic fixtures for tests). **There is no concrete
`AnthropicProvider` class.** No LLM provider is wired into the runtime; `main.py`
hardcodes `provider=None` (offline/heuristic) for `/upload` and `/health` reports
`reasoning_mode: "heuristic"`. The runtime is **fully offline/heuristic by default**.

**`scripts/` directory — does not exist.** Week 1 (Step 4) creates it with
`load_starter_demo.py`.

**Default DB path:** `app/database.py` → `DEFAULT_DB_PATH =
<repo>/data/fact_layer.db`; `Database(":memory:")` is used by tests. `data/` and `*.db`
are gitignored (no runtime data tracked).

### Grep audit (tracked files only, case-insensitive)

**`superjoin`** hits (tracked files):

- `app/main.py` — lines 2 (docstring), 27 (FastAPI `title`), 62 (`/health` `service`),
  179 (`<title>`), 446 (`<h1>`)
- `app/database.py:2`, `app/matcher.py:2`, `app/models.py:2`, `app/normalizer.py:2`,
  `app/reasoner.py:2` — module docstrings
- `tests/test_gate5.py` — lines 62 (`/health` service assertion), 226 & 236 (UI HTML
  assertions)
- `README.md:1` (H1), `README.md:145` (`cd Superjoin` — broken clone path)
- `.gitignore` — ignore patterns for historical local planning files
  (`superjoin-final-plan.md`, `Superjoin_Fact_Knowledge_Layer/`, etc.) — *historical, left
  as-is*
- `docs/validation/phase0-spike.py:2` — *historical validation spike, left as-is*

**`anthropic` / `ANTHROPIC_API_KEY`** hits (tracked files):

- `.env.example` (lines 1–2) — the overclaiming config fixed in Step 2
- `requirements.txt` (lines 12–13) — **commented-out** optional `anthropic` dependency
- `README.md:33` and `README.md:167` — prose, rewritten in Step 6
- `docs/validation/phase0-spike.py` + the untracked `spike/`, `spike.py` — historical
  validation spikes, left as-is

*(Hits inside `.venv` / `.venv_fresh` are in gitignored virtualenvs and are not part of
the repository.)*

---

## 6. Known defects recorded (NOT fixed this branch — deferred to Week 2)

### Defect A — Duplicate fact extraction from a single source sentence

The same director-cessation sentence yields multiple `Fact` objects. Concrete evidence
from the clean run:

- "Mr. Suvir Suren Sujan ceased to be a Director with effect from August 24, 2023" →
  **3 facts** (`b52d6b45e4c9` p40, `19e056d8c435` p40, `e9d2574edbf9` p43)
- "…Donald Francis Colleran ceased to be a Director…" → **3 facts** (`63a228bd325e` p40,
  `f162d52aa90a` p40, `26c75ae3b4ad` p43)
- "…Sandeep Kumar Barasia ceased to be a Director…" → **2 facts** (`b52b876a7b05` p40,
  `f434679e8fa4` p40)

Consequence: the matcher pairs near-identical facts and the reasoner labels them
`CORROBORATES` ("both independent sources corroborate…") even though they are the **same
document / same sentence**. This inflates the relationship count and the CORROBORATES
share with non-independent "corroboration." Deferred to Week 2 dedup work.

### Defect B — `resigned` vs `ceased to be a Director` semantic classification

For Suvir Suren Sujan the same board departure is phrased two ways:
"resigned from the Board with effect from August 24, 2023" (p33) and "ceased to be a
Director with effect from August 24, 2023" (p40/43). The heuristic reasoner treats the
different value strings as conflicting and classifies the pair **CONTRADICTS**
("incompatible claims: resigned from the Board versus ceased to be a Director"). These
are near-synonymous departures of the same person on the same date and should be
RECONCILABLE (or CORROBORATES). Deferred to Week 2 evaluation/normalization work.

### Limitation L1 — standalone/consolidated scope not captured on broad revenue matches

The precise `revenue from operations … INR …` pattern captures `scope` (standalone /
consolidated), but the fallback broad revenue pattern does **not**. In the clean run the
Delhivery standalone (₹74,540.82 M) and consolidated (₹81,415.38 M) figures are captured
by the broad pattern with `scope=None`, so the comparison has `scope=same` and an
ambiguous period, and is classified **UNCERTAIN** rather than RECONCILABLE. This is why the
"Scope Distinction" case is not live today.

---

## 7. Honest current-runtime statement (used by README/UI)

- **Default runtime: offline heuristic extraction + heuristic reasoning.** No API key
  required. Deterministic, reproducible.
- **Provider abstraction exists** (`LLMProvider` ABC + `MockProvider`), but **no concrete,
  validated external LLM provider is wired in.** The runtime never calls an LLM.
- On the two starter datasets the clean run yields **6 docs / 511 pages / 13 facts /
  9 relationships**; the one cleanly reproduced reasoning case is the **GDP estimate-vintage
  reconciliation (RECONCILABLE)**.
