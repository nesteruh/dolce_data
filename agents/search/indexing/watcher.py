"""
indexing/watcher.py -- Filesystem watcher with debouncing.

Uses the `watchdog` library to monitor a directory for file changes and
triggers the indexing pipeline after a quiet period (no new events for
DEBOUNCE_SECONDS). Runs entirely in background daemon threads so the main
application stays responsive.

Usage:
    watcher = DirectoryWatcher()
    watcher.start("/path/to/watch")
    # ... main app runs ...
    watcher.stop()   # graceful shutdown
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import DEBOUNCE_SECONDS, SUPPORTED_EXTENSIONS
from indexing.indexer import index_file, remove_file

logger = logging.getLogger(__name__)

# Filename prefixes to ignore (temp / lock files written by Office apps etc.)
_IGNORE_PREFIXES = ("~$", ".", "_")


# ── Debouncer ──────────────────────────────────────────────────────────────────

class Debouncer:
    """
    Delay a callback until the signal has been quiet for `delay` seconds.

    Every call to .trigger() resets the countdown. The callback fires once
    the countdown expires without another trigger.
    """

    def __init__(self, callback, delay: float = DEBOUNCE_SECONDS) -> None:
        self._callback = callback
        self._delay = delay
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def trigger(self) -> None:
        """Reset the countdown and start (or restart) the wait timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
        try:
            self._callback()
        except Exception as exc:
            logger.error("Debouncer callback error: %s", exc)

    def stop(self) -> None:
        """Cancel any pending timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


# ── Event handler ──────────────────────────────────────────────────────────────

class _SearchEventHandler(FileSystemEventHandler):
    """Handles watchdog filesystem events and queues files for indexing."""

    def __init__(self) -> None:
        super().__init__()
        self._pending_index: set[str] = set()
        self._pending_remove: set[str] = set()
        self._queue_lock = threading.Lock()
        self._debouncer = Debouncer(self._process_queue)

    # watchdog callbacks ---------------------------------------------------

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._queue_index(event.src_path)

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._queue_index(event.src_path)

    def on_deleted(self, event) -> None:
        if not event.is_directory:
            self._queue_remove(event.src_path)

    def on_moved(self, event) -> None:
        if not event.is_directory:
            # Treat as: remove old path, index new path.
            self._queue_remove(event.src_path)
            self._queue_index(event.dest_path)

    # internal helpers -----------------------------------------------------

    def _is_relevant(self, path_str: str) -> bool:
        p = Path(path_str)
        return (
            p.suffix.lower() in SUPPORTED_EXTENSIONS
            and not p.name.startswith(_IGNORE_PREFIXES)
        )

    def _queue_index(self, path_str: str) -> None:
        if not self._is_relevant(path_str):
            return
        with self._queue_lock:
            self._pending_index.add(str(Path(path_str).resolve()))
        self._debouncer.trigger()
        logger.debug("Queued for index: %s", Path(path_str).name)

    def _queue_remove(self, path_str: str) -> None:
        if not self._is_relevant(path_str):
            return
        with self._queue_lock:
            self._pending_remove.add(str(Path(path_str).resolve()))
        self._debouncer.trigger()
        logger.debug("Queued for removal: %s", Path(path_str).name)

    def _process_queue(self) -> None:
        """Called by the debouncer after the quiet period expires."""
        with self._queue_lock:
            to_index  = list(self._pending_index)
            to_remove = list(self._pending_remove)
            self._pending_index.clear()
            self._pending_remove.clear()

        for path in to_remove:
            logger.info("[WATCHER] Removing: %s", Path(path).name)
            try:
                remove_file(path)
            except Exception as exc:
                logger.error("Error removing '%s': %s", path, exc)

        for path in to_index:
            logger.info("[WATCHER] Indexing: %s", Path(path).name)
            try:
                index_file(path)
            except Exception as exc:
                logger.error("Error indexing '%s': %s", path, exc)

    def stop(self) -> None:
        self._debouncer.stop()


# ── Public interface ───────────────────────────────────────────────────────────

class DirectoryWatcher:
    """
    Watches a directory for file changes and keeps the index up to date.

    The watchdog Observer is set as a daemon thread so it exits automatically
    when the main process exits (no orphan threads).
    """

    def __init__(self) -> None:
        self._observer: Observer | None = None
        self._handler: _SearchEventHandler | None = None

    def start(self, directory: str) -> None:
        """
        Start monitoring `directory`. Non-recursive (flat watch only).

        Args:
            directory: Path to the directory to watch.
        """
        target = Path(directory).resolve()
        if not target.is_dir():
            raise ValueError(f"'{target}' is not a valid directory.")

        self._handler = _SearchEventHandler()
        self._observer = Observer()
        self._observer.daemon = True   # daemon = exits with the main process
        self._observer.schedule(self._handler, str(target), recursive=False)
        self._observer.start()
        logger.info("Watching '%s' (debounce: %.1fs)", target, DEBOUNCE_SECONDS)

    def stop(self) -> None:
        """Gracefully stop the observer and debouncer."""
        if self._handler:
            self._handler.stop()
        if self._observer and self._observer.is_alive():
            self._observer.stop()
            self._observer.join(timeout=10)
        logger.info("Watcher stopped.")

    @property
    def is_alive(self) -> bool:
        return self._observer is not None and self._observer.is_alive()
