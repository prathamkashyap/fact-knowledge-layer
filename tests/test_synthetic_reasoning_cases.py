"""Regression tests for the four required reasoning outcomes.

These tests demonstrate Corroboration, Contradiction, Reconciliation and
Uncertain outcomes through the *real* application pipeline (PDF parser ->
candidate prioritization -> deterministic heuristic extraction ->
normalization -> candidate matching -> epistemic reasoning -> SQLite
persistence). They use clearly-labelled synthetic demonstration fixtures,
never the six bundled starter PDFs, and never seed predetermined
``FactComparison`` objects or relationship labels.

The starter-data run already demonstrates Reconciliation (GDP
estimate-vintage and standalone-vs-consolidated scope) and Uncertain
(ambiguous reporting period) from genuine source material, covered by
``tests/test_demo_loader.py``. This file adds the two outcomes the starter
run cannot produce on its own: Corroboration and Contradiction.
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.database import Database  # noqa: E402
from app.pipeline import process_document_pipeline  # noqa: E402

# ---------------------------------------------------------------------------
# Synthetic demonstration fixtures (illustrative, not real-world data).
#
# Every fixture is deliberately labelled "[SYNTHETIC DEMONSTRATION]" so it
# can never be mistaken for a real source document. They exercise the
# existing deterministic heuristic extraction grammar:
#   "Real GDP grew by <N> percent in <FY>."
# ---------------------------------------------------------------------------

SYNTHETIC_HEADER = "[SYNTHETIC DEMONSTRATION FIXTURE - illustrative, not real-world data]"

CORROBORATION_DOCS = [
    (
        "corroboration_source_a.pdf",
        [
            SYNTHETIC_HEADER,
            "Acme Statistical Office - National Accounts Release FY2024/25",
            "Real GDP grew by 6.5 percent in FY2024/25.",
            "Private consumption growth at 7.2 percent in FY2024/25.",
            "Headline inflation at 4.6 percent in 2024-25.",
            "Gross fixed capital formation rose by 6.1 percent in 2024-25.",
        ],
    ),
    (
        "corroboration_source_b.pdf",
        [
            SYNTHETIC_HEADER,
            "Bureau of Economic Analysis - National Accounts Release FY2024/25",
            "Real GDP grew by 6.5 percent in FY2024/25.",
            "Private consumption growth at 7.2 percent in FY2024/25.",
            "Headline inflation at 4.6 percent in 2024-25.",
            "Gross fixed capital formation rose by 6.1 percent in 2024-25.",
        ],
    ),
]

CONTRADICTION_DOCS = [
    (
        "contradiction_source_c.pdf",
        [
            SYNTHETIC_HEADER,
            "Acme Statistical Office - National Accounts Release FY2024/25",
            "Real GDP grew by 6.5 percent in FY2024/25.",
            "Private consumption growth at 7.2 percent in FY2024/25.",
            "Headline inflation at 4.6 percent in 2024-25.",
            "Gross fixed capital formation rose by 6.1 percent in 2024-25.",
            "Exports grew by 4.3 percent in FY2024/25.",
        ],
    ),
    (
        "contradiction_source_d.pdf",
        [
            SYNTHETIC_HEADER,
            "Acme Statistical Office - National Accounts Release FY2024/25",
            "Real GDP grew by 7.2 percent in FY2024/25.",
            "Private consumption growth at 7.2 percent in FY2024/25.",
            "Headline inflation at 4.6 percent in 2024-25.",
            "Gross fixed capital formation rose by 6.1 percent in 2024-25.",
            "Exports grew by 4.3 percent in FY2024/25.",
        ],
    ),
]


def _write_fixture(directory: Path, filename: str, lines: list[str]) -> Path:
    import pymupdf as fitz
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "\n".join(lines), fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def _load_docs_into_db(docs: list[tuple[str, list[str]]], db_path: Path) -> int:
    import fitz  # local import so import-time cost is bounded

    from app.pipeline import process_document_pipeline

    total_comparisons = 0
    directory = db_path.parent
    for filename, lines in docs:
        pdf_path = _write_fixture(directory, filename, lines)
        result = process_document_pipeline(
            pdf_path=str(pdf_path),
            provider=None,
            db=Database(str(db_path)),
            reasoning_mode="heuristic",
        )
        total_comparisons += result["comparisons_count"]
    return total_comparisons


def _has_relationship(relationships: list[dict], label: str) -> bool:
    return any(r.get("relationship") == label for r in relationships)


def test_synthetic_demonstration_produces_corroboration_and_contradiction(tmp_path):
    """Corroboration and Contradiction through the real pipeline.

    Two independent synthetic sources stating the same GDP figure for the
    same period must surface as CORROBORATES; two sources stating
    incompatible values for the same metric, period and scope must surface
    as CONTRADICTS. Labels are asserted as actually produced, never seeded.
    """
    db_path = tmp_path / "synthetic_demonstration.db"
    docs = CORROBORATION_DOCS + CONTRADICTION_DOCS

    from app.database import Database

    db = Database(str(db_path))
    total_comparisons = 0
    try:
        import fitz  # noqa: F401  (ensures PyMuPDF is available in this env)

        directory = db_path.parent
        for filename, lines in docs:
            pdf_path = _write_fixture(directory, filename, lines)
            result = process_document_pipeline(
                pdf_path=str(pdf_path),
                provider=None,
                db=db,
                reasoning_mode="heuristic",
            )
            total_comparisons += result["comparisons_count"]
    finally:
        db.close()

    assert total_comparisons > 0, "no candidate comparisons were generated"

    db = Database(str(db_path))
    try:
        relationships = db.get_relationships(limit=100)

        corroborating = [
            r
            for r in relationships
            if r.get("relationship") == "CORROBORATES"
        ]
        contradicting = [
            r
            for r in relationships
            if r.get("relationship") == "CONTRADICTS"
        ]

        assert len(corroborating) >= 1, (
            "expected a live CORROBORATES relationship; got "
            f"{[(r['relationship'], r['reason']) for r in relationships]}"
        )
        assert len(contradicting) >= 1, (
            "expected a live CONTRADICTS relationship; got "
            f"{[(r['relationship'], r['reason']) for r in relationships]}"
        )

        # Labels must be the ones the application actually produced, with
        # grounded evidence and dimensions on every relationship.
        for r in corroborating + contradicting:
            assert r.get("confidence") is not None
            assert r.get("reason")
            assert r.get("dimensions")
            assert r.get("fact_a_id") and r.get("fact_b_id")

        facts = db.get_facts(limit=100)
        gdp_facts = [
            f
            for f in facts
            if f.subject == "India real GDP growth" and f.predicate == "growth rate"
        ]
        values = sorted({f.value for f in gdp_facts})
        assert values == [6.5, 7.2], f"unexpected extracted GDP values: {values}"
        for f in facts:
            assert f.evidence.text
            assert f.evidence.document_name
            assert f.evidence.page_number > 0
    finally:
        db.close()
