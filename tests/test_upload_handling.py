"""Regression tests for upload-directory configuration and filename safety.

Covers:
- ``FACT_LAYER_UPLOAD_DIR`` routing uploads outside the checkout.
- Lazy creation (resolving the path must not create the directory).
- Basename sanitization for POSIX/Windows/absolute/empty filenames.
- ``POST /upload`` cannot write outside the configured upload directory.
"""

import sys
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.database import Database  # noqa: E402
from app.main import (  # noqa: E402
    app,
    resolve_upload_dir,
    safe_upload_filename,
    set_db,
)


@pytest.fixture
def client_and_db(tmp_path):
    db = Database(str(tmp_path / "upload_handling.db"))
    set_db(db)
    client = TestClient(app)
    yield client, db
    db.close()


def _pdf_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text(
        (50, 50),
        "India's real GDP grew by 6.5 percent in FY2024/25.",
        fontsize=11,
    )
    data = doc.tobytes()
    doc.close()
    return data


def test_resolve_upload_dir_honors_env_and_does_not_create(tmp_path, monkeypatch):
    target = tmp_path / "external-uploads"
    monkeypatch.setenv("FACT_LAYER_UPLOAD_DIR", str(target))

    assert resolve_upload_dir() == str(target)
    assert not target.exists(), "resolving the upload dir must not create it"


def test_safe_upload_filename_rules():
    assert safe_upload_filename("grounded.pdf") == "grounded.pdf"
    assert safe_upload_filename("../../escape.pdf") == "escape.pdf"
    assert safe_upload_filename(r"..\..\escape.pdf") == "escape.pdf"
    assert safe_upload_filename("/tmp/absolute.pdf") == "absolute.pdf"
    assert safe_upload_filename("..") == "upload.pdf"
    assert safe_upload_filename("") == "upload.pdf"
    assert safe_upload_filename(None) == "upload.pdf"


def test_upload_dir_env_override_stores_upload_outside_repo(
    client_and_db, tmp_path, monkeypatch
):
    client, db = client_and_db
    upload_dir = tmp_path / "uploads"
    monkeypatch.setenv("FACT_LAYER_UPLOAD_DIR", str(upload_dir))

    res = client.post(
        "/upload",
        files=[("files", ("grounded.pdf", _pdf_bytes(), "application/pdf"))],
    )

    assert res.status_code == 200, res.text
    assert res.json()["processed_count"] == 1
    assert (upload_dir / "grounded.pdf").is_file()
    assert db.get_documents()[0]["filename"] == "grounded.pdf"


def test_upload_filename_cannot_escape_upload_dir(client_and_db, tmp_path, monkeypatch):
    client, db = client_and_db
    upload_dir = tmp_path / "uploads"
    monkeypatch.setenv("FACT_LAYER_UPLOAD_DIR", str(upload_dir))

    res = client.post(
        "/upload",
        files=[("files", ("../../escape.pdf", _pdf_bytes(), "application/pdf"))],
    )

    assert res.status_code == 200, res.text
    assert (upload_dir / "escape.pdf").is_file()
    assert not (tmp_path / "escape.pdf").exists()
    assert not (tmp_path.parent / "escape.pdf").exists()
    assert db.get_documents()[0]["filename"] == "escape.pdf"


def test_absolute_upload_filename_is_reduced_to_basename(
    client_and_db, tmp_path, monkeypatch
):
    client, db = client_and_db
    upload_dir = tmp_path / "uploads"
    monkeypatch.setenv("FACT_LAYER_UPLOAD_DIR", str(upload_dir))
    escape_target = tmp_path / "absolute.pdf"

    res = client.post(
        "/upload",
        files=[("files", (str(escape_target), _pdf_bytes(), "application/pdf"))],
    )

    assert res.status_code == 200, res.text
    assert (upload_dir / "absolute.pdf").is_file()
    assert not escape_target.exists()
    assert db.get_documents()[0]["filename"] == "absolute.pdf"
