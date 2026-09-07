"""
Page-aware PDF parser and candidate page prioritizer.
Extracts structured page objects from PDFs using PyMuPDF (fitz)
and computes generic, inspectable deterministic scores for extraction candidate selection.
"""

import os
import re
import hashlib
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
import pymupdf as fitz


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class BlockMetadata(BaseModel):
    bbox: List[float]  # [x0, y0, x1, y1]
    text: str
    block_type: int    # 0 = text, 1 = image


class ScoreBreakdown(BaseModel):
    numeric_density: float = 0.0          # Digit tokens / total tokens
    percentage_count: int = 0             # %, percent, per cent
    currency_count: int = 0               # ₹, INR, Rs, $, USD, crore, million, billion
    date_fiscal_count: int = 0            # FY24, 2024-25, Q4, March 31, etc.
    table_score: float = 0.0              # Detected PyMuPDF tables or tabular lines
    heading_score: float = 0.0            # Font size or capitalised heading lines
    keyword_matches: List[str] = Field(default_factory=list) # Found semantic keywords
    keyword_score: float = 0.0            # Weighted keyword score
    total_score: float = 0.0              # Final composite score
    is_candidate: bool = False            # True if total_score >= candidate threshold


class PageObject(BaseModel):
    document_id: str
    filename: str
    page_number: int                      # 1-indexed for human readability
    raw_text: str
    text_length: int
    blocks_count: int
    has_tables: bool
    blocks: List[BlockMetadata] = Field(default_factory=list)
    score_breakdown: ScoreBreakdown


class DocumentIngestionResult(BaseModel):
    document_id: str
    filename: str
    filepath: str
    total_pages: int
    candidate_pages_count: int
    candidate_rate: float
    processing_time_ms: float
    pages: List[PageObject]


# ---------------------------------------------------------------------------
# Prioritization Keyword Specification
# Generic semantic keywords covering both numeric & non-numeric concepts
# ---------------------------------------------------------------------------

DEFAULT_KEYWORDS = [
    "revenue", "growth", "profit", "loss", "estimate",
    "appointed", "resigned", "ceased", "director", "board",
    "address", "acquired", "launched", "guidance", "consolidated",
    "standalone", "gdp", "inflation", "cpi", "fiscal", "deficit",
    "ebitda", "expenditure", "pat", "margin"
]

CANDIDATE_SCORE_THRESHOLD = 8.0


# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------

def compute_page_score(
    text: str,
    has_tables: bool,
    blocks: List[BlockMetadata],
    keywords: Optional[List[str]] = None,
    threshold: float = CANDIDATE_SCORE_THRESHOLD,
) -> ScoreBreakdown:
    if keywords is None:
        keywords = DEFAULT_KEYWORDS

    if not text.strip():
        return ScoreBreakdown(total_score=0.0, is_candidate=False)

    tokens = re.findall(r'\b\w+\b', text)
    total_tokens = len(tokens) if tokens else 1

    # 1. Numeric density
    digit_tokens = len(re.findall(r'\b\d+(?:\.\d+)?\b', text))
    numeric_density = round(digit_tokens / total_tokens, 4)
    # Score component: 0 to 3 points based on density
    numeric_score = min(numeric_density * 10.0, 3.0)

    # 2. Percentage occurrences
    percentage_matches = len(re.findall(r'%\b|\bpercent\b|\bper cent\b', text, re.IGNORECASE))
    percentage_score = min(percentage_matches * 0.5, 2.0)

    # 3. Currency symbols and monetary magnitude words
    currency_matches = len(re.findall(
        r'₹|\$|INR\b|USD\b|Rs\.?|crore\b|million\b|billion\b|lakh\b', text, re.IGNORECASE
    ))
    currency_score = min(currency_matches * 0.4, 2.0)

    # 4. Dates, quarters, fiscal year patterns
    date_fiscal_matches = len(re.findall(
        r'\bFY\d{2,4}\b|\b20\d{2}-\d{2,4}\b|\bQ[1-4]\b|\bMarch \d{1,2}\b|\bApril\b|\bDecember\b',
        text, re.IGNORECASE
    ))
    date_fiscal_score = min(date_fiscal_matches * 0.5, 2.0)

    # 5. Table structure score
    table_score = 2.0 if has_tables else 0.0
    # Additional table heuristic: lines with 3+ numbers
    lines = text.splitlines()
    tabular_lines = sum(1 for line in lines if len(re.findall(r'\b\d+(?:\.\d+)?\b', line)) >= 3)
    table_score += min(tabular_lines * 0.2, 1.5)

    # 6. Heading score (short lines in title/upper case)
    heading_count = sum(
        1 for line in lines
        if 2 <= len(line.strip().split()) <= 8 and (line.isupper() or line.istitle())
    )
    heading_score = min(heading_count * 0.3, 1.5)

    # 7. Semantic keyword matching (covers non-numeric + numeric concepts)
    matched_keywords = []
    text_lower = text.lower()
    for kw in keywords:
        if re.search(r'\b' + re.escape(kw) + r'\b', text_lower):
            matched_keywords.append(kw)

    keyword_score = min(len(matched_keywords) * 0.8, 4.0)

    # Total score calculation
    total_score = round(
        numeric_score + percentage_score + currency_score +
        date_fiscal_score + table_score + heading_score + keyword_score,
        2
    )

    is_candidate = total_score >= threshold

    return ScoreBreakdown(
        numeric_density=numeric_density,
        percentage_count=percentage_matches,
        currency_count=currency_matches,
        date_fiscal_count=date_fiscal_matches,
        table_score=round(table_score, 2),
        heading_score=round(heading_score, 2),
        keyword_matches=matched_keywords,
        keyword_score=round(keyword_score, 2),
        total_score=total_score,
        is_candidate=is_candidate,
    )


# ---------------------------------------------------------------------------
# PDF Ingestion Functions
# ---------------------------------------------------------------------------

def parse_pdf_page(
    doc: fitz.Document,
    page_idx: int,
    doc_id: str,
    filename: str,
    keywords: Optional[List[str]] = None,
    threshold: float = CANDIDATE_SCORE_THRESHOLD,
) -> PageObject:
    page = doc.load_page(page_idx)
    page_number = page_idx + 1  # 1-indexed

    # Extract text and blocks
    raw_text = page.get_text("text") or ""
    text_length = len(raw_text)

    # Extract block geometry
    blocks_raw = page.get_text("blocks") or []
    blocks = []
    for b in blocks_raw:
        # b format: (x0, y0, x1, y1, "text", block_no, block_type)
        if len(b) >= 7:
            blocks.append(BlockMetadata(
                bbox=[round(coord, 2) for coord in b[:4]],
                text=b[4].strip(),
                block_type=b[6],
            ))

    # Detect tables if available in PyMuPDF
    has_tables = False
    try:
        tables = page.find_tables()
        if tables and len(tables.tables) > 0:
            has_tables = True
    except Exception:
        # Fallback if find_tables isn't available or fails
        has_tables = False

    # Compute inspectable page score
    score_breakdown = compute_page_score(
        text=raw_text,
        has_tables=has_tables,
        blocks=blocks,
        keywords=keywords,
        threshold=threshold,
    )

    return PageObject(
        document_id=doc_id,
        filename=filename,
        page_number=page_number,
        raw_text=raw_text,
        text_length=text_length,
        blocks_count=len(blocks),
        has_tables=has_tables,
        blocks=blocks,
        score_breakdown=score_breakdown,
    )


def parse_pdf_document(
    filepath: str,
    keywords: Optional[List[str]] = None,
    threshold: float = CANDIDATE_SCORE_THRESHOLD,
) -> DocumentIngestionResult:
    import time
    start_time = time.time()

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"PDF file not found at: {filepath}")

    filename = os.path.basename(filepath)
    # Stable document ID based on filepath hash
    doc_id = hashlib.md5(os.path.abspath(filepath).encode('utf-8')).hexdigest()[:12]

    doc = fitz.open(filepath)
    total_pages = len(doc)
    pages = []
    candidate_count = 0

    for i in range(total_pages):
        page_obj = parse_pdf_page(
            doc=doc,
            page_idx=i,
            doc_id=doc_id,
            filename=filename,
            keywords=keywords,
            threshold=threshold,
        )
        if page_obj.score_breakdown.is_candidate:
            candidate_count += 1
        pages.append(page_obj)

    doc.close()
    elapsed_ms = round((time.time() - start_time) * 1000, 2)
    candidate_rate = round(candidate_count / total_pages, 4) if total_pages > 0 else 0.0

    return DocumentIngestionResult(
        document_id=doc_id,
        filename=filename,
        filepath=os.path.abspath(filepath),
        total_pages=total_pages,
        candidate_pages_count=candidate_count,
        candidate_rate=candidate_rate,
        processing_time_ms=elapsed_ms,
        pages=pages,
    )
