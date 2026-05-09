"""
core/document_store.py -- SQLite parent document store.

Stores the full text and rich metadata for every indexed file (the "Parent"
in the Parent-Child architecture). ChromaDB holds the small child chunks;
this store holds the source of truth for context retrieval and BM25 corpus.

Schema (table: documents)
    doc_id          TEXT PRIMARY KEY  -- sha256(absolute_file_path)
    file_path       TEXT              -- absolute OS-native path
    file_name       TEXT
    file_extension  TEXT
    file_size       INTEGER           -- bytes
    creation_date   TEXT              -- ISO-8601
    modified_date   TEXT              -- ISO-8601
    parent_folder   TEXT              -- immediate parent directory name
    full_text       TEXT              -- complete extracted text
    indexed_at      TEXT              -- ISO-8601 of last index run
"""

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import SQLITE_PATH

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id          TEXT PRIMARY KEY,
    file_path       TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    file_extension  TEXT NOT NULL,
    file_size       INTEGER NOT NULL DEFAULT 0,
    creation_date   TEXT NOT NULL DEFAULT '',
    modified_date   TEXT NOT NULL DEFAULT '',
    parent_folder   TEXT NOT NULL DEFAULT '',
    full_text       TEXT NOT NULL DEFAULT '',
    indexed_at      TEXT NOT NULL DEFAULT ''
);
"""


def _make_doc_id(file_path: str) -> str:
    """Stable, collision-safe ID derived from the absolute file path."""
    return hashlib.sha256(str(Path(file_path).resolve()).encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DocumentStore:
    """SQLite-backed parent document store. Thread-safe via per-call connections."""

    def __init__(self) -> None:
        # Ensure storage directory exists.
        Path(SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
        self._path = SQLITE_PATH
        self._init_db()

    # ── Setup ──────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Write ──────────────────────────────────────────────────────────────────

    def upsert_document(self, doc: dict) -> str:
        """
        Insert or replace a document record. Returns the doc_id.

        Required keys in `doc`: file_path, file_name, file_extension,
        file_size, creation_date, modified_date, parent_folder, full_text.
        """
        doc_id = _make_doc_id(doc["file_path"])
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO documents
                    (doc_id, file_path, file_name, file_extension,
                     file_size, creation_date, modified_date,
                     parent_folder, full_text, indexed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    file_path=excluded.file_path,
                    file_name=excluded.file_name,
                    file_extension=excluded.file_extension,
                    file_size=excluded.file_size,
                    creation_date=excluded.creation_date,
                    modified_date=excluded.modified_date,
                    parent_folder=excluded.parent_folder,
                    full_text=excluded.full_text,
                    indexed_at=excluded.indexed_at
                """,
                (
                    doc_id,
                    str(doc["file_path"]),
                    str(doc["file_name"]),
                    str(doc["file_extension"]),
                    int(doc.get("file_size", 0)),
                    str(doc.get("creation_date", "")),
                    str(doc.get("modified_date", "")),
                    str(doc.get("parent_folder", "")),
                    str(doc.get("full_text", "")),
                    _now_iso(),
                ),
            )
        return doc_id

    def delete_document(self, file_path: str) -> None:
        """Remove a document (and its metadata) by file path."""
        doc_id = _make_doc_id(file_path)
        with self._connect() as conn:
            conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        logger.info("Deleted document: %s", file_path)

    # ── Read ───────────────────────────────────────────────────────────────────

    def get_document(self, doc_id: str) -> dict | None:
        """Fetch a document record by doc_id. Returns None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_doc_id(self, file_path: str) -> str | None:
        """Return the doc_id for a given file_path, or None if not indexed."""
        doc_id = _make_doc_id(file_path)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT doc_id FROM documents WHERE doc_id = ?", (doc_id,)
            ).fetchone()
        return doc_id if row else None

    def get_all_texts(self) -> list[dict]:
        """
        Return all documents as lightweight dicts for BM25 corpus building.
        Each dict: {doc_id, file_path, file_name, file_extension,
                    parent_folder, file_size, creation_date, full_text}
        """
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT doc_id, file_path, file_name, file_extension,
                          parent_folder, file_size, creation_date, full_text
                   FROM documents"""
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
