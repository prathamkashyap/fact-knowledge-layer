"""Regression tests for confirmed table-adapter structural defects.

These tests use synthetic fixtures only. Each test names the defect it pins:

* B1 - a footnote-marked reporting period must not be classified as a value.
* B2 - physically separate tables must not be fused into one candidate.
* N1/N3 - period headers must map to the value columns they govern, and
  conflicting header tokens must be reported instead of silently overwriting
  each other.
* Phase 3 - the workload guard must not silently change page scoring.

Nothing here asserts factual meaning; these are layout-structure assertions.
"""

import re
from decimal import Decimal

import pytest
import pymupdf

from app.pdf_parser import parse_pdf_page
from app.table_extraction import (
    CellReference,
    ContextDimension,
    DimensionResolution,
    ParsedValueInterpretation,
    ResolutionStatus,
    TableDetectionMethod,
    UnitInterpretation,
    UnitKind,
    TableRowValueContext,
    _aligned_numeric_candidates,
    _is_period_token,
    _looks_numeric,
    extract_tables_from_page,
    validate_context_provenance,
)


FONT_SIZE = 9


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _width(text: str, fontsize: int = FONT_SIZE) -> float:
    return pymupdf.get_text_length(text, fontsize=fontsize)


def _centered(x_center: float, text: str, fontsize: int = FONT_SIZE):
    """A text run centred on ``x_center``."""
    return (x_center - _width(text, fontsize) / 2, text)


def _right(x_right: float, text: str, fontsize: int = FONT_SIZE):
    """A text run whose right edge sits on ``x_right``."""
    return (x_right - _width(text, fontsize), text)


def _at(x_left: float, text: str, fontsize: int = FONT_SIZE):
    return (x_left, text)


def _write(page, rows, fontsize: int = FONT_SIZE) -> None:
    for y, items in rows:
        for x, text in items:
            page.insert_text((x, y), text, fontsize=fontsize)


def _cells(candidate):
    return {(cell.row_index, cell.column_index): cell for cell in candidate.cells}


def _row_texts(candidate):
    rows = {}
    for cell in candidate.cells:
        rows.setdefault(cell.row_index, []).append(cell.text)
    return {index: sorted(texts) for index, texts in rows.items()}


def _geometry_candidates(result):
    return [
        candidate for candidate in result.candidates
        if candidate.detection_method == TableDetectionMethod.WORD_GEOMETRY
    ]


# Column geometry used by the right-aligned fixtures: two value columns whose
# right edges are 250 and 350, with the column centres at 200 and 300.
VALUE_RIGHT_EDGES = (250, 350)
VALUE_COLUMN_CENTRES = (200, 300)
LABEL_LEFT = 30


def _right_aligned_block(y_start: float, headers, rows, row_pitch: float = 12.0):
    """Build the rows of one right-aligned table block."""
    out = [(y_start, [_at(LABEL_LEFT, "Measure")]
            + [_centered(centre, header) for centre, header in zip(VALUE_COLUMN_CENTRES, headers)])]
    for offset, (label, values) in enumerate(rows, start=1):
        y = y_start + offset * row_pitch
        out.append((y, [_at(LABEL_LEFT, label)]
                    + [_right(edge, value) for edge, value in zip(VALUE_RIGHT_EDGES, values)]))
    return out


# ---------------------------------------------------------------------------
# B1 - footnote-marked reporting periods
# ---------------------------------------------------------------------------


def test_footnote_marked_years_are_periods_not_values():
    for token in ("2024*", "2025†", "2026‡", "2024**"):
        assert _is_period_token(token), token
        assert not _looks_numeric(token), token


def test_footnote_marked_year_ranges_are_periods_not_values():
    for token in ("2023-24*", "2023-24†", "FY2024*", "Q4*"):
        assert _is_period_token(token), token
        assert not _looks_numeric(token), token


def test_ordinary_values_remain_recognisable_after_marker_handling():
    for token in ("(£25m)", "-2.1%", "£38m", "1,234.56", "0.25", "-£3.45 million*", "£1.25p"):
        assert _looks_numeric(token), token
    for token in ("n/a", "—", "-", "", "FY2024", "Q4", "2024", "31/12/2024"):
        assert not _looks_numeric(token), token


def test_footnote_marked_year_headers_stay_headers_with_the_original_text():
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=200)
    rows = _right_aligned_block(
        45,
        ("2024*", "2025**"),
        [("Revenue", ("1200", "1450")), ("Profit", ("25", "38")), ("Margin", ("-2.1", "2.6"))],
    )
    _write(page, rows)
    try:
        result = extract_tables_from_page(page, page_number=3)
        candidates = _geometry_candidates(result)
        assert len(candidates) == 1, [c.diagnostics for c in result.candidates]
        candidate = candidates[0]
        cells = _cells(candidate)
        # The marker must be preserved in the emitted header text.
        header_texts = {cells[(0, column)].text for column in (1, 2)}
        assert header_texts == {"2024*", "2025**"}
        # One header row plus exactly three data rows.
        assert sorted({row for row, _ in cells}) == [0, 1, 2, 3]
        # The year tokens must not appear on any data row.
        for (row, _column), cell in cells.items():
            if row == 0:
                continue
            assert "2024" not in cell.text and "2025" not in cell.text
    finally:
        doc.close()


def test_footnote_marked_year_headers_do_not_make_the_header_a_data_row():
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=200)
    rows = _right_aligned_block(
        45,
        ("2024*", "2025**"),
        [("Revenue", ("1200", "1450")), ("Profit", ("25", "38")), ("Margin", ("-2.1", "2.6"))],
    )
    _write(page, rows)
    try:
        result = extract_tables_from_page(page, page_number=3)
        candidate = _geometry_candidates(result)[0]
        texts_by_row = _row_texts(candidate)
        assert len(texts_by_row) == 4
        assert {"2024*", "2025**"} <= set(texts_by_row[0])
        for row in (1, 2, 3):
            assert not any("2024" in text or "2025" in text for text in texts_by_row[row])
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# B2 - physically separate tables must stay separate
# ---------------------------------------------------------------------------


def test_two_tables_separated_by_whitespace_produce_two_candidates():
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=360)
    first = _right_aligned_block(
        45,
        ("2023", "2024"),
        [("Revenue", ("1200", "1450")), ("Profit", ("25", "38")), ("Margin", ("-2.1", "2.6"))],
    )
    second = _right_aligned_block(
        245,
        ("2025", "2026"),
        [("Revenue", ("1300", "1500")), ("Profit", ("26", "39")), ("Margin", ("-2.0", "2.7"))],
    )
    _write(page, first + second)
    try:
        result = extract_tables_from_page(page, page_number=4)
        candidates = _geometry_candidates(result)
        assert len(candidates) == 2, [
            (candidate.bbox, [d.code for d in candidate.diagnostics]) for candidate in candidates
        ]
        for candidate in candidates:
            assert candidate.bbox is not None
        upper, lower = sorted(candidates, key=lambda item: item.bbox[1])
        # Neither candidate may span both table regions.
        assert upper.bbox[3] < lower.bbox[1]
        # Each table keeps its own header.
        upper_text = {cell.text for cell in upper.cells}
        lower_text = {cell.text for cell in lower.cells}
        assert {"2023", "2024"} <= upper_text
        assert {"2025", "2026"} <= lower_text
        assert not ({"2025", "2026"} & upper_text)
        assert not ({"2023", "2024"} & lower_text)
    finally:
        doc.close()


def test_two_tables_separated_by_prose_produce_two_candidates():
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=360)
    first = _right_aligned_block(
        45,
        ("2023", "2024"),
        [("Revenue", ("1200", "1450")), ("Profit", ("25", "38")), ("Margin", ("-2.1", "2.6"))],
    )
    second = _right_aligned_block(
        165,
        ("2025", "2026"),
        [("Revenue", ("1300", "1500")), ("Profit", ("26", "39")), ("Margin", ("-2.0", "2.7"))],
    )
    prose = (120, [_at(LABEL_LEFT, "The following table restates the same measures for the following year.")])
    _write(page, first + [prose] + second)
    try:
        result = extract_tables_from_page(page, page_number=5)
        candidates = _geometry_candidates(result)
        assert len(candidates) == 2, [
            (candidate.bbox, [d.code for d in candidate.diagnostics]) for candidate in candidates
        ]
        upper, lower = sorted(candidates, key=lambda item: item.bbox[1])
        assert upper.bbox[3] < lower.bbox[1]
        upper_text = {cell.text for cell in upper.cells}
        lower_text = {cell.text for cell in lower.cells}
        assert {"2023", "2024"} <= upper_text
        assert {"2025", "2026"} <= lower_text
    finally:
        doc.close()


def test_two_nearby_tables_with_independent_headers_produce_two_candidates():
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=300)
    first = _right_aligned_block(
        45,
        ("2023", "2024"),
        [("Revenue", ("1200", "1450")), ("Profit", ("25", "38")), ("Margin", ("-2.1", "2.6"))],
    )
    second = _right_aligned_block(
        110,
        ("2025", "2026"),
        [("Revenue", ("1300", "1500")), ("Profit", ("26", "39")), ("Margin", ("-2.0", "2.7"))],
    )
    _write(page, first + second)
    try:
        result = extract_tables_from_page(page, page_number=6)
        candidates = _geometry_candidates(result)
        assert len(candidates) == 2, [
            (candidate.bbox, [d.code for d in candidate.diagnostics]) for candidate in candidates
        ]
        upper, lower = sorted(candidates, key=lambda item: item.bbox[1])
        assert upper.bbox[3] < lower.bbox[1]
        upper_text = {cell.text for cell in upper.cells}
        lower_text = {cell.text for cell in lower.cells}
        assert {"2023", "2024"} <= upper_text
        assert {"2025", "2026"} <= lower_text
        assert not ({"2025", "2026"} & upper_text)
    finally:
        doc.close()


def test_one_table_with_a_genuine_internal_subheading_stays_one_candidate():
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=260)
    rows = [
        (45, [_at(LABEL_LEFT, "Measure")]
         + [_centered(centre, header) for centre, header in zip(VALUE_COLUMN_CENTRES, ("2024", "2025"))]),
        (65, [_at(LABEL_LEFT, "Revenue")] + [_right(edge, value) for edge, value in zip(VALUE_RIGHT_EDGES, ("1200", "1450"))]),
        (85, [_at(LABEL_LEFT, "Profit")] + [_right(edge, value) for edge, value in zip(VALUE_RIGHT_EDGES, ("25", "38"))]),
        (105, [_at(LABEL_LEFT, "SEGMENT BETA")]),
        (125, [_at(LABEL_LEFT, "Revenue")] + [_right(edge, value) for edge, value in zip(VALUE_RIGHT_EDGES, ("50", "55"))]),
        (145, [_at(LABEL_LEFT, "Profit")] + [_right(edge, value) for edge, value in zip(VALUE_RIGHT_EDGES, ("10", "12"))]),
        (165, [_at(LABEL_LEFT, "Margin")] + [_right(edge, value) for edge, value in zip(VALUE_RIGHT_EDGES, ("20%", "21%"))]),
    ]
    _write(page, rows)
    try:
        result = extract_tables_from_page(page, page_number=7)
        candidates = _geometry_candidates(result)
        assert len(candidates) == 1, [
            (candidate.bbox, [d.code for d in candidate.diagnostics]) for candidate in candidates
        ]
        candidate = candidates[0]
        assert any(cell.text == "SEGMENT BETA" for cell in candidate.cells)
        assert any(
            diagnostic.code == "internal_subheading_retained" for diagnostic in candidate.diagnostics
        )
    finally:
        doc.close()


def test_two_borderless_tables_separated_by_whitespace_produce_two_candidates():
    """The gap-clustered path must not fuse two blocks either."""
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=340)
    rows = []
    for y_start, values in ((45, ("1200", "1450")), (235, ("1300", "1500"))):
        rows.append((y_start, [_at(35, "Measure"), _at(190, "FY2024"), _at(300, "FY2025")]))
        for offset, (label, first, second) in enumerate(
            (("Revenue", *values), ("Profit", "25", "38"), ("Margin", "-2.1", "2.6")), start=1
        ):
            rows.append((y_start + offset * 30, [_at(35, label), _at(180, first), _at(290, second)]))
    _write(page, rows)
    try:
        result = extract_tables_from_page(page, page_number=8)
        candidates = _geometry_candidates(result)
        assert len(candidates) == 2, [
            (candidate.bbox, [d.code for d in candidate.diagnostics]) for candidate in candidates
        ]
        upper, lower = sorted(candidates, key=lambda item: item.bbox[1])
        assert upper.bbox[3] < lower.bbox[1]
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# N1 / N3 - header mapping and header collisions
# ---------------------------------------------------------------------------


def _synthetic_words(rows, height=9.0):
    """Build the ``(words, heights)`` input of the aligned-geometry detector.

    ``rows`` is a list of ``(y, [(x0, text, width), ...])`` entries.
    """
    words = []
    for y, items in rows:
        for x0, text, width in items:
            words.append(((x0, y, x0 + width, y + height), text))
    return words, [height] * len(words)


def _aligned_fixture(header_centres, header_texts, value_edges, rows, label="Measure"):
    """One right-aligned block: a period header row plus numeric data rows."""
    entries = [(40, [(30.0, label, 60.0)]
                + [(centre - _width(text) / 2, text, _width(text))
                   for centre, text in zip(header_centres, header_texts)])]
    for offset, (row_label, values) in enumerate(rows, start=1):
        entries.append((40 + offset * 12, [(30.0, row_label, 60.0)]
                        + [(edge - _width(value), value, _width(value))
                           for edge, value in zip(value_edges, values)]))
    return entries


def test_aligned_path_maps_centered_headers_to_the_value_columns_they_govern():
    """A centred header must govern the values of its own column.

    The values are right-aligned, so a header token sits half a column width
    away from the right edge of the values it labels.
    """
    entries = _aligned_fixture(
        (200.0, 300.0),
        ("2023", "2024"),
        (250.0, 350.0),
        [("Revenue", ("1200", "1450")), ("Profit", ("25", "38")), ("Margin", ("-2.1", "2.6"))],
    )
    words, heights = _synthetic_words(entries)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 1, [c.diagnostics for c in candidates]
    candidate = candidates[0]
    cells = _cells(candidate)
    assert cells[(0, 1)].text == "2023"
    assert cells[(0, 2)].text == "2024"
    assert cells[(1, 1)].text == "1200"
    assert cells[(1, 2)].text == "1450"
    assert cells[(3, 1)].text == "-2.1"
    assert cells[(3, 2)].text == "2.6"
    assert any(
        diagnostic.code == "period_headers_associated_by_alignment"
        for diagnostic in candidate.diagnostics
    )


def test_aligned_path_reports_conflicting_header_tokens():
    """Two period tokens competing for one column must be reported."""
    entries = _aligned_fixture(
        (200.0, 258.0),
        ("2023", "2024"),
        (250.0, 350.0),
        [("Revenue", ("1200", "1450")), ("Profit", ("25", "38")), ("Margin", ("-2.1", "2.6"))],
    )
    words, heights = _synthetic_words(entries)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 1
    candidate = candidates[0]
    codes = {diagnostic.code for diagnostic in candidate.diagnostics}
    assert "period_header_conflict" in codes, codes
    header_cells = [cell for cell in candidate.cells if cell.row_index == 0]
    texts = [cell.text for cell in header_cells]
    assert len(texts) == len(set(texts)), texts


def test_aligned_path_reports_a_detected_but_unassociated_period_header():
    """A period header that cannot govern the block stays visible."""
    entries = [
        (40, [(30.0, "Measure", 60.0), (190.0, "2023", 20.0), (290.0, "2024", 20.0)]),
        (100, [(30.0, "Other", 60.0), (190.0, "2021", 20.0), (290.0, "2022", 20.0)]),
        (200, [(30.0, "Revenue", 60.0), (210.0, "1200", 40.0), (310.0, "1450", 40.0)]),
        (212, [(30.0, "Profit", 60.0), (230.0, "25", 20.0), (310.0, "38", 40.0)]),
        (224, [(30.0, "Margin", 60.0), (210.0, "-2.1", 40.0), (310.0, "2.6", 40.0)]),
    ]
    words, heights = _synthetic_words(entries)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 1
    candidate = candidates[0]
    codes = {diagnostic.code for diagnostic in candidate.diagnostics}
    assert "period_header_not_associated" in codes, codes
    # No period header may be attached when no header governs the block.
    assert not [cell for cell in candidate.cells if _is_period_token(cell.text)]


def test_aligned_path_keeps_footnote_marked_years_out_of_the_data_rows():
    """A marked year header must not become a row of values."""
    entries = _aligned_fixture(
        (200.0, 300.0),
        ("2024*", "2025*"),
        (250.0, 350.0),
        [("Revenue", ("1200", "1450")), ("Profit", ("25", "38")), ("Margin", ("-2.1", "2.6"))],
    )
    words, heights = _synthetic_words(entries)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 1
    candidate = candidates[0]
    cells = _cells(candidate)
    # The marked years are the header row, with their original text.
    assert cells[(0, 1)].text == "2024*"
    assert cells[(0, 2)].text == "2025*"
    # Exactly three data rows follow it.
    assert sorted({row for row, _ in cells}) == [0, 1, 2, 3]
    for (row, _column), cell in cells.items():
        if row > 0:
            assert "2024" not in cell.text and "2025" not in cell.text


def test_aligned_candidate_satisfies_the_phase0_header_provenance_contract():
    """The emitted header must govern its own value column under the contract.

    The Phase 0 contract's provenance validator is used as an independent
    check: a header path that points at the wrong cell, or that does not
    govern the value column, is rejected by the contract itself.
    """
    entries = _aligned_fixture(
        (200.0, 300.0),
        ("2023", "2024"),
        (250.0, 350.0),
        [("Revenue", ("1200", "1450")), ("Profit", ("25", "38")), ("Margin", ("-2.1", "2.6"))],
    )
    words, heights = _synthetic_words(entries)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 1
    candidate = candidates[0]
    cells = _cells(candidate)
    for column, expected_value in ((1, "1200"), (2, "1450")):
        value_cell = cells[(1, column)]
        assert value_cell.text == expected_value
        context = TableRowValueContext(
            page_number=candidate.page_number,
            table_id=candidate.table_id,
            row_index=1,
            value_column_index=column,
            row_label=CellReference(row_index=1, column_index=0),
            column_header_path=[CellReference(row_index=0, column_index=column)],
            value_cell=CellReference(row_index=1, column_index=column),
            raw_display_value=value_cell.text,
            value=ParsedValueInterpretation(
                raw_text=value_cell.text,
                numeric_value=Decimal("1200"),
                unit=UnitInterpretation(kind=UnitKind.NUMBER, raw_unit=""),
            ),
            resolutions=[
                DimensionResolution(
                    dimension=ContextDimension.ROW_VALUE_ASSOCIATION,
                    status=ResolutionStatus.RESOLVED,
                ),
                DimensionResolution(
                    dimension=ContextDimension.HEADER_ASSOCIATION,
                    status=ResolutionStatus.RESOLVED,
                ),
                DimensionResolution(
                    dimension=ContextDimension.ENTITY,
                    status=ResolutionStatus.UNRESOLVED,
                    reason="The adapter does not resolve an issuer.",
                ),
                DimensionResolution(
                    dimension=ContextDimension.PERIOD,
                    status=ResolutionStatus.RESOLVED,
                ),
                DimensionResolution(
                    dimension=ContextDimension.UNIT,
                    status=ResolutionStatus.RESOLVED,
                ),
                DimensionResolution(
                    dimension=ContextDimension.SCALE,
                    status=ResolutionStatus.RESOLVED,
                ),
            ],
        )
        validate_context_provenance(candidate, context)
        assert context.column_header_path[-1] == CellReference(row_index=0, column_index=column)


def test_centered_headers_map_to_the_value_columns_they_govern():
    """A centred header must govern the values of its own column.

    The values are right-aligned, so the header token sits half a column
    width away from the right edge of the values it labels. The header cell
    and the value cells must still share a column index.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=460, height=200)
    centres = (170, 250, 330)
    edges = (210, 290, 370)
    headers = ("2023", "2024", "2025")
    rows = [(45, [_at(LABEL_LEFT, "Measure")]
             + [_centered(centre, header) for centre, header in zip(centres, headers)])]
    for offset, (label, values) in enumerate(
        (("Revenue", ("111", "222", "333")),
         ("Profit", ("112", "223", "334")),
         ("Margin", ("113", "224", "335"))), start=1
    ):
        rows.append((45 + offset * 12, [_at(LABEL_LEFT, label)]
                     + [_right(edge, value) for edge, value in zip(edges, values)]))
    _write(page, rows)
    try:
        result = extract_tables_from_page(page, page_number=9)
        candidates = _geometry_candidates(result)
        assert len(candidates) == 1, [
            (candidate.bbox, [d.code for d in candidate.diagnostics]) for candidate in result.candidates
        ]
        candidate = candidates[0]
        cells = _cells(candidate)
        expected = {1: ("2023", {"111", "112", "113"}),
                    2: ("2024", {"222", "223", "224"}),
                    3: ("2025", {"333", "334", "335"})}
        for column, (header, values) in expected.items():
            assert (0, column) in cells, sorted(cells)
            assert cells[(0, column)].text == header
            column_values = {
                cell.text for cell in candidate.cells
                if cell.column_index == column and cell.row_index > 0
            }
            assert column_values == values, (column, column_values)
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Phase 3 - workload guard and legacy scoring
# ---------------------------------------------------------------------------


def _independent_total_score(text, has_tables, blocks, keywords=None):
    """Recompute the documented page score without using the parser's flags."""
    if keywords is None:
        from app.pdf_parser import DEFAULT_KEYWORDS

        keywords = DEFAULT_KEYWORDS
    tokens = re.findall(r"\b\w+\b", text)
    total_tokens = len(tokens) if tokens else 1
    numeric_density = round(len(re.findall(r"\b\d+(?:\.\d+)?\b", text)) / total_tokens, 4)
    numeric_score = min(numeric_density * 10.0, 3.0)
    percentage_score = min(len(re.findall(r"%\b|\bpercent\b|\bper cent\b", text, re.IGNORECASE)) * 0.5, 2.0)
    currency_score = min(len(re.findall(
        r"₹|\$|INR\b|USD\b|Rs\.?|crore\b|million\b|billion\b|lakh\b", text, re.IGNORECASE
    )) * 0.4, 2.0)
    date_fiscal_score = min(len(re.findall(
        r"\bFY\d{2,4}\b|\b20\d{2}-\d{2,4}\b|\bQ[1-4]\b|\bMarch \d{1,2}\b|\bApril\b|\bDecember\b",
        text, re.IGNORECASE
    )) * 0.5, 2.0)
    table_score = 2.0 if has_tables else 0.0
    lines = text.splitlines()
    table_score += min(sum(
        1 for line in lines if len(re.findall(r"\b\d+(?:\.\d+)?\b", line)) >= 3
    ) * 0.2, 1.5)
    heading_score = min(sum(
        1 for line in lines if 2 <= len(line.strip().split()) <= 8 and (line.isupper() or line.istitle())
    ) * 0.3, 1.5)
    matched = [kw for kw in keywords if re.search(r"\b" + re.escape(kw) + r"\b", text.lower())]
    keyword_score = min(len(matched) * 0.8, 4.0)
    return round(
        numeric_score + percentage_score + currency_score + date_fiscal_score
        + table_score + heading_score + keyword_score,
        2,
    )


def _make_bordered_page():
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=250)
    xs = [35, 175, 280, 365]
    ys = [35, 75, 115, 155, 195]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    rows = [
        ["Measure", "FY2024", "FY2025"],
        ["Revenue", "£1,200m*", "£1,450m"],
        ["Operating profit", "(£25m)", "£38m"],
        ["Margin", "-2.1%", "2.6%"],
    ]
    for row_index, row in enumerate(rows):
        for column_index, text in enumerate(row):
            page.insert_text((xs[column_index] + 4, ys[row_index] + 24), text, fontsize=9)
    return doc


def test_has_tables_follows_the_legacy_rule_for_bounded_pages():
    """A bordered table within the drawing ceiling must set has_tables.

    The expectation is stated literally: the legacy rule detects a table on
    this page, so the parser flag must be True and the score must be the one
    implied by that flag.
    """
    doc = _make_bordered_page()
    try:
        parsed = parse_pdf_page(doc, 0, "synthetic", "synthetic.pdf")
        assert parsed.has_tables is True
        assert parsed.score_breakdown.table_score >= 2.0
        assert parsed.score_breakdown.total_score == _independent_total_score(
            parsed.raw_text, True, parsed.blocks
        )
        assert parsed.score_breakdown.total_score == _independent_total_score(
            parsed.raw_text, False, parsed.blocks
        ) + 2.0
    finally:
        doc.close()


def test_page_without_tables_keeps_has_tables_false():
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 60), "Revenue remained resilient during the year.", fontsize=9)
    try:
        parsed = parse_pdf_page(doc, 0, "synthetic", "prose.pdf")
        assert parsed.has_tables is False
        assert parsed.score_breakdown.total_score == _independent_total_score(
            parsed.raw_text, False, parsed.blocks
        )
    finally:
        doc.close()


@pytest.mark.parametrize(
    "reason,expected_code",
    [
        ("drawing_limit", "find_tables_skipped_workload"),
        ("word_limit", "find_tables_skipped_workload"),
        ("text_limit", "find_tables_skipped_workload"),
        ("no_vector_drawings", "find_tables_skipped_workload"),
        ("measurement_failed", "find_tables_skipped_workload"),
    ],
)
def test_each_workload_guard_skip_path_is_reported_and_keeps_the_fallback(reason, expected_code):
    class GuardPage:
        rotation = 0

        def __init__(self, inner):
            self.inner = inner
            self.calls = 0
            self.drawing_measurements = 0

        def get_text(self, kind):
            return self.inner.get_text(kind)

        def get_drawings(self):
            self.drawing_measurements += 1
            if reason == "drawing_limit":
                return [{}] * 45
            if reason == "no_vector_drawings":
                return []
            if reason == "measurement_failed":
                raise RuntimeError("drawings unavailable")
            return [{}] * 2

        def find_tables(self):
            self.calls += 1
            raise AssertionError("the detector must not run for this skip path")

    inner = pymupdf.open()
    page = inner.new_page(width=420, height=220)
    rows = _right_aligned_block(
        45,
        ("2024", "2025"),
        [("Revenue", ("1200", "1450")), ("Profit", ("25", "38")), ("Margin", ("-2.1", "2.6"))],
    )
    _write(page, rows)
    try:
        guarded = GuardPage(inner[0])
        if reason == "word_limit":
            # More words than the workload bound, without changing the page's
            # text volume or its table geometry.
            real_words = inner[0].get_text("words")
            padding = [
                (600.0 + index, 400.0, 601.0 + index, 409.0, "x", 0, 0, 0)
                for index in range(3200)
            ]
            guarded.get_text = lambda kind, real=real_words, pad=padding: (
                list(real) + list(pad) if kind == "words" else inner[0].get_text(kind)
            )
        if reason == "text_limit":
            guarded.get_text = lambda kind: ("x" * 25000) if kind == "text" else inner[0].get_text(kind)
        result = extract_tables_from_page(guarded, page_number=12)
        codes = {diagnostic.code for diagnostic in result.diagnostics}
        assert expected_code in codes, codes
        assert guarded.calls == 0
        # The geometry fallback must still run.
        assert _geometry_candidates(result), "geometry fallback did not run"
    finally:
        inner.close()


def test_detector_failure_does_not_discard_page_text_or_scoring():
    class FailingPage:
        rotation = 0
        rect = pymupdf.Rect(0, 0, 300, 200)
        mediabox = pymupdf.Rect(0, 0, 300, 200)
        derotation_matrix = pymupdf.Matrix(1, 1)

        def get_text(self, kind):
            return "Revenue remains stable.\n" if kind == "text" else []

        def get_drawings(self):
            return [{}]

        def find_tables(self):
            raise RuntimeError("fixture detector failure")

    class Document:
        def __init__(self):
            self.page = FailingPage()

        def load_page(self, index):
            return self.page

    parsed = parse_pdf_page(Document(), 0, "fixture", "fixture.pdf")
    assert parsed.raw_text == "Revenue remains stable.\n"
    assert parsed.has_tables is False
    assert parsed.score_breakdown.total_score == _independent_total_score(
        parsed.raw_text, False, parsed.blocks
    )
    assert any(item.code == "find_tables_failed" for item in parsed.table_diagnostics)


def test_workload_guard_skips_measure_drawings_for_volume_excluded_pages():
    """Vector drawing enumeration is deferred for pages outside the bounds.

    Enumerating drawings is the most expensive measurement on vector-heavy
    pages, and a page already excluded by its text volume cannot reach the
    costly detector, so the measurement is skipped for it.
    """
    class CountingPage:
        rotation = 0

        def __init__(self, inner):
            self.inner = inner
            self.drawing_measurements = 0

        def get_text(self, kind):
            if kind == "text":
                return "x" * 25000
            return self.inner.get_text(kind)

        def get_drawings(self):
            self.drawing_measurements += 1
            return [{}] * 2

        def find_tables(self):
            raise AssertionError("the detector must not run")

    inner = pymupdf.open()
    inner.new_page(width=420, height=220)
    try:
        page = CountingPage(inner[0])
        result = extract_tables_from_page(page, page_number=13)
        codes = {diagnostic.code for diagnostic in result.diagnostics}
        assert "find_tables_skipped_workload" in codes
        message = next(
            item.message for item in result.diagnostics
            if item.code == "find_tables_skipped_workload"
        )
        assert "text_limit" in message
        assert page.drawing_measurements == 0
    finally:
        inner.close()


def test_unavailable_measurements_abstain_from_the_detector():
    class UnmeasurablePage:
        rotation = 0
        rect = pymupdf.Rect(0, 0, 100, 100)

        def get_text(self, kind):
            if kind == "text":
                return "Revenue remains stable.\n"
            raise RuntimeError("words unavailable")

        def get_drawings(self):
            return [{}]

        def find_tables(self):
            raise AssertionError("the detector must not run")

    result = extract_tables_from_page(UnmeasurablePage(), page_number=14)
    codes = {diagnostic.code for diagnostic in result.diagnostics}
    assert "find_tables_skipped_workload" in codes
    message = next(
        item.message for item in result.diagnostics
        if item.code == "find_tables_skipped_workload"
    )
    assert "measurement_failed" in message


def test_legacy_has_tables_signal_is_unchanged_for_pages_inside_the_workload_bounds():
    """The legacy rule still detects a table on a page the guard allows.

    The workload guard decides whether the costly detector may run. It must
    not change the established page signal for pages the guard allows.
    """
    doc = _make_bordered_page()
    try:
        parsed = parse_pdf_page(doc, 0, "synthetic", "synthetic.pdf")
        assert parsed.has_tables is True
        assert parsed.score_breakdown.table_score >= 2.0
        assert parsed.score_breakdown.is_candidate == (
            parsed.score_breakdown.total_score >= 8.0
        )
    finally:
        doc.close()


def test_documented_has_tables_deviation_on_a_workload_excluded_page():
    """A page outside the workload bounds cannot be probed by the detector.

    This is the precise compatibility change: the legacy rule could call the
    detector on such a page, the workload guard cannot, so the table signal
    falls back to the tabular-line heuristic and the page score loses the
    table component.
    """
    class ExcludedPage:
        rotation = 0
        rect = pymupdf.Rect(0, 0, 300, 200)
        mediabox = pymupdf.Rect(0, 0, 300, 200)
        derotation_matrix = pymupdf.Matrix(1, 1)

        def __init__(self):
            self.calls = 0

        def get_text(self, kind):
            if kind == "text":
                return "x" * 25000
            return []

        def get_drawings(self):
            return [{}] * 4

        def find_tables(self):
            self.calls += 1
            return type("Finder", (), {"tables": [object()]})()

    class Document:
        def __init__(self):
            self.page = ExcludedPage()

        def load_page(self, index):
            return self.page

    document = Document()
    parsed = parse_pdf_page(document, 0, "fixture", "fixture.pdf")
    assert document.page.calls == 0
    assert parsed.has_tables is False
    assert parsed.score_breakdown.table_score < 2.0
    assert any(
        item.code == "find_tables_skipped_workload" for item in parsed.table_diagnostics
    )
