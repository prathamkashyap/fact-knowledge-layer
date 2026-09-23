"""
Evaluation harness for the Fact Knowledge Layer starter corpus.

Scores the extraction pipeline and relationship reasoner against the
hand-verified gold sets in docs/evaluation/:

  - extraction_gold.json    (precision / recall / F1 + capture rates)
  - relationship_gold.json  (accuracy + per-class precision / recall)

Usage:
  python scripts/evaluate.py                      # fresh pipeline run (ground truth for CI)
  python scripts/evaluate.py --db data/demo.db    # score an existing database
  python scripts/evaluate.py --label pre-fix      # tag the results file

Writes docs/evaluation/results.json and prints a plain results table.
Exit codes: 0 on successful evaluation, 1 on setup/gold errors.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for p in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.database import Database  # noqa: E402
from app.pipeline import process_document_pipeline  # noqa: E402

GOLD_DIR = REPO_ROOT / "docs" / "evaluation"
EXTRACTION_GOLD = GOLD_DIR / "extraction_gold.json"
RELATIONSHIP_GOLD = GOLD_DIR / "relationship_gold.json"
RESULTS_PATH = GOLD_DIR / "results.json"

RELATIONSHIP_CLASSES = ["CONTRADICTS", "CORROBORATES", "RECONCILABLE", "UNCERTAIN"]


# ---------------------------------------------------------------------------
# Normalization / claim keys
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def _norm(text: Any) -> str:
    """Whitespace-collapse, strip, casefold — for free-text claim fields."""
    if text is None:
        return ""
    return _WS.sub(" ", str(text)).strip().casefold()


def _val(value: Any) -> str:
    """Stringify a claim value robustly (floats, ints, strings, None)."""
    if value is None:
        return ""
    if isinstance(value, float):
        text = repr(value)
        if text.endswith(".0"):
            text = text[:-2]
        return text
    return _norm(value)


def claim_key(
    subject: Any, predicate: Any, value: Any, unit: Any, as_of: Any = None
) -> Tuple[str, str, str, str, str]:
    """Core claim identity used for matching extracted facts.

    Includes as_of so status claims that differ only by effective date
    (e.g. two different cessation sentences for the same director) stay
    distinct, while page/evidence differences do NOT distinguish claims
    (that is exactly what Defect A duplicates look like).

    Relationship pair keys use the 4-field form via relationship_key().
    """
    return (_norm(subject), _norm(predicate), _val(value), _norm(unit), _norm(as_of))


def relationship_key(subject: Any, predicate: Any, value: Any, unit: Any) -> Tuple[str, str, str, str]:
    """Claim identity for relationship pair matching (as_of not selected by
    the relationships query; all gold relationship claims are distinct on
    these four fields)."""
    return (_norm(subject), _norm(predicate), _val(value), _norm(unit))


def pair_key(claim_a: Tuple[str, str, str, str], claim_b: Tuple[str, str, str, str]):
    """Order-independent key for a relationship between two claims."""
    return frozenset((claim_a, claim_b))


def _load_gold(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Gold file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Prediction collection
# ---------------------------------------------------------------------------

def run_fresh_pipeline(db_path: Optional[Path] = None) -> Tuple[Database, Dict[str, Any]]:
    """Run the offline heuristic pipeline over all 6 starter PDFs into a DB."""
    from load_starter_demo import discover_pdfs, reset_demo_db

    pdfs = discover_pdfs()
    temp_dir: Optional[tempfile.TemporaryDirectory] = None
    if db_path is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="fkl_eval_")
        db_path = Path(temp_dir.name) / "eval.db"
    reset_demo_db(db_path)

    db = Database(str(db_path))
    # Stash the temp dir on the db object so callers can keep it alive.
    db._eval_temp_dir = temp_dir  # type: ignore[attr-defined]

    t0 = time.time()
    per_doc = []
    for name, pdf in pdfs:
        res = process_document_pipeline(
            pdf_path=str(pdf),
            provider=None,
            db=db,
            reasoning_mode="heuristic",
        )
        per_doc.append({"dataset": name, "file": pdf.name, **{k: res[k] for k in res if isinstance(res[k], (int, float, str))}})
    meta = {
        "source": "fresh_run",
        "documents": len(pdfs),
        "wall_seconds": round(time.time() - t0, 2),
        "per_document": per_doc,
    }
    return db, meta


def open_db(db_path: Path) -> Tuple[Database, Dict[str, Any]]:
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")
    return Database(str(db_path)), {"source": f"db:{db_path}"}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_extraction(facts: List[Any], gold: Dict[str, Any]) -> Dict[str, Any]:
    """Precision / recall / F1 of fact extraction against the gold set.

    Duplicate pipeline rows that map to an already-matched gold item count
    as false positives — this is what makes the metric punish Defect A.
    """
    gold_items = gold["items"]
    # claim key -> FIFO queue of gold item ids (file order kept for determinism)
    gold_queues: Dict[Tuple[str, str, str, str, str], List[str]] = {}
    gold_by_id = {g["id"]: g for g in gold_items}
    for g in gold_items:
        gold_queues.setdefault(
            claim_key(g["subject"], g["predicate"], g["value"], g.get("unit"), g.get("as_of")), []
        ).append(g["id"])

    matched_gold_ids = set()
    tp = fp = 0
    duplicate_fp = 0
    unknown_fp = 0
    # as_of is part of the match key, so only period/scope remain as
    # secondary capture signals for matched facts.
    capture = {"period": [0, 0], "scope": [0, 0]}
    matched_pairs: List[Tuple[Any, Dict[str, Any]]] = []

    for f in facts:
        key = claim_key(f.subject, f.predicate, f.value, f.unit, getattr(f, "as_of", None))
        queue = gold_queues.get(key)
        if queue:
            gold_id = queue.pop(0)
            tp += 1
            matched_gold_ids.add(gold_id)
            g = gold_by_id[gold_id]
            matched_pairs.append((f, g))
            for field in capture:
                if g.get(field) not in (None, ""):
                    capture[field][1] += 1
                    actual = getattr(f, field, None)
                    if _norm(actual) == _norm(g[field]):
                        capture[field][0] += 1
        elif key in gold_queues:
            # key exists in gold but every item already consumed -> duplicate
            fp += 1
            duplicate_fp += 1
        else:
            fp += 1
            unknown_fp += 1

    total_predicted = tp + fp
    gold_total = len(gold_items)
    gold_matched = len(matched_gold_ids)
    fn = gold_total - gold_matched

    precision = tp / total_predicted if total_predicted else 0.0
    recall = gold_matched / gold_total if gold_total else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    capture_rates = {}
    for field, (hits, required) in capture.items():
        capture_rates[field] = round(hits / required, 4) if required else None

    source_split = {"current_pipeline": 0, "recall_miss": 0}
    source_matched = {"current_pipeline": 0, "recall_miss": 0}
    for g in gold_items:
        src = g.get("source", "current_pipeline")
        source_split[src] = source_split.get(src, 0) + 1
        if g["id"] in matched_gold_ids:
            source_matched[src] = source_matched.get(src, 0) + 1

    return {
        "predicted_facts": total_predicted,
        "gold_facts": gold_total,
        "tp": tp,
        "fp": fp,
        "fp_duplicates": duplicate_fp,
        "fp_unknown": unknown_fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "capture_rates": capture_rates,
        "gold_by_source": {
            src: {"total": source_split[src], "matched": source_matched.get(src, 0)}
            for src in source_split
        },
    }


def score_relationships(rel_rows: List[Dict[str, Any]], gold: Dict[str, Any]) -> Dict[str, Any]:
    """Accuracy and per-class precision/recall against the relationship gold.

    Predicted rows are collapsed to unique claim-level pair keys first —
    multiplicity is already punished by extraction precision.
    """
    gold_items = gold["items"]

    # Collapse predicted rows to unique pair keys.
    predicted: Dict[frozenset, str] = {}
    raw_rows = len(rel_rows)
    for r in rel_rows:
        ka = relationship_key(r.get("fa_subject"), r.get("fa_predicate"), r.get("fa_value"), r.get("fa_unit"))
        kb = relationship_key(r.get("fb_subject"), r.get("fb_predicate"), r.get("fb_value"), r.get("fb_unit"))
        pk = pair_key(ka, kb)
        label = (r.get("relationship") or "").upper()
        if pk not in predicted:
            predicted[pk] = label

    def gold_pair(g: Dict[str, Any]) -> frozenset:
        ka = relationship_key(g["fact_a"]["subject"], g["fact_a"]["predicate"], g["fact_a"]["value"], g["fact_a"].get("unit"))
        kb = relationship_key(g["fact_b"]["subject"], g["fact_b"]["predicate"], g["fact_b"]["value"], g["fact_b"].get("unit"))
        return pair_key(ka, kb)

    correct = incorrect = missed = violations = 0
    details = []
    required_by_class: Dict[str, List[str]] = {c: [] for c in RELATIONSHIP_CLASSES}
    required_hit: Dict[str, int] = {c: 0 for c in RELATIONSHIP_CLASSES}
    absent_total = absent_ok = 0
    predicted_in_gold = 0

    gold_by_pair = {gold_pair(g): g for g in gold_items}

    for g in gold_items:
        pk = gold_pair(g)
        produced = pk in predicted
        predicted_label = predicted.get(pk)
        if g["expectation"] == "absent":
            absent_total += 1
            if produced:
                violations += 1
                outcome = "VIOLATION"
            else:
                absent_ok += 1
                correct += 1
                outcome = "ok(absent)"
        else:
            expected = g["expected"]
            required_by_class.setdefault(expected, []).append(g["id"])
            if not produced:
                missed += 1
                outcome = "missed"
            elif predicted_label == expected:
                correct += 1
                required_hit[expected] = required_hit.get(expected, 0) + 1
                predicted_in_gold += 1
                outcome = "ok"
            else:
                incorrect += 1
                outcome = f"wrong({predicted_label})"
        details.append({"id": g["id"], "expectation": g["expectation"],
                        "expected": g.get("expected"), "produced": produced,
                        "predicted_label": predicted_label, "outcome": outcome})

    gold_total = len(gold_items)
    accuracy = correct / gold_total if gold_total else 0.0

    # Per-class precision: of predicted pairs labeled C, how many satisfy a
    # gold item that expects C. Per-class recall: of gold items expecting C,
    # how many were produced with label C.
    per_class: Dict[str, Dict[str, Optional[float]]] = {}
    for cls in RELATIONSHIP_CLASSES:
        pred_cls = [pk for pk, lbl in predicted.items() if lbl == cls]
        pred_tp = sum(
            1 for pk in pred_cls
            if pk in gold_by_pair
            and gold_by_pair[pk]["expectation"] == "label"
            and gold_by_pair[pk]["expected"] == cls
        )
        precision = (pred_tp / len(pred_cls)) if pred_cls else None
        req = required_by_class.get(cls, [])
        req_hits = required_hit.get(cls, 0)
        recall = (req_hits / len(req)) if req else None
        per_class[cls] = {
            "predicted": len(pred_cls),
            "gold_required": len(req) if req else 0,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
        }

    return {
        "raw_relationship_rows": raw_rows,
        "predicted_unique_pairs": len(predicted),
        "gold_items": gold_total,
        "correct": correct,
        "incorrect_label": incorrect,
        "missed_required": missed,
        "absent_violations": violations,
        "accuracy": round(accuracy, 4),
        "absent_compliance": round(absent_ok / absent_total, 4) if absent_total else None,
        "per_class": per_class,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def evaluate(db_path: Optional[Path] = None, label: str = "unlabeled") -> Dict[str, Any]:
    extraction_gold = _load_gold(EXTRACTION_GOLD)
    relationship_gold = _load_gold(RELATIONSHIP_GOLD)

    if db_path is None:
        db, meta = run_fresh_pipeline()
    else:
        db, meta = open_db(db_path)

    try:
        facts = db.get_facts(limit=5000)
        rel_rows = db.get_relationships(limit=5000)
        stats = db.get_stats()
    finally:
        db.close()
        temp = getattr(db, "_eval_temp_dir", None)
        if temp is not None:
            temp.cleanup()

    extraction = score_extraction(facts, extraction_gold)
    relationships = score_relationships(rel_rows, relationship_gold)

    return {
        "label": label,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **meta,
        "raw_counts": {
            "documents": stats.get("total_documents"),
            "pages": stats.get("total_pages"),
            "facts": stats.get("total_facts"),
            "relationships": stats.get("total_relationships"),
            "relationship_breakdown": stats.get("relationship_breakdown"),
        },
        "extraction": extraction,
        "relationships": relationships,
    }


def format_table(result: Dict[str, Any]) -> str:
    ex = result["extraction"]
    rl = result["relationships"]
    lines = []
    lines.append("=" * 64)
    lines.append(f"EVALUATION RESULTS — {result['label']} ({result['timestamp']})")
    lines.append(f"source: {result.get('source', '?')}   wall: {result.get('wall_seconds', 'n/a')}s")
    lines.append("=" * 64)
    lines.append("RAW COUNTS")
    rc = result["raw_counts"]
    lines.append(
        f"  documents={rc['documents']}  pages={rc['pages']}  "
        f"facts={rc['facts']}  relationships={rc['relationships']}  "
        f"{rc['relationship_breakdown']}"
    )
    lines.append("")
    lines.append("EXTRACTION  (gold = hand-verified claims from the 6-PDF corpus)")
    lines.append(
        f"  predicted={ex['predicted_facts']}  gold={ex['gold_facts']}  "
        f"tp={ex['tp']}  fp={ex['fp']} (dup={ex['fp_duplicates']}, unknown={ex['fp_unknown']})  fn={ex['fn']}"
    )
    lines.append(
        f"  precision={ex['precision']:.4f}  recall={ex['recall']:.4f}  f1={ex['f1']:.4f}"
    )
    lines.append(
        f"  capture: period={ex['capture_rates']['period']}  "
        f"scope={ex['capture_rates']['scope']}"
    )
    for src, row in ex["gold_by_source"].items():
        lines.append(f"  gold[{src}]: {row['matched']}/{row['total']} matched")
    lines.append("")
    lines.append("RELATIONSHIPS  (gold = source-verified pair labels)")
    lines.append(
        f"  raw_rows={rl['raw_relationship_rows']}  unique_pairs={rl['predicted_unique_pairs']}  "
        f"gold_items={rl['gold_items']}"
    )
    lines.append(
        f"  accuracy={rl['accuracy']:.4f}  correct={rl['correct']}  "
        f"wrong_label={rl['incorrect_label']}  missed={rl['missed_required']}  "
        f"absent_violations={rl['absent_violations']}  "
        f"absent_compliance={rl['absent_compliance']}"
    )
    lines.append("  per-class:")
    for cls, row in rl["per_class"].items():
        prec = f"{row['precision']:.4f}" if row["precision"] is not None else "  n/a "
        rec = f"{row['recall']:.4f}" if row["recall"] is not None else "  n/a "
        lines.append(
            f"    {cls:<14} predicted={row['predicted']}  required={row['gold_required']}  "
            f"precision={prec}  recall={rec}"
        )
    lines.append("=" * 64)
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate FKL extraction + relationships against gold sets.")
    parser.add_argument("--db", type=Path, default=None, help="Score an existing SQLite DB instead of a fresh run.")
    parser.add_argument("--label", type=str, default="unlabeled", help="Tag written into results.json.")
    parser.add_argument("--no-write", action="store_true", help="Print results without updating results.json.")
    args = parser.parse_args(argv)

    try:
        result = evaluate(db_path=args.db, label=args.label)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(format_table(result))

    if not args.no_write:
        # results.json stores the latest run plus a history of labeled runs
        # so before/after comparisons stay diffable in git.
        history: List[Dict[str, Any]] = []
        if RESULTS_PATH.is_file():
            try:
                existing = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
                history = existing.get("history", [])
                if "latest" in existing and existing["latest"].get("label"):
                    history.append(existing["latest"])
            except (json.JSONDecodeError, KeyError):
                history = []
        history = history[-19:]  # keep the file bounded
        payload = {"latest": result, "history": history}
        RESULTS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {RESULTS_PATH.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
