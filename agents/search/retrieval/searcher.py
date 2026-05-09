"""
retrieval/searcher.py — Semantic file search using the VectorStore.

Takes a natural language query, returns a deduplicated list of matching
file paths ordered by relevance (best match first).
"""

import logging
from core.vector_store import VectorStore

logger = logging.getLogger(__name__)


def search_files(user_query: str, n_results: int = 5) -> list[str]:
    """
    Find files relevant to `user_query` using semantic similarity.

    Args:
        user_query: A natural language search string (e.g. "tax documents 2024").
        n_results:  Maximum number of chunk hits to retrieve from ChromaDB.
                    Since multiple chunks may belong to the same file, the
                    returned file list can be shorter than n_results.

    Returns:
        Deduplicated list of file path strings, ordered by first-seen relevance.
        Returns an empty list if no matches are found.
    """
    store = VectorStore()
    results = store.search(user_query, n_results=n_results)

    # ChromaDB wraps results in an extra list because it supports batched queries.
    # We only ever send one query at a time, so index [0] is always our result.
    metadatas: list[dict] = results.get("metadatas", [[]])[0]

    if not metadatas:
        return []

    # Deduplicate file paths while preserving relevance order.
    seen: set[str] = set()
    unique_paths: list[str] = []

    for meta in metadatas:
        fp = meta.get("file_path", "")
        if fp and fp not in seen:
            seen.add(fp)
            unique_paths.append(fp)

    return unique_paths
