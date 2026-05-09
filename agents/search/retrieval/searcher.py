"""
retrieval/searcher.py -- Vector search + LLM reranking orchestrator.

Pipeline
--------
1. VectorStore.search() fetches RERANKER_CANDIDATES chunks from ChromaDB.
2. Results are deduplicated to one entry per file (best matching chunk kept).
3. _llm_rerank() sends all candidates to llama3.2 in ONE prompt.
   - The LLM outputs ONLY a comma-separated list of document numbers in
     relevance order (e.g. "3,1,7,2"). This format is parsed with a simple
     regex -- immune to JSON syntax errors from the LLM.
   - Falls back to original vector-distance order if the LLM fails.
4. search_files()         -> reranked file paths (no snippets)
4. search_with_snippets() -> reranked paths + per-result LLM summaries

metadata_filter (optional):
    ChromaDB where dict applied during vector search.
    Examples:
        {"file_extension": {"$eq": ".pdf"}}
        {"creation_date":  {"$gte": "2024-01-01"}}
"""

import logging
import re

from openai import OpenAI

from config import OLLAMA_API_KEY, OLLAMA_BASE_URL, RERANKER_CANDIDATES, SNIPPET_MODEL
from core.vector_store import VectorStore

logger = logging.getLogger(__name__)

_llm_client = OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)

_vec_store: VectorStore | None = None


def _get_store() -> VectorStore:
    global _vec_store
    if _vec_store is None:
        _vec_store = VectorStore()
    return _vec_store


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


# ── Public API ─────────────────────────────────────────────────────────────────

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
    store = _get_store()
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
    store = _get_store()
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
