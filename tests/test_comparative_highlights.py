"""Week 4: p.22 comparative financial-highlights cluster (Clusters A+B).

Named regression tests for the six source-verified claims in the two p.22
sentence shapes (ex-009, ex-010, ex-011 and the three induced loss truths
ex-025..ex-027), negative-grammar guards for the sentences the anchors must
NOT fire on (p.36 "to ₹X from ₹Y", per-cent "as against" prose, the
prospectus loss wording, Q4 Rs./Cr KPI lines), a corpus-wide blast-radius
test proving exactly those six claims and nothing else come out of the
family, and reasoner guards proving the 11 induced pairs classify
RECONCILABLE via the scope rule (6) or the cross-period rule (8b) with no
revenue-to-loss cross-pairing.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.pdf_parser import PageObject, ScoreBreakdown, parse_pdf_document
from app.pipeline import extract_heuristic_facts_from_page, verify_evidence_on_page
from app.matcher import generate_candidate_pairs
from app.reasoner import reason_comparison

AR_FILENAME = "02-delhivery-annual-report-fy24-excerpt.pdf"

P22_HIGHLIGHTS = (
    "y The revenue from operations on standalone basis for FY24 stood at "
    "₹ 74,540.82 million as against ₹66,586.61 million for FY23, registering "
    "a growth of 11.95%. Whereas the loss for FY24 stood at ₹ 1,679.68 million "
    "as against ₹8,123.02 million for FY23, a reduction of loss by 79.32%. "
    "y The revenue from operations on consolidated basis for FY24 stood at "
    "₹ 81,415.38 million as against ₹72,253.01 million for FY23, registering "
    "a growth of 12.68%. Whereas the loss for FY24 stood at ₹ 2,491.86 million "
    "as against ₹10,077.79 million for FY23, a reduction of loss by 75.27%."
)


def _page(raw_text: str, filename: str = AR_FILENAME, page_number: int = 22) -> PageObject:
    return PageObject(
        document_id="comp_test",
        filename=filename,
        page_number=page_number,
        raw_text=raw_text,
        text_length=len(raw_text),
        blocks_count=1,
        has_tables=False,
        score_breakdown=ScoreBreakdown(total_score=10.0, is_candidate=True),
    )


def _fact(facts, predicate: str, value: float, period: str):
    matched = [
        f for f in facts
        if f.predicate == predicate
        and abs(float(f.value) - value) < 1e-9
        and (f.period or "").upper() == period.upper()
    ]
    assert len(matched) == 1, (
        f"expected exactly one {predicate} {value} {period}, got {len(matched)}"
    )
    return matched[0]


# ---------------------------------------------------------------------------
# Named regression tests: one per gold claim, against its real source text
# ---------------------------------------------------------------------------

def test_ex_009_revenue_comparative_standalone_fy23():
    facts = extract_heuristic_facts_from_page(_page(P22_HIGHLIGHTS))
    f = _fact(facts, "revenue", 66586.61, "FY23")
    assert f.subject == "delhivery revenue"
    assert f.unit == "INR million"
    assert f.scope == "standalone"
    assert f.as_of is None


def test_ex_010_revenue_comparative_consolidated_fy23():
    facts = extract_heuristic_facts_from_page(_page(P22_HIGHLIGHTS))
    f = _fact(facts, "revenue", 72253.01, "FY23")
    assert f.subject == "delhivery revenue"
    assert f.unit == "INR million"
    assert f.scope == "consolidated"


def test_ex_011_consolidated_loss_fy24():
    facts = extract_heuristic_facts_from_page(_page(P22_HIGHLIGHTS))
    f = _fact(facts, "loss", 2491.86, "FY24")
    assert f.subject == "delhivery loss"
    assert f.unit == "INR million"
    assert f.scope == "consolidated", "scope must be inherited from the revenue bullet"


def test_ex_025_standalone_loss_fy24():
    facts = extract_heuristic_facts_from_page(_page(P22_HIGHLIGHTS))
    f = _fact(facts, "loss", 1679.68, "FY24")
    assert f.subject == "delhivery loss"
    assert f.scope == "standalone"


def test_ex_026_standalone_loss_comparative_fy23():
    facts = extract_heuristic_facts_from_page(_page(P22_HIGHLIGHTS))
    f = _fact(facts, "loss", 8123.02, "FY23")
    assert f.subject == "delhivery loss"
    assert f.scope == "standalone"


def test_ex_027_consolidated_loss_comparative_fy23():
    facts = extract_heuristic_facts_from_page(_page(P22_HIGHLIGHTS))
    f = _fact(facts, "loss", 10077.79, "FY23")
    assert f.subject == "delhivery loss"
    assert f.scope == "consolidated"


def test_p22_family_emits_expected_claims_no_duplicates():
    """The family adds exactly the six gold claims; the two FY24 revenue
    figures appear once (broad pattern only — no double emission)."""
    facts = extract_heuristic_facts_from_page(_page(P22_HIGHLIGHTS))
    family = [
        (f.predicate, round(float(f.value), 2), f.period)
        for f in facts if f.predicate in ("revenue", "loss")
    ]
    assert sorted(family) == [
        ("loss", 1679.68, "FY24"),
        ("loss", 2491.86, "FY24"),
        ("loss", 8123.02, "FY23"),
        ("loss", 10077.79, "FY23"),
        ("revenue", 66586.61, "FY23"),
        ("revenue", 72253.01, "FY23"),
        ("revenue", 74540.82, "FY24"),
        ("revenue", 81415.38, "FY24"),
    ]


def test_family_evidence_verifies_on_page():
    facts = extract_heuristic_facts_from_page(_page(P22_HIGHLIGHTS))
    for f in facts:
        if f.predicate in ("revenue", "loss"):
            assert verify_evidence_on_page(f.evidence.text, P22_HIGHLIGHTS), (
                f"evidence must appear verbatim on the cited page: {f.evidence.text!r}"
            )


# ---------------------------------------------------------------------------
# Negative guards: the anchors must not fire elsewhere
# ---------------------------------------------------------------------------

def test_negative_p36_to_from_clauses():
    """p.36 performance-highlights use 'to ₹X million for FYyy from ₹Y' — a
    different shape with the subject outside the clause. No family emission."""
    facts = extract_heuristic_facts_from_page(_page(
        "Revenues from customers increased by 12.68% to ₹81,415.38 million for "
        "FY24 from ₹72,253.01 million for FY23. Loss for the year decreased to "
        "₹2,491.86 million for FY24 from ₹10,077.79 million for FY23.",
        filename=AR_FILENAME, page_number=36,
    ))
    assert [f for f in facts if f.predicate == "loss"] == []
    assert [f for f in facts if f.predicate == "revenue" and f.period == "FY23"] == []


def test_negative_as_against_percent_prose():
    """Comparative prose with per-cent/lakh units must not hit the ₹-million
    comparative anchor."""
    facts = extract_heuristic_facts_from_page(_page(
        "India's merchandise exports grew marginally by 0.1 per cent in 2024-25 "
        "as against a contraction of 3.1 per cent a year ago. Mutual funds made "
        "net purchases of ₹4.7 lakh crore in 2024-25 as against ₹2.0 lakh crore "
        "in the previous year.",
        filename="02-rbi-annual-report-2024-25-excerpt.pdf", page_number=11,
    ))
    assert [f for f in facts if f.predicate in ("revenue", "loss")] == []


def test_negative_prospectus_loss_wording():
    facts = extract_heuristic_facts_from_page(_page(
        "our restated loss for the year has improved from ₹17,833.04 million "
        "in Fiscal 2019 to ₹2,689.26 million in Fiscal 2020.",
        filename="01-delhivery-prospectus-2022-excerpt.pdf", page_number=55,
    ))
    assert [f for f in facts if f.predicate == "loss"] == []


def test_negative_q4_rs_cr_kpi_lines():
    """Q4 deck Rs./Cr sentences are a different unit and grammar (Cluster H,
    not implemented here)."""
    facts = extract_heuristic_facts_from_page(_page(
        "FY24 EBITDA increased by Rs. 578 Cr to Rs. 127 Cr from Rs. (452 Cr) "
        "in FY23 PAT loss reduced by Rs. 759 Cr from Rs. (1,008 Cr) in FY23",
        filename="03-delhivery-q4-fy24-earnings-presentation.pdf", page_number=5,
    ))
    assert [f for f in facts if f.predicate in ("revenue", "loss")] == []


def test_scope_fallback_when_no_basis_phrase():
    """A highlights sentence with no preceding basis phrase yields scope=None
    (documented fallback) instead of inventing a scope."""
    facts = extract_heuristic_facts_from_page(_page(
        "Whereas the loss for FY24 stood at ₹ 2,491.86 million as against "
        "₹10,077.79 million for FY23.",
        page_number=22,
    ))
    f = _fact(facts, "loss", 2491.86, "FY24")
    assert f.scope is None


# ---------------------------------------------------------------------------
# Corpus-wide blast radius: exactly the six family claims, nowhere else
# ---------------------------------------------------------------------------

def test_blast_radius_corpus_wide():
    expected = {
        ("revenue", 66586.61, "FY23", "standalone"),
        ("revenue", 72253.01, "FY23", "consolidated"),
        ("loss", 1679.68, "FY24", "standalone"),
        ("loss", 2491.86, "FY24", "consolidated"),
        ("loss", 8123.02, "FY23", "standalone"),
        ("loss", 10077.79, "FY23", "consolidated"),
    }
    seen = set()
    for path in sorted((REPO_ROOT / "starter-datasets").rglob("*.pdf")):
        doc = parse_pdf_document(str(path))
        for page in doc.pages:
            for f in extract_heuristic_facts_from_page(page):
                if f.predicate == "loss":
                    seen.add(("loss", round(float(f.value), 2),
                              (f.period or "").upper(), f.scope))
                elif f.predicate == "revenue" and (f.period or "").upper() == "FY23":
                    seen.add(("revenue", round(float(f.value), 2),
                              (f.period or "").upper(), f.scope))
    assert seen == expected


# ---------------------------------------------------------------------------
# Reasoner guards: the 11 induced pairs are RECONCILABLE, no cross-group pairs
# ---------------------------------------------------------------------------

def _reasoned_pairs(facts):
    prepared = generate_candidate_pairs(facts)
    out = []
    for p in prepared:
        cmp_ = reason_comparison(p)
        out.append((p, cmp_))
    return out


def test_pairing_and_labels_on_highlights_block():
    facts = extract_heuristic_facts_from_page(_page(P22_HIGHLIGHTS))
    pairs = _reasoned_pairs(facts)
    # revenue group C(4,2)=6 + loss group C(4,2)=6; no revenue-to-loss pairs
    # (different canonical subject/predicate groups).
    assert len(pairs) == 12
    for prepared, cmp_ in pairs:
        assert cmp_.relationship == "RECONCILABLE", (
            f"{prepared.fact_a.subject} {prepared.fact_a.value} x "
            f"{prepared.fact_b.subject} {prepared.fact_b.value} -> "
            f"{cmp_.relationship} ({cmp_.reason})"
        )
    scope_rule = [c for _, c in pairs
                  if "standalone" in c.reason and "consolidated" in c.reason]
    period_rule = [c for _, c in pairs if "reporting periods" in c.reason]
    assert len(scope_rule) == 8, "4 revenue + 4 loss cross-scope pairs"
    assert len(period_rule) == 4, "2 revenue + 2 loss same-scope YoY pairs"


def test_existing_scope_pair_rel005_path_unchanged():
    """The baseline 74,540.82 x 81,415.38 pair must keep classifying via the
    scope rule after the group grows to four members."""
    facts = extract_heuristic_facts_from_page(_page(P22_HIGHLIGHTS))
    pairs = _reasoned_pairs(facts)
    hit = [
        c for p, c in pairs
        if {round(float(p.fact_a.value), 2), round(float(p.fact_b.value), 2)}
        == {74540.82, 81415.38}
    ]
    assert len(hit) == 1
    assert hit[0].relationship == "RECONCILABLE"
    assert "standalone" in hit[0].reason and "consolidated" in hit[0].reason
