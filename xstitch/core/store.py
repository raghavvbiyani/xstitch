"""Re-export shim — actual code in xstitch.store.

Allows: from xstitch.core.store import Store, GLOBAL_HOME, ...
"""

from ..store import *  # noqa: F401, F403
from ..store import (  # noqa: F401 — private names used in tests
    _DEDUP_WINDOW_SECONDS,
    _DEDUP_SIMILARITY_THRESHOLD,
    _MIN_SNAP_MESSAGE_LEN,
    _MIN_DECISION_PROBLEM_LEN,
    _TTL_DAYS,
    _CLEANUP_COOLDOWN_HOURS,
)
