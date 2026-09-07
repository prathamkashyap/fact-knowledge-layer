"""
Semantic relationship reasoning layer for the Superjoin Fact Knowledge Layer.
Takes Gate 3 PreparedComparison objects and determines:
- CORROBORATES
- CONTRADICTS
- RECONCILABLE
- UNRELATED
- UNCERTAIN
with confidence, concise evidence-grounded reason, and structured dimensions.

Adheres strictly to the assignment's epistemic rules:
- Numerical difference is NOT automatically contradiction.
- Numerical match is NOT automatically corroboration.
- Apparent differences explained by context (scope, vintage, time, units) are RECONCILABLE.
- Missing or ambiguous evidence (e.g. table column detachment) is UNCERTAIN.
- Forward-looking forecasts from different institutions are NOT contradictions.
"""

from typing import Dict, Any, Optional, List
from app.models import FactComparison, PreparedComparison, Dimensions
from app.providers import LLMProvider


ALLOWED_RELATIONSHIPS = {
    "CORROBORATES",
    "CONTRADICTS",
    "RECONCILABLE",
    "UNRELATED",
    "UNCERTAIN",
}

SYSTEM_REASONING_PROMPT = """You are an expert financial and macroeconomic epistemic reasoning engine.
Your task is to classify the relationship between two extracted factual assertions.

RELATIONSHIP LABELS:
- CORROBORATES: Both assertions independently support materially the same underlying fact in compatible context.
- CONTRADICTS: Assertions address the same underlying fact in materially compatible context, but make conflicting, incompatible claims about a realized outcome.
- RECONCILABLE: The apparent difference is explained by a meaningful contextual distinction such as:
  * Reporting scope/basis (e.g. standalone vs consolidated)
  * Data vintage or estimate release (e.g. First Advance Estimate vs Second Advance Estimate)
  * Reporting period or temporal/as-of date (e.g. listed in earlier document vs later resignation)
  * Definition differences (e.g. GDP at market prices vs GVA at basic prices)
  * Presentation rounding across units (e.g. ₹8,142 crore vs ₹81,415.38 million)
  * Independent forward-looking projections from different institutions
- UNRELATED: The assertions concern different entities or incompatible metrics that are not meaningfully comparable.
- UNCERTAIN: The assertions may concern the same subject, but available evidence or context is insufficient to decide reliably (e.g. table header detachment or ambiguous period).

CRITICAL OPERATING RULES:
1. A numerical difference is NOT automatically a contradiction.
2. A numerical match is NOT automatically corroboration.
3. Two institutions producing different forward-looking forecasts (e.g. 6.5% vs 6.6%) is NOT a contradiction; it reflects different modeling assumptions.
4. When context explains an apparent difference, classify as RECONCILABLE and state the explaining dimension.
5. If table layout or context leaves the period or scope ambiguous, classify as UNCERTAIN.
6. Do NOT invent context or facts not present in the supplied text.
7. Return a concise, factual reason (1-2 sentences) grounded in the evidence, suitable for UI display. Do NOT expose private chain-of-thought."""


def build_reasoning_prompt(prepared: PreparedComparison) -> str:
    """
    Construct a context-rich prompt for relationship classification.
    Supplies Fact A, Fact B, deterministic dimension hints, and numerical/period notes.
    """
    fa = prepared.fact_a
    fb = prepared.fact_b
    dims = prepared.dimensions

    num_note = ""
    if prepared.numerical_comparison:
        nc = prepared.numerical_comparison
        num_note = f"\n- Numerical note: {nc.status} ({nc.rounding_note or 'no rounding details'})"

    period_note = ""
    if prepared.period_comparison:
        pc = prepared.period_comparison
        period_note = f"\n- Period note: {pc.compatibility_note or 'no period details'}"

    prompt = f"""Compare the following two facts:

--- FACT A ---
- Subject: {fa.subject} (canonical: {fa.canonical_subject or fa.subject})
- Predicate: {fa.predicate} (canonical: {fa.canonical_predicate or fa.predicate})
- Value: {fa.value} (normalized: {fa.normalized_value})
- Unit: {fa.unit} (normalized: {fa.normalized_unit})
- Period: {fa.period} (normalized: {fa.normalized_period}, type: {fa.period_type})
- As of: {fa.as_of}
- Scope: {fa.scope}
- Qualifiers: {fa.qualifiers}
- Source: {fa.evidence.document_name} p.{fa.evidence.page_number}
- Evidence: "{fa.evidence.text}"

--- FACT B ---
- Subject: {fb.subject} (canonical: {fb.canonical_subject or fb.subject})
- Predicate: {fb.predicate} (canonical: {fb.canonical_predicate or fb.predicate})
- Value: {fb.value} (normalized: {fb.normalized_value})
- Unit: {fb.unit} (normalized: {fb.normalized_unit})
- Period: {fb.period} (normalized: {fb.normalized_period}, type: {fb.period_type})
- As of: {fb.as_of}
- Scope: {fb.scope}
- Qualifiers: {fb.qualifiers}
- Source: {fb.evidence.document_name} p.{fb.evidence.page_number}
- Evidence: "{fb.evidence.text}"

--- DETERMINISTIC DIMENSION DIFF (Hints, not verdicts) ---
- Subject: {dims.subject}
- Predicate: {dims.predicate}
- Value: {dims.value}{num_note}
- Unit: {dims.unit}
- Period: {dims.period}{period_note}
- Scope: {dims.scope}
- Qualifiers: {dims.qualifiers}

Classify the relationship between Fact A and Fact B according to the system rules."""

    return prompt


def parse_reasoning_response(
    response: Dict[str, Any],
    prepared: PreparedComparison,
) -> FactComparison:
    """
    Safely parse raw LLM output into a validated FactComparison model.
    Handles malformed responses gracefully with UNCERTAIN fallback.
    """
    if not isinstance(response, dict):
        return FactComparison(
            fact_a_id=prepared.fact_a.id,
            fact_b_id=prepared.fact_b.id,
            relationship="UNCERTAIN",
            confidence=0.0,
            reason="Malformed provider output: response was not a dictionary.",
            dimensions=prepared.dimensions,
        )

    raw_rel = str(response.get("relationship", "")).strip().upper()
    relationship = raw_rel if raw_rel in ALLOWED_RELATIONSHIPS else "UNCERTAIN"

    try:
        conf = float(response.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, conf))
    except (ValueError, TypeError):
        confidence = 0.0

    reason = str(response.get("reason", "")).strip()
    if not reason:
        reason = f"Relationship classified as {relationship} based on dimensional comparison."

    return FactComparison(
        fact_a_id=prepared.fact_a.id,
        fact_b_id=prepared.fact_b.id,
        relationship=relationship,
        confidence=confidence,
        reason=reason,
        dimensions=prepared.dimensions,
    )


def classify_relationship_heuristically(prepared: PreparedComparison) -> FactComparison:
    """
    Deterministic baseline reasoning engine implementing the assignment's epistemic rules.
    Used for offline testing, fixture validation, and fallback when no live LLM is configured.
    """
    fa = prepared.fact_a
    fb = prepared.fact_b
    dims = prepared.dimensions
    nc = prepared.numerical_comparison
    pc = prepared.period_comparison

    # 1. Check for table ambiguity or missing period/context
    if dims.period == "unknown" or (pc and not pc.is_same_period and not pc.is_same_type and (not fa.period or not fb.period)):
        return FactComparison(
            fact_a_id=fa.id,
            fact_b_id=fb.id,
            relationship="UNCERTAIN",
            confidence=0.75,
            reason="Available source context or table layout leaves reporting period ambiguous, rendering relationship uncertain.",
            dimensions=dims,
        )

    # 2. Check for incompatible predicates
    if dims.predicate == "different":
        return FactComparison(
            fact_a_id=fa.id,
            fact_b_id=fb.id,
            relationship="UNRELATED",
            confidence=0.90,
            reason=f"Metrics assert different predicates ('{fa.predicate}' vs '{fb.predicate}') and are not meaningfully comparable.",
            dimensions=dims,
        )

    # 3. Check for forward-looking forecast disagreement
    is_forecast_a = any("forecast" in q.lower() or "project" in q.lower() for q in fa.qualifiers) or "project" in fa.predicate.lower()
    is_forecast_b = any("forecast" in q.lower() or "project" in q.lower() for q in fb.qualifiers) or "project" in fb.predicate.lower()
    if is_forecast_a or is_forecast_b:
        if dims.value == "different":
            return FactComparison(
                fact_a_id=fa.id,
                fact_b_id=fb.id,
                relationship="RECONCILABLE",
                confidence=0.88,
                reason=(
                    f"Both figures are independent forward-looking projections from different institutions "
                    f"({fa.value}{fa.unit or ''} vs {fb.value}{fb.unit or ''}) based on different baseline assumptions."
                ),
                dimensions=dims,
            )

    # 4. Check for categorical temporal reconciliation (e.g. director timeline)
    if not nc or not nc.is_numeric:
        if dims.period == "different" or fa.as_of != fb.as_of:
            return FactComparison(
                fact_a_id=fa.id,
                fact_b_id=fb.id,
                relationship="RECONCILABLE",
                confidence=0.92,
                reason=(
                    f"Apparent difference in board status ('{fa.value}' vs '{fb.value}') is reconciled by temporal context: "
                    f"earlier disclosure dates vs subsequent resignation/cessation dates."
                ),
                dimensions=dims,
            )

    # 5. Check for estimate vintage differences (e.g. First Advance Estimate vs Second Advance Estimate)
    if dims.qualifiers == "different" and any("advance" in q.lower() for q in fa.qualifiers + fb.qualifiers):
        q_a = ", ".join(fa.qualifiers) if fa.qualifiers else "standard"
        q_b = ", ".join(fb.qualifiers) if fb.qualifiers else "standard"
        return FactComparison(
            fact_a_id=fa.id,
            fact_b_id=fb.id,
            relationship="RECONCILABLE",
            confidence=0.95,
            reason=(
                f"Both figures refer to the same period, but represent different estimate vintages: "
                f"{q_a} ({fa.value}{fa.unit or ''}) versus {q_b} ({fb.value}{fb.unit or ''})."
            ),
            dimensions=dims,
        )

    # 6. Check for reporting scope differences (e.g. standalone vs consolidated)
    if dims.scope == "different":
        return FactComparison(
            fact_a_id=fa.id,
            fact_b_id=fb.id,
            relationship="RECONCILABLE",
            confidence=0.95,
            reason=(
                f"Figures report on different accounting scopes: "
                f"standalone ({fa.value if fa.scope == 'standalone' else fb.value}) versus "
                f"consolidated ({fa.value if fa.scope == 'consolidated' else fb.value})."
            ),
            dimensions=dims,
        )

    # 7. Check for numerical closeness due to presentation rounding (crore vs million)
    if nc and nc.is_close_rounding and dims.period == "same":
        return FactComparison(
            fact_a_id=fa.id,
            fact_b_id=fb.id,
            relationship="CORROBORATES",
            confidence=0.92,
            reason=(
                f"Both sources report consistent figures for the same metric and period, with the minor difference "
                f"({nc.relative_diff * 100:.3f}%) explained by rounding across presentation formats."
            ),
            dimensions=dims,
        )

    # 8. Check for exact corroboration
    if dims.value == "same" and dims.period == "same" and dims.scope == "same":
        return FactComparison(
            fact_a_id=fa.id,
            fact_b_id=fb.id,
            relationship="CORROBORATES",
            confidence=0.96,
            reason=f"Both independent sources corroborate that {fa.subject} had {fa.predicate} of {fa.value} {fa.unit or ''} for {fa.period}.",
            dimensions=dims,
        )

    # 9. Genuine contradiction check: same subject, predicate, period, scope, compatible qualifiers, but materially different values
    if (
        dims.subject == "same"
        and dims.predicate == "same"
        and dims.period == "same"
        and dims.scope == "same"
        and dims.value == "different"
        and not is_forecast_a
        and not is_forecast_b
    ):
        return FactComparison(
            fact_a_id=fa.id,
            fact_b_id=fb.id,
            relationship="CONTRADICTS",
            confidence=0.85,
            reason=(
                f"Assertions address the same metric, period ({fa.period}), and scope, "
                f"but make incompatible claims: {fa.value} {fa.unit or ''} versus {fb.value} {fb.unit or ''}."
            ),
            dimensions=dims,
        )

    # Default fallback
    return FactComparison(
        fact_a_id=fa.id,
        fact_b_id=fb.id,
        relationship="UNCERTAIN",
        confidence=0.50,
        reason="Available context is insufficient to determine a conclusive relationship.",
        dimensions=dims,
    )


def reason_comparison(
    prepared: PreparedComparison,
    provider: Optional[LLMProvider] = None,
) -> FactComparison:
    """
    Classify the relationship between facts in a PreparedComparison.
    Delegates to LLMProvider if provided; otherwise falls back to deterministic heuristic classification.
    """
    if provider is not None:
        try:
            return provider.compare_facts(prepared.fact_a, prepared.fact_b)
        except Exception as e:
            return FactComparison(
                fact_a_id=prepared.fact_a.id,
                fact_b_id=prepared.fact_b.id,
                relationship="UNCERTAIN",
                confidence=0.0,
                reason=f"Provider execution failed: {str(e)}",
                dimensions=prepared.dimensions,
            )

    return classify_relationship_heuristically(prepared)


def reason_all_comparisons(
    prepared_list: List[PreparedComparison],
    provider: Optional[LLMProvider] = None,
) -> List[FactComparison]:
    """Classify relationships for a list of prepared candidate pairs."""
    return [reason_comparison(p, provider=provider) for p in prepared_list]
