"""
Gate 4 automated tests for Relationship Reasoning.
Tests covering:
- Prompt generation and structured response parsing
- Robustness: malformed provider output, confidence clamping, UNCERTAIN fallbacks
- All relationship labels: CORROBORATES, CONTRADICTS, RECONCILABLE, UNRELATED, UNCERTAIN
- Real starter-data cases A through G:
  A. RBI 6.5% vs IMF 6.5% (CORROBORATES)
  B. Economic Survey 6.4% vs RBI 6.5% (RECONCILABLE by estimate vintage)
  C. Delhivery standalone vs consolidated revenue (RECONCILABLE by scope)
  D. Delhivery director timeline (RECONCILABLE by temporal as_of context)
  E. Delhivery ₹249 Cr vs ₹2,491.86M (CORROBORATES with rounding explanation)
  F. RBI 6.5% vs IMF 6.6% FY25-26 forecasts (RECONCILABLE/UNCERTAIN, not CONTRADICTS)
  G. Table ambiguity (UNCERTAIN due to detached table header)
- Case 2 investigation & epistemic candidate evaluation
- Critical anti-shortcut regression tests (proving differences are not lazily labeled CONTRADICTS and matches are not lazily labeled CORROBORATES)
- Integration with MockProvider
"""

import pytest
from app.models import Fact, Evidence, Dimensions, PreparedComparison, FactComparison
from app.providers import MockProvider
from app.matcher import generate_candidate_pairs
from app.reasoner import (
    build_reasoning_prompt,
    parse_reasoning_response,
    classify_relationship_heuristically,
    reason_comparison,
    reason_all_comparisons,
)
from tests.test_gate3 import (
    FACT_RBI_ACTUAL_GDP,
    FACT_IMF_ACTUAL_GDP,
    FACT_ECOSURVEY_GDP,
    FACT_DELHIVERY_STANDALONE,
    FACT_DELHIVERY_CONSOLIDATED,
    FACT_DELHIVERY_PRESENTATION_LOSS,
    FACT_DELHIVERY_AR_LOSS,
    FACT_DIRECTOR_PROSPECTUS_SUJAN,
    FACT_DIRECTOR_AR_SUJAN,
    FACT_RBI_FORECAST_GDP,
    FACT_IMF_FORECAST_GDP,
    FACT_TABLE_AMBIGUOUS,
)


# ---------------------------------------------------------------------------
# 1. Prompt Construction and Response Parsing Tests
# ---------------------------------------------------------------------------

def test_build_reasoning_prompt_contains_all_context():
    pairs = generate_candidate_pairs([FACT_RBI_ACTUAL_GDP, FACT_IMF_ACTUAL_GDP])
    pair = pairs[0]
    prompt = build_reasoning_prompt(pair)

    # Must contain both facts' details
    assert "FACT A" in prompt
    assert "FACT B" in prompt
    assert "India" in prompt
    assert "6.5" in prompt
    assert "percent" in prompt.lower()
    # Must contain deterministic dimension diff hints
    assert "DETERMINISTIC DIMENSION DIFF" in prompt
    assert "Subject: same" in prompt
    assert "Predicate: same" in prompt
    # Must contain evidence quotes
    assert pair.fact_a.evidence.text in prompt
    assert pair.fact_b.evidence.text in prompt


def test_parse_reasoning_response_valid():
    pairs = generate_candidate_pairs([FACT_RBI_ACTUAL_GDP, FACT_IMF_ACTUAL_GDP])
    pair = pairs[0]
    raw = {
        "relationship": "CORROBORATES",
        "confidence": 0.95,
        "reason": "Both sources confirm 6.5% GDP growth for FY2024-25.",
    }
    result = parse_reasoning_response(raw, pair)
    assert result.relationship == "CORROBORATES"
    assert result.confidence == 0.95
    assert "Both sources" in result.reason
    assert result.fact_a_id == pair.fact_a.id
    assert result.fact_b_id == pair.fact_b.id
    assert result.dimensions.value == "same"


def test_parse_reasoning_response_malformed_fallback():
    pairs = generate_candidate_pairs([FACT_RBI_ACTUAL_GDP, FACT_IMF_ACTUAL_GDP])
    pair = pairs[0]

    # Non-dictionary input
    res1 = parse_reasoning_response("not a dict", pair)  # type: ignore
    assert res1.relationship == "UNCERTAIN"
    assert res1.confidence == 0.0

    # Invalid relationship enum
    res2 = parse_reasoning_response({"relationship": "INVALID_RELATION", "confidence": 0.8}, pair)
    assert res2.relationship == "UNCERTAIN"

    # Missing fields
    res3 = parse_reasoning_response({}, pair)
    assert res3.relationship == "UNCERTAIN"
    assert res3.confidence == 0.0


def test_parse_reasoning_response_confidence_clamping():
    pairs = generate_candidate_pairs([FACT_RBI_ACTUAL_GDP, FACT_IMF_ACTUAL_GDP])
    pair = pairs[0]

    # Confidence above 1.0 clamped to 1.0
    res_high = parse_reasoning_response({"relationship": "CORROBORATES", "confidence": 1.8}, pair)
    assert res_high.confidence == 1.0

    # Negative confidence clamped to 0.0
    res_neg = parse_reasoning_response({"relationship": "CORROBORATES", "confidence": -0.5}, pair)
    assert res_neg.confidence == 0.0


# ---------------------------------------------------------------------------
# 2. Real Starter-Data Cases (A through G)
# ---------------------------------------------------------------------------

def test_case_a_gdp_corroboration():
    """Case A: RBI 6.5% vs IMF 6.5% -> CORROBORATES."""
    pairs = generate_candidate_pairs([FACT_RBI_ACTUAL_GDP, FACT_IMF_ACTUAL_GDP])
    comp = reason_comparison(pairs[0])

    assert comp.relationship == "CORROBORATES"
    assert comp.confidence >= 0.90
    assert "corroborate" in comp.reason.lower() or "6.5" in comp.reason
    assert comp.dimensions.value == "same"
    assert comp.dimensions.period == "same"


def test_case_b_gdp_vintage_reconciliation():
    """Case B: Economic Survey 6.4% (FAE) vs RBI 6.5% (SAE) -> RECONCILABLE by estimate vintage."""
    pairs = generate_candidate_pairs([FACT_ECOSURVEY_GDP, FACT_RBI_ACTUAL_GDP])
    comp = reason_comparison(pairs[0])

    assert comp.relationship == "RECONCILABLE"
    assert comp.confidence >= 0.90
    assert "vintage" in comp.reason.lower() or "advance estimate" in comp.reason.lower()
    assert comp.dimensions.value == "different"
    assert comp.dimensions.qualifiers == "different"


def test_case_c_delhivery_scope_reconciliation():
    """Case C: Delhivery standalone (74,540.82M) vs consolidated (81,415.38M) -> RECONCILABLE by scope."""
    pairs = generate_candidate_pairs([FACT_DELHIVERY_STANDALONE, FACT_DELHIVERY_CONSOLIDATED])
    comp = reason_comparison(pairs[0])

    assert comp.relationship == "RECONCILABLE"
    assert comp.confidence >= 0.90
    assert "standalone" in comp.reason.lower() and "consolidated" in comp.reason.lower()
    assert comp.dimensions.scope == "different"
    assert comp.dimensions.value == "different"


def test_case_d_delhivery_director_temporal_reconciliation():
    """Case D: Director listed in 2022 Prospectus vs FY24 resignation -> RECONCILABLE by temporal context."""
    pairs = generate_candidate_pairs([FACT_DIRECTOR_PROSPECTUS_SUJAN, FACT_DIRECTOR_AR_SUJAN])
    comp = reason_comparison(pairs[0])

    assert comp.relationship == "RECONCILABLE"
    assert comp.confidence >= 0.90
    assert "temporal" in comp.reason.lower() or "date" in comp.reason.lower() or "resignation" in comp.reason.lower()
    assert comp.dimensions.period == "different"


def test_case_e_delhivery_rounding_corroboration():
    """Case E: ₹249 Cr in earnings presentation vs ₹2,491.86M in Annual Report -> CORROBORATES."""
    pairs = generate_candidate_pairs([FACT_DELHIVERY_AR_LOSS, FACT_DELHIVERY_PRESENTATION_LOSS])
    comp = reason_comparison(pairs[0])

    assert comp.relationship == "CORROBORATES"
    assert comp.confidence >= 0.90
    assert "rounding" in comp.reason.lower()
    assert comp.dimensions.value == "same"  # matched via close rounding


def test_case_f_forecast_disagreement_not_contradiction():
    """Case F: RBI 6.5% vs IMF 6.6% FY25-26 forecasts -> RECONCILABLE, NOT CONTRADICTS."""
    pairs = generate_candidate_pairs([FACT_RBI_FORECAST_GDP, FACT_IMF_FORECAST_GDP])
    comp = reason_comparison(pairs[0])

    # Must NOT be CONTRADICTS
    assert comp.relationship in ("RECONCILABLE", "UNCERTAIN")
    assert comp.relationship != "CONTRADICTS"
    assert "projection" in comp.reason.lower() or "forecast" in comp.reason.lower() or "assumptions" in comp.reason.lower()


def test_case_g_table_layout_ambiguity():
    """Case G: Ambiguous table row with missing period -> UNCERTAIN."""
    pair = PreparedComparison(
        fact_a=FACT_TABLE_AMBIGUOUS,
        fact_b=FACT_RBI_ACTUAL_GDP,
        dimensions=Dimensions(
            subject="same", predicate="same", value="same", unit="same",
            period="unknown", scope="same", qualifiers="same"
        ),
    )
    comp = reason_comparison(pair)

    assert comp.relationship == "UNCERTAIN"
    assert "ambiguous" in comp.reason.lower() or "period" in comp.reason.lower() or "table" in comp.reason.lower()


# ---------------------------------------------------------------------------
# 3. Case 2 Discovery and Evaluation Tests
# ---------------------------------------------------------------------------

def test_case_2_genuine_contradiction_detection():
    """
    Simulate a genuine contradiction (same entity, same metric, same realized period,
    same scope, incompatible values for a realized actual) to ensure CONTRADICTS triggers correctly.
    """
    fact_actual_1 = Fact(
        subject="India real GDP growth",
        predicate="growth rate",
        value=6.5,
        unit="per cent",
        period="2024-25",
        scope="actual",
        qualifiers=["final audited"],
        evidence=Evidence(document_id="doc1", page_number=1, text="India GDP was 6.5%"),
    )
    fact_actual_2 = Fact(
        subject="India real GDP growth",
        predicate="growth rate",
        value=7.8,  # materially conflicting claim for the exact same realized period/scope
        unit="per cent",
        period="2024-25",
        scope="actual",
        qualifiers=["final audited"],
        evidence=Evidence(document_id="doc2", page_number=1, text="India GDP was 7.8%"),
    )
    pairs = generate_candidate_pairs([fact_actual_1, fact_actual_2])
    comp = reason_comparison(pairs[0])

    assert comp.relationship == "CONTRADICTS"
    assert comp.confidence >= 0.80
    assert "incompatible" in comp.reason.lower() or "conflict" in comp.reason.lower() or "6.5" in comp.reason


def test_case_2_cpi_inflation_candidate_investigation():
    """
    Test the CPI Inflation 2024-25 candidate (RBI 4.6% vs IMF 4.4% in Table 1).
    Demonstrates that without knowing whether IMF's figure was an unrevised projection
    or a conflicting actual, epistemic honesty classifies this with appropriate context.
    """
    rbi_cpi = Fact(
        subject="India CPI Inflation",
        predicate="inflation",
        value=4.6,
        unit="per cent",
        period="2024-25",
        scope=None,
        qualifiers=["actual full-year estimate"],
        evidence=Evidence(document_id="rbi_ar", page_number=91, text="CPI Combined 4.6% in 2024-25"),
    )
    imf_cpi = Fact(
        subject="India CPI Inflation",
        predicate="inflation",
        value=4.4,
        unit="percent",
        period="2024/25",
        scope=None,
        qualifiers=["projections / staff estimate"],
        evidence=Evidence(document_id="imf_art_iv", page_number=25, text="CPI inflation 4.4 in 2024/25 under projections"),
    )
    pairs = generate_candidate_pairs([rbi_cpi, imf_cpi])
    comp = reason_comparison(pairs[0])

    # Because IMF's qualifier indicates 'projections / staff estimate', this is reconcilable
    # as an estimate-vintage / projection mismatch rather than a proven hard contradiction.
    assert comp.relationship in ("RECONCILABLE", "UNCERTAIN")
    assert comp.relationship != "CORROBORATES"


# ---------------------------------------------------------------------------
# 4. Anti-Shortcut Regression Tests (Section 10)
# ---------------------------------------------------------------------------

def test_anti_shortcut_different_period_not_automatically_contradiction():
    """
    Delhivery PIN codes: FY21 = 16,677 vs Q4 FY22 = 18,074.
    Different period must NOT be classified as CONTRADICTS.
    """
    f_fy21 = Fact(
        subject="Delhivery", predicate="network reach", value=16677.0, unit="pin codes",
        period="FY21", evidence=Evidence(document_id="p", page_number=1, text="16,677 pin codes in FY21"),
    )
    f_q4fy22 = Fact(
        subject="Delhivery", predicate="network reach", value=18074.0, unit="pin codes",
        period="Q4 FY22", evidence=Evidence(document_id="p", page_number=1, text="18,074 pin codes in Q4 FY22"),
    )
    pairs = generate_candidate_pairs([f_fy21, f_q4fy22])
    comp = reason_comparison(pairs[0])

    assert comp.relationship != "CONTRADICTS"
    assert comp.relationship in ("RECONCILABLE", "UNCERTAIN")


def test_anti_shortcut_different_scope_not_automatically_contradiction():
    """Different scope (standalone vs consolidated) must NOT be classified as CONTRADICTS."""
    pairs = generate_candidate_pairs([FACT_DELHIVERY_STANDALONE, FACT_DELHIVERY_CONSOLIDATED])
    comp = reason_comparison(pairs[0])
    assert comp.relationship != "CONTRADICTS"
    assert comp.relationship == "RECONCILABLE"


def test_anti_shortcut_different_qualifier_not_automatically_contradiction():
    """First Advance Estimate vs Second Advance Estimate must NOT be classified as CONTRADICTS."""
    pairs = generate_candidate_pairs([FACT_ECOSURVEY_GDP, FACT_RBI_ACTUAL_GDP])
    comp = reason_comparison(pairs[0])
    assert comp.relationship != "CONTRADICTS"
    assert comp.relationship == "RECONCILABLE"


def test_anti_shortcut_different_forecast_not_automatically_contradiction():
    """Different institution projections (6.5% vs 6.6%) must NOT be classified as CONTRADICTS."""
    pairs = generate_candidate_pairs([FACT_RBI_FORECAST_GDP, FACT_IMF_FORECAST_GDP])
    comp = reason_comparison(pairs[0])
    assert comp.relationship != "CONTRADICTS"


def test_anti_shortcut_same_number_not_automatically_corroboration():
    """
    Two facts sharing the same numeric value (e.g. 6.5) but describing different metrics
    (GDP growth vs Fiscal Deficit) must NOT be classified as CORROBORATES.
    """
    f_gdp = Fact(
        subject="India", predicate="real GDP growth", value=6.5, unit="per cent",
        period="2024-25", evidence=Evidence(document_id="1", page_number=1, text="GDP growth was 6.5%"),
    )
    f_deficit = Fact(
        subject="India", predicate="gross fiscal deficit", value=6.5, unit="per cent",
        period="2024-25", evidence=Evidence(document_id="1", page_number=1, text="Fiscal deficit was 6.5%"),
    )
    pair = PreparedComparison(
        fact_a=f_gdp,
        fact_b=f_deficit,
        dimensions=Dimensions(
            subject="same", predicate="different", value="same", unit="same",
            period="same", scope="same", qualifiers="same"
        ),
    )
    comp = reason_comparison(pair)

    assert comp.relationship != "CORROBORATES"
    assert comp.relationship == "UNRELATED"


def test_anti_shortcut_close_rounding_different_metric_not_corroboration():
    """
    Close numeric values across incompatible metrics (e.g. revenue ₹8,142 Cr vs expenditure ₹8,140 Cr)
    must NOT be classified as CORROBORATES.
    """
    f_rev = Fact(
        subject="Delhivery", predicate="revenue", value=8142.0, unit="₹ crore",
        period="FY24", evidence=Evidence(document_id="1", page_number=1, text="Revenue was 8142 Cr"),
    )
    f_exp = Fact(
        subject="Delhivery", predicate="total expenses", value=8140.0, unit="₹ crore",
        period="FY24", evidence=Evidence(document_id="1", page_number=1, text="Expenses were 8140 Cr"),
    )
    pair = PreparedComparison(
        fact_a=f_rev,
        fact_b=f_exp,
        dimensions=Dimensions(
            subject="same", predicate="different", value="same", unit="same",
            period="same", scope="same", qualifiers="same"
        ),
    )
    comp = reason_comparison(pair)

    assert comp.relationship != "CORROBORATES"
    assert comp.relationship == "UNRELATED"


# ---------------------------------------------------------------------------
# 5. Integration with MockProvider & Batch Reasoning Tests
# ---------------------------------------------------------------------------

def test_mock_provider_integration_with_comparisons():
    pairs = generate_candidate_pairs([FACT_RBI_ACTUAL_GDP, FACT_IMF_ACTUAL_GDP])
    pair = pairs[0]

    custom_comp = FactComparison(
        fact_a_id=pair.fact_a.id,
        fact_b_id=pair.fact_b.id,
        relationship="CORROBORATES",
        confidence=0.99,
        reason="Verified by MockProvider fixture.",
        dimensions=pair.dimensions,
    )
    provider = MockProvider(comparisons=[custom_comp])

    comp = reason_comparison(pair, provider=provider)
    assert comp.relationship == "CORROBORATES"
    assert comp.confidence == 0.99
    assert comp.reason == "Verified by MockProvider fixture."
    assert provider.compare_call_count == 1


def test_reason_all_comparisons_batch():
    facts = [
        FACT_RBI_ACTUAL_GDP,
        FACT_IMF_ACTUAL_GDP,
        FACT_ECOSURVEY_GDP,
        FACT_DELHIVERY_STANDALONE,
        FACT_DELHIVERY_CONSOLIDATED,
    ]
    pairs = generate_candidate_pairs(facts)
    assert len(pairs) >= 4

    results = reason_all_comparisons(pairs)
    assert len(results) == len(pairs)
    for r in results:
        assert isinstance(r, FactComparison)
        assert r.relationship in ("CORROBORATES", "CONTRADICTS", "RECONCILABLE", "UNRELATED", "UNCERTAIN")
        assert len(r.reason) > 0
