"""
retrieval/searcher.py -- Vector search + LLM reranking orchestrator + Agent Tools.

Pipeline (existing)
-------------------
1. VectorStore.search() fetches RERANKER_CANDIDATES chunks from ChromaDB.
2. Results are deduplicated to one entry per file (best matching chunk kept).
3. _llm_rerank() sends all candidates to llama3.2 in ONE prompt.
   - The LLM outputs ONLY a comma-separated list of document numbers in
     relevance order (e.g. "3,1,7,2"). This format is parsed with a simple
     regex -- immune to JSON syntax errors from the LLM.
   - Falls back to original vector-distance order if the LLM fails.
4. search_files()         -> reranked file paths (no snippets)
4. search_with_snippets() -> reranked paths + per-result LLM summaries

Agent Tools (new)
-----------------
5. fast_filename_search()    -> RapidFuzz fuzzy filename search
6. get_relevant_filenames()  -> vector + reranker, filenames only
7. get_document_context()    -> vector chunks formatted within a token budget
8. get_file_analysis()       -> pre-computed summary + PII flag from SQLite

metadata_filter (optional):
    ChromaDB where dict applied during vector search.
    Examples:
        {"file_extension": {"$eq": ".pdf"}}
        {"creation_date":  {"$gte": "2024-01-01"}}
"""

import logging
import os
import re
from pathlib import Path

from openai import OpenAI
from rapidfuzz import fuzz, process as fuzz_process

from config import (
    OLLAMA_API_KEY,
    OLLAMA_BASE_URL,
    RERANKER_CANDIDATES,
    SNIPPET_MODEL,
)
from core.document_store import DocumentStore
from core.vector_store import VectorStore

logger = logging.getLogger(__name__)

_llm_client = OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)

_vec_store: VectorStore | None = None
_doc_store: DocumentStore | None = None


def _get_vec_store() -> VectorStore:
    global _vec_store
    if _vec_store is None:
        _vec_store = VectorStore()
    return _vec_store


def _get_doc_store() -> DocumentStore:
    global _doc_store
    if _doc_store is None:
        _doc_store = DocumentStore()
    return _doc_store


# ── Candidate preparation ──────────────────────────────────────────────────────

def _deduplicate(chunks: list[dict]) -> list[dict]:
    """
    Collapse chunk-level results to one entry per parent file.
    The first (best-distance) chunk for each file is kept as 'best_chunk_text'.
    Returns a list of flat dicts ready for the reranker and output formatters.
    """
    seen: set[str] = set()
    results: list[dict] = []

    for chunk in chunks:
        meta = chunk["metadata"]
        pid  = meta.get("parent_id", "")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        results.append({
            "parent_id":      pid,
            "file_path":      meta.get("file_path", ""),
            "file_name":      meta.get("file_name", ""),
            "file_extension": meta.get("file_extension", ""),
            "parent_folder":  meta.get("parent_folder", ""),
            "creation_date":  meta.get("creation_date", ""),
            "best_chunk_text": chunk["text"],
            "distance":       chunk["distance"],
        })

    return results


# ── LLM Reranker ──────────────────────────────────────────────────────────────

def _llm_rerank(candidates: list[dict], query: str, top_k: int) -> list[dict]:
    """
    Ask the LLM to re-rank candidates by relevance to `query`.

    Prompt design: show each candidate's file name + a short excerpt, then
    ask for ONLY a comma-separated list of numbers in relevance order.
    Example expected output: "3,1,7,2"

    Parsing with re.findall(r'\\d+', ...) makes this immune to any JSON or
    punctuation issues that caused the previous JSON-based reranker to fail.

    Falls back to original vector-distance order on any error.
    """
    if not candidates:
        return []

    lines = [
        f'Search query: "{query}"\n',
        "TASK: Rank the document excerpts below by how well they answer the search query.",
        "Use these STRICT rules when deciding the order:\n",
        "  RANK HIGH   -- the excerpt explicitly and directly discusses the query topic.",
        "               The key terms from the query appear in the text itself.",
        "  RANK MIDDLE -- the excerpt is from the same broad field but only touches",
        "               the topic in passing, or discusses a closely related concept.",
        "  RANK LOW    -- the excerpt does NOT explicitly mention the query terms.",
        "               Being in the same document or field is NOT enough.",
        "  RANK LOWEST -- the excerpt is completely off-topic.\n",
    ]

    for i, cand in enumerate(candidates, 1):
        # Use 600 chars -- enough to detect explicit mention vs. topical adjacency.
        excerpt = (cand.get("best_chunk_text", "") or "").strip()[:600]
        lines.append(f"[{i}] {cand.get('file_name', 'unknown')}")
        lines.append(f"     {excerpt}\n")

    lines.append(
        "Reply with ONLY the document numbers in order from most to least relevant, "
        "separated by commas. Do not include any explanation.\n"
        "Example: 3,1,7,2"
    )

    prompt = "\n".join(lines)

    try:
        response = _llm_client.chat.completions.create(
            model=SNIPPET_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict relevance judge for a document retrieval system. "
                        "Your only job is to rank document excerpts by how explicitly and "
                        "directly they address the search query. "
                        "An excerpt that does NOT contain the specific query terms must be "
                        "ranked BELOW any excerpt that does, even if it is from the same "
                        "field or document. "
                        "Output ONLY a comma-separated list of document numbers. "
                        "Do not output any explanation."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=64,       # just enough for "3,1,7,2,5" -- keeps it tight
            temperature=0.0,
        )

        raw = response.choices[0].message.content.strip()
        logger.info("Reranker raw output: %r", raw)

        # Extract all integers from the response (robust to any surrounding text).
        numbers = [int(n) for n in re.findall(r"\d+", raw)]

        # Validate: only keep numbers in valid range, deduplicate, preserve order.
        seen_n: set[int] = set()
        ranked_indices: list[int] = []
        for n in numbers:
            if 1 <= n <= len(candidates) and n not in seen_n:
                seen_n.add(n)
                ranked_indices.append(n)

        if not ranked_indices:
            raise ValueError(f"No valid numbers found in reranker output: {raw!r}")

        # Append any candidates the LLM didn't mention (ranked last).
        all_indices = set(range(1, len(candidates) + 1))
        for n in sorted(all_indices - seen_n):
            ranked_indices.append(n)

        reranked = [candidates[n - 1] for n in ranked_indices]

        # Attach llm_score (position-based: rank 1 = highest score)
        for pos, cand in enumerate(reranked):
            cand["llm_score"] = float(len(candidates) - pos)

        logger.info(
            "Reranked: %s",
            " > ".join(c.get("file_name", "?") for c in reranked[:top_k]),
        )
        return reranked[:top_k]

    except Exception as exc:
        logger.warning("LLM reranking failed (%s) -- using vector-distance order.", exc)
        for cand in candidates:
            cand["llm_score"] = -1.0
        return candidates[:top_k]


# ── Snippet generation ─────────────────────────────────────────────────────────

def _generate_snippet(query: str, chunk_text: str) -> str:
    """2-3 sentence LLM summary of what chunk_text says about query."""
    excerpt = chunk_text[:2000].strip()
    if not excerpt:
        return ""

    try:
        response = _llm_client.chat.completions.create(
            model=SNIPPET_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise document summariser. "
                        "Answer only with the 2-3 sentence summary, nothing else."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f'Text excerpt:\n"""\n{excerpt}\n"""\n\n'
                        f'In 2-3 sentences, summarise what this excerpt says about: "{query}"'
                    ),
                },
            ],
            max_tokens=150,
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("Snippet generation failed: %s", exc)
        return ""


# ── Public API (existing) ──────────────────────────────────────────────────────

def search_files(
    user_query: str,
    n_results: int = 5,
    metadata_filter: dict | None = None,
) -> list[str]:
    """
    Vector search + LLM reranking. Returns deduplicated file paths, best first.

    Args:
        user_query:      Natural language query.
        n_results:       Number of unique files to return.
        metadata_filter: Optional ChromaDB where-filter dict.
    """
    store = _get_vec_store()
    chunks = store.search(
        user_query,
        n=max(RERANKER_CANDIDATES, n_results * 2),
        where=metadata_filter,
    )

    candidates = _deduplicate(chunks)
    if not candidates:
        return []

    reranked = _llm_rerank(candidates, user_query, top_k=n_results)
    return [r["file_path"] for r in reranked if r.get("file_path")]


def search_with_snippets(
    user_query: str,
    n_results: int = 5,
    metadata_filter: dict | None = None,
) -> list[dict]:
    """
    Vector search + LLM reranking + per-result LLM snippet.

    Returns list of dicts (best match first):
    {
        score_rank, llm_score, file_path, file_name,
        file_extension, parent_folder, creation_date, snippet
    }
    """
    store = _get_vec_store()
    chunks = store.search(
        user_query,
        n=max(RERANKER_CANDIDATES, n_results * 2),
        where=metadata_filter,
    )

    candidates = _deduplicate(chunks)
    if not candidates:
        return []

    reranked = _llm_rerank(candidates, user_query, top_k=n_results)

    output = []
    for rank, result in enumerate(reranked, start=1):
        snippet = _generate_snippet(user_query, result.get("best_chunk_text", ""))
        output.append({
            "score_rank":     rank,
            "llm_score":      result.get("llm_score", -1.0),
            "file_path":      result.get("file_path", ""),
            "file_name":      result.get("file_name", ""),
            "file_extension": result.get("file_extension", ""),
            "parent_folder":  result.get("parent_folder", ""),
            "creation_date":  result.get("creation_date", ""),
            "snippet":        snippet,
        })

    return output


# ── Agent Tools ────────────────────────────────────────────────────────────────

def fast_filename_search(
    query: str,
    folder_path: str | None = None,
    max_results: int = 20,
    score_cutoff: int = 55,
) -> list[dict]:
    """
    Non-AI fuzzy filename search using RapidFuzz.

    When `folder_path` is provided, walks the directory live.
    When omitted, searches only files already indexed in SQLite.

    Args:
        query:        Filename substring or fuzzy query (case-insensitive).
        folder_path:  Optional directory to walk. If None, uses indexed files.
        max_results:  Maximum number of results to return.
        score_cutoff: Minimum RapidFuzz WRatio score (0-100) to include a result.

    Returns:
        List of dicts sorted by score descending:
        [{"file_name": str, "file_path": str, "score": float}]
    """
    # Build the candidate list: [(display_name, file_path), ...]
    candidates: list[tuple[str, str]] = []

    if folder_path:
        root = Path(folder_path).resolve()
        for dirpath, _, filenames in os.walk(str(root)):
            for fn in filenames:
                full = os.path.abspath(os.path.join(dirpath, fn))
                candidates.append((fn, full))
    else:
        # Fall back to indexed files from SQLite
        rows = _get_doc_store().get_all_paths()
        for row in rows:
            candidates.append((row["file_name"], row["file_path"]))

    if not candidates:
        return []

    names = [c[0] for c in candidates]
    path_map = {c[0]: c[1] for c in candidates}

    # RapidFuzz extract returns (match, score, index) tuples
    matches = fuzz_process.extract(
        query,
        names,
        scorer=fuzz.WRatio,
        score_cutoff=score_cutoff,
        limit=max_results,
    )

    results = []
    seen_paths: set[str] = set()
    for name, score, idx in sorted(matches, key=lambda x: x[1], reverse=True):
        fp = candidates[idx][1]
        if fp in seen_paths:
            continue
        seen_paths.add(fp)
        results.append({
            "file_name": name,
            "file_path": fp,
            "score":     round(score, 1),
        })

    return results


def get_relevant_filenames(
    query: str,
    k: int = 5,
    extension_filter: str | None = None,
) -> list[dict]:
    """
    Semantic search on file content with LLM reranking. Returns unique files.

    Args:
        query:            Natural language query.
        k:                Number of unique files to return.
        extension_filter: Optional file extension to restrict results (e.g. ".pdf").

    Returns:
        List of dicts sorted by relevance:
        [{"file_name": str, "file_path": str}]
    """
    where: dict | None = None
    if extension_filter:
        ext = extension_filter if extension_filter.startswith(".") else f".{extension_filter}"
        where = {"file_extension": {"$eq": ext}}

    store = _get_vec_store()
    chunks = store.search(
        query,
        n=max(RERANKER_CANDIDATES, k * 2),
        where=where,
    )

    candidates = _deduplicate(chunks)
    if not candidates:
        return []

    reranked = _llm_rerank(candidates, query, top_k=k)
    return [
        {"file_name": r["file_name"], "file_path": r["file_path"]}
        for r in reranked
        if r.get("file_path")
    ]


def get_document_context(
    query: str,
    token_budget: int = 1500,
    extension_filter: str | None = None,
) -> dict:
    """
    Retrieve pre-computed summaries + relevant chunks within a token budget.

    Fetches top vector candidates, groups by parent file, then builds a
    context string made of source blocks. The pre-computed summary is
    prepended to each file's section for quick orientation.

    Token budget: 1 token ≈ 4 characters.

    Args:
        query:            Natural language query.
        token_budget:     Approx. max tokens to return (4 chars each).
        extension_filter: Optional extension filter (e.g. ".pdf").

    Returns:
        {
            "context": str,           # formatted source blocks
            "sources": list[str],     # unique file paths included
            "token_estimate": int,    # approximate token count used
        }
    """
    char_budget = token_budget * 4

    where: dict | None = None
    if extension_filter:
        ext = extension_filter if extension_filter.startswith(".") else f".{extension_filter}"
        where = {"file_extension": {"$eq": ext}}

    store = _get_vec_store()
    doc_store = _get_doc_store()

    # Fetch a generous pool of chunks
    chunks = store.search(query, n=RERANKER_CANDIDATES, where=where)
    if not chunks:
        return {"context": "", "sources": [], "token_estimate": 0}

    # Group chunks by parent file (preserve best-distance order per file)
    file_chunks: dict[str, dict] = {}  # parent_id -> {meta, chunks}
    for chunk in chunks:
        meta = chunk["metadata"]
        pid = meta.get("parent_id", "")
        if not pid:
            continue
        if pid not in file_chunks:
            file_chunks[pid] = {
                "file_name": meta.get("file_name", "unknown"),
                "file_path": meta.get("file_path", ""),
                "parent_id": pid,
                "texts": [],
            }
        file_chunks[pid]["texts"].append(chunk["text"])

    # Fetch pre-computed summaries from SQLite
    for pid, entry in file_chunks.items():
        doc = doc_store.get_document(pid)
        entry["summary"] = doc.get("summary", "") if doc else ""

    # Build context blocks within budget
    blocks: list[str] = []
    sources: list[str] = []
    chars_used = 0

    for entry in file_chunks.values():
        if chars_used >= char_budget:
            break

        file_name = entry["file_name"]
        file_path = entry["file_path"]
        summary   = entry["summary"]

        # Header + summary
        header = f"[Source: {file_name} | Path: {file_path}]"
        summary_line = f"Summary: {summary}" if summary else ""
        block_parts = [header]
        if summary_line:
            block_parts.append(summary_line)

        # Append relevant chunks until budget is tight
        for text in entry["texts"]:
            text = text.strip()
            tentative = chars_used + len("\n".join(block_parts)) + len(text) + 4
            if tentative > char_budget:
                break
            block_parts.append(text)

        block = "\n".join(block_parts)
        chars_used += len(block) + 2  # +2 for the blank line separator

        blocks.append(block)
        sources.append(file_path)

    context = "\n\n".join(blocks)
    return {
        "context":        context,
        "sources":        sources,
        "token_estimate": chars_used // 4,
    }


def get_file_analysis(file_path: str) -> dict:
    """
    Retrieve the pre-computed LLM analysis for a specific file.

    Fetches the 2-sentence summary and PII flag from SQLite without any
    additional LLM calls. Returns immediately even if the file isn't indexed.

    Args:
        file_path: Absolute path to the file.

    Returns:
        {
            "file_path":    str,
            "file_name":    str,
            "summary":      str,       # empty string if not indexed or not computed
            "pii_detected": bool,
            "indexed":      bool,      # False if file not in SQLite
        }
    """
    path = Path(file_path).resolve()
    doc_store = _get_doc_store()
    doc = doc_store.get_document_by_path(str(path))

    if doc is None:
        return {
            "file_path":    str(path),
            "file_name":    path.name,
            "summary":      "",
            "pii_detected": False,
            "indexed":      False,
        }

    return {
        "file_path":    doc["file_path"],
        "file_name":    doc["file_name"],
        "summary":      doc.get("summary", ""),
        "pii_detected": bool(doc.get("pii_flag", 0)),
        "indexed":      True,
    }


# ── Manual test entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Quick smoke-test for all 4 agent tool functions.

    Run from the search/ directory:
        python -m retrieval.searcher

    Requires:
        - Ollama running with nomic-embed-text + llama3.2 pulled
        - At least one file indexed (python -m indexing.indexer or main.py index)
    """
    import json

    TEST_DIR = r"D:\me\code\searching_test"

    logging.basicConfig(level=logging.WARNING)
    for mod in ("retrieval", "core", "indexing"):
        logging.getLogger(mod).setLevel(logging.INFO)

    print("=" * 60)
    print("Agent Tool Smoke Tests")
    print("=" * 60)

    # ── 1. fast_filename_search ────────────────────────────────────────────────
    print("\n[1] fast_filename_search(query='report', folder_path=TEST_DIR)")
    r1 = fast_filename_search("ethical", folder_path=TEST_DIR, max_results=5)
    print(json.dumps(r1, indent=2))

    print("\n[1b] fast_filename_search(query='data') -- from indexed files only")
    r1b = fast_filename_search("data", max_results=5)
    print(json.dumps(r1b, indent=2))

    # ── 2. get_relevant_filenames ──────────────────────────────────────────────
    print("\n[2] get_relevant_filenames(query='AI in Legal Frameworks', k=3)")
    r2 = get_relevant_filenames("AI in Legal Frameworks", k=3)
    print(json.dumps(r2, indent=2))

    print("\n[2b] get_relevant_filenames(query='budget', k=3, extension_filter='.txt')")
    r2b = get_relevant_filenames("budget", k=3, extension_filter=".txt")
    print(json.dumps(r2b, indent=2))

    # ── 3. get_document_context ────────────────────────────────────────────────
    print("\n[3] get_document_context(query='key findings', token_budget=800)")
    r3 = get_document_context("key findings", token_budget=800)
    print(f"  token_estimate : {r3['token_estimate']}")
    print(f"  sources        : {r3['sources']}")
    print("  context preview:")
    print(r3["context"])
    print("  ...")

    # ── 4. get_file_analysis ───────────────────────────────────────────────────
    # Use first indexed file if available, else a dummy path.
    doc_store = _get_doc_store()
    all_paths = doc_store.get_all_paths()
    sample_path = all_paths[0]["file_path"] if all_paths else r"D:\nonexistent\file.txt"

    print(f"\n[4] get_file_analysis(file_path='{sample_path}')")
    r4 = get_file_analysis(sample_path)
    print(json.dumps(r4, indent=2))

    print("\n[4b] get_file_analysis -- unindexed file")
    r4b = get_file_analysis(r"D:\nonexistent\ghost.pdf")
    print(json.dumps(r4b, indent=2))

    print("\n" + "=" * 60)
    print("All tests complete.")
