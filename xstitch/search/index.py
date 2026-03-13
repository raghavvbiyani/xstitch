"""Persistent search index with incremental updates.

Stores the tokenized index at ~/.stitch/projects/<key>/search_index.json
so that repeated searches don't re-tokenize all tasks from scratch.

Design decisions:

Why JSON file (not SQLite or pickle):
  - JSON is human-readable and debuggable.
  - Our index is small (typically <100KB for 50 tasks).
  - Atomic writes via temp file + rename prevent corruption.
  - No binary format compatibility issues across Python versions.
  - SQLite would be faster for large datasets but adds complexity
    we don't need at our scale.

Why mtime-based staleness check (not content hashing):
  - File mtime is free — no disk reads required.
  - Content hashing requires reading every meta.json to compute the hash,
    which is exactly what we're trying to avoid.
  - False positives (mtime changed but content didn't) just cause a
    re-tokenization of one task — cheap at our scale.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..store import Store


class PersistentIndex:
    """Manages a cached search index on disk.

    The index stores pre-tokenized field data for each task. On search,
    only tasks whose meta.json is newer than the index entry are
    re-tokenized — the rest are loaded from cache.
    """

    def __init__(self, index_path: Path):
        self._path = index_path
        self._data: dict = {}
        self._loaded = False

    def load(self) -> bool:
        """Load the index from disk. Returns True if successfully loaded."""
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
                self._loaded = True
                return True
            except (json.JSONDecodeError, OSError):
                self._data = {}
        return False

    def save(self):
        """Save the index to disk atomically."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self._data, indent=2))
            tmp.rename(self._path)
        except OSError:
            if tmp.exists():
                tmp.unlink()

    def get_entry(self, task_id: str) -> dict | None:
        """Get cached index entry for a task, or None if stale/missing."""
        return self._data.get(task_id)

    def set_entry(self, task_id: str, entry: dict):
        """Update the index entry for a task."""
        self._data[task_id] = entry

    def remove_entry(self, task_id: str):
        """Remove a task from the index."""
        self._data.pop(task_id, None)

    def is_stale(self, task_id: str, meta_mtime: float) -> bool:
        """Check if the cached entry is older than the task's meta.json."""
        entry = self._data.get(task_id)
        if not entry:
            return True
        cached_mtime = entry.get("_mtime", 0)
        return meta_mtime > cached_mtime

    def task_ids(self) -> set[str]:
        """Return all task IDs in the index."""
        return set(self._data.keys())
