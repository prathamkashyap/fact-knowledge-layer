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


class Dimensions(BaseModel):
    """Deterministic dimension comparison between two facts."""
    subject: str      # "same" / "different" / "unknown"
    predicate: str
    value: str
    unit: str
    period: str
    scope: str
    qualifiers: str   # "same" / "different" / "unknown"


class FactComparison(BaseModel):
    """Structured relationship between two facts with reasoning."""
    fact_a_id: str
    fact_b_id: str
    relationship: str    # CORROBORATES | CONTRADICTS | RECONCILABLE | UNRELATED | UNCERTAIN
    confidence: float
    reason: str
    dimensions: Dimensions
