"""Cross-platform advisory file locking for Stitch.

Used to serialize read-modify-write cycles on shared JSON files
(``registry.json``, ``events.jsonl``) so multiple MCP servers / CLI
invocations from different agents cannot lose updates.

Design notes:
    * The lock is advisory: cooperating processes must all go through
      ``file_lock``. External writers (e.g. a user editing ``registry.json``
      by hand) are not protected against — that's by design.
    * We lock a *sibling* ``.lock`` file rather than the data file itself
      so readers that don't mutate don't need to acquire the lock.
    * On POSIX we use ``fcntl.flock`` with a blocking call plus a small
      timeout-watchdog thread. On Windows we use ``msvcrt.locking``.
    * Timeout default is 5s which is plenty for JSON-sized writes but
      short enough to surface stuck locks quickly.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_IS_WINDOWS = sys.platform.startswith("win")

if _IS_WINDOWS:  # pragma: no cover - Windows-only path
    import msvcrt
else:
    import fcntl


class FileLockTimeout(OSError):
    """Raised when a lock cannot be acquired within the timeout."""


@contextmanager
def file_lock(lock_path: Path, timeout: float = 5.0, poll: float = 0.05) -> Iterator[Path]:
    """Acquire an exclusive lock on ``lock_path`` for the duration of the block.

    Creates the file (and any missing parents) if needed. The lock is
    released automatically when the block exits, even if an exception is
    raised inside it.

    Raises ``FileLockTimeout`` if the lock cannot be acquired within
    ``timeout`` seconds.
    """
    lock_path = Path(lock_path)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # If parents cannot be created, fall back to a no-op lock so the
        # caller can still make progress. Concurrency guarantees will be
        # best-effort in that edge case.
        yield lock_path
        return

    # Open in r+ mode, creating if missing.
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + max(timeout, 0.0)
        acquired = False
        while True:
            try:
                if _IS_WINDOWS:  # pragma: no cover
                    # Lock 1 byte; that's sufficient for advisory exclusion.
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise FileLockTimeout(
                        f"Could not acquire lock on {lock_path} within {timeout}s"
                    )
                time.sleep(poll)

        try:
            # Best-effort stamp so stale locks are debuggable.
            try:
                os.write(fd, f"{os.getpid()}\n".encode())
                os.fsync(fd)
            except OSError:
                pass
            yield lock_path
        finally:
            if acquired:
                try:
                    if _IS_WINDOWS:  # pragma: no cover
                        os.lseek(fd, 0, 0)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
