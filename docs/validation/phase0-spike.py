"""
Phase 0 validation spike — Superjoin Fact Knowledge Layer
Run this BEFORE scaffolding the repo. No PDF parsing, no DB, no FastAPI —
just: does the extraction + comparison prompt actually work on real text?

Usage:
    pip install "anthropic>=0.40,<1" "pydantic>=2,<3"
    export ANTHROPIC_API_KEY=...
    python spike.py

Pinned pydantic>=2,<3 because this script relies on v2-only APIs
(model_dump_json). Freeze exact versions once the environment works.

Swap ANTHROPIC/MODEL below for a different provider if you're not using
Claude — the Fact/FactComparison schema and the four tests don't change.
"""

import json
import os
from typing import Union, List, Optional
from pydantic import BaseModel, Field

import anthropic

MODEL = "claude-sonnet-4-6"
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


# ---------------------------------------------------------------------------
# Schema (from the final plan, section 3/4/5)
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    text: str
    page_hint: str  # free text is fine at spike stage — real page numbers come from PyMuPDF later


# NOTE (schema extension flagged by Test D, not implemented in the spike):
# a board listing's "as-of" date and a resignation's "effective" date are not
# the same concept as `period`, but forcing them into `period` loses that
# distinction. Consider a first-class `as_of: Optional[str]` field in the
# real schema. Kept out of the spike to keep it small — recording it here so
# it isn't lost.
class Fact(BaseModel):
    subject: str
    predicate: str
    value: Union[float, str]          # numeric OR categorical — this is the point of Test D
    unit: Optional[str] = None
    period: Optional[str] = None
    scope: Optional[str] = None
    qualifiers: List[str] = Field(default_factory=list)
    evidence: Evidence
    confidence: float


class Dimensions(BaseModel):
    subject: str
    predicate: str
    value: str
    unit: str
    period: str
    scope: str
    qualifiers: str  # e.g. estimate vintage (SAE vs FAE) — the B-test hinges on this, not scope


class FactComparison(BaseModel):
    relationship: str  # CORROBORATES | CONTRADICTS | RECONCILABLE | UNRELATED | UNCERTAIN
    confidence: float
    reason: str
    dimensions: Dimensions


# ---------------------------------------------------------------------------
# Extraction — forced tool-call, not json.loads(text)
# ---------------------------------------------------------------------------

EXTRACTION_TOOL = {
    "name": "record_facts",
    "description": "Record every decision-relevant, independently attributable factual assertion found in the source text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "predicate": {"type": "string"},
                        "value": {"type": ["number", "string"]},
                        "unit": {"type": ["string", "null"]},
                        "period": {"type": ["string", "null"]},
                        "scope": {"type": ["string", "null"]},
                        "qualifiers": {"type": "array", "items": {"type": "string"}},
                        "evidence_text": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["subject", "predicate", "value", "evidence_text", "confidence"],
                },
            }
        },
        "required": ["facts"],
    },
}

EXTRACTION_SYSTEM = """You extract grounded, decision-relevant facts from a page of a financial or economic document.

Rules:
- Extract every decision-relevant, independently attributable factual assertion —
  not every sentence. Skip incidental or boilerplate facts (e.g. "the report was
  published in 2024", "the meeting was called to order") that carry no standalone
  informational value on their own.
- Extract both numeric facts (revenue, growth rate, ...) AND categorical/semantic
  facts (a director's stated role, an appointment, a resignation, an address).
- value can be a number OR a short string — but for categorical facts, that string
  must be grounded in the source's own wording (e.g. "Executive Director and Chief
  Business Officer", "ceased to be a Director", "resigned from the Board"). Do NOT
  normalize this into an invented status label such as "active" unless that exact
  concept is stated — a person being listed on a board is not the same as the text
  saying they are "active", so don't manufacture that word.
- Never invent a value, unit, period, or scope that is not stated or directly
  derivable from the text. If it's not present, leave it null.
- If the text specifies a basis/definition (standalone, consolidated, excluding
  traded goods, etc.), put it in `scope`.
- If the text specifies an estimate type or vintage (First Advance Estimate,
  Second Advance Estimate, provisional, etc.), put it in `qualifiers`.
- evidence_text must be the exact source sentence(s) the fact came from.
- Only extract facts actually present in the given text. Do not pad the list."""


def extract_facts(text: str, page_hint: str) -> List[Fact]:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=EXTRACTION_SYSTEM,
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "record_facts"},
        messages=[{"role": "user", "content": text}],
    )
    tool_use = next(b for b in resp.content if b.type == "tool_use")
    facts = []
    for f in tool_use.input["facts"]:
        facts.append(Fact(
            subject=f["subject"],
            predicate=f["predicate"],
            value=f["value"],
            unit=f.get("unit"),
            period=f.get("period"),
            scope=f.get("scope"),
            qualifiers=f.get("qualifiers", []),
            evidence=Evidence(text=f["evidence_text"], page_hint=page_hint),
            confidence=f["confidence"],
        ))
    return facts


# ---------------------------------------------------------------------------
# Comparison — deterministic diff feeds the LLM as a hint, doesn't decide alone
# ---------------------------------------------------------------------------

COMPARISON_TOOL = {
    "name": "classify_relationship",
    "description": "Classify the relationship between two facts.",
    "input_schema": {
        "type": "object",
        "properties": {
            "relationship": {
                "type": "string",
                "enum": ["CORROBORATES", "CONTRADICTS", "RECONCILABLE", "UNRELATED", "UNCERTAIN"],
            },
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["relationship", "confidence", "reason"],
    },
}


def diff_dimensions(a: Fact, b: Fact) -> Dimensions:
    def cmp(x, y):
        if x is None or y is None:
            return "unknown"
        return "same" if str(x).strip().lower() == str(y).strip().lower() else "different"

    def cmp_list(x: List[str], y: List[str]) -> str:
        # order-insensitive, case-insensitive set comparison. Both empty means
        # neither fact specified any qualifiers, which is genuinely "same" (no
        # qualifier distinction between them). One empty and one populated is
        # "unknown" — the empty side wasn't extracted/specified, which isn't
        # the same as being known-absent.
        if not x and not y:
            return "same"
        if not x or not y:
            return "unknown"
        xs = {s.strip().lower() for s in x}
        ys = {s.strip().lower() for s in y}
        return "same" if xs == ys else "different"

    return Dimensions(
        subject=cmp(a.subject, b.subject),
        predicate=cmp(a.predicate, b.predicate),
        value=cmp(a.value, b.value),
        unit=cmp(a.unit, b.unit),
        period=cmp(a.period, b.period),
        scope=cmp(a.scope, b.scope),
        qualifiers=cmp_list(a.qualifiers, b.qualifiers),
    )


def compare_facts(a: Fact, b: Fact) -> FactComparison:
    dims = diff_dimensions(a, b)
    prompt = f"""Fact A: {a.model_dump_json()}
Fact B: {b.model_dump_json()}

Deterministic dimension diff (a hint, not a verdict — you decide the final relationship):
{dims.model_dump_json()}

Classify the relationship between Fact A and Fact B."""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=500,
        tools=[COMPARISON_TOOL],
        tool_choice={"type": "tool", "name": "classify_relationship"},
        messages=[{"role": "user", "content": prompt}],
    )
    tool_use = next(b for b in resp.content if b.type == "tool_use")
    out = tool_use.input
    return FactComparison(
        relationship=out["relationship"],
        confidence=out["confidence"],
        reason=out["reason"],
        dimensions=dims,
    )


# ---------------------------------------------------------------------------
# Real snippets pulled from the actual starter PDFs (not synthetic)
# ---------------------------------------------------------------------------

RBI_GDP_SNIPPET = (
    "Real GDP growth moderated to 6.5 per cent in 2024-25, as compared with "
    "6.6 per cent in 2023-24. All references to GDP data in this Report are "
    "based on the Second Advance Estimates (SAE) of National Income 2024-25 "
    "released by the National Statistical Office on February 28, 2025, "
    "unless indicated otherwise."
)

IMF_GDP_SNIPPET_ACTUAL = (
    "Growth has been robust. India's real GDP grew by 6.5 percent in "
    "FY2024/25."
)

ECOSURVEY_GDP_SNIPPET = (
    "As per the first advance estimates of national accounts, India's real "
    "GDP is estimated to grow by 6.4 per cent in FY25."
)

DELHIVERY_REVENUE_SNIPPET = (
    "The revenue from operations on standalone basis for FY24 stood at "
    "INR 74,540.82 million as against INR 66,586.61 million for FY23, "
    "registering a growth of 11.95%. The revenue from operations on "
    "consolidated basis for FY24 stood at INR 81,415.38 million as "
    "against INR 72,253.01 million for FY23, registering a growth of "
    "12.68%."
)

PROSPECTUS_DIRECTORS_SNIPPET = (
    "Board of Directors (as of the 2022 Prospectus): Sandeep Kumar "
    "Barasia — Executive Director and Chief Business Officer. Donald "
    "Francis Colleran — Non-Executive Nominee Director. Suvir Suren "
    "Sujan — Non-Executive Nominee Director."
)

ANNUAL_REPORT_DIRECTOR_CHANGES_SNIPPET = (
    "Suvir Suren Sujan, Non-Executive Director, resigned from the Board "
    "with effect from August 24, 2023. Donald Francis Colleran, "
    "Non-Executive Director, ceased to be a Director with effect from "
    "September 27, 2023. Mr. Sandeep Kumar Barasia ceased to be a "
    "Director with effect from July 01, 2024."
)

RBI_FORECAST_SNIPPET = (
    "Taking into account these factors, real GDP growth for 2025-26 is "
    "projected at 6.5 per cent, with risks evenly balanced."
)

IMF_FORECAST_SNIPPET = (
    "Under staff's baseline scenario, real GDP growth is projected at "
    "6.6 percent in FY2025/26, helped by the strong 2025Q2 growth "
    "outturn, the GST reform, and a 2025Q3 nowcast pointing to continued "
    "momentum, offsetting the adverse impact of U.S. tariffs."
)


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

def run():
    print("=" * 70)
    print("TEST A — basic extraction (RBI actuals page)")
    print("=" * 70)
    facts_a = extract_facts(RBI_GDP_SNIPPET, "RBI Annual Report 2024-25")
    for f in facts_a:
        print(f.model_dump_json(indent=2))
    print("PASS if: subject/predicate/value/period present, no invented fields.\n")

    print("=" * 70)
    print("TEST B — numeric reconciliation (6.4% vs 6.5%, estimate vintage)")
    print("=" * 70)
    eco_facts = extract_facts(ECOSURVEY_GDP_SNIPPET, "Economic Survey 2024-25")
    rbi_facts = extract_facts(RBI_GDP_SNIPPET, "RBI Annual Report 2024-25")
    result_b = compare_facts(eco_facts[0], rbi_facts[0])
    print(result_b.model_dump_json(indent=2))
    print(f"PASS if relationship == RECONCILABLE and reason mentions estimate/vintage. "
          f"Got: {result_b.relationship}\n")

    print("=" * 70)
    print("TEST C — scope reconciliation (standalone vs consolidated revenue)")
    print("=" * 70)
    delhivery_facts = extract_facts(DELHIVERY_REVENUE_SNIPPET, "Delhivery Annual Report FY24")
    standalone = next(f for f in delhivery_facts if f.scope and "standalone" in f.scope.lower())
    consolidated = next(f for f in delhivery_facts if f.scope and "consolidated" in f.scope.lower())
    result_c = compare_facts(standalone, consolidated)
    print(result_c.model_dump_json(indent=2))
    print(f"PASS if relationship == RECONCILABLE and dimensions.scope == 'different'. "
          f"Got: {result_c.relationship}, scope diff: {result_c.dimensions.scope}\n")

    print("=" * 70)
    print("TEST D — non-numeric extraction (director status)")
    print("=" * 70)
    prospectus_facts = extract_facts(PROSPECTUS_DIRECTORS_SNIPPET, "Prospectus 2022")
    changes_facts = extract_facts(ANNUAL_REPORT_DIRECTOR_CHANGES_SNIPPET, "Annual Report FY24")
    for f in prospectus_facts + changes_facts:
        print(f.model_dump_json(indent=2))
    print("PASS if: categorical, source-grounded values were extracted at all — e.g. a "
          "stated role like 'Executive Director and Chief Business Officer', or a stated "
          "change like 'resigned'/'ceased to be a Director'. FAIL if this list is empty or "
          "all-numeric (the prompt is implicitly numbers-only), and also FAIL if any value "
          "is a normalized label like 'active' that isn't actually the source's wording — "
          "that's the model inventing a status rather than extracting one.\n")

    print("=" * 70)
    print("TEST E — forecast disagreement: RBI vs IMF FY25-26 GDP projection")
    print("=" * 70)
    rbi_forecast = extract_facts(RBI_FORECAST_SNIPPET, "RBI Annual Report 2024-25")
    imf_forecast = extract_facts(IMF_FORECAST_SNIPPET, "IMF Article IV 2025")
    result_e = compare_facts(rbi_forecast[0], imf_forecast[0])
    print(result_e.model_dump_json(indent=2))
    print(
        "NOTE: both figures are FORECASTS from different institutions, not two\n"
        "claims about the same already-realized actual — differing point estimates\n"
        "(6.5% vs 6.6%) don't by themselves establish a contradiction the way two\n"
        "conflicting figures for an actual outcome would.\n"
        f"PASS if relationship is UNCERTAIN or RECONCILABLE. Got: {result_e.relationship}. "
        "If the model instead returns CONTRADICTS, don't take that at face value — "
        "check whether `reason` actually engages with why the forecasts differ "
        "(different models/assumptions, e.g. RBI's own risk framing vs IMF citing "
        "GST reform and Q2 outturn) or whether it hand-waves a generic 'different "
        "methodology' excuse; the latter is a reasoning-quality limitation worth "
        "recording, not a passing result.\n"
        "This test is deliberately NOT a stand-in for Case 2 (genuine/likely "
        "contradiction) — that still needs to be found by comparing two claims "
        "about the same realized fact, and verified manually before it goes in "
        "the submission.\n"
    )


if __name__ == "__main__":
    run()
