"""
Conservative fact normalizer and canonicalizer for the Superjoin Fact Knowledge Layer.
Handles:
- Entity canonicalization (stripping corporate legal suffixes, resolving anaphora with context)
- Predicate canonicalization (standardizing reporting metrics into candidate groupings)
- Period / as_of normalization (fiscal years, quarters, calendar dates, without collapsing granularities)
- Unit normalization & conversion (percent, INR crore -> INR million, etc.)
- Numerical comparability & rounding detection (without float equality or premature relationship decisions)
- Categorical fact preservation (retaining exact source wording)
- Scope and qualifier preservation
"""

import re
from datetime import datetime
from typing import Union, List, Optional, Tuple, Dict, Any
from app.models import Fact, NumericalComparison, PeriodComparison


# ---------------------------------------------------------------------------
# Entity Canonicalization
# ---------------------------------------------------------------------------

LEGAL_SUFFIXES_RE = re.compile(
    r'\b(?:Private\s+Limited|Pvt\.?\s*Ltd\.?|Limited|Ltd\.?|Corporation|Corp\.?|'
    r'Incorporated|Inc\.?|LLC|LLP|PLC)\b',
    re.IGNORECASE
)

TITLE_PREFIXES_RE = re.compile(
    r'^(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri|Prof\.?)\s+',
    re.IGNORECASE
)

# Metric terms commonly appended in unstructured subjects (sorted longest first)
COMMON_METRIC_TERMS = sorted([
    "revenue from operations", "revenue from services", "total revenue", "revenue",
    "real gdp growth", "real gdp", "gdp growth", "gdp",
    "profit after tax", "net profit for the year", "net profit",
    "loss for the year", "net loss for the year", "net loss", "pat",
    "cpi inflation", "inflation", "fiscal deficit"
], key=len, reverse=True)


def canonicalize_entity(subject: str, context: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """
    Canonicalize an entity/subject string while returning any embedded metric phrase.

    Returns:
        (canonical_subject, optional_metric_remainder)
    """
    if not subject:
        return ("", None)

    raw = subject.strip()

    # 1. Check for anaphora ("the Company", "the Group", "the Bank")
    if raw.lower() in ("the company", "the group", "the firm", "the corporation", "company"):
        if context:
            ctx_clean = re.sub(r'[\-_]', ' ', context)
            if re.search(r'\bdelhivery\b', ctx_clean, re.IGNORECASE):
                return ("Delhivery", None)
            if re.search(r'\brbi\b|reserve bank', ctx_clean, re.IGNORECASE):
                return ("Reserve Bank of India", None)
            words = [w for w in ctx_clean.split() if w[0].isupper() and len(w) > 2]
            if words:
                return (words[0], None)
        return (raw, None)

    # 2. Check for institutional acronyms & sovereign references
    raw_lower = raw.lower()
    if raw_lower.startswith("reserve bank of india") or raw_lower == "rbi":
        remainder = raw[len("reserve bank of india"):].strip() if raw_lower.startswith("reserve bank of india") else None
        return ("Reserve Bank of India", remainder or None)

    if raw_lower.startswith("international monetary fund") or raw_lower == "imf":
        remainder = raw[len("international monetary fund"):].strip() if raw_lower.startswith("international monetary fund") else None
        return ("IMF", remainder or None)

    if raw_lower.startswith("government of india") or raw_lower.startswith("govt of india") or raw_lower.startswith("govt. of india"):
        return ("Government of India", None)

    # 3. Check for sovereign possessive or entity+metric combinations:
    sovereign_match = re.match(r"^(India(?:'s)?|Indian(?:\s+economy)?)\s+(.+)$", raw, re.IGNORECASE)
    if sovereign_match:
        entity = "India"
        remainder = sovereign_match.group(2).strip()
        return (entity, remainder)

    if raw_lower in ("india", "india's", "indian economy"):
        return ("India", None)

    # 4. Check for corporate entity + metric combination:
    remainder = None
    for metric in COMMON_METRIC_TERMS:
        pattern = re.compile(rf'^(.*?)\s+(?:on\s+)?(?:the\s+)?({re.escape(metric)}.*)$', re.IGNORECASE)
        match = pattern.match(raw)
        if match:
            raw = match.group(1).strip()
            remainder = match.group(2).strip()
            # Clean residual qualifiers/scope left at end of subject (e.g. "Delhivery net" -> "Delhivery")
            raw = re.sub(r'\b(?:net|standalone|consolidated)\b$', '', raw, flags=re.IGNORECASE).strip()
            break

    # 5. Clean personal titles
    cleaned = TITLE_PREFIXES_RE.sub("", raw).strip()

    # 6. Clean legal corporate suffixes ("Delhivery Limited" -> "Delhivery")
    cleaned = LEGAL_SUFFIXES_RE.sub("", cleaned).strip()

    # 7. Strip trailing commas/periods/dashes
    cleaned = re.sub(r'[\s,\.\-]+$', '', cleaned).strip()

    canonical = cleaned if cleaned else raw
    return (canonical, remainder)


# ---------------------------------------------------------------------------
# Predicate Canonicalization
# ---------------------------------------------------------------------------

def canonicalize_predicate(predicate: str, subject_remainder: Optional[str] = None) -> str:
    """
    Standardize predicate into a canonical semantic grouping representation.
    Incorporates any metric remainder extracted from subject if present.
    """
    combined = (predicate or "").lower()
    if subject_remainder:
        combined = f"{subject_remainder.lower()} {combined}".strip()

    combined_clean = re.sub(r'[\-_/]', ' ', combined)

    # 1. Revenue
    if any(k in combined_clean for k in ("revenue", "turnover", "sales")):
        return "revenue"

    # 2. GDP Growth
    if ("gdp" in combined_clean and "growth" in combined_clean) or "economic growth" in combined_clean:
        return "gdp_growth"

    # If predicate is just "growth rate" or "growth" without GDP:
    if combined_clean in ("growth rate", "growth", "growth %", "real growth"):
        if subject_remainder and "gdp" in subject_remainder.lower():
            return "gdp_growth"

    # 3. Inflation
    if any(k in combined_clean for k in ("inflation", "cpi", "consumer price index")):
        return "inflation"

    # 4. Fiscal Deficit
    if "fiscal deficit" in combined_clean or "budget deficit" in combined_clean:
        return "fiscal_deficit"

    # 5. Profit / Loss
    if any(k in combined_clean for k in ("profit after tax", "net profit", "pat", "loss for the year", "net loss", "profit loss")):
        return "net_profit_loss"

    # 6. EBITDA
    if "ebitda" in combined_clean:
        return "ebitda"

    # 7. Board & Governance
    if any(k in combined_clean for k in ("board", "director", "role", "designation", "position", "resignation", "cessation", "appointment")):
        return "board_role"

    # 8. Network / Infrastructure reach
    if any(k in combined_clean for k in ("pin code", "pincode", "network reach", "reach", "coverage")):
        return "network_reach"

    # Fallback: clean lowercase string with underscores
    slug = re.sub(r'\s+', '_', combined_clean.strip())
    slug = re.sub(r'[^a-z0-9_]', '', slug)
    return slug or "unknown_predicate"


# ---------------------------------------------------------------------------
# Period / As-Of Normalization
# ---------------------------------------------------------------------------

def normalize_period(
    period: Optional[str],
    as_of: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Normalize period and as_of without collapsing different granularities.

    Returns:
        (normalized_period, period_type)
        where period_type in {"fiscal_year", "quarter", "date", "as_of", "unknown"}
    """
    raw = (period or "").strip()
    raw_as_of = (as_of or "").strip()

    # If period is missing, try as_of
    target = raw if raw else raw_as_of
    if not target:
        return (None, "unknown")

    # 1. Quarter patterns: e.g. "Q4 FY24", "Q4 2023-24", "Q4 FY2024", "4Q FY24"
    quarter_match = re.search(
        r'\b(?:(Q[1-4])|(?:[1-4]Q))\s*(?:of\s*)?(?:FY\s*)?(\d{2,4}(?:[-/]\d{2,4})?)',
        target,
        re.IGNORECASE
    )
    if quarter_match:
        q_label = (quarter_match.group(1) or "Q" + target[0]).upper()
        fy_part = quarter_match.group(2)
        if not fy_part.upper().startswith("FY") and len(fy_part) in (2, 4):
            fy_part = f"FY{fy_part}"
        norm_fy, _ = normalize_period(fy_part)
        norm_val = f"{norm_fy}-{q_label}" if norm_fy else f"{target}-{q_label}"
        return (norm_val, "quarter")

    # 2. Fiscal year span patterns:
    # "2024-25", "FY2024-25", "FY 2024-25", "2024/25", "FY2024/25", "2023-2024", "FY23-24"
    fy_span_match = re.search(
        r'(?:FY\s*)?20?(\d{2})[-/](?:20)?(\d{2})',
        target,
        re.IGNORECASE
    )
    if fy_span_match:
        start_yy = int(fy_span_match.group(1))
        end_yy = int(fy_span_match.group(2))
        start_year = 2000 + start_yy
        end_year = 2000 + end_yy
        return (f"FY{start_year}-{str(end_year)[-2:]}", "fiscal_year")

    # 3. Single FY patterns: "FY24", "FY 24", "FY2024", "FY25", "FY 2025"
    single_fy_match = re.search(
        r'\bFY\s*(\d{2,4})\b',
        target,
        re.IGNORECASE
    )
    if single_fy_match:
        num = int(single_fy_match.group(1))
        end_year = 2000 + num if num < 100 else num
        start_year = end_year - 1
        return (f"FY{start_year}-{str(end_year)[-2:]}", "fiscal_year")

    # 4. Standard calendar date patterns:
    date_formats = [
        "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y",
        "%d %B %Y", "%d %b %Y", "%d %B, %Y",
        "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"
    ]
    date_str = re.sub(r'(\d+)(?:st|nd|rd|th)\b', r'\1', target).strip()
    for fmt in date_formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return (dt.strftime("%Y-%m-%d"), "date" if raw else "as_of")
        except ValueError:
            continue

    # 5. As of year mention: "as of the 2022 Prospectus", "2022"
    year_match = re.search(r'\b(20\d{2})\b', target)
    if year_match:
        return (year_match.group(1), "as_of" if raw_as_of or "as of" in target.lower() else "date")

    return (target, "unknown")


# ---------------------------------------------------------------------------
# Unit Normalization & Numerical Value Conversion
# ---------------------------------------------------------------------------

def normalize_unit_and_value(
    value: Union[float, str],
    unit: Optional[str],
) -> Tuple[Union[float, str], Optional[str]]:
    """
    Normalize unit and convert numeric values to standard base units.
    Standard bases:
    - Percentage: "percent"
    - Indian currency: "INR million" (1 crore = 10 million, 1 lakh = 0.1 million)
    Categorical string values are left untouched.
    """
    if isinstance(value, str):
        try:
            num = float(value.replace(",", "").strip())
            value = num
        except ValueError:
            return (value, None)

    if unit is None:
        return (round(value, 4), None)

    raw_unit = unit.strip().lower()

    # 1. Percentage
    if any(raw_unit == p or raw_unit.startswith(p) for p in ("%", "percent", "per cent", "pct", "% change")):
        return (round(value, 4), "percent")

    # 2. Currency: INR (Base: "INR million")
    if "crore" in raw_unit or "cr" in raw_unit.split() or raw_unit in ("cr", "cr."):
        # 1 crore = 10 million
        converted = round(value * 10.0, 4)
        return (converted, "INR million")

    if "million" in raw_unit or "mn" in raw_unit.split() or "inr m" in raw_unit:
        return (round(value, 4), "INR million")

    if "lakh" in raw_unit or "lac" in raw_unit:
        converted = round(value * 0.1, 4)
        return (converted, "INR million")

    if "billion" in raw_unit:
        converted = round(value * 1000.0, 4)
        return (converted, "INR million")

    if raw_unit in ("inr", "₹", "rs", "rs.", "rupees"):
        converted = round(value / 1000000.0, 6)
        return (converted, "INR million")

    return (round(value, 4), raw_unit)


# ---------------------------------------------------------------------------
# Numerical Comparability & Rounding Analysis
# ---------------------------------------------------------------------------

def compare_numbers(
    v1: Union[float, str],
    u1: Optional[str],
    v2: Union[float, str],
    u2: Optional[str],
    rounding_rel_tol: float = 0.005,  # 0.5% relative tolerance for rounding across formats
) -> NumericalComparison:
    """
    Establish numerical comparability between two facts.
    Detects exact equality, rounding differences (e.g. crore vs million), and genuine differences.
    Does NOT declare semantic corroboration (only numerical comparability).
    """
    if isinstance(v1, str) or isinstance(v2, str):
        return NumericalComparison(
            is_numeric=False,
            status="categorical",
            rounding_note="Categorical value comparison"
        )

    if u1 != u2:
        return NumericalComparison(
            is_numeric=True,
            status="incompatible_units",
            value_a_norm=v1,
            value_b_norm=v2,
            unit_norm=f"{u1} vs {u2}",
            rounding_note=f"Incompatible units cannot be directly compared: {u1} vs {u2}"
        )

    abs_diff = round(abs(v1 - v2), 6)
    max_val = max(abs(v1), abs(v2))
    rel_diff = round((abs_diff / max_val), 6) if max_val > 0 else 0.0

    if abs_diff < 1e-5:
        return NumericalComparison(
            is_numeric=True,
            status="exact",
            is_exact=True,
            is_close_rounding=False,
            value_a_norm=v1,
            value_b_norm=v2,
            unit_norm=u1,
            absolute_diff=abs_diff,
            relative_diff=rel_diff,
            rounding_note="Exact numerical match"
        )

    if rel_diff <= rounding_rel_tol:
        pct_diff = round(rel_diff * 100, 4)
        return NumericalComparison(
            is_numeric=True,
            status="close_rounding",
            is_exact=False,
            is_close_rounding=True,
            value_a_norm=v1,
            value_b_norm=v2,
            unit_norm=u1,
            absolute_diff=abs_diff,
            relative_diff=rel_diff,
            rounding_note=(
                f"Values differ by {pct_diff}% ({abs_diff} {u1}), "
                "consistent with rounding across disclosure formats (e.g. crore to million)."
            )
        )

    return NumericalComparison(
        is_numeric=True,
        status="different",
        is_exact=False,
        is_close_rounding=False,
        value_a_norm=v1,
        value_b_norm=v2,
        unit_norm=u1,
        absolute_diff=abs_diff,
        relative_diff=rel_diff,
        rounding_note=f"Material numerical difference of {round(rel_diff * 100, 2)}%."
    )


# ---------------------------------------------------------------------------
# Period Compatibility Analysis
# ---------------------------------------------------------------------------

def compare_periods(
    p1: Optional[str],
    pt1: Optional[str],
    p2: Optional[str],
    pt2: Optional[str],
) -> PeriodComparison:
    """Compare normalized periods and their structural granularities."""
    if not p1 or not p2:
        return PeriodComparison(
            period_a_norm=p1,
            period_b_norm=p2,
            type_a=pt1,
            type_b=pt2,
            is_same_period=False,
            is_same_type=False,
            compatibility_note="One or both periods are unspecified."
        )

    is_same_type = (pt1 == pt2)
    is_same_period = (p1.strip().lower() == p2.strip().lower()) and is_same_type

    if is_same_period:
        note = f"Identical {pt1}: {p1}"
    elif not is_same_type:
        note = f"Different period granularities: {pt1} ({p1}) vs {pt2} ({p2})"
    else:
        note = f"Different {pt1}s: {p1} vs {p2}"

    return PeriodComparison(
        period_a_norm=p1,
        period_b_norm=p2,
        type_a=pt1,
        type_b=pt2,
        is_same_period=is_same_period,
        is_same_type=is_same_type,
        compatibility_note=note
    )


# ---------------------------------------------------------------------------
# Fact Normalization Entry Points
# ---------------------------------------------------------------------------

def normalize_fact(fact: Fact, context: Optional[str] = None) -> Fact:
    """
    Produce a normalized copy of a Fact object with canonical fields populated.
    Preserves all original fields and evidence intact.
    """
    ctx = context or fact.evidence.document_name

    # 1. Canonicalize entity and extract any metric remainder
    canonical_subject, metric_remainder = canonicalize_entity(fact.subject, context=ctx)

    # 2. Canonicalize predicate
    canonical_predicate = canonicalize_predicate(fact.predicate, subject_remainder=metric_remainder)

    # 3. Normalize period & as_of
    norm_period, period_type = normalize_period(fact.period, as_of=fact.as_of)

    # 4. Normalize value & unit
    norm_value, norm_unit = normalize_unit_and_value(fact.value, fact.unit)

    # Return updated clone
    return fact.model_copy(update={
        "canonical_subject": canonical_subject,
        "canonical_predicate": canonical_predicate,
        "normalized_value": norm_value,
        "normalized_unit": norm_unit,
        "normalized_period": norm_period,
        "period_type": period_type,
    })


def normalize_facts(facts: List[Fact], context: Optional[str] = None) -> List[Fact]:
    """Normalize a collection of Fact objects."""
    return [normalize_fact(f, context=context) for f in facts]
