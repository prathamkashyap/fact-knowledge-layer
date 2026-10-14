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
from typing import Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator


BoundingBox = Tuple[float, float, float, float]


class TableDetectionMethod(str, Enum):
    """How a table-layout hypothesis was produced."""

    PYMUPDF_FIND_TABLES = "pymupdf_find_tables"
    WORD_GEOMETRY = "word_geometry"
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
