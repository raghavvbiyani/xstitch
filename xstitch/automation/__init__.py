"""Automation tools for Stitch — git hooks, background daemon, macOS launchd.

This package provides organized import paths for:
  - hooks: Git post-commit/post-checkout hooks for auto-snapshots
  - daemon: Background snapshot daemon
  - launchd: macOS launchd agent for automatic snapshots

Implementation note: actual code lives in xstitch.hooks, xstitch.daemon, xstitch.launchd.
"""

from ..hooks import (  # noqa: F401
    install_hooks,
    uninstall_hooks,
    HOOK_MARKER,
    POST_COMMIT_HOOK,
    POST_CHECKOUT_HOOK,
)
from ..daemon import (  # noqa: F401
    start_daemon,
    stop_daemon,
    daemon_status,
    PID_DIR,
)
from ..launchd import (  # noqa: F401
    install_launchd,
    uninstall_launchd,
    launchd_status,
    PLIST_LABEL,
    LAUNCH_AGENTS_DIR,
    PLIST_PATH,
    WATCHER_SCRIPT,
)
