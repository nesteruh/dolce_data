"""
indexing/indexer.py -- V2 indexing pipeline.

Pipeline per file:
    extract_document()
        -> DocumentStore.upsert_document()   (save parent + full text to SQLite)
        -> make_child_chunks()               (recursive chunker)
        -> VectorStore.add_chunks()          (embed + store in ChromaDB)

Two entry points:
    build_index(directory_path)  -- full directory scan (CLI / first-run)
    index_file(file_path)        -- single file (used by the watcher)
"""

import logging
from pathlib import Path

from core.document_store import DocumentStore
from core.extractor import extract_document
from core.chunker import make_child_chunks
from core.vector_store import VectorStore
from config import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

_IGNORE_PREFIXES = ("~$", ".", "_")


def _should_index(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
        and not path.name.startswith(_IGNORE_PREFIXES)
    )


# Shared store instances reused across calls in the same process.
_doc_store: DocumentStore | None = None
_vec_store: VectorStore | None = None


def _get_stores() -> tuple[DocumentStore, VectorStore]:
    global _doc_store, _vec_store
    if _doc_store is None:
        _doc_store = DocumentStore()
    if _vec_store is None:
        _vec_store = VectorStore()
    return _doc_store, _vec_store


# ── Public API ─────────────────────────────────────────────────────────────────

def index_file(file_path: str) -> bool:
    """
    Index a single file. Safe to call repeatedly -- upsert handles updates.
    Returns True if indexing succeeded, False if skipped.
    """
    path = Path(file_path).resolve()
    if not _should_index(path):
        logger.info("Skipping (unsupported or temp): %s", path.name)
        return False

    doc_store, vec_store = _get_stores()

    doc = extract_document(str(path))
    if doc is None or not doc.get("full_text", "").strip():
        logger.warning("No text extracted from '%s', skipping.", path.name)
        return False

    doc_id = doc_store.upsert_document(doc)
    doc["doc_id"] = doc_id

    chunks = make_child_chunks(doc["full_text"], doc)
    if not chunks:
        logger.warning("No chunks produced for '%s', skipping.", path.name)
        return False

    vec_store.add_chunks(chunks)
    logger.info("Indexed '%s' (%d chunks).", path.name, len(chunks))
    return True


def build_index(directory_path: str) -> None:
    """Index all supported files in a directory (flat, non-recursive)."""
    target_dir = Path(directory_path).resolve()

    if not target_dir.is_dir():
        print(f"[ERROR] '{target_dir}' is not a valid directory.")
        return

    files = [f for f in target_dir.iterdir() if _should_index(f)]

    if not files:
        print(f"[INFO] No supported files found in '{target_dir}'.")
        return

    total = len(files)
    print(f"[INFO] Found {total} file(s) to index in '{target_dir}'.\n")

    doc_store, vec_store = _get_stores()
    indexed = skipped = 0

    for i, file_path in enumerate(files, start=1):
        print(f"[{i}/{total}] Indexing: {file_path.name} ...", end=" ", flush=True)

        doc = extract_document(str(file_path))
        if doc is None or not doc.get("full_text", "").strip():
            print("SKIPPED (no text extracted)")
            skipped += 1
            continue

        doc_id = doc_store.upsert_document(doc)
        doc["doc_id"] = doc_id

        chunks = make_child_chunks(doc["full_text"], doc)
        if not chunks:
            print("SKIPPED (no chunks)")
            skipped += 1
            continue

        vec_store.add_chunks(chunks)
        print(f"OK ({len(chunks)} chunk(s))")
        indexed += 1

    print(f"\n[DONE] Indexed {indexed} file(s), skipped {skipped}.")


def remove_file(file_path: str) -> None:
    """Remove a file from both stores (called by the watcher on delete events)."""
    doc_store, vec_store = _get_stores()
    doc_id = doc_store.get_doc_id(file_path)
    if doc_id:
        vec_store.delete_by_parent(doc_id)
        doc_store.delete_document(file_path)
        logger.info("Removed '%s' from index.", Path(file_path).name)
