"""
Gate 3 automated tests for Fact Normalization and Candidate Matching.
Tests covering:
- Entity canonicalization (legal suffixes, personal titles, anaphora)
- Predicate canonicalization
- Period normalization (FY spans, single FY, quarters not collapsed, dates, as_of)
- Unit normalization & conversion (percent, crore -> million, etc.)
- Numerical comparability & rounding detection (without float equality)
- Categorical fact preservation
- Scope and qualifier preservation
- Candidate grouping & irrelevant pair filtering
- Generic rules (no document-specific hardcoding)
- Real starter-data cases A through H
- Semantic safeguards (no premature relationship assignment)
- Candidate reduction statistics
"""

import pytest
from app.models import Fact, Evidence, Dimensions, PreparedComparison
from app.normalizer import (
    canonicalize_entity,
    canonicalize_predicate,
    normalize_period,
    normalize_unit_and_value,
    compare_numbers,
    compare_periods,
    normalize_fact,
    normalize_facts,
)
from app.matcher import (
    diff_dimensions_normalized,
    group_candidate_facts,
    generate_candidate_pairs,
    compute_candidate_statistics,
)


# ---------------------------------------------------------------------------
# Fixtures: Real starter-data facts
# ---------------------------------------------------------------------------

FACT_RBI_ACTUAL_GDP = Fact(
    subject="India real GDP growth",
    predicate="growth rate",
    value=6.5,
    unit="per cent",
    period="2024-25",
    scope=None,
    qualifiers=["Second Advance Estimates"],
    evidence=Evidence(
        document_id="rbi_2024_25",
        page_number=24,
        text="Real GDP growth moderated to 6.5 per cent in 2024-25, as compared with 6.6 per cent in 2023-24. All references based on Second Advance Estimates.",
        document_name="02-rbi-annual-report-2024-25-excerpt.pdf",
    ),
    confidence=0.95,
)

FACT_IMF_ACTUAL_GDP = Fact(
    subject="India real GDP",
    predicate="growth rate",
    value=6.5,
    unit="percent",
    period="FY2024/25",
    scope=None,
    qualifiers=[],
    evidence=Evidence(
        document_id="imf_2025",
        page_number=12,
        text="India's real GDP grew by 6.5 percent in FY2024/25.",
        document_name="03-imf-india-2025-article-iv-excerpt.pdf",
    ),
    confidence=0.95,
)

FACT_ECOSURVEY_GDP = Fact(
    subject="India real GDP growth",
    predicate="growth rate",
    value=6.4,
    unit="per cent",
    period="FY25",
    scope=None,
    qualifiers=["First Advance Estimate"],
    evidence=Evidence(
        document_id="ecosurvey_2024_25",
        page_number=28,
        text="As per the first advance estimates of national accounts, India's real GDP is estimated to grow by 6.4 per cent in FY25.",
        document_name="01-india-economic-survey-2024-25-excerpt.pdf",
    ),
    confidence=0.95,
)

FACT_DELHIVERY_STANDALONE = Fact(
    subject="Delhivery Limited revenue from operations",
    predicate="revenue",
    value=74540.82,
    unit="INR million",
    period="FY24",
    scope="standalone",
    qualifiers=[],
    evidence=Evidence(
        document_id="delhivery_ar_fy24",
        page_number=36,
        text="The revenue from operations on standalone basis for FY24 stood at INR 74,540.82 million.",
        document_name="02-delhivery-annual-report-fy24-excerpt.pdf",
    ),
    confidence=0.95,
)

FACT_DELHIVERY_CONSOLIDATED = Fact(
    subject="Delhivery Limited revenue from operations",
    predicate="revenue",
    value=81415.38,
    unit="INR million",
    period="FY 2023-24",
    scope="consolidated",
    qualifiers=[],
    evidence=Evidence(
        document_id="delhivery_ar_fy24",
        page_number=36,
        text="The revenue from operations on consolidated basis for FY24 stood at INR 81,415.38 million.",
        document_name="02-delhivery-annual-report-fy24-excerpt.pdf",
    ),
    confidence=0.95,
)

FACT_DELHIVERY_PRESENTATION_REVENUE = Fact(
    subject="Delhivery revenue from services",
    predicate="revenue",
    value=8142.0,
    unit="₹ crore",
    period="FY24",
    scope="consolidated",
    qualifiers=["excluding traded goods"],
    evidence=Evidence(
        document_id="delhivery_presentation_q4",
        page_number=6,
        text="Revenue from services stood at approximately ₹8,142 crore for FY24.",
        document_name="03-delhivery-q4-fy24-earnings-presentation.pdf",
    ),
    confidence=0.95,
)

FACT_DELHIVERY_AR_LOSS = Fact(
    subject="Delhivery net loss for the year",
    predicate="net profit / loss",
    value=2491.86,
    unit="INR million",
    period="FY24",
    scope="consolidated",
    qualifiers=[],
    evidence=Evidence(
        document_id="delhivery_ar_fy24",
        page_number=36,
        text="Loss for the year was INR 2,491.86 million.",
        document_name="02-delhivery-annual-report-fy24-excerpt.pdf",
    ),
    confidence=0.95,
)

FACT_DELHIVERY_PRESENTATION_LOSS = Fact(
    subject="Delhivery net loss",
    predicate="loss for the year",
    value=249.0,
    unit="₹ crore",
    period="FY24",
    scope="consolidated",
    qualifiers=[],
    evidence=Evidence(
        document_id="delhivery_presentation_q4",
        page_number=7,
        text="Net loss for FY24 was ₹249 Cr.",
        document_name="03-delhivery-q4-fy24-earnings-presentation.pdf",
    ),
    confidence=0.95,
)

FACT_DIRECTOR_PROSPECTUS_SUJAN = Fact(
    subject="Suvir Suren Sujan",
    predicate="role",
    value="Non-Executive Nominee Director",
    unit=None,
    period=None,
    as_of="2022",
    scope=None,
    qualifiers=[],
    evidence=Evidence(
        document_id="delhivery_prospectus_2022",
        page_number=216,
        text="Suvir Suren Sujan — Non-Executive Nominee Director (as of the 2022 Prospectus).",
        document_name="01-delhivery-prospectus-2022-excerpt.pdf",
    ),
    confidence=0.95,
)

FACT_DIRECTOR_AR_SUJAN = Fact(
    subject="Suvir Suren Sujan",
    predicate="board status",
    value="resigned from the Board",
    unit=None,
    period=None,
    as_of="August 24, 2023",
    scope=None,
    qualifiers=[],
    evidence=Evidence(
        document_id="delhivery_ar_fy24",
        page_number=24,
        text="Suvir Suren Sujan, Non-Executive Director, resigned from the Board with effect from August 24, 2023.",
        document_name="02-delhivery-annual-report-fy24-excerpt.pdf",
    ),
    confidence=0.95,
)

FACT_DIRECTOR_AR_COLLERAN = Fact(
    subject="Donald Francis Colleran",
    predicate="board status",
    value="ceased to be a Director",
    unit=None,
    period=None,
    as_of="September 27, 2023",
    scope=None,
    qualifiers=[],
    evidence=Evidence(
        document_id="delhivery_ar_fy24",
        page_number=24,
        text="Donald Francis Colleran, Non-Executive Director, ceased to be a Director with effect from September 27, 2023.",
        document_name="02-delhivery-annual-report-fy24-excerpt.pdf",
    ),
    confidence=0.95,
)

FACT_DIRECTOR_AR_BARASIA = Fact(
    subject="Mr. Sandeep Kumar Barasia",
    predicate="board status",
    value="ceased to be a Director",
    unit=None,
    period=None,
    as_of="July 01, 2024",
    scope=None,
    qualifiers=[],
    evidence=Evidence(
        document_id="delhivery_ar_fy24",
        page_number=24,
        text="Mr. Sandeep Kumar Barasia ceased to be a Director with effect from July 01, 2024.",
        document_name="02-delhivery-annual-report-fy24-excerpt.pdf",
    ),
    confidence=0.95,
)

FACT_RBI_FORECAST_GDP = Fact(
    subject="India real GDP growth",
    predicate="projected growth rate",
    value=6.5,
    unit="per cent",
    period="2025-26",
    scope=None,
    qualifiers=["forecast"],
    evidence=Evidence(
        document_id="rbi_2024_25",
        page_number=35,
        text="Real GDP growth for 2025-26 is projected at 6.5 per cent, with risks evenly balanced.",
        document_name="02-rbi-annual-report-2024-25-excerpt.pdf",
    ),
    confidence=0.95,
)

FACT_IMF_FORECAST_GDP = Fact(
    subject="India real GDP growth",
    predicate="projected growth rate",
    value=6.6,
    unit="percent",
    period="FY2025/26",
    scope=None,
    qualifiers=["forecast", "baseline scenario"],
    evidence=Evidence(
        document_id="imf_2025",
        page_number=18,
        text="Under staff's baseline scenario, real GDP growth is projected at 6.6 percent in FY2025/26.",
        document_name="03-imf-india-2025-article-iv-excerpt.pdf",
    ),
    confidence=0.95,
)

FACT_TABLE_AMBIGUOUS = Fact(
    subject="Real GDP at Market Prices",
    predicate="change",
    value=6.5,
    unit="%",
    period=None,  # missing due to detached plain-text table header
    scope=None,
    qualifiers=[],
    evidence=Evidence(
        document_id="rbi_2024_25",
        page_number=91,
        text="Real GDP at Market Prices (% change)* 6.5",
        document_name="02-rbi-annual-report-2024-25-excerpt.pdf",
    ),
    confidence=0.8,
)


# ---------------------------------------------------------------------------
# 1. Entity Canonicalization Tests
# ---------------------------------------------------------------------------

def test_entity_canonicalization_legal_suffixes():
    assert canonicalize_entity("Delhivery Limited")[0] == "Delhivery"
    assert canonicalize_entity("Delhivery Ltd.")[0] == "Delhivery"
    assert canonicalize_entity("Acme Private Limited")[0] == "Acme"
    assert canonicalize_entity("Global Corp.")[0] == "Global"
    assert canonicalize_entity("Tech Incorporated")[0] == "Tech"
    assert canonicalize_entity("Logistics LLC")[0] == "Logistics"


def test_entity_canonicalization_personal_titles():
    assert canonicalize_entity("Mr. Sandeep Kumar Barasia")[0] == "Sandeep Kumar Barasia"
    assert canonicalize_entity("Dr. Manmohan Singh")[0] == "Manmohan Singh"
    assert canonicalize_entity("Shri Narendra Modi")[0] == "Narendra Modi"


def test_entity_canonicalization_sovereign_and_institutional():
    assert canonicalize_entity("India's real GDP growth")[0] == "India"
    assert canonicalize_entity("India real GDP growth")[0] == "India"
    assert canonicalize_entity("Indian economy")[0] == "India"
    assert canonicalize_entity("Reserve Bank of India")[0] == "Reserve Bank of India"
    assert canonicalize_entity("IMF")[0] == "IMF"
    assert canonicalize_entity("International Monetary Fund")[0] == "IMF"


def test_entity_canonicalization_anaphora_resolution():
    # "the Company" resolved via document context
    assert canonicalize_entity("the Company", context="Delhivery Annual Report FY24")[0] == "Delhivery"
    # "the Company" unresolved without context preserves original string
    assert canonicalize_entity("the Company", context=None)[0] == "the Company"


# ---------------------------------------------------------------------------
# 2. Predicate Canonicalization Tests
# ---------------------------------------------------------------------------

def test_predicate_canonicalization_revenue():
    assert canonicalize_predicate("revenue from operations") == "revenue"
    assert canonicalize_predicate("revenue from services") == "revenue"
    assert canonicalize_predicate("total revenue") == "revenue"
    assert canonicalize_predicate("revenue") == "revenue"


def test_predicate_canonicalization_gdp_growth():
    assert canonicalize_predicate("real GDP growth") == "gdp_growth"
    assert canonicalize_predicate("GDP growth") == "gdp_growth"
    assert canonicalize_predicate("growth rate", subject_remainder="real GDP growth") == "gdp_growth"


def test_predicate_canonicalization_board():
    assert canonicalize_predicate("board status") == "board_role"
    assert canonicalize_predicate("role") == "board_role"
    assert canonicalize_predicate("directorship") == "board_role"
    assert canonicalize_predicate("resignation") == "board_role"


def test_predicate_canonicalization_profit_loss():
    assert canonicalize_predicate("profit after tax") == "net_profit_loss"
    assert canonicalize_predicate("loss for the year") == "net_profit_loss"
    assert canonicalize_predicate("net loss") == "net_profit_loss"


# ---------------------------------------------------------------------------
# 3. Period Normalization Tests
# ---------------------------------------------------------------------------

def test_fy_period_normalization():
    p1, t1 = normalize_period("FY24")
    p2, t2 = normalize_period("FY 2023-24")
    p3, t3 = normalize_period("2023-24")
    p4, t4 = normalize_period("FY2023/24")

    assert p1 == "FY2023-24"
    assert p2 == "FY2023-24"
    assert p3 == "FY2023-24"
    assert p4 == "FY2023-24"
    assert t1 == "fiscal_year"


def test_fy25_period_variants():
    p1, _ = normalize_period("FY25")
    p2, _ = normalize_period("2024-25")
    p3, _ = normalize_period("FY2024/25")
    p4, _ = normalize_period("FY 2024-25")

    assert p1 == "FY2024-25"
    assert p2 == "FY2024-25"
    assert p3 == "FY2024-25"
    assert p4 == "FY2024-25"


def test_quarter_not_collapsed_to_fiscal_year():
    pq, tq = normalize_period("Q4 FY24")
    pfy, tfy = normalize_period("FY24")

    assert pq == "FY2023-24-Q4"
    assert tq == "quarter"
    assert pfy == "FY2023-24"
    assert tfy == "fiscal_year"
    # Must NOT be equal
    assert pq != pfy


def test_calendar_date_and_as_of_normalization():
    p1, t1 = normalize_period(None, as_of="March 31, 2024")
    p2, t2 = normalize_period(None, as_of="August 24, 2023")
    p3, t3 = normalize_period(None, as_of="September 27, 2023")
    p4, t4 = normalize_period(None, as_of="July 01, 2024")

    assert p1 == "2024-03-31"
    assert p2 == "2023-08-24"
    assert p3 == "2023-09-27"
    assert p4 == "2024-07-01"
    assert t1 == "as_of"


# ---------------------------------------------------------------------------
# 4. Unit Normalization & Numerical Comparability Tests
# ---------------------------------------------------------------------------

def test_percentage_normalization():
    v1, u1 = normalize_unit_and_value(6.5, "%")
    v2, u2 = normalize_unit_and_value(6.5, "per cent")
    v3, u3 = normalize_unit_and_value(6.5, "percent")

    assert v1 == v2 == v3 == 6.5
    assert u1 == u2 == u3 == "percent"


def test_crore_to_million_conversion():
    # ₹8,142 crore -> 81,420 INR million
    v, u = normalize_unit_and_value(8142.0, "₹ crore")
    assert v == 81420.0
    assert u == "INR million"

    # ₹249 crore -> 2,490 INR million
    v2, u2 = normalize_unit_and_value(249.0, "Cr")
    assert v2 == 2490.0
    assert u2 == "INR million"


def test_million_normalization():
    v, u = normalize_unit_and_value(74540.82, "INR million")
    assert v == 74540.82
    assert u == "INR million"


def test_rounding_difference_detection():
    # ₹81,415.38 million vs ₹8,142 crore (81,420 million)
    comp = compare_numbers(81415.38, "INR million", 81420.0, "INR million")
    assert comp.is_numeric is True
    assert comp.is_close_rounding is True
    assert comp.is_exact is False
    assert comp.status == "close_rounding"
    assert comp.relative_diff < 0.001  # less than 0.1% diff


def test_delhivery_loss_rounding_detection():
    # ₹2,491.86M vs ₹249 Cr (2,490M)
    comp = compare_numbers(2491.86, "INR million", 2490.0, "INR million")
    assert comp.is_close_rounding is True
    assert comp.status == "close_rounding"
    assert comp.relative_diff < 0.001


def test_exact_numerical_equality():
    comp = compare_numbers(6.5, "percent", 6.5, "percent")
    assert comp.is_exact is True
    assert comp.status == "exact"
    assert comp.absolute_diff == 0.0


def test_material_numerical_difference():
    # 6.4% vs 6.5% -> ~1.54% difference, exceeds rounding tolerance
    comp = compare_numbers(6.4, "percent", 6.5, "percent")
    assert comp.is_exact is False
    assert comp.is_close_rounding is False
    assert comp.status == "different"
    assert comp.absolute_diff == 0.1


# ---------------------------------------------------------------------------
# 5. Categorical Fact Preservation Tests
# ---------------------------------------------------------------------------

def test_categorical_fact_values_not_numeric_normalized():
    v, u = normalize_unit_and_value("resigned from the Board", None)
    assert v == "resigned from the Board"
    assert u is None

    v2, u2 = normalize_unit_and_value("Executive Director and Chief Business Officer", None)
    assert v2 == "Executive Director and Chief Business Officer"
    assert u2 is None


def test_categorical_comparison_status():
    comp = compare_numbers("resigned from the Board", None, "ceased to be a Director", None)
    assert comp.is_numeric is False
    assert comp.status == "categorical"


# ---------------------------------------------------------------------------
# 6. Scope and Qualifier Preservation Tests
# ---------------------------------------------------------------------------

def test_scope_and_qualifiers_preserved_in_normalized_fact():
    norm = normalize_fact(FACT_DELHIVERY_STANDALONE)
    assert norm.scope == "standalone"
    assert norm.qualifiers == []
    assert norm.canonical_subject == "Delhivery"
    assert norm.canonical_predicate == "revenue"

    norm_eco = normalize_fact(FACT_ECOSURVEY_GDP)
    assert norm_eco.qualifiers == ["First Advance Estimate"]
    assert norm_eco.canonical_subject == "India"
    assert norm_eco.canonical_predicate == "gdp_growth"


# ---------------------------------------------------------------------------
# 7. Candidate Grouping & Pair Generation Tests
# ---------------------------------------------------------------------------

def test_candidate_grouping_by_canonical_keys():
    facts = [FACT_RBI_ACTUAL_GDP, FACT_IMF_ACTUAL_GDP, FACT_ECOSURVEY_GDP, FACT_DELHIVERY_STANDALONE]
    norm_facts = normalize_facts(facts)
    groups = group_candidate_facts(norm_facts)

    assert ("india", "gdp_growth") in groups
    assert len(groups[("india", "gdp_growth")]) == 3
    assert ("delhivery", "revenue") in groups
    assert len(groups[("delhivery", "revenue")]) == 1


def test_irrelevant_pair_filtering():
    # A standalone fact with no matches in its group does not produce pairs
    facts = [FACT_DELHIVERY_STANDALONE]
    pairs = generate_candidate_pairs(facts)
    assert len(pairs) == 0


def test_incompatible_unit_filtering():
    # A percentage growth fact and an INR million fact should not pair even if force-grouped
    f_growth = Fact(
        subject="Delhivery", predicate="growth", value=12.0, unit="%",
        period="FY24", evidence=Evidence(document_id="1", page_number=1, text="growth 12%"),
    )
    f_rev = Fact(
        subject="Delhivery", predicate="growth", value=81415.38, unit="INR million",
        period="FY24", evidence=Evidence(document_id="1", page_number=1, text="revenue 81415M"),
    )
    pairs = generate_candidate_pairs([f_growth, f_rev], filter_irrelevant=True)
    assert len(pairs) == 0


def test_generic_rules_without_document_specific_logic():
    """Verify that completely unseen company and metrics normalize cleanly."""
    unseen_a = Fact(
        subject="Zomato Limited revenue from operations",
        predicate="total turnover",
        value=12114.0,
        unit="INR million",
        period="FY24",
        evidence=Evidence(document_id="zomato", page_number=5, text="turnover was 12114M"),
    )
    unseen_b = Fact(
        subject="Zomato Ltd.",
        predicate="revenue from operations",
        value=1211.4,
        unit="₹ crore",
        period="2023-24",
        evidence=Evidence(document_id="zomato_pres", page_number=2, text="revenue was 1211.4 Cr"),
    )
    pairs = generate_candidate_pairs([unseen_a, unseen_b])
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.fact_a.canonical_subject == "Zomato"
    assert pair.fact_b.canonical_subject == "Zomato"
    assert pair.fact_a.canonical_predicate == "revenue"
    assert pair.fact_b.canonical_predicate == "revenue"
    assert pair.dimensions.subject == "same"
    assert pair.dimensions.predicate == "same"
    assert pair.dimensions.value == "same"  # 12114.0M == 1211.4Cr (12114M)
    assert pair.dimensions.period == "same"


# ---------------------------------------------------------------------------
# 8. Real Starter-Data Cases (A through H)
# ---------------------------------------------------------------------------

def test_case_a_and_case_b_gdp_corroboration_and_reconciliation_preparation():
    """Case A (RBI 6.5% vs IMF 6.5%) and Case B (Eco Survey 6.4% vs RBI 6.5%)."""
    facts = [FACT_RBI_ACTUAL_GDP, FACT_IMF_ACTUAL_GDP, FACT_ECOSURVEY_GDP]
    pairs = generate_candidate_pairs(facts)
    # 3 facts in ("india", "gdp_growth") group -> 3 candidate pairs
    assert len(pairs) == 3

    # Check Case A candidate: RBI ↔ IMF
    rbi_imf = next(
        p for p in pairs
        if ("rbi" in p.fact_a.evidence.document_id and "imf" in p.fact_b.evidence.document_id)
        or ("imf" in p.fact_a.evidence.document_id and "rbi" in p.fact_b.evidence.document_id)
    )
    assert rbi_imf.dimensions.subject == "same"
    assert rbi_imf.dimensions.predicate == "same"
    assert rbi_imf.dimensions.value == "same"
    assert rbi_imf.dimensions.unit == "same"
    assert rbi_imf.dimensions.period == "same"
    assert rbi_imf.numerical_comparison.is_exact is True

    # Check Case B candidate: EcoSurvey ↔ RBI
    eco_rbi = next(
        p for p in pairs
        if ("ecosurvey" in p.fact_a.evidence.document_id and "rbi" in p.fact_b.evidence.document_id)
        or ("rbi" in p.fact_a.evidence.document_id and "ecosurvey" in p.fact_b.evidence.document_id)
    )
    assert eco_rbi.dimensions.subject == "same"
    assert eco_rbi.dimensions.predicate == "same"
    assert eco_rbi.dimensions.value == "different"
    assert eco_rbi.dimensions.period == "same"
    assert eco_rbi.dimensions.qualifiers == "different"  # FAE vs SAE


def test_case_c_and_d_delhivery_standalone_vs_consolidated():
    """Delhivery standalone (74,540.82M) vs consolidated (81,415.38M)."""
    pairs = generate_candidate_pairs([FACT_DELHIVERY_STANDALONE, FACT_DELHIVERY_CONSOLIDATED])
    assert len(pairs) == 1
    pair = pairs[0]

    assert pair.dimensions.subject == "same"
    assert pair.dimensions.predicate == "same"
    assert pair.dimensions.value == "different"
    assert pair.dimensions.period == "same"
    assert pair.dimensions.scope == "different"  # standalone vs consolidated


def test_case_e_delhivery_director_facts():
    """Director facts: Suvir Suren Sujan prospectus listing vs later resignation."""
    pairs = generate_candidate_pairs([FACT_DIRECTOR_PROSPECTUS_SUJAN, FACT_DIRECTOR_AR_SUJAN])
    assert len(pairs) == 1
    pair = pairs[0]

    assert pair.fact_a.canonical_subject == "Suvir Suren Sujan"
    assert pair.fact_b.canonical_subject == "Suvir Suren Sujan"
    assert pair.dimensions.subject == "same"
    assert pair.dimensions.predicate == "same"
    # Source values preserved verbatim
    assert pair.fact_a.value == "Non-Executive Nominee Director"
    assert pair.fact_b.value == "resigned from the Board"
    # As of dates differ (2022 vs 2023-08-24)
    assert pair.dimensions.period == "different"


def test_case_f_delhivery_rounding_conversion():
    """₹249 Cr in earnings presentation vs ₹2,491.86M in Annual Report."""
    pairs = generate_candidate_pairs([FACT_DELHIVERY_AR_LOSS, FACT_DELHIVERY_PRESENTATION_LOSS])
    assert len(pairs) == 1
    pair = pairs[0]

    assert pair.dimensions.subject == "same"
    assert pair.dimensions.predicate == "same"
    assert pair.numerical_comparison.is_close_rounding is True
    assert pair.dimensions.value == "same"  # detected as close rounding
    assert pair.dimensions.unit == "same"   # converted to INR million


def test_case_g_rbi_imf_forecast_disagreement():
    """Forecast disagreement: RBI 6.5% vs IMF 6.6% for FY25-26."""
    pairs = generate_candidate_pairs([FACT_RBI_FORECAST_GDP, FACT_IMF_FORECAST_GDP])
    assert len(pairs) == 1
    pair = pairs[0]

    assert pair.dimensions.subject == "same"
    assert pair.dimensions.predicate == "same"
    assert pair.dimensions.period == "same"  # FY2025-26
    assert pair.dimensions.value == "different"  # 6.5 vs 6.6


def test_case_h_table_related_ambiguous_candidate():
    """Table row with missing/detached year header produces ambiguous period comparison."""
    norm_table = normalize_fact(FACT_TABLE_AMBIGUOUS)
    norm_rbi = normalize_fact(FACT_RBI_ACTUAL_GDP)

    period_comp = compare_periods(
        norm_table.normalized_period, norm_table.period_type,
        norm_rbi.normalized_period, norm_rbi.period_type,
    )
    assert period_comp.is_same_period is False
    assert "unspecified" in period_comp.compatibility_note.lower()


# ---------------------------------------------------------------------------
# 9. Semantic Safeguards Tests
# ---------------------------------------------------------------------------

def test_prepared_comparison_does_not_assign_final_relationship():
    """Gate 3 must NOT decide final relationship (no premature CORROBORATES/CONTRADICTS)."""
    pairs = generate_candidate_pairs([FACT_RBI_ACTUAL_GDP, FACT_IMF_ACTUAL_GDP])
    pair = pairs[0]
    # PreparedComparison does NOT even have a 'relationship' field
    assert not hasattr(pair, "relationship")


def test_different_scope_does_not_force_verdict():
    """Different scope exposes diff, does not classify."""
    pairs = generate_candidate_pairs([FACT_DELHIVERY_STANDALONE, FACT_DELHIVERY_CONSOLIDATED])
    pair = pairs[0]
    assert pair.dimensions.scope == "different"


def test_close_numerical_values_does_not_assert_semantic_corroboration():
    """Numerical closeness is an arithmetic observation, not a semantic classification."""
    comp = compare_numbers(81415.38, "INR million", 81420.0, "INR million")
    assert comp.is_close_rounding is True
    # Does not have semantic relationship attribute
    assert not hasattr(comp, "relationship")


# ---------------------------------------------------------------------------
# 10. Candidate Reduction Performance & Statistics
# ---------------------------------------------------------------------------

def test_candidate_reduction_statistics():
    facts = [
        FACT_RBI_ACTUAL_GDP, FACT_IMF_ACTUAL_GDP, FACT_ECOSURVEY_GDP,
        FACT_DELHIVERY_STANDALONE, FACT_DELHIVERY_CONSOLIDATED, FACT_DELHIVERY_PRESENTATION_REVENUE,
        FACT_DELHIVERY_AR_LOSS, FACT_DELHIVERY_PRESENTATION_LOSS,
        FACT_DIRECTOR_PROSPECTUS_SUJAN, FACT_DIRECTOR_AR_SUJAN,
        FACT_DIRECTOR_AR_COLLERAN, FACT_DIRECTOR_AR_BARASIA,
        FACT_RBI_FORECAST_GDP, FACT_IMF_FORECAST_GDP,
    ]
    pairs = generate_candidate_pairs(facts)
    stats = compute_candidate_statistics(facts, pairs)

    assert stats.total_facts == 14
    # Naive pairs: 14 * 13 / 2 = 91
    assert stats.total_naive_pairs == 91
    # Candidate pairs are strictly restricted to plausible matching groups
    assert stats.candidate_pairs_count < 20
    # Reduction ratio should be substantial (> 75%)
    assert stats.reduction_ratio >= 0.75
    assert stats.candidate_groups_count > 0


# ---------------------------------------------------------------------------
# 11. Edge-Case Regression Tests
# ---------------------------------------------------------------------------

def test_generate_candidate_pairs_empty_input():
    """Empty fact list must produce zero pairs without crashing."""
    pairs = generate_candidate_pairs([])
    assert pairs == []
    stats = compute_candidate_statistics([], pairs)
    assert stats.total_facts == 0
    assert stats.total_naive_pairs == 0
    assert stats.candidate_pairs_count == 0


def test_generate_candidate_pairs_all_unique_subjects():
    """Facts with all different subjects produce zero candidate pairs."""
    f1 = Fact(subject="Acme", predicate="revenue", value=100.0, unit="INR million",
              period="FY24", evidence=Evidence(document_id="d1", page_number=1, text="Acme rev"))
    f2 = Fact(subject="Bcorp", predicate="revenue", value=200.0, unit="INR million",
              period="FY24", evidence=Evidence(document_id="d2", page_number=1, text="Bcorp rev"))
    pairs = generate_candidate_pairs([f1, f2])
    assert len(pairs) == 0


def test_duplicate_evidence_filtered_out():
    """Two facts with identical document, page, evidence text, and value must be filtered."""
    f1 = Fact(subject="Delhivery", predicate="revenue", value=81415.38, unit="INR million",
              period="FY24", evidence=Evidence(document_id="ar_fy24", page_number=36,
              text="Revenue was INR 81,415.38 million.", document_name="AR.pdf"))
    f2 = Fact(subject="Delhivery", predicate="revenue", value=81415.38, unit="INR million",
              period="FY24", evidence=Evidence(document_id="ar_fy24", page_number=36,
              text="Revenue was INR 81,415.38 million.", document_name="AR.pdf"))
    pairs = generate_candidate_pairs([f1, f2], filter_irrelevant=True)
    assert len(pairs) == 0


def test_duplicate_evidence_different_value_not_filtered():
    """Same document/page/text but different value must NOT be filtered."""
    f1 = Fact(subject="Delhivery", predicate="revenue", value=81415.38, unit="INR million",
              period="FY24", evidence=Evidence(document_id="ar_fy24", page_number=36,
              text="Revenue was INR 81,415.38 million."))
    f2 = Fact(subject="Delhivery", predicate="revenue", value=74540.82, unit="INR million",
              period="FY24", evidence=Evidence(document_id="ar_fy24", page_number=36,
              text="Revenue was INR 81,415.38 million."))
    pairs = generate_candidate_pairs([f1, f2], filter_irrelevant=True)
    assert len(pairs) == 1


# ---------------------------------------------------------------------------
# 12. Unit Conversion Edge Cases
# ---------------------------------------------------------------------------

def test_lakh_to_million_conversion():
    v, u = normalize_unit_and_value(100.0, "lakh")
    assert v == 10.0
    assert u == "INR million"

def test_lac_to_million_conversion():
    v, u = normalize_unit_and_value(50.0, "lac")
    assert v == 5.0
    assert u == "INR million"

def test_billion_to_million_conversion():
    v, u = normalize_unit_and_value(1.5, "billion")
    assert v == 1500.0
    assert u == "INR million"

def test_raw_inr_to_million_conversion():
    v, u = normalize_unit_and_value(5000000.0, "INR")
    assert v == 5.0
    assert u == "INR million"

def test_raw_rupee_symbol_to_million():
    v, u = normalize_unit_and_value(10000000.0, "₹")
    assert v == 10.0
    assert u == "INR million"

def test_raw_rs_to_million():
    v, u = normalize_unit_and_value(2000000.0, "Rs.")
    assert v == 2.0
    assert u == "INR million"


# ---------------------------------------------------------------------------
# 13. Numeric-as-String Normalization
# ---------------------------------------------------------------------------

def test_numeric_string_promoted_to_float():
    """A numeric string with commas should be promoted to float for unit conversion."""
    v, u = normalize_unit_and_value("8,142", "₹ crore")
    assert v == 81420.0
    assert u == "INR million"

def test_numeric_string_no_unit():
    v, u = normalize_unit_and_value("6.5", "%")
    assert v == 6.5
    assert u == "percent"

def test_non_numeric_string_passthrough():
    v, u = normalize_unit_and_value("resigned from the Board", None)
    assert v == "resigned from the Board"
    assert u is None


# ---------------------------------------------------------------------------
# 14. Zero-Value Comparison
# ---------------------------------------------------------------------------

def test_zero_values_comparison():
    """Both values zero: relative diff is 0.0 (guarded by max_val > 0)."""
    comp = compare_numbers(0.0, "percent", 0.0, "percent")
    assert comp.is_exact is True
    assert comp.absolute_diff == 0.0
    assert comp.relative_diff == 0.0

def test_zero_vs_nonzero_comparison():
    comp = compare_numbers(0.0, "INR million", 100.0, "INR million")
    assert comp.is_exact is False
    assert comp.is_close_rounding is False
    assert comp.status == "different"


# ---------------------------------------------------------------------------
# 15. Anti-Shortcut: Same Number + Different Period
# ---------------------------------------------------------------------------

def test_anti_shortcut_same_number_different_period_not_corroboration():
    """
    Two facts with identical numeric value, same subject/predicate, but different
    periods must NOT be classified as CORROBORATES by the matcher (period is 'different').
    """
    f1 = Fact(subject="Delhivery", predicate="network reach", value=16677.0, unit="pin codes",
              period="FY21", evidence=Evidence(document_id="d1", page_number=1, text="16,677 PIN codes FY21"))
    f2 = Fact(subject="Delhivery", predicate="network reach", value=16677.0, unit="pin codes",
              period="FY22", evidence=Evidence(document_id="d2", page_number=1, text="16,677 PIN codes FY22"))
    pairs = generate_candidate_pairs([f1, f2])
    assert len(pairs) == 1
    # Matcher must expose that period is different
    assert pairs[0].dimensions.period == "different"
    # Value is the same
    assert pairs[0].dimensions.value == "same"
    # Subject and predicate are the same
    assert pairs[0].dimensions.subject == "same"
    assert pairs[0].dimensions.predicate == "same"


# ---------------------------------------------------------------------------
# 16. Ambiguous-Period Fact Flows Through Candidate Pairs
# ---------------------------------------------------------------------------

def test_ambiguous_period_fact_generates_candidate_pair():
    """A fact with period=None should still appear as a candidate when grouped by subject+predicate."""
    f_ambiguous = Fact(
        subject="India real GDP growth", predicate="growth rate", value=6.5, unit="%",
        period=None,  # missing due to detached table header
        evidence=Evidence(document_id="rbi_table", page_number=91, text="GDP growth 6.5% in 2024-25"),
    )
    f_actual = Fact(
        subject="India real GDP growth", predicate="growth rate", value=6.5, unit="per cent",
        period="2024-25",
        evidence=Evidence(document_id="rbi_ar", page_number=24, text="GDP growth 6.5 per cent in 2024-25"),
    )
    pairs = generate_candidate_pairs([f_ambiguous, f_actual])
    assert len(pairs) == 1
    pair = pairs[0]
    # The ambiguous fact's period should be unknown
    assert pair.dimensions.period == "unknown"
    assert pair.dimensions.subject == "same"
    assert pair.dimensions.predicate == "same"


# ---------------------------------------------------------------------------
# 17. Negative Values Through Unit Conversion
# ---------------------------------------------------------------------------

def test_negative_value_crore_conversion():
    v, u = normalize_unit_and_value(-500.0, "₹ crore")
    assert v == -5000.0
    assert u == "INR million"

def test_negative_value_percent():
    v, u = normalize_unit_and_value(-3.2, "%")
    assert v == -3.2
    assert u == "percent"
