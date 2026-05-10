"""
main.py -- CLI entry point for Smart Search V2.

Subcommands:
    index   --dir <path>                     Full directory index
    search  --query <text>                   Hybrid search + LLM reranking (paths only)
    search  --query <text> --snippets        + LLM-generated snippets
    search  --query <text> --filter ext=.pdf Filter by extension
    watch   --dir <path>                     Background watcher (Ctrl+C to stop)

Cross-platform: pathlib used throughout. Works on Windows, macOS, Linux.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s | %(name)s | %(message)s",
)
# Show INFO from our own modules but not from chromadb/httpx/etc.
for _mod in ("indexing", "retrieval", "core"):
    logging.getLogger(_mod).setLevel(logging.INFO)


def cmd_index(args: argparse.Namespace) -> None:
    from indexing.indexer import build_index
    print(f"\n[INDEX] Scanning: {args.dir}\n")
    try:
        build_index(args.dir)
    except Exception as exc:
        _fatal(exc)


def cmd_search(args: argparse.Namespace) -> None:
    where = _parse_filter(args.filter) if args.filter else None
    if args.snippets:
        _search_with_snippets(args.query, args.top_k, where)
    else:
        _search_paths(args.query, args.top_k, where)


def _search_paths(query: str, top_k: int, where: dict | None) -> None:
    from retrieval.searcher import search_files
    print(f'\n[SEARCH] Query: "{query}"\n')
    try:
        results = search_files(query, n_results=top_k, metadata_filter=where)
    except Exception as exc:
        _fatal(exc)
        return

    if not results:
        print("No matching files found.\n"
              "Tip: run  python main.py index --dir <path>  first.")
        return

    print(f"Found {len(results)} result(s):\n")
    for i, path in enumerate(results, 1):
        print(f"  {i}. {path}")
    print()


def _search_with_snippets(query: str, top_k: int, where: dict | None) -> None:
    from retrieval.searcher import search_with_snippets
    print(f'\n[SEARCH+SNIPPETS] Query: "{query}"\n')
    try:
        results = search_with_snippets(query, n_results=top_k, metadata_filter=where)
    except Exception as exc:
        _fatal(exc)
        return

    if not results:
        print("No matching files found.")
        return

    for r in results:
        # Show LLM relevance score (0-10) so user can see the reranker's judgment.
        llm_tag = f"llm={r['llm_score']:.1f}/10" if r["llm_score"] >= 0 else "llm=fallback"
        print(f"  [{r['score_rank']}] {r['file_name']}  ({llm_tag})")
        print(f"      Path:    {r['file_path']}")
        print(f"      Date:    {r['creation_date']}  |  Folder: {r['parent_folder']}")
        if r["snippet"]:
            print(f"      Snippet: {r['snippet']}")
        print()


def cmd_watch(args: argparse.Namespace) -> None:
    from indexing.watcher import DirectoryWatcher
    watcher = DirectoryWatcher()
    try:
        watcher.start(args.dir)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    print(f"[WATCH] Monitoring '{args.dir}'")
    print("[WATCH] Debounce: 5s quiet period before re-indexing changed files.")
    print("[WATCH] Press Ctrl+C to stop.\n")

    try:
        while watcher.is_alive:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[WATCH] Shutting down...")
    finally:
        watcher.stop()
        print("[WATCH] Stopped.")


def _parse_filter(filter_str: str) -> dict | None:
    """
    Convert a CLI filter string to a ChromaDB where dict.

    Supported shorthand keys:
        ext=.pdf          -> {"file_extension": {"$eq": ".pdf"}}
        folder=finance    -> {"parent_folder":  {"$eq": "finance"}}
        date=2024-01-01   -> {"creation_date":  {"$gte": "2024-01-01"}}
    """
    _KEY_MAP = {
        "ext":    "file_extension",
        "folder": "parent_folder",
        "date":   "creation_date",
    }
    if "=" not in filter_str:
        print(f"[WARN] Ignoring malformed --filter '{filter_str}' (expected key=value).")
        return None

    key, _, value = filter_str.partition("=")
    chroma_key = _KEY_MAP.get(key.lower(), key)
    op = "$gte" if key.lower() == "date" else "$eq"
    return {chroma_key: {op: value}}


def _fatal(exc: Exception) -> None:
    msg = str(exc)
    print(f"\n[ERROR] {msg}")
    if any(w in msg.lower() for w in ("connection", "refused", "ollama", "connect")):
        print(
            "\nHint: Ollama is not responding.\n"
            "  1. Install: https://ollama.com/download\n"
            "  2. Pull embedding model: ollama pull nomic-embed-text\n"
            "  3. Start server:         ollama serve\n"
        )
    sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Smart Search V2 -- Hybrid Semantic + Keyword + LLM Reranking (100%% offline).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="Index a directory of documents.")
    p_index.add_argument("--dir", required=True, metavar="PATH")

    p_search = sub.add_parser("search", help="Search the indexed documents.")
    p_search.add_argument("--query", required=True, metavar="TEXT")
    p_search.add_argument("--top-k", type=int, default=5, dest="top_k", metavar="N")
    p_search.add_argument(
        "--snippets", action="store_true",
        help="Generate LLM snippets for each result (requires llama3.2 in Ollama)."
    )
    p_search.add_argument(
        "--filter", default=None, metavar="KEY=VALUE",
        help="Metadata filter. Examples: ext=.pdf  folder=finance  date=2024-01-01"
    )

    p_watch = sub.add_parser("watch", help="Watch a directory and auto-index changes.")
    p_watch.add_argument("--dir", required=True, metavar="PATH")

    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    {"index": cmd_index, "search": cmd_search, "watch": cmd_watch}[args.command](args)
