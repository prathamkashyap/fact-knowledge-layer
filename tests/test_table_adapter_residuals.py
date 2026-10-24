"""Regression tests for the residual table-boundary and header-ambiguity defects.

These follow the independent audit of commit 386d43b and pin the behaviour that
the repair must deliver. Each test names the defect it pins:

* B2-1 - independently separated blocks must not be fused into one candidate
  because a page-wide pitch estimate was contaminated by the very gap it
  should have detected.
* B2-2 - a table whose own row pitch is wider than another table's on the same
  page must not disappear because of that other table.
* B2-3 - a candidate's bounding box must not claim another table's row region.
* N3 - period header tokens that cannot be justified by their geometry must be
  reported as ambiguous instead of being assigned one per column.
* B1-a - ordinary quarterly period labels such as ``Q4 2024`` must be periods.

Synthetic fixtures only; no real document is read.
"""

from decimal import Decimal

import pytest
import pymupdf

from app.table_extraction import (
    TableDetectionMethod,
    _aligned_numeric_candidates,
    _borderless_candidates,
    _is_period_token,
    _looks_numeric,
    _period_tokens_in,
    extract_tables_from_page,
)


FONT_SIZE = 9


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _width(text: str, fontsize: int = FONT_SIZE) -> float:
    return pymupdf.get_text_length(text, fontsize=fontsize)


def _centered(x_center: float, text: str, fontsize: int = FONT_SIZE):
    return (x_center - _width(text, fontsize) / 2, text)


def _right(x_right: float, text: str, fontsize: int = FONT_SIZE):
    return (x_right - _width(text, fontsize), text)


def _at(x_left: float, text: str, fontsize: int = FONT_SIZE):
    return (x_left, text)


def _write(page, rows, fontsize: int = FONT_SIZE) -> None:
    for y, items in rows:
        for x, text in items:
            page.insert_text((x, y), text, fontsize=fontsize)


def _cells(candidate):
    return {(cell.row_index, cell.column_index): cell for cell in candidate.cells}


def _geometry_candidates(result):
    return [
        candidate for candidate in result.candidates
        if candidate.detection_method == TableDetectionMethod.WORD_GEOMETRY
    ]


def _synthetic_words(rows, height=9.0):
    """Build the ``(words, heights)`` input of the aligned-geometry detector.

    ``rows`` is a list of ``(y, [(x0, text, width), ...])`` entries.
    """
    words = []
    for y, items in rows:
        for x0, text, width in items:
            words.append(((x0, y, x0 + width, y + height), text))
    return words, [height] * len(words)


def _right_word(x_right, text, height=9.0):
    width = 6.0 * len(text)
    return (x_right - width, text, width)


def _centered_word(x_center, text, height=9.0):
    width = 6.0 * len(text)
    return (x_center - width / 2, text, width)


def _label_word(x_left, text, height=9.0):
    return (x_left, text, 6.0 * len(text))


def _rows_to_words(rows, height=9.0):
    return _synthetic_words(rows, height)


# ---------------------------------------------------------------------------
# B2-1 - independently separated blocks must not be fused
# ---------------------------------------------------------------------------


def test_two_headerless_blocks_with_few_rows_do_not_fuse_into_one_candidate():
    """B2-1: two headerless blocks, few rows, a large gap between them.

    The audited case: a two-row block and a one-row block, 200 points apart.
    Neither block independently satisfies the detector's minimum evidence, so
    abstention is the correct outcome. Fusing them into one confident
    candidate is not.
    """
    rows = [
        (40, [_label_word(30.0, "Revenue"), _right_word(250.0, "1200"), _right_word(350.0, "1450")]),
        (52, [_label_word(30.0, "Profit"), _right_word(250.0, "25"), _right_word(350.0, "38")]),
        (252, [_label_word(30.0, "Cash"), _right_word(250.0, "900"), _right_word(350.0, "950")]),
    ]
    words, heights = _rows_to_words(rows)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    for candidate in candidates:
        top = candidate.bbox[1]
        bottom = candidate.bbox[3]
        assert not (top < 130 < bottom), (
            "a candidate spans both independent blocks: "
            f"bbox=({candidate.bbox[0]:.0f},{top:.0f},{candidate.bbox[2]:.0f},{bottom:.0f})"
        )
    # Fusing is not acceptable; abstention is.
    assert len(candidates) <= 1
    if candidates:
        assert candidates[0].bbox[1] > 130 or candidates[0].bbox[3] < 130


def test_two_headerless_tables_with_enough_rows_produce_two_candidates():
    """B2-1: both blocks independently qualify, so they must be two candidates."""
    rows = [
        (40, [_label_word(30.0, "Revenue"), _right_word(250.0, "1200"), _right_word(350.0, "1450")]),
        (52, [_label_word(30.0, "Profit"), _right_word(250.0, "25"), _right_word(350.0, "38")]),
        (64, [_label_word(30.0, "Margin"), _right_word(250.0, "-2.1"), _right_word(350.0, "2.6")]),
        (264, [_label_word(30.0, "Revenue"), _right_word(250.0, "1300"), _right_word(350.0, "1500")]),
        (276, [_label_word(30.0, "Profit"), _right_word(250.0, "26"), _right_word(350.0, "39")]),
        (288, [_label_word(30.0, "Margin"), _right_word(250.0, "-2.0"), _right_word(350.0, "2.7")]),
    ]
    words, heights = _rows_to_words(rows)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 2, [
        (round(c.bbox[1]), round(c.bbox[3]), [d.code for d in c.diagnostics]) for c in candidates
    ]
    upper, lower = sorted(candidates, key=lambda c: c.bbox[1])
    assert upper.bbox[3] < lower.bbox[1]
    upper_text = {cell.text for cell in upper.cells}
    lower_text = {cell.text for cell in lower.cells}
    assert upper_text == {"Revenue", "Profit", "Margin", "1200", "1450", "25", "38", "-2.1", "2.6"}
    assert lower_text == {"Revenue", "Profit", "Margin", "1300", "1500", "26", "39", "-2.0", "2.7"}
    # The two tables repeat the same row labels but must not share any value.
    assert not ({"1200", "1450", "25", "38", "-2.1", "2.6"} & lower_text)
    assert not ({"1300", "1500", "26", "39", "-2.0", "2.7"} & upper_text)


def test_two_headerless_blocks_do_not_fuse_in_the_borderless_path():
    """B2-1 through the public adapter, on a bordered-style gap layout."""
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=400)
    rows = [
        (40, [_at(30, "Revenue"), _right(250, "1200"), _right(350, "1450")]),
        (52, [_at(30, "Profit"), _right(250, "25"), _right(350, "38")]),
        (64, [_at(30, "Margin"), _right(250, "-2.1"), _right(350, "2.6")]),
        (264, [_at(30, "Revenue"), _right(250, "1300"), _right(350, "1500")]),
        (276, [_at(30, "Profit"), _right(250, "26"), _right(350, "39")]),
        (288, [_at(30, "Margin"), _right(250, "-2.0"), _right(350, "2.7")]),
    ]
    _write(page, rows)
    try:
        result = extract_tables_from_page(page, page_number=1)
        candidates = _geometry_candidates(result)
        for candidate in candidates:
            top, bottom = candidate.bbox[1], candidate.bbox[3]
            assert not (top < 130 < bottom), (
                f"a candidate spans both blocks: bbox=({top:.0f}..{bottom:.0f})"
            )
        if len(candidates) == 2:
            upper, lower = sorted(candidates, key=lambda c: c.bbox[1])
            assert upper.bbox[3] < lower.bbox[1]
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# B2-2 - a loosely spaced table must not disappear beside a dense table
# ---------------------------------------------------------------------------


def test_loose_table_survives_next_to_a_dense_table_on_the_same_page():
    """B2-2: detection must not depend on an unrelated table's row pitch."""
    rows = [
        (40, [_label_word(30.0, "Measure"), _centered_word(200.0, "2023"),
              _centered_word(300.0, "2024")]),
    ]
    y = 52
    for label, values in (("Revenue", ("1200", "1450")), ("Profit", ("25", "38")),
                           ("Margin", ("-2.1", "2.6")), ("Tax", ("10", "12")),
                           ("Capex", ("5", "6"))):
        rows.append((y, [_label_word(30.0, label), _right_word(250.0, values[0]),
                          _right_word(350.0, values[1])]))
        y += 12
    rows.append((400, [_label_word(30.0, "Measure"), _centered_word(200.0, "2025"),
                        _centered_word(300.0, "2026")]))
    for offset, (label, values) in enumerate(
        (("Revenue", ("1300", "1500")), ("Profit", ("26", "39")),
         ("Margin", ("-2.0", "2.7"))), start=1
    ):
        rows.append((400 + offset * 56, [_label_word(30.0, label),
                                           _right_word(250.0, values[0]),
                                           _right_word(350.0, values[1])]))
    words, heights = _rows_to_words(rows)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 2, [
        (round(c.bbox[1]), round(c.bbox[3])) for c in candidates
    ]
    upper, lower = sorted(candidates, key=lambda c: c.bbox[1])
    assert upper.bbox[3] < lower.bbox[1]
    lower_text = {cell.text for cell in lower.cells}
    assert {"2025", "2026"} <= lower_text
    assert "1300" in lower_text


def test_loose_table_survives_next_to_a_dense_table_through_the_public_adapter():
    """B2-2 through the public adapter, using real font metrics."""
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=800)
    rows = [(40, [_at(30, "Measure"), _centered(200, "2023"), _centered(300, "2024")])]
    y = 52
    for label, values in (("Revenue", ("1200", "1450")), ("Profit", ("25", "38")),
                           ("Margin", ("-2.1", "2.6")), ("Tax", ("10", "12")),
                           ("Capex", ("5", "6"))):
        rows.append((y, [_at(30, label), _right(250, values[0]), _right(350, values[1])]))
        y += 12
    rows.append((400, [_at(30, "Measure"), _centered(200, "2025"), _centered(300, "2026")]))
    for offset, (label, values) in enumerate(
        (("Revenue", ("1300", "1500")), ("Profit", ("26", "39")),
         ("Margin", ("-2.0", "2.7"))), start=1
    ):
        rows.append((400 + offset * 56, [_at(30, label), _right(250, values[0]),
                                         _right(350, values[1])]))
    _write(page, rows)
    try:
        result = extract_tables_from_page(page, page_number=1)
        candidates = _geometry_candidates(result)
        assert len(candidates) == 2, [
            (round(c.bbox[1]), round(c.bbox[3])) for c in candidates
        ]
        upper, lower = sorted(candidates, key=lambda c: c.bbox[1])
        assert upper.bbox[3] < lower.bbox[1]
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# B2-3 - candidate bounds must not claim another table's row region
# ---------------------------------------------------------------------------


def test_page_grid_header_does_not_make_a_candidate_span_the_other_table():
    """B2-3: a distant page-wide header may be referenced but must not enlarge
    the candidate's region beyond its own rows."""
    rows = [
        (40, [_label_word(30.0, "Measure"), _centered_word(200.0, "2023"),
              _centered_word(300.0, "2024")]),
        (52, [_label_word(30.0, "Revenue"), _right_word(250.0, "1200"), _right_word(350.0, "1450")]),
        (64, [_label_word(30.0, "Profit"), _right_word(250.0, "25"), _right_word(350.0, "38")]),
        (76, [_label_word(30.0, "Margin"), _right_word(250.0, "-2.1"), _right_word(350.0, "2.6")]),
        (250, [_label_word(30.0, "Revenue"), _right_word(250.0, "1300"), _right_word(350.0, "1500")]),
        (262, [_label_word(30.0, "Profit"), _right_word(250.0, "26"), _right_word(350.0, "39")]),
        (274, [_label_word(30.0, "Margin"), _right_word(250.0, "-2.0"), _right_word(350.0, "2.7")]),
    ]
    words, heights = _rows_to_words(rows)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 2, [round(c.bbox[1]) for c in candidates]
    upper, lower = sorted(candidates, key=lambda c: c.bbox[1])
    # The two candidates must not claim overlapping row regions.
    assert upper.bbox[3] < lower.bbox[1], (
        f"candidate bounds overlap: {upper.bbox} and {lower.bbox}"
    )
    # The distant header must still be recorded as a source location.
    lower_cells = _cells(lower)
    header_cells = [cell for cell in lower.cells if cell.text in {"2023", "2024"}]
    assert header_cells, "the page-wide header source was lost"
    for cell in header_cells:
        assert cell.bbox is not None
        assert cell.bbox[3] < lower.bbox[1] or cell.bbox[1] >= lower.bbox[1]
    assert lower_cells


# ---------------------------------------------------------------------------
# N3 - unjustified period header assignments must be reported
# ---------------------------------------------------------------------------


def test_competing_period_tokens_between_two_columns_are_reported_as_ambiguous():
    """N3: two header tokens crowded between the same pair of value columns.

    Their geometry does not justify one token per column, so the association
    must be reported as a conflict and no header may be attached.
    """
    rows = [
        (40, [_label_word(30.0, "Measure"), _centered_word(300.0, "2023"),
              _centered_word(320.0, "2024")]),
        (52, [_label_word(30.0, "Revenue"), _right_word(250.0, "1200"), _right_word(350.0, "1450")]),
        (64, [_label_word(30.0, "Profit"), _right_word(250.0, "25"), _right_word(350.0, "38")]),
        (76, [_label_word(30.0, "Margin"), _right_word(250.0, "-2.1"), _right_word(350.0, "2.6")]),
    ]
    words, heights = _rows_to_words(rows)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 1
    candidate = candidates[0]
    codes = {diagnostic.code for diagnostic in candidate.diagnostics}
    assert "period_header_conflict" in codes, codes
    # No header may be attached when the association is not justified.
    assert not [cell for cell in candidate.cells if _is_period_token(cell.text)]


def test_a_correctly_aligned_centred_header_is_still_accepted():
    """N3 guard: the ordinary centred-header case must keep its header."""
    rows = [
        (40, [_label_word(30.0, "Measure"), _centered_word(200.0, "2023"),
              _centered_word(300.0, "2024")]),
        (52, [_label_word(30.0, "Revenue"), _right_word(250.0, "1200"), _right_word(350.0, "1450")]),
        (64, [_label_word(30.0, "Profit"), _right_word(250.0, "25"), _right_word(350.0, "38")]),
        (76, [_label_word(30.0, "Margin"), _right_word(250.0, "-2.1"), _right_word(350.0, "2.6")]),
    ]
    words, heights = _rows_to_words(rows)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 1
    candidate = candidates[0]
    cells = _cells(candidate)
    assert cells[(0, 1)].text == "2023"
    assert cells[(0, 2)].text == "2024"
    codes = {diagnostic.code for diagnostic in candidate.diagnostics}
    assert "period_header_conflict" not in codes, codes


def test_a_nearby_header_within_the_tolerance_is_still_accepted():
    """N3 guard: a header slightly offset from its column stays accepted."""
    rows = [
        (40, [_label_word(30.0, "Measure"), _centered_word(205.0, "2023"),
              _centered_word(305.0, "2024")]),
        (52, [_label_word(30.0, "Revenue"), _right_word(250.0, "1200"), _right_word(350.0, "1450")]),
        (64, [_label_word(30.0, "Profit"), _right_word(250.0, "25"), _right_word(350.0, "38")]),
        (76, [_label_word(30.0, "Margin"), _right_word(250.0, "-2.1"), _right_word(350.0, "2.6")]),
    ]
    words, heights = _rows_to_words(rows)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 1
    candidate = candidates[0]
    cells = _cells(candidate)
    assert cells[(0, 1)].text == "2023"
    assert cells[(0, 2)].text == "2024"


# ---------------------------------------------------------------------------
# B1-a - quarterly period labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    ["Q4 2024", "Q1 2024", "Q4 FY2024", "Q1 FY24", "Q4*", "Q4 2024*", "Q3 2023"],
)
def test_quarterly_period_labels_are_recognised(token):
    assert _is_period_token(token), token
    assert not _looks_numeric(token), token


@pytest.mark.parametrize(
    "token",
    ["Q5 2024", "Q 4 2024", "Q4 20245", "Q4Q1", "quarter 2024", "Q4 20a4"],
)
def test_non_quarterly_tokens_are_not_promoted_to_periods(token):
    assert not _is_period_token(token), token


@pytest.mark.parametrize("token", ["1200", "-2.1%", "£38m", "Q5 2024", "Q 4 2024"])
def test_values_and_malformed_labels_are_not_classified_as_periods(token):
    assert not _is_period_token(token), token


def test_quarterly_period_header_is_mapped_to_its_column():
    """A quarterly header must be usable as a period header."""
    rows = [
        (40, [_label_word(30.0, "Measure"), _centered_word(200.0, "Q4 2024"),
              _centered_word(300.0, "Q4 2025")]),
        (52, [_label_word(30.0, "Revenue"), _right_word(250.0, "1200"), _right_word(350.0, "1450")]),
        (64, [_label_word(30.0, "Profit"), _right_word(250.0, "25"), _right_word(350.0, "38")]),
        (76, [_label_word(30.0, "Margin"), _right_word(250.0, "-2.1"), _right_word(350.0, "2.6")]),
    ]
    words, heights = _rows_to_words(rows)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 1
    cells = _cells(candidates[0])
    assert cells[(0, 1)].text == "Q4 2024"
    assert cells[(0, 2)].text == "Q4 2025"


# ---------------------------------------------------------------------------
# B1-a continued - quarterly labels printed as several words stay one label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Q4 2024", ["Q4 2024"]),
        ("Q1 2024", ["Q1 2024"]),
        ("Q4 FY2024", ["Q4 FY2024"]),
        ("Q4 FY 2024", ["Q4 FY 2024"]),
        ("Q1 FY24", ["Q1 FY24"]),
        ("Q4*", ["Q4*"]),
        ("Q4 2024*", ["Q4 2024*"]),
        ("Q3 2023", ["Q3 2023"]),
        ("2023 2024", ["2023", "2024"]),
        ("Q4 2024 2025", ["Q4 2024", "2025"]),
        ("Q4* 2024", ["Q4*", "2024"]),
        ("", []),
        ("Revenue", []),
    ],
)
def test_period_labels_are_parsed_as_whole_labels(text, expected):
    """A cell's period labels are parsed left to right as whole labels.

    Splitting on whitespace would read ``Q4 2024`` as the two labels
    ``Q4`` and ``2024`` and wrongly report the cell as ambiguous.
    """
    assert _period_tokens_in(text) == expected, text


def test_quarterly_period_header_printed_as_two_words_is_mapped_in_the_aligned_path():
    """Real PDFs print ``Q4`` and ``2024`` as separate words; they form one label."""
    rows = [
        (40, [_label_word(30.0, "Measure"), _centered_word(194.0, "Q4"),
              _centered_word(206.0, "2024"), _centered_word(294.0, "Q4"),
              _centered_word(306.0, "2025")]),
        (52, [_label_word(30.0, "Revenue"), _right_word(250.0, "1200"),
              _right_word(350.0, "1450")]),
        (64, [_label_word(30.0, "Profit"), _right_word(250.0, "25"),
              _right_word(350.0, "38")]),
        (76, [_label_word(30.0, "Margin"), _right_word(250.0, "-2.1"),
              _right_word(350.0, "2.6")]),
    ]
    words, heights = _rows_to_words(rows)
    candidates, diagnostics = _aligned_numeric_candidates(1, words, heights, [])
    assert len(candidates) == 1, ([d.code for d in diagnostics], candidates)
    cells = _cells(candidates[0])
    assert cells[(0, 1)].text == "Q4 2024", cells
    assert cells[(0, 2)].text == "Q4 2025", cells
    assert not any(d.code == "period_header_conflict" for d in candidates[0].diagnostics), (
        [d.code for d in candidates[0].diagnostics])


def _raw_row_words(rows):
    """Convert ``(y, [(x0, text, width), ...])`` rows to raw word tuples."""
    words = []
    for y, items in rows:
        for item in items:
            if len(item) == 3:
                x0, text, width = item
            else:
                x0, text = item
                width = 6.0 * len(text)
            words.append((x0, y, x0 + width, y + 9, text, 0, 0, 0))
    return words


def test_quarterly_period_header_printed_as_two_words_is_mapped_in_the_borderless_path():
    """The borderless path must not raise a false conflict for split words."""
    rows = [
        (40, [_label_word(30.0, "Measure"), _centered_word(194.0, "Q4"),
              _centered_word(206.0, "2024"), _centered_word(294.0, "Q4"),
              _centered_word(306.0, "2025")]),
        (52, [_label_word(30.0, "Revenue"), _right_word(250.0, "1200"),
              _right_word(350.0, "1450")]),
        (64, [_label_word(30.0, "Profit"), _right_word(250.0, "25"),
              _right_word(350.0, "38")]),
        (76, [_label_word(30.0, "Margin"), _right_word(250.0, "-2.1"),
              _right_word(350.0, "2.6")]),
    ]
    candidates, diagnostics = _borderless_candidates(None, 1, _raw_row_words(rows), [])
    assert len(candidates) == 1, ([d.code for d in diagnostics], candidates)
    cells = _cells(candidates[0])
    assert cells[(0, 1)].text == "Q4 2024", cells
    assert cells[(0, 2)].text == "Q4 2025", cells
    assert not any(d.code == "period_header_conflict" for d in candidates[0].diagnostics), (
        [d.code for d in candidates[0].diagnostics])


def test_a_cell_carrying_two_period_labels_stays_ambiguous_in_the_borderless_path():
    """One cell holding two period labels cannot label a single column.

    ``2023 2024`` is two labels in one cell, so the header stays unresolved
    and the conflict is reported instead of being silently assigned.
    """
    rows = [
        (40, [_label_word(30.0, "Measure"), _at(180.0, "2023"), _at(192.0, "2024"),
              _at(280.0, "2025")]),
        (52, [_label_word(30.0, "Revenue"), _right_word(250.0, "1200"),
              _right_word(350.0, "1450")]),
        (64, [_label_word(30.0, "Profit"), _right_word(250.0, "25"),
              _right_word(350.0, "38")]),
    ]
    candidates, diagnostics = _borderless_candidates(None, 1, _raw_row_words(rows), [])
    assert len(candidates) == 1, ([d.code for d in diagnostics], candidates)
    cells = _cells(candidates[0])
    assert cells[(0, 1)].text == "2023 2024", cells
    assert cells[(0, 2)].text == "2025", cells
    assert any(d.code == "period_header_conflict" for d in candidates[0].diagnostics), (
        [d.code for d in candidates[0].diagnostics])


def test_quarterly_split_word_headers_associated_without_conflict():
    """Reproduce F-1 / §6.2-6.3 vs §14 contradiction independently.

    Realistic centered Q4 2024 / Q4 2025 headers (split-word geometry) are
    correctly parsed as coherent quarter/year labels by _period_tokens_in and
    correctly associated by both paths. Independent reproduction across
    aligned, borderless, and direct adapter paths shows
    period_header_conflict=False. The audit §14 claim that both paths emit
    period_header_conflict for split-word quarterly headers is not
    reproducible; this regression asserts the verified outcome.
    Genuine ambiguity (multi-label cell 2023 2024, crowded/offset headers)
    remains unresolved per F-2.
    """
    from tests.test_table_adapter_bridges import _header_row, _data_rows, _at, _right, VALUES, _words_from, _raw_words
    # Fixture: centered quarterly headers with numeric data
    rows = [_header_row(("Q4 2024", "Q4 2025"), centres=(200.0, 300.0))]
    rows += _data_rows(52.0, 12.0, list(VALUES))
    words, heights = _words_from(rows)
    aligned_cands, aligned_diags = _aligned_numeric_candidates(1, words, heights, [])
    border_cands, _ = _borderless_candidates(None, 1, _raw_words(rows), [])
    for label, cands, diags in (("aligned", aligned_cands, aligned_diags), ("borderless", border_cands, [])):
        assert len(cands) == 1, (label, "expected one candidate for coherent quarterly headers", cands)
        diag_codes = [d.code for d in (cands[0].diagnostics if cands else [])] + [d.code for d in diags]
        assert "period_header_conflict" not in diag_codes, (label, "quarterly headers should not conflict", diag_codes)
        texts = {cell.text for cell in cands[0].cells}
        # Coherent quarter/year labels retained as semantic tokens
        assert any("2024" in t or "2025" in t for t in texts), (label, texts)
    # Multi-label ambiguity remains detected (control)
    assert _period_tokens_in("2023 2024") == ["2023", "2024"]


def test_quarterly_split_word_pdf_fixture_preserved_in_public_adapter(tmp_path):
    """Self-contained split-word quarterly fixture (no external scratch files).

    Builds the PDF in tmp_path, confirms PyMuPDF splits "Q4 2024" into
    quarter/year word tokens with a real ~2.5 pt gap, then verifies the
    borderless and public-adapter paths retain the combined labels.
    """
    import pymupdf
    from app.table_extraction import _borderless_candidates, extract_tables_from_page, _aligned_numeric_candidates
    pdf_path = tmp_path / "quarterly_split.pdf"
    # Realistic split-word phrases with normal space — positioned over verified
    # aligned-path column centres (200/300) with numeric right edges 250/350.
    from tests.test_table_adapter_bridges import _width
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=200)
    for centre, text in ((200, "Q4 2024"), (300, "Q4 2025")):
        w = _width(text, fontsize=9)
        page.insert_text((centre - w/2, 40), text, fontsize=9)
    for y, label, v1, v2 in (
        (52, "Revenue", "1200", "1450"),
        (64, "Profit", "25", "38"),
        (76, "Margin", "-2.1", "2.6"),
    ):
        page.insert_text((30, y), label, fontsize=9)
        for val, right in ((v1, 250), (v2, 350)):
            w = _width(val, fontsize=9)
            page.insert_text((right - w, y), val, fontsize=9)
    doc.save(str(pdf_path))
    doc.close()

    pdf = pymupdf.open(str(pdf_path))
    try:
        page = pdf.load_page(0)
        words = page.get_text("words")
        # Confirm realistic split-word geometry
        header_words = [w for w in words if w[4] in ("Q4", "2024", "Q4", "2025")]
        assert len(header_words) == 4, ("expected 4 header word tokens", [w[4] for w in header_words])
        # Verify split: Q4 then 2024 with small real gap; year not merged with quarter
        q4_first = next(w for w in words if w[4] == "Q4")
        y2024_first = next(w for w in words if w[4] == "2024" and w[0] > q4_first[0])
        gap_q4_2024 = y2024_first[0] - q4_first[2]
        assert 0 < gap_q4_2024 < 10, ("inter-word gap should be realistic (small)", gap_q4_2024)
        # Confirm column separation is large enough to avoid grouping across columns
        q4_first_x = q4_first[0]
        q4_second = next(w for w in words if w[4] == "Q4" and w[0] > q4_first_x)
        inter_col_gap = q4_second[0] - y2024_first[0]
        assert inter_col_gap > 40, ("column separation must be well above grouping threshold", inter_col_gap)

        raw = [(w[0], w[1], w[2], w[3], w[4], 0, 0, 0) for w in words]
        border_cands, _ = _borderless_candidates(None, 1, raw, [])
        assert len(border_cands) >= 1, ("borderless found no candidate")
        texts = {cell.text for cell in border_cands[0].cells}
        assert "Q4 2024" in texts, texts
        assert "Q4 2025" in texts, texts
        diag_codes = [d.code for d in border_cands[0].diagnostics]
        assert "period_header_conflict" not in diag_codes, diag_codes
        # ambiguous_header_span is structural (merged-header span; source line 912);
        # this fixture's header row has fewer occupied cells than the multi-column
        # structure, so the diagnostic is expected and must not suppress association.
        assert "ambiguous_header_span" in diag_codes, ("expected structural merged-header span on this fixture", diag_codes)
        # Column mapping verified directly where adapter exposes column_index.
        border_texts_map = {cell.text: cell for cell in border_cands[0].cells}
        assert "Q4 2024" in border_texts_map, ("borderless missing Q4 2024", border_texts_map)
        assert "Q4 2025" in border_texts_map, ("borderless missing Q4 2025", border_texts_map)
        assert border_texts_map["Q4 2024"].column_index == 1, ("borderless Q4 2024 column mapping", border_texts_map)
        assert border_texts_map["Q4 2025"].column_index == 2, ("borderless Q4 2025 column mapping", border_texts_map)
        numeric_col1_border = [cell.text for cell in border_cands[0].cells if getattr(cell, "column_index", None) == 1 and cell.text not in ("Q4 2024", "Revenue", "Profit", "Margin")]
        numeric_col2_border = [cell.text for cell in border_cands[0].cells if getattr(cell, "column_index", None) == 2 and cell.text not in ("Q4 2025", "Revenue", "Profit", "Margin")]
        assert {"1200", "25", "-2.1"} <= set(numeric_col1_border), numeric_col1_border
        assert {"1450", "38", "2.6"} <= set(numeric_col2_border), numeric_col2_border

        # Aligned path: must produce one candidate with full quarter labels
        aligned_words = [((w[0], w[1], w[2], w[3]), w[4]) for w in words]
        aligned_cands, aligned_diags = _aligned_numeric_candidates(1, aligned_words, [9.0]*len(aligned_words), [])
        assert len(aligned_cands) == 1, ("aligned must find one candidate for complete quarterly header", len(aligned_cands), aligned_diags)
        a = aligned_cands[0]
        a_texts = {cell.text for cell in a.cells}
        assert "Q4 2024" in a_texts, ("quarter token dropped; only year remains", a_texts)
        assert "Q4 2025" in a_texts, ("quarter token dropped; only year remains", a_texts)
        a_diag = [d.code for d in a.diagnostics]
        assert "period_header_conflict" not in a_diag, ("conflict should not occur for unambiguous quarterly header", a_diag)
        assert "period_header_incomplete" not in a_diag, ("complete header should not trigger incomplete", a_diag)
        # Header-to-column association verified via cell presence; coupled with
        # period_headers_associated_by_alignment / period_headers_associated_by_page_grid
        # diagnostics showing association is established.
        assert any("period_headers_associated" in d for d in a_diag), ("header association missing", a_diag)
        # Explicit column mapping: Q4 2024 -> column 1 (1200, 25, -2.1);
        # Q4 2025 -> column 2 (1450, 38, 2.6)
        header_by_text = {cell.text: cell for cell in a.cells if cell.text in ("Q4 2024", "Q4 2025")}
        assert "Q4 2024" in header_by_text and header_by_text["Q4 2024"].column_index == 1, ("first quarter must map to first numeric column", header_by_text)
        assert "Q4 2025" in header_by_text and header_by_text["Q4 2025"].column_index == 2, ("second quarter must map to second numeric column", header_by_text)
        numeric_col1 = [cell.text for cell in a.cells if cell.column_index == 1 and cell.text not in ("Q4 2024", "Revenue", "Profit", "Margin")]
        numeric_col2 = [cell.text for cell in a.cells if cell.column_index == 2 and cell.text not in ("Q4 2025", "Revenue", "Profit", "Margin")]
        assert {"1200", "25", "-2.1"} <= set(numeric_col1), ("first numeric column missing expected values", numeric_col1)
        assert {"1450", "38", "2.6"} <= set(numeric_col2), ("second numeric column missing expected values", numeric_col2)

        # Public adapter
        public = extract_tables_from_page(page, 1)
        assert len(public.candidates) >= 1, ("public adapter found no candidate")
        pub_texts = {cell.text for cell in public.candidates[0].cells}
        assert "Q4 2024" in pub_texts, pub_texts
        assert "Q4 2025" in pub_texts, pub_texts
        pub_header_map = {cell.text: cell for cell in public.candidates[0].cells if cell.text in ("Q4 2024", "Q4 2025") and hasattr(cell, "column_index")}
        assert "Q4 2024" in pub_header_map, ("public adapter missing Q4 2024 cell", pub_texts)
        assert "Q4 2025" in pub_header_map, ("public adapter missing Q4 2025 cell", pub_texts)
        assert pub_header_map["Q4 2024"].column_index == 1, ("public Q4 2024 column mapping", pub_header_map)
        assert pub_header_map["Q4 2025"].column_index == 2, ("public Q4 2025 column mapping", pub_header_map)
        # Numeric column presence verified directly on public candidate cells
        pub_numeric_col1 = [cell.text for cell in public.candidates[0].cells if getattr(cell, "column_index", None) == 1 and cell.text not in ("Q4 2024", "Revenue", "Profit", "Margin")]
        pub_numeric_col2 = [cell.text for cell in public.candidates[0].cells if getattr(cell, "column_index", None) == 2 and cell.text not in ("Q4 2025", "Revenue", "Profit", "Margin")]
        assert {"1200", "25", "-2.1"} <= set(pub_numeric_col1), ("public numeric col 1 missing values", pub_numeric_col1)
        assert {"1450", "38", "2.6"} <= set(pub_numeric_col2), ("public numeric col 2 missing values", pub_numeric_col2)
    finally:
        pdf.close()
