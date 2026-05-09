"""
core/chunker.py — Split extracted text into overlapping chunks.

Uses a simple character-based sliding window (no external NLP libraries needed).
"""

from pathlib import Path
from config import CHUNK_SIZE, CHUNK_OVERLAP


def chunk_text(text: str, file_path: str) -> list[dict]:
    """
    Split `text` into overlapping chunks and attach file metadata to each.

    The algorithm:
      - Start at position 0.
      - Take a slice of length CHUNK_SIZE.
      - Advance by (CHUNK_SIZE - CHUNK_OVERLAP) for the next window.
      - Repeat until the end of the text.

    Args:
        text:      The full extracted text of a document.
        file_path: Path to the source file (used in metadata).

    Returns:
        A list of dicts, each with keys:
          - "text_chunk": the chunk string
          - "metadata":   {"file_path": str, "file_name": str}
    """
    if not text or not text.strip():
        return []

    path = Path(file_path)
    step = max(1, CHUNK_SIZE - CHUNK_OVERLAP)  # how far we advance each iteration

    chunks: list[dict] = []
    start = 0

    while start < len(text):
        end = start + CHUNK_SIZE
        chunk_str = text[start:end].strip()

        if chunk_str:  # skip chunks that are purely whitespace
            chunks.append(
                {
                    "text_chunk": chunk_str,
                    "metadata": {
                        "file_path": str(path),          # keep OS-native string
                        "file_name": path.name,
                    },
                }
            )

        start += step

    return chunks
