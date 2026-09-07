"""
Production fact schema for the Superjoin Fact Knowledge Layer.
Based on the validated Phase 0 spike, extended with evidence grounding
and as_of/effective-date support.
"""

import uuid
from typing import Union, List, Optional
from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """Source evidence linking a fact to its origin in a specific document page."""
    document_id: str
    page_number: int
    text: str                    # exact source sentence(s) the fact came from
    document_name: Optional[str] = None  # human-readable filename


class Fact(BaseModel):
    """
    A single grounded, decision-relevant factual assertion extracted from a document.

    value supports both numeric (float) and categorical/string values.
    This is intentional: the system is a semantic fact layer, not merely a
    numeric extraction tool.
    """
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    subject: str                                 # entity/topic the fact is about
    predicate: str                               # what is being asserted
    value: Union[float, str]                     # numeric OR categorical/string
    unit: Optional[str] = None                   # %, INR million, people, etc.
    period: Optional[str] = None                 # FY2024, 2024-25, Q4 FY24, etc.
    as_of: Optional[str] = None                  # effective date / reporting date
                                                 # when it differs from period
    scope: Optional[str] = None                  # standalone, consolidated, segment
    qualifiers: List[str] = Field(default_factory=list)  # First Advance Estimate, etc.
    evidence: Evidence
    confidence: float = 1.0                      # extraction confidence

    # Canonicalized forms (populated during normalization, not extraction)
    canonical_subject: Optional[str] = None
    canonical_predicate: Optional[str] = None
    normalized_value: Optional[Union[float, str]] = None
    normalized_unit: Optional[str] = None
    normalized_period: Optional[str] = None
    period_type: Optional[str] = None            # "fiscal_year" | "quarter" | "date" | "as_of" | "unknown"


class Dimensions(BaseModel):
    """Deterministic dimension comparison between two facts."""
    subject: str      # "same" / "different" / "unknown"
    predicate: str
    value: str
    unit: str
    period: str
    scope: str
    qualifiers: str   # "same" / "different" / "unknown"


class NumericalComparison(BaseModel):
    """Metadata describing numerical comparability between two facts."""
    is_numeric: bool
    status: str            # "exact" | "close_rounding" | "different" | "incompatible_units" | "categorical"
    is_exact: bool = False
    is_close_rounding: bool = False
    value_a_norm: Optional[float] = None
    value_b_norm: Optional[float] = None
    unit_norm: Optional[str] = None
    absolute_diff: Optional[float] = None
    relative_diff: Optional[float] = None
    rounding_note: Optional[str] = None


class PeriodComparison(BaseModel):
    """Metadata describing period compatibility between two facts."""
    period_a_norm: Optional[str] = None
    period_b_norm: Optional[str] = None
    type_a: Optional[str] = None
    type_b: Optional[str] = None
    is_same_period: bool = False
    is_same_type: bool = False
    compatibility_note: Optional[str] = None


class PreparedComparison(BaseModel):
    """
    Prepared candidate pair ready for downstream relationship reasoning.
    Retains original facts, normalized facts, dimension diff, and comparison metadata.
    NOTE: Does NOT assign the final relationship (that is the reasoner's responsibility).
    """
    pair_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    fact_a: Fact
    fact_b: Fact
    dimensions: Dimensions
    numerical_comparison: Optional[NumericalComparison] = None
    period_comparison: Optional[PeriodComparison] = None


class CandidateStatistics(BaseModel):
    """Inspectable candidate generation metrics."""
    total_facts: int
    total_naive_pairs: int
    candidate_groups_count: int
    candidate_pairs_count: int
    reduction_ratio: float
    processing_time_ms: float = 0.0


class FactComparison(BaseModel):
    """Structured relationship between two facts with reasoning."""
    fact_a_id: str
    fact_b_id: str
    relationship: str    # CORROBORATES | CONTRADICTS | RECONCILABLE | UNRELATED | UNCERTAIN
    confidence: float
    reason: str
    dimensions: Dimensions
