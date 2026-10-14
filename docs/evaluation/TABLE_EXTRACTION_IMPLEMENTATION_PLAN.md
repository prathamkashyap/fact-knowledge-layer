# FKL Table Extraction and Attribution Improvement Plan

**Status:** design only. No implementation, extraction, PDF parsing, tests, or evaluation were run for this plan.

## Decision summary

Improve the current deterministic pipeline by retaining spatial table structure alongside the existing page text, mapping rows and columns into facts only when their context is recoverable, and refusing to guess entity, period, currency, or reporting basis when evidence is ambiguous. Start with the installed PyMuPDF dependency and a small adapter over its word geometry and table facilities. Compare it with `pdfplumber` on controlled fixtures before adding a dependency. Keep `PageObject`, `Fact`, `Evidence`, the normalizer, and the existing evidence-validation stage; extend them narrowly where the new traceability requirements cannot fit.

The first implementation slice should be a generic table representation plus one layout-to-fact path, demonstrated on synthetic tables and one already-inspected financial table layout. It should not begin by lowering the candidate-page score threshold: the audit found all 50 annotated evidence pages already selected.

## Evidence from the first run and audit

The frozen production baseline is `d9c68bd424883a09107b7507e348107e9f4fbc41`; the independently annotated gold set is at `979563f9af7bb054fd24c856010998a9be989bdd`. The first run is preserved at result commit `82d49332e44dff1b529188700103bcad2df60ee2`; its forensic audit is `6e5516c213a77423163229ed0923bcc14f8dc579`.

- **Observed outcomes:** on the authorized Rolls-Royce report, 220 pages yielded 114 candidate pages, zero raw candidates, and zero final facts against 27 sampled annotations. On the authorized IMF UK report, 102 pages yielded 34 candidate pages, one raw and final candidate, and zero matches against 23 annotations. The one prediction was “India headline inflation,” from prose on physical page 11, rather than a UK fact from the annotated table on physical page 5. See `HELDOUT_FAILURE_AUDIT.md`, “Confirmed root cause” and “Evaluation compatibility.”
- **Page selection is not the gap shown by this run:** all 50 gold quotations occur in stored text on their expected 1-based physical pages, and those pages passed the existing candidate score. `PageObject.candidate_pages_count` counts pages whose `ScoreBreakdown.is_candidate` is true; it does not mean a page has parsed table cells or valid facts (`app/pdf_parser.py`, `DocumentIngestionResult`, `compute_page_score`, and `parse_pdf_document`). Keep candidate scoring as prioritization telemetry; do not tune its threshold as the first response.
- **Structure is discarded before extraction:** `parse_pdf_page` stores `page.get_text("text")` plus block text and bounding boxes. Its `has_tables` value is a scoring heuristic using numeric lines and, conditionally, `page.find_tables()`; it does not retain rows, columns, or cells (`app/pdf_parser.py:176-240`). `extract_heuristic_facts_from_page` joins nonempty text lines into one string before applying text rules (`app/pipeline.py:57-69`). Existing fixtures explicitly demonstrate year headers detached from values in plain-text RBI and IMF table excerpts (`tests/test_table_regression.py`; `tests/test_gate1.py`).
- **Attribution has a source-independent default:** the closed macro indicator tuple assigns “headline inflation” the subject “India headline inflation” regardless of source context (`app/pipeline.py:101-148`). The IMF UK result demonstrates the failure. Entity attribution must use evidence from the claim and its enclosing context; filename-derived subject fallback in `_extract_entity_from_context` is not adequate as the sole source.
- **Unit and basis risks are separate from the zero-output cause:** `normalize_unit_and_value` converts generic “million” and “billion” units to INR magnitude units (`app/normalizer.py:284-355`). That can corrupt foreign-currency amounts such as GBP million. `Fact` currently has `unit`, `period`, `as_of`, `scope`, and free-form `qualifiers`, but no typed reporting-basis member (`app/models.py:20-45`). Neither issue explains the first-run zero by itself because no Rolls-Royce facts reached normalization.
- **Evidence validation is reusable but page-text-only:** `validate_fact_evidence` checks that the fact's evidence text appears on its cited page (`app/pipeline.py:508-544`). Table extraction should preserve this grounding guarantee and add enough region/cell provenance to audit a numeric mapping.
- **The evaluation boundary is important:** the gold samples selected sections; it is not an exhaustive annotation of either PDF. The frozen protocol defines sampled-gold recall, leaves unmatched predictions unadjudicated, and says precision/F1/false-positive counts are not defensible (`HELDOUT_BASELINE_PROTOCOL.md`). These 50 annotations have now been inspected and used to diagnose the implementation; they are development/regression material, not a blind test for revisions.

Audit evidence is preserved in the committed report and raw run artifacts. The proposed work does not alter that report, the first-run outputs, gold, or their manifests.

## Target architecture

### 1. Retain the existing ingestion and pipeline boundaries

Continue to parse each PDF page once into `PageObject`, retain `raw_text` for current rules and evidence checks, and call the existing deterministic extraction/normalization/deduplication/validation pipeline. Add a table-layout intermediate representation to the page parser rather than replacing ingestion or rewriting all rules. A separate table adapter should convert sufficiently resolved rows into the existing `Fact` model. Existing narrative rules can remain in place while table facts use this new path.

The first layout representation should be a typed, serializable `TableCandidate` containing a stable page-local ID, page number, table bounding box, detection method, and confidence/diagnostics. It should contain ordered `TableCell` records with row and column indices, text, bounding box, and optional row/column span. A derived row view should retain:

- row-label cells, column-header paths, and header-to-value association;
- parsed surface value and raw token, sign (including parentheses and explicit minus), currency code/symbol, scale (`thousand`, `million`, `billion`), and unit kind (percent, percentage point, per-share, count, currency, or unresolved);
- column period and whether that period is a fiscal year, calendar year, quarter, or point in time;
- section/table heading, issuer/country, business segment, and applicable footnote/qualifier references;
- evidence coordinates linking a fact to the source page, table, row, column, and value/label cell regions.

Do not equate a detected grid with a semantic table. A table candidate is only a layout hypothesis. Promotion to a fact requires a recoverable row label, a value cell, compatible column header, and enough context to resolve the fact dimensions. Incomplete or contradictory context stays as a diagnostic or unresolved candidate and is not emitted as a confident `Fact`.

### 2. Parser choice: existing dependency first, dependency experiment before adoption

**Smallest option — existing PyMuPDF.** It is already a required dependency (`requirements.txt`); `app/pdf_parser.py` already uses text blocks, `get_drawings()`, and `page.find_tables()`. Extend parsing to obtain word-level text and bounding boxes, then group words into lines, columns, cells, and table regions. Where `find_tables()` returns usable cells, adapt its output; where tables are borderless, use word alignment and repeated x-coordinate bands as a generic fallback. Preserve the original page text and include detector diagnostics so a failed table hypothesis can be explained. This avoids a new runtime dependency and should be the default first experiment.

**Alternative — `pdfplumber`.** It exposes lower-level characters, lines, rectangles, and table-finding strategies that may help with borderless or irregular layouts, at the cost of a new dependency and a second layout behavior to support. Do not adopt it based on feature descriptions alone. In a later implementation task, compare the current PyMuPDF adapter and a small `pdfplumber` spike on generated bordered/borderless fixtures plus the already-inspected RR and IMF layouts. Score cell text accuracy, row/column placement, header association, evidence-region quality, runtime, and failure diagnostics. Add it only if it provides a material, reproducible structural advantage that the PyMuPDF path cannot achieve with bounded complexity. OCR, Camelot, and hosted document services are outside the first deterministic slice; scanned-PDF support is not a requirement established by this audit.

### 3. Source-aware entity attribution with abstention

Resolve a claim's subject using this strict evidence precedence:

1. **Claim-local mention:** an explicit entity in the row label or nearby sentence/claim text.
2. **Table or section heading:** an entity/jurisdiction that scopes the table or section, unless the row/column identifies a different entity or a comparative context.
3. **Verified document scope:** use only when document content establishes one unambiguous issuer or jurisdiction for the claim. A filename alone is never sufficient evidence.
4. **Unresolved:** leave subject unresolved or mark attribution status unresolved; do not substitute a default such as India.

Higher-precedence evidence overrides lower-precedence scope only when explicit. Contradictions between levels must be surfaced as ambiguity, not silently resolved. For multi-country tables, identify each country from the corresponding row/column group and emit one fact per entity only when the alignment is clear. For subsidiaries, preserve the named legal entity as subject and retain parent affiliation separately if available. For business segments, keep the issuer as entity and the explicit segment in `scope` or a controlled segment qualifier; do not merge segment revenue into issuer-wide revenue. If a comparative table has no reliable row-to-entity mapping, abstain.

This requires replacing hard-coded source-independent macro subjects and limiting filename-based fallback attribution. It does not require a general knowledge resolver or an LLM.

### 4. Preserve monetary units, periods, status, and reporting basis

Use a single unit parser that carries the raw surface form and structured interpretation. Currency and scale are independent dimensions: `£250 million` means GBP with a 10^6 scale; it must never become INR because the word “million” appears. Normalize magnitude only within an explicit same-currency unit and only with exact scale arithmetic. Keep the displayed amount available for evidence. Prefer `Decimal` while parsing and scaling; convert at the existing `Fact` boundary only if persistence/comparison precision is demonstrated safe. An unknown currency or ambiguous scale must remain unresolved, not be filled from locale.

Keep `%` versus percentage points, percent-of-GDP, per-share values, and counts as distinct unit kinds. Preserve negative signs, parentheses, dashes, and footnote markers until their meaning is validated. Associate each value with its header path so multiple reporting years do not collapse into a single unqualified fact. Preserve actual/forecast status only when supported by a column header, section marker, or linked footnote; period and actual/forecast status are independent dimensions.

For **underlying versus statutory**, the gold schema and matching protocol show this distinction is material. Start with a closed, validated qualifier vocabulary attached to facts and included in deduplication/matching keys. Keep `scope` for consolidation/segment/jurisdiction. Add a typed `reporting_basis` field only if the prototype proves that free-form qualifiers cannot preserve basis through normalization, persistence, deduplication, and comparison. If added, make it optional, use an enumerated vocabulary with an explicit unknown state, and add a database migration plus round-trip tests. This is a justified narrow schema change, not a general Fact redesign.

### 5. Evidence traceability and rejection behavior

Retain `Evidence.page_number` and source text. Extend the evidence representation with optional table/cell identifiers and normalized bounding boxes, or store an equivalent immutable extraction trace linked to the fact. The trace must make it possible to inspect the exact label/header/value/footnote source for a fact. Persist it through `app/database.py`; merely constructing an in-memory bbox that is lost on save is not enough. Keep the existing evidence-text validation, and add checks that region coordinates are within the cited page and the referenced source text is present in the corresponding cell/region. Missing entity, period, unit, or basis context should produce an explicit unresolved/rejected diagnostic rather than a guessed fact.

## Implementation roadmap and gates

Effort estimates are person-days for one contributor and exclude review/queue time. They are planning estimates, not commitments to performance gains.

| Phase | Scope and expected files | Effort / main risk | Acceptance and stop/go gate |
|---|---|---|---|
| **0. Contract and fixture inventory** | Define table/cell/evidence types and unresolved-state semantics. Add synthetic fixtures only. Expected: `app/pdf_parser.py` design notes, new `app/table_extraction.py` (types may live here), `tests/test_table_extraction.py`. | 0.5–1 day. Risk: an underspecified representation that cannot express merged headers or footnotes. | **Go** only when examples cover row labels, multi-level headers, merged cells, periods, currencies, segment, and footnote references; unresolved cases are represented without fake values. No production extraction change yet. |
| **1. Layout adapter spike (smallest useful slice)** | Add PyMuPDF word geometry and a generic row/column reconstruction adapter; retain old raw text. Compare with `pdfplumber` only in an isolated experiment if fixtures reveal a PyMuPDF limitation. Expected: `app/pdf_parser.py`, `app/table_extraction.py`, `tests/test_table_extraction.py`, dependency files only if justified. | 1–3 days. Risk: borderless tables, rotated text, multi-column layouts, merged cells, and `find_tables()` false positives. | **Go** when synthetic bordered and borderless tables preserve cell order and coordinates; the known detached-header fixtures can associate year headers with the correct row values; malformed layouts return ambiguity diagnostics. Add no dependency unless the comparison shows material benefit. |
| **2. One generic table-to-fact path** | Map label/value/header-path records to existing `Fact`/`Evidence`; call existing normalization, dedupe, and evidence-validation boundaries. Keep candidate page scoring unchanged. Expected: `app/table_extraction.py`, `app/pipeline.py`, `app/models.py` only for necessary evidence references, `app/database.py` only if trace persistence requires it, and targeted tests. | 2–4 days. Risk: false table structures being accepted as claims; persistence/API compatibility for optional trace fields. | **Go** when tests show correct row/column alignment for multiple periods/entities, every fact has page/table/cell trace, invalid/missing associations are rejected or unresolved, and legacy facts still serialize and persist. Do not claim recall improvement from fixtures. |
| **3. Entity attribution and ambiguity policy** | Implement precedence resolution over claim, table/section, and verified document contexts; remove source-independent country defaults. Expected: `app/pipeline.py` or a small `app/entity_attribution.py`; `tests/test_entity_attribution.py`; focused updates to macro tests. | 1–2 days. Risk: conflicting scopes and ambiguous comparative layouts. | **Go** when explicit claim-local entity wins, scoped heading applies only within its section/table, verified single-document scope is used only when unique, multi-entity rows remain distinct, and absent/conflicting context never yields a guessed subject. |
| **4. Currency, periods, and basis correctness** | Parse exact amount/unit components, protect currencies and signs, carry header period and actual/forecast markers, validate basis qualifiers; decide on typed basis field from phase 2 evidence. Expected: `app/normalizer.py`, possibly `app/models.py`/`app/database.py` and a schema migration, `tests/test_normalizer.py` (or existing `tests/test_gate3.py`), round-trip tests. | 1–3 days. Risk: legacy unit aliases, float precision, migration/backward compatibility, footnotes with broad scope. | **Go** only when GBP million remains GBP million, INR conversions remain correct, signs and percentage types are preserved, periods/status remain distinct, underlying/statutory facts never dedupe together, and old persisted records load unchanged. |
| **5. Controlled regression and freeze** | Run targeted and full regression checks in a separate development environment; produce a new versioned run record without replacing first-run artifacts. Compare new predictions to immutable raw first-run output and gold; adjudicate unmatched predictions separately. Expected: relevant test files, a new evaluation report/manifest under `docs/evaluation/` only if explicitly authorized. | 1–2 days plus review. Risk: overfitting the inspected 50 annotations and treating non-exhaustive labels as full precision coverage. | **Go** to freeze only after all acceptance tests pass, output and config hashes are recorded, gold and first-run hashes are unchanged, and every matched/unmatched/ambiguous outcome is explained. Report sampled-gold recall descriptively; precision/F1 remain not defensible without an exhaustive scope. |

### Cross-phase acceptance tests

Use synthetic fixture tables for deterministic unit tests and retain the existing table examples as regressions. Required cases include:

- multiple rows, columns, multi-level and repeated page headers, wrapped labels, merged cells, blank cells, and values with signs;
- year/quarter headers tied to the correct value cells; distinguish actual columns from projected columns;
- separate entities in comparative rows/columns and segment rows; unresolved entity for missing or contradictory context;
- GBP and INR at thousand/million/billion scales; no cross-currency conversion; exact sign, percentage, percentage-point, percentage-of-GDP, and per-share behavior;
- underlying and statutory values for the same metric remain distinct through normalize, dedupe, and database round trip;
- evidence page, region, table, row, column, value, row label, and footnote references survive persistence; out-of-page, missing-cell, mismatched-header, or absent-text evidence is rejected or left unresolved with a reason;
- existing narrative extraction and legacy facts continue through the existing normalizer and evidence validator without requiring table metadata.

Do not weaken tests or validation to accept a higher number of predictions. A detected table with uncertain cell mapping is a failed/ambiguous parse, not a successful fact extraction.

## Evaluation and data boundaries

1. Preserve the frozen tag, first-run commit, original prediction/report artifacts, raw outputs, and manifests byte-for-byte. New runs go to a new branch, directory, and manifest with hashes and implementation commit/config recorded.
2. Compare each new run against the immutable first-run outputs: raw candidates, final validated facts, matched annotated IDs, unmatched gold IDs, duplicates, conflicts, and ambiguity counts. Report sampled-gold recall per document and overall. Keep all unmatched predictions as unadjudicated until reviewed against source evidence.
3. The 50 annotated RR/IMF facts are now known development examples. They may be used for regression and exploratory error analysis, with that disclosure; they must not be presented as an untouched blind evaluation or used to claim general precision. Do not change the gold to match extractor behavior.
4. Freeze code, rules, dependencies, and parameters before blind evaluation. The Bank of England report remains sealed and must not be opened, inspected, hashed, parsed, or otherwise accessed during implementation. Reserve it for a later independent blind run after freeze. Seek a second independently annotated source/document family for coverage and reproducibility; identify and authorize that source separately without looking at any sealed file.
5. No expected recall gain is promised. Each phase has to earn progression through structural, semantic, and evidence acceptance gates before held-out measurement.

## Risks and open design experiments

**Confirmed requirements from repository evidence:** preserve source structure and header-to-value relation; correct the source-independent India default; preserve currency; keep basis, period, and evidence dimensions distinct; reject or expose ambiguity; protect the first run and gold.

**Options that need a bounded experiment:** whether PyMuPDF's word geometry and `find_tables()` are sufficient for the inspected layouts; whether a `pdfplumber` adapter improves cell alignment enough to justify a dependency; whether bbox/cell coordinates belong directly on optional `Evidence` fields or in a separate persisted extraction trace; whether `reporting_basis` can remain a controlled qualifier or needs a typed field; whether float persistence is sufficiently exact for the currencies/scales in scope.

Principal risks are false-positive table detection, header carry-over across page breaks, footnotes scoped to the wrong rows, ambiguous multi-country and segment context, loss of currency during normalization, and regression in existing narrative facts. Keep diagnostics and an abstain path for each. Do not compensate with document-specific coordinates, filename-only entity rules, global threshold lowering, OCR, or ad hoc regex additions.

## Later LLM gate

Consider an LLM only after the deterministic table representation, entity policy, unit model, and evidence links are stable and the acceptance suite shows the remaining failures are semantic interpretation cases rather than layout/provenance defects. Any later provider must be optional, offline-compatible tests must remain, every proposed fact must cite page/cell evidence, and deterministic validation must reject unsupported values/entities/periods/bases. Freeze its prompt/model/parameters before a blind evaluation on the sealed Bank of England report and an independently annotated source. Do not use the current 50 facts as a tuning target or claim improvement from a single document pair.

## Anticipated implementation file list

Expected first-pass files, subject to the gates above:

- `app/pdf_parser.py` — retain spatial text and attach generic table hypotheses to pages.
- `app/table_extraction.py` — new table/cell representation, geometric reconstruction, header association, and row-to-fact adapter.
- `app/pipeline.py` — route table rows through existing deterministic extraction, normalization, deduplication, and evidence-validation flow; replace hard-coded attribution defaults.
- `app/entity_attribution.py` — optional small module if it keeps the precedence policy testable and isolated.
- `app/models.py` and `app/database.py` — only for optional evidence-region persistence or a justified typed reporting-basis field; include migration/round-trip support if required.
- `app/normalizer.py` — currency-preserving exact scale parsing and typed unit normalization.
- `tests/test_table_extraction.py`, `tests/test_entity_attribution.py`, and focused updates to `tests/test_gate1.py`, `tests/test_gate3.py`, `tests/test_evidence_validation.py`, `tests/test_comparative_highlights.py`, and/or `tests/test_pipeline_integration.py`.
- `requirements.txt` — only if a documented comparison justifies adding a PDF-layout dependency.

No files outside this list should be assumed necessary. Gold files, frozen outputs, `docs/evaluation/results.json`, `baseline-week5`, and remote refs remain outside the implementation scope.
