"""
Tests for scripts/evaluate.py and the docs/evaluation gold sets.

Covers:
1. Gold file integrity (structure, counts, verification flags)
2. Claim-key normalization (floats, whitespace, casefold, as_of distinction)
3. Pair-key order independence
4. Extraction scoring: duplicates -> FP, recall misses -> FN, capture rates
5. Relationship scoring: wrong label, absent violations, correct outcomes
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for p in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import evaluate  # noqa: E402
from evaluate import (  # noqa: E402
    claim_key,
    relationship_key,
    pair_key,
    score_extraction,
    score_relationships,
    _load_gold,
    EXTRACTION_GOLD,
    RELATIONSHIP_GOLD,
)


# ---------------------------------------------------------------------------
# Gold file integrity
# ---------------------------------------------------------------------------

def test_extraction_gold_structure():
    gold = _load_gold(EXTRACTION_GOLD)
    items = gold["items"]
    assert gold["gold_version"] == "week3-phase0"
    assert len(items) == 24

    current = [g for g in items if g["source"] == "current_pipeline"]
    misses = [g for g in items if g["source"] == "recall_miss"]
    assert len(current) == 8
    assert len(misses) == 16

    ids = [g["id"] for g in items]
    assert len(ids) == len(set(ids)), "gold item ids must be unique"

    for g in items:
        assert g["subject"]
        assert g["predicate"]
        assert g["value"] is not None
        assert g["verified"] is True
        assert g["evidence_document"].endswith(".pdf")
        assert g["evidence_pages"]

    audit = gold["pipeline_fact_audit"]
    assert audit["summary"]["rows"] == 13
    assert audit["summary"]["correct_unique"] == 8
    assert audit["summary"]["duplicates"] == 5
    assert audit["summary"]["incorrect"] == 0


def test_relationship_gold_structure():
    gold = _load_gold(RELATIONSHIP_GOLD)
    items = gold["items"]
    assert gold["gold_version"] == "week3-phase0"
    assert len(items) == 19

    label_items = [g for g in items if g["expectation"] == "label"]
    absent_items = [g for g in items if g["expectation"] == "absent"]
    assert len(label_items) == 13
    assert len(absent_items) == 6

    for g in items:
        assert g["source_verified"] is True
        assert g["note"]
        if g["expectation"] == "label":
            assert g["expected"] in evaluate.RELATIONSHIP_CLASSES
        else:
            assert g["expected"] == "ABSENT"

    # Verdicts: rel-004 is the one genuine baseline pair; rel-010..rel-019
    # are the ten source-verified Cluster G pairs added in Week 3 Phase 0.
    genuine = {g["id"] for g in items if g.get("verdict") == "genuine"}
    assert genuine == {"rel-004"} | {f"rel-{i:03d}" for i in range(10, 20)}

    audit = gold["current_pipeline_rows"]
    assert audit["summary"]["rows"] == 9
    assert audit["summary"]["genuine_correct_label"] == 1
    assert audit["summary"]["defect_artifacts"] == 7
    assert audit["summary"]["limitation_artifacts"] == 1


# ---------------------------------------------------------------------------
# Key normalization
# ---------------------------------------------------------------------------

def test_claim_key_normalizes_floats_and_whitespace():
    a = claim_key("delhivery  revenue", "revenue", 74540.82, "INR million", None)
    b = claim_key("DELHIVERY REVENUE", "Revenue", "74540.82", " inr  million ", None)
    assert a == b
    assert a[2] == "74540.82"


def test_claim_key_distinguishes_as_of():
    resigned = claim_key("X", "board status", "resigned", None, "August 24, 2023")
    ceased_same_day = claim_key("X", "board status", "ceased", None, "August 24, 2023")
    ceased_other_date = claim_key("X", "board status", "ceased", None, "September 27, 2023")
    ceased_textual = claim_key("X", "board status", "ceased", None, "conclusion of the 12th AGM")
    assert resigned != ceased_same_day  # value differs
    assert ceased_same_day != ceased_other_date  # as_of differs
    assert ceased_other_date != ceased_textual  # as_of differs
    # Page is not part of the key at all — same claim twice is one key
    assert claim_key("X", "p", "v", None, "d") == claim_key("X", "p", "v", None, "d")


def test_relationship_key_has_four_elements_and_pair_is_order_independent():
    ka = relationship_key("India real GDP growth", "growth rate", "6.5", "per cent")
    kb = relationship_key("India real GDP growth", "growth rate", 6.4, "per cent")
    assert len(ka) == 4
    assert ka != kb
    assert pair_key(ka, kb) == pair_key(kb, ka)
    # Self-pair collapses to a single-element frozenset consistently
    assert pair_key(ka, ka) == frozenset([ka])


def test_float_integer_values_stringify_consistently():
    assert claim_key("s", "p", 127.0, None)[2] == "127"
    assert claim_key("s", "p", "127", None)[2] == "127"
    assert claim_key("s", "p", 6.5, None)[2] == "6.5"


# ---------------------------------------------------------------------------
# Extraction scoring (synthetic)
# ---------------------------------------------------------------------------

def _fact(subject, predicate, value, unit=None, as_of=None, period=None, scope=None):
    return SimpleNamespace(
        subject=subject, predicate=predicate, value=value, unit=unit,
        as_of=as_of, period=period, scope=scope,
    )


def _extraction_gold(items):
    return {"items": items}


def test_score_extraction_counts_duplicates_as_false_positives():
    gold = _extraction_gold([
        {"id": "g1", "source": "current_pipeline", "subject": "S", "predicate": "board status",
         "value": "ceased", "unit": None, "as_of": "July 01, 2024", "period": None, "scope": None},
    ])
    # Three identical rows (Defect A shape): 1 TP + 2 duplicate FP
    facts = [
        _fact("S", "board status", "ceased", as_of="July 01, 2024"),
        _fact("S", "board status", "ceased", as_of="July 01, 2024"),
        _fact("S", "board status", "ceased", as_of="July 01, 2024"),
    ]
    out = score_extraction(facts, gold)
    assert out["tp"] == 1
    assert out["fp"] == 2
    assert out["fp_duplicates"] == 2
    assert out["fp_unknown"] == 0
    assert out["precision"] == round(1 / 3, 4)
    assert out["recall"] == 1.0
    assert out["fn"] == 0


def test_score_extraction_recall_miss_and_unknown_fact():
    gold = _extraction_gold([
        {"id": "g1", "source": "current_pipeline", "subject": "A", "predicate": "p",
         "value": "1", "unit": None, "as_of": None, "period": None, "scope": None},
        {"id": "g2", "source": "recall_miss", "subject": "B", "predicate": "p",
         "value": "2", "unit": None, "as_of": None, "period": None, "scope": None},
    ])
    facts = [
        _fact("A", "p", "1"),
        _fact("Z", "p", "99"),  # not in gold at all
    ]
    out = score_extraction(facts, gold)
    assert out["tp"] == 1
    assert out["fp_unknown"] == 1
    assert out["fn"] == 1  # g2 never matched
    assert out["gold_by_source"]["recall_miss"] == {"total": 1, "matched": 0}
    assert out["gold_by_source"]["current_pipeline"] == {"total": 1, "matched": 1}


def test_score_extraction_capture_rates_only_count_gold_nonnull_fields():
    gold = _extraction_gold([
        {"id": "g1", "source": "current_pipeline", "subject": "rev", "predicate": "revenue",
         "value": "100", "unit": "INR million", "as_of": None,
         "period": "FY24", "scope": "standalone"},   # pipeline will miss both
        {"id": "g2", "source": "current_pipeline", "subject": "gdp", "predicate": "growth rate",
         "value": "6.5", "unit": "per cent", "as_of": None,
         "period": "FY2024/25", "scope": None},       # pipeline matches period
    ])
    facts = [
        _fact("rev", "revenue", "100", "INR million", period=None, scope=None),
        _fact("gdp", "growth rate", "6.5", "per cent", period="FY2024/25", scope=None),
    ]
    out = score_extraction(facts, gold)
    assert out["capture_rates"]["period"] == 0.5  # 1 of 2 required periods captured
    assert out["capture_rates"]["scope"] == 0.0   # gold scope required once, missed
    assert out["f1"] > 0


# ---------------------------------------------------------------------------
# Relationship scoring (synthetic)
# ---------------------------------------------------------------------------

def _rel_row(subj_a, val_a, subj_b, val_b, label, pred="p", unit=None):
    return {
        "fa_subject": subj_a, "fa_predicate": pred, "fa_value": val_a, "fa_unit": unit,
        "fb_subject": subj_b, "fb_predicate": pred, "fb_value": val_b, "fb_unit": unit,
        "relationship": label,
    }


def _rel_gold_item(gid, expectation, expected, a, b, **extra):
    fact_a = {"subject": a[0], "predicate": a[1], "value": a[2], "unit": a[3] if len(a) > 3 else None}
    fact_b = {"subject": b[0], "predicate": b[1], "value": b[2], "unit": b[3] if len(b) > 3 else None}
    return {"id": gid, "expectation": expectation, "expected": expected,
            "fact_a": fact_a, "fact_b": fact_b, **extra}


def test_score_relationships_wrong_label_missed_and_violations():
    gold = {"items": [
        # expects RECONCILABLE but pipeline says CONTRADICTS -> wrong label
        _rel_gold_item("r1", "label", "RECONCILABLE",
                       ("E", "p", "resigned"), ("E", "p", "ceased")),
        # expects RECONCILABLE and pipeline agrees -> correct
        _rel_gold_item("r2", "label", "RECONCILABLE",
                       ("G", "p", "6.5"), ("G", "p", "6.4")),
        # expects RECONCILABLE but pipeline never produces it -> missed
        _rel_gold_item("r3", "label", "RECONCILABLE",
                       ("H", "p", "100"), ("H", "p", "200")),
        # duplicate self-pair must be absent but pipeline produced it -> violation
        _rel_gold_item("r4", "absent", "ABSENT",
                       ("D", "p", "ceased"), ("D", "p", "ceased")),
        # unrelated pair correctly not produced -> correct (absent)
        _rel_gold_item("r5", "absent", "ABSENT",
                       ("X", "p", "1"), ("Y", "p", "2")),
    ]}
    rows = [
        _rel_row("E", "resigned", "E", "ceased", "CONTRADICTS"),
        _rel_row("G", "6.5", "G", "6.4", "RECONCILABLE"),
        _rel_row("D", "ceased", "D", "ceased", "CORROBORATES"),
        # multiplicity: duplicate raw rows collapse to the same unique pair
        _rel_row("D", "ceased", "D", "ceased", "CORROBORATES"),
    ]
    out = score_relationships(rows, gold)
    assert out["raw_relationship_rows"] == 4
    assert out["predicted_unique_pairs"] == 3
    assert out["gold_items"] == 5
    assert out["correct"] == 2          # r2 + r5
    assert out["incorrect_label"] == 1  # r1
    assert out["missed_required"] == 1  # r3
    assert out["absent_violations"] == 1  # r4
    assert out["accuracy"] == round(2 / 5, 4)
    assert out["absent_compliance"] == 0.5
    # Per-class: the one predicted RECONCILABLE satisfies gold -> precision 1.0
    assert out["per_class"]["RECONCILABLE"]["precision"] == 1.0
    assert out["per_class"]["RECONCILABLE"]["recall"] == round(1 / 3, 4)
    # CORROBORATES predicted once but gold never requires it -> precision 0
    assert out["per_class"]["CORROBORATES"]["precision"] == 0.0
    assert out["per_class"]["CORROBORATES"]["recall"] is None


def test_score_relationships_pair_order_does_not_matter():
    gold = {"items": [
        _rel_gold_item("r1", "label", "RECONCILABLE",
                       ("G", "p", "6.5"), ("G", "p", "6.4")),
    ]}
    # Pipeline emits the pair with sides swapped relative to gold
    rows = [_rel_row("G", "6.4", "G", "6.5", "RECONCILABLE")]
    out = score_relationships(rows, gold)
    assert out["correct"] == 1
    assert out["accuracy"] == 1.0
