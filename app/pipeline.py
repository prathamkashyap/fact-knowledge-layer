"""
Extraction pipeline: ties together page parsing, candidate selection,
and LLM-based fact extraction.
"""

from typing import List, Optional
from app.models import Fact
from app.pdf_parser import parse_pdf_document, PageObject
from app.providers import LLMProvider


def extract_facts_from_page(
    provider: LLMProvider,
    page: PageObject,
    candidate_only: bool = True,
) -> List[Fact]:
    """
    Extract facts from a single page using the given LLM provider.

    If candidate_only is True, skip pages that did not pass the prioritization
    threshold (score_breakdown.is_candidate == False).
    """
    if candidate_only and not page.score_breakdown.is_candidate:
        return []

    if not page.raw_text.strip():
        return []

    page_context = f"{page.filename} p.{page.page_number}"
    facts = provider.extract_facts(text=page.raw_text, page_context=page_context)

    # Attach document-level evidence metadata
    for fact in facts:
        fact.evidence.document_id = page.document_id
        fact.evidence.page_number = page.page_number
        fact.evidence.document_name = page.filename

    return facts


def extract_facts_from_document(
    provider: LLMProvider,
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
    provider: LLMProvider,
    pages: List[PageObject],
    candidate_only: bool = True,
) -> List[Fact]:
    """
    Extract facts from a pre-parsed list of pages.
    Useful for testing or when pages come from a specific selection.
    """
    all_facts: List[Fact] = []
    for page in pages:
        facts = extract_facts_from_page(provider, page, candidate_only=candidate_only)
        all_facts.extend(facts)
    return all_facts
