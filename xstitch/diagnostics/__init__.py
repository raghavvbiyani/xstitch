"""Diagnostic tools for Stitch installation health.

This package provides organized import paths for:
  - doctor: Full system health check
  - healthcheck: Quick checks and editable install validation

Implementation note: actual code lives in xstitch.doctor and xstitch.healthcheck.
"""

from ..doctor import (  # noqa: F401
    run_doctor,
    format_doctor_report,
    PASS,
    FAIL,
    WARN,
    SKIP,
)
from ..healthcheck import (  # noqa: F401
    quick_check,
    check_editable_install,
    check_python_environment,
    check_version_consistency,
    diagnose,
)
