"""Synthetic contract tests for table layout and unresolved row contexts."""

from decimal import Decimal

import pytest
from pydantic import ValidationError
import pymupdf

from app.table_extraction import (
    CellReference,
    ContextDimension,
    ContextSource,
    ContextSourceKind,
    DimensionResolution,
    EntityInterpretation,
    MagnitudeScale,
    ParsedValueInterpretation,
    PeriodInterpretation,
    PeriodType,
    QualifierReference,
    ResolutionStatus,
    TableCandidate,
    TableCell,
    TableDetectionMethod,
    TableRowValueContext,
    UnitInterpretation,
    UnitKind,
    _is_placeholder,
    _is_period_token,
    _looks_numeric,
    extract_tables_from_page,
    validate_context_provenance,
)


def _synthetic_financial_table(*, geometry: bool = True) -> TableCandidate:
    box = lambda x0, y0, x1, y1: (x0, y0, x1, y1) if geometry else None
    return TableCandidate(
        table_id="page-5-table-0",
        page_number=5,
        bbox=box(10, 10, 400, 160),
        detection_method=TableDetectionMethod.SYNTHETIC,
        confidence=1.0,
        cells=[
            TableCell(row_index=0, column_index=0, text="Measure", bbox=box(10, 10, 120, 30)),
            TableCell(
                row_index=0,
                column_index=1,
                text="Revenue (£m)",
                bbox=box(120, 10, 400, 30),
                column_span=2,
            ),
            TableCell(row_index=1, column_index=0, text="Segment", bbox=box(10, 30, 120, 50)),
            TableCell(row_index=1, column_index=1, text="FY2025", bbox=box(120, 30, 260, 50)),
            TableCell(row_index=1, column_index=2, text="FY2026", bbox=box(260, 30, 400, 50)),
            TableCell(row_index=2, column_index=0, text="Civil Aerospace", bbox=box(10, 50, 120, 80)),
            TableCell(row_index=2, column_index=1, text="-£3.45 million*", bbox=box(120, 50, 260, 80)),
            TableCell(row_index=2, column_index=2, text="£3.20 million", bbox=box(260, 50, 400, 80)),
            TableCell(row_index=3, column_index=0, text="* Underlying revenue.", bbox=box(10, 80, 400, 100), column_span=3),
        ],
    )


def _resolved_context(
    table: TableCandidate,
    *,
    value_column: int = 1,
    value_text: str = "-£3.45 million*",
    amount: str = "-3.45",
    period_text: str = "FY2025",
    period_type: PeriodType = PeriodType.FISCAL_YEAR,
) -> TableRowValueContext:
    value_cell = CellReference(row_index=2, column_index=value_column)
    header_cell = CellReference(row_index=1, column_index=value_column)
    source_kind = ContextSourceKind.COLUMN_HEADER
    return TableRowValueContext(
        page_number=table.page_number,
        table_id=table.table_id,
        row_index=2,
        value_column_index=value_column,
        row_label=CellReference(row_index=2, column_index=0),
        column_header_path=[CellReference(row_index=0, column_index=1), header_cell],
        value_cell=value_cell,
        raw_display_value=value_text,
        value=ParsedValueInterpretation(
            raw_text=value_text,
            numeric_value=Decimal(amount),
            unit=UnitInterpretation(
                kind=UnitKind.CURRENCY,
                raw_unit="GBP million",
                currency_code="GBP",
                scale=MagnitudeScale.MILLION,
            ),
        ),
        period=PeriodInterpretation(
            raw_text=period_text,
            normalized_text=period_text,
            period_type=period_type,
            sources=[
                ContextSource(
                    kind=source_kind,
                    text=period_text,
                    cell=header_cell,
                )
            ],
        ),
        entity=EntityInterpretation(
            issuer="Rolls-Royce",
            segment="Civil Aerospace",
            sources=[
                ContextSource(
                    kind=ContextSourceKind.ROW_LABEL,
                    text="Civil Aerospace",
                    cell=CellReference(row_index=2, column_index=0),
                ),
                ContextSource(kind=ContextSourceKind.TABLE_HEADING, text="Rolls-Royce Group"),
            ],
        ),
        qualifiers=[
            QualifierReference(
                raw_text="* Underlying revenue.",
                interpreted_as="underlying",
                source=ContextSource(
                    kind=ContextSourceKind.FOOTNOTE,
                    text="* Underlying revenue.",
                    cell=CellReference(row_index=3, column_index=0),
                ),
            )
        ],
        resolutions=[
            DimensionResolution(
                dimension=ContextDimension.ROW_VALUE_ASSOCIATION,
                status=ResolutionStatus.RESOLVED,
            ),
            DimensionResolution(
                dimension=ContextDimension.HEADER_ASSOCIATION,
                status=ResolutionStatus.RESOLVED,
            ),
            DimensionResolution(dimension=ContextDimension.ENTITY, status=ResolutionStatus.RESOLVED),
            DimensionResolution(dimension=ContextDimension.PERIOD, status=ResolutionStatus.RESOLVED),
            DimensionResolution(dimension=ContextDimension.UNIT, status=ResolutionStatus.RESOLVED),
            DimensionResolution(dimension=ContextDimension.SCALE, status=ResolutionStatus.RESOLVED),
            DimensionResolution(
                dimension=ContextDimension.REPORTING_BASIS,
                status=ResolutionStatus.RESOLVED,
            ),
        ],
    )


def test_bordered_table_preserves_labels_headers_values_and_geometry():
    table = _synthetic_financial_table()
    context = _resolved_context(table)

    assert table.page_number == 5
    assert table.bbox == (10.0, 10.0, 400.0, 160.0)
    assert table.cells[5].text == "Civil Aerospace"
    assert table.cells[6].text == "-£3.45 million*"
    assert context.row_label == CellReference(row_index=2, column_index=0)
    assert context.column_header_path[-1] == CellReference(row_index=1, column_index=1)
    assert context.value.numeric_value == Decimal("-3.45")
    assert context.value.unit.currency_code == "GBP"
    assert context.value.unit.scale == MagnitudeScale.MILLION
    assert context.is_context_resolved
    validate_context_provenance(table, context)


def test_borderless_table_keeps_ordered_cells_without_inventing_geometry():
    table = _synthetic_financial_table(geometry=False)

    assert table.bbox is None
    assert all(cell.bbox is None for cell in table.cells)
    assert [(cell.row_index, cell.column_index) for cell in table.cells] == sorted(
        (cell.row_index, cell.column_index) for cell in table.cells
    )


def test_each_value_retains_its_own_reporting_year_header():
    table = _synthetic_financial_table()
    fy2025 = _resolved_context(table, value_column=1, value_text="-£3.45 million*", amount="-3.45")
    fy2026 = _resolved_context(
        table,
        value_column=2,
        value_text="£3.20 million",
        amount="3.20",
        period_text="FY2026",
    )

    assert fy2025.column_header_path[-1].column_index == 1
    assert fy2025.period.raw_text == "FY2025"
    assert fy2026.column_header_path[-1].column_index == 2
    assert fy2026.period.raw_text == "FY2026"
    assert fy2025.value.numeric_value == Decimal("-3.45")
    assert fy2026.value.numeric_value == Decimal("3.20")


def test_multi_level_and_merged_headers_are_referenced_as_a_path():
    table = _synthetic_financial_table()
    context = _resolved_context(table)
    group_header = table.get_cell(context.column_header_path[0])

    assert group_header.text == "Revenue (£m)"
    assert group_header.column_span == 2
    assert [table.get_cell(ref).text for ref in context.column_header_path] == [
        "Revenue (£m)",
        "FY2025",
    ]
    validate_context_provenance(table, context)


def test_financial_value_preserves_currency_magnitude_sign_percent_and_per_share_units():
    gbp = ParsedValueInterpretation(
        raw_text="(£1,250.50)m",
        numeric_value=Decimal("-1250.50"),
        unit=UnitInterpretation(
            kind=UnitKind.CURRENCY,
            raw_unit="GBP million",
            currency_code="GBP",
            scale=MagnitudeScale.MILLION,
        ),
    )
    percent = UnitInterpretation(kind=UnitKind.PERCENT, raw_unit="%")
    percentage_points = UnitInterpretation(
        kind=UnitKind.PERCENTAGE_POINT,
        raw_unit="percentage points",
    )
    per_share = UnitInterpretation(kind=UnitKind.PER_SHARE, raw_unit="pence per share")
    percentage_value = ParsedValueInterpretation(
        raw_text="5.4%",
        numeric_value=Decimal("5.4"),
        unit=percent,
    )

    assert gbp.numeric_value == Decimal("-1250.50")
    assert gbp.unit.currency_code == "GBP"
    assert gbp.unit.scale == MagnitudeScale.MILLION
    assert percentage_value.numeric_value == Decimal("5.4")
    assert percent.kind == UnitKind.PERCENT
    assert percentage_points.kind == UnitKind.PERCENTAGE_POINT
    assert per_share.kind == UnitKind.PER_SHARE
    assert per_share.raw_unit == "pence per share"


def test_segment_is_distinct_from_issuer_and_footnote_qualifier_keeps_reference():
    table = _synthetic_financial_table()
    context = _resolved_context(table)

    assert context.entity.issuer == "Rolls-Royce"
    assert context.entity.segment == "Civil Aerospace"
    assert context.entity.issuer != context.entity.segment
    assert context.qualifiers[0].interpreted_as == "underlying"
    assert context.qualifiers[0].source.cell == CellReference(row_index=3, column_index=0)
    validate_context_provenance(table, context)


def test_ambiguous_entity_period_and_unit_remain_unresolved_without_guesses():
    context = TableRowValueContext(
        page_number=7,
        table_id="table-0",
        row_index=4,
        value_column_index=2,
        raw_display_value="5.4",
        resolutions=[
            DimensionResolution(
                dimension=ContextDimension.ROW_VALUE_ASSOCIATION,
                status=ResolutionStatus.UNRESOLVED,
                reason="Two nearby numeric cells could be the target value.",
            ),
            DimensionResolution(
                dimension=ContextDimension.HEADER_ASSOCIATION,
                status=ResolutionStatus.AMBIGUOUS,
                reason="Two header paths govern this column.",
            ),
            DimensionResolution(
                dimension=ContextDimension.ENTITY,
                status=ResolutionStatus.UNRESOLVED,
                reason="No source-supported issuer or jurisdiction was found.",
            ),
            DimensionResolution(
                dimension=ContextDimension.PERIOD,
                status=ResolutionStatus.AMBIGUOUS,
                reason="The period header is detached from the value column.",
            ),
            DimensionResolution(
                dimension=ContextDimension.UNIT,
                status=ResolutionStatus.UNRESOLVED,
                reason="The row and table do not establish a unit.",
            ),
            DimensionResolution(
                dimension=ContextDimension.SCALE,
                status=ResolutionStatus.UNRESOLVED,
                reason="The displayed magnitude is not specified.",
            ),
        ],
    )

    assert context.value is None
    assert context.entity is None
    assert context.period is None
    assert not context.is_context_resolved


def test_missing_context_can_be_marked_not_present_but_is_not_complete():
    table = _synthetic_financial_table()
    context = _resolved_context(table)
    context.resolutions = [
        resolution
        for resolution in context.resolutions
        if resolution.dimension != ContextDimension.PERIOD
    ] + [
        DimensionResolution(
            dimension=ContextDimension.PERIOD,
            status=ResolutionStatus.NOT_PRESENT,
            reason="No reporting period is printed for this row.",
        )
    ]
    context.period = None

    assert not context.is_context_resolved


def test_conflicting_headers_are_ambiguous_not_selected_by_order():
    table = _synthetic_financial_table()
    ambiguous = _resolved_context(table)
    ambiguous.resolutions = [
        resolution
        for resolution in ambiguous.resolutions
        if resolution.dimension != ContextDimension.HEADER_ASSOCIATION
    ] + [
        DimensionResolution(
            dimension=ContextDimension.HEADER_ASSOCIATION,
            status=ResolutionStatus.AMBIGUOUS,
            reason="Two competing column-header interpretations remain.",
        )
    ]

    assert not ambiguous.is_context_resolved
    validate_context_provenance(table, ambiguous)


def test_conflicting_entity_sources_remain_ambiguous():
    table = _synthetic_financial_table()
    context = _resolved_context(table)
    context.entity = EntityInterpretation(
        issuer="Rolls-Royce",
        jurisdiction="United Kingdom",
        sources=[
            ContextSource(kind=ContextSourceKind.ROW_LABEL, text="Subsidiary X"),
            ContextSource(kind=ContextSourceKind.TABLE_HEADING, text="Different issuer Y"),
        ],
    )
    context.resolutions = [
        resolution
        for resolution in context.resolutions
        if resolution.dimension != ContextDimension.ENTITY
    ] + [
        DimensionResolution(
            dimension=ContextDimension.ENTITY,
            status=ResolutionStatus.AMBIGUOUS,
            reason="The row and table heading identify conflicting entities.",
        )
    ]

    assert not context.is_context_resolved
    validate_context_provenance(table, context)


def test_missing_geometry_round_trips_as_null():
    table = _synthetic_financial_table(geometry=False)
    restored = TableCandidate.model_validate_json(table.model_dump_json())

    assert restored.bbox is None
    assert all(cell.bbox is None for cell in restored.cells)


def test_serialization_is_deterministic_and_round_trip_preserves_contract():
    table = _synthetic_financial_table()
    context = _resolved_context(table)
    table_json = table.model_dump_json()
    context_json = context.model_dump_json()

    assert table_json == table.model_dump_json()
    assert context_json == context.model_dump_json()
    assert TableCandidate.model_validate_json(table_json) == table
    assert TableRowValueContext.model_validate_json(context_json) == context


def test_invalid_indices_spans_and_bounding_boxes_are_rejected():
    with pytest.raises(ValidationError):
        TableCell(row_index=-1, column_index=0, text="bad")
    with pytest.raises(ValidationError):
        TableCell(row_index=0, column_index=0, text="bad", column_span=0)
    with pytest.raises(ValidationError):
        TableCell(row_index=0, column_index=0, text="bad", bbox=(4, 2, 1, 8))
    with pytest.raises(ValidationError):
        TableCell(row_index=0, column_index=0, text="bad", bbox=(0, 0, float("nan"), 1))

    with pytest.raises(ValidationError, match="spans must not overlap"):
        TableCandidate(
            table_id="overlap",
            page_number=1,
            detection_method=TableDetectionMethod.SYNTHETIC,
            cells=[
                TableCell(row_index=0, column_index=0, text="merged", column_span=2),
                TableCell(row_index=0, column_index=1, text="overlap"),
            ],
        )


def test_invalid_cross_table_or_header_provenance_is_rejected():
    table = _synthetic_financial_table()
    context = _resolved_context(table)
    wrong_header = CellReference(row_index=2, column_index=2)
    context.column_header_path[-1] = wrong_header

    with pytest.raises(ValueError, match="precede and govern"):
        validate_context_provenance(table, context)

    context.table_id = "different-table"
    with pytest.raises(ValueError, match="identify its source page and table"):
        validate_context_provenance(table, context)


def test_structural_candidate_does_not_imply_a_fact():
    table = TableCandidate(
        table_id="unclassified-grid",
        page_number=3,
        detection_method=TableDetectionMethod.SYNTHETIC,
        cells=[
            TableCell(row_index=0, column_index=0, text="A"),
            TableCell(row_index=0, column_index=1, text="B"),
            TableCell(row_index=1, column_index=0, text="12"),
            TableCell(row_index=1, column_index=1, text="18"),
        ],
    )

    assert len(table.cells) == 4
    assert not hasattr(table, "facts")
    assert not hasattr(table, "claims")


def test_context_requires_explicit_status_for_required_dimensions():
    with pytest.raises(ValidationError, match="missing explicit resolution"):
        TableRowValueContext(
            page_number=1,
            table_id="table-0",
            row_index=0,
            value_column_index=0,
        )


def test_conflicting_or_missing_currency_details_cannot_be_called_resolved():
    with pytest.raises(ValidationError, match="explicit currency_code"):
        UnitInterpretation(kind=UnitKind.CURRENCY, raw_unit="£ million")
    with pytest.raises(ValidationError, match="three-letter uppercase"):
        UnitInterpretation(
            kind=UnitKind.CURRENCY,
            raw_unit="USD",
            currency_code="usd",
        )
    with pytest.raises(ValidationError, match="both be present or both be absent"):
        ParsedValueInterpretation(raw_text="£1m", numeric_value=Decimal("1"))


def _make_bordered_pdf(rotation: int = 0):
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
    page.insert_text((40, 218), "* Underlying measure", fontsize=8)
    if rotation:
        page.set_rotation(rotation)
    return doc


def _make_borderless_pdf():
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=220)
    rows = [
        [(35, "Measure"), (190, "FY2024"), (300, "FY2025")],
        [(35, "Revenue"), (180, "£1,200m*"), (290, "£1,450m")],
        [(35, "Operating profit"), (180, "(£25m)"), (290, "£38m")],
        [(35, "Margin"), (180, "-2.1%"), (290, "n/a")],
        [(35, "Nominal GDP (billions GBP)"), (180, "2,323"), (290, "2,581")],
    ]
    for row_index, row in enumerate(rows):
        y = 45 + row_index * 30
        for x, text in row:
            page.insert_text((x, y), text, fontsize=9)
    page.insert_text((35, 180), "* Underlying measure", fontsize=8)
    return doc


def test_pymupdf_adapter_reconstructs_bordered_rows_text_and_geometry():
    doc = _make_bordered_pdf()
    try:
        page = doc[0]
        result = extract_tables_from_page(page, page_number=1)
        assert len(result.candidates) == 1
        table = result.candidates[0]
        assert table.detection_method == TableDetectionMethod.PYMUPDF_FIND_TABLES
        assert table.confidence is None
        assert table.bbox is not None
        assert [(cell.row_index, cell.column_index, cell.text) for cell in table.cells[:6]] == [
            (0, 0, "Measure"), (0, 1, "FY2024"), (0, 2, "FY2025"),
            (1, 0, "Revenue"), (1, 1, "£1,200m*"), (1, 2, "£1,450m"),
        ]
        assert table.cells[4].bbox is not None
        assert table.cells[4].bbox[0] < table.cells[4].bbox[2]
        assert "(£25m)" in [cell.text for cell in table.cells]
        assert "-2.1%" in [cell.text for cell in table.cells]
        assert "* Underlying measure" in page.get_text("text")
        assert all(cell.bbox is None or (
            0 <= cell.bbox[0] <= cell.bbox[2] <= page.mediabox.width
            and 0 <= cell.bbox[1] <= cell.bbox[3] <= page.mediabox.height
            and table.bbox[0] <= cell.bbox[0] <= cell.bbox[2] <= table.bbox[2]
            and table.bbox[1] <= cell.bbox[1] <= cell.bbox[3] <= table.bbox[3]
        ) for cell in table.cells)
    finally:
        doc.close()


def test_word_geometry_adapter_reconstructs_borderless_rows_and_year_headers():
    doc = _make_borderless_pdf()
    try:
        result = extract_tables_from_page(doc[0], page_number=8)
        assert len(result.candidates) == 1
        table = result.candidates[0]
        assert table.detection_method == TableDetectionMethod.WORD_GEOMETRY
        assert table.confidence is None
        cells = {(cell.row_index, cell.column_index): cell for cell in table.cells}
        assert cells[(0, 1)].text == "FY2024"
        assert cells[(0, 2)].text == "FY2025"
        assert cells[(1, 1)].text == "£1,200m*"
        assert cells[(2, 1)].text == "(£25m)"
        assert cells[(3, 1)].text == "-2.1%"
        assert cells[(3, 2)].text == "n/a"
        assert cells[(1, 0)].text == "Revenue"
        assert cells[(4, 0)].text == "Nominal GDP (billions GBP)"
        assert "recurring columns" in table.diagnostics[0].message
        assert table.bbox is not None
        assert all(table.bbox[0] <= cell.bbox[0] <= cell.bbox[2] <= table.bbox[2]
                   and table.bbox[1] <= cell.bbox[1] <= cell.bbox[3] <= table.bbox[3]
                   for cell in table.cells if cell.bbox is not None)
    finally:
        doc.close()


def test_rotation_normalizes_find_tables_geometry_to_unrotated_page_space():
    doc = _make_bordered_pdf(rotation=90)
    plain_doc = _make_bordered_pdf()
    try:
        page = doc[0]
        result = extract_tables_from_page(page, page_number=2)
        assert len(result.candidates) == 1
        table = result.candidates[0]
        assert table.bbox is not None
        # The media box remains 400 x 250 points although page.rect is rotated.
        assert table.bbox[2] <= page.mediabox.width
        assert table.bbox[3] <= page.mediabox.height
        assert table.bbox[2] > table.bbox[3]
        assert (table.cells[0].row_index, table.cells[0].column_index) == (0, 0)
        assert any(d.code == "rotated_find_tables_normalized" for d in table.diagnostics)
        plain = extract_tables_from_page(plain_doc[0], page_number=2).candidates[0]
        assert [(cell.row_index, cell.column_index, cell.text) for cell in table.cells] == [
            (cell.row_index, cell.column_index, cell.text) for cell in plain.cells
        ]
        assert table.bbox == pytest.approx(plain.bbox)
    finally:
        doc.close()
        plain_doc.close()


def test_merged_borderless_header_is_reported_as_ambiguous():
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=210)
    xs, ys = [30, 120, 210, 300, 370], [30, 65, 100, 135, 170]
    for x in (xs[0], xs[1], xs[-1]):
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for x in xs[2:-1]:
        page.draw_line((x, ys[1]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    page.insert_text((40, 52), "Measure", fontsize=9)
    page.insert_text((130, 52), "Financial results", fontsize=9)
    for column, text in enumerate(("", "FY2024", "FY2025", "FY2026")):
        if text:
            page.insert_text((xs[column] + 3, 88), text, fontsize=9)
    for row, label, values in ((2, "Revenue", ("£1m", "£2m", "£3m")), (3, "Profit", ("£0.1m", "£0.2m", "£0.3m"))):
        page.insert_text((xs[0] + 3, ys[row] + 22), label, fontsize=9)
        for column, text in enumerate(values, 1):
            page.insert_text((xs[column] + 3, ys[row] + 22), text, fontsize=9)
    try:
        result = extract_tables_from_page(page, page_number=1)
        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.detection_method == TableDetectionMethod.WORD_GEOMETRY
        assert any(d.code == "ambiguous_header_span" for d in candidate.diagnostics)
        assert all(cell.bbox is not None for cell in candidate.cells)
    finally:
        doc.close()


def test_multilevel_header_cells_remain_distinct_and_associable():
    doc = _make_bordered_pdf()
    try:
        result = extract_tables_from_page(doc[0], page_number=1)
        cells = {(cell.row_index, cell.column_index): cell for cell in result.candidates[0].cells}
        # Header paths remain spatially represented, even though interpretation
        # of actual versus forecast or underlying basis belongs to later work.
        assert cells[(0, 1)].text == "FY2024"
        assert cells[(0, 2)].text == "FY2025"
        assert cells[(1, 1)].text == "£1,200m*"
        assert cells[(1, 1)].bbox[1] > cells[(0, 1)].bbox[3] - 1
    finally:
        doc.close()


def test_prose_without_repeated_numeric_rows_is_not_emitted_as_a_table():
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=220)
    for index, text in enumerate((
        "The report describes the business in several regions.",
        "The company expects demand to remain resilient over time.",
        "Management discussed strategy and customer relationships.",
        "This text continues in a second column for visual balance.",
    )):
        x = 35 if index < 2 else 230
        y = 55 + (index % 2) * 35
        page.insert_text((x, y), text, fontsize=9)
    try:
        result = extract_tables_from_page(page, page_number=1)
        assert not result.candidates
        assert any(item.code.startswith("borderless_") for item in result.diagnostics)
    finally:
        doc.close()


def test_unavailable_and_malformed_find_tables_results_emit_diagnostics():
    class NoFindTablesPage:
        rotation = 0
        rect = pymupdf.Rect(0, 0, 100, 100)

        def get_text(self, kind):
            return []

        def get_drawings(self):
            return [{}]

    class FakePage:
        rotation = 0
        rect = pymupdf.Rect(0, 0, 100, 100)
        mediabox = pymupdf.Rect(0, 0, 100, 100)

        def __init__(self, finder):
            self.finder = finder

        def find_tables(self):
            if isinstance(self.finder, Exception):
                raise self.finder
            return self.finder

        def get_text(self, kind):
            return []

        def get_drawings(self):
            return [{}]

    class MalformedTable:
        row_count = 2
        col_count = 2
        cells = [(0, 0, 50, 20)]

        def extract(self):
            return [["A", "B"], ["1", "2"]]

    class Finder:
        tables = [MalformedTable()]

    unavailable = extract_tables_from_page(NoFindTablesPage(), 1)
    failed = extract_tables_from_page(FakePage(RuntimeError("unsupported")), 1)
    malformed = extract_tables_from_page(FakePage(Finder()), 1)
    assert any(item.code == "find_tables_unavailable" for item in unavailable.diagnostics)
    assert any(item.code == "find_tables_failed" for item in failed.diagnostics)
    assert any(item.code in {"malformed_find_tables_result", "empty_find_tables_result"} for item in malformed.diagnostics)
    assert not failed.candidates
    assert not malformed.candidates


def test_ids_are_stable_and_missing_cell_geometry_is_not_fabricated():
    doc = _make_bordered_pdf()
    try:
        page = doc[0]
        first = extract_tables_from_page(page, page_number=4)
        second = extract_tables_from_page(page, page_number=4)
        assert [table.table_id for table in first.candidates] == [table.table_id for table in second.candidates]
        assert first.candidates[0].model_dump_json() == second.candidates[0].model_dump_json()
    finally:
        doc.close()

    class FakePage:
        rotation = 0
        rect = pymupdf.Rect(0, 0, 100, 100)

        def get_text(self, kind):
            return []

        def get_drawings(self):
            return [{}]

        def find_tables(self):
            return type("Finder", (), {"tables": [type("Table", (), {
                "row_count": 1, "col_count": 1, "bbox": None, "cells": [None],
                "extract": lambda self: [["source text"]],
            })()]})()

    missing = extract_tables_from_page(FakePage(), 1)
    assert len(missing.candidates) == 1
    assert missing.candidates[0].cells[0].text == "source text"
    assert missing.candidates[0].bbox is None
    assert missing.candidates[0].cells[0].bbox is None
    assert any(item.code == "cell_geometry_missing" for item in missing.candidates[0].diagnostics)


def test_parser_exposes_layout_candidates_without_changing_text_or_score():
    from app.pdf_parser import compute_page_score, parse_pdf_page

    doc = _make_bordered_pdf()
    try:
        page = doc[0]
        raw = page.get_text("text") or ""
        parsed = parse_pdf_page(doc, 0, "synthetic", "synthetic.pdf")
        expected = compute_page_score(raw, parsed.has_tables, parsed.blocks)
        assert parsed.raw_text == raw
        assert parsed.score_breakdown == expected
        assert parsed.table_candidates
        assert not hasattr(parsed.table_candidates[0], "facts")
    finally:
        doc.close()


def test_period_tokens_and_placeholders_are_not_numeric_values():
    for token in ("FY2024", "Q4", "2023-24", "2023–24", "2024", "31/12/2024", "n/a", "—", "–", ""):
        assert not _looks_numeric(token), token
    for token in ("(£25m)", "-2.1%", "£38m", "£25 million", "£1.25p", "1,234.50", "0.25"):
        assert _looks_numeric(token), token
    assert _is_period_token("FY2024")
    assert _is_period_token("Q4")
    assert _is_period_token("2023-24")
    assert _is_placeholder("—") and _is_placeholder("n/a")


def test_date_heavy_two_column_prose_does_not_become_a_table():
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=260)
    for index, (left, right) in enumerate((
        ("The 2024 results were published", "on 31 March 2024 after review"),
        ("During 2023 the company reported", "on 15 June 2023 to investors"),
        ("In FY2022 the report notes", "on 2 February 2022 for reference"),
    )):
        y = 60 + index * 35
        page.insert_text((30, y), left, fontsize=9)
        page.insert_text((275, y), right, fontsize=9)
    try:
        result = extract_tables_from_page(page, page_number=1)
        assert not result.candidates
    finally:
        doc.close()


def test_year_headers_and_placeholder_values_keep_their_columns():
    doc = _make_borderless_pdf()
    try:
        result = extract_tables_from_page(doc[0], page_number=3)
        assert result.candidates
        candidate = result.candidates[0]
        cells = {(cell.row_index, cell.column_index): cell.text for cell in candidate.cells}
        assert "FY2024" in cells.values() and "FY2025" in cells.values()
        assert cells[(3, 2)] == "n/a"
        assert cells[(3, 1)] == "-2.1%"
    finally:
        doc.close()


def test_blank_and_dash_cells_do_not_shift_later_numeric_columns():
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=230)
    rows = [
        ((35, "Measure"), (190, "FY2024"), (300, "FY2025")),
        ((35, "Revenue"), (190, "100"), (300, "110")),
        ((35, "Profit"), (300, "12")),  # the first value cell is genuinely blank
        ((35, "Margin"), (190, "-2.1%"), (300, "3.0%")),
        ((35, "Cash"), (190, "—"), (300, "15")),
    ]
    for row_index, row in enumerate(rows):
        for x, text in row:
            page.insert_text((x, 45 + row_index * 30), text, fontsize=9)
    try:
        result = extract_tables_from_page(page, page_number=4, allow_find_tables=False)
        assert result.candidates
        candidate = max(result.candidates, key=lambda item: len(item.cells))
        cells = {(cell.row_index, cell.column_index): cell.text for cell in candidate.cells}
        assert any(text == "12" and column > 1 for (row, column), text in cells.items())
        # PyMuPDF's synthetic Helvetica encoding exposes this em dash as a
        # middle-dot glyph; the raw extracted surface is retained and treated
        # as a placeholder rather than as numeric data.
        assert "·" in cells.values() and _is_placeholder("·")
        assert any(text == "15" and column > 1 for (row, column), text in cells.items())
    finally:
        doc.close()


def test_high_drawing_workload_skips_find_tables_but_keeps_word_fallback_and_stable_diagnostic():
    source = _make_borderless_pdf()
    try:
        class ComplexPage:
            rotation = 0
            rect = source[0].rect
            mediabox = source[0].mediabox
            derotation_matrix = source[0].derotation_matrix

            def __init__(self):
                self.calls = 0

            def get_text(self, kind):
                return source[0].get_text(kind)

            def get_drawings(self):
                return [{}] * 45

            def find_tables(self):
                self.calls += 1
                raise AssertionError("expensive detector should be skipped")

        page = ComplexPage()
        first = extract_tables_from_page(page, page_number=7)
        second = extract_tables_from_page(page, page_number=7)
        assert page.calls == 0
        assert first.candidates
        assert any(item.code == "find_tables_skipped_workload" for item in first.diagnostics)
        diagnostic = next(item.message for item in first.diagnostics if item.code == "find_tables_skipped_workload")
        assert "drawing_count=45" in diagnostic and "word_count=" in diagnostic and "fallback remains enabled" in diagnostic
        assert first.diagnostics[0].model_dump_json() == second.diagnostics[0].model_dump_json()
    finally:
        source.close()


def test_bordered_table_uses_find_tables_within_workload_limits():
    doc = _make_bordered_pdf()
    try:
        result = extract_tables_from_page(doc[0], page_number=9)
        assert any(item.detection_method == TableDetectionMethod.PYMUPDF_FIND_TABLES for item in result.candidates)
        assert not any(item.code == "find_tables_skipped_workload" for item in result.diagnostics)
    finally:
        doc.close()


def test_two_adjacent_bordered_tables_remain_separate_candidates():
    doc = pymupdf.open()
    page = doc.new_page(width=440, height=250)
    for x0, y0, label in ((30, 30, "Revenue"), (240, 135, "Cash flow")):
        x1, xm, x2 = x0, x0 + 75, x0 + 150
        y1, ym, y2 = y0, y0 + 32, y0 + 64
        for x in (x1, xm, x2):
            page.draw_line((x, y1), (x, y2))
        for y in (y1, ym, y2):
            page.draw_line((x1, y), (x2, y))
        page.insert_text((x1 + 4, y1 + 20), "Measure", fontsize=9)
        page.insert_text((xm + 4, y1 + 20), "FY2024", fontsize=9)
        page.insert_text((x1 + 4, ym + 20), label, fontsize=9)
        page.insert_text((xm + 4, ym + 20), "100", fontsize=9)
    try:
        result = extract_tables_from_page(page, page_number=1)
        tables = [candidate for candidate in result.candidates if candidate.detection_method == TableDetectionMethod.PYMUPDF_FIND_TABLES]
        assert len(tables) == 2
        assert sorted(candidate.bbox[1] for candidate in tables) == sorted((30, 135))
        assert all(any(cell.text in {"Revenue", "Cash flow"} for cell in candidate.cells) for candidate in tables)
    finally:
        doc.close()


def test_overlapping_same_origin_candidates_receive_stable_content_order_ids():
    class Table:
        row_count = 1
        col_count = 1
        bbox = (10, 10, 90, 30)
        cells = [(10, 10, 90, 30)]

        def __init__(self, text):
            self.text = text

        def extract(self):
            return [[self.text]]

    class Finder:
        def __init__(self, tables):
            self.tables = tables

    class Page:
        rotation = 0
        rect = pymupdf.Rect(0, 0, 100, 100)
        mediabox = pymupdf.Rect(0, 0, 100, 100)

        def __init__(self, order):
            self.order = order

        def get_drawings(self):
            return [{}]

        def get_text(self, kind):
            return []

        def find_tables(self):
            return Finder([Table(item) for item in self.order])

    first = extract_tables_from_page(Page(["Zeta", "Alpha"]), 1)
    second = extract_tables_from_page(Page(["Alpha", "Zeta"]), 1)
    assert [(item.table_id, item.cells[0].text) for item in first.candidates] == [
        (item.table_id, item.cells[0].text) for item in second.candidates
    ]
    assert [item.cells[0].text for item in first.candidates] == ["Alpha", "Zeta"]


def test_find_tables_exception_does_not_drop_parser_text_or_scoring_result():
    from app.pdf_parser import parse_pdf_page

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
    assert parsed.score_breakdown is not None
    assert any(item.code == "find_tables_failed" for item in parsed.table_diagnostics)


def test_repeated_kpi_cards_preserve_heading_label_value_and_unresolved_semantics():
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=250)
    for y, group, value in ((70, "SEGMENT ALPHA", "£10m"), (150, "SEGMENT BETA", "£12m"), (220, "SEGMENT GAMMA", "£14m")):
        page.insert_text((35, y), group, fontsize=9)
        page.insert_text((235, y), "UNDERLYING OPERATING PROFIT", fontsize=9)
        page.insert_text((235, y + 20), value, fontsize=9)
    try:
        result = extract_tables_from_page(page, page_number=5, allow_find_tables=False)
        groups = [candidate for candidate in result.candidates if candidate.detection_method == TableDetectionMethod.LABEL_VALUE_GROUPS]
        assert len(groups) == 1
        candidate = groups[0]
        assert candidate.confidence is None
        assert any(d.code == "group_semantics_unresolved" for d in candidate.diagnostics)
        by_row = {}
        for cell in candidate.cells:
            by_row.setdefault(cell.row_index, {})[cell.column_index] = cell
        assert len(by_row) == 3
        for row in by_row.values():
            assert row[0].text.startswith("SEGMENT")
            assert row[1].text == "UNDERLYING OPERATING PROFIT"
            assert row[2].text.startswith("£")
            assert row[0].bbox and row[1].bbox and row[2].bbox
        assert any(d.code == "find_tables_skipped_workload" for d in result.diagnostics)
    finally:
        doc.close()


def test_one_off_definition_pair_is_not_a_confident_group():
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=160)
    page.insert_text((35, 60), "OPERATING MARGIN", fontsize=9)
    page.insert_text((35, 80), "12.5%", fontsize=9)
    page.insert_text((35, 120), "Operating margin describes profitability.", fontsize=9)
    try:
        result = extract_tables_from_page(page, page_number=1, allow_find_tables=False)
        assert not any(candidate.detection_method == TableDetectionMethod.LABEL_VALUE_GROUPS for candidate in result.candidates)
    finally:
        doc.close()


def test_aligned_table_keeps_internal_subheading_and_header_row_traceable():
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=250)
    entries = [
        (35, 45, "Measure"), (190, 45, "FY2024"), (300, 45, "FY2025"),
        (35, 70, "Revenue"), (190, 70, "100"), (300, 70, "110"),
        (35, 90, "Profit"), (190, 90, "20"), (300, 90, "25"),
        (35, 110, "SEGMENT B"),
        (35, 130, "Revenue"), (190, 130, "50"), (300, 130, "55"),
        (35, 150, "Profit"), (190, 150, "10"), (300, 150, "12"),
        (35, 170, "Margin"), (190, 170, "20%"), (300, 170, "21%"),
    ]
    for x, y, text in entries:
        page.insert_text((x, y), text, fontsize=9)
    try:
        result = extract_tables_from_page(page, page_number=2, allow_find_tables=False)
        candidates = [candidate for candidate in result.candidates if candidate.detection_method == TableDetectionMethod.WORD_GEOMETRY]
        assert candidates
        candidate = max(candidates, key=lambda item: len(item.cells))
        assert any(cell.text == "SEGMENT B" for cell in candidate.cells)
        assert any(d.code == "internal_subheading_retained" for d in candidate.diagnostics)
        header_cells = [cell for cell in candidate.cells if cell.text in {"FY2024", "FY2025"}]
        assert len(header_cells) == 2
        for cell in header_cells:
            governed = [value for value in candidate.cells if value.column_index == cell.column_index and value.row_index > cell.row_index]
            assert governed
    finally:
        doc.close()


@pytest.mark.parametrize("rotation", [180, 270])
def test_rotations_180_and_270_keep_unrotated_bounded_geometry(rotation):
    doc = _make_bordered_pdf(rotation=rotation)
    try:
        page = doc[0]
        first = extract_tables_from_page(page, page_number=6)
        second = extract_tables_from_page(page, page_number=6)
        candidate = next(item for item in first.candidates if item.detection_method == TableDetectionMethod.PYMUPDF_FIND_TABLES)
        plain = _make_bordered_pdf()
        try:
            baseline = extract_tables_from_page(plain[0], page_number=6).candidates[0]
            assert candidate.bbox == pytest.approx(baseline.bbox)
            assert [(cell.row_index, cell.column_index, cell.text) for cell in candidate.cells] == [
                (cell.row_index, cell.column_index, cell.text) for cell in baseline.cells
            ]
            assert candidate.bbox[2] <= page.mediabox.width and candidate.bbox[3] <= page.mediabox.height
            assert all(
                cell.bbox is None or (
                    0 <= cell.bbox[0] <= cell.bbox[2] <= page.mediabox.width
                    and 0 <= cell.bbox[1] <= cell.bbox[3] <= page.mediabox.height
                    and candidate.bbox[0] <= cell.bbox[0] <= cell.bbox[2] <= candidate.bbox[2]
                    and candidate.bbox[1] <= cell.bbox[1] <= cell.bbox[3] <= candidate.bbox[3]
                )
                for cell in candidate.cells
            )
            assert candidate.model_dump_json() == next(item for item in second.candidates if item.detection_method == TableDetectionMethod.PYMUPDF_FIND_TABLES).model_dump_json()
        finally:
            plain.close()
    finally:
        doc.close()
