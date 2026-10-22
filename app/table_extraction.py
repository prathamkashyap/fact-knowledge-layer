"""Standalone, serializable contract for PDF table-layout hypotheses.

This module deliberately does not parse PDFs or create :class:`Fact` objects.
A ``TableCandidate`` describes structure proposed by a layout detector; the
separate row/value context records what is and is not supported by that
structure. Later extraction code must resolve the required dimensions before
it can map a context into a production fact.
"""

from decimal import Decimal
from enum import Enum
from functools import lru_cache
import math
import re
from statistics import median
from typing import Any, Optional, Tuple

import pymupdf

from pydantic import BaseModel, Field, field_validator, model_validator


BoundingBox = Tuple[float, float, float, float]


class TableDetectionMethod(str, Enum):
    """How a table-layout hypothesis was produced."""

    PYMUPDF_FIND_TABLES = "pymupdf_find_tables"
    WORD_GEOMETRY = "word_geometry"
    LABEL_VALUE_GROUPS = "label_value_groups"
    SYNTHETIC = "synthetic"
    OTHER = "other"


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ContextDimension(str, Enum):
    """Dimensions whose source association can be resolved independently."""

    ROW_VALUE_ASSOCIATION = "row_value_association"
    HEADER_ASSOCIATION = "header_association"
    ENTITY = "entity"
    PERIOD = "period"
    UNIT = "unit"
    SCALE = "scale"
    REPORTING_BASIS = "reporting_basis"


class ResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    NOT_PRESENT = "not_present"


class PeriodType(str, Enum):
    FISCAL_YEAR = "fiscal_year"
    CALENDAR_YEAR = "calendar_year"
    QUARTER = "quarter"
    DATE = "date"
    PERIOD_AVERAGE = "period_average"
    POINT_IN_TIME = "point_in_time"
    UNKNOWN = "unknown"


class UnitKind(str, Enum):
    CURRENCY = "currency"
    PERCENT = "percent"
    PERCENTAGE_POINT = "percentage_point"
    PER_SHARE = "per_share"
    COUNT = "count"
    RATIO = "ratio"
    NUMBER = "number"


class MagnitudeScale(str, Enum):
    NONE = "none"
    THOUSAND = "thousand"
    MILLION = "million"
    BILLION = "billion"


class ContextSourceKind(str, Enum):
    ROW_LABEL = "row_label"
    COLUMN_HEADER = "column_header"
    TABLE_HEADING = "table_heading"
    SECTION_HEADING = "section_heading"
    DOCUMENT_SCOPE = "document_scope"
    FOOTNOTE = "footnote"
    OTHER = "other"


class TableDiagnostic(BaseModel):
    """Inspectable detector or interpretation issue; it does not imply a fact."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: DiagnosticSeverity = DiagnosticSeverity.WARNING
    dimension: Optional[ContextDimension] = None


class TableExtractionResult(BaseModel):
    """Page-local layout hypotheses and diagnostics from a best-effort adapter."""

    candidates: list["TableCandidate"] = Field(default_factory=list)
    diagnostics: list[TableDiagnostic] = Field(default_factory=list)


class PageComplexity(BaseModel):
    """Cheap, deterministic workload measurements for PyMuPDF's table detector."""

    drawing_count: Optional[int] = None
    word_count: Optional[int] = None
    text_char_count: Optional[int] = None
    measurement_errors: list[str] = Field(default_factory=list)


# Preserve the established 30-drawing ceiling. The additional volume ceilings
# bound routine pages by both text complexity dimensions; they are conservative
# workload filters, not runtime guarantees. Development smoke pages are below
# the text ceilings but exceed the drawing ceiling and therefore use geometry.
MAX_FIND_TABLES_DRAWINGS = 30
MAX_FIND_TABLES_WORDS = 3_000
MAX_FIND_TABLES_TEXT_CHARS = 20_000


def measure_page_complexity(page: Any, raw_text: Optional[str] = None, words: Optional[list] = None) -> PageComplexity:
    """Measure the page properties the workload guard needs.

    Text volume is measured first. Enumerating vector drawings is the most
    expensive of these measurements on vector-heavy pages, so it is deferred
    until the page's text volume is known to be inside the workload bounds.
    A page outside those bounds is already excluded from the costly detector,
    so it does not pay for the drawing measurement.
    """
    measurements = PageComplexity()
    if raw_text is None:
        try:
            raw_text = page.get_text("text") or ""
        except Exception as exc:
            measurements.measurement_errors.append(f"text:{type(exc).__name__}")
    if raw_text is not None:
        measurements.text_char_count = len(raw_text)
    if words is None:
        try:
            words = page.get_text("words") or []
        except Exception as exc:
            measurements.measurement_errors.append(f"words:{type(exc).__name__}")
    if words is not None:
        measurements.word_count = len(words)
    within_text_bounds = (
        measurements.measurement_errors == []
        and measurements.word_count is not None
        and measurements.text_char_count is not None
        and measurements.word_count <= MAX_FIND_TABLES_WORDS
        and measurements.text_char_count <= MAX_FIND_TABLES_TEXT_CHARS
    )
    if within_text_bounds:
        try:
            measurements.drawing_count = len(page.get_drawings())
        except Exception as exc:
            measurements.measurement_errors.append(f"drawings:{type(exc).__name__}")
    return measurements


def table_detector_workload_allowed(complexity: PageComplexity) -> tuple[bool, str]:
    """Apply fixed workload bounds; missing measurements abstain from the costly detector."""
    if complexity.measurement_errors:
        return False, "measurement_failed"
    if complexity.word_count is None or complexity.text_char_count is None:
        return False, "measurement_unavailable"
    if complexity.word_count > MAX_FIND_TABLES_WORDS:
        return False, "word_limit"
    if complexity.text_char_count > MAX_FIND_TABLES_TEXT_CHARS:
        return False, "text_limit"
    if complexity.drawing_count is None:
        return False, "measurement_unavailable"
    if complexity.drawing_count == 0:
        return False, "no_vector_drawings"
    if complexity.drawing_count > MAX_FIND_TABLES_DRAWINGS:
        return False, "drawing_limit"
    return True, "within_limits"


def _workload_diagnostic_message(reason: str, complexity: PageComplexity) -> str:
    return (
        f"find_tables skipped ({reason}); drawing_count={complexity.drawing_count}, "
        f"word_count={complexity.word_count}, text_char_count={complexity.text_char_count}, "
        f"measurement_errors={','.join(complexity.measurement_errors) or 'none'}. "
        "Word-geometry fallback remains enabled."
    )


class TableCell(BaseModel):
    """One source cell, identified by its zero-based origin and optional span.

    Bounding boxes use the page-coordinate system supplied by the parser; this
    contract does not normalize or fabricate coordinates.
    """

    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    text: str
    bbox: Optional[BoundingBox] = None
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, bbox: Optional[BoundingBox]) -> Optional[BoundingBox]:
        return _validate_bbox(bbox)


class CellReference(BaseModel):
    """Reference to a cell's origin within its parent table candidate."""

    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)


class TableCandidate(BaseModel):
    """A page-local layout hypothesis, not a semantic table or factual claim.

    ``table_id`` is supplied by the detector and must be deterministic for the
    same page/table ordering. Cells are stored in row-major origin order.
    """

    table_id: str = Field(min_length=1)
    page_number: int = Field(gt=0, description="1-based physical PDF page number")
    bbox: Optional[BoundingBox] = None
    detection_method: TableDetectionMethod
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Detector confidence only; it is not claim or fact confidence.",
    )
    diagnostics: list[TableDiagnostic] = Field(default_factory=list)
    cells: list[TableCell] = Field(default_factory=list)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, bbox: Optional[BoundingBox]) -> Optional[BoundingBox]:
        return _validate_bbox(bbox)

    @model_validator(mode="after")
    def validate_cell_layout(self) -> "TableCandidate":
        origins = [(cell.row_index, cell.column_index) for cell in self.cells]
        if origins != sorted(origins):
            raise ValueError("cells must be ordered by row_index then column_index")
        if len(origins) != len(set(origins)):
            raise ValueError("cell origins must be unique")

        occupied: set[tuple[int, int]] = set()
        for cell in self.cells:
            for row in range(cell.row_index, cell.row_index + cell.row_span):
                for column in range(cell.column_index, cell.column_index + cell.column_span):
                    coordinate = (row, column)
                    if coordinate in occupied:
                        raise ValueError("cell spans must not overlap")
                    occupied.add(coordinate)
        return self

    def get_cell(self, reference: CellReference) -> Optional[TableCell]:
        """Return a cell by its origin, or ``None`` when the reference is absent."""
        return next(
            (
                cell
                for cell in self.cells
                if (cell.row_index, cell.column_index)
                == (reference.row_index, reference.column_index)
            ),
            None,
        )


class DimensionResolution(BaseModel):
    """Status and reason for one interpretation dimension."""

    dimension: ContextDimension
    status: ResolutionStatus
    reason: Optional[str] = None

    @model_validator(mode="after")
    def require_reason_when_not_resolved(self) -> "DimensionResolution":
        if self.status != ResolutionStatus.RESOLVED and not (self.reason or "").strip():
            raise ValueError("non-resolved dimensions require an explanatory reason")
        return self


class ContextSource(BaseModel):
    """Raw source excerpt and optional layout locator for an interpretation."""

    kind: ContextSourceKind
    text: str = Field(min_length=1)
    cell: Optional[CellReference] = None
    bbox: Optional[BoundingBox] = None

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, bbox: Optional[BoundingBox]) -> Optional[BoundingBox]:
        return _validate_bbox(bbox)


class UnitInterpretation(BaseModel):
    """Unnormalized unit components; scale is separate from currency."""

    kind: UnitKind
    raw_unit: str = ""
    currency_code: Optional[str] = None
    scale: MagnitudeScale = MagnitudeScale.NONE

    @field_validator("currency_code")
    @classmethod
    def validate_currency_code(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and (len(value) != 3 or not value.isalpha() or value != value.upper()):
            raise ValueError("currency_code must be a three-letter uppercase code")
        return value

    @model_validator(mode="after")
    def validate_currency_components(self) -> "UnitInterpretation":
        if self.kind == UnitKind.CURRENCY:
            if self.currency_code is None:
                raise ValueError("resolved currency units require an explicit currency_code")
        elif self.currency_code is not None:
            raise ValueError("currency_code is only valid for currency units")
        return self


class ParsedValueInterpretation(BaseModel):
    """Raw displayed value plus an optional exact, still-scaled interpretation."""

    raw_text: str = Field(min_length=1)
    numeric_value: Optional[Decimal] = None
    unit: Optional[UnitInterpretation] = None

    @model_validator(mode="after")
    def keep_value_and_unit_together(self) -> "ParsedValueInterpretation":
        if (self.numeric_value is None) != (self.unit is None):
            raise ValueError("numeric_value and unit must either both be present or both be absent")
        if self.numeric_value is not None and not self.numeric_value.is_finite():
            raise ValueError("numeric_value must be finite")
        return self


class PeriodInterpretation(BaseModel):
    """Period text and type as supported by its source context."""

    raw_text: str = Field(min_length=1)
    normalized_text: Optional[str] = None
    period_type: PeriodType = PeriodType.UNKNOWN
    sources: list[ContextSource] = Field(default_factory=list)


class EntityInterpretation(BaseModel):
    """Source-supported issuer, jurisdiction, and segment context."""

    issuer: Optional[str] = None
    jurisdiction: Optional[str] = None
    segment: Optional[str] = None
    sources: list[ContextSource] = Field(default_factory=list)


class QualifierReference(BaseModel):
    """Qualifier or footnote kept with its source rather than flattened away."""

    raw_text: str = Field(min_length=1)
    interpreted_as: Optional[str] = None
    source: ContextSource


class TableRowValueContext(BaseModel):
    """A proposed row/value association with explicit resolution and provenance.

    This is a source-context record only. It intentionally has no conversion to
    ``Fact``: a later phase must decide whether the row is a claim and whether
    each required dimension is sufficiently supported.
    """

    page_number: int = Field(gt=0)
    table_id: str = Field(min_length=1)
    row_index: int = Field(ge=0)
    value_column_index: int = Field(ge=0)
    row_label: Optional[CellReference] = None
    column_header_path: list[CellReference] = Field(default_factory=list)
    value_cell: Optional[CellReference] = None
    raw_display_value: Optional[str] = None
    value: Optional[ParsedValueInterpretation] = None
    period: Optional[PeriodInterpretation] = None
    entity: Optional[EntityInterpretation] = None
    qualifiers: list[QualifierReference] = Field(default_factory=list)
    resolutions: list[DimensionResolution] = Field(default_factory=list)
    diagnostics: list[TableDiagnostic] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_explicit_resolution(self) -> "TableRowValueContext":
        dimensions = [item.dimension for item in self.resolutions]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("each context dimension may have only one resolution record")

        by_dimension = {item.dimension: item for item in self.resolutions}
        association = by_dimension.get(ContextDimension.ROW_VALUE_ASSOCIATION)
        if association and association.status == ResolutionStatus.RESOLVED:
            if self.row_label is None or self.value_cell is None or self.raw_display_value is None:
                raise ValueError("resolved row/value association requires label, value cell, and raw value")
            if self.value is None:
                raise ValueError("resolved row/value association requires an interpretation record")

        header = by_dimension.get(ContextDimension.HEADER_ASSOCIATION)
        if header and header.status == ResolutionStatus.RESOLVED and not self.column_header_path:
            raise ValueError("resolved header association requires a header path")

        unit = by_dimension.get(ContextDimension.UNIT)
        scale = by_dimension.get(ContextDimension.SCALE)
        if self.value and self.value.numeric_value is not None:
            if unit is None or unit.status != ResolutionStatus.RESOLVED:
                raise ValueError("parsed units require a resolved unit dimension")
            if scale is None or scale.status != ResolutionStatus.RESOLVED:
                raise ValueError("parsed values require an explicit scale resolution")

        for required in (
            ContextDimension.ROW_VALUE_ASSOCIATION,
            ContextDimension.HEADER_ASSOCIATION,
            ContextDimension.ENTITY,
            ContextDimension.PERIOD,
            ContextDimension.UNIT,
            ContextDimension.SCALE,
        ):
            if required not in by_dimension:
                raise ValueError(f"missing explicit resolution for {required.value}")

        return self

    @property
    def is_context_resolved(self) -> bool:
        """True only when every required dimension has a supported resolution.

        Explicitly absent information is preserved, but it is not sufficient to
        treat the context as complete for factual extraction.
        """
        required = (
            ContextDimension.ROW_VALUE_ASSOCIATION,
            ContextDimension.HEADER_ASSOCIATION,
            ContextDimension.ENTITY,
            ContextDimension.PERIOD,
            ContextDimension.UNIT,
            ContextDimension.SCALE,
        )
        by_dimension = {item.dimension: item.status for item in self.resolutions}
        return all(
            by_dimension.get(dimension) == ResolutionStatus.RESOLVED
            for dimension in required
        )


def validate_context_provenance(
    table: TableCandidate,
    context: TableRowValueContext,
) -> None:
    """Raise ``ValueError`` if supplied cell references do not match a table.

    Missing references are allowed for unresolved contexts. This function
    verifies only structural provenance; it does not assert semantic validity.
    """
    if context.page_number != table.page_number or context.table_id != table.table_id:
        raise ValueError("row/value context must identify its source page and table")

    refs = list(context.column_header_path)
    refs.extend(ref for ref in (context.row_label, context.value_cell) if ref is not None)
    if context.period is not None:
        refs.extend(source.cell for source in context.period.sources if source.cell is not None)
    if context.entity is not None:
        refs.extend(source.cell for source in context.entity.sources if source.cell is not None)
    refs.extend(
        qualifier.source.cell
        for qualifier in context.qualifiers
        if qualifier.source.cell is not None
    )
    cells_by_origin = {
        (cell.row_index, cell.column_index): cell
        for cell in table.cells
    }
    for ref in refs:
        if (ref.row_index, ref.column_index) not in cells_by_origin:
            raise ValueError(f"cell reference ({ref.row_index}, {ref.column_index}) is absent")

    if context.row_label is not None and context.row_label.row_index != context.row_index:
        raise ValueError("row label reference must be on the context row")
    if context.value_cell is not None:
        if context.value_cell.row_index != context.row_index:
            raise ValueError("value cell reference must be on the context row")
        if context.value_cell.column_index != context.value_column_index:
            raise ValueError("value cell reference must match value_column_index")

    for header_ref in context.column_header_path:
        header = cells_by_origin[(header_ref.row_index, header_ref.column_index)]
        governs_value_column = (
            header.column_index
            <= context.value_column_index
            < header.column_index + header.column_span
        )
        if header.row_index >= context.row_index or not governs_value_column:
            raise ValueError("header references must precede and govern the value column")


def _validate_bbox(bbox: Optional[BoundingBox]) -> Optional[BoundingBox]:
    if bbox is None:
        return None
    if len(bbox) != 4 or not all(math.isfinite(float(value)) for value in bbox):
        raise ValueError("bounding boxes require four finite coordinates")
    x0, y0, x1, y1 = bbox
    if x1 < x0 or y1 < y0:
        raise ValueError("bounding box coordinates must be ordered as x0, y0, x1, y1")
    return bbox


def extract_tables_from_page(
    page: Any,
    page_number: int,
    *,
    allow_find_tables: Optional[bool] = None,
    complexity: Optional["PageComplexity"] = None,
    words: Optional[list] = None,
    find_tables_result: Any = None,
    find_tables_error: Optional[str] = None,
    find_tables_was_called: bool = False,
) -> TableExtractionResult:
    """Adapt PyMuPDF table and word geometry without asserting factual meaning.

    Coordinates are unrotated PyMuPDF page coordinates: top-left origin, x
    increases rightward, y increases downward, and units are PDF points. Word
    boxes already use this space. ``find_tables`` boxes are mapped through the
    page's derotation matrix when the page has display rotation. Returned cell
    origins are then ordered top-to-bottom and left-to-right in that same space.

    Borderless detection is deliberately conservative. It requires repeated
    column gaps, at least three aligned rows, and numeric-looking values in at
    least two rows. Its structural score is not a probability, so confidence
    remains unknown.
    """
    result = TableExtractionResult()
    if page_number < 1:
        result.diagnostics.append(_diagnostic("invalid_page_number", "Physical page number must be positive."))
        return result

    complexity = complexity or measure_page_complexity(page)
    allowed, reason = table_detector_workload_allowed(complexity)
    if allow_find_tables is not None:
        allowed = allowed and allow_find_tables
        if not allow_find_tables:
            reason = "caller_disabled"
    bordered: list[TableCandidate] = []
    finder = None
    find_tables = getattr(page, "find_tables", None)
    if not callable(find_tables):
        result.diagnostics.append(_diagnostic("find_tables_unavailable", "This PyMuPDF page has no find_tables() API."))
    elif not allowed:
        result.diagnostics.append(_diagnostic(
            "find_tables_skipped_workload",
            _workload_diagnostic_message(reason, complexity),
            DiagnosticSeverity.INFO,
        ))
    elif find_tables_was_called:
        finder = find_tables_result
        if find_tables_error:
            result.diagnostics.append(_diagnostic(
                "find_tables_failed", f"PyMuPDF table detection failed: {find_tables_error}."
            ))
    else:
        try:
            finder = find_tables()
        except Exception as exc:  # PyMuPDF versions can reject unsupported page layouts.
            result.diagnostics.append(_diagnostic(
                "find_tables_failed", f"PyMuPDF table detection failed: {type(exc).__name__}."
            ))

    raw_tables = getattr(finder, "tables", ()) if finder is not None else ()
    for table_index, table in enumerate(raw_tables or ()):
        try:
            candidate = _candidate_from_pymupdf_table(page, page_number, table_index, table)
        except Exception as exc:
            result.diagnostics.append(_diagnostic(
                "malformed_find_tables_result",
                f"Skipped a malformed PyMuPDF table result ({type(exc).__name__}).",
            ))
            continue
        if candidate is None:
            result.diagnostics.append(_diagnostic(
                "empty_find_tables_result", f"Skipped PyMuPDF table result {table_index}: no usable cell grid."
            ))
            continue
        bordered.append(candidate)

    result.candidates.extend(bordered)
    try:
        words = words if words is not None else (page.get_text("words") or [])
        borderless, diagnostics = _borderless_candidates(page, page_number, words, bordered)
        result.candidates.extend(borderless)
        result.diagnostics.extend(diagnostics)
        groups, group_diagnostics = _label_value_group_candidates(page, page_number, words, bordered)
        result.candidates.extend(groups)
        result.diagnostics.extend(group_diagnostics)
    except Exception as exc:
        result.diagnostics.append(_diagnostic(
            "word_geometry_failed", f"PyMuPDF word geometry failed: {type(exc).__name__}."
        ))

    # IDs are independent of finder iteration order; geometry defines page order.
    result.candidates.sort(key=lambda item: (
        item.bbox[1] if item.bbox else float("inf"),
        item.bbox[0] if item.bbox else float("inf"),
        item.detection_method.value,
        tuple(
            (cell.row_index, cell.column_index, cell.bbox or (), cell.text)
            for cell in item.cells
        ),
        item.model_dump_json(exclude={"table_id"}),
    ))
    for index, candidate in enumerate(result.candidates):
        candidate.table_id = f"p{page_number}-t{index:02d}"
    return result


def _candidate_from_pymupdf_table(page: Any, page_number: int, index: int, table: Any) -> Optional[TableCandidate]:
    row_count = int(table.row_count)
    column_count = int(table.col_count)
    extracted = table.extract()
    if row_count < 1 or column_count < 1 or not extracted:
        return None
    cells_raw = list(table.cells or [])
    if len(cells_raw) != row_count * column_count:
        return None

    derotation = getattr(page, "derotation_matrix", None)
    rotation = int(getattr(page, "rotation", 0) or 0)
    page_bbox = _unrotated_page_bbox(page)
    grid: list[tuple[int, int, str, Optional[BoundingBox]]] = []
    # PyMuPDF Table.cells is column-major; extract() is row-major. The mapping
    # is checked against a synthetic fixture and each cell box is normalized.
    for row in range(row_count):
        row_values = extracted[row] if row < len(extracted) else []
        for column in range(column_count):
            flat_index = column * row_count + row
            raw_box = cells_raw[flat_index]
            text = "" if column >= len(row_values) or row_values[column] is None else str(row_values[column])
            bbox = _transform_bbox(raw_box, derotation if rotation else None)
            if bbox is not None:
                bbox = _clip_or_reject_bbox(bbox, page_bbox)
            grid.append((row, column, text, bbox))

    boxes = [item[3] for item in grid if item[3] is not None]
    if not boxes:
        # Without boxes, preserve the API row/column matrix but state that
        # orientation and merged-cell association cannot be checked.
        cells = [TableCell(row_index=r, column_index=c, text=text) for r, c, text, _ in grid]
        bbox = None
        diagnostics = [_diagnostic("cell_geometry_missing", "PyMuPDF returned cells without geometry.")]
    else:
        row_edges = _unique_edges([v for box in boxes for v in (box[1], box[3])])
        column_edges = _unique_edges([v for box in boxes for v in (box[0], box[2])])
        cells = []
        for _, _, text, cell_bbox in grid:
            if cell_bbox is None:
                continue
            row = _interval_index((cell_bbox[1] + cell_bbox[3]) / 2, row_edges)
            column = _interval_index((cell_bbox[0] + cell_bbox[2]) / 2, column_edges)
            row_span = _covered_intervals(cell_bbox[1], cell_bbox[3], row_edges)
            column_span = _covered_intervals(cell_bbox[0], cell_bbox[2], column_edges)
            cells.append(TableCell(
                row_index=row,
                column_index=column,
                text=text,
                bbox=cell_bbox,
                row_span=row_span,
                column_span=column_span,
            ))
        cells.sort(key=lambda cell: (cell.row_index, cell.column_index))
        table_box = _transform_bbox(getattr(table, "bbox", None), derotation if rotation else None)
        bbox = _clip_or_reject_bbox(table_box, page_bbox) if table_box else _union_boxes(boxes)
        diagnostics = []
        if rotation:
            diagnostics.append(_diagnostic(
                "rotated_find_tables_normalized",
                "Table and cell boxes were derotated into unrotated page coordinates; cell order follows geometry.",
                DiagnosticSeverity.INFO,
            ))

    # Some PyMuPDF versions expose merged cells as None slots. A cell span is
    # inferred only from its returned rectangle crossing observed grid edges.
    if len(cells) != row_count * column_count:
        diagnostics.append(_diagnostic(
            "non_rectangular_or_merged_grid",
            "The detector returned a sparse or merged grid; absent slots remain absent and are not fabricated.",
            DiagnosticSeverity.INFO,
        ))
    if row_count == 1:
        diagnostics.append(_diagnostic(
            "single_row_layout",
            "A single detected row is retained as a layout hypothesis and is not sufficient to establish a factual table.",
        ))
    return TableCandidate(
        table_id=f"p{page_number}-detected-{index}",
        page_number=page_number,
        bbox=bbox,
        detection_method=TableDetectionMethod.PYMUPDF_FIND_TABLES,
        confidence=None,
        diagnostics=diagnostics,
        cells=cells,
    )


def _borderless_candidates(
    page: Any,
    page_number: int,
    words: list,
    bordered: list[TableCandidate],
) -> tuple[list[TableCandidate], list[TableDiagnostic]]:
    if len(words) < 6:
        return [], [_diagnostic("borderless_insufficient_words", "Too few words to assess repeated table structure.", DiagnosticSeverity.INFO)]
    parsed = []
    heights = []
    for word in words:
        if len(word) < 5:
            continue
        x0, y0, x1, y1, text = word[:5]
        try:
            box = _validate_bbox((float(x0), float(y0), float(x1), float(y1)))
        except (TypeError, ValueError):
            continue
        if box is None or not str(text).strip() or box[3] <= box[1]:
            continue
        parsed.append((box, str(text)))
        heights.append(box[3] - box[1])
    if len(parsed) < 6:
        return [], [_diagnostic("borderless_insufficient_geometry", "Word boxes do not provide enough usable geometry.", DiagnosticSeverity.INFO)]

    line_tolerance = max(1.0, median(heights) * 0.45)
    lines = _group_words_into_lines(parsed, line_tolerance)
    if len(lines) < 3:
        return [], [_diagnostic("borderless_insufficient_rows", "Fewer than three text baselines were found.", DiagnosticSeverity.INFO)]
    gap_threshold = max(7.0, median(heights) * 1.25)
    segmented = [(line, _split_line_cells(line, gap_threshold)) for line in lines]
    gap_positions = [
        (cells[i][0][2] + cells[i + 1][0][0]) / 2
        for _, cells in segmented
        for i in range(len(cells) - 1)
    ]
    if not gap_positions:
        return [], [_diagnostic("borderless_no_repeated_columns", "No inter-column gaps were found.", DiagnosticSeverity.INFO)]

    gap_tolerance = max(3.0, median(heights) * 1.1)
    clusters = _cluster_positions(gap_positions, gap_tolerance)
    support_required = max(2, (len(lines) + 1) // 2)
    boundaries = sorted(center for center, support in clusters if support >= support_required)
    if not boundaries:
        return [], [_diagnostic(
            "borderless_no_repeated_columns",
            "Column gaps did not recur across enough baselines; no grid was inferred.",
            DiagnosticSeverity.INFO,
        )]

    row_records = []
    numeric_rows = 0
    conflicting_rows = []
    for line_index, (line, segments) in enumerate(segmented):
        if not segments:
            continue
        row_cells: dict[int, tuple[str, BoundingBox]] = {}
        for box, text in segments:
            center_x = (box[0] + box[2]) / 2
            column = sum(boundary < center_x for boundary in boundaries)
            if column in row_cells:
                # Conflicting segments in one inferred column make this row
                # unresolved; do not concatenate across a dubious boundary.
                conflicting_rows.append(line_index)
                row_cells = {}
                break
            row_cells[column] = (text, box)
        if len(row_cells) >= 2:
            has_numeric_value = any(_looks_numeric(text) for col, (text, _) in row_cells.items() if col > 0)
            numeric_rows += int(has_numeric_value)
            row_records.append((line_index, row_cells))

    conflict_diagnostics = []
    if conflicting_rows:
        conflict_diagnostics.append(_diagnostic(
            "ambiguous_column_boundary",
            f"{len(conflicting_rows)} row(s) placed several segments in one inferred column and "
            "were left unresolved; the dropped rows are not part of any candidate.",
        ))

    if len(row_records) < 3 or numeric_rows < 2:
        aligned, aligned_diagnostics = _aligned_numeric_candidates(page_number, parsed, heights, bordered)
        if aligned:
            return aligned, [*conflict_diagnostics, _diagnostic(
                "gap_grid_abstained",
                "Gap-based column segmentation was insufficient; repeated numeric alignment supplied the candidate grid.",
                DiagnosticSeverity.INFO,
            )]
        return [], [*conflict_diagnostics, _diagnostic(
            "borderless_structure_insufficient",
            "Repeated gaps were found, but at least three aligned rows and numeric values in two rows are required.",
            DiagnosticSeverity.INFO,
        ), *aligned_diagnostics]

    # A broad page-wide cluster of gaps can combine unrelated prose. Retain
    # only the longest local run of aligned rows, allowing one intervening
    # heading/baseline but not stitching distant page regions together.
    # Adjacent baselines are not enough to continue a run: two tables that are
    # separated by whitespace only also land on neighbouring text lines. The
    # row rhythm decides instead, and it is compared with the neighbouring
    # rhythm rather than with a page-wide average. A row whose value cells are
    # all reporting periods is a header row, and a header row governs the rows
    # below it, so it also ends the run above it.
    row_extents = [
        _row_extent([box for _, box in row.values()]) for _, row in row_records
    ]
    separated_pairs = _separated_row_pairs(_row_pitches(row_extents), median(heights))
    runs: list[list[tuple[int, dict[int, tuple[str, BoundingBox]]]]] = []
    for record_index, record in enumerate(row_records):
        if not runs:
            runs.append([record])
            continue
        previous = runs[-1][-1]
        gap = record[0] - previous[0]
        shared = set(record[1]) & set(previous[1])
        is_separated = (record_index - 1) in separated_pairs
        if _is_period_header_row(record[1]):
            # A header row governs the rows below it, so it always starts a
            # new block even when it follows the previous block immediately.
            runs.append([record])
            continue
        if gap == 1 and len(shared) >= 2 and not is_separated:
            runs[-1].append(record)
            continue
        between = segmented[previous[0] + 1:record[0]]
        heading_rows = []
        for heading_index, (heading_line, heading_segments) in enumerate(between, start=previous[0] + 1):
            if len(heading_segments) != 1:
                continue
            heading_box, heading_text = heading_segments[0]
            center = (heading_box[0] + heading_box[2]) / 2
            heading_column = sum(boundary < center for boundary in boundaries)
            if heading_column == 0 and not _looks_numeric(heading_text) and not _is_period_token(heading_text):
                heading_rows.append((heading_index, {0: (heading_text, heading_box)}))
        bridge = (
            gap in {2, 3}
            and len(shared) >= 2
            and len(heading_rows) == gap - 1
            and not is_separated
            and 0 in previous[1] and 0 in record[1]
            and abs(previous[1][0][1][0] - record[1][0][1][0]) <= max(20.0, median(heights) * 4)
        )
        if bridge:
            runs[-1].extend(heading_rows)
            runs[-1].append(record)
        else:
            runs.append([record])
    runs = [run for run in runs if len(run) >= 3 and sum(
        any(col > 0 and _looks_numeric(text) for col, (text, _) in row.items()) for _, row in run
    ) >= 2]

    candidates = []
    for run_index, run in enumerate(runs):
        all_boxes = [box for _, row in run for _, box in row.values()]
        bbox = _union_boxes(all_boxes)
        if any(_boxes_overlap_ratio(bbox, item.bbox) > 0.5 for item in bordered if item.bbox):
            continue
        column_indices = sorted({col for _, row in run for col in row})
        cells = []
        for row_index, (_, row) in enumerate(run):
            for col, (text, cell_box) in row.items():
                cells.append(TableCell(row_index=row_index, column_index=col, text=text, bbox=cell_box))
        cells, header_note = _align_period_header_row(cells)
        diagnostics = [
            _diagnostic(
                "borderless_repeated_structure",
                f"Word geometry formed {len(run)} adjacent rows across {len(column_indices)} recurring columns; this is a structural heuristic, not calibrated confidence.",
                DiagnosticSeverity.INFO,
            )
        ]
        if header_note:
            diagnostics.append(_diagnostic("period_header_alignment", header_note, DiagnosticSeverity.INFO))
        if any(len(row) == 1 and 0 in row for _, row in run):
            diagnostics.append(_diagnostic(
                "internal_subheading_retained",
                "A text-only subheading was retained because recurring columns and the label region continue across it.",
                DiagnosticSeverity.INFO,
            ))
        if len(column_indices) >= 3:
            for row_index, (_, row) in enumerate(run[:-1]):
                contains_value = any(_looks_numeric(text) for text, _ in row.values())
                if len(row) < len(column_indices) and not contains_value:
                    diagnostics.append(_diagnostic(
                        "ambiguous_header_span",
                        f"Row {row_index} has fewer occupied cells than the following multi-column structure; merged-header span is unresolved.",
                    ))
        candidates.append(TableCandidate(
            table_id=f"p{page_number}-words-{run_index}",
            page_number=page_number,
            bbox=bbox,
            detection_method=TableDetectionMethod.WORD_GEOMETRY,
            confidence=None,
            diagnostics=diagnostics,
            cells=cells,
        ))
    if not candidates:
        aligned, aligned_diagnostics = _aligned_numeric_candidates(page_number, parsed, heights, bordered)
        if aligned:
            return aligned, [*conflict_diagnostics, _diagnostic(
                "gap_grid_abstained",
                "Gap-based segmentation did not form a local grid; repeated numeric alignment supplied the candidate grid.",
                DiagnosticSeverity.INFO,
            )]
        return [], [*conflict_diagnostics, _diagnostic(
            "borderless_candidate_abstained",
            "Page geometry did not yield a locally repeated grid with sufficient numeric structure.",
            DiagnosticSeverity.INFO,
        ), *aligned_diagnostics]
    return candidates, conflict_diagnostics


def _aligned_numeric_candidates(
    page_number: int,
    words: list[tuple[BoundingBox, str]],
    heights: list[float],
    bordered: list[TableCandidate],
) -> tuple[list[TableCandidate], list[TableDiagnostic]]:
    """Fallback for borderless financial tables whose numeric columns are right-aligned."""
    if not words or not heights:
        return [], []
    lines = _group_words_into_lines(words, max(1.0, median(heights) * 0.45))
    numeric_positions = []
    for line_index, line in enumerate(lines):
        for box, text in line:
            if _looks_numeric(text):
                numeric_positions.append((box[2], line_index))
    if len(numeric_positions) < 6:
        return [], []

    tolerance = max(2.5, median(heights) * 0.45)
    position_clusters: list[list[tuple[float, int]]] = []
    for position in sorted(numeric_positions):
        if not position_clusters or position[0] - median(item[0] for item in position_clusters[-1]) > tolerance:
            position_clusters.append([position])
        else:
            position_clusters[-1].append(position)
    anchors = []
    for cluster in position_clusters:
        support = len({line_index for _, line_index in cluster})
        if support >= 3:
            anchors.append((median(value for value, _ in cluster), support))
    anchors.sort()
    if len(anchors) < 2:
        return [], [_diagnostic(
            "aligned_numeric_columns_insufficient",
            "Fewer than two numeric right-edge positions recur on three baselines.",
            DiagnosticSeverity.INFO,
        )]

    anchor_x = [item[0] for item in anchors]
    row_records = []
    for line_index, line in enumerate(lines):
        assignments: dict[int, list[tuple[BoundingBox, str]]] = {}
        non_numeric = []
        for box, text in line:
            nearest = min(range(len(anchor_x)), key=lambda i: abs(anchor_x[i] - box[2]))
            aligned_right_edge = abs(anchor_x[nearest] - box[2]) <= tolerance
            aligned_placeholder = (
                _is_placeholder(text)
                and abs(anchor_x[nearest] - box[2]) <= tolerance * 1.8
                and box[0] >= anchor_x[0] - tolerance
            )
            if (_looks_numeric(text) and aligned_right_edge) or aligned_placeholder:
                assignments.setdefault(nearest, []).append((box, text))
            else:
                non_numeric.append((box, text))
        if len(assignments) >= 2 and all(len(items) == 1 for items in assignments.values()):
            row_records.append((line_index, assignments, non_numeric))

    if len(row_records) < 3:
        return [], [_diagnostic(
            "aligned_numeric_rows_insufficient",
            "Recurring right-edge anchors did not yield three rows with distinct numeric cells.",
            DiagnosticSeverity.INFO,
        )]

    # Period header lines are collected as source tokens. Which column a token
    # governs is decided later, per candidate, because only a candidate knows
    # which numeric columns it actually covers.
    period_header_lines = []
    for line_index, line in enumerate(lines):
        tokens = sorted(
            ((box, text) for box, text in line if _is_period_token(text)),
            key=lambda item: (item[0][0] + item[0][2]) / 2,
        )
        if len(tokens) >= 2:
            period_header_lines.append((line_index, tokens))

    # Keep local vertical runs. A row continues the previous run only when the
    # columns continue and the local row rhythm continues: adjacent baselines
    # alone are not enough, because two separate tables can be separated by
    # whitespace only and still land on neighbouring text lines. The rhythm is
    # compared with the neighbouring rhythm rather than with a page-wide
    # average, so one loosely spaced table does not change how a denser table
    # on the same page is segmented.
    max_line_gap = max(3, round(28.0 / max(median(heights), 1.0)))
    row_extents = [_row_extent(_record_boxes(record)) for record in row_records]
    separated_pairs = _separated_row_pairs(_row_pitches(row_extents), median(heights))
    runs: list[list[tuple[int, dict[int, list[tuple[BoundingBox, str]]], list[tuple[BoundingBox, str]]]]] = []
    for record_index, record in enumerate(row_records):
        if not runs:
            runs.append([record])
            continue
        previous = runs[-1][-1]
        gap = record[0] - previous[0]
        shared = set(record[1]) & set(previous[1])
        is_separated = (record_index - 1) in separated_pairs
        if gap == 1 and len(shared) >= 2 and not is_separated:
            runs[-1].append(record)
            continue
        intervening = lines[previous[0] + 1:record[0]]
        bridge = (
            gap in {2, 3}
            and gap <= max_line_gap
            and len(shared) >= 2
            and bool(intervening)
            and not is_separated
            and all(
                not any(_looks_numeric(text) or _is_period_token(text) for _, text in line)
                and (not line or max(box[2] for box, _ in line) < anchor_x[0] - tolerance)
                for line in intervening
            )
            and bool(previous[2]) and bool(record[2])
            and abs(max(item[0][2] for item in previous[2]) - max(item[0][2] for item in record[2])) <= tolerance * 4
        )
        if bridge:
            for offset, heading_line in enumerate(intervening, start=1):
                runs[-1].append((previous[0] + offset, {}, heading_line))
            runs[-1].append(record)
        else:
            runs.append([record])

    candidates = []
    dropped_blocks = 0
    for run_index, run in enumerate(runs):
        if len(run) < 3:
            dropped_blocks += 1
            continue
        # At least two numeric anchors must occur on most rows in this run.
        frequent = [anchor for anchor in range(len(anchor_x)) if sum(anchor in row[1] for row in run) >= max(3, (len(run) + 1) // 2)]
        if len(frequent) < 2:
            dropped_blocks += 1
            continue
        first_anchor = anchor_x[min(frequent)]
        cells = []
        all_boxes = []
        unresolved_rows = []
        for row_index, (_, assignments, label_words) in enumerate(run):
            if not assignments:
                if label_words:
                    heading_box = _union_boxes([item[0] for item in label_words])
                    heading_text = " ".join(item[1] for item in sorted(label_words, key=lambda item: item[0][0]))
                    cells.append(TableCell(row_index=row_index, column_index=0, text=heading_text, bbox=heading_box))
                    all_boxes.append(heading_box)
                continue
            usable = {anchor: values[0] for anchor, values in assignments.items() if anchor in frequent}
            if len(usable) < 2:
                continue
            label_items = [item for item in label_words if item[0][0] < first_anchor - tolerance]
            if label_items:
                label_box = _union_boxes([item[0] for item in label_items])
                label_text = " ".join(item[1] for item in sorted(label_items, key=lambda item: item[0][0]))
                cells.append(TableCell(row_index=row_index, column_index=0, text=label_text, bbox=label_box))
                all_boxes.append(label_box)
                previous_line_index = run[row_index][0] - 1
                if previous_line_index >= 0:
                    previous_line = lines[previous_line_index]
                    previous_text = " ".join(text for box, text in previous_line if box[0] < first_anchor - tolerance)
                    current_label_words = label_text.split()
                    if (
                        previous_line
                        and abs(
                            ((previous_line[0][0][1] + previous_line[0][0][3]) / 2)
                            - ((label_items[0][0][1] + label_items[0][0][3]) / 2)
                        ) <= median(heights) * 1.5
                        and len(current_label_words) <= 2
                        and len(previous_text.split()) >= 2
                    ):
                        unresolved_rows.append(row_index)
            for anchor in frequent:
                if anchor not in usable:
                    continue
                box, text = usable[anchor]
                column = anchor - min(frequent) + 1
                cells.append(TableCell(row_index=row_index, column_index=column, text=text, bbox=box))
                all_boxes.append(box)
        cells.sort(key=lambda cell: (cell.row_index, cell.column_index))
        if not cells:
            continue
        bbox = _union_boxes(all_boxes)
        if any(_boxes_overlap_ratio(bbox, candidate.bbox) > 0.5 for candidate in bordered if candidate.bbox):
            continue
        aligned_diagnostics = [
            _diagnostic(
                "aligned_numeric_structure",
                f"{len(run)} rows share {len(frequent)} recurring numeric right-edge anchors; confidence is uncalibrated.",
                DiagnosticSeverity.INFO,
            )
        ]
        if any(not row[1] for row in run):
            aligned_diagnostics.append(_diagnostic(
                "internal_subheading_retained",
                "A text-only subheading was retained inside the recurring value-column region.",
                DiagnosticSeverity.INFO,
            ))
        first_value_top = min(
            box[1]
            for values in run[0][1].values()
            for box, _ in values
        )
        header_distance_limit = max(48.0, median(heights) * 6.0)
        # The recurring columns of this candidate, in left-to-right order.
        ordered_anchors = sorted(frequent, key=lambda anchor: anchor_x[anchor])
        column_positions = [anchor_x[anchor] for anchor in ordered_anchors]
        header_tolerance = _header_match_tolerance(column_positions, median(heights))
        matching_headers = [
            (line_index, tokens) for line_index, tokens in period_header_lines
            if 0 <= first_value_top - max(box[3] for box, _ in tokens) <= header_distance_limit
        ]
        header_association_kind = "period_headers_associated_by_alignment"
        selected_header = None
        if len(matching_headers) == 1:
            selected_header = matching_headers[0]
        elif len(matching_headers) > 1:
            aligned_diagnostics.append(_diagnostic(
                "ambiguous_period_header",
                "Multiple nearby period headers align with numeric columns; no header was selected.",
            ))
        elif len(period_header_lines) == 1:
            # A page-wide header may govern multiple separated row blocks of a
            # single table. It is accepted only when every recurring numeric
            # column of this candidate receives an unambiguous period token.
            global_header = period_header_lines[0]
            mapping, ambiguous = _match_period_tokens(
                global_header[1], column_positions, header_tolerance
            )
            if not ambiguous and len(mapping) == len(column_positions):
                selected_header = global_header
                header_association_kind = "period_headers_associated_by_page_grid"
        if selected_header is not None:
            header_line_index, header_tokens = selected_header
            mapping, ambiguous = _match_period_tokens(
                header_tokens, column_positions, header_tolerance
            )
            dropped = len(header_tokens) - len(mapping)
            one_to_one_expected = len(header_tokens) == len(column_positions)
            offsets_inconsistent = _header_offsets_inconsistent(
                mapping, header_tokens, column_positions
            )
            if ambiguous or (one_to_one_expected and dropped > 0) or offsets_inconsistent:
                aligned_diagnostics.append(_diagnostic(
                    "period_header_conflict",
                    "Competing period tokens were found for the same numeric column, or the "
                    "tokens do not line up with the columns they would govern; no header was "
                    "selected and the period association remains unresolved.",
                ))
            elif len(mapping) >= 2:
                shifted = [cell.model_copy(update={"row_index": cell.row_index + 1}) for cell in cells]
                for position, token_index in mapping:
                    box, text = header_tokens[token_index]
                    # The header uses the same column numbering as the value
                    # cells, so a header always governs its own column.
                    anchor = ordered_anchors[position]
                    column = anchor - min(frequent) + 1
                    shifted.append(TableCell(row_index=0, column_index=column, text=text, bbox=box))
                    if header_association_kind != "period_headers_associated_by_page_grid":
                        all_boxes.append(box)
                cells = sorted(shifted, key=lambda cell: (cell.row_index, cell.column_index))
                aligned_diagnostics.append(_diagnostic(
                    header_association_kind,
                    f"A period header at text line {header_line_index} aligns with recurring numeric columns.",
                    DiagnosticSeverity.INFO,
                ))
                if header_association_kind == "period_headers_associated_by_page_grid":
                    aligned_diagnostics.append(_diagnostic(
                        "period_header_outside_candidate_region",
                        "The governing period header lies outside this candidate's rows. Its source "
                        "location is kept on the header cell, but it does not extend the candidate's "
                        "region over the other table's rows.",
                        DiagnosticSeverity.INFO,
                    ))
                if dropped > 0:
                    aligned_diagnostics.append(_diagnostic(
                        "period_header_incomplete",
                        "Some period tokens on the selected header line do not govern a column "
                        "of this candidate; they were not attached.",
                        DiagnosticSeverity.INFO,
                    ))
            else:
                aligned_diagnostics.append(_diagnostic(
                    "period_header_not_associated",
                    "A period header line was detected but does not unambiguously govern this "
                    "candidate's numeric columns; the period association remains unresolved.",
                ))
        elif period_header_lines:
            aligned_diagnostics.append(_diagnostic(
                "period_header_not_associated",
                "A period header line was detected on the page but was not associated with this "
                "candidate; the period association remains unresolved.",
            ))
        bbox = _union_boxes(all_boxes)
        for row_index in unresolved_rows:
            aligned_diagnostics.append(_diagnostic(
                "possible_wrapped_row_label",
                f"Row {row_index} has a short label near a preceding text baseline; row-label association remains unresolved.",
            ))
        if any(len(row[1]) < len(frequent) for row in run):
            aligned_diagnostics.append(_diagnostic(
                "incomplete_numeric_row",
                "Some aligned value columns are absent or non-numeric; missing cells were not fabricated.",
                DiagnosticSeverity.INFO,
            ))
        candidates.append(TableCandidate(
            table_id=f"p{page_number}-aligned-{run_index}",
            page_number=page_number,
            bbox=bbox,
            detection_method=TableDetectionMethod.WORD_GEOMETRY,
            confidence=None,
            diagnostics=aligned_diagnostics,
            cells=cells,
        ))
    page_diagnostics = []
    if dropped_blocks:
        page_diagnostics.append(_diagnostic(
            "aligned_numeric_block_dropped",
            f"{dropped_blocks} row block(s) did not carry enough recurring numeric columns to "
            "form a table and were left unresolved; they are not part of any candidate.",
        ))
    if not candidates:
        return [], [*page_diagnostics, _diagnostic(
            "aligned_numeric_candidate_abstained",
            "Aligned numeric anchors did not form a local repeated table region.",
            DiagnosticSeverity.INFO,
        )]
    return candidates, page_diagnostics


def _row_extent(boxes: list[BoundingBox]) -> tuple[float, float]:
    """Return the vertical extent (top, bottom) of a row's source boxes."""
    return min(box[1] for box in boxes), max(box[3] for box in boxes)


# A table boundary is recognised relative to the local row rhythm of the page.
# A page-wide average cannot decide this: it is inflated by the very gap it
# should detect when a page holds few rows, and it penalises a table whose own
# rows are more widely spaced than another table on the same page.
ROW_SEPARATION_FACTOR = 3.0
ROW_SEPARATION_MINIMUM_FACTOR = 1.5

# How far the offsets between a period header token and the column it labels
# may differ from each other, as a fraction of the column spacing. A centred
# header keeps a constant offset; a larger spread means the tokens cannot
# explain one column each.
HEADER_OFFSET_SPREAD_FACTOR = 0.6


def _row_pitches(row_extents: list[tuple[float, float]]) -> list[float]:
    """Distance between the tops of consecutive rows."""
    return [
        row_extents[index + 1][0] - row_extents[index][0]
        for index in range(len(row_extents) - 1)
    ]


def _separated_row_pairs(pitches: list[float], height: float) -> set[int]:
    """Indices of the row gaps that start a new block of rows.

    ``pitches[index]`` is the distance between the top of row ``index`` and the
    top of row ``index + 1``. A gap is treated as a table boundary when it is
    much larger than the smallest pitch that surrounds it: a table keeps a
    steady rhythm, so a jump of several times the neighbouring rhythm is a
    different structure rather than the next row of the same one.

    The comparison is with the neighbouring pitches rather than with a page
    average, so the decision is local. One loosely spaced table therefore does
    not change how a denser table on the same page is segmented, and a large
    gap is still detected when the page holds only a few rows.
    """
    separated: set[int] = set()
    count = len(pitches)
    for index, pitch in enumerate(pitches):
        if pitch <= 0:
            continue
        neighbours = [
            pitches[other]
            for other in (index - 1, index + 1)
            if 0 <= other < count and pitches[other] > 0
        ]
        # With no neighbouring pitch there is no local rhythm to compare
        # against; a generous multiple of the text height is used instead.
        reference = min(neighbours) if neighbours else 4.0 * height
        if reference <= 0:
            continue
        if (
            pitch > ROW_SEPARATION_FACTOR * reference
            and pitch > ROW_SEPARATION_MINIMUM_FACTOR * height
        ):
            separated.add(index)
    return separated


def _is_period_header_row(row: dict[int, tuple[str, BoundingBox]]) -> bool:
    """True when a row's value cells are reporting periods rather than values.

    Such a row labels the rows below it, so it must not be merged into the
    block above it.
    """
    value_cells = [(text, box) for column, (text, box) in row.items() if column > 0]
    if len(value_cells) < 2:
        return False
    if not all(_is_period_token(text) for text, _ in value_cells):
        return False
    label = row.get(0)
    return label is not None and not _looks_numeric(label[0])


def _align_period_header_row(cells: list[TableCell]) -> tuple[list[TableCell], Optional[str]]:
    """Attach a header row's period tokens to the columns they govern.

    A period header is often centred over its column while the values below
    it are right-aligned, so the header can fall either side of the boundary
    that separates the label column from the first value column. When the
    header row carries exactly as many period tokens as the run has value
    columns, the tokens are attached to those columns in reading order, which
    is the only reliable link between a centred header and its values.

    The original header text is preserved; only the column it is attached to
    can change, and the change is reported.
    """
    if not cells:
        return cells, None
    by_row: dict[int, list[TableCell]] = {}
    for cell in cells:
        by_row.setdefault(cell.row_index, []).append(cell)
    value_columns = sorted({
        cell.column_index
        for row_cells in by_row.values()
        for cell in row_cells
        if cell.column_index > 0 and not _is_period_token(cell.text)
    })
    if len(value_columns) < 2:
        return cells, None
    adjusted: list[TableCell] = []
    note = None
    for row_index, row_cells in by_row.items():
        header_cells = sorted(
            (cell for cell in row_cells if cell.column_index > 0 and _is_period_token(cell.text)),
            key=lambda cell: ((cell.bbox[0] + cell.bbox[2]) / 2) if cell.bbox else cell.column_index,
        )
        other_cells = [cell for cell in row_cells if cell not in header_cells]
        if len(header_cells) >= 2 and len(header_cells) == len(value_columns):
            for token_cell, target_column in zip(header_cells, value_columns):
                if token_cell.column_index != target_column:
                    note = (
                        f"Row {row_index} period header tokens were attached to the value columns "
                        "in reading order because a centred header does not share the value column's edge."
                    )
                adjusted.append(token_cell.model_copy(update={"column_index": target_column}))
        else:
            adjusted.extend(header_cells)
        adjusted.extend(other_cells)
    adjusted.sort(key=lambda cell: (cell.row_index, cell.column_index))
    return adjusted, note


def _record_boxes(record) -> list[BoundingBox]:
    """Collect every source box belonging to one row record."""
    _line_index, assignments, non_numeric = record
    boxes = [box for values in assignments.values() for box, _ in values]
    boxes.extend(box for box, _ in non_numeric)
    return boxes


def _header_match_tolerance(column_positions: list[float], height: float) -> float:
    """Tolerance for matching a header token to a numeric column.

    A period header is usually centred over its column while the values below
    it are right-aligned, so the header's centre can sit half a column width
    away from the value column's right edge. Comparing a header centre with a
    value right edge therefore needs a tolerance that scales with the column
    spacing rather than with the text height alone.
    """
    tolerance = max(12.0, height * 2.5)
    if len(column_positions) >= 2:
        spacing = median([
            column_positions[index + 1] - column_positions[index]
            for index in range(len(column_positions) - 1)
        ])
        tolerance = max(tolerance, 0.6 * spacing)
    return tolerance


def _match_period_tokens(
    tokens: list[tuple[BoundingBox, str]],
    column_positions: list[float],
    tolerance: float,
) -> tuple[list[tuple[int, int]], bool]:
    """Assign period header tokens to numeric columns in reading order.

    The assignment is order preserving: a header token may only govern a
    column at or after the column governed by the previous token. This keeps
    a header with the column it labels even when the header is centred over
    the column and the values below it are right-aligned, where a nearest-edge
    comparison would pick the wrong neighbour or tie.

    Returns the matched ``(column_index, token_index)`` pairs and a flag that
    is true when more than one optimal assignment exists.
    """
    if not tokens or not column_positions:
        return [], False
    centres = [(box[0] + box[2]) / 2 for box, _ in tokens]
    token_count = len(tokens)
    column_count = len(column_positions)

    def _prefer(first, second):
        if first[0] != second[0]:
            return first if first[0] > second[0] else second
        if abs(first[1] - second[1]) > 1e-9:
            return first if first[1] < second[1] else second
        return (first[0], first[1], first[2] + second[2])

    @lru_cache(maxsize=None)
    def solve(token_index: int, first_column: int):
        """Best (count, cost, ways) for the remaining tokens and columns."""
        if token_index == token_count or first_column >= column_count:
            return (0, 0.0, 1)
        best = solve(token_index + 1, first_column)
        for column_index in range(first_column, column_count):
            distance = abs(centres[token_index] - column_positions[column_index])
            if distance > tolerance:
                continue
            count, cost, ways = solve(token_index + 1, column_index + 1)
            best = _prefer(best, (count + 1, cost + distance, ways))
        return best

    count, _cost, ways = solve(0, 0)
    mapping: list[tuple[int, int]] = []
    token_index = 0
    first_column = 0
    while token_index < token_count and first_column < column_count:
        options = []
        skipped = solve(token_index + 1, first_column)
        options.append((skipped, None))
        for column_index in range(first_column, column_count):
            distance = abs(centres[token_index] - column_positions[column_index])
            if distance > tolerance:
                continue
            sub_count, sub_cost, sub_ways = solve(token_index + 1, column_index + 1)
            options.append(((sub_count + 1, sub_cost + distance, sub_ways), column_index))
        target_count, target_cost, _ = solve(token_index, first_column)
        chosen = None
        for value, column_index in options:
            if value[0] == target_count and abs(value[1] - target_cost) <= 1e-9:
                if chosen is None or (column_index is not None and chosen[1] is None):
                    chosen = (value, column_index)
        if chosen is None:
            break
        if chosen[1] is None:
            token_index += 1
            continue
        mapping.append((chosen[1], token_index))
        token_index += 1
        first_column = chosen[1] + 1
    return mapping, ways > 1


def _column_spacing(column_positions: list[float]) -> float:
    """The typical distance between neighbouring numeric columns."""
    if len(column_positions) < 2:
        return 0.0
    return median([
        column_positions[index + 1] - column_positions[index]
        for index in range(len(column_positions) - 1)
    ])


def _header_offsets_inconsistent(
    mapping: list[tuple[int, int]],
    tokens: list[tuple[BoundingBox, str]],
    column_positions: list[float],
) -> bool:
    """True when a header assignment is not justified by the token geometry.

    A period header is usually centred over its column while the values below
    it are right-aligned, so every header token sits a similar distance to the
    left of the column it labels. If the assigned offsets differ from each
    other by more than a fraction of the column spacing, the tokens are
    crowded together or spread apart in a way that one-token-per-column cannot
    explain, and the assignment is not justified by the layout.
    """
    if len(mapping) < 2:
        return False
    offsets = []
    for column_index, token_index in mapping:
        box = tokens[token_index][0]
        centre = (box[0] + box[2]) / 2
        offsets.append(column_positions[column_index] - centre)
    spread = max(offsets) - min(offsets)
    spacing = _column_spacing(column_positions)
    if spacing <= 0:
        return False
    return spread > HEADER_OFFSET_SPREAD_FACTOR * spacing


def _group_words_into_lines(words: list[tuple[BoundingBox, str]], tolerance: float) -> list[list[tuple[BoundingBox, str]]]:
    ordered = sorted(words, key=lambda item: ((item[0][1] + item[0][3]) / 2, item[0][0]))
    groups: list[list[tuple[BoundingBox, str]]] = []
    centers: list[float] = []
    for word in ordered:
        center = (word[0][1] + word[0][3]) / 2
        match = next((i for i, current in enumerate(centers) if abs(center - current) <= tolerance), None)
        if match is None:
            groups.append([word]); centers.append(center)
        else:
            groups[match].append(word)
            centers[match] = sum((item[0][1] + item[0][3]) / 2 for item in groups[match]) / len(groups[match])
    return [sorted(group, key=lambda item: item[0][0]) for _, group in sorted(zip(centers, groups), key=lambda pair: pair[0])]


def _split_line_cells(line: list[tuple[BoundingBox, str]], gap_threshold: float) -> list[tuple[BoundingBox, str]]:
    if not line:
        return []
    segments = []
    current = [line[0]]
    for word in line[1:]:
        if word[0][0] - current[-1][0][2] >= gap_threshold:
            segments.append(_join_segment(current))
            current = [word]
        else:
            current.append(word)
    segments.append(_join_segment(current))
    return segments


def _join_segment(words: list[tuple[BoundingBox, str]]) -> tuple[BoundingBox, str]:
    return _union_boxes([item[0] for item in words]), " ".join(item[1] for item in words)


_FOOTNOTE_MARKERS = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰*†‡]+$")


def _strip_footnote_markers(text: str) -> str:
    """Remove a trailing footnote marker from a source token.

    A marker is metadata attached to a cell, not part of the reported period
    or value. It must not change how the token is classified, so it is
    removed before any period or numeric decision is taken. The caller's
    original text is never modified; only this normalized copy is used.
    """
    return _FOOTNOTE_MARKERS.sub("", text.strip()).strip()


def _looks_numeric(text: str) -> bool:
    """Recognize value-like numeric tokens while excluding periods and prose.

    Footnote markers are stripped first so that a marked period such as
    ``2024*`` is still recognised as a period instead of falling through to
    the numeric pattern as the bare number 2024.
    """
    value = _strip_footnote_markers(text)
    if not value or _is_placeholder(value) or _is_period_token(value):
        return False
    value = value.replace("−", "-").replace("–", "-")
    return bool(re.fullmatch(
        r"\(?\s*[+-]?\s*(?:(?:GBP|USD|EUR|INR|JPY|CNY)\s*)?"
        r"[£$€₹¥]?\s*\d[\d,]*(?:\.\d+)?\s*"
        r"(?:%|percent|per\s+cent|pp|p\.p\.?|pence|p|thousand|million|billion|mn|bn|[kmb])?\s*\)?",
        value,
        re.IGNORECASE,
    ))


def _is_period_token(text: str) -> bool:
    """Recognize a reporting-period token, tolerating a footnote marker."""
    value = text.strip().strip("()[]{}.,;:")
    value = _strip_footnote_markers(value)
    return bool(
        re.fullmatch(r"(?i)(?:FY\s*)?(?:19|20)\d{2}(?:\s*[-/–—]\s*\d{2,4})?", value)
        or re.fullmatch(r"(?i)FY\s*\d{2,4}", value)
        or re.fullmatch(r"(?i)Q[1-4](?:\s*(?:FY)?\s*\d{2,4})?", value)
        or re.fullmatch(r"\d{1,2}/\d{1,2}/(?:19|20)\d{2}", value)
    )


def _is_placeholder(text: str) -> bool:
    return text.strip().casefold() in {
        "", "-", "–", "—", "−", "·", "...", "…", "n/a", "na", "n.a.", "not available", "nil"
    }


def _label_text(lines: list[tuple[BoundingBox, str]]) -> str:
    return " ".join(text for _, text in sorted(lines, key=lambda item: item[0][0])).strip()


def _is_group_label(text: str) -> bool:
    words = text.split()
    letters = [char for char in text if char.isalpha()]
    if not letters or not 2 <= len(words) <= 5 or len(text) > 64:
        return False
    if _looks_numeric(text) or _is_period_token(text) or any(char in text for char in ".,;:!?"):
        return False
    return sum(char.isupper() for char in letters) / len(letters) >= 0.68


def _is_group_heading(text: str) -> bool:
    words = text.split()
    letters = [char for char in text if char.isalpha()]
    return bool(
        letters and 1 <= len(words) <= 4 and len(text) <= 48
        and not _looks_numeric(text)
        and sum(char.isupper() for char in letters) / len(letters) >= 0.68
    )


def _label_value_group_candidates(
    page: Any,
    page_number: int,
    words: list,
    bordered: list[TableCandidate],
) -> tuple[list[TableCandidate], list[TableDiagnostic]]:
    """Find repeated, local label/value cards without interpreting their meaning."""
    parsed = []
    heights = []
    for word in words:
        if len(word) < 5:
            continue
        try:
            box = _validate_bbox(tuple(float(value) for value in word[:4]))
        except (TypeError, ValueError):
            continue
        text = str(word[4]).strip()
        if box is None or not text or box[3] <= box[1]:
            continue
        parsed.append((box, text))
        heights.append(box[3] - box[1])
    if len(parsed) < 10:
        return [], []
    lines = _group_words_into_lines(parsed, max(1.0, median(heights) * 0.45))
    line_info = []
    gap_threshold = max(18.0, median(heights) * 2.2)
    for index, line in enumerate(lines):
        for segment_box, segment_text in _split_line_cells(line, gap_threshold):
            segment = [(box, text) for box, text in line if box[0] >= segment_box[0] - 0.5 and box[2] <= segment_box[2] + 0.5]
            values = [(cell_box, value) for cell_box, value in segment if _looks_numeric(value)]
            line_info.append((index, segment, segment_text, segment_box, values))

    pairs = []
    max_vertical = max(18.0, median(heights) * 3.5)
    for label_index, label_line, label, label_box, _ in line_info:
        if not _is_group_label(label):
            continue
        choices = []
        for value_index, value_line, _, value_line_box, values in line_info:
            delta = value_line_box[1] - label_box[3]
            if value_index <= label_index or delta < -1.0 or delta > max_vertical or len(values) != 1:
                continue
            value_box, value_text = values[0]
            if abs(value_box[0] - label_box[0]) > max(24.0, median(heights) * 3.0):
                continue
            choices.append((delta, value_index, value_line, value_box, value_text))
        if not choices:
            continue
        delta, value_index, value_line, value_box, value_text = min(choices, key=lambda item: (item[0], item[1]))
        # A nearby left-hand short heading can be retained as group context,
        # but only its source text is recorded; it is not called an entity.
        nearby = [
            (heading_box, heading_text)
            for _, heading_line, heading_text, heading_box, _ in line_info
            if abs(((heading_box[1] + heading_box[3]) / 2) - ((label_box[1] + label_box[3]) / 2)) <= max(8.0, median(heights))
            and heading_box[2] < label_box[0] - max(30.0, median(heights) * 3.0)
            and _is_group_heading(heading_text)
        ]
        context = max(nearby, key=lambda item: (item[0][2], -item[0][0])) if nearby else None
        pairs.append({
            "label": label,
            "label_box": label_box,
            "label_index": label_index,
            "value": value_text,
            "value_box": value_box,
            "value_index": value_index,
            "context": context,
        })

    # A one-off label/value pair is indistinguishable from a definition list.
    # Require repeated exact metric labels and repeated group headings, each
    # backed by a spatially adjacent value line.
    by_label: dict[str, list[dict]] = {}
    for pair in pairs:
        by_label.setdefault(re.sub(r"\s+", " ", pair["label"]).casefold(), []).append(pair)

    candidates = []
    diagnostics = []
    for label_key, group in sorted(by_label.items()):
        with_context = [pair for pair in group if pair["context"] is not None]
        if len(group) < 2 or len(with_context) < 2:
            continue
        group.sort(key=lambda pair: (pair["label_box"][1], pair["label_box"][0], pair["value_box"][1]))
        cells = []
        all_boxes = []
        for row_index, pair in enumerate(group):
            if pair["context"]:
                context_box, context_text = pair["context"]
                cells.append(TableCell(row_index=row_index, column_index=0, text=context_text, bbox=context_box))
                all_boxes.append(context_box)
            cells.append(TableCell(row_index=row_index, column_index=1, text=pair["label"], bbox=pair["label_box"]))
            cells.append(TableCell(row_index=row_index, column_index=2, text=pair["value"], bbox=pair["value_box"]))
            all_boxes.extend((pair["label_box"], pair["value_box"]))
        bbox = _union_boxes(all_boxes)
        if any(_boxes_overlap_ratio(bbox, table.bbox) > 0.75 for table in bordered if table.bbox):
            continue
        candidates.append(TableCandidate(
            table_id=f"p{page_number}-groups-{len(candidates):02d}",
            page_number=page_number,
            bbox=bbox,
            detection_method=TableDetectionMethod.LABEL_VALUE_GROUPS,
            confidence=None,
            diagnostics=[
                _diagnostic(
                    "repeated_label_value_groups",
                    f"Repeated source label {group[0]['label']!r} is paired with {len(group)} nearby displayed values and adjacent group headings.",
                    DiagnosticSeverity.INFO,
                ),
                _diagnostic(
                    "group_semantics_unresolved",
                    "Group headings, entity, period, unit, and factual meaning remain source text or unresolved context.",
                    DiagnosticSeverity.INFO,
                ),
            ],
            cells=cells,
        ))
    if pairs and not candidates:
        diagnostics.append(_diagnostic(
            "label_value_groups_insufficient_repetition",
            "Nearby label/value pairs lacked repeated labels and adjacent group headings; no group candidate was emitted.",
            DiagnosticSeverity.INFO,
        ))
    return candidates, diagnostics


def _cluster_positions(values: list[float], tolerance: float) -> list[tuple[float, int]]:
    clusters: list[list[float]] = []
    for value in sorted(values):
        if not clusters or value - median(clusters[-1]) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return [(median(cluster), len(cluster)) for cluster in clusters]


def _unique_edges(values: list[float]) -> list[float]:
    values = sorted(values)
    edges = []
    for value in values:
        if not edges or value - edges[-1] > 1.0:
            edges.append(value)
        else:
            edges[-1] = (edges[-1] + value) / 2
    return edges


def _interval_index(value: float, edges: list[float]) -> int:
    return max(0, sum(edge <= value for edge in edges) - 1)


def _covered_intervals(start: float, end: float, edges: list[float]) -> int:
    return max(1, sum(start < right and end > left for left, right in zip(edges, edges[1:])))


def _transform_bbox(raw_box: Any, matrix: Any = None) -> Optional[BoundingBox]:
    if raw_box is None:
        return None
    try:
        rect = pymupdf.Rect(raw_box)
        if matrix is not None:
            rect = rect * matrix
        box = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        return _validate_bbox(box)
    except Exception:
        return None


def _unrotated_page_bbox(page: Any) -> Optional[BoundingBox]:
    rect = getattr(page, "rect", None)
    if rect is None:
        return None
    return _transform_bbox(rect, getattr(page, "derotation_matrix", None) if getattr(page, "rotation", 0) else None)


def _clip_or_reject_bbox(box: Optional[BoundingBox], page_box: Optional[BoundingBox]) -> Optional[BoundingBox]:
    if box is None or page_box is None:
        return box
    # Tiny antialiasing/float excursions are clipped; materially out-of-page
    # values are rejected rather than silently rewritten.
    x0, y0, x1, y1 = box
    px0, py0, px1, py1 = page_box
    epsilon = 1.5
    if x0 < px0 - epsilon or y0 < py0 - epsilon or x1 > px1 + epsilon or y1 > py1 + epsilon:
        return None
    return (max(px0, x0), max(py0, y0), min(px1, x1), min(py1, y1))


def _union_boxes(boxes: list[BoundingBox]) -> BoundingBox:
    return (min(box[0] for box in boxes), min(box[1] for box in boxes), max(box[2] for box in boxes), max(box[3] for box in boxes))


def _boxes_overlap_ratio(first: BoundingBox, second: BoundingBox) -> float:
    x0, y0 = max(first[0], second[0]), max(first[1], second[1])
    x1, y1 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    return intersection / area if area else 0.0


def _diagnostic(code: str, message: str, severity: DiagnosticSeverity = DiagnosticSeverity.WARNING) -> TableDiagnostic:
    return TableDiagnostic(code=code, message=message, severity=severity)
