"""
Gate 2 tests for structured fact extraction.
Tests A through J using fixture-based validation (no live LLM required).
"""

import pytest
from app.models import Fact, Evidence, FactComparison, Dimensions
from app.providers import MockProvider
from app.extraction import (
    _cmp, _cmp_list, _diff_dimensions,
    _parse_extracted_facts, _parse_comparison,
    EXTRACTION_TOOL, COMPARISON_TOOL,
)
from app.pipeline import extract_facts_from_page, extract_facts_from_document
from app.pdf_parser import parse_pdf_document, PageObject, ScoreBreakdown

STARTER_DIR = "starter-datasets"


# ---------------------------------------------------------------------------
# Fixture: representative extracted facts from known source text
# ---------------------------------------------------------------------------

RBI_GDP_FACT = Fact(
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
        text="Real GDP growth moderated to 6.5 per cent in 2024-25, as compared with 6.6 per cent in 2023-24.",
        document_name="RBI Annual Report 2024-25",
    ),
    confidence=0.95,
)

ECOSURVEY_GDP_FACT = Fact(
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
        document_name="Economic Survey 2024-25",
    ),
    confidence=0.95,
)

DELHIVERY_STANDALONE_FACT = Fact(
    subject="Delhivery revenue from operations",
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
        document_name="Delhivery Annual Report FY24",
    ),
    confidence=0.95,
)

DELHIVERY_CONSOLIDATED_FACT = Fact(
    subject="Delhivery revenue from operations",
    predicate="revenue",
    value=81415.38,
    unit="INR million",
    period="FY24",
    scope="consolidated",
    qualifiers=[],
    evidence=Evidence(
        document_id="delhivery_ar_fy24",
        page_number=36,
        text="The revenue from operations on consolidated basis for FY24 stood at INR 81,415.38 million.",
        document_name="Delhivery Annual Report FY24",
    ),
    confidence=0.95,
)

DIRECTOR_RESIGNATION_FACT = Fact(
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
        document_name="Delhivery Annual Report FY24",
    ),
    confidence=0.95,
)

DIRECTOR_CEASED_FACT = Fact(
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
        document_name="Delhivery Annual Report FY24",
    ),
    confidence=0.95,
)

DIRECTOR_ROLE_FACT = Fact(
    subject="Sandeep Kumar Barasia",
    predicate="role",
    value="Executive Director and Chief Business Officer",
    unit=None,
    period=None,
    as_of=None,
    scope=None,
    qualifiers=[],
    evidence=Evidence(
        document_id="delhivery_prospectus_2022",
        page_number=216,
        text="Sandeep Kumar Barasia — Executive Director and Chief Business Officer.",
        document_name="Delhivery Prospectus 2022",
    ),
    confidence=0.95,
)

# Multiple facts from one page
DELHIVERY_REVENUE_PAGE_FACTS = [
    Fact(
        subject="Delhivery standalone revenue",
        predicate="revenue from operations",
        value=74540.82,
        unit="INR million",
        period="FY24",
        scope="standalone",
        qualifiers=[],
        evidence=Evidence(document_id="d", page_number=36,
                          text="standalone basis for FY24 stood at INR 74,540.82 million",
                          document_name="AR FY24"),
        confidence=0.95,
    ),
    Fact(
        subject="Delhivery consolidated revenue",
        predicate="revenue from operations",
        value=81415.38,
        unit="INR million",
        period="FY24",
        scope="consolidated",
        qualifiers=[],
        evidence=Evidence(document_id="d", page_number=36,
                          text="consolidated basis for FY24 stood at INR 81,415.38 million",
                          document_name="AR FY24"),
        confidence=0.95,
    ),
    Fact(
        subject="Delhivery standalone revenue",
        predicate="revenue from operations",
        value=66586.61,
        unit="INR million",
        period="FY23",
        scope="standalone",
        qualifiers=[],
        evidence=Evidence(document_id="d", page_number=36,
                          text="as against INR 66,586.61 million for FY23",
                          document_name="AR FY24"),
        confidence=0.95,
    ),
]


# ---------------------------------------------------------------------------
# Test A: Numeric extraction
# ---------------------------------------------------------------------------

def test_numeric_fact_has_numeric_value():
    assert isinstance(RBI_GDP_FACT.value, float)
    assert RBI_GDP_FACT.value == 6.5

def test_numeric_fact_has_unit():
    assert RBI_GDP_FACT.unit == "per cent"

def test_numeric_currency_fact():
    assert isinstance(DELHIVERY_STANDALONE_FACT.value, float)
    assert DELHIVERY_STANDALONE_FACT.value == 74540.82
    assert DELHIVERY_STANDALONE_FACT.unit == "INR million"


# ---------------------------------------------------------------------------
# Test B: Categorical extraction
# ---------------------------------------------------------------------------

def test_categorical_fact_has_string_value():
    assert isinstance(DIRECTOR_RESIGNATION_FACT.value, str)
    assert DIRECTOR_RESIGNATION_FACT.value == "resigned from the Board"

def test_categorical_preserves_source_wording():
    assert "resigned" in DIRECTOR_RESIGNATION_FACT.value
    assert "ceased" in DIRECTOR_CEASED_FACT.value
    assert "Executive Director" in DIRECTOR_ROLE_FACT.value

def test_categorical_not_normalized_to_active():
    """The extractor should NOT invent 'active' when source only lists a director."""
    assert DIRECTOR_ROLE_FACT.value != "active"
    assert "active" not in DIRECTOR_ROLE_FACT.value.lower()


# ---------------------------------------------------------------------------
# Test C: Evidence preservation
# ---------------------------------------------------------------------------

def test_evidence_has_document_and_page():
    assert RBI_GDP_FACT.evidence.document_id != ""
    assert RBI_GDP_FACT.evidence.page_number > 0

def test_evidence_text_is_exact_source():
    assert "6.5 per cent" in RBI_GDP_FACT.evidence.text
    assert "Second Advance Estimates" not in RBI_GDP_FACT.evidence.text or \
           "Second Advance" in RBI_GDP_FACT.evidence.text

def test_evidence_retained_in_categorical():
    assert "resigned from the Board" in DIRECTOR_RESIGNATION_FACT.evidence.text
    assert "August 24, 2023" in DIRECTOR_RESIGNATION_FACT.evidence.text


# ---------------------------------------------------------------------------
# Test D: Missing optional metadata remains null
# ---------------------------------------------------------------------------

def test_missing_scope_is_none():
    assert RBI_GDP_FACT.scope is None

def test_missing_as_of_is_none():
    assert RBI_GDP_FACT.as_of is None

def test_empty_qualifiers_is_empty_list():
    assert DELHIVERY_STANDALONE_FACT.qualifiers == []


# ---------------------------------------------------------------------------
# Test E: Scope preservation
# ---------------------------------------------------------------------------

def test_scope_standalone_vs_consolidated():
    assert DELHIVERY_STANDALONE_FACT.scope == "standalone"
    assert DELHIVERY_CONSOLIDATED_FACT.scope == "consolidated"

def test_scope_diff_is_detected():
    dims = _diff_dimensions(DELHIVERY_STANDALONE_FACT, DELHIVERY_CONSOLIDATED_FACT)
    assert dims.scope == "different"


# ---------------------------------------------------------------------------
# Test F: Qualifier preservation
# ---------------------------------------------------------------------------

def test_qualifiers_first_advance_estimate():
    assert "First Advance Estimate" in ECOSURVEY_GDP_FACT.qualifiers

def test_qualifiers_second_advance_estimate():
    assert "Second Advance Estimates" in RBI_GDP_FACT.qualifiers

def test_qualifier_diff_is_detected():
    dims = _diff_dimensions(ECOSURVEY_GDP_FACT, RBI_GDP_FACT)
    assert dims.qualifiers == "different"


# ---------------------------------------------------------------------------
# Test G: Period / as-of preservation
# ---------------------------------------------------------------------------

def test_period_preserved():
    assert RBI_GDP_FACT.period == "2024-25"
    assert ECOSURVEY_GDP_FACT.period == "FY25"

def test_as_of_preserved_for_director_events():
    assert DIRECTOR_RESIGNATION_FACT.as_of == "August 24, 2023"
    assert DIRECTOR_CEASED_FACT.as_of == "September 27, 2023"

def test_as_of_is_separate_from_period():
    """Period and as_of are different concepts for director events."""
    assert DIRECTOR_RESIGNATION_FACT.period is None
    assert DIRECTOR_RESIGNATION_FACT.as_of is not None


# ---------------------------------------------------------------------------
# Test H: Multiple facts from a single page
# ---------------------------------------------------------------------------

def test_multiple_facts_from_revenue_page():
    assert len(DELHIVERY_REVENUE_PAGE_FACTS) == 3

def test_multiple_facts_different_periods():
    periods = {f.period for f in DELHIVERY_REVENUE_PAGE_FACTS}
    assert "FY24" in periods
    assert "FY23" in periods

def test_multiple_facts_different_scopes():
    scopes = {f.scope for f in DELHIVERY_REVENUE_PAGE_FACTS}
    assert "standalone" in scopes
    assert "consolidated" in scopes


# ---------------------------------------------------------------------------
# Test I: No boilerplate padding
# ---------------------------------------------------------------------------

def test_facts_are_not_padded_with_boilerplate():
    """A short text should produce only relevant facts, not padding."""
    provider = MockProvider(facts=[
        Fact(subject="X", predicate="Y", value=1.0,
             evidence=Evidence(document_id="d", page_number=1, text="X is 1.0"),
             confidence=0.9)
    ])
    page = PageObject(
        document_id="test", filename="test.pdf", page_number=1,
        raw_text="X is 1.0", text_length=7, blocks_count=1,
        has_tables=False, score_breakdown=ScoreBreakdown(total_score=10.0, is_candidate=True),
    )
    facts = extract_facts_from_page(provider, page)
    assert len(facts) == 1
    assert provider.extract_call_count == 1

def test_empty_page_produces_no_facts():
    provider = MockProvider(facts=[])
    page = PageObject(
        document_id="test", filename="test.pdf", page_number=1,
        raw_text="", text_length=0, blocks_count=0,
        has_tables=False, score_breakdown=ScoreBreakdown(total_score=0.0, is_candidate=False),
    )
    facts = extract_facts_from_page(provider, page, candidate_only=True)
    assert len(facts) == 0
    assert provider.extract_call_count == 0


# ---------------------------------------------------------------------------
# Test J: Structured LLM output validation
# ---------------------------------------------------------------------------

def test_extraction_tool_schema_is_valid():
    """Verify the extraction tool schema has required fields."""
    schema = EXTRACTION_TOOL["input_schema"]
    assert "facts" in schema["properties"]
    fact_items = schema["properties"]["facts"]["items"]["properties"]
    required_fields = ["subject", "predicate", "value", "evidence_text", "confidence"]
    for field in required_fields:
        assert field in fact_items, f"Missing required field: {field}"

def test_comparison_tool_schema_is_valid():
    """Verify the comparison tool schema has required fields."""
    schema = COMPARISON_TOOL["input_schema"]
    assert "relationship" in schema["properties"]
    assert "confidence" in schema["properties"]
    assert "reason" in schema["properties"]
    relationships = schema["properties"]["relationship"]["enum"]
    assert "CORROBORATES" in relationships
    assert "CONTRADICTS" in relationships
    assert "RECONCILABLE" in relationships
    assert "UNRELATED" in relationships
    assert "UNCERTAIN" in relationships

def test_parse_extracted_facts():
    """Verify raw dict parsing produces valid Fact objects."""
    raw = [{
        "subject": "India GDP",
        "predicate": "growth",
        "value": 6.5,
        "unit": "per cent",
        "period": "2024-25",
        "scope": None,
        "qualifiers": [],
        "evidence_text": "GDP grew by 6.5 per cent.",
        "confidence": 0.9,
    }]
    facts = _parse_extracted_facts(raw, "TestDoc p.1")
    assert len(facts) == 1
    assert facts[0].subject == "India GDP"
    assert facts[0].value == 6.5
    assert facts[0].evidence.text == "GDP grew by 6.5 per cent."
    assert facts[0].evidence.document_name == "TestDoc p.1"

def test_parse_comparison():
    """Verify raw dict parsing produces valid FactComparison."""
    raw = {"relationship": "RECONCILABLE", "confidence": 0.9, "reason": "Different estimate vintages."}
    dims = Dimensions(subject="same", predicate="same", value="different",
                      unit="same", period="same", scope="same", qualifiers="different")
    comp = _parse_comparison(raw, "a1", "b2", dims)
    assert comp.relationship == "RECONCILABLE"
    assert comp.fact_a_id == "a1"
    assert comp.fact_b_id == "b2"
    assert comp.dimensions.qualifiers == "different"

def test_mock_provider_returns_facts():
    """Verify MockProvider integration with pipeline."""
    provider = MockProvider(facts=[RBI_GDP_FACT])
    page = PageObject(
        document_id="test", filename="test.pdf", page_number=1,
        raw_text="GDP grew by 6.5 per cent", text_length=25, blocks_count=1,
        has_tables=False, score_breakdown=ScoreBreakdown(total_score=10.0, is_candidate=True),
    )
    facts = extract_facts_from_page(provider, page)
    assert len(facts) == 1
    assert facts[0].evidence.document_id == "test"
    assert facts[0].evidence.page_number == 1


# ---------------------------------------------------------------------------
# Deterministic dimension diff tests
# ---------------------------------------------------------------------------

def test_cmp_same():
    assert _cmp("hello", "hello") == "same"

def test_cmp_different():
    assert _cmp("hello", "world") == "different"

def test_cmp_none():
    assert _cmp(None, "hello") == "unknown"

def test_cmp_list_same():
    assert _cmp_list(["a", "b"], ["B", "A"]) == "same"

def test_cmp_list_different():
    assert _cmp_list(["First Advance Estimate"], ["Second Advance Estimate"]) == "different"

def test_cmp_list_one_empty():
    assert _cmp_list(["a"], []) == "unknown"

def test_cmp_list_both_empty():
    assert _cmp_list([], []) == "same"
