"""
Regression tests for evidence validation (Week 2 Phase 6).

After normalization and dedup, every fact's evidence string must appear
verbatim (whitespace-insensitive) on the raw text of its cited page.
Facts citing a page that does not exist, or whose evidence text is absent
from the cited page, are rejected before persistence — a page-offset or
evidence-assembly tripwire for the heuristic path today and for any future
provider path.

The named end-to-end test runs the real annual report through the pipeline
and independently re-verifies every surviving fact against the parsed PDF.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models import Fact, Evidence  # noqa: E402
from app.pipeline import (  # noqa: E402
    normalize_evidence_text,
    verify_evidence_on_page,
    validate_fact_evidence,
    process_document_pipeline,
)
from app.database import Database  # noqa: E402
from app.pdf_parser import parse_pdf_document  # noqa: E402

STARTER_AR = REPO_ROOT / "starter-datasets" / "delhivery" / "02-delhivery-annual-report-fy24-excerpt.pdf"


def _fact(page_number: int, text: str) -> Fact:
    return Fact(
        subject="delhivery revenue",
        predicate="revenue",
        value=1.0,
        unit="INR million",
        period="FY24",
        scope="standalone",
        evidence=Evidence(
            document_id="doc",
            page_number=page_number,
            text=text,
            document_name="doc.pdf",
        ),
    )


# ---------------------------------------------------------------------------
# normalize_evidence_text / verify_evidence_on_page
# ---------------------------------------------------------------------------

def test_normalize_evidence_collapses_whitespace():
    assert normalize_evidence_text("  a\n\tb   c \n") == "a b c"
    assert normalize_evidence_text(None) == ""


def test_verify_evidence_exact_match():
    assert verify_evidence_on_page("revenue stood at 74540.82 million",
                                   "revenue stood at 74540.82 million")


def test_verify_evidence_whitespace_insensitive():
    page = "y The revenue from operations\non standalone basis for FY24\nstood at ₹ 74,540.82 million"
    evidence = "y The revenue from operations on standalone basis for FY24 stood at ₹ 74,540.82 million"
    assert verify_evidence_on_page(evidence, page)


def test_verify_evidence_absent_text_fails():
    assert not verify_evidence_on_page("revenue stood at 999999.00 million",
                                       "revenue stood at 74540.82 million")


def test_verify_evidence_empty_fails():
    assert not verify_evidence_on_page("", "some page")
    assert not verify_evidence_on_page("some evidence", "")
    assert not verify_evidence_on_page(None, "some page")


# ---------------------------------------------------------------------------
# validate_fact_evidence split behaviour
# ---------------------------------------------------------------------------

def test_validate_splits_verified_and_rejected():
    pages = {
        22: "Line one about revenue.\nLine two with the number 74540.82.",
        40: "Director resignations noted on this page.",
    }
    good = _fact(22, "Line one about revenue.")
    also_good = _fact(40, "Director resignations noted on this page.")
    # Tampered evidence: text never appears on page 22
    tampered = _fact(22, "A completely different sentence never present.")
    # Page-offset bug: real page-22 text cited as page 40
    wrong_page = _fact(40, "Line one about revenue.")
    # Page does not exist in the document
    missing_page = _fact(999, "Line one about revenue.")

    verified, rejected = validate_fact_evidence([good, also_good, tampered, wrong_page, missing_page], pages)
    assert verified == [good, also_good]
    assert rejected == [tampered, wrong_page, missing_page]


# ---------------------------------------------------------------------------
# THE named regression test — end to end on the real PDF
# ---------------------------------------------------------------------------

def test_all_pipeline_evidence_verifies_on_real_pdf():
    db = Database(":memory:")
    try:
        result = process_document_pipeline(
            pdf_path=str(STARTER_AR), provider=None, db=db, reasoning_mode="heuristic",
        )
        # The Delhivery annual report alone contributes 12 facts
        # (2 FY24 revenue + 4 director events after dedup, plus the 6
        # Week 4 comparative-highlights claims: 2 FY23 revenue + 4 loss);
        # the corpus count includes the other 5 demo PDFs.
        assert result["facts_count"] == 12
        assert result["evidence_rejected_count"] == 0, (
            "heuristic-path evidence is a slice of the cited page's own text "
            "and must always verify"
        )

        # Independent re-verification against the parsed PDF, not trusting
        # the pipeline's own bookkeeping.
        doc = parse_pdf_document(str(STARTER_AR))
        pages_by_number = {p.page_number: p.raw_text for p in doc.pages}
        facts = db.get_facts(limit=500)
        assert len(facts) == 12
        for f in facts:
            page_text = pages_by_number.get(f.evidence.page_number, "")
            assert verify_evidence_on_page(f.evidence.text, page_text), (
                f"evidence for fact {f.uuid} not found verbatim on cited "
                f"page {f.evidence.page_number}: {f.evidence.text!r}"
            )
    finally:
        db.close()
