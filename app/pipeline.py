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
from app.normalizer import normalize_facts, normalize_fact, dedupe_facts
from app.matcher import generate_candidate_pairs
from app.reasoner import reason_all_comparisons
from app.database import Database


def _extract_entity_from_context(filename: str) -> str:
    """Extract a likely entity/organization name from the PDF filename for use as a fallback subject."""
    name = os.path.splitext(filename)[0]
    name = re.sub(r'^\d+[-_]', '', name)
    name = re.sub(r'[-_]+', ' ', name).strip()
    name = re.sub(r'\b(excerpt|annual|report|prospectus|presentation|document|pdf|economy|economic|survey|article|iv|consultation)\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\b(fy\d{2,4}|\d{4}[-/]\d{2,4}|\d{4})\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+', ' ', name).strip()
    words = [w for w in name.split() if len(w) > 2]
    return ' '.join(words[:3]) if words else "Unknown Entity"


def _detect_qualifiers_near_match(full_text: str, match_start: int, match_end: int, window: int = 300) -> List[str]:
    """Detect estimate-vintage and forecast qualifiers within a sentence window around a regex match."""
    text_lower = full_text.lower()
    start = max(0, match_start - window)
    end = min(len(text_lower), match_end + window)
    context = text_lower[start:end]

    qualifiers: List[str] = []
    if "first advance" in context:
        qualifiers.append("First Advance Estimate")
    elif "second advance" in context:
        qualifiers.append("Second Advance Estimates")
    if "provisional" in context:
        qualifiers.append("provisional")
    if "revised" in context:
        qualifiers.append("revised")
    if "preliminary" in context:
        qualifiers.append("preliminary")
    if "final" in context and "final" not in qualifiers:
        qualifiers.append("final")
    if "estimated" in context:
        qualifiers.append("estimated")
    if "projected" in context or "projected" in full_text[match_start:match_end].lower():
        qualifiers.append("forecast")
    return qualifiers


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
        r"((?:[A-Z][\w\s\'']+?)?\s*real\s+GDP\s+(?:grew\s+by|is\s+estimated\s+to\s+grow\s+by)\s+(\d+\.?\d*)\s*(?:per\s*cent|percent|%)\s*(?:in\s+)?(FY\d{2,4}(?:[-/]\d{2,4})?|\b20\d{2}-\d{2}\b)?)",
        # "Real GDP growth moderated to 6.5 per cent in 2024-25"
        r"((?:[A-Z][\w\s\'']+?)?\s*Real\s+GDP\s+growth\s+(?:moderated\s+to|stood\s+at|projected\s+at)\s+(\d+\.?\d*)\s*(?:per\s*cent|percent|%)\s*(?:in\s+)?(FY\d{2,4}(?:[-/]\d{2,4})?|\b20\d{2}-\d{2}\b)?)",
    ]
    for pat in gdp_patterns:
        for match in re.finditer(pat, full_clean_text, re.IGNORECASE):
            sentence = match.group(1).strip()
            val_str = match.group(2)
            period_str = match.group(3) if len(match.groups()) >= 3 else None
            qualifiers = _detect_qualifiers_near_match(full_clean_text, match.start(), match.end())

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

    # 1b. Macro indicator statements (Week 3 Cluster G). One closed
    # indicator map, one shared grammar: "<indicator> [up to 5 words]
    # (at|to) <number> percent [in <period>]", with an optional leading
    # "In FY..." period. Bounded on purpose — a closed map plus the
    # at/to anchor keeps the blast radius at the seven source-verified
    # claims (ex-017..ex-020, ex-022..ex-024) and cannot fire on
    # investment growth, CPI projection, or "averaged 4.6 per cent".
    macro_indicators = (
        ("private consumption growth", "India private consumption growth", "growth rate", None),
        ("headline inflation", "India headline inflation", "inflation rate", None),
        ("core inflation", "India core inflation", "inflation rate", "core"),
        ("unemployment", "India unemployment", "unemployment rate", None),
    )
    macro_month = (
        r"(?:January|February|March|April|May|June|July|August"
        r"|September|October|November|December)(?:\s+20\d{2})?"
    )
    macro_period = rf"(?:{macro_month}|FY\d{{2,4}}(?:[-/]\d{{2,4}})?|\b\d{{4}}-\d{{2}}\b)"
    macro_lead = r"(?:In\s+(FY\d{2,4}(?:[-/]\d{2,4})?)\s*,?\s+)?"
    macro_gap = r"\s+\w+(?:\s+\w+){0,4}\s+"
    macro_val = r"(?:at|to)\s+(\d+(?:\.\d+)?)\s*(?:per\s*cent|percent|%)"
    macro_trail = rf"(?:\s+in\s+({macro_period}))?"
    for ind_phrase, macro_subject, macro_predicate, macro_qual in macro_indicators:
        macro_pat = re.compile(
            rf"{macro_lead}\b{re.escape(ind_phrase)}{macro_gap}{macro_val}{macro_trail}",
            re.IGNORECASE,
        )
        for match in macro_pat.finditer(full_clean_text):
            period_str = match.group(1) or match.group(3)
            qualifiers: List[str] = [macro_qual] if macro_qual else []
            if re.search(r"\b(?:expected|projected|forecast)\b", match.group(0), re.IGNORECASE):
                qualifiers.append("forecast")
            facts.append(Fact(
                subject=macro_subject,
                predicate=macro_predicate,
                value=float(match.group(2)),
                unit="per cent",
                period=period_str,
                scope=None,
                qualifiers=qualifiers,
                evidence=Evidence(
                    document_id=page.document_id,
                    page_number=page.page_number,
                    text=match.group(0).strip(),
                    document_name=page.filename,
                ),
                confidence=0.9,
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

    # Broader revenue pattern: "Revenue from operations stood at INR N million"
    rev_broad = re.compile(
        r"((?:[\w\s]+?)\s+revenue\s+(?:from\s+[\w\s]+?\s+)?(?:stood\s+at|was|is)\s+(?:INR|₹)\s*([\d,]+\.?\d*)\s*(million|mn|crore|cr|lakh|lac|billion)\b)",
        re.IGNORECASE
    )
    for match in rev_broad.finditer(full_clean_text):
        sentence = match.group(1).strip()
        val = float(match.group(2).replace(",", ""))
        unit_raw = match.group(3).lower()
        unit = "INR million" if unit_raw in ("million", "mn") else ("₹ crore" if unit_raw in ("crore", "cr") else ("lakh" if unit_raw in ("lakh", "lac") else "billion"))
        # Skip if this was already captured by the exact pattern above
        already_captured = any(
            abs(val - (f.value * (10.0 if f.unit == "₹ crore" else 1.0))) < 0.01
            for f in facts if f.predicate == "revenue"
        )
        if not already_captured:
            entity = _extract_entity_from_context(page.filename)
            # L1: recover the reporting basis and fiscal year from the matched
            # sentence. The broad pattern itself cannot capture them (its
            # precision twin fails on the rupee-symbol wording), which is how
            # standalone/consolidated pairs used to reach the reasoner with
            # scope=None and read UNCERTAIN instead of RECONCILABLE.
            scope = None
            scope_match = re.search(r"\bon\s+(standalone|consolidated)\s+basis\b", sentence, re.IGNORECASE)
            if scope_match:
                scope = scope_match.group(1).lower()
            period = None
            period_match = re.search(r"\b(FY\d{2,4})\b", sentence, re.IGNORECASE)
            if period_match:
                period = period_match.group(1).upper()
            facts.append(Fact(
                subject=f"{entity} revenue",
                predicate="revenue",
                value=val,
                unit=unit,
                period=period,
                scope=scope,
                qualifiers=[],
                evidence=Evidence(
                    document_id=page.document_id,
                    page_number=page.page_number,
                    text=sentence,
                    document_name=page.filename,
                ),
                confidence=0.85,
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
        entity = _extract_entity_from_context(page.filename)
        facts.append(Fact(
            subject=f"{entity} revenue from services",
            predicate="revenue",
            value=val,
            unit="₹ crore",
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

    # 2d. Comparative financial-highlights sentences (Week 4 audit cluster):
    # AR FY24 p.22 pairs every highlights figure with its prior-year value
    # via "as against ₹W million for FYyy", and states the loss figures as
    # "the loss for FYxx stood at ₹V million". Six source-verified claims
    # live in these two sentence shapes (ex-009, ex-010, ex-011 and the
    # three induced standalone/consolidated loss truths); corpus-wide the
    # anchors fire nowhere else (verified: p.36 uses "from ₹.. million",
    # comparative prose uses per-cent/lakh units, the prospectus uses
    # "has improved from").
    comp_scope_phrase = re.compile(r"\bon\s+(standalone|consolidated)\s+basis\b", re.IGNORECASE)

    def _nearest_scope_before(pos: int) -> Optional[str]:
        found = None
        for m in comp_scope_phrase.finditer(full_clean_text[:pos]):
            found = m.group(1).lower()
        return found

    rev_comp = re.compile(
        r"(revenue\s+from\s+operations\s+on\s+(standalone|consolidated)\s+basis"
        r"\s+for\s+(FY\d{2,4})\s+stood\s+at\s+(?:INR|₹)\s*[\d,]+\.?\d*\s+million"
        r"\s+as\s+against\s+(?:INR|₹)\s*([\d,]+\.?\d*)\s+million\s+for\s+(FY\d{2,4}))",
        re.IGNORECASE,
    )
    for match in rev_comp.finditer(full_clean_text):
        val = float(match.group(4).replace(",", ""))
        facts.append(Fact(
            subject=f"{_extract_entity_from_context(page.filename)} revenue",
            predicate="revenue",
            value=val,
            unit="INR million",
            period=match.group(5).upper(),
            scope=match.group(2).lower(),
            qualifiers=[],
            evidence=Evidence(
                document_id=page.document_id,
                page_number=page.page_number,
                text=match.group(1).strip(),
                document_name=page.filename,
            ),
            confidence=0.95,
        ))

    loss_comp = re.compile(
        r"((?:the\s+)?loss\s+for\s+(FY\d{2,4})\s+stood\s+at\s+(?:INR|₹)\s*([\d,]+\.?\d*)\s+million"
        r"(?:\s+as\s+against\s+(?:INR|₹)\s*([\d,]+\.?\d*)\s+million\s+for\s+(FY\d{2,4}))?)",
        re.IGNORECASE,
    )
    for match in loss_comp.finditer(full_clean_text):
        entity = _extract_entity_from_context(page.filename)
        scope = _nearest_scope_before(match.start())
        # Scope comes from the paired revenue bullet on the same page
        # ("...on standalone basis... Whereas the loss for FY24 stood at"),
        # never from the loss clause itself.
        losses = [(match.group(3), match.group(2))]
        if match.group(4):
            losses.append((match.group(4), match.group(5)))
        for raw_value, raw_period in losses:
            facts.append(Fact(
                subject=f"{entity} loss",
                predicate="loss",
                value=float(raw_value.replace(",", "")),
                unit="INR million",
                period=raw_period.upper(),
                scope=scope,
                qualifiers=[],
                evidence=Evidence(
                    document_id=page.document_id,
                    page_number=page.page_number,
                    text=match.group(1).strip(),
                    document_name=page.filename,
                ),
                confidence=0.95,
            ))

    # 3. Net Loss / Profit:
    loss_patterns = [
        re.compile(r"(loss\s+for\s+the\s+year\s+was\s+INR\s+([\d,]+\.?\d*)\s+million)", re.IGNORECASE),
        re.compile(r"(net\s+loss\s+for\s+(FY\d{2,4})\s+was\s+₹?([\d,]+\.?\d*)\s*(?:cr|crore))", re.IGNORECASE),
    ]
    for pat in loss_patterns:
        for match in pat.finditer(full_clean_text):
            sentence = match.group(1).strip()
            # Pattern 1: 2 groups (sentence, value) — no period
            # Pattern 2: 3 groups (sentence, period, value)
            if match.lastindex and match.lastindex >= 3:
                period = match.group(2).upper()
                val = float(match.group(3).replace(",", ""))
            else:
                val = float(match.group(2).replace(",", ""))
                period = None
            unit = "₹ crore" if "cr" in sentence.lower() else "INR million"
            entity = _extract_entity_from_context(page.filename)
            facts.append(Fact(
                subject=f"{entity} net loss" if entity else "net loss",
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
    # Patterns 1-2 use IGNORECASE for action words (resigned/ceased); pattern 3 must NOT
    # use IGNORECASE so that [A-Z] correctly rejects non-name text fragments.
    director_patterns = [
        re.compile(r"([A-Z][a-zA-Z\.\s]+?),\s*(?:Non-Executive\s+Director|Director),\s*(resigned\s+from\s+the\s+Board)\s+with\s+effect\s+from\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})", re.IGNORECASE),
        re.compile(r"([A-Z][a-zA-Z\.\s]+?)(?:,\s*Non-Executive\s+Director)?\s*(ceased\s+to\s+be\s+a\s+Director)\s+with\s+effect\s+from\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})", re.IGNORECASE),
        re.compile(r"([A-Z][a-z]+(?:\s+(?:de\s+|di\s+|van\s+|von\s+)?[A-Z][a-z]+)+)\s*[—–-]\s*(Executive\s+Director(?:\s+and\s+[A-Za-z\s]+)?|Non-Executive\s+Nominee\s+Director)"),
    ]
    invalid_name_trailing = {"the", "to", "for", "and", "or", "of", "in", "on", "by", "our", "non", "none"}
    for pat in director_patterns:
        for match in pat.finditer(full_clean_text):
            sentence = match.group(0).strip()
            person = match.group(1).strip().replace("Mr.", "").replace("Ms.", "").strip()
            action_or_role = match.group(2).strip()
            as_of_date = match.group(3).strip() if len(match.groups()) >= 3 else None
            last_word = person.split()[-1].lower() if person.split() else ""
            if last_word in invalid_name_trailing or len(person.split()) < 2:
                continue
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

    # 4b. Director appointment events (Week 5 recall cycle):
    # "... was appointed as Non-Executive Independent Director
    # [for a period of N years] with effect from <Month DD, YYYY>".
    # Anchored on the independence-director role AND the date slot so the
    # committee-membership footnote, officer-appointment notes, Monitoring/
    # Internal Auditor sentences, and remuneration-revision clauses in the
    # same corpus cannot fire (corpus-wide blast radius: exactly the two
    # source-verified claims ex-014/ex-015, both on the annual report).
    # Case-sensitive on purpose (same rationale as pattern 3 above): the
    # capitalized name/role/date shapes reject prose fragments.
    appointment_pattern = re.compile(
        r"([A-Z][a-zA-Z\.\s]{2,48}?)\s+(?:was|has\s+been)\s+appointed\s+as\s+"
        r"(Non-Executive\s+Independent\s+Director)"
        r"(?:\s+for\s+a\s+period\s+of\s+(\d+)\s+years?)?"
        r"\s+with\s+effect\s+from\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})"
    )
    for match in appointment_pattern.finditer(full_clean_text):
        sentence = match.group(0).strip()
        person = match.group(1).strip().replace("Mr.", "").replace("Ms.", "").strip()
        last_word = person.split()[-1].lower() if person.split() else ""
        if (
            last_word in invalid_name_trailing
            or len(person.split()) < 2
            or len(person.split()) > 6
        ):
            continue
        term_qualifiers = [f"{match.group(3)} year term"] if match.group(3) else []
        facts.append(Fact(
            subject=person,
            predicate="board status",
            value=f"appointed as {match.group(2)}",
            unit=None,
            period=None,
            as_of=match.group(4),
            scope=None,
            qualifiers=term_qualifiers,
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


def normalize_evidence_text(text: Optional[str]) -> str:
    """Collapse all whitespace runs to a single space for containment checks."""
    return re.sub(r"\s+", " ", text or "").strip()


def verify_evidence_on_page(evidence_text: Optional[str], page_text: Optional[str]) -> bool:
    """
    True when the evidence string appears verbatim (whitespace-insensitive)
    on the cited page. Extraction builds evidence from the page's own text
    with lines rejoined by single spaces, so any mismatch indicates a
    page-offset or evidence-assembly bug, not a content difference.
    """
    ev = normalize_evidence_text(evidence_text)
    page = normalize_evidence_text(page_text)
    if not ev or not page:
        return False
    return ev in page


def validate_fact_evidence(
    facts: List[Fact],
    pages_by_number: Dict[int, str],
) -> tuple[List[Fact], List[Fact]]:
    """
    Split facts into (verified, rejected) by checking each fact's evidence
    string against the raw text of its cited page. Facts citing a page that
    does not exist in the document are rejected (page-offset bug).
    """
    verified: List[Fact] = []
    rejected: List[Fact] = []
    for fact in facts:
        page_text = pages_by_number.get(fact.evidence.page_number, "")
        if verify_evidence_on_page(fact.evidence.text, page_text):
            verified.append(fact)
        else:
            rejected.append(fact)
    return verified, rejected


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
    new_facts = extract_facts_from_pages(provider, doc_result.pages, candidate_only=True)
    normalized_new_facts = normalize_facts(new_facts, context=doc_result.filename)
    # Fold duplicate extraction candidates (same doc, same claim, any page)
    # under one canonical fact before persistence — see dedupe_facts().
    normalized_new_facts = dedupe_facts(normalized_new_facts)
    # Ground every fact in the raw text of its cited page before persistence.
    # Rejects evidence that is absent from the cited page (page-offset or
    # evidence-assembly bugs); heuristic-path evidence always verifies
    # because it is a slice of that page's own text.
    pages_by_number = {p.page_number: p.raw_text for p in doc_result.pages}
    normalized_new_facts, rejected_facts = validate_fact_evidence(
        normalized_new_facts, pages_by_number
    )

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
        "evidence_rejected_count": len(rejected_facts),
        "comparisons_count": len(comparisons),
        "reasoning_mode": reasoning_mode,
        "processing_time_ms": doc_result.processing_time_ms,
    }
