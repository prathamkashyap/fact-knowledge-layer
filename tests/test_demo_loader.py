"""
Regression tests for scripts/load_starter_demo.py.

Covers:
1. Path resolution (REPO_ROOT, DEMO_DB_PATH, DATASET_DIRS)
2. Starter dataset discovery (discovers all 6 bundled PDFs across 2 datasets)
3. Clean failure on invalid dataset paths (FileNotFoundError, main() exits 1)
4. Clean failure on empty dataset directories
5. DB reset helper cleans SQLite sidecars
6. End-to-end smoke check (full load populates documents > 0, facts > 0, relationships > 0)
"""

import sys
from pathlib import Path
import pytest

# Ensure repo root and scripts/ are on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import load_starter_demo  # noqa: E402
from app.database import Database  # noqa: E402


def test_demo_paths_resolve():
    """Verify repository root and dataset paths resolve to real directories."""
    assert load_starter_demo.REPO_ROOT.is_dir()
    assert (load_starter_demo.REPO_ROOT / "app").is_dir()
    assert (load_starter_demo.REPO_ROOT / "starter-datasets").is_dir()
    assert (load_starter_demo.REPO_ROOT / "scripts").is_dir()

    # Default demo DB path must be under data/ and named demo.db
    assert load_starter_demo.DEMO_DB_PATH.name == "demo.db"
    assert load_starter_demo.DEMO_DB_PATH.parent.name == "data"

    # Both dataset directories must exist on disk
    for name, path in load_starter_demo.DATASET_DIRS:
        assert path.is_dir(), f"Dataset directory missing: {path}"


def test_starter_datasets_discovered():
    """Verify all 6 starter PDFs across both datasets are discovered."""
    pdfs = load_starter_demo.discover_pdfs()
    assert len(pdfs) == 6

    datasets = {name for name, _ in pdfs}
    assert datasets == {"india-macroeconomy", "delhivery"}

    filenames = [path.name for _, path in pdfs]
    assert "01-india-economic-survey-2024-25-excerpt.pdf" in filenames
    assert "02-rbi-annual-report-2024-25-excerpt.pdf" in filenames
    assert "03-imf-india-2025-article-iv-excerpt.pdf" in filenames
    assert "01-delhivery-prospectus-2022-excerpt.pdf" in filenames
    assert "02-delhivery-annual-report-fy24-excerpt.pdf" in filenames
    assert "03-delhivery-q4-fy24-earnings-presentation.pdf" in filenames

    # Every discovered file must be a non-empty readable PDF
    for _, path in pdfs:
        assert path.is_file()
        assert path.stat().st_size > 50_000


def test_invalid_dataset_path_fails_cleanly(monkeypatch):
    """Verify discover_pdfs raises FileNotFoundError and main() exits 1 when path is invalid."""
    bad_dirs = [("nonexistent", Path("/tmp/definitely_does_not_exist_fkl_test"))]
    monkeypatch.setattr(load_starter_demo, "DATASET_DIRS", bad_dirs)

    with pytest.raises(FileNotFoundError, match="Starter dataset directory not found"):
        load_starter_demo.discover_pdfs()

    exit_code = load_starter_demo.main()
    assert exit_code == 1


def test_empty_dataset_directory_fails_cleanly(monkeypatch, tmp_path):
    """Verify discover_pdfs raises FileNotFoundError when dataset directory has no PDFs."""
    empty_dir = tmp_path / "empty_dataset"
    empty_dir.mkdir()

    monkeypatch.setattr(load_starter_demo, "DATASET_DIRS", [("empty", empty_dir)])

    with pytest.raises(FileNotFoundError, match="No PDFs found in starter dataset"):
        load_starter_demo.discover_pdfs()

    exit_code = load_starter_demo.main()
    assert exit_code == 1


def test_reset_demo_db_cleans_sidecars(tmp_path):
    """Verify reset_demo_db cleans main file and SQLite sidecars (-wal, -journal, -shm)."""
    base = tmp_path / "temp_demo.db"
    wal = tmp_path / "temp_demo.db-wal"
    journal = tmp_path / "temp_demo.db-journal"
    shm = tmp_path / "temp_demo.db-shm"

    for f in (base, wal, journal, shm):
        f.write_text("test")

    load_starter_demo.reset_demo_db(base)

    assert not base.exists()
    assert not wal.exists()
    assert not journal.exists()
    assert not shm.exists()


def test_demo_loader_end_to_end_smoke(monkeypatch, tmp_path):
    """
    End-to-end smoke test: run main() into an isolated temporary DB path,
    verifying it exits 0 and populates documents > 0, facts > 0, relationships > 0.
    """
    smoke_db_path = tmp_path / "smoke_demo.db"
    monkeypatch.setattr(load_starter_demo, "DEMO_DB_PATH", smoke_db_path)

    exit_code = load_starter_demo.main()
    assert exit_code == 0
    assert smoke_db_path.is_file()

    # Inspect the generated SQLite database
    db = Database(str(smoke_db_path))
    try:
        stats = db.get_stats()
        assert stats["total_documents"] == 6
        assert stats["total_pages"] == 511
        # Post-dedup baseline (Defect A fixed): 13 raw rows collapse to 8
        # unique claims; Week 3 Cluster G adds 7 macro-indicator claims
        # (ex-017..ex-020, ex-022..ex-024); Week 4 adds 6 comparative
        # financial-highlights claims (ex-009, ex-010, ex-011, ex-025..ex-027)
        # for 21 total. Relationship cross-products collapse to 24 pairs
        # (3 baseline + 10 Cluster G + 11 Week 4 p.22 pairs).
        assert stats["total_facts"] == 21
        assert stats["total_relationships"] == 24

        # Ensure documents > 0 and facts > 0 per acceptance criteria
        assert stats["total_documents"] > 0
        assert stats["total_facts"] > 0

        # Facts must have non-empty grounded evidence
        facts = db.get_facts(limit=100)
        assert len(facts) == 21
        for f in facts:
            assert f.evidence.text
            assert f.evidence.document_name
            assert f.evidence.page_number > 0

        # Relationships: 20 RECONCILABLE (3 baseline + 3 period-difference
        # rule-8b pairs + 3 forecast/actual pairs + 11 Week 4 p.22 pairs:
        # 4 scope-rule and 2 cross-period on the revenue group, 4 scope-rule
        # and 2 cross-period on the new loss group) and 4 UNCERTAIN
        # (core/headline series missing period context) — zero CONTRADICTS,
        # zero CORROBORATES.
        rels = db.get_relationships(limit=100)
        assert len(rels) == 24
        reconcilable = [r for r in rels if r["relationship"] == "RECONCILABLE"]
        uncertain = [r for r in rels if r["relationship"] == "UNCERTAIN"]
        assert len(reconcilable) == 20
        assert len(uncertain) == 4
        assert [r for r in rels if r["relationship"] == "CONTRADICTS"] == []
        assert [r for r in rels if r["relationship"] == "CORROBORATES"] == []
        assert len([r for r in reconcilable if "vintage" in r["reason"].lower()]) == 1
        assert len([r for r in reconcilable if "temporal progression" in r["reason"].lower()]) == 1
        # Scope rule (rule 6): 1 baseline revenue pair + 3 new revenue
        # cross-scope pairs + 4 new loss cross-scope pairs.
        assert len([r for r in reconcilable if "standalone" in r["reason"].lower()
                    and "consolidated" in r["reason"].lower()]) == 8
        # Rule 8b: different both-known reporting periods explain the value gap
        # (3 baseline inflation pairs + 2 revenue YoY pairs + 2 loss YoY pairs).
        assert len([r for r in reconcilable if "reporting periods" in r["reason"].lower()]) == 7
        # Rule 3: forecast/actual pairs keep their projection reason.
        assert len([r for r in reconcilable if "forward-looking projections" in r["reason"].lower()]) == 3
        # The 4 UNCERTAIN rows are the missing-period core/headline pairs.
        assert len([r for r in uncertain if "ambiguous" in r["reason"].lower()]) == 4
    finally:
        db.close()
