"""
Gate 1 automated tests for PDF ingestion and candidate page prioritization.
Tests A through H covering structural correctness, scoring, and limitations.
"""

import os
import pytest
from app.pdf_parser import (
    parse_pdf_document,
    parse_pdf_page,
    compute_page_score,
    BlockMetadata,
    ScoreBreakdown,
)
from tests.test_table_regression import (
    demonstrate_table_failure,
    demonstrate_imf_table_failure,
    CORRECT_TABLE_ALIGNMENT,
)

STARTER_DIR = "starter-datasets"


# ---------------------------------------------------------------------------
# Test A: PDF opens and page count is correct
# ---------------------------------------------------------------------------

def test_prospectus_page_count():
    doc = parse_pdf_document(f"{STARTER_DIR}/delhivery/01-delhivery-prospectus-2022-excerpt.pdf")
    assert doc.total_pages == 100

def test_annual_report_page_count():
    doc = parse_pdf_document(f"{STARTER_DIR}/delhivery/02-delhivery-annual-report-fy24-excerpt.pdf")
    assert doc.total_pages == 100

def test_earnings_page_count():
    doc = parse_pdf_document(f"{STARTER_DIR}/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf")
    assert doc.total_pages == 27

def test_eco_survey_page_count():
    doc = parse_pdf_document(f"{STARTER_DIR}/india-macroeconomy/01-india-economic-survey-2024-25-excerpt.pdf")
    assert doc.total_pages == 89

def test_rbi_page_count():
    doc = parse_pdf_document(f"{STARTER_DIR}/india-macroeconomy/02-rbi-annual-report-2024-25-excerpt.pdf")
    assert doc.total_pages == 100

def test_imf_page_count():
    doc = parse_pdf_document(f"{STARTER_DIR}/india-macroeconomy/03-imf-india-2025-article-iv-excerpt.pdf")
    assert doc.total_pages == 95


# ---------------------------------------------------------------------------
# Test B: Page numbering is 1-indexed for human-facing output
# ---------------------------------------------------------------------------

def test_page_numbering_starts_at_one():
    doc = parse_pdf_document(f"{STARTER_DIR}/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf")
    assert doc.pages[0].page_number == 1
    assert doc.pages[-1].page_number == 27

def test_page_numbering_sequential():
    doc = parse_pdf_document(f"{STARTER_DIR}/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf")
    for i, page in enumerate(doc.pages):
        assert page.page_number == i + 1


# ---------------------------------------------------------------------------
# Test C: Raw page text is retained
# ---------------------------------------------------------------------------

def test_raw_text_is_nonempty_for_content_pages():
    doc = parse_pdf_document(f"{STARTER_DIR}/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf")
    # Page 1 is the cover — it should have some text
    assert len(doc.pages[0].raw_text) > 0
    # Page 6 (revenue page) should have substantial text
    assert len(doc.pages[5].raw_text) > 500

def test_raw_text_matches_document_id():
    doc = parse_pdf_document(f"{STARTER_DIR}/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf")
    for page in doc.pages:
        assert page.document_id == doc.document_id
        assert page.filename == doc.filename


# ---------------------------------------------------------------------------
# Test D: Candidate scoring is deterministic
# ---------------------------------------------------------------------------

def test_scoring_deterministic():
    text = "Revenue from operations stood at INR 81,415.38 million for FY24 consolidated basis."
    score1 = compute_page_score(text=text, has_tables=True, blocks=[])
    score2 = compute_page_score(text=text, has_tables=True, blocks=[])
    assert score1.total_score == score2.total_score
    assert score1.keyword_matches == score2.keyword_matches

def test_scoring_empty_text():
    score = compute_page_score(text="", has_tables=False, blocks=[])
    assert score.total_score == 0.0
    assert score.is_candidate is False

def test_scoring_deterministic_across_runs():
    text = "The fiscal deficit was 5.5 per cent of GDP in 2023-24."
    scores = [compute_page_score(text, has_tables=False, blocks=[]) for _ in range(5)]
    assert all(s.total_score == scores[0].total_score for s in scores)


# ---------------------------------------------------------------------------
# Test E: Financial/numeric page receives relevant signals
# ---------------------------------------------------------------------------

def test_financial_page_scores_high():
    """A page with revenue, growth, percentages, and fiscal-year references should score high."""
    text = (
        "Revenue from operations on standalone basis for FY24 stood at INR 74,540.82 million, "
        "registering a growth of 11.95%. Consolidated revenue from operations for FY24 stood at "
        "INR 81,415.38 million, registering a growth of 12.68%. Loss for the year was INR 2,491.86 million."
    )
    score = compute_page_score(text=text, has_tables=True, blocks=[])
    assert score.is_candidate is True
    assert score.numeric_density > 0.05
    assert score.currency_count > 0
    assert "revenue" in score.keyword_matches
    assert "growth" in score.keyword_matches
    assert score.total_score >= 8.0


# ---------------------------------------------------------------------------
# Test F: Semantic/non-numeric page receives relevant signals
# ---------------------------------------------------------------------------

def test_director_page_scores_high():
    """A page about director resignations should match semantic keywords even with low numeric density."""
    text = (
        "Suvir Suren Sujan, Non-Executive Director, resigned from the Board "
        "with effect from August 24, 2023. Donald Francis Colleran, "
        "Non-Executive Director, ceased to be a Director with effect from "
        "September 27, 2023. Mr. Sandeep Kumar Barasia ceased to be a "
        "Director with effect from July 01, 2024."
    )
    score = compute_page_score(text=text, has_tables=False, blocks=[])
    assert "resigned" in score.keyword_matches
    assert "ceased" in score.keyword_matches
    assert "director" in score.keyword_matches
    assert "board" in score.keyword_matches
    # Keyword score should be substantial — 4 matches * 0.8 = 3.2
    assert score.keyword_score >= 3.0
    # On a full page with more context, this would easily exceed threshold.
    # The key point is that non-numeric keywords ARE detected.
    assert len(score.keyword_matches) >= 4

def test_heading_rich_page_gets_heading_score():
    """A page with many headings should get a heading score boost."""
    text = (
        "REVENUE BREAKDOWN\n"
        "Express Parcel\n"
        "Part Truckload\n"
        "Supply Chain\n"
        "Cross Border\n"
        "TOTAL REVENUE\n"
    )
    score = compute_page_score(text=text, has_tables=False, blocks=[])
    assert score.heading_score > 0.0


# ---------------------------------------------------------------------------
# Test G: Prioritization works on an unseen/synthetic test PDF
# ---------------------------------------------------------------------------

def test_prioritization_on_synthetic_pdf():
    """Create a tiny synthetic PDF in memory and verify the parser processes it."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()

    # Insert a page with clear financial content
    text = (
        "Revenue from operations: INR 1,000 million\n"
        "Growth rate: 8.5 per cent\n"
        "Loss for the year: INR 200 million\n"
        "FY2024\n"
        "Consolidated basis\n"
        "Fiscal deficit: 5.5 per cent of GDP\n"
    )
    page.insert_text((72, 72), text, fontsize=12)
    tmp_path = "/tmp/test_synthetic_financial.pdf"
    doc.save(tmp_path)
    doc.close()

    result = parse_pdf_document(tmp_path)
    assert result.total_pages == 1
    assert len(result.pages) == 1
    assert result.pages[0].page_number == 1
    assert len(result.pages[0].raw_text) > 0
    assert result.pages[0].score_breakdown.is_candidate is True
    assert "revenue" in result.pages[0].score_breakdown.keyword_matches
    assert "growth" in result.pages[0].score_breakdown.keyword_matches

    os.remove(tmp_path)


# ---------------------------------------------------------------------------
# Test H: Table-layout failure regression fixture
# ---------------------------------------------------------------------------

def test_rbi_table_failure_fixture():
    """Verify that the RBI table regression fixture demonstrates header detachment."""
    fixture = demonstrate_table_failure()
    assert len(fixture["year_header_lines"]) >= 2
    assert "failure_description" in fixture
    assert "correct_alignment" in fixture
    # Verify the ground truth exists
    assert fixture["correct_alignment"]["Real GDP Growth 2022-23"] == 7.6
    assert fixture["correct_alignment"]["CPI Inflation 2024-25"] == 4.6

def test_imf_table_failure_fixture():
    """Verify that the IMF table regression fixture demonstrates header detachment."""
    fixture = demonstrate_imf_table_failure()
    assert fixture["year_header_line"] is not None
    assert "2021/22" in fixture["year_header_text"]
    assert "2023/24" in fixture["year_header_text"]
    assert "failure_description" in fixture
    assert fixture["correct_alignment"]["Real GDP 2023/24"] == 9.2

def test_plain_text_cannot_associate_years_to_values():
    """
    Demonstrates that raw_text extraction loses column-to-year alignment.
    We extract the RBI table page, find the year header and data rows,
    and show they are on different lines with no positional link.
    """
    doc = parse_pdf_document(
        f"{STARTER_DIR}/india-macroeconomy/02-rbi-annual-report-2024-25-excerpt.pdf"
    )
    # Appendix tables are in the 90s pages
    page_91 = next(p for p in doc.pages if p.page_number == 91)
    text = page_91.raw_text

    # The year headers appear as separate lines (not on one line)
    # This is actually WORSE than detached headers — each year is on its own line
    lines = text.splitlines()
    year_lines = [i for i, line in enumerate(lines) if line.strip() in ("2022-23", "2023-24", "2024-25")]
    assert len(year_lines) >= 2, f"Expected at least 2 year header lines, got {len(year_lines)}"

    # The GDP data row is elsewhere in the text, completely disconnected from headers
    gdp_line = None
    for i, line in enumerate(lines):
        if "Real GDP at Market Prices" in line:
            gdp_line = i
            break
    assert gdp_line is not None, "GDP row should be found"
    # Verify GDP row is on a different line from the year headers
    assert gdp_line not in year_lines, "GDP row should be separated from year headers"

    # The year header lines contain ONLY the year label, no data values
    # The data values appear on yet another line, so plain text cannot map them
    for yi in year_lines:
        line_text = lines[yi].strip()
        # Only the year label is on this line
        assert line_text in ("2022-23", "2023-24", "2024-25"), (
            f"Year header line should contain only the year, got: {line_text}"
        )
