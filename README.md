# Superjoin Fact Knowledge Layer

A grounded fact extraction and cross-document epistemic reasoning system for the VIT 2026 Engineering Intern assignment.

The system ingests PDFs, extracts structured factual assertions with verbatim source evidence, normalizes metrics across documents, and determines whether cross-document fact pairs corroborate, contradict, reconcile, are unrelated, or remain uncertain.

## Overview

Most PDF analysis tools treat documents as opaque text blobs. This system takes a different approach: it extracts **grounded, independently attributable factual assertions** — each tied to a specific document, page, and source sentence — and then reasons about how those facts relate to each other across documents.

The core insight is that a numerical difference between two sources is not automatically a contradiction. Context matters: reporting scope, data vintage, effective dates, unit conventions, and forecast vs. actual distinctions all determine whether two facts agree, disagree, or are simply measuring different things at different times.

## Key Features

- **PDF ingestion** with PyMuPDF — extracts text blocks, detects tables, computes page-level scores
- **Deterministic candidate-page prioritization** — ranks pages by financial keyword density, numeric content, table presence, and heading structure
- **Structured fact extraction** — pulls subject/predicate/value/unit/period/scope/qualifiers with verbatim evidence
- **Evidence grounding** — every fact links to a specific document, page, and source sentence
- **Entity and predicate canonicalization** — normalizes corporate names, metric terms, and reporting language
- **Period normalization** — handles FY2024, 2024-25, Q4 FY24, calendar dates into comparable forms
- **Unit conversion** — INR crore to INR million, lakh to million, billion to million; percentage standardization
- **Numerical comparability analysis** — exact match, close-rounding detection, incompatible-unit flagging
- **Cross-document candidate matching** — groups by canonical subject+predicate, filters irrelevant pairs, computes dimension diffs
- **Epistemic relationship reasoning** — classifies CORROBORATES / CONTRADICTS / RECONCILABLE / UNRELATED / UNCERTAIN with confidence and rationale
- **SQLite persistence** — stores documents, pages, facts, comparisons with full evidence trails
- **FastAPI REST API** — 10 endpoints for upload, query, and exploration
- **Fact Explorer UI** — plain HTML/CSS/JS interface with search, detail modals, relationship browsing, and demonstration cases
- **Offline heuristic fallback** — works without any API key using deterministic pattern matching
- **Provider abstraction** — swap in any LLM provider (e.g. Anthropic Claude) via a simple ABC interface

## Architecture

```
PDF → PyMuPDF Parse → Candidate Page Prioritization → Fact Extraction
    → Normalization → Candidate Matching → Relationship Reasoning → SQLite → FastAPI / UI
```

### Pipeline Stages

| Stage | Module | Purpose |
|---|---|---|
| **Parse** | `pdf_parser.py` | Extract text blocks, detect tables, compute page scores using PyMuPDF |
| **Prioritize** | `pdf_parser.py` | Rank pages by financial relevance; select top candidates for extraction |
| **Extract** | `pipeline.py` | Pull structured facts from candidate pages using heuristic patterns or LLM provider |
| **Normalize** | `normalizer.py` | Canonicalize entities/predicates, normalize periods, convert units, assess numerical comparability |
| **Match** | `matcher.py` | Group facts by canonical subject+predicate, generate candidate pairs, compute dimension diffs |
| **Reason** | `reasoner.py` | Classify cross-document relationships using heuristic rules or LLM prompt with strict epistemic rules |
| **Persist** | `database.py` | Store everything in SQLite with full evidence and reasoning traces |
| **Serve** | `main.py` | FastAPI application with REST endpoints and embedded HTML/CSS/JS UI |

## Fact and Evidence Model

Each extracted fact is a structured assertion with the following fields:

| Field | Description |
|---|---|
| `subject` | Entity or topic the fact is about (e.g. "India real GDP growth", "Delhivery revenue from operations") |
| `predicate` | What is being asserted (e.g. "growth rate", "revenue", "board status") |
| `value` | The asserted value — numeric (`float`) or categorical (`str`) |
| `unit` | Unit of measurement (`%`, `INR million`, `₹ crore`, etc.) or `None` for categorical facts |
| `period` | Reporting period (`FY2024`, `2024-25`, `Q4 FY24`) |
| `as_of` | Effective or reporting date when it differs from the period (e.g. director resignation date) |
| `scope` | Reporting basis (`standalone`, `consolidated`, `segment`) |
| `qualifiers` | Additional context (`First Advance Estimate`, `forecast`, `provisional`) |
| `evidence` | Source grounding: `document_id`, `page_number`, verbatim `text`, `document_name` |
| `canonical_subject` | Normalized subject (populated during normalization) |
| `canonical_predicate` | Normalized predicate (populated during normalization) |
| `normalized_value` | Converted numeric value in standard units |
| `normalized_unit` | Standard unit after conversion |
| `normalized_period` | Standardized period string |
| `period_type` | Classification: `fiscal_year`, `quarter`, `date`, `as_of`, `unknown` |

The `value` field intentionally supports both numeric and categorical types. A fact like "Suvir Suren Sujan ceased to be a Director" has a categorical value, while "Real GDP grew by 6.5%" has a numeric value. Both are grounded assertions that can be compared across documents.

## Relationship Reasoning

The system classifies each cross-document fact pair into one of five labels:

| Label | Meaning |
|---|---|
| **CORROBORATES** | Both assertions independently support the same underlying fact in compatible context |
| **CONTRADICTS** | Assertions address the same fact in compatible context but make conflicting claims about a realized outcome |
| **RECONCILABLE** | Apparent difference is explained by a meaningful contextual distinction (scope, vintage, date, units, definitions) |
| **UNRELATED** | Assertions concern different entities or incompatible metrics |
| **UNCERTAIN** | Same subject, but insufficient evidence or ambiguous context to decide reliably |

### Critical Epistemic Rules

The reasoner enforces rules that prevent naive numeric comparison:

1. A numerical **difference** is not automatically a contradiction. Reporting scope, data vintage, or unit conversion can explain it.
2. A numerical **match** is not automatically corroboration. Two sources might arrive at the same number through different methodologies or for different sub-periods.
3. Different **forecasts** from different institutions (e.g. RBI 6.5% vs IMF 6.6%) are not contradictions. They reflect different modeling assumptions about future outcomes.
4. Apparent differences **explained by context** (standalone vs consolidated, First vs Second Advance Estimate, different as-of dates) are classified as RECONCILABLE with the explaining dimension stated.
5. Ambiguous evidence (e.g. table column headers detached from data) results in UNCERTAIN rather than a forced judgment.

## Demonstration Cases

The starter datasets provide two groups of documents for demonstration:

### India Macroeconomy Dataset

Three institutional reports with overlapping facts about the Indian economy:

| Document | Source |
|---|---|
| `01-india-economic-survey-2024-25-excerpt.pdf` | Government of India Economic Survey |
| `02-rbi-annual-report-2024-25-excerpt.pdf` | Reserve Bank of India |
| `03-imf-india-2025-article-iv-excerpt.pdf` | IMF Article IV Consultation |

**Case 1 — GDP Estimate Vintage Reconciliation (Economic Survey vs IMF) — VERIFIED LIVE:**

The Economic Survey reports India's real GDP growth at 6.4% (First Advance Estimate) while the IMF reports 6.5% for the same period. The system classifies this as **RECONCILABLE** — the numerical difference is explained by data vintage (First Advance Estimate vs finalized figure), not a genuine factual conflict. The reasoner identifies the estimate-vintage qualifier and produces the explanation: *"Both figures refer to the same period, but represent different estimate vintages."*

**Case 2 — CPI Potential Conflict:** CPI inflation figures of 4.6% vs 4.4% appear across sources. The RBI figure is an actualized historical value while the IMF figure appears in its projection block. The system treats this as a potential conflict requiring contextual and vintage interpretation, not as an established contradiction.

### Delhivery Corporate Dataset

Three disclosure formats for the same logistics company:

| Document | Source |
|---|---|
| `01-delhivery-prospectus-2022-excerpt.pdf` | IPO Prospectus (2022) |
| `02-delhivery-annual-report-fy24-excerpt.pdf` | Annual Report FY24 |
| `03-delhivery-q4-fy24-earnings-presentation.pdf` | Q4 FY24 Earnings Presentation |

**Case 3 — Scope Distinction (Standalone vs Consolidated):** Revenue figures differ between standalone (₹74,540.82 million) and consolidated (₹81,415.38 million) reporting bases. The system classifies this as RECONCILABLE — the scope distinction explains the numerical difference.

**Case 4 — Director Temporal Reconciliation:** Director appointment/resignation timelines reference different as-of dates across the prospectus and annual report. The system classifies these as RECONCILABLE — the facts are consistent when the effective dates are considered.

**Case 5 — Rounding/Unit Normalization:** Revenue expressed as ₹8,142 crore in one source and ₹81,415.38 million in another. After unit conversion to INR million, these match. The system classifies this as CORROBORATES.

**Case 6 — Ambiguous Table Extraction:** Some table data has detached headers or ambiguous period associations. The system classifies these as UNCERTAIN rather than forcing a relationship judgment.

## Setup

```bash
git clone <repository-url>
cd Superjoin

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run tests
python -m pytest tests/ -q
```

### Dependencies

- `fastapi` — Web framework
- `uvicorn` — ASGI server
- `python-multipart` — File upload support
- `pydantic` — Data validation and schemas
- `pymupdf` — PDF parsing
- `pytest` — Test framework
- `httpx` — HTTP client (for API testing)
- `anthropic` — Optional: live Claude API integration (not required for offline mode)

## Run the Application

```bash
# Start the server
uvicorn app.main:app --host 127.0.0.1 --port 8000

# Open in browser
open http://127.0.0.1:8000/ui
```

The application works without any API key. In offline mode, it uses deterministic heuristic extraction and reasoning.

## API

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | System status, database stats, reasoning mode |
| `/upload` | POST | Upload one or more PDFs for processing |
| `/documents` | GET | List all ingested documents |
| `/documents/{id}` | GET | Document details with page metadata |
| `/facts` | GET | List extracted facts (supports `search`, `document_id`, `limit`, `offset` filters) |
| `/facts/{id}` | GET | Single fact with full evidence and relationships |
| `/relationships` | GET | Cross-document relationships (supports `relationship`, `fact_id`, `document_id` filters) |
| `/stats` | GET | Aggregate statistics |
| `/` | GET | Fact Explorer UI |
| `/ui` | GET | Fact Explorer UI (alias) |

### Upload Example

```bash
curl -X POST http://127.0.0.1:8000/upload \
  -F "files=@starter-datasets/delhivery/01-delhivery-prospectus-2022-excerpt.pdf"
```

## UI

The Fact Explorer is a single-page application built with plain HTML, CSS, and JavaScript (no frameworks, no build step).

**Tabs:**

- **Fact Explorer** — Search and browse all extracted facts with evidence previews. Click any fact card for a detailed modal showing canonical forms, normalized values, source evidence, and all cross-document relationships.
- **Cross-Document Relationships** — Browse all relationship pairs with filter buttons for CORROBORATES, RECONCILABLE, CONTRADICTS, and UNCERTAIN. Each card shows both facts side-by-side with dimension chips, source documents, and the reasoning rationale.
- **Demonstration Cases** — Navigate directly to the verified assignment demonstration cases.
- **Upload & Ingest** — Upload arbitrary PDFs through the browser. Processing happens server-side with progress feedback.
- **Documents** — Table view of all ingested documents with page counts, candidate rates, fact counts, and processing times.

The header displays the reasoning mode badge (Offline / Heuristic Mode) and aggregate statistics.

## Reasoning Modes

### Offline Heuristic Mode (Default)

Works without any API key. Uses:
- Deterministic regex patterns for fact extraction from financial text
- Rule-based relationship classification using dimension diffs (period, scope, qualifiers, units, values)
- Estimate-vintage detection for reconciling different advance/provisional/preliminary figures
- Threshold-based confidence scoring

This mode supports the current assignment demonstration and has been validated against the provided starter datasets.

### Provider Abstraction

The `LLMProvider` abstract base class defines interfaces for LLM-based fact extraction and relationship reasoning. `MockProvider` returns deterministic fixtures for testing. A production provider (e.g. Anthropic Claude) can be implemented and swapped in by setting an API key. The system always falls back to the heuristic mode when no provider is configured.

## Testing

```
198 tests passed, 0 failed
```

Tests cover all five gates:

| Test File | Count | Coverage |
|---|---|---|
| `test_gate1.py` | 20 | PDF parsing, page scoring, candidate prioritization |
| `test_gate2.py` | 46 | Fact extraction, schema validation, parsing edge cases |
| `test_gate3.py` | 55 | Normalization, canonicalization, unit conversion, matching |
| `test_gate4.py` | 37 | Relationship reasoning, heuristic classifier, provider fallback |
| `test_gate5.py` | 12 | API endpoints, UI, file upload, persistence |
| `test_pipeline_integration.py` | 28 | End-to-end pipeline, database round-trips, serialization |
| `test_table_regression.py` | — | Table extraction regression fixtures |

The full suite is verified in a fresh virtual environment from `requirements.txt`.

## Design Decisions and Trade-offs

**SQLite instead of a graph database.** The assignment prefers a small prototype. SQLite provides zero-config persistence and relational querying without external dependencies. The data model is inherently relational (facts link to documents, comparisons link to facts), so a graph database adds complexity without proportional benefit at this scale.

**Deterministic candidate prioritization.** Rather than sending every page to an LLM (expensive, slow, rate-limited), the system scores pages by financial keyword density, numeric content, table presence, and heading structure. This selects the most information-rich pages for extraction, reducing downstream cost.

**Structured facts with evidence.** Every fact carries verbatim source text, document ID, and page number. This makes the system auditable — any extracted assertion can be traced back to its exact source sentence. The evidence trail is not optional metadata; it is a core design requirement.

**Provider abstraction.** The `LLMProvider` ABC separates extraction and reasoning logic from any specific provider. `MockProvider` returns deterministic fixtures for testing. A production provider can be implemented and swapped in when an API key is available. The heuristic fallback operates when no provider is configured.

**Plain HTML/JS UI.** We chose vanilla HTML, CSS, and JavaScript to avoid frontend build complexity. The embedded single-page application uses fetch calls to the API. No build step, no node_modules, no framework overhead.

## Limitations

- **Heuristic extraction coverage.** The regex-based extractor handles common financial sentence patterns (GDP growth, revenue figures, director events) but does not cover all possible fact formats. Unsupported wording or table layouts may result in `UNCERTAIN` or no extracted fact. An LLM-backed extractor would handle arbitrary document layouts.
- **Table layout ambiguity.** PyMuPDF's table detection does not always associate column headers with data cells correctly. This can result in UNCERTAIN classifications for table-extracted facts.
- **Offline reasoning semantic depth.** The heuristic reasoner uses dimension diffs and rule thresholds. It cannot match the nuanced contextual reasoning of a live LLM, particularly for complex multi-dimensional comparisons.
- **Prototype-scale persistence.** SQLite is suitable for demonstration but would need migration to a production database (PostgreSQL, etc.) for concurrent multi-user workloads.

## AI Tools Used

Development was assisted by:

- **MiMo (mimo-v2.5-free)** — Primary implementation agent for Gates 0–2 and test infrastructure. Wrote regression tests, pipeline integration tests, and the initial README draft. Performed post-change verification passes and surgical bug fixes.
- **Copilot** — Code review and suggestion support throughout development.
- **Gemini 3.8 Flash High** — Implemented and reviewed substantial Gate 3–5 work including fact normalization, candidate matching, relationship reasoning, the heuristic classifier, provider abstraction, SQLite persistence, FastAPI endpoints, and the Fact Explorer UI. Performed independent review of normalization logic, matching heuristics, and reasoner prompt design.

All AI-assisted code was reviewed, tested, and validated before commits. The author is responsible for design decisions, architecture, and correctness claims.

## Future Work

- LLM-backed extraction for arbitrary document layouts
- Full-text search with embeddings for semantic fact retrieval (not part of current implementation)
- Temporal versioning of facts (track how a metric changes across report releases)
- Multi-page fact extraction (facts spanning table continuations)
- Incremental re-ingestion (update knowledge base when new documents arrive)
- Production authentication and authorization
- Export to standard formats (JSON-LD, CSV)
