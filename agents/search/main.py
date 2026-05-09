"""
main.py -- CLI entry point for the Smart Search Engine.

Usage:
    Index a directory:
        python main.py index --dir "/path/to/your/documents"

    Search:
        python main.py search --query "New PLD that adopted March 2024"

Cross-platform: all path handling is done via pathlib internally.
Works on Windows, macOS, and Linux without modification.
"""

import argparse
import logging
import sys

# ── Logging setup ─────────────────────────────────────────────────────────────
# INFO-level messages are shown; DEBUG-level messages from dependencies are hidden.
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s | %(name)s | %(message)s",
)
# Surface our own INFO logs (progress messages) but keep noisy libs quiet.
logging.getLogger("indexing").setLevel(logging.INFO)
logging.getLogger("retrieval").setLevel(logging.INFO)


def cmd_index(args: argparse.Namespace) -> None:
    """Handle the `index` subcommand."""
    from indexing.indexer import build_index

    print(f"\n[INDEX] Starting indexing of: {args.dir}\n")
    try:
        build_index(args.dir)
    except Exception as exc:
        _handle_error(exc)


def cmd_search(args: argparse.Namespace) -> None:
    """Handle the `search` subcommand."""
    from retrieval.searcher import search_files

    print(f'\n[SEARCH] Query: "{args.query}"\n')
    try:
        results = search_files(args.query, n_results=args.top_k)
    except Exception as exc:
        _handle_error(exc)
        return

    if not results:
        print(
            "No matching files found.\n"
            "Tip: Make sure you have indexed a directory first with:\n"
            "     python main.py index --dir <path>"
        )
        return

    print(f"Found {len(results)} matching file(s):\n")
    for i, path in enumerate(results, start=1):
        print(f"  {i}. {path}")
    print()


def _handle_error(exc: Exception) -> None:
    """Print a friendly error and hint at the most common cause."""
    msg = str(exc)
    print(f"\n[ERROR] {msg}")

    # Give the user a specific hint when Ollama is not reachable.
    if "connection" in msg.lower() or "refused" in msg.lower() or "ollama" in msg.lower():
        print(
            "\nHint: It looks like Ollama is not running.\n"
            "  1. Make sure Ollama is installed (https://ollama.com/download).\n"
            "  2. Pull the model once:   ollama pull nomic-embed-text\n"
            "  3. Start the server:      ollama serve\n"
        )
    sys.exit(1)


# ── Argument parser ────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Local Semantic Search Engine -- 100%% offline, powered by Ollama + ChromaDB.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── index ──────────────────────────────────────────────────────────────────
    index_parser = subparsers.add_parser("index", help="Index a local directory of documents.")
    index_parser.add_argument(
        "--dir",
        required=True,
        metavar="PATH",
        help="Path to the directory containing .txt, .pdf, or .docx files.",
    )

    # ── search ─────────────────────────────────────────────────────────────────
    search_parser = subparsers.add_parser("search", help="Search the indexed documents.")
    search_parser.add_argument(
        "--query",
        required=True,
        metavar="TEXT",
        help='Natural language query, e.g. "New PLD that adopted March 2024".',
    )
    search_parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        dest="top_k",
        metavar="N",
        help="Number of chunk hits to retrieve (default: 5).",
    )

    return parser


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "index":
        cmd_index(args)
    elif args.command == "search":
        cmd_search(args)
