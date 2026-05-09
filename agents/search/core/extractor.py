"""
core/extractor.py — Text extraction from local files.

Supports: .txt, .pdf (via PyMuPDF), .docx (via python-docx).
Any unsupported or broken file returns None; the caller skips it silently.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_text_from_file(file_path: str) -> str | None:
    """
    Extract plain text from a file.

    Args:
        file_path: Absolute or relative path to the file (any OS path style).

    Returns:
        Extracted text as a single string, or None if extraction fails
        or the file type is not supported.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    try:
        if suffix == ".txt":
            return _read_txt(path)
        elif suffix == ".pdf":
            return _read_pdf(path)
        elif suffix == ".docx":
            return _read_docx(path)
        else:
            logger.warning("Unsupported file type '%s': %s", suffix, path.name)
            return None

    except Exception as exc:
        logger.warning("Failed to extract text from '%s': %s", path.name, exc)
        return None


# ── Private helpers ────────────────────────────────────────────────────────────

def _read_txt(path: Path) -> str:
    """Read a plain-text file, trying UTF-8 first then falling back to latin-1."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _read_pdf(path: Path) -> str:
    """Extract text from every page of a PDF using PyMuPDF (fitz)."""
    import fitz  # PyMuPDF

    pages: list[str] = []
    with fitz.open(str(path)) as doc:
        for page in doc:
            pages.append(page.get_text())
    return "\n".join(pages)


def _read_docx(path: Path) -> str:
    """Extract text from all paragraphs of a DOCX file using python-docx."""
    from docx import Document

    doc = Document(str(path))
    paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
    return "\n".join(paragraphs)
