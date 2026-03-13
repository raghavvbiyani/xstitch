"""Re-export shim — actual code in xstitch.discovery.

Provides: Project-level instruction injection.
"""

from ..discovery import *  # noqa: F401, F403
from ..discovery import (  # noqa: F401
    _inject_into_file,
    _update_gitignore,
    _get_installed_tool_names,
    _generate_page_index,
)
