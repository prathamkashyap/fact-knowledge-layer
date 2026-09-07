"""
SQLite persistence layer for the Superjoin Fact Knowledge Layer.
Stores:
- Ingested documents and page metadata
- Grounded extracted facts and evidence links
- Normalized and canonicalized fact attributes
- Cross-document fact comparisons, reasoning traces, and dimension differences
"""

import json
import sqlite3
import os
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.models import Fact, Evidence, FactComparison, Dimensions
from app.pdf_parser import DocumentIngestionResult, PageObject


DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "fact_layer.db")


class Database:
    """Thread-safe SQLite storage for the Fact Knowledge Layer."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        with self.conn:
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                total_pages INTEGER NOT NULL,
                candidate_pages_count INTEGER NOT NULL,
                candidate_rate REAL NOT NULL,
                processing_time_ms REAL NOT NULL,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)

            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                raw_text TEXT NOT NULL,
                text_length INTEGER NOT NULL,
                blocks_count INTEGER NOT NULL,
                has_tables INTEGER NOT NULL,
                score_breakdown_json TEXT NOT NULL,
                is_candidate INTEGER NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
            );
            """)

            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                document_name TEXT,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                value_raw TEXT NOT NULL,
                value_numeric REAL,
                unit TEXT,
                period TEXT,
                as_of TEXT,
                scope TEXT,
                qualifiers_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                evidence_text TEXT NOT NULL,
                canonical_subject TEXT,
                canonical_predicate TEXT,
                normalized_value_raw TEXT,
                normalized_value_numeric REAL,
                normalized_unit TEXT,
                normalized_period TEXT,
                period_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
            );
            """)

            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS fact_comparisons (
                id TEXT PRIMARY KEY,
                fact_a_id TEXT NOT NULL,
                fact_b_id TEXT NOT NULL,
                relationship TEXT NOT NULL,
                confidence REAL NOT NULL,
                reason TEXT NOT NULL,
                dimensions_json TEXT NOT NULL,
                reasoning_mode TEXT NOT NULL DEFAULT 'heuristic',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (fact_a_id) REFERENCES facts (id) ON DELETE CASCADE,
                FOREIGN KEY (fact_b_id) REFERENCES facts (id) ON DELETE CASCADE
            );
            """)

            # Create search and lookup indices
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_doc ON pages (document_id);")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_doc ON facts (document_id);")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts (canonical_subject);")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts (canonical_predicate);")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_comparisons_rel ON fact_comparisons (relationship);")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_comparisons_fa ON fact_comparisons (fact_a_id);")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_comparisons_fb ON fact_comparisons (fact_b_id);")

    # -----------------------------------------------------------------------
    # Document Methods
    # -----------------------------------------------------------------------

    def save_document(self, doc: DocumentIngestionResult):
        with self.conn:
            self.conn.execute("""
            INSERT OR REPLACE INTO documents 
            (id, filename, filepath, total_pages, candidate_pages_count, candidate_rate, processing_time_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (
                doc.document_id,
                doc.filename,
                doc.filepath,
                doc.total_pages,
                doc.candidate_pages_count,
                doc.candidate_rate,
                doc.processing_time_ms,
            ))

            for page in doc.pages:
                page_id = f"{doc.document_id}_p{page.page_number}"
                self.conn.execute("""
                INSERT OR REPLACE INTO pages
                (id, document_id, page_number, raw_text, text_length, blocks_count, has_tables, score_breakdown_json, is_candidate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    page_id,
                    doc.document_id,
                    page.page_number,
                    page.raw_text,
                    page.text_length,
                    page.blocks_count,
                    1 if page.has_tables else 0,
                    page.score_breakdown.model_dump_json(),
                    1 if page.score_breakdown.is_candidate else 0,
                ))

    def get_documents(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT d.*, 
               (SELECT COUNT(*) FROM facts f WHERE f.document_id = d.id) as facts_count
        FROM documents d
        ORDER BY d.uploaded_at DESC;
        """)
        return [dict(row) for row in cursor.fetchall()]

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT d.*, 
               (SELECT COUNT(*) FROM facts f WHERE f.document_id = d.id) as facts_count
        FROM documents d
        WHERE d.id = ?;
        """, (doc_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_document_pages(self, doc_id: str) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
        SELECT * FROM pages WHERE document_id = ? ORDER BY page_number ASC;
        """, (doc_id,))
        rows = cursor.fetchall()
        pages = []
        for r in rows:
            d = dict(r)
            d["score_breakdown"] = json.loads(d["score_breakdown_json"])
            pages.append(d)
        return pages

    # -----------------------------------------------------------------------
    # Fact Methods
    # -----------------------------------------------------------------------

    def save_fact(self, fact: Fact):
        self.save_facts([fact])

    def save_facts(self, facts: List[Fact]):
        with self.conn:
            for f in facts:
                val_raw = str(f.value)
                val_num = float(f.value) if isinstance(f.value, (int, float)) else None
                norm_raw = str(f.normalized_value) if f.normalized_value is not None else None
                norm_num = float(f.normalized_value) if isinstance(f.normalized_value, (int, float)) else None

                self.conn.execute("""
                INSERT OR REPLACE INTO facts
                (id, document_id, page_number, document_name, subject, predicate, value_raw, value_numeric,
                 unit, period, as_of, scope, qualifiers_json, confidence, evidence_text,
                 canonical_subject, canonical_predicate, normalized_value_raw, normalized_value_numeric,
                 normalized_unit, normalized_period, period_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    f.id,
                    f.evidence.document_id,
                    f.evidence.page_number,
                    f.evidence.document_name,
                    f.subject,
                    f.predicate,
                    val_raw,
                    val_num,
                    f.unit,
                    f.period,
                    f.as_of,
                    f.scope,
                    json.dumps(f.qualifiers),
                    f.confidence,
                    f.evidence.text,
                    f.canonical_subject,
                    f.canonical_predicate,
                    norm_raw,
                    norm_num,
                    f.normalized_unit,
                    f.normalized_period,
                    f.period_type,
                ))

    def _row_to_fact(self, row: sqlite3.Row) -> Fact:
        val_raw = row["value_raw"]
        val_num = row["value_numeric"]
        value: Any = val_num if val_num is not None else val_raw

        norm_raw = row["normalized_value_raw"]
        norm_num = row["normalized_value_numeric"]
        norm_value: Any = norm_num if norm_num is not None else norm_raw

        return Fact(
            id=row["id"],
            subject=row["subject"],
            predicate=row["predicate"],
            value=value,
            unit=row["unit"],
            period=row["period"],
            as_of=row["as_of"],
            scope=row["scope"],
            qualifiers=json.loads(row["qualifiers_json"]),
            evidence=Evidence(
                document_id=row["document_id"],
                page_number=row["page_number"],
                text=row["evidence_text"],
                document_name=row["document_name"],
            ),
            confidence=row["confidence"],
            canonical_subject=row["canonical_subject"],
            canonical_predicate=row["canonical_predicate"],
            normalized_value=norm_value,
            normalized_unit=row["normalized_unit"],
            normalized_period=row["normalized_period"],
            period_type=row["period_type"],
        )

    def get_facts(
        self,
        document_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Fact]:
        query = "SELECT * FROM facts WHERE 1=1"
        params: List[Any] = []

        if document_id:
            query += " AND document_id = ?"
            params.append(document_id)

        if search:
            search_wild = f"%{search.strip().lower()}%"
            query += """ AND (
                LOWER(subject) LIKE ? OR
                LOWER(predicate) LIKE ? OR
                LOWER(value_raw) LIKE ? OR
                LOWER(evidence_text) LIKE ? OR
                LOWER(canonical_subject) LIKE ? OR
                LOWER(canonical_predicate) LIKE ?
            )"""
            params.extend([search_wild] * 6)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        return [self._row_to_fact(r) for r in cursor.fetchall()]

    def get_fact(self, fact_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM facts WHERE id = ?;", (fact_id,))
        row = cursor.fetchone()
        if not row:
            return None

        fact = self._row_to_fact(row)
        fact_dict = fact.model_dump()

        # Fetch associated relationships
        cursor.execute("""
        SELECT * FROM fact_comparisons
        WHERE fact_a_id = ? OR fact_b_id = ?
        ORDER BY confidence DESC;
        """, (fact_id, fact_id))

        comparisons = []
        for crow in cursor.fetchall():
            cdict = dict(crow)
            cdict["dimensions"] = json.loads(cdict["dimensions_json"])
            other_id = cdict["fact_b_id"] if cdict["fact_a_id"] == fact_id else cdict["fact_a_id"]
            other_row = self.conn.execute("SELECT * FROM facts WHERE id = ?;", (other_id,)).fetchone()
            cdict["related_fact"] = self._row_to_fact(other_row).model_dump() if other_row else None
            comparisons.append(cdict)

        fact_dict["relationships"] = comparisons
        return fact_dict

    # -----------------------------------------------------------------------
    # Comparison / Relationship Methods
    # -----------------------------------------------------------------------

    def save_comparison(self, comp: FactComparison, reasoning_mode: str = "heuristic"):
        self.save_comparisons([comp], reasoning_mode=reasoning_mode)

    def save_comparisons(self, comps: List[FactComparison], reasoning_mode: str = "heuristic"):
        with self.conn:
            for c in comps:
                comp_id = f"{c.fact_a_id}_{c.fact_b_id}"
                self.conn.execute("""
                INSERT OR REPLACE INTO fact_comparisons
                (id, fact_a_id, fact_b_id, relationship, confidence, reason, dimensions_json, reasoning_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    comp_id,
                    c.fact_a_id,
                    c.fact_b_id,
                    c.relationship,
                    c.confidence,
                    c.reason,
                    c.dimensions.model_dump_json(),
                    reasoning_mode,
                ))

    def get_relationships(
        self,
        relationship: Optional[str] = None,
        fact_id: Optional[str] = None,
        document_id: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        query = """
        SELECT c.*,
               fa.subject as fa_subject, fa.predicate as fa_predicate, fa.value_raw as fa_value,
               fa.unit as fa_unit, fa.period as fa_period, fa.scope as fa_scope,
               fa.document_name as fa_doc, fa.page_number as fa_page, fa.evidence_text as fa_evidence,
               fa.document_id as fa_doc_id,
               fb.subject as fb_subject, fb.predicate as fb_predicate, fb.value_raw as fb_value,
               fb.unit as fb_unit, fb.period as fb_period, fb.scope as fb_scope,
               fb.document_name as fb_doc, fb.page_number as fb_page, fb.evidence_text as fb_evidence,
               fb.document_id as fb_doc_id
        FROM fact_comparisons c
        JOIN facts fa ON c.fact_a_id = fa.id
        JOIN facts fb ON c.fact_b_id = fb.id
        WHERE 1=1
        """
        params: List[Any] = []

        if relationship:
            query += " AND c.relationship = ?"
            params.append(relationship.strip().upper())

        if fact_id:
            query += " AND (c.fact_a_id = ? OR c.fact_b_id = ?)"
            params.extend([fact_id, fact_id])

        if document_id:
            query += " AND (fa.document_id = ? OR fb.document_id = ?)"
            params.extend([document_id, document_id])

        query += " ORDER BY c.confidence DESC, c.created_at DESC LIMIT ?;"
        params.append(limit)

        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["dimensions"] = json.loads(d["dimensions_json"])
            results.append(d)
        return results

    # -----------------------------------------------------------------------
    # System Stats
    # -----------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM documents;")
        total_docs = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM pages;")
        total_pages = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM facts;")
        total_facts = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM fact_comparisons;")
        total_comparisons = cursor.fetchone()[0]

        cursor.execute("SELECT relationship, COUNT(*) FROM fact_comparisons GROUP BY relationship;")
        rel_counts = {row[0]: row[1] for row in cursor.fetchall()}

        return {
            "total_documents": total_docs,
            "total_pages": total_pages,
            "total_facts": total_facts,
            "total_relationships": total_comparisons,
            "relationship_breakdown": rel_counts,
        }

    def close(self):
        self.conn.close()
