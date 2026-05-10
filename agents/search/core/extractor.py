"""
core/extractor.py -- Rich document extraction using markitdown.

Returns a metadata dict (not just a string) so the indexer can populate
both the SQLite parent store and the ChromaDB child metadata in one pass.

Supported: .pdf, .docx, .pptx, .xlsx, .md, .txt
"""

import logging
import warnings
from datetime import datetime, timezone
from pathlib import Path

from config import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

# markitdown's audio/video support tries to locate ffmpeg on import and emits
# a RuntimeWarning when it cannot find it. Suppress it -- we never use A/V.
_FFMPEG_WARNING = "Couldn't find ffmpeg or avconv"

# Plain-text extensions handled directly (markitdown uses ASCII codec for these,
# which breaks on any non-ASCII content such as accented characters).
_PLAIN_TEXT_EXTENSIONS = {".txt", ".md"}


def extract_document(file_path: str) -> dict | None:
    """
    Extract text and filesystem metadata from a local file.

    Args:
        file_path: Path to the file (any OS format).

    Returns:
        Dict with keys:
            full_text, file_path, file_name, file_extension,
            file_size, creation_date, modified_date, parent_folder
        or None if the file type is unsupported or extraction fails.
    """
    path = Path(file_path).resolve()
    suffix = path.suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        logger.warning("Unsupported extension '%s': %s", suffix, path.name)
        return None

    # ── Filesystem metadata (always available) ────────────────────────────────
    try:
        stat = path.stat()
        file_size = stat.st_size
        modified_date = _ts_to_iso(stat.st_mtime)
        # st_birthtime on macOS/Windows; fall back to st_ctime on Linux
        creation_ts = getattr(stat, "st_birthtime", stat.st_ctime)
        creation_date = _ts_to_iso(creation_ts)
    except OSError as exc:
        logger.warning("Cannot stat '%s': %s", path.name, exc)
        return None

    # ── Text extraction via markitdown ────────────────────────────────────────
    full_text = _extract_text(path)
    if full_text is None:
        return None

    return {
        "full_text":      full_text,
        "file_path":      str(path),
        "file_name":      path.name,
        "file_extension": suffix,
        "file_size":      file_size,
        "creation_date":  creation_date,
        "modified_date":  modified_date,
        "parent_folder":  path.parent.name,
    }


# ── Private helpers ────────────────────────────────────────────────────────────

def _extract_text(path: Path) -> str | None:
    """
    Extract text from a file.

    .txt and .md files are read directly with UTF-8 (falling back to latin-1)
    to avoid markitdown's ASCII-only PlainTextConverter limitation.

    All other supported types (.pdf, .docx, .pptx, .xlsx) go through markitdown.
    """
    suffix = path.suffix.lower()

    # Fast path for plain-text files -- skip markitdown entirely.
    if suffix in _PLAIN_TEXT_EXTENSIONS:
        return _read_plain(path)

    # Binary/structured formats -- use markitdown.
    try:
        # Suppress the ffmpeg RuntimeWarning that markitdown emits on import.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=_FFMPEG_WARNING)
            from markitdown import MarkItDown
            md = MarkItDown()   # no llm_client -- stays 100% offline

        result = md.convert(str(path))
        text = (result.text_content or "").strip()
        if text:
            return text
        logger.warning("markitdown returned empty text for '%s'.", path.name)
        return None
    except Exception as exc:
        logger.warning("markitdown failed for '%s': %s", path.name, exc)
        return None


def _read_plain(path: Path) -> str | None:
    """Read plain-text file; tries UTF-8 then latin-1."""
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="latin-1").strip() or None
        except Exception as exc:
            logger.warning("Could not read '%s': %s", path.name, exc)
            return None


def _ts_to_iso(timestamp: float) -> str:
    """Convert a POSIX timestamp to an ISO-8601 date string (UTC)."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
