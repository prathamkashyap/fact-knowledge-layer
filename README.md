# Fact Knowledge Layer

> Grounded cross-document fact extraction and epistemic reasoning engine.

Originally developed for the VIT 2026 Engineering Intern assignment, this repository provides a complete, deterministic, and auditable pipeline that parses multi-page PDFs, extracts structured factual assertions with verbatim source evidence, normalizes metrics across sources, and determines whether cross-document fact pairs **corroborate**, **contradict**, **reconcile**, or remain **uncertain**.

---

## At a Glance

- **Evidence-grounded:** Every extracted fact links directly to a document ID, page number, and verbatim source sentence. No hallucinations or ungrounded assertions.
- **Epistemic reasoning:** A numerical difference is not automatically a contradiction. Context—such as reporting scope, estimate vintages, effective dates, and forecast vs. actual status—governs how facts relate.
- **Fully offline by default:** Runs out of the box with zero external API dependencies or costs using deterministic rule-based extraction and dimension diff reasoning.
- **Provider-ready abstraction:** Clean `LLMProvider` abstract interface ready for external model providers without rewriting pipeline logic.
- **Zero-build UI:** Instant single-page Fact Explorer served directly by FastAPI using vanilla HTML, CSS, and modern JavaScript.

[Watch the ≤3-minute video demo](https://drive.google.com/file/d/1YXpP6BZX0QCNputBu7vanfl3royLB2t3/view?usp=sharing)

---

## Quickstart

Run a fresh clone from terminal to interactive UI in under two minutes:

```bash
# 1. Clone the repository
git clone https://github.com/prathamkashyap/fact-knowledge-layer.git
cd fact-knowledge-layer

# 2. Set up virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Verify regression test suite (266 passing)
python -m pytest tests/ -q

# 4. Load the bundled starter demo database
python scripts/load_starter_demo.py

# 5. Launch the application against the demo database
FACT_LAYER_DB_PATH=data/demo.db uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **[http://127.0.0.1:8000/ui](http://127.0.0.1:8000/ui)** in your browser. Real facts, cross-document comparisons, and evidence trails are visible immediately.

> **Clean Knowledge Base Mode:** Running the server without `FACT_LAYER_DB_PATH` starts a clean, empty knowledge base at `data/fact_layer.db`:
> ```bash
> uvicorn app.main:app --host 127.0.0.1 --port 8000
> ```

---

## Architecture

The system follows a linear, deterministic pipeline where each stage has a single responsibility and clean boundaries:

```
PDF Document
    │
    ▼
[ 1. PyMuPDF Parser ] ─── Extract text blocks, detect tables, score candidate pages
    │
    ▼
[ 2. Fact Extraction ] ── Pull structured assertions (Subject, Predicate, Value, Unit, Period, Evidence)
    │
    ▼
[ 3. Normalization ] ──── Canonicalize entities & metrics, standardize units & fiscal periods
    │
    ▼
[ 4. Candidate Matching ] Group by canonical subject+predicate, compute deterministic dimension diffs
    │
    ▼
[ 5. Epistemic Reasoner ] Classify relationship (CORROBORATES, CONTRADICTS, RECONCILABLE, etc.)
    │
    ▼
[ 6. SQLite Persistence ] Thread-safe storage with search indices & foreign key cascades
    │
    ▼
[ 7. FastAPI & UI ] ───── REST API + embedded zero-build Fact Explorer UI
```

### Pipeline Modules

| Stage | Module | Responsibility |
|---|---|---|
| **Parse & Prioritize** | `app/pdf_parser.py` | Extracts text and tables; prioritizes high-density candidate pages via keyword and numeric scoring. |
| **Extraction** | `app/pipeline.py`, `app/extraction.py` | Pulls grounded facts with verbatim source evidence sentences from candidate pages. |
| **Normalization** | `app/normalizer.py` | Canonicalizes company names and metrics; converts units (e.g. crore to million); normalizes fiscal periods. |
| **Matching** | `app/matcher.py` | Pairs facts sharing canonical subjects/predicates; evaluates per-dimension sameness (period, unit, scope, qualifiers). |
| **Reasoning** | `app/reasoner.py` | Applies strict epistemic rules to dimension diffs to classify relationship type, confidence, and rationale. |
| **Storage** | `app/database.py` | Thread-safe SQLite persistence for documents, pages, grounded facts, comparisons, and full audit traces. |
| **Interface** | `app/main.py` | FastAPI application exposing REST endpoints and the responsive, single-page Fact Explorer UI. |
| **Provider ABC** | `app/providers.py` | Abstract base class `LLMProvider` and deterministic `MockProvider` for test isolation. |

---

## Fact and Evidence Model

Facts are structured, verifiable units of knowledge rather than unstructured text chunks:

```python
class Fact(BaseModel):
    id: str                         # Unique identifier
    subject: str                    # Entity or topic (e.g. "India real GDP growth")
    predicate: str                  # Metric asserted (e.g. "growth rate", "revenue", "board status")
    value: Union[float, str]        # Quantitative (6.5) or categorical ("resigned from the Board")
    unit: Optional[str]             # Standardized unit (%, INR million, ₹ crore)
    period: Optional[str]           # Fiscal year or quarter (FY2024, 2024-25, Q4 FY24)
    as_of: Optional[str]            # Effective date for events (e.g. "August 24, 2023")
    scope: Optional[str]            # Reporting basis ("standalone", "consolidated")
    qualifiers: List[str]           # Context modifiers ("First Advance Estimate", "forecast")
    evidence: Evidence              # Source grounding: document_id, page_number, verbatim source sentence
    canonical_subject: str          # Normalized entity name
    canonical_predicate: str        # Normalized metric category
    normalized_value: float         # Converted value in baseline units
    normalized_unit: str            # Baseline unit
    normalized_period: str          # Standardized period string
```

### Relationship Categories & Epistemic Rules

Cross-document fact pairs are evaluated across all dimensions and assigned one of five relationships:

| Relationship | Definition | Example |
|---|---|---|
| **CORROBORATES** | Independent sources assert identical metrics for the same entity, period, and scope. | Two independent reports confirming the same executive resignation date. |
| **RECONCILABLE** | Apparent numeric or textual difference explained by contextual variance (vintage, scope, date). | 6.4% First Advance Estimate vs. 6.5% updated actual for same FY. |
| **CONTRADICTS** | Same entity, metric, period, and scope with mutually incompatible claims about a realized outcome. | Contradicting reported historical figures without reconciling context. |
| **UNCERTAIN** | Related topics, but missing context, ambiguous period, or detached table layout prevents safe judgment. | Standalone vs. consolidated revenues when reporting scope cannot be verified. |
| **UNRELATED** | Different entities or non-comparable metrics sharing superficial terminology. | Revenue of Company A vs. GDP of Country B. |

**Guiding Epistemic Principles:**
1. *A numerical difference is not automatically a contradiction.* Vintage, reporting scope, or accounting standards often reconcile differences.
2. *A numerical match is not automatically corroboration.* Matching numbers across different sub-periods or scopes are coincidental, not confirmatory.
3. *Forecasts are not facts.* Divergent projections from different institutions reflect model variance, not real-world contradictions.
4. *Grounding is non-negotiable.* An assertion without document, page, and exact source text is discarded.

---

## Starter Datasets & Demo Loader

The repository ships with two multi-source document packages in `starter-datasets/`:
1. **India Macroeconomy:** Official economic publications covering national accounts and GDP growth:
   - `01-india-economic-survey-2024-25-excerpt.pdf` (Government of India)
   - `02-rbi-annual-report-2024-25-excerpt.pdf` (Reserve Bank of India)
   - `03-imf-india-2025-article-iv-excerpt.pdf` (International Monetary Fund)
2. **Delhivery Corporate Disclosures:** Multi-format disclosures from an Indian logistics company:
   - `01-delhivery-prospectus-2022-excerpt.pdf` (IPO Prospectus)
   - `02-delhivery-annual-report-fy24-excerpt.pdf` (Annual Report FY24)
   - `03-delhivery-q4-fy24-earnings-presentation.pdf` (Quarterly Earnings)

### The Demo Loader (`scripts/load_starter_demo.py`)

To ensure a seamless evaluation experience, `load_starter_demo.py` automates ingestion:
- Locates both dataset directories and discovers all 6 PDFs.
- Resets a dedicated SQLite database at `data/demo.db` (leaving the default `data/fact_layer.db` clean).
- Calls the identical `process_document_pipeline` used by the production `/upload` endpoint (no mocked ingestion or hand-seeded facts).
- Outputs live summary counts and exits non-zero if any error occurs.

---

## Example Reasoning Cases

The Fact Explorer UI includes an **Example Reasoning Cases** tab displaying worked cross-document scenarios with transparent status labeling:

```
┌────────────────────────────────────────────────────────────────────────┐
│ Status: VERIFIED LIVE                                                  │
│ Case 3a: Estimate Vintage Reconciliation (Economic Survey vs IMF)      │
├────────────────────────────────────────────────────────────────────────┤
│ Fact A: India real GDP growth | 6.5 per cent | FY2024/25               │
│         Source: 03-imf-india-2025-article-iv-excerpt.pdf (p.10)        │
│         Evidence: "s real GDP grew by 6.5 percent in FY2024/25"        │
│                                                                        │
│ Fact B: India real GDP growth | 6.4 per cent | FY25                    │
│         Source: 01-india-economic-survey-2024-25-excerpt.pdf (p.4)     │
│         Evidence: "real GDP is estimated to grow by 6.4 per cent..."   │
│                                                                        │
│ Result: RECONCILABLE (Confidence: 95%)                                 │
│ Reason: Both figures refer to the same period, but represent different │
│         estimate vintages: standard (6.5%) versus First Advance        │
│         Estimate, estimated (6.4%).                                    │
└────────────────────────────────────────────────────────────────────────┘
```

### Case Status Breakdown

| Case | Scenario | Current Runtime Status | Notes |
|---|---|---|---|
| **Case 3a** | GDP Vintage Reconciliation | `VERIFIED LIVE` | Economic Survey 6.4% (First Advance Estimate) vs. IMF 6.5% reconciled by vintage qualifier. Pulled directly from database. |
| **Case 1** | GDP Growth Corroboration | `REGRESSION SCENARIO` | Covered by unit tests in `test_gate3.py` & `test_gate4.py`. In this clean run, GDP figures differ by vintage (reconciled in Case 3a). |
| **Case 2** | CPI Inflation Investigation | `REGRESSION SCENARIO` | Covered by `test_case_2_cpi_inflation_candidate_investigation` in `test_gate4.py`. The heuristic regex set does not extract CPI tables from the current excerpts. |
| **Case 3b** | Director Temporal Reconciliation | `VERIFIED LIVE` | Resigned p.33 vs. ceased p.40/43 with the same effective date reconcile as RECONCILABLE (temporal progression), not CONTRADICTS — Week 2 Defect B fix. Covered by `tests/test_status_reasoning.py`. |
| **Case 4** | Scope Distinction (Standalone vs Consolidated) | `VERIFIED LIVE` | Reporting basis and fiscal year are captured from the matched sentence; standalone vs. consolidated pair classifies RECONCILABLE by scope — Week 2 Limitation L1 fix. Covered by `tests/test_scope_extraction.py`. |

---

## API Reference

The FastAPI service exposes 10 REST endpoints:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | System status, reasoning mode, and aggregate database counts. |
| `POST` | `/upload` | Multipart upload for one or more PDF files for immediate pipeline execution. |
| `GET` | `/documents` | List all ingested documents with page counts and candidate ratios. |
| `GET` | `/documents/{id}` | Detailed page-by-page metadata and scoring breakdown for a document. |
| `GET` | `/facts` | Query extracted facts with filters (`search`, `document_id`, `limit`, `offset`). |
| `GET` | `/facts/{id}` | Single fact detail including verbatim evidence and all linked relationships. |
| `GET` | `/relationships` | Query comparisons with filters (`relationship`, `fact_id`, `document_id`). |
| `GET` | `/stats` | System summary statistics and relationship distribution breakdown. |
| `GET` | `/` | Responsive Fact Explorer single-page UI. |
| `GET` | `/ui` | Alias redirect to Fact Explorer UI. |

### Upload Example (cURL)

```bash
curl -X POST http://127.0.0.1:8000/upload \
  -F "files=@starter-datasets/delhivery/01-delhivery-prospectus-2022-excerpt.pdf"
```

---

## Fact Explorer UI

The embedded UI is implemented without frontend frameworks or external CDNs:
- **Fact Explorer Tab:** Real-time search across entities, metrics, and evidence snippets. Modal view reveals normalized values, fiscal periods, and cross-document links.
- **Cross-Document Relationships Tab:** Filter relationships by category (`CORROBORATES`, `RECONCILABLE`, `CONTRADICTS`, `UNCERTAIN`). Inspect side-by-side fact cards with dimension chips (`same` vs `different`) and reasoning rationale.
- **Example Reasoning Cases Tab:** Live demonstration cases with verified status indicators.
- **Upload & Ingest Tab:** Drag-and-drop PDF ingestion with immediate client feedback.
- **Documents Tab:** Ingested document catalog with candidate page ratios and processing latencies.

---

## Testing & Quality Gates

The test suite enforces five progressive quality gates plus end-to-end integration:

```bash
python -m pytest tests/ -q
```

```
===================== 266 passed, 7 warnings in ~95s =====================
```

| Test File | Count | Coverage Focus |
|---|---|---|
| `tests/test_gate1.py` | 20 | PDF text parsing, table detection heuristics, candidate page prioritization scoring. |
| `tests/test_gate2.py` | 46 | Structured fact schema validation, evidence grounding, regex pattern edge cases. |
| `tests/test_gate3.py` | 55 | Entity/predicate canonicalization, unit conversion (crore/million/lakh), period alignment. |
| `tests/test_gate4.py` | 37 | Epistemic relationship classification, confidence scoring, heuristic rule validation. |
| `tests/test_gate5.py` | 12 | FastAPI endpoints, health checks, persistence operations, UI serving. |
| `tests/test_pipeline_integration.py` | 28 | End-to-end multi-document pipeline runs, database round-trips, serialization. |
| `tests/test_demo_loader.py` | 6 | Demo path resolution, starter dataset discovery, clean error handling, e2e smoke load. |
| `tests/test_macro_indicators.py` | 22 | Named Cluster G gold regressions (ex-017..ex-024), negative grammar guards, corpus-wide blast-radius test, period-reconciliation reasoning. |
| `tests/test_evaluate.py` | 11 | Gold-set integrity (counts, ids, verdicts) and scoring-key semantics. |
| `tests/test_dedup.py` | 7 | Duplicate fact folding and provenance qualifiers (Week 2 Defect A). |
| `tests/test_evidence_validation.py` | 7 | Verbatim evidence grounding against the cited page before persistence. |
| `tests/test_scope_extraction.py` | 7 | Standalone/consolidated scope and fiscal-year capture (Week 2 L1). |
| `tests/test_status_reasoning.py` | 8 | Resigned→ceased temporal progression rule (Week 2 Defect B). |
| `tests/test_table_regression.py` | — | Multi-column table layout regression fixtures. |

---

## Design Decisions and Trade-offs

- **SQLite over Graph/Vector DBs:** The problem domain requires relational joins between facts, evidence, and comparisons. SQLite provides zero-configuration, thread-safe, ACID-compliant persistence without requiring background daemon processes or external infrastructure.
- **Deterministic Candidate Prioritization:** Running complete PDF text through language models is computationally expensive and slow. The pipeline scores pages by financial keyword density, table occurrences, and numerical frequency, focusing extraction on the top candidate pages.
- **Verbatim Evidence Linking:** Every fact retains an exact sentence slice from the source text. This provides human auditability and prevents ungrounded hallucinations.
- **Provider Abstraction with Offline Fallback:** An abstract `LLMProvider` interface decouples the core logic from specific model providers. The application defaults to deterministic offline heuristics so it can be evaluated reliably without API keys.
- **Single-File Zero-Build UI:** Keeping the frontend in vanilla HTML/CSS/JavaScript within the repository avoids Node.js dependencies, build steps, or bundle tooling.

---

## Known Limitations and Defects

In the spirit of complete engineering honesty, the following known gaps are documented:

1. **Table Layout Detachment:**
   Complex financial tables with multi-tier column headers can detach from numerical cells during raw PyMuPDF text extraction. (Note: a Week 3 audit confirmed 0 of the 13 tracked recall misses were caused by page selection or table layout — all 13 were sentence-pattern gaps — so table work is recall-neutral for the current harness, but the UI/extraction limitation itself remains.)
2. **Extraction Recall:**
   The offline extractor is precision-first: of 24 hand-verified gold claims in `docs/evaluation/extraction_gold.json`, the clean run matches 15 — the remaining 9 are deliberate recall misses (comparative-period figures, loss wording, textual as-of dates, associated-status updates, director appointments, an acronym/footnote GDP claim, and `Rs./Cr` KPI wording) tracked by the evaluation harness rather than pattern-guessed.

### Fixed in Week 2 (for the record)

- **Defect A — Duplicate Fact Extraction:** duplicate occurrences of the same claim (e.g. the same director cessation on pp. 40 and 43) are now grouped under one canonical fact by `dedupe_facts()`; suppressed occurrences are retained as `duplicate-evidence` page references. Covered by `tests/test_dedup.py`.
- **Defect B — Resigned vs. Ceased Classification:** same-date `resigned` → `ceased` board-status progressions now classify RECONCILABLE via a bounded status-progression rule instead of falling through to CONTRADICTS. Covered by `tests/test_status_reasoning.py`.
- **Limitation L1 — Standalone/Consolidated Scope:** the broad revenue pattern now recovers `on standalone/consolidated basis` and `FYxx` from the matched sentence, so the scope pair classifies RECONCILABLE instead of UNCERTAIN. Covered by `tests/test_scope_extraction.py`.
- **Evidence Validation:** every fact's evidence string is verified verbatim (whitespace-insensitive) against the raw text of its cited page before persistence; ungrounded facts are rejected and counted. Covered by `tests/test_evidence_validation.py`.

---

## Evaluation & Baseline

A formal baseline audit was established before polish work commenced. Full artifacts are archived in `docs/validation/`:
- [`docs/validation/BASELINE.md`](docs/validation/BASELINE.md): Detailed audit log, module inventory, grep records, and defect analyses.
- [`docs/validation/baseline_facts.json`](docs/validation/baseline_facts.json): The 13 grounded facts extracted from the 6 starter PDFs (Week 1 archive, pre-dedup).
- [`docs/validation/baseline_relationships.json`](docs/validation/baseline_relationships.json): The 9 relationships produced by the offline reasoning engine (Week 1 archive, duplicate-inflated).
- [`docs/validation/baseline_metrics.json`](docs/validation/baseline_metrics.json): Processing latencies and candidate page rates.

Week 2 added a quantitative harness with hand-verified gold sets:
- [`scripts/evaluate.py`](scripts/evaluate.py): fresh-run evaluation over the 6-PDF corpus; writes a diffable snapshot to `docs/evaluation/results.json` (latest run + bounded history of labeled runs).
- [`docs/evaluation/extraction_gold.json`](docs/evaluation/extraction_gold.json): 24 hand-verified gold claims (8 current-pipeline + 16 deliberate recall misses; `gold_version: week3-phase0`).
- [`docs/evaluation/relationship_gold.json`](docs/evaluation/relationship_gold.json): 19 source-verified pair expectations (13 label-expectation + 6 must-stay-absent; `gold_version: week3-phase0`).

**Baseline relationship composition (Week 1 archive, 9 rows):** of the 9 baseline relationship rows, all 3 `CONTRADICTS` were artifacts (Defect B, **0 genuine**); 4 `CORROBORATES` rows were same-document duplicate-extraction artifacts (Defect A); the GDP estimate-vintage `RECONCILABLE` was the genuine correct classification; and the revenue `UNCERTAIN` (missing scope context) became `RECONCILABLE` after Week 2 scope/context capture.

**Gold-set scope note:** the relationship gold set contains 19 source-verifiable expectations for the current corpus; expanding it further requires additional cross-document source material rather than synthetic cases — padding with unverifiable entries would weaken the evaluation. Methodology rule (Week 3): pair labels are assigned only after source-verifying each pair against the PDFs; copying the reasoner's own output into the gold set is explicitly banned, and a numerical match across two different series (e.g. headline vs. core inflation) must never be lazily labeled CORROBORATES.

### Week 3 Cluster G (gold_version week3-phase0)

A corpus-wide audit classified all 16 remaining `recall_miss` gold claims: **0 candidate-page misses, 0 table-layout misses, 13/13 sentence-pattern gaps**, clustered by cause (macro indicators, comparative periods, loss wording, textual as-of, associated status, appointments, GDP acronym, KPI units). Phase 0/1 closed the macro-indicator cluster:

- **Gold completeness first:** a blast-radius scan of all candidate pages found 3 additional source-verified claims beyond the original four (RBI annual `4.6 per cent in 2024-25`, RBI March 2025 `3.3 per cent`, IMF FY2026/27 projection `4 per cent`), and corrected ex-020's inferred period to the source wording (`September`). The induced matcher pairs were enumerated and source-verified (rel-010..rel-019: 6 RECONCILABLE, 4 UNCERTAIN) *before* any code changed. The `week2-frozen` results snapshot was left untouched.
- **Closed extraction map:** a four-indicator, one-grammar family (`private consumption growth`, `headline inflation`, `core inflation`, `unemployment` with an `(at|to) <number> percent` anchor) extracts exactly those 7 claims and nothing else — guarded by named per-claim regressions, negative-grammar guards, and a corpus-wide blast-radius test asserting the exact claim set.
- **Reasoner rule 8b (period reconciliation):** numeric same-metric pairs whose *both-known* reporting periods differ classify RECONCILABLE with the period difference stated (e.g. September 2025 vs. the 2024-25 annual average). Same-period differences still reach the contradiction rule; forecast pairs still reconcile via the existing forecast rule.
- **Two latent bugs found by the new pairs and fixed:** `normalize_period` collapsed `September 2025` and `March 2025` to the same bare-year key `2025` (which made two different months read as the same period); and the evaluator's claim-key stringifier keyed DB-row `str(4.0)` differently from gold `"4"`. Both had named regression tests added.

### Before / After (Week 2)

| Metric | Pre-fix baseline | Post-Week-2 |
|---|---|---|
| Extraction precision | 0.6154 | **1.0000** |
| Extraction recall | 0.3810 | 0.3810 |
| Extraction F1 | 0.4706 | **0.5517** |
| Period capture | 0.50 | **1.00** |
| Scope capture | 0.00 | **1.00** |
| Relationship accuracy | 0.5556 | **1.0000** |
| Absent-expectation compliance | 0.6667 | **1.0000** |
| Facts (clean run) | 13 (5 duplicate) | **8** |
| Relationships (clean run) | 9 (duplicate-inflated) | **3** (all RECONCILABLE) |

### Before / After (Week 3 Cluster G)

| Metric | Post-Week-2 (`week2-frozen`) | Post-Cluster-G (`week3-cluster-g`) |
|---|---|---|
| Extraction precision | 1.0000 | **1.0000** |
| Extraction recall | 0.3810 | **0.6250** |
| Extraction F1 | 0.5517 | **0.7692** |
| Period capture | 1.00 | **1.00** |
| Scope capture | 1.00 | **1.00** |
| Relationship accuracy | 1.0000 (9/9) | **1.0000 (19/19)** |
| Absent-expectation compliance | 1.0000 | **1.0000** |
| Facts (clean run) | 8 | **15** |
| Relationships (clean run) | 3 (all RECONCILABLE) | **13** (9 RECONCILABLE + 4 UNCERTAIN, 0 CONTRADICTS, 0 CORROBORATES) |

Reproduce with:

```bash
python scripts/evaluate.py --label <name>   # fresh run; appends to history in docs/evaluation/results.json
```

### Clean Ingest Summary

| Metric | Week 1 Baseline | Post-Week 2 | Post-Week 3 (Cluster G) |
|---|---|---|---|
| Ingested Documents | **6** | **6** | **6** |
| Pages Parsed | **511** | **511** | **511** |
| Extracted Facts | 13 (incl. 5 duplicates) | **8** (deduplicated) | **15** (deduplicated) |
| Relationships | 9 (duplicate-inflated) | **3** (all RECONCILABLE, 0 CONTRADICTS, 0 UNCERTAIN) | **13** (9 RECONCILABLE + 4 UNCERTAIN, 0 CONTRADICTS) |
| Total Ingestion Time | ~29 s | ~29 s (eval wall time ~36–55 s includes gold matching) | ~29 s (eval wall time ~36–55 s includes gold matching) |

*(Note: Earlier runbook documentation reflected an earlier spike using permissive, ungrounded heuristics. The current precision-focused offline pipeline yields 15 verified facts and 13 grounded relationships at precision 1.0000; the Week 1 archive of 13/9 remains in `docs/validation/` for provenance.)*

---

## Future Work

- **Extraction Recall:** 9 hand-verified gold claims in `docs/evaluation/extraction_gold.json` (`recall_miss`) remain unextracted: comparative-period figures (ex-009/010), standalone loss wording (ex-011), textual as-of dates (ex-012), associated-status updates (ex-013), director appointments (ex-014/015), the acronym/footnote GDP claim (ex-016), and `Rs./Cr` KPI wording (ex-021). Raise recall against the harness without regressing precision from 1.0000 — each cluster ships with source-verified gold first, a named regression, and a complete passing eval, per the Cluster G methodology.
- **Table Layout Detachment:** improve structured extraction for multi-tier financial tables (remaining known limitation above).
- **Concrete LLM Provider:** Wire a tested, provider-neutral client (Anthropic Claude, MiMo, or local Ollama) into the existing `LLMProvider` ABC for generalized table extraction. The provider interface exists; no live provider is currently configured or enabled.

---

## AI Tools Used

In accordance with transparent engineering disclosure, development of this project was assisted by:

- **MiMo (`mimo-v2.5-free`):** Primary agent for initial Gates 0–2 scaffolding, PyMuPDF candidate scoring routines, and core unit test suites.
- **GitHub Copilot:** Code completion and docstring typing assistance throughout development.
- **Gemini 3.8 Flash High:** Architecture review, implementation of Gate 3–5 normalization, candidate matching, epistemic relationship reasoning heuristics, SQLite persistence, FastAPI routes, zero-build UI, baseline audit verification, and repository polish.

All architectural decisions, epistemic rules, test validations, and factual claims were verified and tested directly in local execution.
