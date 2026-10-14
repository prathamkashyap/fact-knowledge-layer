"""Synthetic contract tests for table layout and unresolved row contexts."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

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
