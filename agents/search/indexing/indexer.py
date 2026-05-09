"""
indexing/indexer.py — Walk a directory and index all supported files.

Pipeline per file:
  extract_text_from_file  →  chunk_text  →  VectorStore.add_chunks
"""

import logging
from pathlib import Path

from core.extractor import extract_text_from_file
from core.chunker import chunk_text
from core.vector_store import VectorStore

logger = logging.getLogger(__name__)

# File extensions the extractor can handle.
SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".docx"}


def build_index(directory_path: str) -> None:
    """
    Index all supported documents in a directory (non-recursive, flat only).

    Args:
        directory_path: Path to the folder to index.
                        Accepts any OS path format — uses pathlib internally.
    """
    target_dir = Path(directory_path).resolve()

    if not target_dir.is_dir():
        print(f"[ERROR] '{target_dir}' is not a valid directory.")
        return

    # Collect only the supported file types (flat, no sub-directories).
    files = [
        f for f in target_dir.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not files:
        print(f"[INFO] No supported files (.txt, .pdf, .docx) found in '{target_dir}'.")
        return

    total = len(files)
    print(f"[INFO] Found {total} file(s) to index in '{target_dir}'.\n")

    store = VectorStore()

    indexed = 0
    skipped = 0

    for i, file_path in enumerate(files, start=1):
        print(f"[{i}/{total}] Indexing: {file_path.name} ...", end=" ", flush=True)

        # Step 1 — Extract raw text.
        text = extract_text_from_file(str(file_path))
        if text is None:
            print("SKIPPED (extraction failed or unsupported)")
            skipped += 1
            continue

        if not text.strip():
            print("SKIPPED (empty document)")
            skipped += 1
            continue

        # Step 2 — Split into chunks.
        chunks = chunk_text(text, str(file_path))
        if not chunks:
            print("SKIPPED (no chunks generated)")
            skipped += 1
            continue

        # Step 3 — Store in ChromaDB (embeddings generated via Ollama here).
        store.add_chunks(chunks)
        print(f"OK ({len(chunks)} chunk(s))")
        indexed += 1

    print(f"\n[DONE] Indexed {indexed} file(s), skipped {skipped}.")
