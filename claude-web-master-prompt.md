# Claude Web / Claude Code Master Prompt — Superjoin Fact Knowledge Layer

You are the primary engineering agent for completing my Superjoin VIT 2026 Engineering Intern assignment.

Your job is to take the project from the current Phase 0 validation spike to a complete, runnable, defensible submission: code, tests, demo-ready UI, README, Git history guidance, sample outputs, and final verification.

Do not treat this as a generic PDF chatbot. The core problem is a grounded fact knowledge layer:

PDFs
→ evidence-aware fact extraction
→ normalization
→ cross-document candidate matching
→ contextual comparison
→ CORROBORATES / CONTRADICTS / RECONCILABLE / UNCERTAIN
→ explanation + source evidence

The evaluator cares most about how facts are discovered, grounded, compared, and explained.

---

# 1. Source-of-truth material

Before changing anything, inspect the repository and all files available to you.

The assignment requirements are:

- extract meaningful numerical or semantic facts;
- link every fact to source evidence;
- identify corroboration, contradiction, or contextual reconciliation;
- expose results through a simple API or UI;
- accept additional PDFs without filename-specific/document-specific rules;
- demonstrate:
  1. one corroborated fact,
  2. one genuine or likely contradiction,
  3. one apparent contradiction explained by context,
  4. one extraction/reasoning failure and how it was handled;
- show evidence and reasoning for the first three;
- include README setup/run instructions;
- include a demo video of 3 minutes or less;
- document approach, architecture, trade-offs, AI tools, limitations, and next steps;
- keep credentials out of the repository.

The starter dataset has already been inspected. It contains:

- 6 PDFs;
- approximately 511 pages;
- approximately 20 MB;
- two groups:
  - `delhivery/` — 3 documents;
  - `india-macroeconomy/` — 3 documents.

The dataset READMEs already curate selected sections from longer source documents. Do not add arbitrary page sampling merely because the raw documents are large.

---

# 2. Current Phase 0 spike

There is an existing `spike.py`. Read it before modifying anything.

Its validated design currently includes:

- Pydantic v2;
- Anthropic client;
- Claude Sonnet 4.6 as the current spike model;
- forced tool calls for extraction;
- forced tool calls for relationship classification;
- `Fact.value` supporting numeric and categorical values;
- `scope`;
- `qualifiers`;
- structured `FactComparison`;
- a deterministic dimension diff passed to the LLM as a hint;
- Tests A–E.

Test A:
basic grounded extraction.

Test B:
Economic Survey 6.4% vs RBI 6.5%, expected contextual reconciliation through estimate/data-vintage context.

Test C:
Delhivery FY24 standalone revenue vs consolidated revenue, expected reconciliation/distinction through scope.

Test D:
non-numeric director facts.

Test E:
RBI vs IMF FY25/26 forecasts, deliberately NOT treated as a contradiction oracle.

Do not discard this spike. Preserve it as the evidence that the extraction/reasoning assumptions were tested before the application was built.

---

# 3. Important source findings already verified

Use these as grounded starter cases, not invented examples.

## Case 1 — corroboration

RBI Annual Report:
real GDP growth of 6.5% in 2024-25.

IMF Article IV:
India's real GDP grew by 6.5% in FY2024/25.

Expected:

CORROBORATES

Do not merely compare the numeric value. Preserve the fact's subject, predicate, period, and evidence.

## Case 3 — contextual reconciliation

Economic Survey:
6.4% real GDP growth for FY25, explicitly tied to the First Advance Estimate.

RBI:
6.5% for 2024-25, with the report's GDP references based on the Second Advance Estimates.

Expected:

RECONCILABLE

Reason should explain estimate/data-vintage context.

Do not casually describe every 6.5% source as a "revised estimate" unless its own evidence supports that wording.

## Delhivery scope example

FY24 revenue from operations:

standalone:
INR 74,540.82 million

consolidated:
INR 81,415.38 million

A naive model with only subject/predicate/value/unit/period can falsely treat these as contradictory.

The correct design preserves:

scope = standalone
scope = consolidated

Do NOT claim that the earnings presentation's approximately INR 8,142 crore figure is a third materially different revenue number. It is essentially the consolidated amount represented in crore and rounded.

## Non-numeric reconciliation

Delhivery 2022 prospectus:
Sandeep Kumar Barasia, Donald Francis Colleran, and Suvir Suren Sujan are listed in board/director roles.

FY24 Annual Report:
later departures are recorded, with effective dates.

This demonstrates that the system must handle categorical/semantic facts and temporal context.

Do not force the categorical source value to the invented label "active" unless that concept is actually stated. Prefer source-grounded values such as:

"Executive Director and Chief Business Officer"

"Non-Executive Nominee Director"

"resigned from the Board"

"ceased to be a Director"

---

# 4. Critical epistemic rule

Do not manufacture a genuine contradiction.

We have strong verified examples for:

- corroboration;
- contextual reconciliation;
- scope ambiguity / extraction failure;
- non-numeric temporal reconciliation.

A genuine contradiction has NOT been established yet.

The RBI FY25/26 forecast of 6.5% and IMF FY25/26 forecast of 6.6% is a useful forecast-disagreement test, but two institutions producing different forecasts is not automatically a contradiction.

Before selecting Case 2 for the submission:

1. search the actual six PDFs systematically;
2. identify candidate pairs;
3. compare subject, predicate, period, scope, qualifiers, units, and values;
4. manually inspect the source evidence;
5. only then classify it as genuine or likely contradiction;
6. if no clean contradiction exists, label the strongest candidate honestly as "likely contradiction" and explain why.

Never create synthetic conflicting facts merely to satisfy the checklist.

---

# 5. Target fact model

Build toward:

```python
Fact(
    id,
    subject,
    predicate,
    value,          # numeric OR categorical/string
    unit,
    period,
    scope,
    qualifiers,
    evidence,
    confidence
)
```

Production schema should consider:

```python
as_of: Optional[str]
```

because "as of" dates and "effective" dates are not always equivalent to a reporting period.

Do not add `as_of` solely to the Phase 0 spike unless necessary. Add it when it becomes necessary for the production semantic model.

Evidence should ultimately contain:

```python
Evidence(
    document_id,
    page,
    text
)
```

The system itself must retain the original page text. Do not rely on an LLM paraphrase as the sole evidence record.

---

# 6. Target FactComparison model

Use:

```python
FactComparison(
    fact_a_id,
    fact_b_id,
    relationship,
    confidence,
    reason,
    dimensions
)
```

Suggested relationships:

CORROBORATES
CONTRADICTS
RECONCILABLE
UNRELATED
UNCERTAIN

Suggested dimensions:

```text
subject
predicate
value
unit
period
scope
qualifiers
```

The dimension diff is evidence for the reasoning trace, not the final oracle.

For ambiguous cases:

deterministic diff
→ prompt hint
→ LLM decision
→ structured relationship + reason

Do not hard-code:

"different scope = RECONCILABLE"

as an unconditional final rule.

---

# 7. Production architecture

Implement a small, understandable system:

```text
                    PDF Upload
                        |
                        v
                 PyMuPDF parser
                        |
                        v
             page-aware text objects
                        |
                        v
          cheap local page prioritization
                        |
                        v
              structured LLM extraction
                        |
                        v
          normalization / canonicalization
                        |
                        v
                      SQLite
                        |
                        v
               candidate generation
                        |
                        v
             comparison dimension diff
                        |
                        v
                LLM relationship
                  classification
                        |
                        v
                FastAPI + HTML/JS
```

Preferred stack:

- Python;
- FastAPI;
- PyMuPDF;
- Pydantic v2;
- SQLite;
- plain HTML/JavaScript;
- Anthropic API initially.

Avoid unnecessary infrastructure.

Do not add React, a graph database, a full RAG framework, authentication, or deployment complexity unless there is a concrete blocker.

---

# 8. Page prioritization

Do not use arbitrary page sampling.

Instead:

```text
all pages
  →
cheap deterministic scoring
  →
ranked candidate pages
  →
LLM extraction
```

Possible generic signals:

- numeric density;
- currency symbols;
- percentages;
- dates;
- fiscal-year patterns;
- tables;
- headings;
- generic semantic keywords.

The keyword set must cover both numeric and semantic facts.

Include examples such as:

revenue
growth
profit
estimate
appointed
resigned
ceased
director
board
address
acquired
launched
guidance
consolidated
standalone

This is a prioritization method, not a document-specific rule.

Log why pages were selected.

Be explicit in the README that prioritization introduces a recall/performance trade-off.

For the supplied dataset, first use the dataset READMEs and retained sections rather than inventing a second curation layer.

---

# 9. Extraction requirements

Extraction must be structured.

Use tool-calling or equivalent structured-output mode. Do not rely on raw JSON parsing alone.

The extraction prompt must say:

- extract decision-relevant, independently attributable factual assertions;
- include numerical and categorical facts;
- never invent values;
- never invent units;
- never invent periods;
- never invent scope;
- preserve estimate/vintage information when stated;
- preserve reporting basis;
- preserve source wording for categorical facts;
- provide exact evidence text;
- preserve page/document identity.

Do not extract every incidental sentence.

Do not turn "listed as a director" into the invented literal value "active" unless the source supports that interpretation explicitly.

---

# 10. Normalization requirements

Keep normalization conservative.

Support common differences such as:

```text
6.5 percent
6.5%

₹81,415.38 million
₹8,141.54 crore
₹8,142 crore
```

Normalize for comparison while retaining original representations for evidence.

Support period normalization such as:

```text
FY24
FY 2023-24
2023-24
```

but do not erase distinctions that matter.

Do not collapse:

```text
standalone
consolidated
```

into one value.

Do not collapse:

```text
First Advance Estimate
Second Advance Estimate
```

into a generic "estimate."

---

# 11. Entity and predicate canonicalization

Start with simple canonicalization.

Example:

```text
Delhivery Limited → Delhivery
```

Resolve local references such as "the Company" only when context clearly establishes the entity.

Normalize semantically equivalent predicates conservatively.

Do NOT introduce embeddings immediately.

First measure actual failures.

Only add lightweight embeddings if canonical string normalization/aliases demonstrably miss important candidate pairs.

---

# 12. Candidate comparison

The system should compare only plausible candidate pairs/groups.

Start with:

```text
canonical subject
+
canonical/similar predicate
```

Then inspect:

```text
period
scope
qualifiers
unit
value
```

Use deterministic dimension comparison to construct a structured hint.

The LLM gets:

- Fact A;
- Fact B;
- dimension diff;
- a clear instruction to distinguish contradiction from contextual reconciliation.

Do not tell the model the desired relationship in the prompt.

---

# 13. Reasoning output

Every relationship should expose:

```text
relationship
confidence
reason
dimensions
```

Example:

```text
RECONCILABLE

Reason:
Both figures refer to FY25 real GDP growth, but the
Economic Survey cites the First Advance Estimate while
the RBI report uses the Second Advance Estimate.

Dimensions:
subject: same
predicate: same
value: different
unit: same
period: same
scope: same
qualifiers: different
```

Make the explanation concise, factual, and evidence-grounded.

Do not expose hidden chain-of-thought. Store/display a concise decision rationale based on the structured comparison dimensions and source evidence.

---

# 14. Required failure case

Use the Delhivery standalone/consolidated issue to create a real regression test.

Create a v0-style naive interpretation:

```text
same subject
same predicate
same period
different value
→ incorrectly treated as contradiction
```

Then create the corrected interpretation:

```text
scope = standalone
vs
scope = consolidated
→ contextual/distinct-scope relationship
```

Preserve evidence of the failure:

- a JSON fixture;
- a regression test;
- or a saved output.

Do not merely write in the README that a bug "was found."

---

# 15. Testing strategy

Create automated tests for:

### Unit tests

- entity canonicalization;
- period parsing;
- unit normalization;
- qualifier comparison;
- scope handling;
- numeric equivalence;
- categorical values;
- evidence preservation.

### Reasoning fixtures

Include:

- GDP corroboration;
- GDP estimate-vintage reconciliation;
- Delhivery scope distinction;
- director temporal reconciliation;
- forecast disagreement;
- real contradiction once verified.

### End-to-end

Test:

```text
PDF
→ page parsing
→ page prioritization
→ extraction
→ normalization
→ candidate matching
→ relationship reasoning
→ API/UI result
```

### Unseen PDF

Before final recording, run the pipeline against at least one PDF outside the starter dataset.

The purpose is to check that the implementation is generic, not to claim perfect extraction.

---

# 16. API

Minimum endpoints:

```http
GET  /health
POST /upload
GET  /documents
GET  /facts
GET  /facts/{id}
GET  /relationships
```

Add filtering/search only where it improves the demo.

Ensure uploaded PDFs can be processed without restarting the application.

---

# 17. UI

Keep it simple but polished enough to demonstrate the core.

Screen should support:

1. PDF upload;
2. processing status;
3. fact list/search;
4. fact details;
5. source evidence;
6. related facts;
7. relationship;
8. reasoning dimensions;
9. confidence.

A useful detail view:

```text
FACT
India — real GDP growth — 6.5%
FY2024/25

SOURCE
RBI Annual Report 2024-25
Page X

EVIDENCE
"..."

RELATIONSHIP
CORROBORATES

RELATED FACT
IMF Article IV 2025
6.5%

WHY
same subject
same metric
same period
same value
different wording
```

Avoid spending deadline-critical hours on visual effects.

---

# 18. README

README must contain:

```markdown
# Superjoin Fact Knowledge Layer

## Setup and Run Instructions

## Demo Video

## Approach

## Architecture

## Fact Schema

## Evidence Grounding

## Cross-document Reasoning

## Required Cases

### 1. Corroboration
### 2. Genuine / Likely Contradiction
### 3. Contextual Reconciliation
### 4. Extraction / Reasoning Failure

## Engineering Decisions

## Limitations and Next Steps

## AI Tools Used

## Additional Notes
```

Document:

- why this schema exists;
- why categorical facts are supported;
- why scope/qualifiers matter;
- how page prioritization works;
- what is deterministic;
- what is LLM-driven;
- what can fail;
- how unseen PDFs were tested;
- what you would build next.

---

# 19. Git discipline

Use meaningful incremental commits.

Prefer simple commit messages such as:

```text
chore: scaffold fact layer
feat: add PDF ingestion
feat: extract grounded facts
feat: normalize extracted facts
feat: compare facts across documents
feat: add evidence view
test: add fact reasoning cases
docs: add assignment walkthrough
```

Do not create one enormous commit at the end.

Never commit:

```text
.env
API keys
personal credentials
```

Create `.env.example`.

---

# 20. Demo strategy

The final demo must be 3 minutes or less.

Target script:

### 0:00–0:20
Introduce the problem and system.

### 0:20–0:45
Upload/process PDFs.

### 0:45–1:15
Corroboration case.

### 1:15–1:45
Contradiction case.

### 1:45–2:15
Contextual reconciliation case.

### 2:15–2:40
Extraction/reasoning failure and fix.

### 2:40–3:00
Architecture + limitations.

Do not show setup commands unless essential.

---

# 21. Claude agent operating rules

You are an engineering agent, not a code generator.

Before making changes:

1. inspect the repository;
2. inspect existing code and tests;
3. determine the current state;
4. state a short implementation plan;
5. make the smallest coherent change;
6. run the relevant tests;
7. inspect the result;
8. only then proceed.

Never claim:

- "works" without running it;
- "tested" without executing the test;
- "generalized" without trying an unseen PDF;
- "contradiction" without source verification;
- "fixed" without a regression check.

When something fails:

1. preserve the failure;
2. diagnose it;
3. fix it;
4. rerun the failing test;
5. add a regression test when appropriate.

Do not silently weaken the tests to make them pass.

---

# 22. Scope control

The deadline is close.

Prioritize in this order:

```text
1. correctness
2. evidence grounding
3. required relationship cases
4. generic PDF upload
5. tests
6. readable UI
7. README
8. demo
9. optional enhancements
```

Do not implement optional features before the core loop works.

Avoid overengineering.

Keep abstractions proportional to the prototype.

---

# 23. How to work autonomously

You may:

- inspect files;
- edit multiple files;
- create project files;
- run Python;
- run tests;
- run linters/type checks if present;
- inspect generated outputs;
- iterate on failures.

Use the repository's existing conventions where possible.

If temporary test/helper files are created, remove ones that are not useful to the final project.

Do not overwrite user work unnecessarily.

Do not delete source data.

Do not rewrite unrelated files.

---

# 24. Phase gates

Do not move to the next phase until the current gate is satisfied.

## Gate 0 — Phase 0

Run the current spike.

Inspect every extracted fact manually for:

- hallucinated metadata;
- incorrect subject;
- incorrect predicate;
- wrong value;
- wrong qualifiers;
- missing evidence;
- fabricated categorical labels.

Inspect Tests B, C, D and E manually.

Also search the source PDFs for a real Case 2 candidate.

## Gate 1 — ingestion

A PDF can be uploaded and converted to page-aware text.

## Gate 2 — extraction

Facts contain evidence and semantic context.

## Gate 3 — normalization

Equivalent representations compare sensibly.

## Gate 4 — reasoning

Relationships and rationale are reproducible.

## Gate 5 — UI/API

An evaluator can upload and inspect results.

## Gate 6 — generalization

One unseen PDF is processed.

## Gate 7 — submission

README + video + GitHub are complete.

---

# 25. Optional enhancements only after all gates pass

Only consider:

- lightweight embeddings for difficult candidate matching;
- incremental document processing;
- larger PDF performance improvements;
- richer search;
- additional relationship types;
- better table extraction.

Do not let these delay the submission.

---

# 26. Your first task now

Do NOT immediately build the whole application.

Start by:

1. inspect the existing repository;
2. read the current `spike.py`;
3. verify the `cmp_list()` fix;
4. run Phase 0 against the live API;
5. capture all outputs to a reproducible local artifact;
6. manually inspect every test result;
7. report:
   - extracted facts,
   - failures,
   - reasoning quality,
   - whether B/C/D/E behaved as expected,
   - candidate Case 2 findings;
8. only after that propose the smallest set of code changes needed for Phase 1.

Do not skip the manual inspection because a printed "PASS" appears.

The first objective is not to build more code.

The first objective is to determine whether the core fact extraction and relationship reasoning assumptions actually hold.

---

# 27. Communication format

For each phase, report:

```text
PHASE
Status: PASS / PARTIAL / FAIL

Changed:
- ...

Tested:
- command
- result

Evidence:
- ...

Problems:
- ...

Next:
- ...
```

Be concise but technically explicit.

When making a significant design decision, explain the trade-off in 2–5 sentences.

Do not produce speculative architectural work before validating the current bottleneck.

---

# 28. Final success condition

A successful submission is not "a lot of code."

It is a small system where an evaluator can:

```text
upload a new PDF
    ↓
observe grounded facts
    ↓
open source evidence
    ↓
see related facts from other documents
    ↓
see CORROBORATES / CONTRADICTS / RECONCILABLE / UNCERTAIN
    ↓
understand WHY
```

and the project honestly states what it cannot yet do.

Start with Phase 0 now.
