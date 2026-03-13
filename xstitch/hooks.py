"""Git hooks for automatic context capture.

Installs post-commit and post-checkout hooks that auto-snapshot
the current task's state whenever a commit is made.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .capture import run_git

HOOK_MARKER = "# Stitch-MANAGED-HOOK"

POST_COMMIT_HOOK = f"""\
#!/bin/sh
{HOOK_MARKER}
# Auto-snapshot after each commit for Stitch context preservation.
# Installed by: stitch hooks install

if command -v stitch >/dev/null 2>&1; then
    stitch snap -m "post-commit auto-snapshot" --source git-hook 2>/dev/null || true
elif command -v python3 >/dev/null 2>&1; then
    python3 -m xstitch.cli snap -m "post-commit auto-snapshot" --source git-hook 2>/dev/null || true
fi
"""

POST_CHECKOUT_HOOK = f"""\
#!/bin/sh
{HOOK_MARKER}
# Auto-snapshot after branch switch for Stitch context preservation.

if command -v stitch >/dev/null 2>&1; then
    stitch snap -m "post-checkout auto-snapshot (branch switch)" --source git-hook 2>/dev/null || true
elif command -v python3 >/dev/null 2>&1; then
    python3 -m xstitch.cli snap -m "post-checkout auto-snapshot (branch switch)" --source git-hook 2>/dev/null || true
fi
"""


def _find_git_hooks_dir(project_path: str) -> Path | None:
    git_dir = run_git(["rev-parse", "--git-dir"], cwd=project_path)
    if not git_dir:
        return None
    hooks_dir = Path(project_path) / git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    return hooks_dir


def install_hooks(project_path: str):
    hooks_dir = _find_git_hooks_dir(project_path)
    if not hooks_dir:
        print("Not a git repository. Cannot install hooks.")
        return

    hooks = {
        "post-commit": POST_COMMIT_HOOK,
        "post-checkout": POST_CHECKOUT_HOOK,
    }

    for name, content in hooks.items():
        hook_path = hooks_dir / name
        if hook_path.exists():
            existing = hook_path.read_text()
            if HOOK_MARKER in existing:
                print(f"  Hook {name} already installed, updating...")
                hook_path.write_text(content)
            else:
                # Append to existing hook
                with open(hook_path, "a") as f:
                    f.write(f"\n\n{content}")
                print(f"  Appended Stitch hook to existing {name}")
        else:
            hook_path.write_text(content)
            print(f"  Installed {name} hook")

        # Make executable
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

    print("Git hooks installed. Snapshots will be taken automatically on commit.")


def uninstall_hooks(project_path: str):
    hooks_dir = _find_git_hooks_dir(project_path)
    if not hooks_dir:
        print("Not a git repository.")
        return

    for name in ["post-commit", "post-checkout"]:
        hook_path = hooks_dir / name
        if not hook_path.exists():
            continue

        content = hook_path.read_text()
        if HOOK_MARKER not in content:
            continue

        # If the entire hook is ours, remove it
        lines = content.split("\n")
        stitch_start = None
        for i, line in enumerate(lines):
            if HOOK_MARKER in line:
                stitch_start = max(0, i - 1)  # include the shebang if it's ours
                break

        if stitch_start is not None and stitch_start <= 1:
            hook_path.unlink()
            print(f"  Removed {name} hook")
        else:
            # Remove just our section
            cleaned = []
            skip = False
            for line in lines:
                if HOOK_MARKER in line:
                    skip = True
                    continue
                if skip and line.strip() == "":
                    skip = False
                    continue
                if not skip:
                    cleaned.append(line)
            hook_path.write_text("\n".join(cleaned))
            print(f"  Removed Stitch section from {name} hook")

    print("Git hooks uninstalled.")
