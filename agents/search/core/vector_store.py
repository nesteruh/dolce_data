"""
core/vector_store.py — ChromaDB wrapper for storing and querying document chunks.

How the embedding pipeline works:
  1. Text chunks are passed to ChromaDB's OllamaEmbeddingFunction.
  2. That function calls the local Ollama HTTP API (/api/embeddings) to generate
     vector embeddings using the `nomic-embed-text` model.
  3. ChromaDB stores the embeddings in a local SQLite-backed file under DB_PATH.
  4. At query time, the same function embeds the query string and ChromaDB finds
     the nearest neighbours by cosine similarity.

Everything stays 100% offline — no cloud calls are made at any point.
"""

import hashlib
import logging
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction

from config import (
    CHUNK_SIZE,        # imported only so config is the single source of truth
    COLLECTION_NAME,
    DB_PATH,
    OLLAMA_MODEL,
    OLLAMA_URL,
)

logger = logging.getLogger(__name__)


class VectorStore:
    """Thin wrapper around a ChromaDB persistent collection."""

    def __init__(self) -> None:
        # Ensure the storage directory exists before ChromaDB tries to open it.
        db_path = Path(DB_PATH)
        db_path.mkdir(parents=True, exist_ok=True)

        # PersistentClient writes everything to disk under DB_PATH.
        self._client = chromadb.PersistentClient(path=str(db_path))

        # Wire up the local Ollama embedding function.
        # ChromaDB will call this automatically whenever it needs to embed text.
        self._embedding_fn = OllamaEmbeddingFunction(
            model_name=OLLAMA_MODEL,
            url=OLLAMA_URL,
        )

        # get_or_create_collection is idempotent — safe to call on every startup.
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},  # cosine similarity for semantic search
        )

        logger.info(
            "VectorStore ready — collection '%s' has %d documents.",
            COLLECTION_NAME,
            self._collection.count(),
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def add_chunks(self, chunks: list[dict]) -> None:
        """
        Upsert a list of text chunks into the ChromaDB collection.

        Uses deterministic IDs (sha256 of file_path + chunk index) so that
        re-indexing the same file never creates duplicate entries.

        Args:
            chunks: List of dicts returned by chunker.chunk_text().
                    Each dict must have keys "text_chunk" and "metadata".
        """
        if not chunks:
            return

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []

        for i, chunk in enumerate(chunks):
            # Build a stable ID that is unique per (file, chunk_position).
            raw_id = f"{chunk['metadata']['file_path']}::{i}"
            chunk_id = hashlib.sha256(raw_id.encode()).hexdigest()

            ids.append(chunk_id)
            documents.append(chunk["text_chunk"])
            metadatas.append(chunk["metadata"])

        # upsert: insert new, overwrite existing — handles re-indexing gracefully.
        self._collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )

    def search(self, query_text: str, n_results: int = 5) -> dict:
        """
        Embed the query and return the most similar chunks from the collection.

        Args:
            query_text: Natural language search string from the user.
            n_results:  How many chunk matches to retrieve.

        Returns:
            Raw ChromaDB result dict with keys: ids, documents, metadatas,
            distances. The caller (searcher.py) extracts what it needs.
        """
        # Clamp n_results to the number of stored documents to avoid ChromaDB errors.
        stored = self._collection.count()
        if stored == 0:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        n = min(n_results, stored)
        return self._collection.query(
            query_texts=[query_text],
            n_results=n,
        )
