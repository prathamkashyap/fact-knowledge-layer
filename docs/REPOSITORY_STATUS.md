# Repository State — Fact Knowledge Layer (audit branch: codex/fkl-repository-state-audit)

**Verified commit:** `5f6d2eff0c8e40ef1e8ddeed92b970425a409a3b`
**Parent:** `e052eecafa03b85446c509a5df4f37b91bb5971e`
**Branch published:** `codex/fkl-repository-state-audit` (from this audit worktree)
**Audit date:** 2026-10-24

## Purpose

This document reconciles the repository's current state against its authoritative plan (`fact-knowledge-layer-final-plan.md`, untracked, not modified), verifies protected evaluation artifacts, records verified test results, and distinguishes historical from current metrics.

## Phase status (from authoritative plan)

- Phase 1 — repository and ingestion: scaffold + PDF ingestion (historical milestone)
- Phase 2 — structured extraction: Fact / Evidence / structured LLM output (future; not started)
- Phase 3 — normalization and matching (future)
- Phase 4 — reasoning (future)
- Phase 5 — API (future)
- Phase 6 — UI (future)
- Phase 1.5 is **not defined** in the plan. The quarterly-header regression work (aligned path, bridge ceiling, ambiguity protections) is a test-quality follow-up to Phase 1's table-adapter fixes, not a specified Phase 1.5 deliverable.

## Verified local state

- Original checkout HEAD: `979563f9af7bb054fd24c856010998a9be989bdd`
- Baseline tag `baseline-week5`: `d9c68bd424883a09107b7507e348107e9f4fbc41`
- Latest verified local commit (quarterly-header regression): `5f6d2eff0c8e40ef1e8ddeed92b970425a409a3b`
- Protected hashes (verified before/after audit):
  - heldout_gold.json: `6511b0ad3b18b18ceb9616a3f93901527128c1cffb5801312836c21a87e6078f`
  - results.json: `80706fd3d99d975a0a875dfb10d6fb2713246cd23d73ac8d20c2f8521c72793e`
  - extraction_gold.json: `fb6fcf69d753157bf088d7520c1bf112a1a82f3192713e35fc1ca461ec137868`
  - relationship_gold.json: `867c0be4734be4a492709467bad88a9450bc9f7f8ecd317a78c2a6b561676600`
- Untracked preserved: `.agents/`, `Fact_Knowledge_Layer/`, `fact-knowledge-layer-final-plan.md`
- No destructive Git operations performed; worktrees preserved (original + 10+ existing)

## Table-adapter / quarterly regression state (current)

- `test_bridge_ceiling_is_pinned_on_both_sides`: repaired; unreachable loop replaced; asserts both inclusive (45.0) and exclusive (45.5) boundaries; fixture geometry independently computed; control case included.
- `test_quarterly_split_word_pdf_fixture_preserved_in_public_adapter`: hermetic `tmp_path` PDF; split-word `Q4 2024` / `Q4 2025` preserved in borderless and public adapter; no `period_header_conflict`; `ambiguous_header_span` preserved (structural, source line 912); aligned path produces 1 candidate with `column_index` mapping assertions (`Q4 2024` → col 1, `Q4 2025` → col 2) verified independently.
- Independent audit verdict: PASS WITH FINDINGS (non-blocking assertion-quality observations; no production source change required).

## Test suite (independent verification, audit worktree)

- Focused quarterly: 1 passed
- `tests/test_table_adapter_bridges.py` + `tests/test_table_adapter_residuals.py`: 76 passed
- Full suite (`tests/`): 444 passed, 7 warnings, 133.65 s (verified at audit-remediation worktree 2026-10-24, exit 0)
- `compileall`: exit 0

## Directory organization (high level, safe to describe)

- `app/`: source modules (`table_extraction.py`, `models.py`, `database.py`, `pdf_parser.py`, `extraction.py`, `normalizer.py`, `matcher.py`, `reasoner.py`, `main.py`, `pipeline.py`, `providers.py`, `__init__.py`)
- `tests/`: 21 test files covering table extraction, bridge/ceiling, quarter/period header, defects, residuals, evaluation gates, comparative highlights, evidence validation, director appointments, dedup, demo loader, gate checks
- `docs/`: `evaluation/` (protected gold, protocols, reports), `validation/` (baseline metrics/relationships), `REPOSITORY_STATUS.md` (this doc), `FILE_INVENTORY.md`
- `scripts/`: `evaluate.py`, `load_starter_demo.py`
- `starter-datasets/`: `delhivery/`, `india-macroeconomy/`; `README.md`; `starter-datasets.zip` (generated / package artifact — excluded from source-change scope)
- `docs/evaluation/`: protected artifacts (`heldout_gold.json`, `results.json`, `extraction_gold.json`, `relationship_gold.json`, `heldout_baseline_predictions.json`, `HELDOUT_BASELINE_PROTOCOL.md`, `heldout_baseline_report.md`, `heldout_baseline_runner.py`, `HELDOUT_FAILURE_AUDIT.md`, `TABLE_EXTRACTION_IMPLEMENTATION_PLAN.md`, `TABLE_LAYOUT_ADAPTER_LIMITATIONS.md`) — these must not be modified by Phase 1/2/any audit work
- `docs/validation/`: `baseline_facts.json`, `baseline_metrics.json`, `baseline_relationships.json`, `BASELINE.md`, `phase0-spike.py`
- `.env.example`, `requirements.txt`, `README.md`
- `.gitignore`

## Reproducible validation commands

```bash
# Focused quarterly PDF regression
pytest tests/test_table_adapter_residuals.py::test_quarterly_split_word_pdf_fixture_preserved_in_public_adapter -q

# Bridge + residuals
pytest tests/test_table_adapter_bridges.py tests/test_table_adapter_residuals.py -q

# Full suite (long)
pytest tests/ -q --tb=no

# Compile
python -m compileall -q app tests scripts
```

## Known limitations / unresolved observations

- Aligned path requires precise numeric-column geometry; synthetic fixtures must align header centres (200/300) with fixed right edges (250/350) to produce positive candidates. The earlier mismatched fixture (header at 170/280, data at 290/390) returned zero candidates due to geometry, not code defect.
- `period_header_outside_candidate_region` is expected when header bbox (y≈30) lies above candidate top (y≈42); association is via `period_headers_associated_by_alignment` / `period_headers_associated_by_page_grid`, not bbox overlap.
- `ambiguous_header_span` (source line 912) describes structural merged-header span (fewer occupied cells than multi-column structure) and must not be suppressed.
- No Phase 1.5 is defined in `fact-knowledge-layer-final-plan.md`; any future work between Phase 1 and Phase 2 requires clarification.
- The Bank of England February 2026 report (referenced in audit instructions) must remain unaccessed; it is not present in the working tree and is excluded from all scans.
- Corpus exclusions: the evaluation dataset does not include the Rolls-Royce 2025 annual report or IMF UK 2026 Article IV reports; they are not processed in this audit.

## Publication status

- Branch `codex/fkl-repository-state-audit` pushed from audit worktree (`5f6d2ef` parent, commit `92abc12` in branch history, documentation added here).
- No `main` replacement; no force-push; no history rewrite.
- This status doc and inventory were produced solely for audit/reconciliation; no application behavior changed.
