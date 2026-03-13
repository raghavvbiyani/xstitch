"""Re-export shim — actual code in xstitch.capture.

Allows: from xstitch.core.capture import capture_snapshot, run_git, ...
"""

from ..capture import *  # noqa: F401, F403
