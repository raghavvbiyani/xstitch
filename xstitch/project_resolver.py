"""Unified project root resolution for Stitch.

Ensures the CLI, Cursor-spawned MCP server, and Claude-spawned MCP server all
agree on the same project root, preventing split-brain where the same repo
ends up with different ``project_key`` hashes.

Resolution order (first match wins):

1. Explicit ``override`` argument (e.g., ``--project`` flag).
2. ``AHCP_PROJECT_PATH`` environment variable, if it points to an existing dir.
3. Ancestor walk from ``os.getcwd()`` looking for a project marker:
   - ``.git`` (directory OR file - git worktrees use a file)
   - ``.ahcp`` (directory OR file - explicit sentinel for non-git projects)
4. Final fallback: ``os.getcwd()``.

Home directory is NEVER auto-selected (that would bucket every cwd-less MCP
into the same "home" scope, which is exactly the bug we are fixing).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Markers that indicate "this directory is the root of a project".
# Order does not matter for matching; the closest ancestor wins.
PROJECT_MARKERS: tuple[str, ...] = (".git", ".ahcp")


def resolve_project_path(override: Optional[str] = None) -> Path:
    """Resolve the canonical project root.

    Args:
        override: Explicit project path (e.g., from a ``--project`` CLI flag).
            If provided, it wins unconditionally. Empty string is treated
            as "not provided".

    Returns:
        An absolute, symlink-resolved ``Path`` to the project root.
    """
    if override:
        return _safe_resolve(Path(override))

    env = os.environ.get("AHCP_PROJECT_PATH", "").strip()
    if env:
        env_path = _safe_resolve(Path(env))
        if env_path.is_dir():
            return env_path

    # Ancestor walk from current working directory.
    cwd = _safe_resolve(Path.cwd())
    marker_root = find_project_root(cwd)
    if marker_root is not None:
        return marker_root

    # Final fallback: cwd as-is. We deliberately do NOT fall back to
    # ``Path.home()`` because that would cause every MCP spawned with a
    # missing cwd to bucket into the same "home" project key, which is
    # the root cause of the Cursor/Claude split-brain bug.
    return cwd


def find_project_root(start: Path) -> Optional[Path]:
    """Walk up from ``start`` looking for a project marker.

    Returns the closest ancestor (including ``start`` itself) that contains
    a ``.git`` or ``.ahcp`` entry (file or directory). Returns ``None`` if
    no marker is found before reaching the filesystem root.

    This function never returns the user's home directory, because a marker
    inside ``$HOME`` is very unlikely to be the intended project root for
    an agent running inside a sub-directory.
    """
    try:
        start = _safe_resolve(start)
    except OSError:
        return None

    home = _safe_home()
    candidates = [start, *start.parents]
    for ancestor in candidates:
        if home is not None and ancestor == home:
            # Skip the user's home dir - see function docstring.
            continue
        if _has_marker(ancestor):
            return ancestor
    return None


def has_ahcp_sentinel(path: Path) -> bool:
    """Check whether a path contains an explicit ``.ahcp`` marker (file or dir)."""
    return (path / ".ahcp").exists()


def write_ahcp_sentinel(path: Path) -> Path:
    """Create an explicit ``.ahcp`` sentinel file pinning the project root.

    Used by ``stitch init --pin`` so non-git projects can anchor the resolver.
    Idempotent: if a sentinel (file or directory) already exists, no-op.
    Returns the path to the sentinel.
    """
    path = _safe_resolve(path)
    target = path / ".ahcp"
    if target.exists():
        return target
    # Write a sentinel file (not a directory) so it does not conflict with
    # the legacy in-repo ``.ahcp/`` storage directory semantics. If the
    # user already has a ``.ahcp`` storage directory, the ``exists()``
    # check above returns True and we do not overwrite it.
    target.write_text(
        "# Stitch project root sentinel\n"
        "# This file marks the directory as the canonical Stitch project root.\n"
        "# Created by `stitch init --pin`.\n"
    )
    return target


# --- internals ---------------------------------------------------------------


def _has_marker(directory: Path) -> bool:
    """Does this directory contain any project marker?"""
    for marker in PROJECT_MARKERS:
        if (directory / marker).exists():
            return True
    return False


def _safe_resolve(path: Path) -> Path:
    """``Path.resolve()`` that tolerates broken symlinks and missing dirs."""
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path.absolute()


def _safe_home() -> Optional[Path]:
    try:
        return Path.home().resolve(strict=False)
    except (OSError, RuntimeError):
        return None
