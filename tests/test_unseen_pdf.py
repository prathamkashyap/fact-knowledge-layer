"""Unseen-PDF validation — demonstrates pipeline generality.

The fixture is a synthetic PDF that does not appear in the six bundled
starter datasets (delhivery/ + india-macroeconomy/). It is processed
through the full application pipeline — parse, score, extract, normalize,
match, reason, persist — and verified through the live FastAPI endpoints.
Every result comes from the actual pipeline; nothing is seeded.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pymupdf
from app.database import Database  # noqa: E402
from app.main import app, set_db  # noqa: E402

# A synthetic document that is clearly not any starter PDF.
UNSEEN_FIXTURE_NAME = "synthetic_unseen_meridian_global_fy26.pdf"
UNSEEN_FIXTURE_CONTENT = (
    "Meridian Global Limited - Annual Review FY26\n"
    "Real GDP growth for FY26 is projected at 5.8 percent based on first-quarter "
    "survey data and a stable industrial outlook.\n"
    "Revenue from operations on consolidated basis for FY26 stood at INR 12,000 million.\n"
    "Headline inflation at 3.4 percent in FY26.\n"
)


def _build_unseen_pdf(tmp_path: Path) -> Path:
    path = tmp_path / UNSEEN_FIXTURE_NAME
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), UNSEEN_FIXTURE_CONTENT, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def unseen_client_and_db(tmp_path):
    db_path = tmp_path / "unseen_validation.db"
    db = Database(str(db_path))
    set_db(db)
    client = TestClient(app)
    yield client, db, tmp_path
    db.close()


def test_unseen_pdf_pipeline_and_api(unseen_client_and_db):
    client, db, tmp_path = unseen_client_and_db
    pdf_path = _build_unseen_pdf(tmp_path)

    # Full pipeline — the same path the /upload endpoint uses
    from app.pipeline import process_document_pipeline

    result = process_document_pipeline(
        pdf_path=str(pdf_path),
        provider=None,
        db=db,
        reasoning_mode="heuristic",
    )

    # Pipeline must have processed the document
    assert result["facts_count"] > 0, f"No facts extracted from unseen PDF (got {result})"
    assert result["candidate_pages_count"] >= 1

    # Document persisted
    docs = db.get_documents()
    assert len(docs) == 1, f"Expected 1 document, got {len(docs)}"
    doc = docs[0]
    assert doc["filename"] == UNSEEN_FIXTURE_NAME
    assert doc["total_pages"] >= 1
    assert doc["facts_count"] > 0

    # Facts must carry grounded evidence
    facts = db.get_facts(limit=50)
    assert len(facts) > 0
    for f in facts:
        assert f.evidence.text
        assert f.evidence.page_number == 1
        assert f.evidence.document_name == UNSEEN_FIXTURE_NAME

    # Normalization must have been applied (at least canonical fields present)
    # The heuristic extractor sets subject/predicate/value; normalization
    # runs in pipeline (normalizer.py). We check the DB records have the
    # fields populated rather than asserting exact canonical strings.
    first = facts[0]
    assert first.subject is not None and len(str(first.subject).strip()) > 0
    assert first.predicate is not None and len(str(first.predicate).strip()) > 0

    # API retrieval of the unseen document through the live server
    res_docs = client.get("/documents")
    assert res_docs.status_code == 200
    docs_resp = res_docs.json()
    assert any(d.get("filename") == UNSEEN_FIXTURE_NAME for d in docs_resp)

    # API retrieval of facts from the unseen document
    res_facts = client.get("/facts?limit=20")
    assert res_facts.status_code == 200
    facts_resp = res_facts.json()
    assert len(facts_resp) > 0
    assert any(
        f.get("evidence", {}).get("document_name") == UNSEEN_FIXTURE_NAME
        for f in facts_resp
    )
