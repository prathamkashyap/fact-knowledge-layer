"""Focused regression tests for heading bridges, header ambiguity, thresholds.

These pin the behaviour that the independent audit of commit 3c490a4 required:

* F1 - a heading bridge has its own, more permissive tolerance. A table with an
  internal subheading must not be fragmented because the bridge is judged by
  the row-separation rule.
* F2 - the borderless path must apply the same header-ambiguity policy as the
  aligned path: a period header whose column assignment the geometry cannot
  justify is reported, not silently accepted.
* F3 - the separation and header-offset thresholds are pinned on both sides,
  and the few-row borderless fusion case is covered directly.

The synthetic layouts used here are committed so that the behaviour can be
reviewed without recreating geometry fixtures from a scratch script.
"""

import pymupdf
import pytest

from app.table_extraction import (
    TableDetectionMethod,
    _aligned_numeric_candidates,
    _borderless_candidates,
    _is_period_token,
    extract_tables_from_page,
)


FONT_SIZE = 9
TEXT_HEIGHT = 9.0


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def _width(text: str, fontsize: int = FONT_SIZE) -> float:
    return pymupdf.get_text_length(text, fontsize=fontsize)


def _at(x_left: float, text: str):
    return (x_left, text, 6.0 * len(text))


def _right(x_right: float, text: str):
    width = 6.0 * len(text)
    return (x_right - width, text, width)


def _centre(x_centre: float, text: str):
    width = 6.0 * len(text)
    return (x_centre - width / 2, text, width)


def _words_from(rows, height=TEXT_HEIGHT):
    """``rows`` is a list of ``(y, [(x0, text, width), ...])`` entries."""
    words = []
    for y, items in rows:
        for x0, text, width in items:
            words.append(((x0, y, x0 + width, y + height), text))
    return words, [height] * len(words)


def _raw_words(rows, height=TEXT_HEIGHT):
    """The same layout expressed as PyMuPDF-style word tuples."""
    words, _ = _words_from(rows, height)
    return [(box[0], box[1], box[2], box[3], text, 0, 0, 0) for box, text in words]


def _write(page, rows):
    for y, items in rows:
        for x0, text, width in items:
            page.insert_text((x0, y), text, fontsize=FONT_SIZE)


def _aligned(rows):
    words, heights = _words_from(rows)
    candidates, _ = _aligned_numeric_candidates(1, words, heights, [])
    return [
        (
            (round(candidate.bbox[1]), round(candidate.bbox[3])),
            sorted({cell.text for cell in candidate.cells}),
            sorted({diagnostic.code for diagnostic in candidate.diagnostics}),
        )
        for candidate in candidates
    ]


def _borderless(rows):
    candidates, _ = _borderless_candidates(None, 1, _raw_words(rows), [])
    return [
        (
            (round(candidate.bbox[1]), round(candidate.bbox[3])),
            sorted({cell.text for cell in candidate.cells}),
            sorted({diagnostic.code for diagnostic in candidate.diagnostics}),
        )
        for candidate in candidates
    ]


def _header_row(headers, centres=(200.0, 300.0), y=40.0):
    return (y, [_at(30.0, "Measure")]
            + [_centre(centre, header) for centre, header in zip(centres, headers)])


def _data_rows(y_start, pitch, rows):
    out = []
    for offset, (label, values) in enumerate(rows, start=1):
        out.append((y_start + offset * pitch,
                    [_at(30.0, label), _right(250.0, values[0]), _right(350.0, values[1])]))
    return out


VALUES = (("Revenue", ("1200", "1450")), ("Profit", ("25", "38")), ("Margin", ("-2.1", "2.6")))


def _subheading_table(pitch=12.0, blank_lines=0):
    """A table whose second half starts after a subheading.

    ``blank_lines`` is the number of empty lines between the subheading and
    the rows that follow it.
    """
    rows = [_header_row(("2023", "2024"))]
    rows += _data_rows(40.0, pitch, list(VALUES[:2]))
    subheading_y = 40.0 + 3 * pitch + blank_lines * pitch
    rows.append((subheading_y, [_at(30.0, "SEGMENT BETA")]))
    rows += _data_rows(subheading_y, pitch, [(VALUES[2][0], ("50", "55"))])
    rows.append((subheading_y + 2 * pitch,
                 [_at(30.0, "Tax"), _right(250.0, "10"), _right(350.0, "12")]))
    return rows


# ---------------------------------------------------------------------------
# F1 - heading bridges keep their own tolerance
# ---------------------------------------------------------------------------


def test_table_without_a_subheading_is_one_candidate():
    """Control: an ordinary table is unaffected by the bridge rules."""
    rows = [_header_row(("2023", "2024"))] + _data_rows(40.0, 12.0, list(VALUES))
    for name, run in (("aligned", _aligned), ("borderless", _borderless)):
        candidates = run(rows)
        assert len(candidates) == 1, (name, candidates)
        assert {"1200", "1450", "25", "38", "-2.1", "2.6"} <= set(candidates[0][1]), (
            name, candidates)


def test_subheading_one_blank_line_keeps_one_candidate():
    """One empty line between the subheading and the rows below it.

    The record-to-record span across the subheading is three times the row
    pitch, which is the row-separation boundary; the table stays whole.
    """
    rows = _subheading_table(pitch=12.0, blank_lines=1)
    for name, run in (("aligned", _aligned), ("borderless", _borderless)):
        candidates = run(rows)
        assert len(candidates) == 1, (name, candidates)
        texts = candidates[0][1]
        assert "SEGMENT BETA" in texts, (name, texts)
        assert {"1200", "1450", "25", "38", "50", "55", "10", "12"} <= set(texts), (
            name, texts)


def test_subheading_two_blank_lines_keeps_one_candidate():
    """Two empty lines: the bridge must still hold the table together.

    The record-to-record span across the subheading is four times the row
    pitch. This is the layout the independent audit found fragmented in
    3c490a4, where the row-separation rule also governed the bridge.
    """
    rows = _subheading_table(pitch=12.0, blank_lines=2)
    for name, run in (("aligned", _aligned), ("borderless", _borderless)):
        candidates = run(rows)
        assert len(candidates) == 1, (name, candidates)
        texts = candidates[0][1]
        assert "SEGMENT BETA" in texts, (name, texts)
        assert {"1200", "1450", "25", "38", "50", "55", "10", "12"} <= set(texts), (
            name, texts)


def test_bridge_ceiling_is_pinned_on_both_sides():
    """The bridge ceiling pins the subheading bridge on both sides.

    ``_bridge_ceiling`` is the larger of 3.5 x the local row pitch and 5 x the
    text height. With a 12 pt pitch and 9 pt text that is 45.0 pt, measured
    from the bottom of the row above the bridge to the top of the row below it.
    At the ceiling (gap 45.0) the bridge still holds both blocks together; just
    beyond it (gap 45.5) the aligned path abstains and the borderless path
    keeps only the upper block. Rows are never fabricated to span the split.
    """
    import app.table_extraction as te

    assert te._bridge_ceiling(12.0, 9.0) == 45.0

    def build(vertical_gap: float):
        rows = [_header_row(("2023", "2024"))]
        rows += _data_rows(40.0, 12.0, VALUES[:2])
        subheading_y = 73.0 + vertical_gap / 2.0
        margin_y = 73.0 + vertical_gap
        rows.append((subheading_y, [_at(30.0, "SEGMENT BETA")]))
        rows.append((margin_y, [_at(30.0, "Margin"), _right(250.0, "-2.1"), _right(350.0, "2.6")]))
        rows.append((margin_y + 12.0, [_at(30.0, "Tax"), _right(250.0, "10"), _right(350.0, "12")]))
        return rows, subheading_y, margin_y

    # Independent fixture-geometry assertions (do not derive result from thresholds)
    for vertical_gap in (30.0, 45.0, 45.5):
        rows, subheading_y, margin_y = build(vertical_gap)
        # Subheading sits halfway between block end (73.0) and margin start (73+gap)
        assert subheading_y == 73.0 + vertical_gap / 2.0, (vertical_gap, subheading_y)
        assert margin_y == 73.0 + vertical_gap, (vertical_gap, margin_y)
        # Margin and following rows are spaced by the local pitch (12 pt)
        tax_y = margin_y + 12.0
        assert abs(tax_y - (73.0 + vertical_gap + 12.0)) < 0.01, (vertical_gap, tax_y)
        # Fixture exercises bridge logic: subheading is between two numeric blocks
        texts = [text for _, items in rows for _, text, _ in items]
        assert "SEGMENT BETA" in texts, (vertical_gap, texts)
        assert "Margin" in texts, (vertical_gap, texts)

    # Below-ceiling control: small gap -> bridge fully holds (both paths fused)
    rows, _, _ = build(30.0)
    words, heights = _words_from(rows)
    aligned_cands, aligned_diags = _aligned_numeric_candidates(1, words, heights, [])
    border_cands, _ = _borderless_candidates(None, 1, _raw_words(rows), [])
    assert len(aligned_cands) == 1 and len(border_cands) == 1, (30.0, len(aligned_cands), len(border_cands))
    for cands in (aligned_cands, border_cands):
        c = cands[0]
        texts = {cell.text for cell in c.cells}
        assert "SEGMENT BETA" in texts, (30.0, texts)
        # Bridge logic exercised: subheading retained inside one candidate
        assert c.bbox[1] < 73.0 + 30.0 / 2.0, (30.0, c.bbox)
        assert c.bbox[3] > 73.0 + 30.0 / 2.0, (30.0, c.bbox)

    # At inclusive ceiling (45.0 pt gap) the bridge still holds both sides
    rows, _, _ = build(45.0)
    words, heights = _words_from(rows)
    aligned_cands, aligned_diags = _aligned_numeric_candidates(1, words, heights, [])
    border_cands, _ = _borderless_candidates(None, 1, _raw_words(rows), [])
    assert len(aligned_cands) == 1, (45.0, "aligned", aligned_cands)
    assert len(border_cands) == 1, (45.0, "borderless", border_cands)
    for label, cands in (("aligned", aligned_cands), ("borderless", border_cands)):
        c = cands[0]
        texts = {cell.text for cell in c.cells}
        assert "SEGMENT BETA" in texts, (label, 45.0, texts)
        # Bridge holds at ceiling: candidate spans across subheading, includes lower numeric rows
        assert "-2.1" in texts and "10" in texts and "12" in texts, (label, 45.0, texts)
        assert "1200" in texts and "1450" in texts, (label, 45.0, texts)
        # Bridge decision verified directly: candidate spans across the subheading
        assert c.bbox[1] < 73.0 + 45.0 / 2.0, (label, 45.0, c.bbox)
        assert c.bbox[3] > 73.0 + 45.0 / 2.0, (label, 45.0, c.bbox)

    # Just above ceiling (45.5 pt): aligned abstains, borderless keeps upper block only
    rows, _, _ = build(45.5)
    words, heights = _words_from(rows)
    aligned_cands, aligned_diags = _aligned_numeric_candidates(1, words, heights, [])
    border_cands, _ = _borderless_candidates(None, 1, _raw_words(rows), [])
    # Aligned path abstains (not inventing rows across the split)
    assert aligned_cands == [], (45.5, "aligned candidates non-empty", aligned_cands)
    assert any(d.code in ("aligned_numeric_block_dropped",
                          "aligned_numeric_candidate_abstained")
               for d in aligned_diags), (45.5, "aligned missing abstention diag", aligned_diags)
    # Borderless keeps only upper block; subheading and lower rows excluded
    assert len(border_cands) == 1, (45.5, "borderless count", len(border_cands))
    c = border_cands[0]
    texts = {cell.text for cell in c.cells}
    assert "1200" in texts and "1450" in texts, (45.5, texts)
    assert "Measure" in texts or "Profit" in texts or "Revenue" in texts, (45.5, texts)
    assert "SEGMENT BETA" not in texts, (45.5, "subheading leaked", texts)
    # Lower block excluded (no Margin/Tax numeric values from below the split)
    assert "-2.1" not in texts, (45.5, "lower margin leaked", texts)
    assert "10" not in texts or "12" not in texts, (45.5, "lower tax leaked", texts)
    # Bridge decision verified: candidate does not claim rows below the bridge
    assert c.bbox[3] <= 73.0 + 45.5 / 2.0, (45.5, "bbox crosses split", c.bbox)


def test_two_headerless_blocks_separated_by_a_heading_inside_the_fusion_window_fuse():
    """F-3 - the widened fusion window (inherent to the F1 fix).

    Two headerless row blocks separated by a single heading-like text line are
    fused into one candidate when the row-top pitch across the heading lies in
    the roughly 38-54 pt band (about 3.2-4.5 x the local 12 pt pitch). This is
    required for F1: the two-blank-line fixture's 48 pt pitch falls inside it,
    so removing the window re-breaks quarterly header handling. The ambiguity
    between an internal subheading and a heading that starts a new table is not
    resolvable from geometry alone, so this is a deliberate trade-off rather
    than a defect; it is documented in the limitations file.
    """
    def build(pitch_across_heading: float):
        return [
            (40.0, [_at(30.0, "Revenue"), _right(250.0, "1200"), _right(350.0, "1450")]),
            (52.0, [_at(30.0, "Profit"), _right(250.0, "25"), _right(350.0, "38")]),
            (64.0, [_at(30.0, "Margin"), _right(250.0, "-2.1"), _right(350.0, "2.6")]),
            (64.0 + pitch_across_heading / 2.0, [_at(30.0, "SEGMENT BETA")]),
            (64.0 + pitch_across_heading, [_at(30.0, "Revenue"), _right(250.0, "1300"),
                                           _right(350.0, "1500")]),
            (64.0 + pitch_across_heading + 12.0, [_at(30.0, "Profit"), _right(250.0, "26"),
                                                  _right(350.0, "39")]),
            (64.0 + pitch_across_heading + 24.0, [_at(30.0, "Margin"), _right(250.0, "-2.0"),
                                                  _right(350.0, "2.7")]),
        ]

    for name, run in (("aligned", _aligned), ("borderless", _borderless)):
        candidates = run(build(48.0))
        assert len(candidates) == 1, (name, candidates)
        texts = set(candidates[0][1])
        assert {"1200", "1300", "1450", "1500", "25", "26", "38", "39"} <= texts, (
            name, texts)


def test_two_headerless_blocks_separated_by_a_heading_outside_the_fusion_window_split():
    """F-3 boundary control: beyond the ceiling the bridge does not apply.

    With a 60 pt pitch across the heading (vertical gap 51 pt, past the 45 pt
    ceiling) the two headerless blocks stay as separate candidates; neither
    candidate claims the other's rows.
    """
    rows = [
        (40.0, [_at(30.0, "Revenue"), _right(250.0, "1200"), _right(350.0, "1450")]),
        (52.0, [_at(30.0, "Profit"), _right(250.0, "25"), _right(350.0, "38")]),
        (64.0, [_at(30.0, "Margin"), _right(250.0, "-2.1"), _right(350.0, "2.6")]),
        (94.0, [_at(30.0, "SEGMENT BETA")]),
        (124.0, [_at(30.0, "Revenue"), _right(250.0, "1300"), _right(350.0, "1500")]),
        (136.0, [_at(30.0, "Profit"), _right(250.0, "26"), _right(350.0, "39")]),
        (148.0, [_at(30.0, "Margin"), _right(250.0, "-2.0"), _right(350.0, "2.7")]),
    ]
    for name, run in (("aligned", _aligned), ("borderless", _borderless)):
        candidates = run(rows)
        assert len(candidates) == 2, (name, candidates)
        upper, lower = sorted(candidates, key=lambda item: item[0][0])
        assert upper[0][1] < lower[0][0], (name, candidates)
        assert {"1200", "1450", "25", "38"} <= set(upper[1]), (name, upper)
        assert {"1300", "1500", "26", "39"} <= set(lower[1]), (name, lower)
        assert not ({"1300", "1500"} & set(upper[1])), (name, upper)
        assert not ({"1200", "1450"} & set(lower[1])), (name, lower)


def test_restored_bridge_does_not_reunite_two_separate_tables():
    """A permissive bridge must not undo the B2-1 / B2-2 repairs."""
    first = [_header_row(("2023", "2024"))] + _data_rows(40.0, 12.0, VALUES)
    second = [_header_row(("2025", "2026"), y=264.0)] + _data_rows(264.0, 12.0, VALUES)
    for name, run in (("aligned", _aligned), ("borderless", _borderless)):
        candidates = run(first + second)
        assert len(candidates) == 2, (name, candidates)
        upper, lower = sorted(candidates, key=lambda item: item[0][0])
        assert upper[0][1] < lower[0][0], (name, candidates)
        assert "2023" in upper[1] and "2025" in lower[1], (name, candidates)


def test_restored_bridge_does_not_reunite_two_headerless_blocks():
    """Headerless blocks stay apart even with the more permissive bridge."""
    blocks = [
        (40.0, [_at(30.0, "Revenue"), _right(250.0, "1200"), _right(350.0, "1450")]),
        (52.0, [_at(30.0, "Profit"), _right(250.0, "25"), _right(350.0, "38")]),
        (52.0 + 200.0, [_at(30.0, "Cash"), _right(250.0, "900"), _right(350.0, "950")]),
    ]
    for name, run in (("aligned", _aligned), ("borderless", _borderless)):
        candidates = run(blocks)
        for candidate in candidates:
            top, bottom = candidate[0]
            assert not (top < 130 < bottom), (name, candidate)


def test_loose_table_beside_a_dense_table_is_still_separate():
    """The B2-2 repair must survive the bridge change."""
    dense = [_header_row(("2023", "2024"))] + _data_rows(40.0, 12.0, VALUES * 2)
    loose = [_header_row(("2025", "2026"), y=400.0)]
    loose += [
        (400.0 + offset * 56.0, [_at(30.0, label), _right(250.0, first), _right(350.0, second)])
        for offset, (label, (first, second)) in enumerate(
            (("Revenue", ("1300", "1500")), ("Profit", ("26", "39")),
             ("Margin", ("-2.0", "2.7"))), start=1)
    ]
    for name, run in (("aligned", _aligned), ("borderless", _borderless)):
        candidates = run(dense + loose)
        assert len(candidates) == 2, (name, candidates)
        upper, lower = sorted(candidates, key=lambda item: item[0][0])
        assert upper[0][1] < lower[0][0], (name, candidates)
        assert "2025" in lower[1], (name, candidates)


def test_uneven_row_spacing_layout_is_unchanged():
    """A table with one uneven internal gap keeps its rows."""
    rows = [_header_row(("2023", "2024"))]
    rows.append((52.0, [_at(30.0, "Revenue"), _right(250.0, "1200"), _right(350.0, "1450")]))
    rows.append((64.0, [_at(30.0, "Profit"), _right(250.0, "25"), _right(350.0, "38")]))
    rows.append((88.0, [_at(30.0, "Margin"), _right(250.0, "-2.1"), _right(350.0, "2.6")]))
    rows.append((100.0, [_at(30.0, "Tax"), _right(250.0, "10"), _right(350.0, "12")]))
    for name, run in (("aligned", _aligned), ("borderless", _borderless)):
        candidates = run(rows)
        assert len(candidates) == 1, (name, candidates)
        assert {"1200", "1450", "25", "38", "-2.1", "2.6", "10", "12"} <= set(candidates[0][1]), (
            name, candidates)


# ---------------------------------------------------------------------------
# F2 - one header-ambiguity policy in both paths
# ---------------------------------------------------------------------------


def _crowded_header_rows(token_centres=(300.0, 320.0), headers=("2023", "2024")):
    rows = [
        (40.0, [_at(30.0, "Measure")]
         + [_centre(centre, header) for centre, header in zip(token_centres, headers)]),
    ]
    rows += _data_rows(40.0, 12.0, VALUES)
    return rows


def test_crowded_period_header_is_ambiguous_in_the_aligned_path():
    rows = _crowded_header_rows()
    candidates = _aligned(rows)
    assert len(candidates) == 1, candidates
    bounds, texts, codes = candidates[0]
    assert "period_header_conflict" in codes, codes
    assert not [text for text in texts if _is_period_token(text)], texts


def test_crowded_period_header_is_ambiguous_in_the_borderless_path():
    """The borderless path must not bypass the ambiguity policy."""
    rows = _crowded_header_rows()
    candidates = _borderless(rows)
    assert candidates, "expected a geometry candidate"
    codes = set()
    for _bounds, _texts, candidate_codes in candidates:
        codes |= set(candidate_codes)
    assert "period_header_conflict" in codes, codes


def test_centre_header_is_accepted_in_both_paths():
    rows = [
        (40.0, [_at(30.0, "Measure"), _centre(200.0, "2023"), _centre(300.0, "2024")]),
    ]
    rows += _data_rows(40.0, 12.0, VALUES)
    aligned = _aligned(rows)
    assert len(aligned) == 1, aligned
    assert "2023" in aligned[0][1] and "2024" in aligned[0][1], aligned
    assert "period_header_conflict" not in aligned[0][2], aligned
    borderless = _borderless(rows)
    assert borderless, borderless
    codes = set()
    for _bounds, _texts, candidate_codes in borderless:
        codes |= set(candidate_codes)
    assert "period_header_conflict" not in codes, borderless


def test_small_consistent_offset_is_accepted_in_both_paths():
    rows = [
        (40.0, [_at(30.0, "Measure"), _centre(205.0, "2023"), _centre(305.0, "2024")]),
    ]
    rows += _data_rows(40.0, 12.0, VALUES)
    aligned = _aligned(rows)
    assert len(aligned) == 1, aligned
    assert "2023" in aligned[0][1] and "2024" in aligned[0][1], aligned
    assert "period_header_conflict" not in aligned[0][2], aligned
    borderless = _borderless(rows)
    codes = set()
    for _bounds, _texts, candidate_codes in borderless:
        codes |= set(candidate_codes)
    assert "period_header_conflict" not in codes, borderless


def test_exact_collision_stays_ambiguous():
    rows = [
        (40.0, [_at(30.0, "Measure"), _centre(200.0, "2023"), _centre(258.0, "2024")]),
    ]
    rows += _data_rows(40.0, 12.0, VALUES)
    aligned = _aligned(rows)
    assert len(aligned) == 1, aligned
    assert "period_header_conflict" in aligned[0][2], aligned


def test_partial_header_mapping_keeps_its_diagnostic():
    """Three period tokens for two value columns: the mapping stays partial."""
    rows = [
        (40.0, [_at(30.0, "Measure"), _centre(150.0, "2022"), _centre(250.0, "2023"),
                _centre(350.0, "2024")]),
    ]
    rows += _data_rows(40.0, 12.0, VALUES)
    aligned = _aligned(rows)
    assert len(aligned) == 1, aligned
    _bounds, texts, codes = aligned[0]
    assert "period_header_incomplete" in codes, codes
    assert "2023" in texts and "2024" in texts, texts


def test_non_contiguous_anchors_still_map_correctly():
    """A column with one value only does not shift the other columns."""
    rows = [
        (40.0, [_at(30.0, "Measure"), _centre(150.0, "2022"), _centre(250.0, "2023"),
                _centre(350.0, "2024")]),
        (52.0, [_at(30.0, "Revenue"), _right(170.0, "1200"), _right(270.0, "1450"),
                _right(370.0, "1500")]),
        (64.0, [_at(30.0, "Profit"), _right(170.0, "25"), _right(230.0, "7"),
                _right(370.0, "40")]),
        (76.0, [_at(30.0, "Margin"), _right(170.0, "-2.1"), _right(270.0, "2.6"),
                _right(370.0, "2.8")]),
    ]
    aligned = _aligned(rows)
    assert len(aligned) == 1, aligned
    _bounds, texts, codes = aligned[0]
    assert "2022" in texts and "2024" in texts, texts
    assert "period_header_conflict" not in codes, codes


def test_header_conflict_does_not_leak_into_a_later_candidate():
    rows = _crowded_header_rows()
    later = [(300.0, [_at(30.0, "Measure"), _centre(200.0, "2025"), _centre(300.0, "2026")])]
    later += _data_rows(300.0, 12.0, VALUES)
    aligned = _aligned(rows + later)
    assert len(aligned) == 2, aligned
    conflicting = [candidate for candidate in aligned if "period_header_conflict" in candidate[2]]
    assert len(conflicting) == 1, aligned
    assert not [text for text in conflicting[0][1] if _is_period_token(text)], aligned
    other = [candidate for candidate in aligned if candidate is not conflicting[0]][0]
    assert "2025" in other[1] and "2026" in other[1], aligned


# ---------------------------------------------------------------------------
# F3 - thresholds pinned on both sides
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factor,expected_rows",
    [(2.9, 5), (3.0, 5), (3.1, 3)],
)
def test_row_separation_threshold_neighbourhood(factor, expected_rows):
    """A single table with an internal gap of k x its own row pitch.

    Below and at the separation factor the table is kept whole; above it the
    block is separated, and the rows that were separated are never merged into
    a candidate that claims both sides.
    """
    pitch = 12.0
    gap = pitch * factor
    rows = [
        (40.0, [_at(30.0, "Measure"), _centre(200.0, "2023"), _centre(300.0, "2024")]),
        (52.0, [_at(30.0, "Revenue"), _right(250.0, "1200"), _right(350.0, "1450")]),
        (64.0, [_at(30.0, "Profit"), _right(250.0, "25"), _right(350.0, "38")]),
        (64.0 + gap, [_at(30.0, "Margin"), _right(250.0, "-2.1"), _right(350.0, "2.6")]),
        (76.0 + gap, [_at(30.0, "Tax"), _right(250.0, "10"), _right(350.0, "12")]),
    ]
    for name, run in (("aligned", _aligned), ("borderless", _borderless)):
        candidates = run(rows)
        if expected_rows == 5:
            assert len(candidates) == 1, (name, factor, candidates)
            assert {"1200", "1450", "25", "38", "-2.1", "2.6", "10", "12"} <= set(candidates[0][1]), (
                name, factor, candidates)
        else:
            for candidate in candidates:
                texts = set(candidate[1])
                assert not ({"1200", "1450"} & texts) or not ({"10", "12"} & texts), (
                    name, factor, candidate)


@pytest.mark.parametrize("spread,conflicting", [(0.0, False), (40.0, False), (70.0, True)])
def test_header_offset_threshold_neighbourhood_two_columns(spread, conflicting):
    """Header tokens whose offsets disagree by more than 0.6 of the column
    spacing cannot be justified one-per-column."""
    rows = [
        (40.0, [_at(30.0, "Measure"), _centre(200.0, "2023"), _centre(300.0 + spread, "2024")]),
    ]
    rows += _data_rows(40.0, 12.0, VALUES)
    aligned = _aligned(rows)
    assert len(aligned) == 1, aligned
    _bounds, texts, codes = aligned[0]
    if conflicting:
        assert "period_header_conflict" in codes, codes
        assert not [text for text in texts if _is_period_token(text)], texts
    else:
        assert "period_header_conflict" not in codes, codes
        assert "2023" in texts and "2024" in texts, texts


@pytest.mark.parametrize("spread,conflicting", [(0.0, False), (45.0, False), (70.0, True)])
def test_header_offset_threshold_neighbourhood_three_columns(spread, conflicting):
    """The same policy for a three-column grid."""
    rows = [
        (40.0, [_at(30.0, "Measure"), _centre(150.0, "2022"), _centre(250.0, "2023"),
                _centre(350.0 + spread, "2024")]),
    ]
    rows += [
        (40.0 + offset * 12.0,
         [_at(30.0, label), _right(200.0, first), _right(300.0, second), _right(400.0, third)])
        for offset, (label, (first, second, third)) in enumerate(
            (("Revenue", ("1200", "1450", "1500")), ("Profit", ("25", "38", "40")),
             ("Margin", ("-2.1", "2.6", "2.8"))), start=1)
    ]
    aligned = _aligned(rows)
    assert len(aligned) == 1, aligned
    _bounds, texts, codes = aligned[0]
    if conflicting:
        assert "period_header_conflict" in codes, codes
        assert not [text for text in texts if _is_period_token(text)], texts
    else:
        assert "period_header_conflict" not in codes, codes
        assert "2022" in texts and "2024" in texts, texts


@pytest.mark.parametrize("token_centres,conflicting", [
    ((200.0, 300.0), False),
    ((150.0, 400.0), True),
])
def test_borderless_path_applies_the_same_header_offset_policy(token_centres, conflicting):
    """The borderless path must reach the same conclusion as the aligned one.

    The two headers are far from the columns they would label, so one token
    per column is not justified by the geometry.
    """
    rows = [
        (40.0, [_at(30.0, "Measure")]
         + [_centre(centre, header) for centre, header in zip(token_centres, ("2023", "2024"))]),
    ]
    rows += _data_rows(40.0, 12.0, list(VALUES))
    candidates = _borderless(rows)
    assert candidates, "expected a geometry candidate"
    codes = set()
    for _bounds, _texts, candidate_codes in candidates:
        codes |= set(candidate_codes)
    if conflicting:
        assert "period_header_conflict" in codes, codes
    else:
        assert "period_header_conflict" not in codes, codes


def test_borderless_few_row_blocks_are_not_fused():
    """B2-1 in the borderless path: two headerless blocks, a large gap.

    The fragments do not independently meet the minimum evidence, so no
    candidate may confidently span both.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=400)
    rows = [
        (40, [_at(30, "Revenue"), _right(250, "1200"), _right(350, "1450")]),
        (52, [_at(30, "Profit"), _right(250, "25"), _right(350, "38")]),
        (252, [_at(30, "Cash"), _right(250, "900"), _right(350, "950")]),
    ]
    _write(page, rows)
    try:
        result = extract_tables_from_page(page, page_number=1)
        candidates = [
            candidate for candidate in result.candidates
            if candidate.detection_method == TableDetectionMethod.WORD_GEOMETRY
        ]
        for candidate in candidates:
            top, bottom = candidate.bbox[1], candidate.bbox[3]
            assert not (top < 130 < bottom), (
                f"a candidate spans both blocks: bbox=({top:.0f}..{bottom:.0f})")
    finally:
        doc.close()
