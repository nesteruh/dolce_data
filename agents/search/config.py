"""
config.py — Central configuration for the Smart Search Engine.
All tuneable constants live here; every other module imports from this file.
"""

from pathlib import Path

# ── Vector store ──────────────────────────────────────────────────────────────
# Path where ChromaDB will persist its SQLite files.
# Using pathlib so the path works identically on Windows, macOS, and Linux.
DB_PATH: str = str(Path(__file__).parent / "db_storage")

# Name of the ChromaDB collection that holds all indexed document chunks.
COLLECTION_NAME: str = "local_files"

# ── Ollama (local embeddings) ─────────────────────────────────────────────────
OLLAMA_MODEL: str = "nomic-embed-text"
OLLAMA_URL: str = "http://localhost:11434"

# ── Chunking ──────────────────────────────────────────────────────────────────
# Maximum number of characters per chunk (not tokens).
CHUNK_SIZE: int = 500

# Number of characters that consecutive chunks share (sliding window overlap).
CHUNK_OVERLAP: int = 50
