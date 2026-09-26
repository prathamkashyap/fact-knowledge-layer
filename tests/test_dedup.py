"""
Regression tests for duplicate fact grouping (Defect A) — Phase 3.

Defect A (documented in docs/validation/BASELINE.md): the clean starter run
extracted the same director-cessation sentence multiple times from the
Delhivery annual report excerpt — Suvir x3 (two tables on p.40 plus p.43),
Colleran x3, Barasia x2 — inflating the fact count from 8 unique claims to
13 rows and multiplying cross-page relationship pairs.

The named regression test below asserts the exact case: after dedupe_facts,
each director's cessation claim exists exactly once, with the suppressed
spans retained as provenance qualifiers.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models import Fact, Evidence  # noqa: E402
from app.normalizer import dedupe_facts, fact_fingerprint  # noqa: E402
from app.database import Database  # noqa: E402
from app.pipeline import process_document_pipeline  # noqa: E402

STARTER_AR = REPO_ROOT / "starter-datasets" / "delhivery" / "02-delhivery-annual-report-fy24-excerpt.pdf"


def _director_fact(subject: str, value: str, page: int, as_of: str, doc: str = "AR.pdf") -> Fact:
    return Fact(
        subject=subject,
        predicate="board status",
        value=value,
        as_of=as_of,
        evidence=Evidence(
            document_id="ar_fy24",
            page_number=page,
            text=f"Mr. {subject} {value} with effect from {as_of}",
            document_name=doc,
        ),
    )


# ---------------------------------------------------------------------------
# THE named regression test for Defect A
# ---------------------------------------------------------------------------

def test_suvir_colleran_barasia_duplicate_grouping():
    """Suvir x3, Colleran x3, Barasia x2 must collapse to exactly 3 facts,
    with suppressed spans retained as duplicate-evidence provenance."""
    facts = [
        # Barasia: two tables on p.40
        _director_fact("Sandeep Kumar Barasia", "ceased to be a Director", 40, "July 01, 2024"),
        _director_fact("Sandeep Kumar Barasia", "ceased to be a Director", 40, "July 01, 2024"),
        # Suvir: two tables on p.40 + repeat on p.43
        _director_fact("Suvir Suren Sujan", "ceased to be a Director", 40, "August 24, 2023"),
        _director_fact("Suvir Suren Sujan", "ceased to be a Director", 40, "August 24, 2023"),
        _director_fact("Suvir Suren Sujan", "ceased to be a Director", 43, "August 24, 2023"),
        # Colleran: two tables on p.40 + repeat on p.43
        _director_fact("Donald Francis Colleran", "ceased to be a Director", 40, "September 27, 2023"),
        _director_fact("Donald Francis Colleran", "ceased to be a Director", 40, "September 27, 2023"),
        _director_fact("Donald Francis Colleran", "ceased to be a Director", 43, "September 27, 2023"),
    ]
    assert len(facts) == 8

    canonical = dedupe_facts(facts)

    # Exactly one fact per director
    assert len(canonical) == 3
    by_subject = {f.subject: f for f in canonical}
    assert set(by_subject) == {
        "Sandeep Kumar Barasia",
        "Suvir Suren Sujan",
        "Donald Francis Colleran",
    }

    # Provenance retained — suppressed spans are not silently discarded
    suvir = by_subject["Suvir Suren Sujan"]
    suvir_prov = [q for q in suvir.qualifiers if q.startswith("duplicate-evidence:")]
    assert suvir_prov, "Suvir's canonical fact must retain provenance for suppressed spans"
    assert any("p.43" in q for q in suvir_prov)

    colleran = by_subject["Donald Francis Colleran"]
    assert any(q.startswith("duplicate-evidence:") for q in colleran.qualifiers)
    assert any("p.43" in q for q in colleran.qualifiers)

    barasia = by_subject["Sandeep Kumar Barasia"]
    assert any(q.startswith("duplicate-evidence:") for q in barasia.qualifiers)

    # Canonical facts keep their own (first) evidence, not a merged blob
    assert suvir.evidence.page_number == 40
    assert suvir.as_of == "August 24, 2023"


def test_distinct_claims_with_same_subject_are_not_grouped():
    """Resigned vs ceased (different value), and different effective dates,
    must remain distinct facts — that is what Phase 4 reasoning needs."""
    resigned = _director_fact("Suvir Suren Sujan", "resigned from the Board", 33, "August 24, 2023")
    ceased = _director_fact("Suvir Suren Sujan", "ceased to be a Director", 40, "August 24, 2023")
    other_date = _director_fact("Donald Francis Colleran", "ceased to be a Director", 33, "conclusion of the 12th AGM")
    sept_date = _director_fact("Donald Francis Colleran", "ceased to be a Director", 40, "September 27, 2023")

    out = dedupe_facts([resigned, ceased, other_date, sept_date])
    assert len(out) == 4
    assert fact_fingerprint(resigned) != fact_fingerprint(ceased)
    assert fact_fingerprint(other_date) != fact_fingerprint(sept_date)


def test_same_claim_across_documents_is_not_grouped():
    """Document is part of the fingerprint: repetition in two sources is a
    genuine cross-source pair, not a duplicate."""
    a = _director_fact("X", "ceased to be a Director", 5, "July 01, 2024", doc="AR.pdf")
    b = _director_fact("X", "ceased to be a Director", 9, "July 01, 2024", doc="Prospectus.pdf")
    out = dedupe_facts([a, b])
    assert len(out) == 2


def test_same_value_different_scope_is_not_grouped():
    """Scope participates in the fingerprint so standalone/consolidated
    claims with an identical number never collapse into one."""
    standalone = Fact(
        subject="delhivery revenue", predicate="revenue", value=100.0,
        unit="INR million", period="FY24", scope="standalone",
        evidence=Evidence(document_id="d", page_number=22, text="standalone 100"),
    )
    consolidated = Fact(
        subject="delhivery revenue", predicate="revenue", value=100.0,
        unit="INR million", period="FY24", scope="consolidated",
        evidence=Evidence(document_id="d", page_number=22, text="consolidated 100"),
    )
    out = dedupe_facts([standalone, consolidated])
    assert len(out) == 2


def test_provenance_qualifiers_are_free_of_reasoner_trigger_keywords():
    """duplicate-evidence provenance must not contain keywords that would
    flip reasoner rules (vintage reconciliation, forecast reconciliation)."""
    banned = (
        "advance", "provisional", "revised", "preliminary", "estimated",
        "final", "first estimate", "second estimate", "forecast", "project",
    )
    facts = [
        _director_fact("Suvir Suren Sujan", "ceased to be a Director", 40, "August 24, 2023"),
        _director_fact("Suvir Suren Sujan", "ceased to be a Director", 43, "August 24, 2023"),
        _director_fact("Suvir Suren Sujan", "ceased to be a Director", 44, "August 24, 2023"),
    ]
    out = dedupe_facts(facts)
    assert len(out) == 1
    for q in out[0].qualifiers:
        ql = q.lower()
        for kw in banned:
            assert kw not in ql, f"provenance qualifier {q!r} contains trigger keyword {kw!r}"


def test_dedupe_is_idempotent_on_already_unique_facts():
    facts = [
        _director_fact("A", "ceased to be a Director", 1, "July 01, 2024"),
        _director_fact("B", "ceased to be a Director", 1, "August 24, 2023"),
        _director_fact("C", "resigned from the Board", 2, "September 27, 2023"),
    ]
    once = dedupe_facts(facts)
    twice = dedupe_facts(once)
    assert len(once) == 3
    assert [f.id for f in once] == [f.id for f in twice]


# ---------------------------------------------------------------------------
# End-to-end: real PDF through the full pipeline
# ---------------------------------------------------------------------------

def test_delhivery_annual_report_deduplicates_director_facts(tmp_path):
    """Run the actual pipeline on the Delhivery annual report excerpt and
    assert the Defect A shape is gone: exactly one fact each for Suvir,
    Colleran and Barasia cessation claims, revenue pair intact."""
    db = Database(":memory:")
    try:
        res = process_document_pipeline(
            pdf_path=str(STARTER_AR),
            provider=None,
            db=db,
            reasoning_mode="heuristic",
        )
        assert res["facts_count"] > 0

        facts = db.get_facts(limit=500)
        # 11 raw rows pre-fix -> 6 unique claims post-fix
        # (revenue x2, Suvir resigned, Suvir ceased, Colleran ceased, Barasia
        # ceased); Week 4 adds 6 comparative-highlights claims
        # (2 revenue FY23 + 4 loss) for 12 total.
        assert len(facts) == 12

        ceased = [f for f in facts if f.predicate == "board status"
                  and f.value.strip().lower() == "ceased to be a director"]
        assert len(ceased) == 3
        by_subject = {f.subject: f for f in ceased}
        assert by_subject["Suvir Suren Sujan"].evidence.page_number == 40
        assert any("duplicate-evidence:" in q for q in by_subject["Suvir Suren Sujan"].qualifiers)
        assert by_subject["Donald Francis Colleran"].evidence.page_number == 40
        assert by_subject["Sandeep Kumar Barasia"].evidence.page_number == 40

        # Revenue facts remain distinct (different values): FY24 standalone +
        # FY24 consolidated + the two Week 4 FY23 comparatives.
        revenue = [f for f in facts if f.predicate == "revenue"]
        assert len(revenue) == 4

        # Suvir resigned <-> Suvir ceased: exactly one comparison row now
        # (pre-fix: 3 rows from 1 x 3 cross product). Label is asserted in
        # the Phase 4 reasoning test, not here.
        rels = db.get_relationships(limit=100)
        suvir_pairs = [
            r for r in rels
            if r["fa_subject"] == "Suvir Suren Sujan" and r["fb_subject"] == "Suvir Suren Sujan"
        ]
        assert len(suvir_pairs) == 1

        # No same-claim self-pairs survive (pre-fix CORROBORATES artifacts)
        self_pairs = [
            r for r in rels
            if r["fa_subject"] == r["fb_subject"]
            and r["fa_value"] == r["fb_value"]
        ]
        assert self_pairs == []
    finally:
        db.close()
