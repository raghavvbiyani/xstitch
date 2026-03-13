"""Re-export shim — actual code in xstitch.log.

Allows: from xstitch.core.log import ok, info, warn, ...
"""

from ..log import *  # noqa: F401, F403
