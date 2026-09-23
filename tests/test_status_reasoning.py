"""
Regression tests for the bounded status-progression reasoning rule
(Defect B) — Week 2 Phase 4.

Defect B (documented in docs/validation/BASELINE.md): the clean starter run
classified 'Suvir Suren Sujan resigned from the Board' (p.33) vs 'Suvir Suren
Sujan ceased to be a Director' (p.40/p.43) as CONTRADICTS, even though both
sentences carry the SAME effective date (August 24, 2023). Root cause: the
temporal reconciliation rule only fired when effective dates DIFFERED, so
same-date resignation -> cessation fell through to the generic contradiction
check.

The named regression test below asserts the exact case, end to end through
the real PDF, resolves to RECONCILABLE.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models import Fact, Evidence  # noqa: E402
from app.matcher import generate_candidate_pairs  # noqa: E402
from app.reasoner import (  # noqa: E402
    reason_comparison,
    is_status_progression_value,
    STATUS_PROGRESSION_PREDICATES,
)
from app.database import Database  # noqa: E402
from app.pipeline import process_document_pipeline  # noqa: E402

STARTER_AR = REPO_ROOT / "starter-datasets" / "delhivery" / "02-delhivery-annual-report-fy24-excerpt.pdf"


def _status_fact(value: str, as_of: str, page: int, predicate: str = "board status",
                 subject: str = "Suvir Suren Sujan") -> Fact:
    return Fact(
        subject=subject,
        predicate=predicate,
        value=value,
        as_of=as_of,
        evidence=Evidence(
            document_id="ar_fy24",
            page_number=page,
            text=f"Mr. {subject}, {value} with effect from {as_of}",
            document_name="AR.pdf",
        ),
    )


# ---------------------------------------------------------------------------
# THE named regression test for Defect B — end to end on the real PDF
# ---------------------------------------------------------------------------

def test_resigned_vs_ceased_same_date_is_reconcilable_end_to_end():
    """The real Delhivery annual report pair must classify RECONCILABLE,
    not CONTRADICTS (same effective date August 24, 2023)."""
    db = Database(":memory:")
    try:
        process_document_pipeline(
            pdf_path=str(STARTER_AR), provider=None, db=db, reasoning_mode="heuristic",
        )
        rels = db.get_relationships(limit=100)
        suvir_pairs = [
            r for r in rels
            if r["fa_subject"] == "Suvir Suren Sujan" and r["fb_subject"] == "Suvir Suren Sujan"
        ]
        assert len(suvir_pairs) == 1, "exactly one Suvir comparison after dedup"
        pair = suvir_pairs[0]
        assert pair["relationship"] == "RECONCILABLE", (
            f"resigned-vs-ceased with identical effective dates must be RECONCILABLE, "
            f"got {pair['relationship']}: {pair['reason']}"
        )
        assert "CONTRADICTS" != pair["relationship"]
        reason_l = pair["reason"].lower()
        assert "temporal progression" in reason_l or "effective date" in reason_l
        # No CONTRADICTS rows should mention this director pair anymore
        contradict_suvir = [
            r for r in rels
            if r["relationship"] == "CONTRADICTS"
            and "Suvir" in (r["fa_subject"] or "") + (r["fb_subject"] or "")
        ]
        assert contradict_suvir == []
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Unit-level coverage of the bounded rule
# ---------------------------------------------------------------------------

def test_same_effective_date_resigned_ceased_reconcilable():
    resigned = _status_fact("resigned from the Board", "August 24, 2023", 33)
    ceased = _status_fact("ceased to be a Director", "August 24, 2023", 40)
    pairs = generate_candidate_pairs([resigned, ceased])
    assert len(pairs) == 1
    comp = reason_comparison(pairs[0])
    assert comp.relationship == "RECONCILABLE"
    assert "August 24, 2023" in comp.reason
    assert comp.confidence >= 0.90


def test_differing_effective_dates_still_reconcilable_via_rule_4():
    """Rule 4 (dates differ) must keep working alongside the new rule 4b."""
    resigned = _status_fact("resigned from the Board", "August 24, 2023", 33)
    ceased = _status_fact("ceased to be a Director", "September 27, 2023", 40)
    pairs = generate_candidate_pairs([resigned, ceased])
    assert len(pairs) == 1
    comp = reason_comparison(pairs[0])
    assert comp.relationship == "RECONCILABLE"


def test_appointed_vs_ceased_same_date_reconcilable():
    appointed = _status_fact("appointed as Non-Executive Independent Director", "August 04, 2023", 33,
                             subject="Anindya Ghose")
    ceased = _status_fact("ceased to be a Director", "August 04, 2023", 40,
                          subject="Anindya Ghose")
    pairs = generate_candidate_pairs([appointed, ceased])
    comp = reason_comparison(pairs[0])
    assert comp.relationship == "RECONCILABLE"


def test_numeric_contradiction_not_affected_by_status_rule():
    """Genuine numeric contradictions must still classify CONTRADICTS —
    the status rule is categorical-only."""
    f1 = Fact(subject="India real GDP growth", predicate="growth rate", value=6.5,
              unit="per cent", period="2024-25", scope="actual",
              evidence=Evidence(document_id="d1", page_number=1, text="GDP 6.5%"))
    f2 = Fact(subject="India real GDP growth", predicate="growth rate", value=7.8,
              unit="per cent", period="2024-25", scope="actual",
              evidence=Evidence(document_id="d2", page_number=1, text="GDP 7.8%"))
    pairs = generate_candidate_pairs([f1, f2])
    comp = reason_comparison(pairs[0])
    assert comp.relationship == "CONTRADICTS"


def test_non_status_values_under_status_predicate_not_reconciled():
    """Boundedness: board_role predicate but values outside the transition
    vocabulary must NOT trigger the progression rule."""
    f1 = _status_fact("candidate member", "August 24, 2023", 1)
    f2 = _status_fact("observer member", "August 24, 2023", 2)
    pairs = generate_candidate_pairs([f1, f2])
    comp = reason_comparison(pairs[0])
    assert comp.relationship != "RECONCILABLE"


def test_status_values_under_non_status_predicate_not_reconciled():
    """Boundedness: transition-looking values under a non-status canonical
    predicate must NOT trigger the progression rule."""
    f1 = Fact(subject="Delhivery", predicate="revenue basis", value="resigned basis",
              period="FY24", evidence=Evidence(document_id="d1", page_number=1, text="a"))
    f2 = Fact(subject="Delhivery", predicate="revenue basis", value="ceased basis",
              period="FY24", evidence=Evidence(document_id="d2", page_number=1, text="b"))
    pairs = generate_candidate_pairs([f1, f2])
    comp = reason_comparison(pairs[0])
    assert comp.relationship != "RECONCILABLE"


def test_status_progression_vocabulary_helpers():
    assert is_status_progression_value("resigned from the Board") is True
    assert is_status_progression_value("ceased to be a Director") is True
    assert is_status_progression_value("appointed as Director") is True
    assert is_status_progression_value("ceased to be associated with the Company") is True
    assert is_status_progression_value("candidate member") is False
    assert is_status_progression_value(74540.82) is False
    assert is_status_progression_value(None) is False
    assert is_status_progression_value("") is False
    assert "board_role" in STATUS_PROGRESSION_PREDICATES
    assert "associated_status" in STATUS_PROGRESSION_PREDICATES
    assert "revenue" not in STATUS_PROGRESSION_PREDICATES
