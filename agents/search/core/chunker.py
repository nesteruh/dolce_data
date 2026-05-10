"""
core/chunker.py -- Recursive text splitter for child chunk generation.

Instead of a fixed sliding window, we split on natural boundaries in
priority order:  paragraph  ->  newline  ->  sentence  ->  word  ->  char.
This preserves semantic coherence so embedding quality is higher.
"""

from pathlib import Path
from config import CHILD_CHUNK_SIZE, CHILD_OVERLAP

# Separators tried in order from coarsest to finest grain.
_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


def make_child_chunks(text: str, doc_meta: dict) -> list[dict]:
    """
    Split document text into overlapping child chunks with metadata.

    Args:
        text:     Full extracted text of the document.
        doc_meta: Dict returned by extract_document() -- must contain
                  at minimum: file_path, file_name, file_extension,
                  file_size, creation_date, parent_folder, and the
                  'doc_id' key (added by the indexer after SQLite upsert).

    Returns:
        List of dicts:
            {
                "text_chunk": str,
                "metadata": {
                    "parent_id":      str,   # doc_id from SQLite
                    "chunk_index":    int,
                    "file_path":      str,
                    "file_name":      str,
                    "file_extension": str,
                    "file_size":      int,
                    "creation_date":  str,
                    "parent_folder":  str,
                }
            }
    """
    if not text or not text.strip():
        return []

    # Split into raw pieces using recursive strategy.
    raw_pieces = _recursive_split(text.strip(), _SEPARATORS, CHILD_CHUNK_SIZE)

    # Merge pieces that are too small and add overlap.
    merged = _merge_and_overlap(raw_pieces, CHILD_CHUNK_SIZE, CHILD_OVERLAP)

    # Build the final chunk dicts with full metadata.
    doc_id = doc_meta.get("doc_id", "")
    base_meta = {
        "parent_id":      doc_id,
        "file_path":      str(doc_meta.get("file_path", "")),
        "file_name":      str(doc_meta.get("file_name", "")),
        "file_extension": str(doc_meta.get("file_extension", "")),
        "file_size":      int(doc_meta.get("file_size", 0)),
        "creation_date":  str(doc_meta.get("creation_date", "")),
        "parent_folder":  str(doc_meta.get("parent_folder", "")),
    }

    chunks = []
    for i, piece in enumerate(merged):
        if piece.strip():
            chunks.append({
                "text_chunk": piece,
                "metadata":   {**base_meta, "chunk_index": i},
            })

    return chunks


# ── Internal helpers ───────────────────────────────────────────────────────────

def _recursive_split(text: str, separators: list[str], chunk_size: int) -> list[str]:
    """
    Recursively split `text` using the first separator that is present.
    Pieces that still exceed `chunk_size` are split again with the next separator.
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    # Find the first separator that actually exists in the text.
    chosen_sep = None
    remaining_seps: list[str] = []
    for i, sep in enumerate(separators):
        if sep == "" or sep in text:
            chosen_sep = sep
            remaining_seps = separators[i + 1 :]
            break

    if chosen_sep is None:
        # Should not happen — empty string "" is always in text — but guard anyway.
        return [text[j : j + chunk_size] for j in range(0, len(text), chunk_size)]

    if chosen_sep == "":
        # Hard character split as last resort.
        return [text[j : j + chunk_size] for j in range(0, len(text), chunk_size) if text[j : j + chunk_size].strip()]

    parts = [p for p in text.split(chosen_sep) if p.strip()]
    result: list[str] = []

    for part in parts:
        if len(part) <= chunk_size:
            result.append(part)
        elif remaining_seps:
            result.extend(_recursive_split(part, remaining_seps, chunk_size))
        else:
            # No more separators; hard-split the oversized piece.
            for j in range(0, len(part), chunk_size):
                piece = part[j : j + chunk_size].strip()
                if piece:
                    result.append(piece)

    return result


def _merge_and_overlap(pieces: list[str], chunk_size: int, overlap: int) -> list[str]:
    """
    Merge pieces that are too small (below 1/4 of chunk_size) into their
    neighbour, then add overlap by prepending the tail of the previous chunk.
    """
    if not pieces:
        return []

    min_size = chunk_size // 4

    # Pass 1 -- merge tiny trailing pieces into previous chunk.
    merged: list[str] = []
    buffer = ""
    for piece in pieces:
        if not buffer:
            buffer = piece
        elif len(buffer) + len(piece) + 1 <= chunk_size:
            buffer = buffer + " " + piece
        else:
            merged.append(buffer)
            buffer = piece
    if buffer:
        merged.append(buffer)

    # Pass 2 -- add overlap by carrying the tail of the previous chunk.
    if overlap <= 0 or len(merged) <= 1:
        return merged

    with_overlap: list[str] = [merged[0]]
    for i in range(1, len(merged)):
        prev_tail = merged[i - 1][-overlap:].strip()
        with_overlap.append((prev_tail + " " + merged[i]) if prev_tail else merged[i])

    return with_overlap
