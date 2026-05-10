"""
search_files.py -- Fast, non-AI file search.

Provides two modes:
    1. Fuzzy filename search via RapidFuzz (default, recommended).
    2. Substring scan via os.walk as a fallback / compatibility mode.

The primary function `search_files()` uses RapidFuzz WRatio scoring so that
partial, misspelled, or reordered queries still surface the right files.

Usage (standalone test):
    python -m search_files
"""

import os
from pathlib import Path
from typing import List, Set

from rapidfuzz import fuzz, process as fuzz_process


def search_files(
    root_dir: str,
    query: str,
    max_results: int = 10,
    match_full_path: bool = False,
    follow_symlinks: bool = False,
    score_cutoff: int = 55,
    fuzzy: bool = True,
) -> List[str]:
    """
    Recursively search for files under `root_dir` whose filename (or full path)
    matches `query`. Uses RapidFuzz for fuzzy matching by default.

    Args:
        root_dir:        Directory to search recursively.
        query:           Search term (fuzzy or substring, case-insensitive).
        max_results:     Maximum number of file paths to return.
        match_full_path: If True, match `query` against the full absolute path;
                         otherwise match only the filename.
        follow_symlinks: If True, follow directory symlinks while walking.
        score_cutoff:    Minimum RapidFuzz WRatio score (0-100). Ignored when
                         `fuzzy=False`.
        fuzzy:           If True (default), use RapidFuzz WRatio scoring.
                         If False, fall back to a simple case-insensitive
                         substring scan (original behaviour).

    Returns:
        List[str]: Absolute file paths sorted by relevance score (fuzzy mode)
                   or discovery order (substring mode), up to `max_results`.
    """
    if fuzzy:
        return _fuzzy_search(
            root_dir, query, max_results, match_full_path, follow_symlinks, score_cutoff
        )
    return _substring_search(root_dir, query, max_results, match_full_path, follow_symlinks)


# ── Private helpers ────────────────────────────────────────────────────────────

def _collect_files(
    root_dir: str,
    follow_symlinks: bool,
) -> list[tuple[str, str]]:
    """
    Walk `root_dir` and return a list of (haystack, absolute_path) tuples.
    `haystack` is the filename (used for matching).
    """
    entries: list[tuple[str, str]] = []
    seen: Set[str] = set()

    for dirpath, _, filenames in os.walk(
        root_dir, followlinks=follow_symlinks, onerror=lambda e: None
    ):
        for fn in filenames:
            candidate = os.path.abspath(os.path.join(dirpath, fn))
            if candidate in seen:
                continue
            seen.add(candidate)
            entries.append((fn, candidate))

    return entries


def _fuzzy_search(
    root_dir: str,
    query: str,
    max_results: int,
    match_full_path: bool,
    follow_symlinks: bool,
    score_cutoff: int,
) -> List[str]:
    """RapidFuzz-powered fuzzy search, sorted best-score-first."""
    entries = _collect_files(root_dir, follow_symlinks)
    if not entries:
        return []

    # Build the list of haystacks (filename or full path)
    haystacks = [
        (fp if match_full_path else fn)
        for fn, fp in entries
    ]

    matches = fuzz_process.extract(
        query,
        haystacks,
        scorer=fuzz.WRatio,
        score_cutoff=score_cutoff,
        limit=max_results,
    )

    # matches -> (matched_string, score, original_index)
    # Sort by score descending, return file paths
    sorted_matches = sorted(matches, key=lambda x: x[1], reverse=True)
    seen_paths: Set[str] = set()
    results: List[str] = []

    for _, _, idx in sorted_matches:
        fp = entries[idx][1]
        if fp not in seen_paths:
            seen_paths.add(fp)
            results.append(fp)

    return results


def _substring_search(
    root_dir: str,
    query: str,
    max_results: int,
    match_full_path: bool,
    follow_symlinks: bool,
) -> List[str]:
    """Original O(n) substring scan -- kept as a fallback."""
    query_lower = query.lower()
    matches: List[str] = []
    seen: Set[str] = set()

    for dirpath, _, filenames in os.walk(
        root_dir, followlinks=follow_symlinks, onerror=lambda e: None
    ):
        for fn in filenames:
            candidate = os.path.abspath(os.path.join(dirpath, fn))
            if candidate in seen:
                continue
            hay = candidate.lower() if match_full_path else fn.lower()
            if query_lower in hay:
                matches.append(candidate)
                seen.add(candidate)
                if len(matches) >= max_results:
                    return matches

    return matches


# ── Manual test entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Smoke-test for search_files() — both fuzzy and substring modes.

    Run from the search/ directory:
        python -m search_files
    """
    import json

    TEST_DIR   = r"D:\me\code\searching_test"
    TEST_QUERY = "report"

    print("=" * 60)
    print("search_files() Smoke Tests")
    print("=" * 60)

    # ── Fuzzy mode (default) ───────────────────────────────────────────────────
    print(f"\n[1] Fuzzy search — query='{TEST_QUERY}', root='{TEST_DIR}'")
    results_fuzzy = search_files(TEST_DIR, TEST_QUERY, max_results=10, fuzzy=True)
    if results_fuzzy:
        for i, p in enumerate(results_fuzzy, 1):
            print(f"  {i}. {p}")
    else:
        print("  (no matches)")

    # ── Fuzzy full-path mode ───────────────────────────────────────────────────
    print(f"\n[2] Fuzzy full-path search — query='code', root='{TEST_DIR}'")
    results_path = search_files(
        TEST_DIR, "code", max_results=5, match_full_path=True, fuzzy=True
    )
    if results_path:
        for i, p in enumerate(results_path, 1):
            print(f"  {i}. {p}")
    else:
        print("  (no matches)")

    # ── Substring fallback ─────────────────────────────────────────────────────
    print(f"\n[3] Substring fallback — query='data', root='{TEST_DIR}'")
    results_sub = search_files(TEST_DIR, "data", max_results=5, fuzzy=False)
    if results_sub:
        for i, p in enumerate(results_sub, 1):
            print(f"  {i}. {p}")
    else:
        print("  (no matches)")

    # ── Low score cutoff (broad match) ────────────────────────────────────────
    print(f"\n[4] Fuzzy broad — query='rpt', score_cutoff=30, root='{TEST_DIR}'")
    results_broad = search_files(TEST_DIR, "rpt", max_results=5, score_cutoff=30)
    if results_broad:
        for i, p in enumerate(results_broad, 1):
            print(f"  {i}. {p}")
    else:
        print("  (no matches)")

    print("\n" + "=" * 60)
    print("All tests complete.")
