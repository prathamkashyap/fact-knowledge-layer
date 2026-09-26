"""Week 3 Step 1: Cluster G macro-indicator regression tests.

Named regression tests for every source-verified recall-miss gold claim
the macro-indicator family is supposed to extract (ex-017..ex-020,
ex-022..ex-024), negative-grammar guards for the sentences the closed
indicator map must NOT fire on, a corpus-wide blast-radius test proving
exactly those seven claims and nothing else come out of the family, and
reasoner tests for the numeric different-period reconciliation rule (8b)
that the new cross-document inflation pairs require.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models import Fact, Evidence
from app.pdf_parser import PageObject, ScoreBreakdown, parse_pdf_document
from app.pipeline import extract_heuristic_facts_from_page, verify_evidence_on_page
from app.matcher import generate_candidate_pairs
from app.reasoner import reason_comparison


MACRO_SUBJECTS = {
    "India private consumption growth",
    "India headline inflation",
    "India core inflation",
    "India unemployment",
}


def _page(raw_text: str, filename: str = "test.pdf", page_number: int = 1) -> PageObject:
    return PageObject(
        document_id="macro_test",
        filename=filename,
        page_number=page_number,
        raw_text=raw_text,
        text_length=len(raw_text),
        blocks_count=1,
        has_tables=False,
        score_breakdown=ScoreBreakdown(total_score=10.0, is_candidate=True),
    )


def _one(facts, subject: str) -> Fact:
    matched = [f for f in facts if f.subject == subject]
    assert len(matched) == 1, f"expected exactly one {subject!r}, got {len(matched)}"
    return matched[0]


def _macro_subjects(facts):
    return [f for f in facts if f.subject in MACRO_SUBJECTS]


def _val(value) -> str:
    """Mirror scripts/evaluate.py claim-value stringification."""
    if isinstance(value, float):
        text = repr(value)
        if text.endswith(".0"):
            text = text[:-2]
        return text
    return str(value)


# ---------------------------------------------------------------------------
# Named regression tests: one per gold claim, against its real source text
# ---------------------------------------------------------------------------

def test_ex_017_private_consumption_growth_extracted():
    """ex-017: IMF p.10 private consumption growth 7.2 percent, no period."""
    facts = extract_heuristic_facts_from_page(_page(
        "Private consumption growth was buoyant at 7.2 percent on increasing "
        "rural demand and stable urban demand. Investment growth moderated to "
        "7.1 percent, supported by easy financial conditions."
    ))
    f = _one(facts, "India private consumption growth")
    assert f.predicate == "growth rate"
    assert f.value == 7.2
    assert f.unit == "per cent"
    assert f.period is None
    # The adjacent investment-growth claim must NOT be extracted (closed map).
    assert not [x for x in facts if "investment" in x.subject.lower()]


def test_ex_018_headline_inflation_extracted():
    """ex-018: IMF p.10 headline inflation 1.5 percent in September 2025."""
    facts = extract_heuristic_facts_from_page(_page(
        "Inflation dynamics are benign. Headline inflation has declined to "
        "1.5 percent in September 2025, down from 4.6 percent (FY2024/25 "
        "average), reflecting lower domestic food prices from good harvests."
    ))
    f = _one(facts, "India headline inflation")
    assert f.predicate == "inflation rate"
    assert f.value == 1.5  # not the 4.6 parenthetical distractor
    assert f.period == "September 2025"
    assert f.unit == "per cent"


def test_ex_019_core_inflation_extracted():
    """ex-019: IMF p.10 core inflation 4.6 percent, not the 3.5 parenthetical."""
    facts = extract_heuristic_facts_from_page(_page(
        "Core inflation increased to 4.6 percent (from 3.5 percent "
        "FY2024/25 average), in part due to rising gold and silver prices."
    ))
    f = _one(facts, "India core inflation")
    assert f.predicate == "inflation rate"
    assert f.value == 4.6  # not 3.5
    assert f.period is None
    assert "core" in f.qualifiers


def test_ex_020_unemployment_extracted():
    """ex-020: IMF p.10 unemployment 5.2 percent in September (no year in source)."""
    facts = extract_heuristic_facts_from_page(_page(
        "Unemployment has remained low at 5.2 percent in September. Despite "
        "recent improvements, challenges persist."
    ))
    f = _one(facts, "India unemployment")
    assert f.predicate == "unemployment rate"
    assert f.value == 5.2
    assert f.period == "September"


def test_ex_022_rbi_annual_headline_extracted():
    """ex-022: RBI p.17 headline inflation 4.6 percent in 2024-25 (line-wrapped)."""
    facts = extract_heuristic_facts_from_page(_page(
        "working through the system, headline inflation \n"
        "eased by 73 bps to 4.6 per cent in 2024-25. "
        "Going forward, easing supply chain pressures."
    ))
    f = _one(facts, "India headline inflation")
    assert f.value == 4.6  # the level after "to", not the 73 bps change
    assert f.period == "2024-25"
    assert f.unit == "per cent"


def test_ex_023_rbi_monthly_headline_extracted():
    """ex-023: RBI p.39 headline inflation 3.3 percent in March 2025 (line-wrapped)."""
    facts = extract_heuristic_facts_from_page(_page(
        "even as core inflation remained largely contained while fuel "
        "continued to be in deflation. Headline \n"
        "inflation eased to 3.3 per cent in March 2025 on sharp moderation "
        "in food inflation."
    ))
    # The "core inflation remained largely contained" clause must not fire.
    f = _one(facts, "India headline inflation")
    assert f.value == 3.3
    assert f.period == "March 2025"


def test_ex_024_imf_projection_forecast_qualifier():
    """ex-024: IMF p.13 headline inflation projected to converge to 4 percent in FY2026/27."""
    facts = extract_heuristic_facts_from_page(_page(
        "Core inflation is projected to average 3.5 percent this FY. "
        "In FY2026/27, headline inflation is expected to converge to "
        "4 percent, mainly reflecting subdued food price dynamics."
    ))
    # The "projected to average 3.5 percent" core sentence must not fire:
    # the grammar anchors on (at|to) <number>, not (to|at) <word>.
    assert not [x for x in facts if x.subject == "India core inflation"]
    f = _one(facts, "India headline inflation")
    assert f.value == 4.0
    assert _val(f.value) == "4"
    assert f.period == "FY2026/27"
    assert "forecast" in f.qualifiers


# ---------------------------------------------------------------------------
# Negative grammar guards: sentences inside the same pages the map must ignore
# ---------------------------------------------------------------------------

def test_cpi_projection_without_map_indicator_not_extracted():
    facts = extract_heuristic_facts_from_page(_page(
        "Taking into account these factors, CPI inflation for 2025-26 is "
        "projected at 4.0 per cent, with risks evenly balanced."
    ))
    assert _macro_subjects(facts) == []


def test_multi_step_price_history_not_extracted():
    """The CPI headline sentence spans >5 words to the first (at|to) <number>."""
    facts = extract_heuristic_facts_from_page(_page(
        "CPI headline inflation in India eased from 4.8 per cent in "
        "April-May 2024 to 3.6 per cent in July 2024 before rising again to "
        "6.2 per cent in October 2024 (Chart II.3.3)."
    ))
    assert _macro_subjects(facts) == []


def test_averaged_wording_without_at_to_anchor_not_extracted():
    facts = extract_heuristic_facts_from_page(_page(
        "Overall, headline inflation averaged 4.6 per cent during 2024-25, "
        "73 basis points (bps) lower than the previous year."
    ))
    assert _macro_subjects(facts) == []


def test_projected_to_average_wording_not_extracted():
    facts = extract_heuristic_facts_from_page(_page(
        "Core inflation is projected to average 3.5 percent this FY."
    ))
    assert _macro_subjects(facts) == []


def test_gdp_sentence_produces_no_macro_facts():
    facts = extract_heuristic_facts_from_page(_page(
        "India's real GDP grew by 6.5 percent in FY2024/25."
    ))
    assert _macro_subjects(facts) == []
    assert any(f.subject == "India real GDP growth" for f in facts)


# ---------------------------------------------------------------------------
# Blast radius: exactly the seven source-verified claims, corpus-wide
# ---------------------------------------------------------------------------

def test_macro_blast_radius_exact_on_real_corpus():
    """Across all 6 starter PDFs the macro family yields exactly 7 claims.

    Every fact's evidence must ground on the page it cites. This is the
    precision tripwire for the closed indicator map: any new (subject,
    value, period) triple — including investment growth or a CPI projection
    — fails this test.
    """
    expected = {
        ("india private consumption growth", "7.2", ""),
        ("india headline inflation", "1.5", "september 2025"),
        ("india core inflation", "4.6", ""),
        ("india unemployment", "5.2", "september"),
        ("india headline inflation", "4.6", "2024-25"),
        ("india headline inflation", "3.3", "march 2025"),
        ("india headline inflation", "4", "fy2026/27"),
    }
    pdfs = sorted((REPO_ROOT / "starter-datasets").rglob("*.pdf"))
    assert len(pdfs) == 6

    got = set()
    all_fact_count = 0
    for path in pdfs:
        doc = parse_pdf_document(str(path))
        for page in doc.pages:
            if not page.score_breakdown.is_candidate:
                continue
            for f in extract_heuristic_facts_from_page(page):
                all_fact_count += 1
                if f.subject in MACRO_SUBJECTS:
                    assert verify_evidence_on_page(f.evidence.text, page.raw_text), (
                        f"evidence for {f.subject}={f.value} does not ground "
                        f"on {path.name} p.{page.page_number}"
                    )
                    got.add((
                        f.subject.casefold(),
                        _val(f.value),
                        (f.period or "").casefold(),
                    ))
                    assert "investment" not in f.subject.casefold()

    assert got == expected


# ---------------------------------------------------------------------------
# Reasoner: rule 8b (numeric different-period reconciliation) and its bounds
# ---------------------------------------------------------------------------

def _inflation_fact(value, period, doc="d", page=1, subject="India headline inflation",
                    predicate="inflation rate", qualifiers=None):
    return Fact(
        subject=subject,
        predicate=predicate,
        value=value,
        unit="per cent",
        period=period,
        scope=None,
        qualifiers=qualifiers or [],
        evidence=Evidence(
            document_id=doc,
            page_number=page,
            text=f"{subject} {value} per cent {period or ''}".strip(),
        ),
        confidence=0.9,
    )


def _reason_pair(fa, fb):
    pairs = generate_candidate_pairs([fa, fb])
    assert len(pairs) == 1, f"expected 1 candidate pair, got {len(pairs)}"
    return reason_comparison(pairs[0])


def test_rule_8b_reconciles_cross_source_different_periods():
    """IMF September 2025 (1.5) x RBI 2024-25 annual (4.6) -> RECONCILABLE."""
    comp = _reason_pair(
        _inflation_fact(1.5, "September 2025", doc="imf", page=10),
        _inflation_fact(4.6, "2024-25", doc="rbi", page=17),
    )
    assert comp.relationship == "RECONCILABLE"
    assert "September 2025" in comp.reason
    assert "2024-25" in comp.reason


def test_normalize_period_keeps_month_year_granularity():
    """'September 2025' and 'March 2025' must not collapse to bare '2025'.

    Regression: the bare-year fallback used to normalize both to
    ('2025', 'date'), so compare_periods reported the same period and
    rule 9 surfaced CONTRADICTS for two different months of 2025.
    """
    from app.normalizer import normalize_period
    sep, sep_type = normalize_period("September 2025")
    mar, mar_type = normalize_period("March 2025")
    assert sep != mar
    assert sep == "2025-09-01"
    assert mar == "2025-03-01"
    assert sep_type == mar_type == "date"
    # Full day-dates keep their existing behavior.
    full, full_type = normalize_period("March 31, 2024")
    assert full == "2024-03-31"
    assert full_type == "date"


def test_rule_8b_reconciles_two_months_of_same_year():
    """IMF September 2025 (1.5) x RBI March 2025 (3.3) -> RECONCILABLE, not CONTRADICTS."""
    comp = _reason_pair(
        _inflation_fact(1.5, "September 2025", doc="imf", page=10),
        _inflation_fact(3.3, "March 2025", doc="rbi", page=39),
    )
    assert comp.relationship == "RECONCILABLE"
    assert "September 2025" in comp.reason
    assert "March 2025" in comp.reason


def test_rule_8b_reconciles_same_source_annual_vs_month():
    """RBI 2024-25 annual (4.6) x RBI March 2025 (3.3) -> RECONCILABLE, not CONTRADICTS."""
    comp = _reason_pair(
        _inflation_fact(4.6, "2024-25", doc="rbi", page=17),
        _inflation_fact(3.3, "March 2025", doc="rbi", page=39),
    )
    assert comp.relationship == "RECONCILABLE"
    assert "March 2025" in comp.reason
    assert "2024-25" in comp.reason


def test_rule_8b_still_yields_contradiction_for_same_period():
    """Rule 8b must not hijack rule 9: same period, different values still CONTRADICTS."""
    comp = _reason_pair(
        _inflation_fact(4.6, "2024-25", doc="rbi", page=17),
        _inflation_fact(3.3, "2024-25", doc="rbi", page=39),
    )
    assert comp.relationship == "CONTRADICTS"


def test_rule_8b_not_fired_when_one_period_missing():
    """Core inflation has no period: safe judgment impossible -> UNCERTAIN."""
    comp = _reason_pair(
        _inflation_fact(1.5, "September 2025", doc="imf", page=10),
        _inflation_fact(4.6, None, doc="imf", page=10, subject="India core inflation"),
    )
    assert comp.relationship == "UNCERTAIN"


def test_equal_values_different_series_not_corroboration():
    """Core 4.6 x headline 4.6: numerical match must not shortcut to CORROBORATES."""
    comp = _reason_pair(
        _inflation_fact(4.6, None, doc="imf", page=10, subject="India core inflation"),
        _inflation_fact(4.6, "2024-25", doc="rbi", page=17),
    )
    assert comp.relationship == "UNCERTAIN"
    assert comp.relationship != "CORROBORATES"


def test_forecast_vs_actual_reconciles_before_rule_8b():
    """Actual 1.5 (September 2025) x projection 4 (FY2026/27, forecast) -> RECONCILABLE."""
    comp = _reason_pair(
        _inflation_fact(1.5, "September 2025", doc="imf", page=10),
        _inflation_fact(4.0, "FY2026/27", doc="imf", page=13,
                       qualifiers=["forecast"]),
    )
    assert comp.relationship == "RECONCILABLE"


def test_cluster_g_grouping_produces_exactly_ten_pairs():
    """5 inflation facts in one canonical group -> 10 pairs; singletons add none."""
    facts = [
        _inflation_fact(1.5, "September 2025", doc="imf", page=10),
        _inflation_fact(4.6, None, doc="imf", page=10, subject="India core inflation"),
        _inflation_fact(4.6, "2024-25", doc="rbi", page=17),
        _inflation_fact(3.3, "March 2025", doc="rbi", page=39),
        _inflation_fact(4.0, "FY2026/27", doc="imf", page=13,
                        qualifiers=["forecast"]),
        # Singletons under different canonical predicates: no pairs.
        Fact(
            subject="India private consumption growth",
            predicate="growth rate",
            value=7.2,
            unit="per cent",
            period=None,
            qualifiers=[],
            evidence=Evidence(document_id="imf", page_number=10,
                              text="Private consumption growth 7.2 percent"),
            confidence=0.9,
        ),
        Fact(
            subject="India unemployment",
            predicate="unemployment rate",
            value=5.2,
            unit="per cent",
            period="September",
            qualifiers=[],
            evidence=Evidence(document_id="imf", page_number=10,
                              text="Unemployment 5.2 percent"),
            confidence=0.9,
        ),
    ]
    pairs = generate_candidate_pairs(facts)
    assert len(pairs) == 10
