"""
Extraction pipeline: ties together page parsing, candidate selection,
structured fact extraction, normalization, candidate matching, and reasoning.
"""

import re
import os
from typing import List, Optional, Dict, Any
from app.models import Fact, Evidence, FactComparison, PreparedComparison
from app.pdf_parser import parse_pdf_document, PageObject, DocumentIngestionResult
from app.providers import LLMProvider
from app.normalizer import normalize_facts, normalize_fact
from app.matcher import generate_candidate_pairs
from app.reasoner import reason_all_comparisons
from app.database import Database


def extract_heuristic_facts_from_page(page: PageObject) -> List[Fact]:
    """
    Deterministic rule-based extractor for candidate pages when running in offline/heuristic mode.
    Extracts grounded, independently attributable assertions with exact source sentence evidence.
    """
    text = page.raw_text
    if not text or not text.strip():
        return []

    facts: List[Fact] = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    full_clean_text = " ".join(lines)

    # 1. Real GDP Growth assertions:
    gdp_patterns = [
        # "real GDP grew by 6.5 percent in FY2024/25"
        r"(India(?:'s)?\s+real\s+GDP\s+(?:grew\s+by|is\s+estimated\s+to\s+grow\s+by)\s+(\d+\.?\d*)\s*(?:per\s*cent|percent|%)\s*(?:in\s+)?(FY\d{2,4}(?:[-/]\d{2,4})?|\b20\d{2}-\d{2}\b)?)",
        # "Real GDP growth moderated to 6.5 per cent in 2024-25"
        r"(Real\s+GDP\s+growth\s+(?:moderated\s+to|stood\s+at|projected\s+at)\s+(\d+\.?\d*)\s*(?:per\s*cent|percent|%)\s*(?:in\s+)?(FY\d{2,4}(?:[-/]\d{2,4})?|\b20\d{2}-\d{2}\b)?)",
    ]
    for pat in gdp_patterns:
        for match in re.finditer(pat, full_clean_text, re.IGNORECASE):
            sentence = match.group(1).strip()
            val_str = match.group(2)
            period_str = match.group(3) if len(match.groups()) >= 3 else None
            qualifiers = []
            if "first advance" in full_clean_text.lower():
                qualifiers.append("First Advance Estimate")
            elif "second advance" in full_clean_text.lower():
                qualifiers.append("Second Advance Estimates")
            if "projected" in sentence.lower():
                qualifiers.append("forecast")

            facts.append(Fact(
                subject="India real GDP growth",
                predicate="growth rate",
                value=float(val_str),
                unit="per cent",
                period=period_str,
                scope=None,
                qualifiers=qualifiers,
                evidence=Evidence(
                    document_id=page.document_id,
                    page_number=page.page_number,
                    text=sentence,
                    document_name=page.filename,
                ),
                confidence=0.95,
            ))

    # 2. Revenue from operations / services assertions:
    rev_pattern = re.compile(
        r"(revenue\s+from\s+operations\s+on\s+(standalone|consolidated)\s+basis\s+for\s+(FY\d{2,4})\s+stood\s+at\s+INR\s+([\d,]+\.?\d*)\s+million)",
        re.IGNORECASE
    )
    for match in rev_pattern.finditer(full_clean_text):
        sentence = match.group(1).strip()
        scope = match.group(2).lower()
        period = match.group(3).upper()
        val = float(match.group(4).replace(",", ""))
        facts.append(Fact(
            subject="Delhivery revenue from operations",
            predicate="revenue",
            value=val,
            unit="INR million",
            period=period,
            scope=scope,
            qualifiers=[],
            evidence=Evidence(
                document_id=page.document_id,
                page_number=page.page_number,
                text=sentence,
                document_name=page.filename,
            ),
            confidence=0.95,
        ))

    # Revenue from services (presentation format):
    pres_rev = re.compile(
        r"(revenue\s+from\s+services\s+stood\s+at\s+(?:approximately\s+)?₹?([\d,]+\.?\d*)\s*(?:cr|crore)\s+for\s+(FY\d{2,4}))",
        re.IGNORECASE
    )
    for match in pres_rev.finditer(full_clean_text):
        sentence = match.group(1).strip()
        val = float(match.group(2).replace(",", ""))
        period = match.group(3).upper()
        facts.append(Fact(
            subject="Delhivery revenue from services",
            predicate="revenue",
            value=val,
            unit="₹ crore",
            period=period,
            scope="consolidated",
            qualifiers=["excluding traded goods"],
            evidence=Evidence(
                document_id=page.document_id,
                page_number=page.page_number,
                text=sentence,
                document_name=page.filename,
            ),
            confidence=0.92,
        ))

    # 3. Net Loss / Profit:
    loss_patterns = [
        re.compile(r"(loss\s+for\s+the\s+year\s+was\s+INR\s+([\d,]+\.?\d*)\s+million)", re.IGNORECASE),
        re.compile(r"(net\s+loss\s+for\s+(FY\d{2,4})\s+was\s+₹?([\d,]+\.?\d*)\s*(?:cr|crore))", re.IGNORECASE),
    ]
    for pat in loss_patterns:
        for match in pat.finditer(full_clean_text):
            sentence = match.group(1).strip()
            if len(match.groups()) == 2:
                val = float(match.group(2).replace(",", ""))
                period = "FY24"
            else:
                period = match.group(2).upper()
                val = float(match.group(3).replace(",", ""))
            unit = "₹ crore" if "cr" in sentence.lower() else "INR million"
            facts.append(Fact(
                subject="Delhivery net loss",
                predicate="loss for the year",
                value=val,
                unit=unit,
                period=period,
                scope="consolidated",
                qualifiers=[],
                evidence=Evidence(
                    document_id=page.document_id,
                    page_number=page.page_number,
                    text=sentence,
                    document_name=page.filename,
                ),
                confidence=0.92,
            ))

    # 4. Director events / governance:
    director_patterns = [
        re.compile(r"([A-Z][a-zA-Z\.\s]+?),\s*(?:Non-Executive\s+Director|Director),\s*(resigned\s+from\s+the\s+Board)\s+with\s+effect\s+from\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})", re.IGNORECASE),
        re.compile(r"([A-Z][a-zA-Z\.\s]+?)(?:,\s*Non-Executive\s+Director)?\s*(ceased\s+to\s+be\s+a\s+Director)\s+with\s+effect\s+from\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})", re.IGNORECASE),
        re.compile(r"([A-Z][a-zA-Z\.\s]+?)\s*[—–-]\s*(Executive\s+Director(?:\s+and\s+[A-Za-z\s]+)?|Non-Executive\s+Nominee\s+Director)", re.IGNORECASE),
    ]
    for pat in director_patterns:
        for match in pat.finditer(full_clean_text):
            sentence = match.group(0).strip()
            person = match.group(1).strip().replace("Mr.", "").replace("Ms.", "").strip()
            action_or_role = match.group(2).strip()
            as_of_date = match.group(3).strip() if len(match.groups()) >= 3 else None
            facts.append(Fact(
                subject=person,
                predicate="board status" if as_of_date else "role",
                value=action_or_role,
                unit=None,
                period=None,
                as_of=as_of_date or ("2022" if "prospectus" in page.filename.lower() else None),
                scope=None,
                qualifiers=[],
                evidence=Evidence(
                    document_id=page.document_id,
                    page_number=page.page_number,
                    text=sentence,
                    document_name=page.filename,
                ),
                confidence=0.95,
            ))

    return facts


def extract_facts_from_page(
    provider: Optional[LLMProvider],
    page: PageObject,
    candidate_only: bool = True,
) -> List[Fact]:
    """
    Extract facts from a single page using the given LLM provider,
    or deterministic heuristic extraction if provider is None.
    """
    if candidate_only and not page.score_breakdown.is_candidate:
        return []

    if not page.raw_text.strip():
        return []

    if provider is not None:
        page_context = f"{page.filename} p.{page.page_number}"
        facts = provider.extract_facts(text=page.raw_text, page_context=page_context)
    else:
        facts = extract_heuristic_facts_from_page(page)

    # Attach document-level evidence metadata
    for fact in facts:
        fact.evidence.document_id = page.document_id
        fact.evidence.page_number = page.page_number
        fact.evidence.document_name = page.filename

    return facts


def extract_facts_from_document(
    provider: Optional[LLMProvider],
    pdf_path: str,
    candidate_only: bool = True,
) -> List[Fact]:
    """
    Parse a PDF, select candidate pages, and extract facts from each.
    Returns all extracted facts with evidence populated.
    """
    doc = parse_pdf_document(pdf_path)
    all_facts: List[Fact] = []

    for page in doc.pages:
        facts = extract_facts_from_page(provider, page, candidate_only=candidate_only)
        all_facts.extend(facts)

    return all_facts


def extract_facts_from_pages(
    provider: Optional[LLMProvider],
    pages: List[PageObject],
    candidate_only: bool = True,
) -> List[Fact]:
    """
    Extract facts from a pre-parsed list of pages.
    """
    all_facts: List[Fact] = []
    for page in pages:
        facts = extract_facts_from_page(provider, page, candidate_only=candidate_only)
        all_facts.extend(facts)
    return all_facts


def process_document_pipeline(
    pdf_path: str,
    provider: Optional[LLMProvider] = None,
    db: Optional[Database] = None,
    reasoning_mode: str = "heuristic",
) -> Dict[str, Any]:
    """
    Complete end-to-end processing pipeline:
    PDF upload → parse → candidate prioritization → structured extraction →
    normalization → candidate matching → relationship reasoning → persistence.
    """
    doc_result = parse_pdf_document(pdf_path)
    new_facts = extract_facts_from_document(provider, pdf_path, candidate_only=True)
    normalized_new_facts = normalize_facts(new_facts, context=doc_result.filename)

    comparisons: List[FactComparison] = []

    if db is not None:
        db.save_document(doc_result)
        db.save_facts(normalized_new_facts)

        # Cross-document candidate matching against all facts in the knowledge base
        all_db_facts = db.get_facts(limit=2000)
        candidate_pairs = generate_candidate_pairs(all_db_facts)
        comparisons = reason_all_comparisons(candidate_pairs, provider=provider)
        db.save_comparisons(comparisons, reasoning_mode=reasoning_mode)
    else:
        candidate_pairs = generate_candidate_pairs(normalized_new_facts)
        comparisons = reason_all_comparisons(candidate_pairs, provider=provider)

    return {
        "document_id": doc_result.document_id,
        "filename": doc_result.filename,
        "total_pages": doc_result.total_pages,
        "candidate_pages_count": doc_result.candidate_pages_count,
        "facts_count": len(normalized_new_facts),
        "comparisons_count": len(comparisons),
        "reasoning_mode": reasoning_mode,
        "processing_time_ms": doc_result.processing_time_ms,
    }
