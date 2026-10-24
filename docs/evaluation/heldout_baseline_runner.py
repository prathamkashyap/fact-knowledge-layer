"""Run the frozen offline FKL pipeline once on each authorized held-out PDF.

This runner is experiment support, not production extractor code. It deliberately
lists only the two active PDF paths and refuses unexpected inputs or artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

import fitz

from app import pipeline
from app.database import Database
from app.pdf_parser import CANDIDATE_SCORE_THRESHOLD, clear_document_cache


BASELINE_SHA = "d9c68bd424883a09107b7507e348107e9f4fbc41"
GOLD_SHA = "979563f9af7bb054fd24c856010998a9be989bdd"
PROTOCOL_COMMIT = "914eb8ca36f6e95ab331170b1d9e78c80cfbeba4"
RUN_DIR = Path("/private/tmp/fkl-heldout-baseline-phase2-run-20261014T1128Z")
OUTPUT_PATH = Path("docs/evaluation/heldout_baseline_predictions.json")

INPUTS = (
    {
        "document_id": "rr-holdings-ar-2025",
        "path": Path("/Users/prathamkashyap/Documents/fkl-heldout-active/rolls-royce-2025-annual-report.pdf"),
        "bytes": 7_077_708,
        "sha256": "c9741d73dba1f52ff4fc48380bf602fb9e422c829a1b47a3d4a5462bdca6d739",
        "pages": 220,
    },
    {
        "document_id": "imf-uk-article-iv-2026",
        "path": Path("/Users/prathamkashyap/Documents/fkl-heldout-active/IMF-United-Kingdom,-2026-Article-IV.pdf"),
        "bytes": 2_832_305,
        "sha256": "5097ec0fe9af420635d75f490880d501f3571235a17fe2c79718d0a7bc300657",
        "pages": 102,
    },
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    if Path.cwd().resolve() != Path(__file__).resolve().parents[2]:
        raise RuntimeError("Run this script from the isolated worktree root")
    if RUN_DIR.exists():
        raise FileExistsError(f"Refusing to overwrite run directory: {RUN_DIR}")
    if OUTPUT_PATH.exists():
        raise FileExistsError(f"Refusing to overwrite prediction artifact: {OUTPUT_PATH}")
    if not Path(".git").exists():
        raise RuntimeError("Expected an isolated Git worktree")

    RUN_DIR.mkdir(parents=True)
    run_record: dict[str, Any] = {
        "baseline_commit": BASELINE_SHA,
        "gold_commit": GOLD_SHA,
        "protocol_commit": PROTOCOL_COMMIT,
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "entry_point": "app.pipeline.process_document_pipeline",
            "command": f"{sys.executable} docs/evaluation/heldout_baseline_runner.py",
            "provider": None,
            "reasoning_mode": "heuristic",
            "candidate_only": True,
            "candidate_score_threshold": CANDIDATE_SCORE_THRESHOLD,
            "database_per_document": "isolated SQLite under RUN_DIR",
            "pdf_parse_cache_cleared_before_each_document": True,
        },
        "documents": [],
    }
    write_json(RUN_DIR / "run-start.json", run_record)

    for source in INPUTS:
        pdf_path: Path = source["path"]
        if not pdf_path.is_file():
            raise FileNotFoundError(f"Authorized active input is missing: {pdf_path}")
        actual_bytes = pdf_path.stat().st_size
        actual_sha = sha256(pdf_path)
        if actual_bytes != source["bytes"] or actual_sha != source["sha256"]:
            raise RuntimeError(f"Input provenance mismatch for {pdf_path}")
        with fitz.open(pdf_path) as pdf:
            actual_pages = pdf.page_count
        if actual_pages != source["pages"]:
            raise RuntimeError(f"Input page count mismatch for {pdf_path}: {actual_pages}")

        raw_facts: list[dict[str, Any]] = []
        original_extractor = pipeline.extract_facts_from_pages

        def capture_raw(provider, pages, candidate_only=True):
            facts = original_extractor(provider, pages, candidate_only=candidate_only)
            raw_facts.extend(fact.model_dump(mode="json") for fact in facts)
            return facts

        pipeline.extract_facts_from_pages = capture_raw
        database_path = RUN_DIR / f"{source['document_id']}.sqlite3"
        db = Database(str(database_path))
        try:
            clear_document_cache()
            summary = pipeline.process_document_pipeline(
                str(pdf_path), provider=None, db=db, reasoning_mode="heuristic"
            )
            final_facts = [fact.model_dump(mode="json") for fact in db.get_facts(
                document_id=summary["document_id"], limit=2000
            )]
        except Exception as error:
            error_record = {
                "document_id": source["document_id"],
                "input": str(pdf_path),
                "input_sha256": actual_sha,
                "error_type": type(error).__name__,
                "error": str(error),
                "raw_candidate_count_captured": len(raw_facts),
                "raw_candidates": raw_facts,
            }
            raw_path = RUN_DIR / f"{source['document_id']}-failure-raw.json"
            write_json(raw_path, error_record)
            write_json(RUN_DIR / "failure.json", error_record)
            raise
        finally:
            db.conn.close()
            pipeline.extract_facts_from_pages = original_extractor

        raw_path = RUN_DIR / f"{source['document_id']}-raw-facts.json"
        final_path = RUN_DIR / f"{source['document_id']}-final-facts.json"
        write_json(raw_path, raw_facts)
        write_json(final_path, final_facts)
        run_record["documents"].append({
            "document_id": source["document_id"],
            "input_filename": pdf_path.name,
            "input_path": str(pdf_path),
            "input_bytes": actual_bytes,
            "input_sha256": actual_sha,
            "page_count": actual_pages,
            "pipeline_summary": summary,
            "raw_pre_normalization_candidate_count": len(raw_facts),
            "final_validated_deduplicated_fact_count": len(final_facts),
            "raw_output_path": str(raw_path),
            "raw_output_sha256": sha256(raw_path),
            "final_output_path": str(final_path),
            "final_output_sha256": sha256(final_path),
            "isolated_database_path": str(database_path),
            "isolated_database_sha256": sha256(database_path),
            "raw_candidates": raw_facts,
            "final_facts": final_facts,
        })
        write_json(RUN_DIR / "run-progress.json", run_record)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT_PATH, run_record)
    run_record["predictions_artifact_path"] = str(OUTPUT_PATH)
    run_record["predictions_artifact_sha256"] = sha256(OUTPUT_PATH)
    write_json(RUN_DIR / "run-manifest.json", run_record)
    print(json.dumps({
        "run_dir": str(RUN_DIR),
        "predictions_path": str(OUTPUT_PATH),
        "predictions_sha256": sha256(OUTPUT_PATH),
        "documents": [
            {
                "document_id": item["document_id"],
                "raw_candidates": item["raw_pre_normalization_candidate_count"],
                "final_facts": item["final_validated_deduplicated_fact_count"],
                "pipeline_summary": item["pipeline_summary"],
            }
            for item in run_record["documents"]
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
