"""
Regression tests for reporting-basis/context capture in the broad revenue
pattern (Limitation L1) — Week 2 Phase 5.

L1 (documented in docs/validation/BASELINE.md): the Delhivery annual report
states both revenue figures in one sentence pair on p.22 —

  'The revenue from operations on standalone basis for FY24 stood at
   Rs 74,540.82 million'
  'The revenue from operations on consolidated basis for FY24 stood at
   Rs 81,415.38 million'

— but the broad extraction pattern that matches the rupee-symbol wording
hardcoded period=None and scope=None. With scope missing, the reasoner
could not reach the scope-reconciliation rule, the period looked unknown,
and the pair read UNCERTAIN instead of RECONCILABLE.

The named regression test below asserts the exact case, end to end through
the real PDF, resolves to RECONCILABLE with standalone/consolidated scopes
captured.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models import Fact, Evidence  # noqa: E402
from app.pdf_parser import PageObject, ScoreBreakdown  # noqa: E402
from app.pipeline import extract_heuristic_facts_from_page, process_document_pipeline  # noqa: E402
from app.matcher import generate_candidate_pairs  # noqa: E402
from app.reasoner import reason_comparison  # noqa: E402
from app.database import Database  # noqa: E402

STARTER_AR = REPO_ROOT / "starter-datasets" / "delhivery" / "02-delhivery-annual-report-fy24-excerpt.pdf"


def _page(raw_text: str, filename: str = "02-delhivery-annual-report-fy24-excerpt.pdf") -> PageObject:
    return PageObject(
        document_id="ar_fy24",
        filename=filename,
        page_number=22,
        raw_text=raw_text,
        text_length=len(raw_text),
        blocks_count=1,
        has_tables=False,
        score_breakdown=ScoreBreakdown(total_score=10.0, is_candidate=True),
    )


def _revenue_fact(scope, period, value, page_number=22) -> Fact:
    return Fact(
        subject="delhivery revenue",
        predicate="revenue",
        value=value,
        unit="INR million",
        period=period,
        scope=scope,
        evidence=Evidence(
            document_id="ar_fy24",
            page_number=page_number,
            text=f"revenue stood at {value}",
            document_name="AR.pdf",
        ),
    )


# ---------------------------------------------------------------------------
# THE named regression test for L1 — end to end on the real PDF
# ---------------------------------------------------------------------------

def test_revenue_pair_reconcilable_by_scope_end_to_end():
    """The real standalone/consolidated revenue pair must classify
    RECONCILABLE, not UNCERTAIN, with both scopes and FY24 captured."""
    db = Database(":memory:")
    try:
        process_document_pipeline(
            pdf_path=str(STARTER_AR), provider=None, db=db, reasoning_mode="heuristic",
        )
        facts = db.get_facts(limit=500)
        revenue = [f for f in facts if f.predicate == "revenue"]
        assert len(revenue) == 2
        scopes = sorted(f.scope for f in revenue)
        assert scopes == ["consolidated", "standalone"]
        assert all(f.period == "FY24" for f in revenue)

        rels = db.get_relationships(limit=100)
        rev_pairs = [
            r for r in rels
            if r["fa_predicate"] == "revenue" and r["fb_predicate"] == "revenue"
        ]
        assert len(rev_pairs) == 1, "exactly one revenue comparison pair"
        pair = rev_pairs[0]
        assert pair["relationship"] == "RECONCILABLE", (
            f"standalone vs consolidated must be RECONCILABLE by scope, "
            f"got {pair['relationship']}: {pair['reason']}"
        )
        reason_l = pair["reason"].lower()
        assert "standalone" in reason_l
        assert "consolidated" in reason_l
        # The baseline UNCERTAIN period-ambiguity reason must be gone
        assert "reporting period ambiguous" not in reason_l
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Unit coverage of the capture and boundedness
# ---------------------------------------------------------------------------

def test_broad_pattern_captures_standalone_scope_and_period():
    page = _page(
        "y The revenue from operations on standalone basis for FY24 stood at "
        "₹ 74,540.82 million as against ₹66,586.61 million"
    )
    facts = extract_heuristic_facts_from_page(page)
    revenue = [f for f in facts if f.predicate == "revenue"]
    assert len(revenue) == 1
    f = revenue[0]
    assert f.value == 74540.82
    assert f.scope == "standalone"
    assert f.period == "FY24"
    assert f.unit == "INR million"


def test_broad_pattern_captures_consolidated_scope_and_period():
    page = _page(
        "The revenue from operations on consolidated basis for FY24 stood at "
        "₹ 81,415.38 million as against ₹72,253.01"
    )
    facts = extract_heuristic_facts_from_page(page)
    revenue = [f for f in facts if f.predicate == "revenue"]
    assert len(revenue) == 1
    f = revenue[0]
    assert f.value == 81415.38
    assert f.scope == "consolidated"
    assert f.period == "FY24"


def test_sentence_without_basis_keeps_scope_none():
    """Boundedness: no 'on X basis' in the sentence -> scope stays None;
    period is still captured when a fiscal year appears inside the match."""
    page = _page("The revenue from operations for FY23 stood at ₹ 500.00 million")
    facts = extract_heuristic_facts_from_page(page)
    revenue = [f for f in facts if f.predicate == "revenue"]
    assert len(revenue) == 1
    f = revenue[0]
    assert f.scope is None
    assert f.period == "FY23"


def test_sentence_without_fiscal_year_keeps_period_none():
    page = _page(
        "The revenue from operations on standalone basis stood at ₹ 999.00 million"
    )
    facts = extract_heuristic_facts_from_page(page)
    revenue = [f for f in facts if f.predicate == "revenue"]
    assert len(revenue) == 1
    assert revenue[0].scope == "standalone"
    assert revenue[0].period is None


def test_reasoner_scope_rule_reconciles_captured_pair():
    """Once scope is captured, the existing scope-difference rule must
    classify the pair RECONCILABLE with both bases named."""
    standalone = _revenue_fact("standalone", "FY24", 74540.82)
    consolidated = _revenue_fact("consolidated", "FY24", 81415.38)
    pairs = generate_candidate_pairs([standalone, consolidated])
    assert len(pairs) == 1
    comp = reason_comparison(pairs[0])
    assert comp.relationship == "RECONCILABLE"
    assert "standalone" in comp.reason.lower()
    assert "consolidated" in comp.reason.lower()
    assert comp.confidence >= 0.90


def test_same_scope_same_value_still_corroborates():
    """Capturing scope must not break exact corroboration for identical
    same-scope figures."""
    a = _revenue_fact("standalone", "FY24", 74540.82, page_number=22)
    b = _revenue_fact("standalone", "FY24", 74540.82, page_number=40)
    pairs = generate_candidate_pairs([a, b])
    assert len(pairs) == 1
    comp = reason_comparison(pairs[0])
    assert comp.relationship == "CORROBORATES"
