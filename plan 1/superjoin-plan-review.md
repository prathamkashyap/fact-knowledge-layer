---
title: "Superjoin Fact Knowledge Layer — Review of Your Plan"
subtitle: "Suggestions grounded in the actual starter dataset, not just the assignment PDF"
author: "Reviewed for Pratham Kashyap"
date: "September 7, 2026 · Deadline: September 9, 2026, 11:00 AM"
---

## TL;DR

The plan (PDF parse → LLM extract → normalize → SQLite → candidate matching →
LLM reasoner → FastAPI/HTML) is the right shape and the schema instinct
(`subject/predicate/value/unit/period/scope/qualifiers`) is sound. Two things
need to change before you start coding:

1. **The starter dataset is bigger and messier than the plan assumes** — 6
   PDFs, ~511 pages, ~20 MB, with the *same company reporting three different
   "revenue" numbers for the same year*. That changes both your performance
   budget and your schema.
2. **The sequencing puts the highest-risk step (LLM extraction + reasoning
   quality) in the middle of day two.** If that step underperforms, you find
   out with ~12 hours left instead of ~40. It should be the first thing you
   validate, today, on one page, before any scaffolding.

Everything below is ranked by how much it changes your outcome, not by how
interesting it is to build.

---

## 1. What's already right — keep this

- **Fact schema instinct.** Splitting `value` from `unit`/`period`/`scope`
  instead of one blob is exactly what the assignment is testing for — see
  Section 2, it's not optional, it's load-bearing.
- **Two-stage reasoning** (candidate grouping, then LLM classifies the pair)
  instead of all-pairs comparison. Correct call, keeps cost bounded.
- **Deliberately including a failure case** rather than hiding one. The brief
  asks for this explicitly, and most candidates will skip it.
- **Refusing React/graph-DB/RAG-framework scope creep.** The brief says this
  directly, and the plan already resists it.

---

## 2. What the real dataset changes

I unzipped `starter-datasets.zip` and looked at the actual PDFs instead of
just the assignment description. This matters because your plan's examples
(the ₹5,000M vs ₹6,000M reconciliation, the "8,460 vs 8,460 million" failure)
are invented placeholders — and the real files already contain better,
free versions of all four required cases.

### 2.1 It's not one folder — it's two independent datasets

`starter-datasets/` splits into `delhivery/` (3 corporate documents) and
`india-macroeconomy/` (3 institutional reports). **6 PDFs, 511 pages total,
~20 MB.** Two of the Delhivery PDFs and two of the macro PDFs are ~100 pages
each. That is not a "process everything through the LLM page by page" budget
inside a two-day window — it's a real cost/latency constraint, not a
hypothetical "brownie point" one.

**Confidence: high** — this is a direct file inspection, not an inference.

**Action:** decide *now*, and say so explicitly in the README's
"Limitations" section, that you sample/prioritize pages (e.g. the sections
each dataset's own `README.md` already flags as "sections retained") rather
than silently processing only what happens to work. Sampling is fine;
*undisclosed* sampling that the grader discovers when they upload a new PDF
is what gets read as hard-coding.

### 2.2 A real corroboration case already exists — use it, don't invent one

Searching the three macro PDFs for GDP growth:

- **RBI Annual Report 2024-25:** growth "moderated to 6.5 per cent in
  2024-25."
- **IMF Article IV 2025:** "India's real GDP grew by 6.5 percent in
  FY2024/25."

Same metric, same period, two independent institutions, different phrasing.
That's Case 1, for free, with zero synthetic data needed.

### 2.3 A real "apparent contradiction" case already exists — and it's a better story than a time-period one

- **Economic Survey 2024-25:** "real GDP is estimated to grow by 6.4 per
  cent in FY25" — explicitly sourced to the **First Advance Estimate**
  (an early, partial-data release).
- **RBI Annual Report** and **IMF Article IV:** both say **6.5 per cent**
  for the same year — based on **later, revised estimates**.

6.4% vs 6.5% looks like noise, but the real reconciling variable isn't time
*period* — it's **data vintage / estimate type** (first advance estimate vs.
provisional/revised estimate). That's a subtler and more convincing
demonstration of "grounded, compared, and explained" than an invented
FY2023-vs-FY2024 example, because a grader can independently verify it from
the source documents you were given.

**This directly means your schema needs one more field** — see 2.5.

### 2.4 A real extraction-failure trap already exists — this should be your Case 4

Inside the Delhivery Annual Report FY24 *alone*:

- Revenue from operations, **standalone** basis, FY24: INR 74,540.82 million.
- Revenue from operations, **consolidated** basis, FY24: INR 81,415.38 million.

And in the Q4 FY24 earnings presentation, a third number: **"revenue from
services"**, which explicitly *excludes* revenue from traded goods.

Three different "revenue" figures, same company, same year, same-ish source
family. A naive extractor that only captures `value + unit + period` (no
scope/basis) will either (a) flag standalone-vs-consolidated as a
contradiction, or (b) silently overwrite one with the other. Either outcome
is a genuine, reproducible failure mode you can show honestly — "the
extractor initially conflated these three revenue definitions; here's how we
detect and flag it" — instead of manufacturing a fake unit-loss bug.

**Confidence: high.** This is the single most useful thing I found — it's a
real bug your extractor *will* hit if you don't design for it, not a
contrived demo.

### 2.5 Schema fix this implies

Your `scope` field needs to carry the *basis/definition*, not just a
geography or business-unit label:

```
scope: "standalone" | "consolidated" | "services (ex. traded goods)" | ...
qualifiers: ["first advance estimate", "provisional estimate", ...]
```

Treat `scope` + `qualifiers` as first-class matching keys, not free text you
only read after a mismatch is already flagged. Two facts with identical
subject/predicate/value/period but different `scope` should route to
**RECONCILABLE**, not **CORROBORATES** — your matcher needs to check this
*before* it calls the LLM, or the LLM will do it inconsistently across
pairs.

---

## 3. Priority fixes, ranked by how much they can sink the submission

| # | Risk | Why it matters | Fix |
|---|------|-----------------|-----|
| 1 | Extraction/reasoning quality is unvalidated until day 2 | If the LLM step is weak, you find out with little runway left | Run one real page through your prompt **today, in the first hour**, before writing any scaffolding (3.1) |
| 2 | 511 pages, naive per-page LLM calls | Cost/latency blowout, possible rate limits mid-build | Cap + disclose a sampling strategy; use a cheap/fast model for extraction, reserve the stronger model for the reasoning/classification step |
| 3 | Candidate matching by string similarity on subject/predicate | Misses "GDP growth" vs "real GDP growth" vs "growth"; misses "Delhivery" vs "Delhivery Limited" vs "the Company" | Light embedding similarity (a small local model or one cheap embedding call) for grouping, not a full retrieval stack — this doesn't violate the "no RAG framework" guidance, it's a targeted technique |
| 4 | Forced JSON schema not actually enforced at generation time | Prompt-and-hope extraction silently produces malformed/hallucinated facts under time pressure | Use tool-calling / structured-output mode so the model *can't* return non-conforming JSON, not just a Pydantic check after the fact |
| 5 | Generalization claim untested | Brief says they may test with unseen PDFs; hand-picking your 4 cases from the starter set is fine only if the underlying logic has zero doc-specific rules | Before recording the demo, run the full pipeline on one PDF that is *not* in the starter set and confirm it doesn't break |
| 6 | Deadline timezone assumption | 47-hour math only holds if "11:00 AM" is IST | Confirm once, now — cheap insurance |

---

## 4. Re-sequenced start (today only — your day 2/3 plan is fine as-is)

Your plan goes: scaffold repo → PDF ingestion → fact extraction → normalization
→ (day 2) reasoning → API → UI. That's a reasonable *build* order but a risky
*validation* order — reasoning quality is both the hardest part and the part
the assignment says is actually being judged ("the interesting part is how
facts are discovered, grounded, compared, and explained"), and it's
scheduled last.

**Suggested change — same total time, different order for the first ~2 hours:**

1. Take *one* page from the RBI report and *one* page from the IMF report
   (the ones with the 6.5%/6.4% figures above).
2. Hand-write or quick-script the extraction prompt and the reasoner prompt
   against just those two pages. No repo, no FastAPI, no DB.
3. Confirm the model (a) extracts a clean fact with scope/period preserved
   and (b) correctly classifies the 6.4-vs-6.5 pair as RECONCILABLE with the
   right reason, not CONTRADICTS.
4. **Only then** scaffold the repo and build outward — you now know your
   core assumption holds, and you already have a validated prompt to reuse.

If step 3 fails, you've lost 45 minutes, not a day and a half.

---

## 5. Smaller suggestions worth doing if time allows

- **Unit tests for the normalizer and matcher** (currency conversions,
  period-string parsing, scope-mismatch routing). Cheap, and it gives you
  concrete material for the README's "Engineering Decisions" section.
- **Entity canonicalization** — even a simple alias table built at extraction
  time ("Delhivery Limited" → "Delhivery") so cross-document grouping doesn't
  silently split the same entity into two subjects.
- **Reasoning trace in the output** (already in your plan) — keep it, it's
  the clearest way to show "explained," not just "classified."
- Keep the UI as plain HTML/JS as planned — nothing above changes that call.

---

## 6. Open questions to settle before you start the clock

1. Confirm "11:00 AM" deadline timezone (IST assumed, from a VIT Vellore
   sender).
2. Which LLM/API you're actually using — cost and rate limits differ enough
   across providers that this affects the page-sampling decision in 2.1/3.2.
3. Whether you're comfortable disclosing a sampling strategy in the README
   (recommended) vs. trying to process all 511 pages for real (higher risk,
   not obviously higher reward given the grading criteria).

---

*This review is based on direct inspection of `starter-datasets.zip`
(both dataset READMEs and text extracted from all 6 PDFs) plus the assignment
PDF and the shortlist email. Figures quoted above are paraphrased from the
source filings for identification purposes.*
