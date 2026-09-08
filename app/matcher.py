"""
Candidate matching and comparison preparation for the Superjoin Fact Knowledge Layer.
Groups facts by canonical subject and predicate, filters out obviously irrelevant pairs,
and prepares structured comparison inputs with deterministic dimension diffs.
NOTE: Does NOT assign the final relationship (that is Gate 4's responsibility).
"""

import time
from typing import List, Dict, Tuple, Optional
from app.models import (
    Fact,
    Dimensions,
    PreparedComparison,
    CandidateStatistics,
    NumericalComparison,
    PeriodComparison,
)
from app.normalizer import (
    normalize_fact,
    normalize_facts,
    compare_numbers,
    compare_periods,
)


def _cmp_qualifiers(x: List[str], y: List[str]) -> str:
    """Set-based qualifier comparison."""
    if not x and not y:
        return "same"
    if not x or not y:
        return "different"
    xs = {s.strip().lower() for s in x}
    ys = {s.strip().lower() for s in y}
    return "same" if xs == ys else "different"


def diff_dimensions_normalized(
    a: Fact,
    b: Fact,
    num_comp: Optional[NumericalComparison] = None,
    period_comp: Optional[PeriodComparison] = None,
) -> Dimensions:
    """
    Compute inspectable dimension diff between two normalized facts.
    Uses normalized representation, unit equivalence, and rounding metadata.
    """
    # 1. Subject
    subj_a = (a.canonical_subject or a.subject or "").strip().lower()
    subj_b = (b.canonical_subject or b.subject or "").strip().lower()
    subj_diff = "same" if (subj_a and subj_b and subj_a == subj_b) else "different"

    # 2. Predicate
    pred_a = (a.canonical_predicate or a.predicate or "").strip().lower()
    pred_b = (b.canonical_predicate or b.predicate or "").strip().lower()
    pred_diff = "same" if (pred_a and pred_b and pred_a == pred_b) else "different"

    # 3. Value
    if num_comp and num_comp.is_numeric:
        val_diff = "same" if (num_comp.is_exact or num_comp.is_close_rounding) else "different"
    elif isinstance(a.value, str) and isinstance(b.value, str):
        val_diff = "same" if a.value.strip().lower() == b.value.strip().lower() else "different"
    else:
        # One numeric and one categorical
        val_diff = "different"

    # 4. Unit
    unit_a = (a.normalized_unit or a.unit or "").strip().lower() if (a.normalized_unit or a.unit) else None
    unit_b = (b.normalized_unit or b.unit or "").strip().lower() if (b.normalized_unit or b.unit) else None
    if unit_a is None and unit_b is None:
        unit_diff = "same"
    elif unit_a is None or unit_b is None:
        unit_diff = "unknown"
    else:
        unit_diff = "same" if unit_a == unit_b else "different"

    # 5. Period
    if period_comp and period_comp.period_a_norm and period_comp.period_b_norm:
        period_diff = "same" if period_comp.is_same_period else "different"
    else:
        p_a = a.normalized_period or a.period
        p_b = b.normalized_period or b.period
        if not p_a and not p_b:
            period_diff = "unknown"
        elif not p_a or not p_b:
            period_diff = "unknown"
        else:
            period_diff = "same" if str(p_a).strip().lower() == str(p_b).strip().lower() else "different"

    # 6. Scope
    scope_a = a.scope.strip().lower() if a.scope else None
    scope_b = b.scope.strip().lower() if b.scope else None
    if scope_a is None and scope_b is None:
        scope_diff = "same"
    elif scope_a is None or scope_b is None:
        scope_diff = "unknown"
    else:
        scope_diff = "same" if scope_a == scope_b else "different"

    # 7. Qualifiers
    qual_diff = _cmp_qualifiers(a.qualifiers, b.qualifiers)

    return Dimensions(
        subject=subj_diff,
        predicate=pred_diff,
        value=val_diff,
        unit=unit_diff,
        period=period_diff,
        scope=scope_diff,
        qualifiers=qual_diff,
    )


def group_candidate_facts(facts: List[Fact]) -> Dict[Tuple[str, str], List[Fact]]:
    """
    Group facts primarily by canonical_subject and canonical_predicate.
    """
    groups: Dict[Tuple[str, str], List[Fact]] = {}
    for f in facts:
        key = (
            (f.canonical_subject or f.subject).strip().lower(),
            (f.canonical_predicate or f.predicate).strip().lower(),
        )
        if key not in groups:
            groups[key] = []
        groups[key].append(f)
    return groups


def generate_candidate_pairs(
    facts: List[Fact],
    filter_irrelevant: bool = True,
) -> List[PreparedComparison]:
    """
    Generate comparison-ready candidate pairs from a list of facts.
    - Normalizes facts
    - Groups by (canonical_subject, canonical_predicate)
    - Filters out obviously irrelevant pairs
    - Prepares dimension differences and numerical/period metadata
    """
    normalized_facts = [
        f if f.canonical_subject is not None else normalize_fact(f)
        for f in facts
    ]

    groups = group_candidate_facts(normalized_facts)
    candidate_pairs: List[PreparedComparison] = []

    for _, group_facts in groups.items():
        if len(group_facts) < 2:
            continue

        for i in range(len(group_facts)):
            for j in range(i + 1, len(group_facts)):
                fa = group_facts[i]
                fb = group_facts[j]

                if filter_irrelevant:
                    # 1. Skip exact self-comparisons
                    if fa.id == fb.id:
                        continue

                    # 2. Skip exact duplicate evidence (same document, same page, same text)
                    if (
                        fa.evidence.document_id == fb.evidence.document_id
                        and fa.evidence.page_number == fb.evidence.page_number
                        and fa.evidence.text.strip() == fb.evidence.text.strip()
                        and fa.value == fb.value
                    ):
                        continue

                    # 3. Skip comparing numeric against categorical facts under same group
                    is_fa_num = isinstance(fa.normalized_value, (int, float))
                    is_fb_num = isinstance(fb.normalized_value, (int, float))
                    if is_fa_num != is_fb_num:
                        continue

                    # 4. Skip comparing fundamentally incompatible units (e.g. percent vs INR million)
                    if is_fa_num and is_fb_num:
                        if fa.normalized_unit and fb.normalized_unit and fa.normalized_unit != fb.normalized_unit:
                            continue

                # Prepare comparability metadata
                num_comp = compare_numbers(
                    fa.normalized_value if fa.normalized_value is not None else fa.value,
                    fa.normalized_unit if fa.normalized_unit is not None else fa.unit,
                    fb.normalized_value if fb.normalized_value is not None else fb.value,
                    fb.normalized_unit if fb.normalized_unit is not None else fb.unit,
                )

                period_comp = compare_periods(
                    fa.normalized_period, fa.period_type,
                    fb.normalized_period, fb.period_type,
                )

                dims = diff_dimensions_normalized(fa, fb, num_comp, period_comp)

                candidate_pairs.append(PreparedComparison(
                    fact_a=fa,
                    fact_b=fb,
                    dimensions=dims,
                    numerical_comparison=num_comp,
                    period_comparison=period_comp,
                ))

    return candidate_pairs


def compute_candidate_statistics(
    facts: List[Fact],
    candidate_pairs: List[PreparedComparison],
    elapsed_ms: float = 0.0,
) -> CandidateStatistics:
    """
    Compute inspectable statistics and reduction ratio for candidate matching.
    """
    total_facts = len(facts)
    total_naive_pairs = (total_facts * (total_facts - 1)) // 2 if total_facts > 1 else 0
    groups = group_candidate_facts(facts)
    cand_pairs_count = len(candidate_pairs)
    reduction = (
        round(1.0 - (cand_pairs_count / total_naive_pairs), 4)
        if total_naive_pairs > 0
        else 0.0
    )

    return CandidateStatistics(
        total_facts=total_facts,
        total_naive_pairs=total_naive_pairs,
        candidate_groups_count=len(groups),
        candidate_pairs_count=cand_pairs_count,
        reduction_ratio=reduction,
        processing_time_ms=elapsed_ms,
    )
