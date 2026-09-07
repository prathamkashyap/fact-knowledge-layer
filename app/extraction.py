"""
Structured fact extraction prompts, tool schemas, and parsing utilities.
Separated from providers so that extraction logic is reusable across providers.
"""

from typing import List, Dict, Any
from app.models import Fact, Evidence, FactComparison, Dimensions


# ---------------------------------------------------------------------------
# Extraction tool schema (for structured output / tool calling)
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
                        "as_of": {"type": ["string", "null"]},
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
- If the text specifies an effective date or as-of date that differs from the
  reporting period, put it in `as_of`.
- evidence_text must be the exact source sentence(s) the fact came from.
- Only extract facts actually present in the given text. Do not pad the list."""


# ---------------------------------------------------------------------------
# Comparison tool schema
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


# ---------------------------------------------------------------------------
# Deterministic dimension diff (hint for the LLM, not the final verdict)
# ---------------------------------------------------------------------------

def _cmp(x, y):
    if x is None or y is None:
        return "unknown"
    return "same" if str(x).strip().lower() == str(y).strip().lower() else "different"


def _cmp_list(x: List[str], y: List[str]) -> str:
    if not x and not y:
        return "same"
    if not x or not y:
        return "unknown"
    xs = {s.strip().lower() for s in x}
    ys = {s.strip().lower() for s in y}
    return "same" if xs == ys else "different"


def _diff_dimensions(a: Fact, b: Fact) -> Dimensions:
    return Dimensions(
        subject=_cmp(a.subject, b.subject),
        predicate=_cmp(a.predicate, b.predicate),
        value=_cmp(a.value, b.value),
        unit=_cmp(a.unit, b.unit),
        period=_cmp(a.period, b.period),
        scope=_cmp(a.scope, b.scope),
        qualifiers=_cmp_list(a.qualifiers, b.qualifiers),
    )


# ---------------------------------------------------------------------------
# Parsing helpers (convert raw tool-call dicts to Pydantic models)
# ---------------------------------------------------------------------------

def _parse_extracted_facts(raw_facts: List[Dict[str, Any]], page_context: str) -> List[Fact]:
    """Parse raw tool-call output into Fact objects with evidence."""
    facts = []
    for f in raw_facts:
        raw_ev = f.get("evidence_text")
        evidence_text = (raw_ev or "").strip()
        if not evidence_text:
            continue  # skip facts with empty evidence
        facts.append(Fact(
            subject=f["subject"],
            predicate=f["predicate"],
            value=f["value"],
            unit=f.get("unit"),
            period=f.get("period"),
            as_of=f.get("as_of"),
            scope=f.get("scope"),
            qualifiers=f.get("qualifiers", []),
            evidence=Evidence(
                document_id="",
                page_number=0,
                text=f["evidence_text"],
                document_name=page_context,
            ),
            confidence=f.get("confidence", 1.0),
        ))
    return facts


def _parse_comparison(raw: Dict[str, Any], fact_a_id: str, fact_b_id: str, dims: Dimensions) -> FactComparison:
    """Parse raw tool-call output into FactComparison."""
    return FactComparison(
        fact_a_id=fact_a_id,
        fact_b_id=fact_b_id,
        relationship=raw["relationship"],
        confidence=raw["confidence"],
        reason=raw["reason"],
        dimensions=dims,
    )
