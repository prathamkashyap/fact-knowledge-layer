# First Held-Out Deterministic Baseline Report

## Frozen inputs and execution

- Frozen Week 5 baseline: `d9c68bd424883a09107b7507e348107e9f4fbc41` (`baseline-week5`).
- Gold-set commit: `979563f9af7bb054fd24c856010998a9be989bdd`; gold file contains 50 unique fact IDs/keys (27 Rolls-Royce, 23 IMF).
- Protocol commit, made before extraction: `914eb8ca36f6e95ab331170b1d9e78c80cfbeba4`. Protocol SHA-256: `97ecacebae2d4bbbe5936c4b97ab59d128fa857b96dc79bb010f08e96099cb4f`.
- Isolated worktree branch: `codex/heldout-baseline-phase2`; initial extraction was run at protocol commit `914eb8ca36f6e95ab331170b1d9e78c80cfbeba4`. Result artifacts are committed afterward.
- Entry point: `app.pipeline.process_document_pipeline`, called once for each PDF with `provider=None`, `reasoning_mode="heuristic"`, candidate-only extraction and threshold `8.0`. Python environment: `/Users/prathamkashyap/Documents/Coding.nosync/github/fact-knowledge-layer/.venv/bin/python`; command: `PYTHONPATH=. /Users/prathamkashyap/Documents/Coding.nosync/github/fact-knowledge-layer/.venv/bin/python docs/evaluation/heldout_baseline_runner.py` from the isolated worktree root.
- Each input used a separate SQLite database under `/private/tmp/fkl-heldout-baseline-phase2-run-20261014T1128Z`; the in-memory PDF parse cache was cleared before each document. No project evaluator was run.
- Runner source SHA-256: `5be71ff35e7b997fb55eaa08ab3ce8aa63084184f1f5f99bb5f84a3eeb78d415`. Machine-readable predictions SHA-256: `de546ad89c0c4fee50b61d022903fd93e5df04d62a050d4f916b1ba3bec00e77`.
- Two runner-launch attempts failed before any PDF processing (first missing `app` import path, then a threshold-constant import assumption). Both are recorded at `/private/tmp/fkl-heldout-baseline-phase2-run-20261014T1125Z/launcher-diagnostics.json`. Neither called the extraction pipeline. The subsequent successful invocation processed each authorized PDF once; there were no extraction execution errors.

## Input provenance

| Document | File | Bytes | Pages | SHA-256 |
|---|---|---:|---:|---|
| Rolls-Royce | `rolls-royce-2025-annual-report.pdf` | 7,077,708 | 220 | `c9741d73dba1f52ff4fc48380bf602fb9e422c829a1b47a3d4a5462bdca6d739` |
| IMF UK Article IV | `IMF-United-Kingdom,-2026-Article-IV.pdf` | 2,832,305 | 102 | `5097ec0fe9af420635d75f490880d501f3571235a17fe2c79718d0a7bc300657` |

## Results

Recall is sampled-gold recall only: a final retained candidate must satisfy all frozen entity, claim type/basis, exact value/unit, period/status/scope and evidence rules. The annotated sample is not exhaustive.

| Document | Gold facts | Candidate pages | Raw candidates | Final validated/deduplicated facts | Gold matched | Unmatched gold | Sampled-gold recall |
|---|---:|---:|---:|---:|---:|---:|---:|
| Rolls-Royce | 27 | 114/220 | 0 | 0 | 0 | 27 | **0/27 = 0.0000** |
| IMF UK Article IV | 23 | 34/102 | 1 | 1 | 0 | 23 | **0/23 = 0.0000** |
| Overall | 50 | 148/322 | 1 | 1 | 0 | 50 | **0/50 = 0.0000** |

### Annotated facts not recovered

- Rolls-Royce: rr-001, rr-002, rr-003, rr-004, rr-005, rr-006, rr-007, rr-008, rr-009, rr-010, rr-011, rr-012, rr-013, rr-014, rr-015, rr-016, rr-017, rr-018, rr-019, rr-020, rr-021, rr-022, rr-023, rr-024, rr-025, rr-026, rr-027.
- IMF UK Article IV: imf-001, imf-002, imf-003, imf-004, imf-005, imf-006, imf-007, imf-008, imf-009, imf-010, imf-011, imf-012, imf-013, imf-014, imf-015, imf-016, imf-017, imf-018, imf-019, imf-020, imf-021, imf-022, imf-023.

### Unmatched extracted candidates

One final candidate was retained from the IMF document and remains **unadjudicated**, not a false positive: subject `India headline inflation`, predicate `inflation rate`, value `2.8 per cent`, period `April`, page 11. The selected IMF gold facts concern the United Kingdom on page 5; the candidate fails the frozen entity and claim/value/context requirements for the annotated set. It is preserved verbatim in the predictions artifact and raw output.

Rolls-Royce produced no raw candidates. The IMF produced one raw candidate, which remained one final validated/deduplicated fact. There were no repeated candidates, ambiguous gold mappings, or conflicting predictions in the outputs.

## Scoring limits

Formal precision, F1, and false-positive counts are **not yet defensible** because the annotations cover selected tables/metrics rather than all claims in either PDF. The unmatched IMF candidate is therefore left for later adjudication. No precision-like score is inferred from the unmatched count.

## Artifacts and integrity

- Frozen protocol: `docs/evaluation/HELDOUT_BASELINE_PROTOCOL.md` (commit `914eb8ca36f6e95ab331170b1d9e78c80cfbeba4`).
- Machine-readable predictions and scoring record: `docs/evaluation/heldout_baseline_predictions.json` (SHA-256 `de546ad89c0c4fee50b61d022903fd93e5df04d62a050d4f916b1ba3bec00e77`).
- Runner: `docs/evaluation/heldout_baseline_runner.py` (SHA-256 `5be71ff35e7b997fb55eaa08ab3ce8aa63084184f1f5f99bb5f84a3eeb78d415`).
- Raw pre-normalization and final fact JSON, isolated SQLite databases, and run manifests: `/private/tmp/fkl-heldout-baseline-phase2-run-20261014T1128Z`. The manifest records the source, raw, final and database hashes.
- Launcher diagnostics from the two no-input startup failures: `/private/tmp/fkl-heldout-baseline-phase2-run-20261014T1125Z/launcher-diagnostics.json`.

No gold annotation was changed. No extraction rules, production code, tests, prior gold files, or `docs/evaluation/results.json` were changed. The Bank of England sealed PDF was not opened or processed. No held-out tuning or provider/LLM work was started.
