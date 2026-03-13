"""Self-healing health checks for Stitch installations.

Detects broken editable installs, missing modules, version mismatches,
and provides actionable fix instructions. Used by `stitch doctor` and
integrated into `auto_setup()` for self-healing.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import sysconfig
from pathlib import Path
from typing import Optional


# Common locations where xstitch source might live
_KNOWN_SOURCE_DIRS = [
    "xstitch",
    "AgentHandOffAndContextProtocol",
]


def quick_check() -> dict:
    """Fast health check suitable for running on every auto-setup.

    Returns {"status": "ok"} or {"status": "broken"|"warning", "reason": ..., "fix": ...}
    """
    try:
        import xstitch as _mod
        mod_file = getattr(_mod, "__file__", None)
        if mod_file and not Path(mod_file).exists():
            fix = _suggest_install_command()
            return {
                "status": "broken",
                "reason": f"xstitch.__file__ points to non-existent path: {mod_file}",
                "fix": fix,
            }
        return {"status": "ok"}
    except ImportError:
        pass

    editable = check_editable_install()
    if editable["status"] == "broken":
        return editable

    fix = _suggest_install_command()
    return {
        "status": "broken",
        "reason": "xstitch module cannot be imported",
        "fix": fix,
    }


def check_editable_install() -> dict:
    """Detect broken editable installs (source dir deleted after pip install -e)."""
    site_packages = Path(sysconfig.get_path("purelib"))

    for pth_file in site_packages.glob("__editable__.xstitch-*.pth"):
        # The finder module follows pip's naming: dots->underscores, dashes->underscores, + "_finder"
        finder_name = pth_file.stem.replace(".", "_").replace("-", "_") + "_finder"
        finder_file = site_packages / f"{finder_name}.py"
        if not finder_file.exists():
            # Try without _finder suffix (varies by setuptools version)
            finder_name_alt = pth_file.stem.replace(".", "_").replace("-", "_")
            finder_file = site_packages / f"{finder_name_alt}.py"
            if not finder_file.exists():
                continue

        content = finder_file.read_text()
        for match in re.finditer(r"'([^']+/xstitch)'", content):
            source_path = Path(match.group(1))
            if not source_path.exists():
                fix = _suggest_install_command()
                return {
                    "status": "broken",
                    "reason": f"Editable install points to deleted path: {source_path}",
                    "fix": fix,
                }

    dist_info_dirs = list(site_packages.glob("xstitch-*.dist-info"))
    for dist_info in dist_info_dirs:
        direct_url = dist_info / "direct_url.json"
        if direct_url.exists():
            try:
                import json
                data = json.loads(direct_url.read_text())
                if data.get("dir_info", {}).get("editable"):
                    url = data.get("url", "")
                    if url.startswith("file://"):
                        source_dir = Path(url.replace("file://", ""))
                        if not source_dir.exists():
                            fix = _suggest_install_command()
                            return {
                                "status": "broken",
                                "reason": f"Editable install source deleted: {source_dir}",
                                "fix": fix,
                            }
            except Exception:
                pass

    return {"status": "ok"}


def check_python_environment() -> dict:
    """Verify python3 resolves to a Python that has xstitch available."""
    python3 = shutil.which("python3")
    if not python3:
        return {
            "status": "broken",
            "reason": "python3 not found in PATH",
            "fix": "Install Python 3.10+ and ensure python3 is in PATH",
        }

    if python3 != sys.executable:
        return {
            "status": "warning",
            "reason": f"python3 ({python3}) differs from current interpreter ({sys.executable})",
            "fix": "Ensure xstitch is installed for the python3 in your PATH",
        }

    return {"status": "ok"}


def check_version_consistency() -> dict:
    """Check that installed metadata version matches the actual module version."""
    try:
        from importlib.metadata import version as pkg_version
        installed_version = pkg_version("xstitch")
    except Exception:
        return {"status": "ok"}

    try:
        import xstitch
        module_version = getattr(xstitch, "__version__", None)
        if module_version and module_version != installed_version:
            return {
                "status": "warning",
                "reason": (
                    f"Version mismatch: pip says {installed_version}, "
                    f"module says {module_version}"
                ),
                "fix": "pip3 install -e <path-to-xstitch> to re-sync",
            }
    except ImportError:
        pass

    return {"status": "ok"}


def diagnose() -> list[dict]:
    """Run all health checks and return a structured report."""
    checks = []

    checks.append({
        "name": "Python",
        "detail": f"{sys.executable} ({sys.version.split()[0]})",
        **check_python_environment(),
    })

    editable = check_editable_install()
    if editable["status"] != "ok":
        checks.append({"name": "Editable install", **editable})
    else:
        checks.append({"name": "Editable install", "status": "ok", "detail": "No broken editable installs"})

    try:
        import xstitch
        mod_file = getattr(xstitch, "__file__", "unknown")
        checks.append({
            "name": "Stitch module",
            "status": "ok",
            "detail": f"v{xstitch.__version__} at {mod_file}",
        })
    except ImportError:
        fix = _suggest_install_command()
        checks.append({
            "name": "Stitch module",
            "status": "broken",
            "reason": "Cannot import xstitch",
            "fix": fix,
        })

    checks.append({"name": "Version consistency", **check_version_consistency()})

    return checks


def _suggest_install_command() -> str:
    """Find the best install command based on what source is available."""
    source = _find_stitch_source()
    if source:
        return f"pip3 uninstall xstitch -y && pip3 install -e {source}"
    return (
        "pip3 uninstall xstitch -y && "
        "pip3 install 'git+https://github.com/raghavvbiyani/xstitch.git'"
    )


def _find_stitch_source() -> Optional[str]:
    """Search common locations for the xstitch source package."""
    search_roots = []

    cwd = Path.cwd()
    search_roots.append(cwd)
    search_roots.append(cwd.parent)

    home = Path.home()
    for candidate in ["IdeaProjects", "Projects", "repos", "src", "code", "dev"]:
        d = home / candidate
        if d.is_dir():
            search_roots.append(d)

    seen = set()
    for root in search_roots:
        root = root.resolve()
        if root in seen:
            continue
        seen.add(root)

        if (root / "xstitch" / "__init__.py").exists() and (root / "pyproject.toml").exists():
            return str(root)

        try:
            for name in _KNOWN_SOURCE_DIRS:
                candidate = root / name
                if candidate.is_dir():
                    if (candidate / "xstitch" / "__init__.py").exists():
                        return str(candidate)
                for child in root.iterdir():
                    if child.is_dir() and name in child.name:
                        if (child / "xstitch" / "__init__.py").exists():
                            return str(child)
        except PermissionError:
            continue

    return None
