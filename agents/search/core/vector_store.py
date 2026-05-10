"""
core/vector_store.py -- ChromaDB wrapper for semantic search.

Pure vector search using Ollama (nomic-embed-text) embeddings.
BM25 / hybrid search removed -- ranking quality is handled by the LLM
reranker in retrieval/searcher.py.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

from config import COLLECTION_NAME, DB_PATH, OLLAMA_MODEL, OLLAMA_URL

logger = logging.getLogger(__name__)


class VectorStore:
    """ChromaDB wrapper: add, delete, and search child chunks by embedding."""

    def __init__(self) -> None:
        Path(DB_PATH).mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=DB_PATH)
        self._embedding_fn = OllamaEmbeddingFunction(
            model_name=OLLAMA_MODEL,
            url=OLLAMA_URL,
        )
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "VectorStore ready -- collection '%s' has %d chunks.",
            COLLECTION_NAME,
            self._collection.count(),
        )

    # ── Indexing ───────────────────────────────────────────────────────────────

    def add_chunks(self, chunks: list[dict]) -> None:
        """
        Upsert child chunks into ChromaDB.
        Each chunk dict must have keys 'text_chunk' and 'metadata'.
        IDs are deterministic (sha256) so re-indexing is safe.
        """
        if not chunks:
            return

        ids, documents, metadatas = [], [], []
        for i, chunk in enumerate(chunks):
            raw_id = f"{chunk['metadata']['file_path']}::{chunk['metadata'].get('chunk_index', i)}"
            ids.append(hashlib.sha256(raw_id.encode()).hexdigest())
            documents.append(chunk["text_chunk"])
            metadatas.append(chunk["metadata"])

        self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    def delete_by_parent(self, parent_id: str) -> None:
        """Remove all child chunks belonging to a parent document."""
        results = self._collection.get(where={"parent_id": {"$eq": parent_id}})
        if results["ids"]:
            self._collection.delete(ids=results["ids"])
            logger.info("Deleted %d chunks for parent %s", len(results["ids"]), parent_id[:8])

    # ── Search ─────────────────────────────────────────────────────────────────

    def search(
        self, query: str, n: int, where: dict | None = None
    ) -> list[dict]:
        """
        Embed the query and retrieve the top-n most similar chunks.

        Args:
            query: Natural language query string.
            n:     Number of chunk results to retrieve.
            where: Optional ChromaDB metadata filter dict.
                   Example: {"file_extension": {"$eq": ".pdf"}}

        Returns:
            List of chunk dicts: {chunk_id, text, metadata, distance}
            Sorted best-first (lowest cosine distance).
        """
        stored = self._collection.count()
        if stored == 0:
            return []

        n = min(n, stored)
        kwargs: dict = {"query_texts": [query], "n_results": n}
        if where:
            kwargs["where"] = where

        raw = self._collection.query(**kwargs)

        chunks = []
        for chunk_id, text, meta, dist in zip(
            raw["ids"][0], raw["documents"][0], raw["metadatas"][0], raw["distances"][0]
        ):
            chunks.append({
                "chunk_id": chunk_id,
                "text":     text,
                "metadata": meta,
                "distance": dist,
            })

        return chunks
