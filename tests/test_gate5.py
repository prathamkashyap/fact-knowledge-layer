"""
Gate 5 automated tests for API, Persistence, and Fact Explorer UI.
Tests covering:
- Health check endpoint
- Empty database responses
- Document persistence
- PDF upload and processing pipeline integration
- Invalid upload handling
- Fact listing, search, and detail retrieval
- Cross-document relationship listing and filtering
- HTML UI serving
"""

import os
import io
import pytest
import pymupdf
from fastapi.testclient import TestClient
from app.main import app, set_db
from app.database import Database
from app.models import Fact, Evidence, FactComparison, Dimensions


@pytest.fixture
def client_and_db(tmp_path):
    """Fixture providing isolated TestClient and temporary SQLite database."""
    test_db_path = str(tmp_path / "test_fact_layer.db")
    db = Database(test_db_path)
    set_db(db)
    client = TestClient(app)
    yield client, db
    db.close()


def create_synthetic_pdf(tmp_path, filename="test_doc.pdf") -> str:
    """Create a small valid PDF with grounded test facts."""
    pdf_path = str(tmp_path / filename)
    doc = pymupdf.open()
    page = doc.new_page()
    text = (
        "India's real GDP grew by 6.5 percent in FY2024/25.\n"
        "Revenue from operations on consolidated basis for FY24 stood at INR 81,415.38 million.\n"
        "Revenue from operations on standalone basis for FY24 stood at INR 74,540.82 million.\n"
        "Suvir Suren Sujan, Non-Executive Director, resigned from the Board with effect from August 24, 2023.\n"
    )
    page.insert_text((50, 50), text, fontsize=11)
    doc.save(pdf_path)
    doc.close()
    return pdf_path


# ---------------------------------------------------------------------------
# 1. Health & Empty Database Tests
# ---------------------------------------------------------------------------

def test_health_endpoint(client_and_db):
    client, _ = client_and_db
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["service"] == "Superjoin Fact Knowledge Layer"
    assert "stats" in data
    assert "reasoning_mode" in data


def test_empty_database_behavior(client_and_db):
    client, _ = client_and_db
    # Empty documents
    res_docs = client.get("/documents")
    assert res_docs.status_code == 200
    assert res_docs.json() == []

    # Empty facts
    res_facts = client.get("/facts")
    assert res_facts.status_code == 200
    assert res_facts.json() == []

    # Empty relationships
    res_rels = client.get("/relationships")
    assert res_rels.status_code == 200
    assert res_rels.json() == []


# ---------------------------------------------------------------------------
# 2. Upload Endpoint Tests
# ---------------------------------------------------------------------------

def test_upload_invalid_file(client_and_db):
    client, _ = client_and_db
    # Uploading a text file instead of PDF should fail with 400
    files = {"files": ("test.txt", b"This is not a PDF", "text/plain")}
    res = client.post("/upload", files=files)
    assert res.status_code == 400
    assert "PDF" in res.json()["detail"]


def test_upload_and_process_pdf(client_and_db, tmp_path):
    client, db = client_and_db
    pdf_path = create_synthetic_pdf(tmp_path, "synthetic_upload.pdf")

    with open(pdf_path, "rb") as f:
        files = [("files", ("synthetic_upload.pdf", f.read(), "application/pdf"))]

    res = client.post("/upload", files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["processed_count"] == 1
    result = data["results"][0]
    assert result["filename"] == "synthetic_upload.pdf"
    assert result["total_pages"] == 1
    assert result["candidate_pages_count"] == 1
    assert result["facts_count"] >= 2

    # Verify documents endpoint now returns the uploaded doc
    docs_res = client.get("/documents")
    assert len(docs_res.json()) == 1
    assert docs_res.json()[0]["filename"] == "synthetic_upload.pdf"


# ---------------------------------------------------------------------------
# 3. Fact Listing and Retrieval Tests
# ---------------------------------------------------------------------------

def test_fact_retrieval_and_evidence(client_and_db, tmp_path):
    client, db = client_and_db
    pdf_path = create_synthetic_pdf(tmp_path, "test_evidence.pdf")

    with open(pdf_path, "rb") as f:
        client.post("/upload", files=[("files", ("test_evidence.pdf", f.read(), "application/pdf"))])

    facts_res = client.get("/facts")
    assert facts_res.status_code == 200
    facts = facts_res.json()
    assert len(facts) >= 2

    # Inspect a fact and verify evidence is fully populated
    f = facts[0]
    assert "evidence" in f
    assert f["evidence"]["document_name"] == "test_evidence.pdf"
    assert f["evidence"]["page_number"] == 1
    assert len(f["evidence"]["text"]) > 0


def test_fact_search_query(client_and_db, tmp_path):
    client, db = client_and_db
    pdf_path = create_synthetic_pdf(tmp_path, "search_test.pdf")

    with open(pdf_path, "rb") as f:
        client.post("/upload", files=[("files", ("search_test.pdf", f.read(), "application/pdf"))])

    # Search for GDP
    res_gdp = client.get("/facts?search=GDP")
    assert res_gdp.status_code == 200
    gdp_facts = res_gdp.json()
    assert len(gdp_facts) >= 1
    assert any("GDP" in f["subject"] or "GDP" in f["evidence"]["text"] for f in gdp_facts)

    # Search for Delhivery
    res_del = client.get("/facts?search=revenue")
    assert res_del.status_code == 200
    rev_facts = res_del.json()
    assert len(rev_facts) >= 1


def test_fact_detail_not_found(client_and_db):
    client, _ = client_and_db
    res = client.get("/facts/nonexistent_id")
    assert res.status_code == 404
    assert res.json()["detail"] == "Fact not found."


def test_fact_detail_with_relationships(client_and_db, tmp_path):
    client, db = client_and_db
    pdf_path = create_synthetic_pdf(tmp_path, "rel_doc.pdf")

    with open(pdf_path, "rb") as f:
        client.post("/upload", files=[("files", ("rel_doc.pdf", f.read(), "application/pdf"))])

    facts = client.get("/facts").json()
    fact_id = facts[0]["id"]

    res_detail = client.get(f"/facts/{fact_id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["id"] == fact_id
    assert "relationships" in detail


# ---------------------------------------------------------------------------
# 4. Relationships and Filtering Tests
# ---------------------------------------------------------------------------

def test_relationship_retrieval_and_filtering(client_and_db, tmp_path):
    client, db = client_and_db
    pdf_path = create_synthetic_pdf(tmp_path, "scope_doc.pdf")

    with open(pdf_path, "rb") as f:
        client.post("/upload", files=[("files", ("scope_doc.pdf", f.read(), "application/pdf"))])

    # The synthetic PDF contains both standalone and consolidated revenue,
    # so the pipeline creates a RECONCILABLE comparison
    rels_res = client.get("/relationships")
    assert rels_res.status_code == 200
    rels = rels_res.json()
    assert len(rels) >= 1

    # Filter by RECONCILABLE
    rec_res = client.get("/relationships?relationship=RECONCILABLE")
    assert rec_res.status_code == 200
    for r in rec_res.json():
        assert r["relationship"] == "RECONCILABLE"
        assert "dimensions" in r


# ---------------------------------------------------------------------------
# 5. UI Serving Tests
# ---------------------------------------------------------------------------

def test_ui_served_at_root(client_and_db):
    client, _ = client_and_db
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Superjoin Fact Knowledge Layer" in res.text
    assert "Fact Explorer" in res.text
    assert "Cross-Document Relationships" in res.text
    assert "Upload & Ingest" in res.text


def test_ui_served_at_ui_route(client_and_db):
    client, _ = client_and_db
    res = client.get("/ui")
    assert res.status_code == 200
    assert "Superjoin Fact Knowledge Layer" in res.text


# ---------------------------------------------------------------------------
# 6. Database Stats Test
# ---------------------------------------------------------------------------

def test_system_stats_endpoint(client_and_db, tmp_path):
    client, _ = client_and_db
    pdf_path = create_synthetic_pdf(tmp_path, "stats_doc.pdf")

    with open(pdf_path, "rb") as f:
        client.post("/upload", files=[("files", ("stats_doc.pdf", f.read(), "application/pdf"))])

    res = client.get("/stats")
    assert res.status_code == 200
    stats = res.json()
    assert stats["total_documents"] == 1
    assert stats["total_facts"] >= 2
