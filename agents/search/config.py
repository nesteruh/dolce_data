"""
config.py -- Central configuration for Smart Search V2.
All tuneable constants live here; every other module imports from this file.
"""

import os
from pathlib import Path

# ── Storage paths ─────────────────────────────────────────────────────────────
_BASE = Path(__file__).parent

# ChromaDB persistent storage (child chunks + embeddings)
DB_PATH: str = str(_BASE / "db_storage")

# SQLite parent document store
SQLITE_PATH: str = str(_BASE / "db_storage" / "document_store.db")

# ── ChromaDB collection ───────────────────────────────────────────────────────
COLLECTION_NAME: str = "smart_search_children"

# ── Ollama embeddings ─────────────────────────────────────────────────────────
OLLAMA_MODEL: str = "nomic-embed-text"
OLLAMA_URL: str = "http://localhost:11434"

# ── Ollama LLM (snippet generation + reranking via OpenAI-compatible endpoint)
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_API_KEY: str  = os.getenv("OLLAMA_API_KEY",  "ollama")
SNIPPET_MODEL: str   = os.getenv("AGENT_MODEL",     "llama3.2")

# ── Pre-computed intelligence (summarization + PII detection at index time) ────
# Model used for summarization and PII detection. Defaults to the same LLM
# used for reranking/snippets, but can be overridden independently.
SUMMARIZER_MODEL: str  = os.getenv("SUMMARIZER_MODEL", SNIPPET_MODEL)
# Max characters of document text sent to the LLM for summarization.
SUMMARY_MAX_CHARS: int = 4000
# Max characters sent to the LLM for PII detection (slightly wider window).
PII_MAX_CHARS: int     = 6000

# ── Chunking ──────────────────────────────────────────────────────────────────
CHILD_CHUNK_SIZE: int = 1200   # chars (~300 tokens)
CHILD_OVERLAP: int    = 200

# ── LLM Reranker ─────────────────────────────────────────────────────────────
# How many vector candidates to fetch before passing to the LLM for reranking.
RERANKER_CANDIDATES: int = 10

# ── Watchdog ──────────────────────────────────────────────────────────────────
DEBOUNCE_SECONDS: float = 5.0

# ── Supported file extensions ─────────────────────────────────────────────────
SUPPORTED_EXTENSIONS: set = {".pdf", ".docx", ".pptx", ".xlsx", ".md", ".txt"}
