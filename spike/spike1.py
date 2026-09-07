"""
Phase 0 validation spike — Superjoin Fact Knowledge Layer
Run this BEFORE scaffolding the repo. No PDF parsing, no DB, no FastAPI —
just: does the extraction + comparison prompt actually work on real text?

Usage:
    pip install anthropic pydantic
    export ANTHROPIC_API_KEY=...
    python spike.py

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
    "description": "Record every meaningful fact found in the source text.",
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

EXTRACTION_SYSTEM = """You extract grounded facts from a page of a financial or economic document.

Rules:
- Extract both numeric facts (revenue, growth rate, ...) AND categorical/semantic
  facts (a director's status, an appointment, a resignation, an address).
- value can be a number OR a short string like "active" / "resigned" / "appointed".
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
    return Dimensions(
        subject=cmp(a.subject, b.subject),
        predicate=cmp(a.predicate, b.predicate),
        value=cmp(a.value, b.value),
        unit=cmp(a.unit, b.unit),
        period=cmp(a.period, b.period),
        scope=cmp(a.scope, b.scope),
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
    print("PASS if: categorical values like 'active'/'resigned'/'ceased' were extracted "
          "at all — if this list is empty or all-numeric, the prompt is implicitly "
          "numbers-only. Fix before building anything else.\n")

    print("=" * 70)
    print("TEST E — Case 2 candidate: RBI vs IMF FY25/26 growth forecast")
    print("=" * 70)
    rbi_forecast = extract_facts(RBI_FORECAST_SNIPPET, "RBI Annual Report 2024-25")
    imf_forecast = extract_facts(IMF_FORECAST_SNIPPET, "IMF Article IV 2025")
    result_e = compare_facts(rbi_forecast[0], imf_forecast[0])
    print(result_e.model_dump_json(indent=2))
    print("This is NOT an assertion of a genuine contradiction — verify the reasoning "
          "manually before using it as Case 2. Check whether the model's `reason` "
          "actually engages with why 6.5% vs 6.6% aren't explained by scope/vintage/"
          "units, or whether it hand-waves a 'different methodology' excuse — if it "
          "does the latter, that's worth noting as a reasoning-quality limitation too.\n")


if __name__ == "__main__":
    run()
