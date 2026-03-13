"""Core domain objects and storage for Stitch.

This package provides organized import paths for the fundamental building blocks:
  - models: Task, Snapshot, Decision, HandoffBundle dataclasses
  - store: Storage engine (JSON + Markdown, machine-local)
  - capture: Git state auto-capture
  - log: Structured logging (all output to stderr)

Why a separate 'core' package:
  - Clear dependency direction: everything depends on core, core depends
    on nothing else in Stitch. This prevents circular imports.
  - Contributors can understand the data model without reading tool
    integration or MCP code.
  - Follows domain-driven design: core = domain, integrations = adapters.

Implementation note: actual code lives in the original flat modules (xstitch.models,
xstitch.store, etc.) to preserve backward compatibility with existing patches,
hooks, and instruction files. This package re-exports from those modules.
"""

from ..models import (  # noqa: F401
    Task,
    Snapshot,
    Decision,
    HandoffBundle,
    to_json,
    from_json,
    _now_iso,
    _new_id,
)
from ..store import (  # noqa: F401
    Store,
    Stitch_DIR,
    GLOBAL_HOME,
    PROJECTS_HOME,
    REGISTRY_FILE,
    ACTIVE_TASK_FILE,
    project_key,
)
from ..capture import (  # noqa: F401
    run_git,
    is_git_repo,
    capture_git_state,
    capture_snapshot,
    has_significant_changes,
    capture_pre_summarize_snapshot,
)
from .. import log  # noqa: F401

__all__ = [
    "Task", "Snapshot", "Decision", "HandoffBundle",
    "to_json", "from_json",
    "Store", "Stitch_DIR", "GLOBAL_HOME", "PROJECTS_HOME",
    "REGISTRY_FILE", "ACTIVE_TASK_FILE", "project_key",
    "run_git", "is_git_repo", "capture_git_state", "capture_snapshot",
    "has_significant_changes", "capture_pre_summarize_snapshot",
    "log",
]
