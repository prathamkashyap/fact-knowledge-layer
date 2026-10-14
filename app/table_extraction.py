"""Standalone, serializable contract for PDF table-layout hypotheses.

This module deliberately does not parse PDFs or create :class:`Fact` objects.
A ``TableCandidate`` describes structure proposed by a layout detector; the
separate row/value context records what is and is not supported by that
structure. Later extraction code must resolve the required dimensions before
it can map a context into a production fact.
"""

from decimal import Decimal
from enum import Enum
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
    try:
        measurements.drawing_count = len(page.get_drawings())
    except Exception as exc:
        measurements.measurement_errors.append(f"drawings:{type(exc).__name__}")
    return measurements


def table_detector_workload_allowed(complexity: PageComplexity) -> tuple[bool, str]:
    """Apply fixed workload bounds; missing measurements abstain from the costly detector."""
    if complexity.measurement_errors:
        return False, "measurement_failed"
    if complexity.drawing_count is None or complexity.word_count is None or complexity.text_char_count is None:
        return False, "measurement_unavailable"
    if complexity.drawing_count == 0:
        return False, "no_vector_drawings"
    if complexity.drawing_count > MAX_FIND_TABLES_DRAWINGS:
        return False, "drawing_limit"
    if complexity.word_count > MAX_FIND_TABLES_WORDS:
        return False, "word_limit"
    if complexity.text_char_count > MAX_FIND_TABLES_TEXT_CHARS:
        return False, "text_limit"
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
                row_cells = {}
                break
            row_cells[column] = (text, box)
        if len(row_cells) >= 2:
            has_numeric_value = any(_looks_numeric(text) for col, (text, _) in row_cells.items() if col > 0)
            numeric_rows += int(has_numeric_value)
            row_records.append((line_index, row_cells))

    if len(row_records) < 3 or numeric_rows < 2:
        aligned, aligned_diagnostics = _aligned_numeric_candidates(page_number, parsed, heights, bordered)
        if aligned:
            return aligned, [_diagnostic(
                "gap_grid_abstained",
                "Gap-based column segmentation was insufficient; repeated numeric alignment supplied the candidate grid.",
                DiagnosticSeverity.INFO,
            )]
        return [], [_diagnostic(
            "borderless_structure_insufficient",
            "Repeated gaps were found, but at least three aligned rows and numeric values in two rows are required.",
            DiagnosticSeverity.INFO,
        ), *aligned_diagnostics]

    # A broad page-wide cluster of gaps can combine unrelated prose. Retain
    # only the longest local run of aligned rows, allowing one intervening
    # heading/baseline but not stitching distant page regions together.
    runs: list[list[tuple[int, dict[int, tuple[str, BoundingBox]]]]] = []
    for record in row_records:
        if not runs:
            runs.append([record])
            continue
        previous = runs[-1][-1]
        gap = record[0] - previous[0]
        shared = set(record[1]) & set(previous[1])
        if gap == 1 and len(shared) >= 2:
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
        diagnostics = [
            _diagnostic(
                "borderless_repeated_structure",
                f"Word geometry formed {len(run)} adjacent rows across {len(column_indices)} recurring columns; this is a structural heuristic, not calibrated confidence.",
                DiagnosticSeverity.INFO,
            )
        ]
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
            return aligned, [_diagnostic(
                "gap_grid_abstained",
                "Gap-based segmentation did not form a local grid; repeated numeric alignment supplied the candidate grid.",
                DiagnosticSeverity.INFO,
            )]
        return [], [_diagnostic(
            "borderless_candidate_abstained",
            "Page geometry did not yield a locally repeated grid with sufficient numeric structure.",
            DiagnosticSeverity.INFO,
        ), *aligned_diagnostics]
    return candidates, []


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

    period_headers = []
    for line_index, line in enumerate(lines):
        periods = [(box, text) for box, text in line if _is_period_token(text)]
        assignments = {}
        for box, text in periods:
            center = (box[0] + box[2]) / 2
            nearest = min(range(len(anchor_x)), key=lambda i: abs(anchor_x[i] - center))
            if abs(anchor_x[nearest] - center) <= max(12.0, median(heights) * 2.5):
                assignments[nearest] = (box, text)
        if len(assignments) >= 2:
            period_headers.append((line_index, assignments))

    # Keep local vertical runs. A single internal text-only subheading may be
    # retained only when two or more right-edge anchors and the left label
    # region continue on both sides; vertical proximity alone is insufficient.
    max_line_gap = max(3, round(28.0 / max(median(heights), 1.0)))
    runs: list[list[tuple[int, dict[int, list[tuple[BoundingBox, str]]], list[tuple[BoundingBox, str]]]]] = []
    for record in row_records:
        if not runs:
            runs.append([record])
            continue
        previous = runs[-1][-1]
        gap = record[0] - previous[0]
        shared = set(record[1]) & set(previous[1])
        if gap == 1 and len(shared) >= 2:
            runs[-1].append(record)
            continue
        intervening = lines[previous[0] + 1:record[0]]
        bridge = (
            gap in {2, 3}
            and gap <= max_line_gap
            and len(shared) >= 2
            and bool(intervening)
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
    for run_index, run in enumerate(runs):
        if len(run) < 3:
            continue
        # At least two numeric anchors must occur on most rows in this run.
        frequent = [anchor for anchor in range(len(anchor_x)) if sum(anchor in row[1] for row in run) >= max(3, (len(run) + 1) // 2)]
        if len(frequent) < 2:
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
        matching_headers = [
            header for header in period_headers
            if 0 <= first_value_top - max(box[3] for box, _ in header[1].values()) <= header_distance_limit
        ]
        header_association_kind = "period_headers_associated_by_alignment"
        if not matching_headers and len(period_headers) == 1:
            # A page-wide header may govern multiple separated row blocks of a
            # single table. Require every recurring numeric column to have an
            # unambiguous period token at its own aligned x position.
            global_header = period_headers[0]
            if set(global_header[1]) == set(frequent):
                matching_headers = [global_header]
                header_association_kind = "period_headers_associated_by_page_grid"
        if len(matching_headers) == 1:
            header_line_index, header_values = matching_headers[0]
            shifted = [cell.model_copy(update={"row_index": cell.row_index + 1}) for cell in cells]
            for anchor, (box, text) in header_values.items():
                if anchor in frequent:
                    column = anchor - min(frequent) + 1
                    shifted.append(TableCell(row_index=0, column_index=column, text=text, bbox=box))
                    all_boxes.append(box)
            cells = sorted(shifted, key=lambda cell: (cell.row_index, cell.column_index))
            aligned_diagnostics.append(_diagnostic(
                header_association_kind,
                f"A period header at text line {header_line_index} aligns with recurring numeric columns.",
                DiagnosticSeverity.INFO,
            ))
        elif len(matching_headers) > 1:
            aligned_diagnostics.append(_diagnostic(
                "ambiguous_period_header",
                "Multiple nearby period headers align with numeric columns; no header was selected.",
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
    if not candidates:
        return [], [_diagnostic(
            "aligned_numeric_candidate_abstained",
            "Aligned numeric anchors did not form a local repeated table region.",
            DiagnosticSeverity.INFO,
        )]
    return candidates, []


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


def _looks_numeric(text: str) -> bool:
    """Recognize value-like numeric tokens while excluding periods and prose."""
    value = text.strip()
    if not value or _is_placeholder(value) or _is_period_token(value):
        return False
    value = value.replace("−", "-").replace("–", "-")
    value = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰*†‡]+$", "", value).strip()
    return bool(re.fullmatch(
        r"\(?\s*[+-]?\s*(?:(?:GBP|USD|EUR|INR|JPY|CNY)\s*)?"
        r"[£$€₹¥]?\s*\d[\d,]*(?:\.\d+)?\s*"
        r"(?:%|percent|per\s+cent|pp|p\.p\.?|pence|p|thousand|million|billion|mn|bn|[kmb])?\s*\)?",
        value,
        re.IGNORECASE,
    ))


def _is_period_token(text: str) -> bool:
    value = text.strip().strip("()[]{}.,;:")
    return bool(
        re.fullmatch(r"(?i)(?:FY\s*)?(?:19|20)\d{2}(?:\s*[-/–—]\s*\d{2,4})?", value)
        or re.fullmatch(r"(?i)FY\s*\d{2,4}", value)
        or re.fullmatch(r"(?i)Q[1-4](?:\s*FY?\s*\d{2,4})?", value)
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
