"""Week 5: director appointment extraction (ex-014, ex-015).

Named regression tests for the two source-verified appointment claims on the
Delhivery annual report (p.33 note 2/note 3 and p.40 note 1/note 3), negative
guards proving the sentence anchors do NOT fire on the officer-appointment,
committee-membership, Monitoring-Agency, remuneration-revision, or
non-independent-director shapes in the same corpus, a corpus-wide blast-radius
test proving exactly those two claims and nothing else come out of the family,
a dedup guard proving the p.40 "5 year term" repeats fold into the p.33
canonical fact, and matcher guards proving the two singleton claims induce no
candidate pairs (relationship gold stays at 30, no new rows).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.pdf_parser import PageObject, ScoreBreakdown, parse_pdf_document
from app.pipeline import extract_heuristic_facts_from_page, verify_evidence_on_page
from app.normalizer import dedupe_facts
from app.matcher import generate_candidate_pairs

AR_FILENAME = "02-delhivery-annual-report-fy24-excerpt.pdf"
AR_PATH = REPO_ROOT / "starter-datasets" / "delhivery" / AR_FILENAME

APPOINTMENT_VALUE = "appointed as Non-Executive Independent Director"

P33_APPOINTMENTS = (
    "Ms. Aruna Sundararajan was appointed as Non-Executive Independent Director "
    "with effect from July 08, 2022. Mr. Anindya Ghose was appointed as "
    "Non-Executive Independent Director with effect from August 04, 2023."
)

P40_APPOINTMENT = (
    "Mr. Anindya Ghose was appointed as Non-Executive Independent Director for "
    "a period of 5 years with effect from August 04, 2023."
)


def _page(raw_text: str, filename: str = AR_FILENAME, page_number: int = 33) -> PageObject:
    return PageObject(
        document_id="appt_test",
        filename=filename,
        page_number=page_number,
        raw_text=raw_text,
        text_length=len(raw_text),
        blocks_count=1,
        has_tables=False,
        score_breakdown=ScoreBreakdown(total_score=10.0, is_candidate=True),
    )


def _ar_page(page_number: int) -> PageObject:
    doc = parse_pdf_document(str(AR_PATH))
    return next(p for p in doc.pages if p.page_number == page_number)


def _appointments(facts):
    return [f for f in facts if f.value == APPOINTMENT_VALUE]


def _one(facts, subject: str):
    matched = [f for f in _appointments(facts) if f.subject == subject]
    assert len(matched) == 1, f"expected exactly one appointment for {subject}, got {len(matched)}"
    return matched[0]


# ---------------------------------------------------------------------------
# Named regression tests: one per gold claim, against its real source text
# ---------------------------------------------------------------------------

def test_ex_015_sundararajan_appointed_p33():
    facts = extract_heuristic_facts_from_page(_page(P33_APPOINTMENTS, page_number=33))
    f = _one(facts, "Aruna Sundararajan")
    assert f.predicate == "board status"
    assert f.value == APPOINTMENT_VALUE
    assert f.unit is None
    assert f.as_of == "July 08, 2022"
    assert f.qualifiers == [], "no term stated in the p.33 note-2 sentence"


def test_ex_014_ghose_appointed_p33():
    facts = extract_heuristic_facts_from_page(_page(P33_APPOINTMENTS, page_number=33))
    f = _one(facts, "Anindya Ghose")
    assert f.predicate == "board status"
    assert f.value == APPOINTMENT_VALUE
    assert f.as_of == "August 04, 2023"
    assert f.qualifiers == [], "p.33 states no term length"


def test_ex_014_ghose_appointed_p40_with_term():
    facts = extract_heuristic_facts_from_page(_page(P40_APPOINTMENT, page_number=40))
    f = _one(facts, "Anindya Ghose")
    assert f.predicate == "board status"
    assert f.value == APPOINTMENT_VALUE
    assert f.as_of == "August 04, 2023"
    assert f.qualifiers == ["5 year term"]


def test_real_p33_page_emits_both_gold_claims():
    page = _ar_page(33)
    facts = _appointments(extract_heuristic_facts_from_page(page))
    assert {(f.subject, f.as_of) for f in facts} == {
        ("Aruna Sundararajan", "July 08, 2022"),
        ("Anindya Ghose", "August 04, 2023"),
    }
    for f in facts:
        assert f.evidence.page_number == 33
        assert verify_evidence_on_page(f.evidence.text, page.raw_text), (
            f"evidence must appear verbatim on p.33: {f.evidence.text!r}"
        )


def test_real_p40_page_emits_term_qualified_ghose():
    page = _ar_page(40)
    facts = _appointments(extract_heuristic_facts_from_page(page))
    assert len(facts) == 2, "p.40 note 1 and note 3 both carry the sentence"
    for f in facts:
        assert f.subject == "Anindya Ghose"
        assert f.as_of == "August 04, 2023"
        assert f.qualifiers == ["5 year term"]
        assert verify_evidence_on_page(f.evidence.text, page.raw_text), (
            f"evidence must appear verbatim on p.40: {f.evidence.text!r}"
        )


def test_p33_p40_fold_to_two_canonical_claims():
    """Across both pages the family yields exactly the two gold claims after
    dedup: Ghose's p.40 term-qualified repeats fold into the p.33 canonical
    fact (fingerprint excludes qualifiers, keeps first evidence)."""
    facts = []
    for pn in (33, 40):
        facts.extend(extract_heuristic_facts_from_page(_ar_page(pn)))
    deduped = dedupe_facts(facts)
    appts = _appointments(deduped)
    assert len(appts) == 2
    ghose = _one(deduped, "Anindya Ghose")
    assert ghose.evidence.page_number == 33, "canonical fact must be the first (p.33) emission"
    assert "5 year term" not in ghose.qualifiers, (
        "folded repeats must not leak their term into the canonical fact"
    )
    assert any(q.startswith("duplicate-evidence: p.40") for q in ghose.qualifiers), (
        "dedup must record the folded p.40 repeats"
    )
    sunda = _one(deduped, "Aruna Sundararajan")
    assert sunda.evidence.page_number == 33


# ---------------------------------------------------------------------------
# Negative guards: the anchors must not fire elsewhere
# ---------------------------------------------------------------------------

def test_negative_officer_appointment_p33():
    """Vivek Kumar's Company Secretary appointment carries 'was appointed as'
    and 'with effect from <Month DD, YYYY>' but not the independent-director
    role slot."""
    facts = extract_heuristic_facts_from_page(_page(
        "Mr. Vivek Kumar, who was already associated with the Company as "
        "Deputy Company Secretary, was appointed as the Company Secretary and "
        "Compliance Officer with effect from June 01, 2023 and ceased to be "
        "associated with the Company with effect from March 27, 2024.",
        page_number=33,
    ))
    assert _appointments(facts) == []


def test_negative_officer_appointment_p41():
    facts = extract_heuristic_facts_from_page(_page(
        "Ms. Madhulika Rawat has been appointed as Company Secretary & "
        "Compliance Officer with effect from May 17, 2024.",
        filename=AR_FILENAME, page_number=41,
    ))
    assert _appointments(facts) == []


def test_negative_committee_membership_same_person_p44():
    """The strongest negative: the SAME person (Anindya Ghose) appointed to a
    committee with the same date grammar must not produce an appointment
    claim — the role slot is what bounds the family."""
    facts = extract_heuristic_facts_from_page(_page(
        "Mr. Anindya Ghose has been appointed as member of the Committee "
        "with effect from November 04, 2023.",
        filename=AR_FILENAME, page_number=44,
    ))
    assert _appointments(facts) == []


def test_negative_monitoring_agency_p23():
    facts = extract_heuristic_facts_from_page(_page(
        "Axis Bank Limited was appointed as the Monitoring Agency",
        filename=AR_FILENAME, page_number=23,
    ))
    assert _appointments(facts) == []


def test_negative_remuneration_revision_p33():
    facts = extract_heuristic_facts_from_page(_page(
        "The remuneration of Mr. Saugata Gupta was revised with effect from "
        "April 01, 2023, pursuant to the approval of shareholders in the "
        "meeting.",
        filename=AR_FILENAME, page_number=33,
    ))
    assert _appointments(facts) == []


def test_negative_non_independent_non_executive_director():
    """Scope bound: only the Independent-Director role is in the family, so a
    plain Non-Executive Director appointment must not fire."""
    facts = extract_heuristic_facts_from_page(_page(
        "Mr. Rahul Mehta was appointed as Non-Executive Director with effect "
        "from January 01, 2024.",
        page_number=40,
    ))
    assert _appointments(facts) == []


def test_negative_role_without_date_slot():
    facts = extract_heuristic_facts_from_page(_page(
        "The Independent Directors of the Company have been appointed in "
        "accordance with the provisions of the Companies Act, 2013.",
        filename=AR_FILENAME, page_number=41,
    ))
    assert _appointments(facts) == []


def test_negative_single_word_subject_guard():
    facts = extract_heuristic_facts_from_page(_page(
        "Board was appointed as Non-Executive Independent Director with "
        "effect from January 01, 2024.",
        page_number=40,
    ))
    assert _appointments(facts) == []


def test_negative_trailing_word_subject_guard():
    """Names ending in a function word (invalid_name_trailing) are rejected
    even when the role and date slots both match."""
    facts = extract_heuristic_facts_from_page(_page(
        "Board of was appointed as Non-Executive Independent Director with "
        "effect from January 01, 2024.",
        page_number=40,
    ))
    assert _appointments(facts) == []


# ---------------------------------------------------------------------------
# Corpus-wide blast radius: exactly the two family claims, nowhere else
# ---------------------------------------------------------------------------

def test_blast_radius_corpus_wide():
    expected = {
        ("Aruna Sundararajan", "July 08, 2022"),
        ("Anindya Ghose", "August 04, 2023"),
    }
    seen = []
    for path in sorted((REPO_ROOT / "starter-datasets").rglob("*.pdf")):
        doc = parse_pdf_document(str(path))
        for page in doc.pages:
            for f in extract_heuristic_facts_from_page(page):
                if f.value == APPOINTMENT_VALUE:
                    seen.append((f.subject, f.as_of))
    assert len(seen) == 4, (
        "corpus-wide raw emissions must be exactly the 4 source sentences "
        f"(p.33 x2, p.40 x2), got {len(seen)}: {seen}"
    )
    assert set(seen) == expected


# ---------------------------------------------------------------------------
# Matcher guards: two singleton subjects -> no candidate pairs, so relationship
# gold (30 rows) and its absent keys are untouched
# ---------------------------------------------------------------------------

def test_appointments_induce_no_pairs_after_dedup():
    facts = []
    for pn in (33, 40):
        facts.extend(extract_heuristic_facts_from_page(_ar_page(pn)))
    deduped = dedupe_facts(facts)
    appts = _appointments(deduped)
    assert generate_candidate_pairs(appts) == []


def test_no_pairs_involving_appointment_subjects_on_ar():
    """The whole annual report after dedup must not pair Anindya Ghose or
    Aruna Sundararajan with anything (their subjects are singleton groups)."""
    from app.pipeline import extract_facts_from_document

    raw = extract_facts_from_document(None, str(AR_PATH))
    deduped = dedupe_facts(raw)
    pairs = generate_candidate_pairs(deduped)
    appointment_subjects = {"Anindya Ghose", "Aruna Sundararajan"}
    offending = [
        p for p in pairs
        if p.fact_a.subject in appointment_subjects or p.fact_b.subject in appointment_subjects
    ]
    assert offending == [], (
        "appointment claims must not induce relationships: "
        + "; ".join(
            f"{p.fact_a.subject} x {p.fact_b.subject}" for p in offending
        )
    )
