# Superjoin Fact Knowledge Layer — Final Plan

## Final verdict

The original architecture is sound:

```text
PDF parse
  → fact extraction
  → normalization
  → fact storage
  → candidate matching
  → relationship reasoning
  → FastAPI / HTML UI
```

The starter material and assignment, however, change several implementation priorities. The final plan below incorporates the useful findings from the two reviews **and corrects claims that were too strong after checking the underlying source pages**.

The assignment prioritizes a small, understandable prototype over production-level polish. It requires meaningful numerical/semantic facts, source evidence for every fact, cross-document relationships, and explicit demonstrations of corroboration, contradiction, contextual reconciliation, and one extraction/reasoning failure. It may also be tested with additional PDFs, so the implementation must remain document-agnostic.

---

## 0. What we actually know from the source material

### 0.1 Dataset scale

The starter dataset contains:

- 6 PDFs
- approximately 511 pages in total
- two logical groups:
  - `delhivery/` — 3 corporate documents
  - `india-macroeconomy/` — 3 institutional reports

The supplied dataset READMEs already curate the source material by retaining selected sections from the original long documents. Therefore, we should not add another arbitrary page-sampling policy just because the raw documents are large.

### 0.2 Corroboration case — confirmed

The macroeconomy documents provide a clean corroboration example:

- **RBI Annual Report 2024-25:** real GDP growth of 6.5% in 2024-25.
- **IMF Article IV 2025:** India's real GDP grew by 6.5% in FY2024/25.

These refer to the same underlying metric and period while using different wording.

Expected relationship:

```text
CORROBORATES
```

### 0.3 Apparent contradiction — confirmed and especially useful

The dataset also contains:

- **Economic Survey 2024-25:** FY25 real GDP growth estimated at 6.4%, explicitly tied to the **First Advance Estimate**.
- **RBI Annual Report:** 6.5% for 2024-25, with the relevant reporting based on the **Second Advance Estimate**.
- **IMF Article IV:** 6.5% for FY2024/25.

The important reconciliation dimension is **estimate/data vintage**, not simply a different reporting period.

We should therefore represent information such as:

```text
qualifiers:
  - first advance estimate
```

or:

```text
qualifiers:
  - second advance estimate
```

when the source explicitly supports it.

Avoid claiming that every 6.5% source is necessarily a "revised estimate" unless that source itself establishes that wording.

Expected relationship:

```text
RECONCILABLE
```

### 0.4 Delhivery revenue — useful schema case, but not "three conflicting numbers"

The Delhivery Annual Report contains FY24 revenue from operations on different reporting bases:

```text
Standalone       ₹74,540.82 million
Consolidated     ₹81,415.38 million
```

The earnings presentation also uses "revenue from services" at approximately ₹8,142 crore and notes the treatment of traded goods. The presentation figure is essentially the consolidated FY24 amount expressed in crore and rounded, rather than a third materially distinct revenue number.

Therefore, the correct lesson is **scope/basis ambiguity**, not "three different conflicting revenue numbers."

A naive extractor that stores only:

```text
subject
predicate
value
unit
period
```

can incorrectly compare:

```text
Delhivery revenue = ₹74,540.82M
Delhivery revenue = ₹81,415.38M
```

as a contradiction.

The fix is to preserve:

```text
scope = standalone
scope = consolidated
```

and relevant qualifiers/definitions.

### 0.5 Non-numeric reconciliation case — confirmed

The dataset contains a particularly strong semantic example:

- The 2022 Delhivery prospectus lists:
  - Sandeep Kumar Barasia
  - Donald Francis Colleran
  - Suvir Suren Sujan
  in active board/director roles.
- The FY24 Annual Report records later departures:
  - Suvir Suren Sujan — resigned effective August 24, 2023
  - Donald Francis Colleran — ceased effective September 27, 2023
  - Sandeep Kumar Barasia — ceased effective July 1, 2024

This is exactly the kind of contextual reconciliation the assignment illustrates: the apparent conflict disappears when the **time/as-of context** is preserved.

This finding has a direct architectural implication:

> The fact model must not be numbers-only.

Categorical values such as `"active"` and `"resigned"` are first-class facts.

### 0.6 Genuine contradiction — do not assume one yet

The source material clearly gives strong examples for:

- corroboration,
- contextual reconciliation,
- extraction/schema failure.

It does **not** automatically follow that a genuine contradiction has already been established.

Before recording the demo, systematically search the six starter PDFs for a pair satisfying approximately:

```text
same subject
same predicate
same or compatible period
same or compatible scope
compatible units
materially different values / incompatible assertions
```

Do not label a scope difference, estimate-vintage difference, rounding difference, or different time as a genuine contradiction.

---

# 1. What to keep from the original plan

## 1.1 Small, understandable architecture

Keep the original stack:

- Python
- PyMuPDF for PDF extraction
- structured LLM output
- SQLite
- FastAPI
- plain HTML/JavaScript

Do not add a graph database, React application, authentication layer, or large RAG framework unless an actual implementation problem requires it.

The assignment explicitly values clarity of approach over production polish.

## 1.2 Two-stage comparison

Keep the two-stage reasoning design:

```text
all facts
  ↓
candidate grouping
  ↓
small candidate pairs/groups
  ↓
relationship reasoning
```

Do not perform naive all-pairs LLM comparison.

---

# 2. Final architecture

```text
                    ┌─────────────────────┐
                    │      PDF Upload     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │      PyMuPDF        │
                    │ page-aware parsing  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Page/section        │
                    │ candidate scoring   │
                    │ (local, cheap)       │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Structured LLM      │
                    │ fact extraction     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Normalization &     │
                    │ entity canonical.   │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │       SQLite        │
                    │ facts/evidence      │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Candidate matching  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ LLM comparison /    │
                    │ relationship reason │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ FastAPI + HTML/JS   │
                    └─────────────────────┘
```

---

# 3. Fact model

The fact model is the most important design decision.

## 3.1 Base Fact

```python
Fact(
    id,
    subject,
    predicate,
    value,
    unit,
    period,
    scope,
    qualifiers,
    evidence,
    confidence
)
```

Recommended semantics:

| Field | Purpose |
|---|---|
| `subject` | Entity/topic the fact is about |
| `predicate` | What is being asserted |
| `value` | Numeric **or categorical/string** result |
| `unit` | `%`, INR million, people, etc.; nullable |
| `period` | FY2024, 2024-25, Q4 FY24, etc.; nullable |
| `scope` | Standalone, consolidated, segment, geography, services, etc. |
| `qualifiers` | First Advance Estimate, provisional, excluding traded goods, etc. |
| `evidence` | Exact source text plus document/page metadata |
| `confidence` | Extraction confidence |

### Important type decision

`value` cannot be restricted to `float`.

Examples:

```text
value = 6.5
value = 81415.38
value = "active"
value = "resigned"
value = "appointed"
```

The system is a semantic fact layer, not merely a numeric extraction tool.

---

# 4. Evidence model

Every extracted fact must be traceable.

Minimum evidence:

```python
Evidence(
    document_id,
    page,
    text
)
```

The UI should always make it possible to answer:

> "Where did this fact come from?"

Do not allow a fact to exist without source evidence.

When possible, preserve the original wording rather than only a normalized paraphrase.

---

# 5. FactComparison

The comparison itself should be stored as structured data.

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

Example:

```json
{
  "relationship": "RECONCILABLE",
  "confidence": 0.93,
  "reason": "The values refer to the same fiscal year but different estimate vintages.",
  "dimensions": {
    "subject": "same",
    "predicate": "same",
    "value": "different",
    "unit": "same",
    "period": "same",
    "scope": "same",
    "qualifiers": "different"
  }
}
```

Suggested relationships:

```text
CORROBORATES
CONTRADICTS
RECONCILABLE
UNRELATED
UNCERTAIN
```

The `dimensions` object is useful because the evaluator can see **why** the system reached its result rather than receiving only a label.

---

# 6. Page prioritization — not arbitrary sampling

The raw dataset is too large to justify an LLM call on every page during development.

However, arbitrary rules such as:

```text
process every 5th page
first 50 pages only
```

are not defensible for unseen documents.

Instead:

```text
all pages
   ↓
cheap deterministic scoring
   ↓
candidate pages
   ↓
LLM extraction
```

Possible generic signals:

```text
numeric density
currency symbols
percentages
dates
FY / quarter patterns
tables
headings
generic semantic keywords
```

The semantic keywords must not be purely financial.

Include terms such as:

```text
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
```

This allows pages containing important non-numeric facts to surface.

### Important limitation

Prioritization is still a recall/performance trade-off. A fact on a low-scoring page can be missed.

Therefore:

- log the page score,
- log selected pages,
- document the trade-off in the README,
- do not claim full-document recall.

For the supplied starter set, first inspect the README curation before designing any additional restriction.

---

# 7. Extraction prompt requirements

The extraction prompt should explicitly require:

1. meaningful numerical **and semantic** facts;
2. exact evidence;
3. page number;
4. period/as-of context when present;
5. scope/basis when present;
6. qualifiers and estimate type when present;
7. no invented values or metadata.

For example:

```text
Never infer a number that is not stated or directly derivable.

If a categorical state is asserted, extract it:
"active", "resigned", "appointed", etc.

If a scope/basis appears, preserve it:
"standalone", "consolidated", etc.

If a temporal/as-of qualifier appears, preserve it.

Every fact must include source evidence and page.
```

Use structured-output/tool-calling mode where available.

Do not rely on:

```python
json.loads(llm_text)
```

alone.

Pydantic validation is useful as a second line of defense, but generation itself should be constrained.

---

# 8. Normalization

The normalizer should handle common representation differences without destroying original evidence.

Examples:

```text
6.5 percent → 6.5 %
₹8,142 crore → approximately ₹81,420 million
FY24 → canonical fiscal-period representation
Delhivery Limited → Delhivery
```

Keep both:

```text
original representation
normalized representation
```

Do not overwrite the source wording.

### Scope and qualifiers are not normalization noise

These are part of the meaning.

Examples:

```text
standalone
consolidated
first advance estimate
second advance estimate
excluding traded goods
```

Preserve them.

---

# 9. Candidate matching

Start simple.

### Step 1 — canonicalize entities

Examples:

```text
Delhivery Limited → Delhivery
the Company → resolve from local document context
```

### Step 2 — canonicalize predicates

Examples:

```text
GDP growth
real GDP growth
real economic growth
```

Do not assume these are identical automatically; use canonicalization conservatively.

### Step 3 — candidate grouping

Group facts where:

```text
subject is equal/alias-equivalent
predicate is equal/similar
```

Then compare metadata:

```text
period
scope
qualifiers
unit
value
```

Do not introduce embeddings initially.

If real matching failures remain after canonical normalization, then add a lightweight embedding similarity mechanism. Do not build it upfront.

---

# 10. Relationship reasoning

The classifier should receive both facts and the comparison dimensions.

Example prompt context:

```text
Fact A:
Delhivery revenue
₹74,540.82 million
FY24
scope: standalone

Fact B:
Delhivery revenue
₹81,415.38 million
FY24
scope: consolidated

Detected dimensions:
subject: same
predicate: same
period: same
unit: same
scope: different
value: different
```

Then ask the model to decide:

```text
CORROBORATES
CONTRADICTS
RECONCILABLE
UNRELATED
UNCERTAIN
```

with:

```text
confidence
reason
```

### Deterministic checks are hints, not final verdicts

A deterministic diff can tell the model:

```text
"These facts differ in scope: standalone vs consolidated."
```

or:

```text
"These facts differ in estimate type."
```

But do not let a simplistic rule silently force:

```text
different scope → RECONCILABLE
```

because that can itself be caused by an extraction error.

For ambiguous comparisons, the LLM should make the final classification.

---

# 11. Required four demonstration cases

## Case 1 — Corroboration

Use:

```text
RBI: 6.5% real GDP growth in 2024-25
IMF: 6.5% real GDP growth in FY2024/25
```

Show:

```text
CORROBORATES
```

with both pieces of evidence.

## Case 2 — Genuine or likely contradiction

This remains a dataset-analysis task.

Systematically search the starter documents for a defensible pair.

Do not manufacture the example.

Do not misclassify:

- different periods,
- different scopes,
- different estimate vintages,
- unit conversions,
- rounding differences,

as genuine contradictions.

If the search produces only "likely contradiction" candidates, label the demo honestly as **likely contradiction** and explain the uncertainty.

## Case 3 — Contextual reconciliation

Use the GDP estimate-vintage example:

```text
Economic Survey
6.4%
First Advance Estimate

vs.

RBI
6.5%
Second Advance Estimate
```

Show:

```text
RECONCILABLE
```

and explicitly state that the difference is explained by the reporting/estimate vintage.

Also consider the director-status example as an alternative or second reconciliation demonstration:

```text
Prospectus 2022:
director = active

Annual Report FY24:
director = resigned/ceased

→ RECONCILABLE by time/as_of
```

The director case is particularly valuable because it proves the system handles semantic facts, not only numbers.

## Case 4 — Extraction/reasoning failure

Do not invent a fake failure such as a lost unit.

Use the Delhivery scope problem as a regression case.

First capture a deliberately naive v0 result:

```text
subject = Delhivery
predicate = revenue
value = 74540.82
period = FY24

subject = Delhivery
predicate = revenue
value = 81415.38
period = FY24
```

Then demonstrate the incorrect comparison:

```text
CONTRADICTS
```

Add scope:

```text
standalone
consolidated
```

and show the corrected result:

```text
RECONCILABLE / DISTINCT SCOPES
```

Keep evidence of the v0 failure in the repository:

```text
sample_outputs/
regression_tests/
or
screenshots
```

Do not claim a bug that cannot be reproduced.

---

# 12. The first 90-minute validation spike

Before building FastAPI, SQLite, or the UI, validate the core intelligence.

### Test A — basic extraction

Input:

- one RBI or IMF page.

Check:

```text
subject
predicate
value
unit
period
evidence
page
```

No invented metadata.

### Test B — GDP reconciliation

Input:

- Economic Survey 6.4% page
- RBI/IMF 6.5% page

Expected:

```text
RECONCILABLE
```

Reason must mention estimate/data-vintage context.

### Test C — scope reconciliation

Input:

- Delhivery FY24 standalone revenue
- Delhivery FY24 consolidated revenue

Expected:

```text
RECONCILABLE
```

with the differing `scope` dimension.

### Test D — non-numeric extraction

Input:

- Delhivery 2022 director list
- FY24 director-change disclosure.

Expected:

```text
value = "active"
value = "resigned"/"ceased"
period/as_of preserved
```

If Test D fails, the extraction prompt is implicitly numbers-only. Discover that before building the application around the wrong schema.

---

# 13. Implementation order after the spike

Once Tests A-D work:

## Phase 1 — repository and ingestion

```text
app/
├── main.py
├── models.py
├── pdf_parser.py
├── extractor.py
├── normalizer.py
├── matcher.py
├── reasoner.py
└── database.py
```

Commit:

```text
chore: scaffold fact layer
```

Then:

```text
feat: add PDF ingestion
```

## Phase 2 — structured extraction

Implement:

```text
Fact
Evidence
structured LLM output
```

Commit:

```text
feat: extract grounded facts
```

## Phase 3 — normalization and matching

Implement:

```text
entity aliases
predicate normalization
unit normalization
period normalization
candidate grouping
```

Commit:

```text
feat: normalize and match facts
```

## Phase 4 — reasoning

Implement:

```text
FactComparison
relationship classification
reasoning trace
```

Commit:

```text
feat: compare facts across documents
```

## Phase 5 — API

Minimum endpoints:

```text
POST /upload
GET  /documents
GET  /facts
GET  /facts/{id}
GET  /relationships
GET  /health
```

## Phase 6 — UI

Plain HTML/JS:

```text
Upload PDFs
Process
View facts
View source evidence
View relationships
View reasoning dimensions
```

Do not spend deadline-critical time on visual polish.

---

# 14. Unseen-PDF validation

Before recording the demo, process at least one PDF that is not part of the starter set.

The goal is not perfect extraction.

The goal is to prove that:

```text
new PDF
→ parsing
→ page prioritization
→ fact extraction
→ evidence
→ comparison
```

works without filename-specific or document-specific rules.

Check the repository for accidental logic such as:

```python
if filename == ...
if "Delhivery" in ...
if "GDP" in ...
```

None should be required for the core pipeline.

---

# 15. Minimal UI

Target:

```text
┌──────────────────────────────────────────────┐
│ Fact Knowledge Layer                         │
├──────────────────────────────────────────────┤
│ Upload PDFs      [ Choose Files ] [ Process ]│
├──────────────────────────────────────────────┤
│ Documents: 6     Facts: 143     Links: 52    │
├──────────────────────────────────────────────┤
│ Search facts                                  │
│ [ India GDP growth                    ]       │
├──────────────────────────────────────────────┤
│ FACT                                          │
│ India — real GDP growth — 6.5%               │
│ FY2024/25                                    │
│                                               │
│ Evidence                                      │
│ RBI Annual Report — p. XX                    │
│ "..."                                         │
│                                               │
│ RELATIONSHIP                                  │
│ CORROBORATES                                  │
│ IMF Article IV — p. XX                       │
│                                               │
│ REASONING                                     │
│ subject: same                                 │
│ predicate: similar                            │
│ value: same                                   │
│ period: same                                  │
│ scope: same                                   │
└──────────────────────────────────────────────┘
```

---

# 16. README requirements

Mirror the assignment structure:

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

### Corroboration
### Genuine/Likely Contradiction
### Contextual Reconciliation
### Extraction/Reasoning Failure

## Engineering Decisions

## Limitations and Next Steps

## AI Tools Used

## Additional Notes
```

The README should explicitly document:

- the page-prioritization trade-off,
- why scope and qualifiers exist,
- why categorical facts are supported,
- how evidence is preserved,
- how candidate pairs are generated,
- why an LLM is used for semantic comparison,
- what remains uncertain,
- how the unseen-PDF test was performed.

Keep credentials out of Git.

---

# 17. Priority table

| Priority | Risk | Response |
|---|---|---|
| 1 | Core extraction/reasoning may fail | Validate it immediately with the four micro-tests |
| 2 | 511 pages make naive LLM-per-page processing expensive | Local page scoring before LLM extraction |
| 3 | Numeric-only schema misses semantic facts | `value` supports numeric and categorical values |
| 4 | Missing scope/qualifier creates false contradictions | Make them first-class Fact fields |
| 5 | Exact string matching splits equivalent entities/predicates | Canonical normalization + aliases |
| 6 | LLM outputs malformed/hallucinated JSON | Structured-output/tool-calling |
| 7 | Dataset examples may tempt hard-coded logic | Use generic extraction/reasoning rules and test an unseen PDF |
| 8 | Required contradiction may be assumed rather than demonstrated | Search the source material systematically and label uncertainty honestly |
| 9 | "Bug we fixed" may be unverifiable | Preserve the v0 failure as a regression artifact |
| 10 | UI work may consume too much time | Plain HTML/JS only |

---

# 18. What not to build

Do not spend the remaining time on:

- React/Next.js unless the basic UI cannot meet the need.
- graph databases,
- elaborate graph visualization,
- full RAG orchestration,
- authentication,
- multi-user infrastructure,
- sophisticated deployment,
- embeddings before a real matching failure,
- large-scale observability.

The evaluator is more likely to care about:

```text
grounded fact
+
correct context
+
transparent comparison
+
credible explanation
```

than about the number of technologies in the repository.

---

# 19. Recommended time budget

## First 90 minutes

```text
core extraction/reasoning spike
```

## Next 3–4 hours

```text
PDF ingestion
structured extraction
Fact/Evidence models
```

## Next 3–4 hours

```text
normalization
candidate matching
FactComparison
reasoning
```

## Next 2–3 hours

```text
FastAPI
plain HTML/JS
```

## Next 2–3 hours

```text
four required cases
unseen-PDF test
regression evidence
```

## Next 2 hours

```text
README
cleanup
demo recording
```

Keep a meaningful buffer for failures.

---

# 20. Final definition of done

The project is done when all of the following are true:

```text
✓ User can upload a PDF through API/UI
✓ Pages are parsed with source-page metadata
✓ Candidate pages are selected by a generic prioritization method
✓ Facts include numerical and categorical values
✓ Facts retain evidence
✓ Period/scope/qualifiers are preserved
✓ Entities/predicates are normalized sufficiently for candidate matching
✓ Cross-document relationships are generated
✓ Comparisons contain structured dimensions and an explanation
✓ Corroboration case is demonstrated
✓ Genuine/likely contradiction is demonstrated
✓ Contextual reconciliation is demonstrated
✓ Extraction/reasoning failure is demonstrated with saved evidence
✓ At least one unseen PDF is processed successfully
✓ README contains setup, approach, limitations, AI tools, and demo
✓ Demo video is 3 minutes or less
✓ No credentials are committed
✓ Git history shows meaningful incremental work
```

---

# 21. Bottom line

The project should be treated as a **fact-grounding and contextual reasoning system**, not as a generic PDF chatbot.

The strongest implementation is:

```text
PDF
 ↓
page-aware evidence
 ↓
generic candidate prioritization
 ↓
structured semantic facts
 ↓
normalization
 ↓
context-aware candidate matching
 ↓
LLM comparison
 ↓
CORROBORATES / CONTRADICTS / RECONCILABLE
 ↓
reasoning trace + evidence
```

The three most valuable source-backed demonstrations are:

1. **RBI ↔ IMF:** 6.5% corroboration.
2. **Economic Survey ↔ RBI/IMF:** 6.4% vs 6.5%, reconciled through estimate/data-vintage context.
3. **Delhivery directors:** active in an earlier document, later resigned/ceased, reconciled through time/as-of context.

The Delhivery revenue material should be used primarily to expose the importance of **scope/basis**, not as evidence of three independent conflicting revenue numbers.

Do not claim a genuine contradiction until one has been verified directly in the source material.
