#!/usr/bin/env python3
"""
Load the bundled starter datasets into a dedicated demo knowledge base.

This is the one-command way to populate a demo database with the two shipped
starter datasets (India macroeconomy + Delhivery corporate) so the Fact
Explorer UI can immediately show real facts and relationships:

    python scripts/load_starter_demo.py

It always resets a dedicated demo database at ``data/demo.db`` (never the
default application DB) and runs every starter PDF through the same
``process_document_pipeline`` function the FastAPI ``/upload`` endpoint uses,
so the demo reflects exactly what the live pipeline produces. It prints the
real summary counts it produced (never hardcoded) and exits non-zero on
failure.

Point the server at the resulting demo database with:

    FACT_LAYER_DB_PATH=data/demo.db uvicorn app.main:app --host 127.0.0.1 --port 8000

Running the server without that variable still serves the default (empty on a
fresh clone) knowledge base at ``data/fact_layer.db``.
"""

import sys
import time
from pathlib import Path

# Make the repository root importable regardless of the current directory,
# so `python scripts/load_starter_demo.py` works from the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.database import Database  # noqa: E402
from app.pipeline import process_document_pipeline  # noqa: E402

# Starter datasets shipped in the repository.
DATASET_DIRS = [
    ("india-macroeconomy", REPO_ROOT / "starter-datasets" / "india-macroeconomy"),
    ("delhivery", REPO_ROOT / "starter-datasets" / "delhivery"),
]

# Dedicated demo DB — deliberately NOT the default application DB path.
DEMO_DB_PATH = REPO_ROOT / "data" / "demo.db"


def discover_pdfs():
    """Return a sorted ``[(dataset_name, pdf_path), ...]`` for all starter PDFs.

    Raises ``FileNotFoundError`` if a dataset directory is missing or contains
    no PDFs, so a partially-shipped repo fails the load instead of silently
    producing an empty demo.
    """
    found = []
    for name, dirpath in DATASET_DIRS:
        if not dirpath.is_dir():
            raise FileNotFoundError(f"Starter dataset directory not found: {dirpath}")
        pdfs = sorted(Path(dirpath).glob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError(f"No PDFs found in starter dataset: {dirpath}")
        for pdf in pdfs:
            found.append((name, pdf))
    return found


def reset_demo_db(db_path: Path):
    """Remove any existing demo DB (and SQLite sidecar files) for a clean load."""
    for suffix in ("", "-wal", "-journal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()


def main() -> int:
    print("Fact Knowledge Layer - starter demo loader", flush=True)
    print("=" * 52, flush=True)

    try:
        pdfs = discover_pdfs()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Discovered {len(pdfs)} starter PDF(s) across {len(DATASET_DIRS)} dataset(s).")
    for name, pdf in pdfs:
        print(f"  - [{name}] {pdf.name}")

    reset_demo_db(DEMO_DB_PATH)
    db = Database(str(DEMO_DB_PATH))
    print(f"Reset dedicated demo DB at {DEMO_DB_PATH}", flush=True)

    t0 = time.time()
    try:
        for name, pdf in pdfs:
            res = process_document_pipeline(
                pdf_path=str(pdf),
                provider=None,
                db=db,
                reasoning_mode="heuristic",
            )
            print(
                f"  ingested [{name}] {pdf.name}: "
                f"{res['facts_count']} facts, "
                f"{res['comparisons_count']} new comparisons, "
                f"{res['candidate_pages_count']}/{res['total_pages']} candidate pages, "
                f"{res['processing_time_ms']:.0f} ms",
                flush=True,
            )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"\nERROR: demo load failed: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    total_seconds = time.time() - t0

    # Reopen read-only for the summary so counts come from the persisted demo DB.
    db = Database(str(DEMO_DB_PATH))
    try:
        stats = db.get_stats()
    finally:
        db.close()

    breakdown = ", ".join(
        f"{rel}={count}" for rel, count in sorted(stats["relationship_breakdown"].items())
    )

    print("\n" + "=" * 52)
    print("Demo load complete.")
    print(f"  Documents:        {stats['total_documents']}")
    print(f"  Pages ingested:   {stats['total_pages']}")
    print(f"  Grounded facts:   {stats['total_facts']}")
    print(f"  Relationships:    {stats['total_relationships']}")
    print(f"  Relationship mix: {breakdown or '(none)'}")
    print(f"  Wall time:        {total_seconds:.2f} s")
    print("=" * 52)
    print(
        "Start the server against this demo DB with:\n"
        "  FACT_LAYER_DB_PATH=data/demo.db uvicorn app.main:app --host 127.0.0.1 --port 8000"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
