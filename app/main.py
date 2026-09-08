"""
FastAPI application for the Superjoin Fact Knowledge Layer.
Exposes REST API and plain HTML/CSS/JavaScript Fact Explorer UI:
- GET /health
- POST /upload
- GET /documents
- GET /documents/{id}
- GET /facts
- GET /facts/{id}
- GET /relationships
- GET /stats
- GET / (UI)
"""

import os
import shutil
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from app.models import Fact, FactComparison
from app.database import Database, DEFAULT_DB_PATH
from app.pipeline import process_document_pipeline
from app.providers import LLMProvider, MockProvider


app = FastAPI(
    title="Superjoin Fact Knowledge Layer",
    description="Evidence-grounded cross-document fact extraction and epistemic reasoning engine.",
    version="1.0.0",
)

# Global database instance
db_instance: Optional[Database] = None
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_db() -> Database:
    global db_instance
    if db_instance is None:
        db_instance = Database()
    return db_instance


def set_db(db: Database):
    """Override database instance (useful for testing)."""
    global db_instance
    db_instance = db


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check():
    db = get_db()
    stats = db.get_stats()

    return {
        "status": "ok",
        "service": "Superjoin Fact Knowledge Layer",
        "version": "1.0.0",
        "database": "connected",
        "reasoning_mode": "heuristic",
        "stats": stats,
    }


@app.post("/upload")
async def upload_documents(files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    db = get_db()
    results = []

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file '{file.filename}'. Only PDF documents are accepted."
            )

        # Save to uploads directory
        save_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(save_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        try:
            res = process_document_pipeline(
                pdf_path=save_path,
                provider=None,
                db=db,
                reasoning_mode="heuristic",
            )
            results.append(res)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error processing PDF '{file.filename}': {str(e)}"
            )

    return {
        "status": "success",
        "processed_count": len(results),
        "results": results,
    }


@app.get("/documents")
def list_documents():
    db = get_db()
    return db.get_documents()


@app.get("/documents/{doc_id}")
def get_document_details(doc_id: str):
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    pages = db.get_document_pages(doc_id)
    return {"document": doc, "pages": pages}


@app.get("/facts")
def list_facts(
    document_id: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    db = get_db()
    facts = db.get_facts(document_id=document_id, search=search, limit=limit, offset=offset)
    return [f.model_dump() for f in facts]


@app.get("/facts/{fact_id}")
def get_fact_details(fact_id: str):
    db = get_db()
    fact_dict = db.get_fact(fact_id)
    if not fact_dict:
        raise HTTPException(status_code=404, detail="Fact not found.")
    return fact_dict


@app.get("/relationships")
def list_relationships(
    relationship: Optional[str] = None,
    fact_id: Optional[str] = None,
    document_id: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
):
    db = get_db()
    return db.get_relationships(
        relationship=relationship,
        fact_id=fact_id,
        document_id=document_id,
        limit=limit,
    )


@app.get("/stats")
def get_system_stats():
    db = get_db()
    return db.get_stats()


# ---------------------------------------------------------------------------
# HTML / UI View
# ---------------------------------------------------------------------------

HTML_UI = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Superjoin Fact Knowledge Layer</title>
    <style>
        :root {
            --bg: #f8fafc;
            --surface: #ffffff;
            --border: #e2e8f0;
            --text-main: #0f172a;
            --text-muted: #64748b;
            --primary: #2563eb;
            --primary-hover: #1d4ed8;
            --corroborates: #10b981;
            --contradicts: #ef4444;
            --reconcilable: #f59e0b;
            --uncertain: #8b5cf6;
            --unrelated: #64748b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text-main);
            line-height: 1.5;
        }
        header {
            background: #0f172a;
            color: #ffffff;
            padding: 1.25rem 2rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid #1e293b;
        }
        .header-title h1 { font-size: 1.3rem; font-weight: 700; letter-spacing: -0.02em; }
        .header-title p { font-size: 0.85rem; color: #94a3b8; }
        .badge-mode {
            padding: 0.3rem 0.8rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            background: #1e293b;
            color: #38bdf8;
            border: 1px solid #334155;
        }
        .stats-bar {
            background: #ffffff;
            border-bottom: 1px solid var(--border);
            padding: 0.75rem 2rem;
            display: flex;
            gap: 2rem;
        }
        .stat-item { display: flex; align-items: baseline; gap: 0.5rem; }
        .stat-value { font-size: 1.25rem; font-weight: 700; color: var(--primary); }
        .stat-label { font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; }

        nav.tabs {
            background: #ffffff;
            border-bottom: 1px solid var(--border);
            padding: 0 2rem;
            display: flex;
            gap: 1.5rem;
        }
        .tab-btn {
            background: none;
            border: none;
            padding: 1rem 0.5rem;
            font-size: 0.9rem;
            font-weight: 600;
            color: var(--text-muted);
            cursor: pointer;
            border-bottom: 2px solid transparent;
            transition: all 0.15s ease;
        }
        .tab-btn.active {
            color: var(--primary);
            border-bottom-color: var(--primary);
        }
        main { padding: 2rem; max-width: 1400px; margin: 0 auto; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        .card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.02);
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }
        .card-title { font-size: 1.1rem; font-weight: 700; color: var(--text-main); }

        .search-row {
            display: flex;
            gap: 1rem;
            margin-bottom: 1.5rem;
        }
        .search-input {
            flex: 1;
            padding: 0.65rem 1rem;
            border: 1px solid var(--border);
            border-radius: 6px;
            font-size: 0.9rem;
        }
        .search-input:focus { outline: none; border-color: var(--primary); ring: 2px solid #bfdbfe; }
        .btn {
            background: var(--primary);
            color: white;
            border: none;
            padding: 0.65rem 1.25rem;
            border-radius: 6px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.15s ease;
        }
        .btn:hover { background: var(--primary-hover); }
        .btn-outline {
            background: transparent;
            color: var(--text-main);
            border: 1px solid var(--border);
        }
        .btn-outline:hover { background: #f1f5f9; }

        .tag {
            display: inline-block;
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
        }
        .tag-corroborates { background: #d1fae5; color: #065f46; }
        .tag-contradicts { background: #fee2e2; color: #991b1b; }
        .tag-reconcilable { background: #fef3c7; color: #92400e; }
        .tag-uncertain { background: #ede9fe; color: #5b21b6; }
        .tag-unrelated { background: #f1f5f9; color: #475569; }

        .grid-facts {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 1.25rem;
        }
        .fact-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.25rem;
            cursor: pointer;
            transition: transform 0.1s, box-shadow 0.1s;
        }
        .fact-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
            border-color: #cbd5e1;
        }
        .fact-subject { font-size: 1rem; font-weight: 700; color: var(--text-main); margin-bottom: 0.25rem; }
        .fact-metric { font-size: 0.85rem; color: var(--text-muted); margin-bottom: 0.75rem; }
        .fact-value-box {
            background: #f8fafc;
            padding: 0.6rem 0.8rem;
            border-radius: 6px;
            margin-bottom: 0.75rem;
            display: flex;
            justify-content: space-between;
            align-items: baseline;
        }
        .fact-val { font-size: 1.15rem; font-weight: 700; color: var(--primary); }
        .fact-period { font-size: 0.8rem; font-weight: 600; color: #475569; }
        .fact-evidence-preview {
            font-size: 0.8rem;
            color: #475569;
            font-style: italic;
            border-left: 2px solid var(--primary);
            padding-left: 0.5rem;
            margin-top: 0.5rem;
        }

        .comparison-item {
            background: #ffffff;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.25rem;
            margin-bottom: 1rem;
        }
        .comparison-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.75rem;
        }
        .comparison-body {
            display: grid;
            grid-template-columns: 1fr auto 1fr;
            gap: 1.5rem;
            align-items: center;
            background: #f8fafc;
            padding: 1rem;
            border-radius: 6px;
            margin-bottom: 0.75rem;
        }
        .side-box { font-size: 0.85rem; }
        .side-box strong { font-size: 0.95rem; color: var(--text-main); display: block; margin-bottom: 0.25rem; }
        .vs-indicator { font-size: 0.8rem; font-weight: 700; color: var(--text-muted); }
        .reason-box {
            font-size: 0.85rem;
            background: #f1f5f9;
            padding: 0.6rem 0.8rem;
            border-radius: 4px;
            color: #1e293b;
        }
        .dimension-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-top: 0.5rem;
        }
        .chip {
            font-size: 0.75rem;
            padding: 0.15rem 0.4rem;
            border-radius: 4px;
            background: #e2e8f0;
            color: #334155;
        }
        .chip.same { background: #dcfce7; color: #166534; }
        .chip.different { background: #fee2e2; color: #991b1b; }

        /* Modal */
        .modal {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.5);
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }
        .modal.active { display: flex; }
        .modal-content {
            background: #ffffff;
            width: 90%;
            max-width: 800px;
            max-height: 90vh;
            border-radius: 8px;
            overflow-y: auto;
            padding: 2rem;
            position: relative;
        }
        .close-btn {
            position: absolute;
            top: 1.5rem; right: 1.5rem;
            font-size: 1.5rem;
            cursor: pointer;
            border: none;
            background: none;
        }
    </style>
</head>
<body>
    <header>
        <div class="header-title">
            <h1>Superjoin Fact Knowledge Layer</h1>
            <p>Evidence-grounded cross-document fact extraction and epistemic reasoning</p>
        </div>
        <div class="badge-mode" id="modeBadge">Offline / Heuristic Mode</div>
    </header>

    <div class="stats-bar">
        <div class="stat-item">
            <div class="stat-value" id="statDocs">0</div>
            <div class="stat-label">Documents</div>
        </div>
        <div class="stat-item">
            <div class="stat-value" id="statPages">0</div>
            <div class="stat-label">Pages Ingested</div>
        </div>
        <div class="stat-item">
            <div class="stat-value" id="statFacts">0</div>
            <div class="stat-label">Grounded Facts</div>
        </div>
        <div class="stat-item">
            <div class="stat-value" id="statRels">0</div>
            <div class="stat-label">Relationships</div>
        </div>
    </div>

    <nav class="tabs">
        <button class="tab-btn active" onclick="switchTab('factsTab')">Fact Explorer</button>
        <button class="tab-btn" onclick="switchTab('relsTab')">Cross-Document Relationships</button>
        <button class="tab-btn" onclick="switchTab('demoTab')">Demonstration Cases</button>
        <button class="tab-btn" onclick="switchTab('uploadTab')">Upload & Ingest</button>
        <button class="tab-btn" onclick="switchTab('docsTab')">Documents</button>
    </nav>

    <main>
        <!-- FACT EXPLORER -->
        <div id="factsTab" class="tab-content active">
            <div class="search-row">
                <input type="text" id="factSearch" class="search-input" placeholder="Search facts by entity, metric, value, or evidence text (e.g. GDP, revenue, Delhivery, 6.5%)...">
                <button class="btn" onclick="loadFacts()">Search</button>
                <button class="btn btn-outline" onclick="resetFactSearch()">Reset</button>
            </div>
            <div class="grid-facts" id="factsGrid">
                <!-- Fact cards rendered via JS -->
            </div>
        </div>

        <!-- RELATIONSHIPS -->
        <div id="relsTab" class="tab-content">
            <div class="search-row">
                <button class="btn btn-outline" onclick="loadRelationships()">All Relationships</button>
                <button class="btn btn-outline" onclick="loadRelationships('CORROBORATES')">Corroborates</button>
                <button class="btn btn-outline" onclick="loadRelationships('RECONCILABLE')">Reconcilable</button>
                <button class="btn btn-outline" onclick="loadRelationships('CONTRADICTS')">Contradicts</button>
                <button class="btn btn-outline" onclick="loadRelationships('UNCERTAIN')">Uncertain</button>
            </div>
            <div id="relationshipsList">
                <!-- Comparisons rendered via JS -->
            </div>
        </div>

        <!-- DEMO CASES -->
        <div id="demoTab" class="tab-content">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">Core Required Demonstration Cases</div>
                </div>
                <p style="margin-bottom: 1.5rem; color: var(--text-muted);">
                    Inspect the 4 core cases required by the assignment with transparent dimension diffs, source evidence, and decision rationales.
                </p>
                <div style="display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem;">
                    <button class="btn" onclick="filterDemoCase('gdp_corroborate')">Case 1: GDP Corroboration (RBI ↔ IMF 6.5%)</button>
                    <button class="btn" onclick="filterDemoCase('cpi_investigate')">Case 2: Contradiction Investigation (CPI Inflation)</button>
                    <button class="btn" onclick="filterDemoCase('vintage_reconcile')">Case 3a: Estimate Vintage Reconciliation (6.4% vs 6.5%)</button>
                    <button class="btn" onclick="filterDemoCase('director_reconcile')">Case 3b: Director Temporal Reconciliation</button>
                    <button class="btn" onclick="filterDemoCase('scope_failure')">Case 4: Scope Distinction Failure & Fix (Standalone vs Consolidated)</button>
                </div>
                <div id="demoResults"></div>
            </div>
        </div>

        <!-- UPLOAD TAB -->
        <div id="uploadTab" class="tab-content">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">Upload & Ingest PDF Documents</div>
                </div>
                <p style="margin-bottom: 1rem; color: var(--text-muted);">
                    Upload any arbitrary PDF document. The pipeline will parse pages with PyMuPDF, run generic candidate page prioritization, extract grounded facts with verbatim evidence, normalize metrics, and perform cross-document candidate matching.
                </p>
                <input type="file" id="pdfFileInput" accept="application/pdf" multiple style="margin-bottom: 1rem;">
                <div>
                    <button class="btn" onclick="handleUpload()">Upload & Ingest</button>
                </div>
                <div id="uploadStatus" style="margin-top: 1rem; font-size: 0.9rem;"></div>
            </div>
        </div>

        <!-- DOCUMENTS TAB -->
        <div id="docsTab" class="tab-content">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">Ingested Documents in Knowledge Layer</div>
                </div>
                <div id="documentsList"></div>
            </div>
        </div>
    </main>

    <!-- FACT DETAIL MODAL -->
    <div id="factModal" class="modal">
        <div class="modal-content">
            <button class="close-btn" onclick="closeModal()">&times;</button>
            <div id="modalBody"></div>
        </div>
    </div>

    <script>
        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            event.target.classList.add('active');
        }

        async function fetchStats() {
            try {
                const res = await fetch('/health');
                const data = await res.json();
                document.getElementById('statDocs').innerText = data.stats.total_documents;
                document.getElementById('statPages').innerText = data.stats.total_pages;
                document.getElementById('statFacts').innerText = data.stats.total_facts;
                document.getElementById('statRels').innerText = data.stats.total_relationships;
                document.getElementById('modeBadge').innerText = 'Offline / Heuristic Mode';
            } catch(e) { console.error(e); }
        }

        async function loadFacts(searchQuery = '') {
            const query = searchQuery || document.getElementById('factSearch').value;
            const url = query ? `/facts?search=${encodeURIComponent(query)}` : '/facts';
            const res = await fetch(url);
            const facts = await res.json();
            const grid = document.getElementById('factsGrid');
            grid.innerHTML = '';

            if (facts.length === 0) {
                grid.innerHTML = '<p style="color: var(--text-muted); grid-column: 1/-1;">No facts found. Upload a PDF to ingest facts.</p>';
                return;
            }

            facts.forEach(f => {
                const card = document.createElement('div');
                card.className = 'fact-card';
                card.onclick = () => openFactModal(f.id);
                card.innerHTML = `
                    <div class="fact-subject">${f.subject}</div>
                    <div class="fact-metric">${f.predicate} ${f.scope ? '(' + f.scope + ')' : ''}</div>
                    <div class="fact-value-box">
                        <span class="fact-val">${f.value} <span style="font-size: 0.85rem; font-weight: normal;">${f.unit || ''}</span></span>
                        <span class="fact-period">${f.period || f.as_of || 'N/A'}</span>
                    </div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); display: flex; justify-content: space-between;">
                        <span>📄 ${f.evidence.document_name || 'PDF'}</span>
                        <span>p.${f.evidence.page_number}</span>
                    </div>
                    <div class="fact-evidence-preview">"${f.evidence.text.substring(0, 100)}..."</div>
                `;
                grid.appendChild(card);
            });
        }

        function resetFactSearch() {
            document.getElementById('factSearch').value = '';
            loadFacts();
        }

        async function openFactModal(factId) {
            const res = await fetch(`/facts/${factId}`);
            const f = await res.json();
            const body = document.getElementById('modalBody');
            
            let relsHtml = '<p style="color: var(--text-muted); font-size: 0.85rem;">No cross-document relationships found for this fact.</p>';
            if (f.relationships && f.relationships.length > 0) {
                relsHtml = f.relationships.map(r => `
                    <div style="background: #f8fafc; border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem; margin-top: 0.5rem;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.25rem;">
                            <span class="tag tag-${r.relationship.toLowerCase()}">${r.relationship}</span>
                            <span style="font-size: 0.75rem; color: var(--text-muted);">Confidence: ${(r.confidence * 100).toFixed(0)}%</span>
                        </div>
                        <p style="font-size: 0.85rem; margin-bottom: 0.25rem;"><strong>Target:</strong> ${r.related_fact ? r.related_fact.subject + ' (' + r.related_fact.value + ' ' + (r.related_fact.unit || '') + ')' : 'Linked Fact'}</p>
                        <p style="font-size: 0.8rem; color: #334155;"><strong>Rationale:</strong> ${r.reason}</p>
                    </div>
                `).join('');
            }

            body.innerHTML = `
                <h2 style="font-size: 1.25rem; margin-bottom: 0.25rem;">${f.subject}</h2>
                <div style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 1rem;">
                    Canonical entity: <strong>${f.canonical_subject || f.subject}</strong> | Canonical predicate: <strong>${f.canonical_predicate || f.predicate}</strong>
                </div>

                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; background: #f8fafc; padding: 1rem; border-radius: 6px; margin-bottom: 1rem; font-size: 0.85rem;">
                    <div>
                        <p><strong>Raw Value:</strong> ${f.value} ${f.unit || ''}</p>
                        <p><strong>Normalized Value:</strong> ${f.normalized_value !== null ? f.normalized_value : 'N/A'} ${f.normalized_unit || ''}</p>
                        <p><strong>Scope / Basis:</strong> ${f.scope || 'Standard'}</p>
                    </div>
                    <div>
                        <p><strong>Period:</strong> ${f.period || 'N/A'}</p>
                        <p><strong>Normalized Period:</strong> ${f.normalized_period || 'N/A'} (${f.period_type || 'unknown'})</p>
                        <p><strong>As-of Date:</strong> ${f.as_of || 'N/A'}</p>
                    </div>
                </div>

                <div style="margin-bottom: 1.25rem;">
                    <h4 style="font-size: 0.9rem; font-weight: 700; margin-bottom: 0.5rem;">Source Evidence</h4>
                    <div style="background: #eff6ff; border-left: 3px solid var(--primary); padding: 0.75rem 1rem; font-size: 0.85rem; color: #1e3a8a;">
                        "${f.evidence.text}"
                    </div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem;">
                        Document: ${f.evidence.document_name || 'N/A'} | Page: ${f.evidence.page_number}
                    </div>
                </div>

                <div>
                    <h4 style="font-size: 0.9rem; font-weight: 700; margin-bottom: 0.5rem;">Cross-Document Relationships (${f.relationships.length})</h4>
                    ${relsHtml}
                </div>
            `;
            document.getElementById('factModal').classList.add('active');
        }

        function closeModal() {
            document.getElementById('factModal').classList.remove('active');
        }

        async function loadRelationships(filterRel = '') {
            const url = filterRel ? `/relationships?relationship=${filterRel}` : '/relationships';
            const res = await fetch(url);
            const rels = await res.json();
            const list = document.getElementById('relationshipsList');
            list.innerHTML = '';

            if (rels.length === 0) {
                list.innerHTML = '<p style="color: var(--text-muted);">No cross-document relationships matching criteria.</p>';
                return;
            }

            rels.forEach(r => {
                const item = document.createElement('div');
                item.className = 'comparison-item';
                
                const dims = r.dimensions || {};
                const dimsHtml = Object.entries(dims).map(([k, v]) => `
                    <span class="chip ${v}">${k}: ${v}</span>
                `).join('');

                item.innerHTML = `
                    <div class="comparison-header">
                        <div>
                            <span class="tag tag-${r.relationship.toLowerCase()}">${r.relationship}</span>
                            <span style="font-size: 0.8rem; color: var(--text-muted); margin-left: 0.5rem;">Confidence: ${(r.confidence * 100).toFixed(0)}%</span>
                        </div>
                        <span style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">Mode: ${r.reasoning_mode}</span>
                    </div>
                    <div class="comparison-body">
                        <div class="side-box">
                            <strong>${r.fa_subject}</strong>
                            <div>${r.fa_value} ${r.fa_unit || ''} | ${r.fa_period || 'N/A'} ${r.fa_scope ? '(' + r.fa_scope + ')' : ''}</div>
                            <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem;">📄 ${r.fa_doc} p.${r.fa_page}</div>
                        </div>
                        <div class="vs-indicator">↔</div>
                        <div class="side-box">
                            <strong>${r.fb_subject}</strong>
                            <div>${r.fb_value} ${r.fb_unit || ''} | ${r.fb_period || 'N/A'} ${r.fb_scope ? '(' + r.fb_scope + ')' : ''}</div>
                            <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem;">📄 ${r.fb_doc} p.${r.fb_page}</div>
                        </div>
                    </div>
                    <div class="reason-box">
                        <strong>Decision Rationale:</strong> ${r.reason}
                    </div>
                    <div class="dimension-chips">
                        ${dimsHtml}
                    </div>
                `;
                list.appendChild(item);
            });
        }

        async function filterDemoCase(caseType) {
            const container = document.getElementById('demoResults');
            container.innerHTML = '<p>Loading case demonstration...</p>';

            const res = await fetch('/relationships');
            const allRels = await res.json();

            let filtered = [];
            if (caseType === 'gdp_corroborate') {
                filtered = allRels.filter(r =>
                    r.relationship === 'CORROBORATES' &&
                    (r.fa_subject || '').toLowerCase().includes('gdp') &&
                    (r.fb_subject || '').toLowerCase().includes('gdp')
                );
            } else if (caseType === 'cpi_investigate') {
                filtered = allRels.filter(r =>
                    (r.fa_subject || '').toLowerCase().includes('cpi') ||
                    (r.fb_subject || '').toLowerCase().includes('cpi') ||
                    (r.fa_subject || '').toLowerCase().includes('inflation') ||
                    (r.fb_subject || '').toLowerCase().includes('inflation')
                );
            } else if (caseType === 'vintage_reconcile') {
                filtered = allRels.filter(r =>
                    r.relationship === 'RECONCILABLE' &&
                    ((r.reason || '').toLowerCase().includes('vintage') ||
                     (r.reason || '').toLowerCase().includes('advance estimate'))
                );
            } else if (caseType === 'director_reconcile') {
                filtered = allRels.filter(r =>
                    r.relationship === 'RECONCILABLE' &&
                    ((r.reason || '').toLowerCase().includes('temporal') ||
                     (r.reason || '').toLowerCase().includes('resignation') ||
                     (r.reason || '').toLowerCase().includes('cessation') ||
                     (r.reason || '').toLowerCase().includes('date'))
                );
            } else if (caseType === 'scope_failure') {
                filtered = allRels.filter(r =>
                    r.relationship === 'RECONCILABLE' &&
                    r.dimensions && r.dimensions.scope === 'different'
                );
            }

            if (filtered.length === 0) {
                container.innerHTML = `<div style="background: #f8fafc; padding: 1rem; border-radius: 6px; border: 1px dashed var(--border);">
                    <p style="color: var(--text-muted);">Demonstration facts for this case have not been ingested yet. Ingest starter datasets to populate.</p>
                </div>`;
                return;
            }

            container.innerHTML = filtered.slice(0, 3).map(r => `
                <div class="comparison-item" style="border-left: 4px solid var(--primary);">
                    <div class="comparison-header">
                        <span class="tag tag-${r.relationship.toLowerCase()}">${r.relationship}</span>
                        <span style="font-size: 0.8rem; color: var(--text-muted);">Confidence: ${(r.confidence * 100).toFixed(0)}%</span>
                    </div>
                    <div class="comparison-body">
                        <div class="side-box">
                            <strong>${r.fa_subject}</strong>
                            <div>${r.fa_value} ${r.fa_unit || ''} | ${r.fa_period || 'N/A'}</div>
                            <div style="font-size: 0.75rem; color: var(--text-muted);">📄 ${r.fa_doc} p.${r.fa_page}</div>
                        </div>
                        <div class="vs-indicator">↔</div>
                        <div class="side-box">
                            <strong>${r.fb_subject}</strong>
                            <div>${r.fb_value} ${r.fb_unit || ''} | ${r.fb_period || 'N/A'}</div>
                            <div style="font-size: 0.75rem; color: var(--text-muted);">📄 ${r.fb_doc} p.${r.fb_page}</div>
                        </div>
                    </div>
                    <div class="reason-box"><strong>Epistemic Reasoning:</strong> ${r.reason}</div>
                </div>
            `).join('');
        }

        async function loadDocuments() {
            const res = await fetch('/documents');
            const docs = await res.json();
            const container = document.getElementById('documentsList');
            if (docs.length === 0) {
                container.innerHTML = '<p style="color: var(--text-muted);">No documents ingested yet.</p>';
                return;
            }

            container.innerHTML = `
                <table style="width: 100%; border-collapse: collapse; font-size: 0.85rem;">
                    <thead>
                        <tr style="border-bottom: 2px solid var(--border); text-align: left;">
                            <th style="padding: 0.75rem;">Filename</th>
                            <th style="padding: 0.75rem;">Total Pages</th>
                            <th style="padding: 0.75rem;">Candidate Pages</th>
                            <th style="padding: 0.75rem;">Facts Extracted</th>
                            <th style="padding: 0.75rem;">Processing Time</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${docs.map(d => `
                            <tr style="border-bottom: 1px solid var(--border);">
                                <td style="padding: 0.75rem;"><strong>${d.filename}</strong></td>
                                <td style="padding: 0.75rem;">${d.total_pages}</td>
                                <td style="padding: 0.75rem;">${d.candidate_pages_count} (${(d.candidate_rate * 100).toFixed(1)}%)</td>
                                <td style="padding: 0.75rem;">${d.facts_count}</td>
                                <td style="padding: 0.75rem;">${d.processing_time_ms} ms</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        }

        async function handleUpload() {
            const input = document.getElementById('pdfFileInput');
            const status = document.getElementById('uploadStatus');
            if (!input.files || input.files.length === 0) {
                alert('Please select at least one PDF file.');
                return;
            }

            status.innerHTML = '<p style="color: var(--primary);">⏳ Ingesting and processing PDF(s)...</p>';
            const formData = new FormData();
            for (let i = 0; i < input.files.length; i++) {
                formData.append('files', input.files[i]);
            }

            try {
                const res = await fetch('/upload', { method: 'POST', body: formData });
                const result = await res.json();
                if (!res.ok) throw new Error(result.detail || 'Upload failed');
                
                status.innerHTML = `<p style="color: #166534; font-weight: 600;">✓ Successfully processed ${result.processed_count} document(s)!</p>`;
                fetchStats();
                loadFacts();
                loadRelationships();
                loadDocuments();
            } catch(e) {
                status.innerHTML = `<p style="color: #991b1b; font-weight: 600;">✗ Error: ${e.message}</p>`;
            }
        }

        // Initial load
        fetchStats();
        loadFacts();
        loadRelationships();
        loadDocuments();
    </script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def index_ui():
    return HTMLResponse(content=HTML_UI)


@app.get("/ui", response_class=HTMLResponse)
def ui_redirect():
    return HTMLResponse(content=HTML_UI)
