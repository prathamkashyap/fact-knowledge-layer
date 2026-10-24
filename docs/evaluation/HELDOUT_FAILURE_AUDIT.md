# Forensic Audit: First Held-Out Deterministic Baseline

## Scope and verified references

This audit read the committed protocol, baseline report, predictions/scoring JSON, runner, two run directories, both per-document SQLite databases, the gold JSON, and the applicable source code. It did not run extraction, parse either PDF again, run the evaluator or tests, or access the sealed Bank of England PDF.

- `baseline-week5^{}` resolves to `d9c68bd424883a09107b7507e348107e9f4fbc41`.
- The inspected result worktree was clean at `82d49332e44dff1b529188700103bcad2df60ee2`; its parent is protocol commit `914eb8ca36f6e95ab331170b1d9e78c80cfbeba4`.
- The requested audit branch/worktree was created at `/private/tmp/fkl-heldout-failure-audit` on `codex/heldout-failure-audit`, starting from the result commit. The original checkout was not switched or edited.
- The two PDFs’ current byte sizes and SHA-256 values match the successful-run manifest and the authorized provenance values. No other PDF was inspected.

The successful-run artifacts are under `/private/tmp/fkl-heldout-baseline-phase2-run-20261014T1128Z`; the two pre-extraction launcher errors are at `/private/tmp/fkl-heldout-baseline-phase2-run-20261014T1125Z/launcher-diagnostics.json`.

## Reconstructed execution path

`parse_pdf_document` opened the recorded absolute path, used the PDF text layer, created one `PageObject` per physical page, and set `page_number = page_idx + 1`. It counted a page as a candidate when its score met the `8.0` threshold (`app/pdf_parser.py:74-80, 87-168, 176-240, 252-309`). The score combines numeric density, percentages, currency tokens, date tokens, table/heading heuristics, and keyword hits. It is a prioritization score, not a claim detector.

The stored page rows show all 220 Rolls-Royce pages and all 102 IMF pages have nonempty extracted text. There are 114 and 34 candidate pages, respectively. Every gold evidence quotation appears in the stored text at its recorded `pdf_page_index`, and each of those evidence pages is flagged as a candidate: Rolls-Royce 27/27 on PDF pages 4–5; IMF 23/23 on page 5. Thus neither empty text nor candidate-page exclusion explains the annotated misses.

Here `pdf_page_index` corresponds to the parser/database’s 1-based physical page number, despite the field name. For example, Rolls-Royce page 4 carries printed page 2, page 5 carries printed page 3, and the IMF table is physical page 5 with printed page 4. The gold quotations are found on those exact stored physical pages; there is no observed page-offset error.

The runner captured raw `Fact` objects returned by `extract_facts_from_pages` before normalization, then the pipeline normalized, deduplicated, checked evidence against the same page text, and persisted accepted facts (`docs/evaluation/heldout_baseline_runner.py:104-163`; `app/pipeline.py:444-471, 493-505, 527-597`). The counts establish the filter path:

| Document | Pages with text | Candidate pages | Raw facts | After normalization/deduplication | Evidence rejected | Persisted final facts |
|---|---:|---:|---:|---:|---:|---:|
| Rolls-Royce | 220/220 | 114 | 0 | 0 | 0 | 0 |
| IMF UK | 102/102 | 34 | 1 | 1 | 0 | 1 |

An empty raw list remains empty through normalization and deduplication. For the IMF, the only raw fact survived both stages and evidence validation. Later filters did not remove the annotated facts: the Rolls-Royce facts never became candidates, and the IMF candidate was retained.

## Findings

### Confirmed root causes

1. **The deterministic rules do not cover the annotated Rolls-Royce financial and segment measures.** The relevant parsed pages are selected and contain the exact gold values and headings, but the raw extractor output is `[]`. The heuristic function’s rules are narrow prose patterns: India GDP and macro indicators, INR/₹ revenue and losses, and director events (`app/pipeline.py:57-441`). The monetary revenue patterns require `INR` or `₹` (`app/pipeline.py:150-249, 268-323`); a source search found no `GBP`, `£`, segment, operating-profit, free-cash-flow, or earnings-per-share extraction rule in `app/`. The saved page text instead contains figures such as `£20,059m`, `£21,207m`, `£3,270m`, `£3,462m`, `17.3%`, and segment rows such as Civil Aerospace underlying revenue and operating profit (SQLite page rows 4–5). This explains the zero raw facts and all 27 Rolls-Royce misses before normalization or scoring.

2. **The IMF prose rule assigns an India subject regardless of the source document.** The only raw fact is `India headline inflation`, `2.8 per cent`, period `April`, page 11. Its evidence text is present in that IMF database page: “headline inflation decreasing to 2.8 percent in April.” In `app/pipeline.py:108-148`, the macro indicator map hard-codes `India headline inflation`, and its generic grammar accepts “headline inflation … to 2.8 percent in April.” The candidate’s document ID, filename, page, and evidence match the sole stored IMF document; the database has exactly one IMF document row and one fact row. This is a source-context labeling defect in the heuristic, not evidence of a candidate copied from another PDF or a prior database.

3. **The IMF annotated table is outside the implemented extraction grammar.** All 23 IMF gold facts are on stored PDF page 5, which has extracted text and is a candidate page. Its text contains Table 1 row labels including Real GDP, Domestic demand, CPI period average/end-period, unemployment, Bank Rate, balances, and nominal GDP. The pipeline’s macro rules target selected prose forms and assign India; there is no table-row/column extraction path in `extract_heuristic_facts_from_page`. The one generated fact instead comes from prose on page 11. The result is 0/23 for the table annotations even though the table text was present and its page passed selection.

Together, the first cause explains the 27 Rolls-Royce misses; the table-coverage cause explains the 23 IMF misses; the hard-coded country label explains why the sole generated IMF fact is not a match. The first held-out score is therefore an extractor coverage/context failure, not a later-stage filter failure.

### Contributing factors and compatibility risks

- **Table structure is only weakly represented.** `parse_pdf_page` stores `get_text("text")` and block counts; table detection is a heuristic used in page scoring, not a table-cell representation (`app/pdf_parser.py:187-228`). The extraction function collapses nonempty lines into one string (`app/pipeline.py:62-68`). In the saved data, IMF page 5 and Rolls-Royce page 5 have `has_tables=0` despite their table-like content; both were still candidates due to their other score components. This increases the difficulty of recovering row/column, measure, and period associations, but it did not exclude those pages.
- **Currency normalization is unsafe for GBP amounts.** The protocol correctly warns against using the production normalizer’s generic “million” conversion for GBP. `normalize_unit_and_value` maps any unit containing `million` to `INR million` (`app/normalizer.py:284-332`). No Rolls-Royce fact reached normalization in this run, so this does not explain the observed zero; it is a likely compatibility defect if GBP candidates are later emitted and matching relies on normalized units.
- **Reporting basis is not a first-class production field.** Gold distinguishes underlying/statutory and other bases. `Fact` has predicate, scope, qualifiers, and evidence but no `reporting_basis` field (`app/models.py:20-45`). This can make a future strict comparison less reliable unless extraction encodes the basis in another preserved field. It did not cause the current zero because no Rolls-Royce fact was emitted.

These factors should be treated as design risks, not as evidence that the current scorer discarded valid extracted facts.

### Unresolved hypotheses

- The SQLite page table preserves extracted text and block counts, not saved block geometry or a table-cell map. The artifacts therefore cannot establish how much row/column association was lost when PyMuPDF linearized the original financial tables, or whether a table-aware layout representation alone would recover every fact.
- `has_tables=0` is observed for the IMF table page, but the run did not save drawing counts or a reason code for the table detector. The artifacts cannot distinguish which detector branch failed to label it as a table. This does not explain candidate exclusion: page 5 scored 10.4 and was selected.
- No per-page/per-rule match trace was saved. The aggregate raw output and source rules establish that no annotated fact was emitted, but do not identify a particular failed regex for each individual gold ID. No such trace was reconstructed by rerunning the extractor.

### Ruled out

- **Wrong input or input substitution:** the runner hard-codes the two authorized paths and verifies bytes/SHA before each pipeline call (`heldout_baseline_runner.py:31-44, 91-102`). The manifest paths and hashes match the current authorized files: Rolls-Royce `c9741d73dba1f52ff4fc48380bf602fb9e422c829a1b47a3d4a5462bdca6d739` (7,077,708 bytes); IMF `5097ec0fe9af420635d75f490880d501f3571235a17fe2c79718d0a7bc300657` (2,832,305 bytes).
- **Cross-document or stale database contamination:** the runner refuses an existing run directory, creates it before execution, uses one database path per document, and clears the in-memory parse cache before each call (`heldout_baseline_runner.py:63-70, 113-119`). Each SQLite file has one document row whose stored filepath is the corresponding authorized PDF; the databases contain 220/102 page rows and 0/1 fact rows, with zero comparisons. The IMF fact’s evidence document ID matches that database’s document ID. There is no persisted record from another document in either output database.
- **PDF text extraction failure on the annotated pages:** every page row has nonempty text; all 50 gold quotations occur on their expected stored page; all 50 such pages are marked candidate. This rules out missing text or page-selection gating for the annotated evidence locations. It does not prove that text linearization preserved every table relationship.
- **A later normalization, deduplication, or evidence filter removed the gold candidates:** raw counts are already zero for Rolls-Royce and one for IMF; `evidence_rejected_count` is zero for both; final counts equal raw counts. The sole IMF candidate’s evidence is retained.
- **Page-number offset:** gold page indices match the database’s 1-based physical page numbers and the stored quotation locations. Printed report page numbers are separate and do not correspond to physical PDF page numbering.
- **A valid prediction was rejected only by the held-out matcher:** there are no Rolls-Royce predictions. The one IMF prediction has subject/canonical subject India, while all gold facts are UK facts; it also comes from different prose/page and is not one of the annotated table claims. The strict matcher did not turn a valid table prediction into zero recall.
- **The two failed launcher attempts processed data:** the diagnostics state `extraction_started: false` and `documents_processed: []`; the successful run artifacts show one pipeline call per authorized document. Those setup errors did not contribute to the recorded extraction outputs.

## Evaluation compatibility

The 0/50 sampled-gold recall is consistent with the preserved outputs: 0/27 Rolls-Royce gold facts have any raw or final prediction; 0/23 IMF table facts have a prediction; the single IMF prose fact cannot match the UK table gold. The gold evidence pages are present and selected, so evaluation page requirements did not create the observed miss. Formal precision, F1, and false-positive counts remain not defensible because the gold is explicitly non-exhaustive. The IMF prediction remains unadjudicated for broader document correctness even though it is not a match for this gold set.

## Remediation candidates (not implemented)

| Rank | Candidate | Confidence and scope |
|---:|---|---|
| 1 | Add a bounded, table-aware extraction design for the identified Rolls-Royce highlights/segment layout and IMF Table 1. Preserve row, column/year, entity, units, segment, and underlying/statutory basis in evidence. | High confidence; directly targets the demonstrated coverage gap, but requires a separate implementation and validation task. |
| 2 | Make macro subject attribution depend on explicit source context or a controlled document/entity mapping; do not default these generic indicators to India. | High confidence; fixes the directly observed IMF labeling defect and guards against cross-document subject leakage. |
| 3 | Correct currency-preserving normalization for GBP million/billion before using normalized values in comparisons. | High confidence as a separate correctness issue; not a cause of this run’s zero because no GBP fact was emitted. |
| 4 | Consider a structured reporting-basis field or an explicitly validated encoding convention for underlying/statutory and table subtypes. | Medium confidence; improves schema alignment with the gold dimensions but does not address the absent candidates by itself. |
| 5 | Review table-page diagnostics and candidate-score coverage, but do not tune the threshold based on this result alone. | Medium confidence; all annotated evidence pages already passed the current threshold, so lowering it is not supported as a remedy for these misses. |

## Integrity and next action

The initial raw/final JSON files and SQLite files were read without modification; their current hashes match the values recorded in `run-manifest.json`:

| Artifact | SHA-256 |
|---|---|
| Rolls-Royce raw JSON and final JSON (`[]`) | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| Rolls-Royce SQLite | `b67125902b4918778f919f01536bd2f9aa303058028b237a7200cb2af94a80e1` |
| IMF raw JSON | `1cdfeaec45f00e05b4c25774b66c12fc7babd6a8d1c87e49f8df1d7c7186af9a` |
| IMF final JSON | `adb9053c688e2a64a91e61f774256aefe1753a37d3578537f77c881d221ede67` |
| IMF SQLite | `3ab307fd12c833de0dd843f64bdd19ea2528d05b39987cf85c53d579c2806ebd` |

The run manifests and launcher diagnostics were also left untouched. The original checkout and its two pre-existing untracked paths were left untouched. No existing branch or tag was changed, and nothing was pushed.

The smallest safe next step is to review and approve a separate, narrowly scoped implementation plan for table extraction and source-aware entity attribution. Keep this first-run baseline and its raw artifacts immutable; do not tune the extractor against these 50 annotations as part of this audit.
