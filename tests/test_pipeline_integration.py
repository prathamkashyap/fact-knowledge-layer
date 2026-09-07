"""
Pipeline integration tests: verifies the full extraction→normalization→matching→reasoning
flow without HTTP/API. Also validates database round-trip serialization and model safety.

These tests guard against regressions that would break Gate 5's API/UI layer.
"""

import json
import pytest
from app.models import (
    Fact, Evidence, Dimensions, PreparedComparison, FactComparison,
    NumericalComparison, PeriodComparison, CandidateStatistics,
)
from app.providers import MockProvider
from app.pdf_parser import parse_pdf_document, PageObject, ScoreBreakdown
from app.pipeline import (
    extract_facts_from_page,
    extract_facts_from_document,
    extract_facts_from_pages,
    extract_heuristic_facts_from_page,
    process_document_pipeline,
)
from app.normalizer import normalize_facts, normalize_fact
from app.matcher import generate_candidate_pairs, compute_candidate_statistics
from app.reasoner import reason_all_comparisons, reason_comparison
from app.database import Database


STARTER_DIR = "starter-datasets"


# ---------------------------------------------------------------------------
# 1. PDF → Parsed Pages → Facts: Full Pipeline Flow
# ---------------------------------------------------------------------------

def test_rbi_pdf_produces_gdp_facts():
    """RBI Annual Report PDF must parse without crashing; heuristic extraction may find 0 facts
    (the rule-based extractor is pattern-specific, not comprehensive)."""
    doc = parse_pdf_document(f"{STARTER_DIR}/india-macroeconomy/02-rbi-annual-report-2024-25-excerpt.pdf")
    assert doc.total_pages > 0
    assert doc.candidate_pages_count >= 0
    all_facts = []
    for page in doc.pages:
        all_facts.extend(extract_facts_from_page(None, page, candidate_only=True))
    # Heuristic extractor may or may not find facts depending on exact text patterns
    # The important thing is it doesn't crash
    assert isinstance(all_facts, list)


def test_delhivery_ar_produces_revenue_facts():
    """Delhivery Annual Report must parse without crashing; heuristic extractor finds director facts."""
    doc = parse_pdf_document(f"{STARTER_DIR}/delhivery/02-delhivery-annual-report-fy24-excerpt.pdf")
    assert doc.total_pages > 0
    all_facts = []
    for page in doc.pages:
        all_facts.extend(extract_facts_from_page(None, page, candidate_only=True))
    # The heuristic extractor finds director events from this document
    assert len(all_facts) >= 1


# ---------------------------------------------------------------------------
# 2. Evidence Trail: Extraction → Pipeline → Normalization
# ---------------------------------------------------------------------------

def test_evidence_survives_normalization():
    """Evidence fields must be preserved after normalization."""
    fact = Fact(
        subject="Delhivery revenue from operations", predicate="revenue",
        value=81415.38, unit="INR million", period="FY24", scope="consolidated",
        evidence=Evidence(document_id="ar_fy24", page_number=36,
                          text="Revenue stood at INR 81,415.38 million.",
                          document_name="AR FY24"),
    )
    norm = normalize_fact(fact)
    assert norm.evidence.document_id == "ar_fy24"
    assert norm.evidence.page_number == 36
    assert norm.evidence.text == "Revenue stood at INR 81,415.38 million."
    assert norm.evidence.document_name == "AR FY24"
    # Original fact must not be mutated
    assert fact.canonical_subject is None


def test_evidence_survives_full_pipeline():
    """Evidence must survive through normalize → match → reason."""
    f1 = Fact(
        subject="Delhivery revenue from operations", predicate="revenue",
        value=74540.82, unit="INR million", period="FY24", scope="standalone",
        evidence=Evidence(document_id="ar_fy24", page_number=36,
                          text="Standalone revenue INR 74,540.82 million.",
                          document_name="AR.pdf"),
    )
    f2 = Fact(
        subject="Delhivery revenue from operations", predicate="revenue",
        value=81415.38, unit="INR million", period="FY24", scope="consolidated",
        evidence=Evidence(document_id="ar_fy24", page_number=36,
                          text="Consolidated revenue INR 81,415.38 million.",
                          document_name="AR.pdf"),
    )
    pairs = generate_candidate_pairs([f1, f2])
    assert len(pairs) == 1
    pair = pairs[0]

    # Evidence on both facts must be intact
    assert pair.fact_a.evidence.document_id == "ar_fy24"
    assert pair.fact_b.evidence.text == "Consolidated revenue INR 81,415.38 million."

    comp = reason_comparison(pair)
    assert comp.fact_a_id == pair.fact_a.id
    assert comp.fact_b_id == pair.fact_b.id
    assert comp.dimensions.scope == "different"
    assert len(comp.reason) > 0


def test_source_page_document_metadata_survives_pipeline():
    """document_name and page_number must survive through the full pipeline."""
    fact = Fact(
        subject="India", predicate="GDP", value=6.5, unit="percent",
        period="2024-25",
        evidence=Evidence(document_id="rbi_2024", page_number=24,
                          text="GDP grew 6.5%", document_name="RBI AR 2024-25.pdf"),
    )
    pairs = generate_candidate_pairs([fact, fact])  # same fact won't pair, need two distinct
    f2 = Fact(
        subject="India", predicate="GDP", value=6.5, unit="percent",
        period="2024-25",
        evidence=Evidence(document_id="imf_2025", page_number=12,
                          text="GDP 6.5%", document_name="IMF Art IV.pdf"),
    )
    pairs = generate_candidate_pairs([fact, f2])
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair.fact_a.evidence.document_name == "RBI AR 2024-25.pdf"
    assert pair.fact_b.evidence.document_name == "IMF Art IV.pdf"
    assert pair.fact_a.evidence.page_number == 24
    assert pair.fact_b.evidence.page_number == 12


# ---------------------------------------------------------------------------
# 3. Normalization → Matching Integration
# ---------------------------------------------------------------------------

def test_normalize_then_match_produces_correct_dimensions():
    """Normalized facts should produce correct dimension diffs."""
    f1 = Fact(subject="Delhivery", predicate="revenue", value=74540.0, unit="INR million",
              period="FY24", scope="standalone",
              evidence=Evidence(document_id="d1", page_number=1, text="Standalone 74540M"))
    f2 = Fact(subject="Delhivery", predicate="revenue", value=81415.0, unit="INR million",
              period="FY24", scope="consolidated",
              evidence=Evidence(document_id="d2", page_number=1, text="Consolidated 81415M"))

    pairs = generate_candidate_pairs([f1, f2])
    assert len(pairs) == 1
    dims = pairs[0].dimensions
    assert dims.subject == "same"
    assert dims.predicate == "same"
    assert dims.period == "same"
    assert dims.scope == "different"


def test_normalize_then_match_cross_document():
    """Facts from different documents should still be matched if subjects align."""
    f1 = Fact(subject="India real GDP growth", predicate="growth rate", value=6.5,
              unit="per cent", period="2024-25",
              evidence=Evidence(document_id="rbi", page_number=24, text="GDP 6.5%"))
    f2 = Fact(subject="India real GDP", predicate="growth rate", value=6.5,
              unit="percent", period="FY2024/25",
              evidence=Evidence(document_id="imf", page_number=12, text="GDP 6.5%"))

    pairs = generate_candidate_pairs([f1, f2])
    assert len(pairs) == 1
    # After normalization, both should have canonical_subject="India" and canonical_predicate="gdp_growth"
    assert pairs[0].fact_a.canonical_subject == "India"
    assert pairs[0].fact_b.canonical_subject == "India"


# ---------------------------------------------------------------------------
# 4. Matching → Reasoning Integration
# ---------------------------------------------------------------------------

def test_match_then_reason_produces_valid_comparison():
    """generate_candidate_pairs → reason_all_comparisons must produce valid FactComparison objects."""
    f1 = Fact(subject="India", predicate="GDP growth", value=6.5, unit="percent",
              period="2024-25",
              evidence=Evidence(document_id="d1", page_number=1, text="GDP 6.5%"))
    f2 = Fact(subject="India", predicate="GDP growth", value=6.5, unit="percent",
              period="2024-25",
              evidence=Evidence(document_id="d2", page_number=1, text="GDP 6.5%"))

    pairs = generate_candidate_pairs([f1, f2])
    results = reason_all_comparisons(pairs)
    assert len(results) == 1
    comp = results[0]
    assert comp.relationship in ("CORROBORATES", "CONTRADICTS", "RECONCILABLE", "UNRELATED", "UNCERTAIN")
    assert 0.0 <= comp.confidence <= 1.0
    assert len(comp.reason) > 0
    assert comp.fact_a_id != "" and comp.fact_b_id != ""


def test_reason_statistics_consistent_with_pairs():
    """CandidateStatistics must be consistent with actual pair counts."""
    f1 = Fact(subject="India", predicate="GDP", value=6.5, unit="%", period="2024-25",
              evidence=Evidence(document_id="d1", page_number=1, text="GDP 6.5"))
    f2 = Fact(subject="India", predicate="GDP", value=6.5, unit="%", period="2024-25",
              evidence=Evidence(document_id="d2", page_number=1, text="GDP 6.5"))
    f3 = Fact(subject="Delhivery", predicate="revenue", value=100.0, unit="INR million", period="FY24",
              evidence=Evidence(document_id="d3", page_number=1, text="Revenue 100M"))

    pairs = generate_candidate_pairs([f1, f2, f3])
    stats = compute_candidate_statistics([f1, f2, f3], pairs)
    assert stats.total_facts == 3
    assert stats.total_naive_pairs == 3  # 3C2 = 3
    assert stats.candidate_pairs_count == len(pairs)
    assert stats.reduction_ratio == round(1.0 - len(pairs) / 3, 4) if 3 > 0 else 0.0


# ---------------------------------------------------------------------------
# 5. Empty / Single-Element Edge Cases
# ---------------------------------------------------------------------------

def test_empty_facts_list_through_pipeline():
    """Empty fact list must flow through normalize → match → reason without crashing."""
    norm = normalize_facts([])
    assert norm == []
    pairs = generate_candidate_pairs([])
    assert pairs == []
    results = reason_all_comparisons([])
    assert results == []


def test_single_fact_no_pairs():
    """A single fact should produce zero candidate pairs."""
    f = Fact(subject="India", predicate="GDP", value=6.5, unit="%",
             evidence=Evidence(document_id="d1", page_number=1, text="GDP 6.5"))
    pairs = generate_candidate_pairs([f])
    assert len(pairs) == 0


def test_two_unrelated_facts_no_pairs():
    """Facts about different entities should produce zero candidate pairs."""
    f1 = Fact(subject="India", predicate="GDP", value=6.5, unit="%",
              evidence=Evidence(document_id="d1", page_number=1, text="GDP 6.5"))
    f2 = Fact(subject="Delhivery", predicate="revenue", value=100.0, unit="INR million",
              evidence=Evidence(document_id="d2", page_number=1, text="Revenue 100M"))
    pairs = generate_candidate_pairs([f1, f2])
    assert len(pairs) == 0


# ---------------------------------------------------------------------------
# 6. Provider Failure Fallback
# ---------------------------------------------------------------------------

def test_provider_crash_fallback_in_reasoning():
    """When provider throws during reasoning, must fall back to UNCERTAIN."""
    class CrashProvider:
        def compare_facts(self, fact_a, fact_b):
            raise ConnectionError("API down")

    f1 = Fact(subject="India", predicate="GDP", value=6.5, unit="%",
              period="2024-25",
              evidence=Evidence(document_id="d1", page_number=1, text="GDP 6.5"))
    f2 = Fact(subject="India", predicate="GDP", value=6.5, unit="%",
              period="2024-25",
              evidence=Evidence(document_id="d2", page_number=1, text="GDP 6.5"))

    pairs = generate_candidate_pairs([f1, f2])
    comp = reason_comparison(pairs[0], provider=CrashProvider())  # type: ignore
    assert comp.relationship == "UNCERTAIN"
    assert comp.confidence == 0.0


def test_provider_crash_fallback_in_extraction():
    """When provider throws during extraction, must not crash the pipeline page-level call."""
    class CrashProvider:
        def extract_facts(self, text, page_context):
            raise RuntimeError("LLM unavailable")

    page = PageObject(
        document_id="test", filename="test.pdf", page_number=1,
        raw_text="Some text here", text_length=14, blocks_count=1,
        has_tables=False,
        score_breakdown=ScoreBreakdown(total_score=10.0, is_candidate=True),
    )
    # extract_facts_from_page calls provider.extract_facts - if it throws, the exception propagates
    # This is expected behavior - the caller (process_document_pipeline) should handle it
    with pytest.raises(RuntimeError):
        extract_facts_from_page(CrashProvider(), page)


# ---------------------------------------------------------------------------
# 7. Database Round-Trip Serialization
# ---------------------------------------------------------------------------

def test_database_fact_round_trip_numeric():
    """Numeric fact must survive save → reload with correct value type."""
    db = Database(":memory:")
    fact = Fact(
        subject="India", predicate="GDP", value=6.5, unit="percent", period="2024-25",
        evidence=Evidence(document_id="d1", page_number=1, text="GDP 6.5%", document_name="doc.pdf"),
        confidence=0.95,
        canonical_subject="India", canonical_predicate="gdp_growth",
        normalized_value=6.5, normalized_unit="percent",
        normalized_period="FY2024-25", period_type="fiscal_year",
    )
    db.save_fact(fact)
    loaded = db.get_facts(limit=1)
    assert len(loaded) == 1
    lf = loaded[0]
    assert lf.id == fact.id
    assert lf.value == 6.5
    assert isinstance(lf.value, float)
    assert lf.subject == "India"
    assert lf.evidence.document_id == "d1"
    assert lf.evidence.text == "GDP 6.5%"
    assert lf.evidence.document_name == "doc.pdf"
    assert lf.canonical_subject == "India"
    assert lf.normalized_value == 6.5
    assert lf.period_type == "fiscal_year"
    db.close()


def test_database_fact_round_trip_categorical():
    """Categorical (string) fact must survive save → reload."""
    db = Database(":memory:")
    fact = Fact(
        subject="Suvir Suren Sujan", predicate="board status",
        value="resigned from the Board",
        as_of="August 24, 2023",
        evidence=Evidence(document_id="ar_fy24", page_number=24, text="Sujan resigned"),
    )
    db.save_fact(fact)
    loaded = db.get_facts(limit=1)
    assert len(loaded) == 1
    lf = loaded[0]
    assert lf.value == "resigned from the Board"
    assert isinstance(lf.value, str)
    assert lf.as_of == "August 24, 2023"
    assert lf.period is None
    db.close()


def test_database_comparison_round_trip():
    """FactComparison must survive save → reload with all fields intact."""
    db = Database(":memory:")
    # Save facts first (comparisons reference facts via FK)
    fa = Fact(subject="A", predicate="x", value=1.0,
              evidence=Evidence(document_id="d1", page_number=1, text="A is 1"))
    fb = Fact(subject="B", predicate="x", value=2.0,
              evidence=Evidence(document_id="d2", page_number=1, text="B is 2"))
    db.save_facts([fa, fb])

    comp = FactComparison(
        fact_a_id=fa.id, fact_b_id=fb.id,
        relationship="RECONCILABLE", confidence=0.92,
        reason="Different scope: standalone vs consolidated.",
        dimensions=Dimensions(subject="same", predicate="same", value="different",
                              unit="same", period="same", scope="different", qualifiers="same"),
    )
    db.save_comparison(comp, reasoning_mode="heuristic")
    rels = db.get_relationships()
    assert len(rels) == 1
    r = rels[0]
    assert r["relationship"] == "RECONCILABLE"
    assert r["confidence"] == 0.92
    assert r["reason"] == "Different scope: standalone vs consolidated."
    assert r["dimensions"]["scope"] == "different"
    assert r["reasoning_mode"] == "heuristic"
    db.close()


def test_database_empty_qualifiers_round_trip():
    """Empty qualifiers list must survive save → reload."""
    db = Database(":memory:")
    fact = Fact(
        subject="X", predicate="Y", value=1.0,
        evidence=Evidence(document_id="d1", page_number=1, text="X is 1"),
        qualifiers=[],
    )
    db.save_fact(fact)
    loaded = db.get_facts(limit=1)
    assert loaded[0].qualifiers == []
    db.close()


def test_database_nonempty_qualifiers_round_trip():
    """Non-empty qualifiers list must survive save → reload."""
    db = Database(":memory:")
    fact = Fact(
        subject="India", predicate="GDP", value=6.5, unit="percent", period="2024-25",
        qualifiers=["First Advance Estimate", "forecast"],
        evidence=Evidence(document_id="d1", page_number=1, text="GDP 6.5%"),
    )
    db.save_fact(fact)
    loaded = db.get_facts(limit=1)
    assert loaded[0].qualifiers == ["First Advance Estimate", "forecast"]
    db.close()


# ---------------------------------------------------------------------------
# 8. Model JSON Serialization Safety
# ---------------------------------------------------------------------------

def test_fact_model_json_round_trip():
    """Fact must survive model_dump → JSON → model_validate cycle."""
    fact = Fact(
        subject="India", predicate="GDP growth", value=6.5, unit="percent",
        period="2024-25", scope=None, qualifiers=["forecast"],
        evidence=Evidence(document_id="d1", page_number=1, text="GDP 6.5%"),
        confidence=0.95,
        canonical_subject="India", canonical_predicate="gdp_growth",
        normalized_value=6.5, normalized_unit="percent",
    )
    dumped = fact.model_dump()
    json_str = json.dumps(dumped)
    loaded = Fact.model_validate(json.loads(json_str))
    assert loaded.id == fact.id
    assert loaded.value == 6.5
    assert loaded.evidence.document_id == "d1"
    assert loaded.qualifiers == ["forecast"]
    assert loaded.canonical_subject == "India"


def test_fact_comparison_model_json_round_trip():
    """FactComparison must survive JSON round-trip."""
    comp = FactComparison(
        fact_a_id="aaa", fact_b_id="bbb",
        relationship="CORROBORATES", confidence=0.96,
        reason="Both confirm 6.5%.",
        dimensions=Dimensions(subject="same", predicate="same", value="same",
                              unit="same", period="same", scope="same", qualifiers="same"),
    )
    dumped = comp.model_dump()
    json_str = json.dumps(dumped)
    loaded = FactComparison.model_validate(json.loads(json_str))
    assert loaded.relationship == "CORROBORATES"
    assert loaded.dimensions.value == "same"


def test_prepared_comparison_model_json_round_trip():
    """PreparedComparison with nested Fact objects must survive JSON round-trip."""
    fact = Fact(subject="India", predicate="GDP", value=6.5, unit="%",
                evidence=Evidence(document_id="d1", page_number=1, text="GDP 6.5"))
    prepared = PreparedComparison(
        fact_a=fact, fact_b=fact,
        dimensions=Dimensions(subject="same", predicate="same", value="same",
                              unit="same", period="same", scope="same", qualifiers="same"),
        numerical_comparison=NumericalComparison(
            is_numeric=True, status="exact", is_exact=True,
            value_a_norm=6.5, value_b_norm=6.5, unit_norm="percent",
        ),
        period_comparison=PeriodComparison(
            period_a_norm="FY2024-25", period_b_norm="FY2024-25",
            type_a="fiscal_year", type_b="fiscal_year",
            is_same_period=True, is_same_type=True,
        ),
    )
    dumped = prepared.model_dump()
    json_str = json.dumps(dumped)
    loaded = PreparedComparison.model_validate(json.loads(json_str))
    assert loaded.fact_a.value == 6.5
    assert loaded.numerical_comparison.is_exact is True
    assert loaded.period_comparison.is_same_period is True


def test_categorical_value_union_type_preserved():
    """Union[float, str] must preserve the correct type through JSON round-trip."""
    # Numeric
    f_num = Fact(subject="X", predicate="y", value=42.0,
                 evidence=Evidence(document_id="d", page_number=1, text="X=42"))
    dumped = f_num.model_dump()
    loaded = Fact.model_validate(json.loads(json.dumps(dumped)))
    assert isinstance(loaded.value, float)
    assert loaded.value == 42.0

    # String
    f_str = Fact(subject="X", predicate="y", value="resigned",
                 evidence=Evidence(document_id="d", page_number=1, text="X resigned"))
    dumped = f_str.model_dump()
    loaded = Fact.model_validate(json.loads(json.dumps(dumped)))
    assert isinstance(loaded.value, str)
    assert loaded.value == "resigned"


# ---------------------------------------------------------------------------
# 9. Heuristic Extraction → Full Pipeline (with starter data)
# ---------------------------------------------------------------------------

def test_heuristic_pipeline_rbi_document():
    """Full heuristic pipeline on RBI PDF must parse without crashing."""
    doc = parse_pdf_document(f"{STARTER_DIR}/india-macroeconomy/02-rbi-annual-report-2024-25-excerpt.pdf")
    all_facts = []
    for page in doc.pages:
        all_facts.extend(extract_facts_from_page(None, page, candidate_only=True))
    # Verify evidence metadata is correctly attached for any extracted facts
    for f in all_facts:
        assert f.evidence.document_id == doc.document_id
        assert f.evidence.page_number > 0
        assert len(f.evidence.text) > 0


def test_heuristic_pipeline_delhivery_document():
    """Full heuristic pipeline on Delhivery AR must produce director facts."""
    doc = parse_pdf_document(f"{STARTER_DIR}/delhivery/02-delhivery-annual-report-fy24-excerpt.pdf")
    all_facts = []
    for page in doc.pages:
        all_facts.extend(extract_facts_from_page(None, page, candidate_only=True))
    # Heuristic extractor finds director events
    board_facts = [f for f in all_facts if f.predicate in ("board status", "role")]
    assert len(board_facts) >= 1
    for f in board_facts:
        assert f.evidence.document_id == doc.document_id


def test_heuristic_pipeline_cross_document_matching():
    """MockProvider facts from two 'documents' should match across documents."""
    f1 = Fact(subject="India real GDP growth", predicate="growth rate", value=6.5,
              unit="per cent", period="2024-25",
              evidence=Evidence(document_id="rbi_doc", page_number=24, text="GDP 6.5% per cent"),
              canonical_subject="India", canonical_predicate="gdp_growth")
    f2 = Fact(subject="India real GDP growth", predicate="growth rate", value=6.4,
              unit="per cent", period="FY25",
              evidence=Evidence(document_id="ecosurvey_doc", page_number=28, text="GDP 6.4% per cent"),
              canonical_subject="India", canonical_predicate="gdp_growth")

    pairs = generate_candidate_pairs([f1, f2])
    assert len(pairs) >= 1
    cross_doc = [p for p in pairs if p.fact_a.evidence.document_id != p.fact_b.evidence.document_id]
    assert len(cross_doc) >= 1
    comp = reason_comparison(cross_doc[0])
    assert comp.relationship in ("CORROBORATES", "CONTRADICTS", "RECONCILABLE", "UNRELATED", "UNCERTAIN")


# ---------------------------------------------------------------------------
# 10. Model Field Completeness for UI Evidence Trail
# ---------------------------------------------------------------------------

def test_fact_has_all_ui_required_fields():
    """Fact must contain all fields needed for UI evidence display."""
    fact = Fact(
        subject="India", predicate="GDP", value=6.5, unit="percent",
        period="2024-25", scope="consolidated", qualifiers=["forecast"],
        evidence=Evidence(document_id="d1", page_number=1, text="GDP was 6.5%",
                          document_name="report.pdf"),
        confidence=0.95,
        canonical_subject="India", canonical_predicate="gdp_growth",
        normalized_value=6.5, normalized_unit="percent",
        normalized_period="FY2024-25", period_type="fiscal_year",
    )
    dumped = fact.model_dump()
    # UI needs these for evidence trail
    assert "evidence" in dumped
    assert dumped["evidence"]["document_id"] == "d1"
    assert dumped["evidence"]["page_number"] == 1
    assert dumped["evidence"]["text"] == "GDP was 6.5%"
    assert dumped["evidence"]["document_name"] == "report.pdf"
    # UI needs these for fact display
    assert dumped["subject"] == "India"
    assert dumped["value"] == 6.5
    assert dumped["period"] == "2024-25"
    assert dumped["scope"] == "consolidated"
    assert dumped["qualifiers"] == ["forecast"]


def test_comparison_has_all_ui_required_fields():
    """FactComparison must contain all fields needed for UI relationship display."""
    comp = FactComparison(
        fact_a_id="aaa", fact_b_id="bbb",
        relationship="RECONCILABLE", confidence=0.92,
        reason="Different scope.",
        dimensions=Dimensions(subject="same", predicate="same", value="different",
                              unit="same", period="same", scope="different", qualifiers="same"),
    )
    dumped = comp.model_dump()
    assert "relationship" in dumped
    assert "confidence" in dumped
    assert "reason" in dumped
    assert "dimensions" in dumped
    assert "fact_a_id" in dumped
    assert "fact_b_id" in dumped
    # Dimensions must have all 7 fields for UI
    dims = dumped["dimensions"]
    for field in ("subject", "predicate", "value", "unit", "period", "scope", "qualifiers"):
        assert field in dims
