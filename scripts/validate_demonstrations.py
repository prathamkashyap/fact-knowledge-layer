#!/usr/bin/env python3
"""Repeatable demonstration of the four required reasoning outcomes.

Uses a single managed temporary directory (tempfile.TemporaryDirectory),
writes all fixtures inside it, asserts the actual relationship labels from
the application pipeline, verifies starter-data RECONCILABLE/UNCERTAIN,
validates an unseen-PDF pipeline, and removes all artifacts on exit.
Does not claim synthetic fixtures as real-world financial information.
"""
from __future__ import annotations

import sys
import tempfile
import shutil
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Database  # noqa: E402
from app.pipeline import process_document_pipeline  # noqa: E402


def _write_pdf(tmp: Path, filename: str, lines: list[str]) -> Path:
    import fitz
    path = tmp / filename
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "\n".join(lines), fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def _run_stage(label: str, action) -> bool:
    try:
        action()
        print(f"PASS {label}")
        return True
    except Exception as exc:
        print(f"FAIL {label}: {exc}")
        return False


def main() -> int:
    tmp_root = tempfile.mkdtemp(prefix="fkl-demonstration-")
    try:
        # ---------- Stage 1: Synthetic reasoning (CORROBORATES + CONTRADICTS) ----------
        def stage_synthetic():
            db_path = Path(tmp_root) / "synth.db"
            db = Database(str(db_path))
            for fn, lines in [
                ("corroboration_source_a.pdf", [
                    "[SYNTHETIC DEMONSTRATION FIXTURE - illustrative, not real-world data]",
                    "Acme Statistical Office - National Accounts Release FY2024/25",
                    "Real GDP grew by 6.5 percent in FY2024/25.",
                    "Private consumption growth at 7.2 percent in FY2024/25.",
                    "Headline inflation at 4.6 percent in 2024-25.",
                    "Gross fixed capital formation rose by 6.1 percent in 2024/25.",
                ]),
                ("corroboration_source_b.pdf", [
                    "[SYNTHETIC DEMONSTRATION FIXTURE - illustrative, not real-world data]",
                    "Bureau of Economic Analysis - National Accounts Release FY2024/25",
                    "Real GDP grew by 6.5 percent in FY2024/25.",
                    "Private consumption growth at 7.2 percent in FY2024/25.",
                    "Headline inflation at 4.6 percent in 2024-25.",
                    "Gross fixed capital formation rose by 6.1 percent in 2024/25.",
                ]),
                ("contradiction_source_c.pdf", [
                    "[SYNTHETIC DEMONSTRATION FIXTURE - illustrative, not real-world data]",
                    "Acme Statistical Office - National Accounts Release FY2024/25",
                    "Real GDP grew by 6.5 percent in FY2024/25.",
                    "Private consumption growth at 7.2 percent in FY2024/25.",
                    "Headline inflation at 4.6 percent in 2024-25.",
                    "Gross fixed capital formation rose by 6.1 percent in 2024/25.",
                    "Exports grew by 4.3 percent in FY2024/25.",
                ]),
                ("contradiction_source_d.pdf", [
                    "[SYNTHETIC DEMONSTRATION FIXTURE - illustrative, not real-world data]",
                    "Acme Statistical Office - National Accounts Release FY2024/25",
                    "Real GDP grew by 7.2 percent in FY2024/25.",
                    "Private consumption growth at 7.2 percent in FY2024/25.",
                    "Headline inflation at 4.6 percent in 2024-25.",
                    "Gross fixed capital formation rose by 6.1 percent in 2024/25.",
                    "Exports grew by 4.3 percent in FY2024/25.",
                ]),
            ]:
                pdf = _write_pdf(Path(tmp_root), fn, lines)
                process_document_pipeline(pdf_path=str(pdf), provider=None, db=db, reasoning_mode="heuristic")
            db.close()
            rels = Database(str(db_path)).get_relationships(limit=100)
            labels = {r.get("relationship") for r in rels}
            assert "CORROBORATES" in labels, f"Missing CORROBORATES; got {labels}"
            assert "CONTRADICTS" in labels, f"Missing CONTRADICTS; got {labels}"
            print("  CORROBORATES present, CONTRADICTS present")

        # ---------- Stage 2: Starter-data RECONCILABLE / UNCERTAIN ----------
        def stage_starter():
            starter_db = Path(tmp_root) / "starter.db"
            # Use the bundle's loader directly into a temp DB
            import subprocess, sys
            loader_result = subprocess.run(
                [sys.executable, "scripts/load_starter_demo.py", "--db", str(starter_db)],
                cwd=str(Path(__file__).resolve().parent.parent),
                capture_output=True,
                text=True,
            )
            assert loader_result.returncode == 0, f"Loader failed: {loader_result.stderr}"
            starter = Database(str(starter_db))
            stats = starter.get_stats()
            assert stats.get("total_documents", 0) == 6, f"Expected 6 starter docs, got {stats}"
            rels = starter.get_relationships(limit=100)
            labels = {r.get("relationship") for r in rels}
            assert "RECONCILABLE" in labels, f"Missing RECONCILABLE in starter DB; got {labels}"
            assert "UNCERTAIN" in labels, f"Missing UNCERTAIN in starter DB; got {labels}"
            starter.close()
            print("  RECONCILABLE present, UNCERTAIN present")

        # ---------- Stage 3: Unseen-PDF pipeline + API retrieval ----------
        def stage_unseen():
            unseen_pdf = Path(tmp_root) / "unseen_meridian.pdf"
            import fitz
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((50, 50),
                "Meridian Global Limited - Annual Review FY26\n"
                "Real GDP growth for FY26 is projected at 5.8 percent based on first-quarter survey data.\n"
                "Revenue from operations on consolidated basis for FY26 stood at INR 12,000 million.\n"
                "Headline inflation at 3.4 percent in FY26.", fontsize=11)
            doc.save(str(unseen_pdf))
            doc.close()
            unseen_db = Path(tmp_root) / "unseen.db"
            db = Database(str(unseen_db))
            result = process_document_pipeline(pdf_path=str(unseen_pdf), provider=None, db=db, reasoning_mode="heuristic")
            assert result["facts_count"] > 0, f"No facts from unseen PDF: {result}"
            assert result["candidate_pages_count"] >= 1, f"No candidate page: {result}"
            facts = db.get_facts(limit=50)
            assert len(facts) > 0
            assert all(f.evidence.text and f.evidence.page_number == 1 for f in facts)
            # API retrieval via TestClient (same database set on app)
            from fastapi.testclient import TestClient
            from app.main import app, set_db
            client = TestClient(app)
            set_db(db)
            docs_res = client.get("/documents")
            assert docs_res.status_code == 200
            assert any(d.get("filename") == "unseen_meridian.pdf" for d in docs_res.json())
            facts_res = client.get("/facts?limit=20")
            assert facts_res.status_code == 200
            unseen_facts = facts_res.json()
            assert len(unseen_facts) > 0
            first_id = unseen_facts[0]["id"]
            detail_res = client.get(f"/facts/{first_id}")
            assert detail_res.status_code == 200, f"/facts/{{id}} failed: {detail_res.status_code}"
            detail = detail_res.json()
            assert detail.get("id") == first_id
            assert "evidence" in detail
            assert detail["evidence"]["document_name"] == "unseen_meridian.pdf"
            db.close()
            print("  Unseen-PDF pipeline + API retrieval pass")

        ok_synth = _run_stage("Synthetic reasoning", stage_synthetic)
        ok_starter = _run_stage("Starter-data RECONCILABLE/UNCERTAIN", stage_starter)
        ok_unseen = _run_stage("Unseen-PDF pipeline + API", stage_unseen)

        if ok_synth and ok_starter and ok_unseen:
            print("\nAll demonstration stages passed.")
            return 0
        else:
            print(f"\nFAILED: synth={ok_synth}, starter={ok_starter}, unseen={ok_unseen}")
            return 1
    finally:
        try:
            shutil.rmtree(tmp_root, ignore_errors=True)
        except NameError:
            pass  # if setup failed before tmp_root defined
        # Confirm nothing left in repo
        repo_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "synthetic_reasoning"
        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
            print("Cleaned leftover fixture directory in repo")


if __name__ == "__main__":
    sys.exit(main())
