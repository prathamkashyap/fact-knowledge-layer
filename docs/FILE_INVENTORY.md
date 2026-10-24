# File Inventory — Fact Knowledge Layer (audit state at 5f6d2ef / branch codex/fkl-repository-state-audit)

## Purpose
High-level inventory of tracked source, test, script, configuration, documentation, evaluation, and project assets. Excludes excluded source PDF categories (sealed Bank of England Feb 2026 report; Rolls-Royce 2025 annual; IMF UK 2026 Article IV) without exposing their paths.

## Source (app/)

| Path | Role | Status | Evidence / Note |
| --- | --- | --- | --- |
| app/__init__.py | Package init | Current | — |
| app/main.py | Entry / UI | Current | FastAPI / HTML explorer |
| app/pdf_parser.py | PDF ingestion | Current | Phase 1 milestone |
| app/extractor.py | Fact extraction | Current | Grounded evidence |
| app/normalizer.py | Normalization | Current | Phase 3 milestone |
| app/matcher.py | Matching | Current | Phase 3 milestone |
| app/reasoner.py | Epistemic reasoning | Current | Phase 4 milestone |
| app/models.py | Data models | Current | — |
| app/database.py | Storage | Current | — |
| app/table_extraction.py | Layout adapter | Current | Quarterly regression verified; diagnostics: `period_header_conflict`, `ambiguous_header_span`, `period_header_outside_candidate_region` documented |
| app/pipeline.py | Pipeline orchestration | Current | — |
| app/providers.py | LLM provider abstraction | Current | — |

## Tests (tests/ — 21 files; key groups)

| File / Group | Feature / Failure Class | Status | Evidence |
| --- | --- | --- | --- |
| test_table_adapter_bridges.py | Bridge / ceiling / F-5 | Current; repaired at 5f6d2ef | 30 passed |
| test_table_adapter_residuals.py | Residual defects / F-1 / F-2 / F-3 | Current; quarterly PDF + column mapping at 5f6d2ef | 45 passed |
| test_table_adapter_defects.py | Defects / F-4 / F-6 | Current | 68 passed with extraction |
| test_table_extraction.py | End-to-end extraction | Current | — |
| test_evaluate.py / test_gate*.py / test_evidence_validation.py / test_director_appointments.py / test_dedup.py | Evaluation gates / evidence / dedup / director data | Current | — |
| test_comparative_highlights.py / test_demo_loader.py | Demo / highlights | Current | — |

## Scripts (scripts/)

| Path | Role |
| --- | --- |
| scripts/evaluate.py | Evaluation runner |
| scripts/load_starter_demo.py | Starter dataset loader |

## Documentation (docs/)

| Path | Role | Status |
| --- | --- | --- |
| docs/REPOSITORY_STATUS.md | This audit reconciliation (new) | Added at audit |
| docs/FILE_INVENTORY.md | This inventory (new) | Added at audit |
| docs/evaluation/HELDOUT_BASELINE_PROTOCOL.md | Held-out protocol | Protected |
| docs/evaluation/HELDOUT_FAILURE_AUDIT.md | Held-out failure evidence | Protected |
| docs/evaluation/TABLE_EXTRACTION_IMPLEMENTATION_PLAN.md | Adapter design | Historical |
| docs/evaluation/TABLE_LAYOUT_ADAPTER_LIMITATIONS.md | Adapter limits | Historical |
| docs/validation/BASELINE.md | Validation baseline | Historical |
| docs/validation/phase0-spike.py | Spike script | Historical |

## Protected evaluation artifacts (do not modify)

- `docs/evaluation/heldout_gold.json`
- `docs/evaluation/results.json`
- `docs/evaluation/extraction_gold.json`
- `docs/evaluation/relationship_gold.json`
- `docs/evaluation/heldout_baseline_predictions.json`
- `docs/evaluation/heldout_baseline_report.md`
- `docs/evaluation/heldout_baseline_runner.py`

Verified hashes preserved; not altered by audit.

## Project / build / config

- `.env.example`, `requirements.txt`, `README.md`, `.gitignore`
- `starter-datasets/` (delhivery/, india-macro-economy/, README.md, starter-datasets.zip — package artifact, not source)

## Excluded / out-of-scope

- Sealed Bank of England February 2026 report: strictly excluded; not referenced, not listed, not stat'd, not parsed.
- Rolls-Royce 2025 annual report: excluded from evaluation runs.
- IMF UK 2026 Article IV reports: excluded from evaluation runs.
- Corpus PDFs that match excluded categories: not enumerated to avoid exposure; excluded at the evaluation-harness level (`find_tables` workload policy and starter-dataset scope).

## Generated / local-only (not committed here)

- `.agents/`, `Fact_Knowledge_Layer/`, `fact-knowledge-layer-final-plan.md` (untracked, preserved)
- `starter-datasets.zip` (package artifact; may be regenerated)
- Worktree directories under `/private/tmp/` (local audit artifacts, described without full paths in documentation)
