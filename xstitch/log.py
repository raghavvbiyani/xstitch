"""Structured logging for Stitch.

All Stitch output goes through this module so agents and users see consistent,
informative status messages. Agents see these in their tool output / thinking
console; users see them as part of the agent's response.

IMPORTANT: All output goes to stderr. stdout must stay clean because Claude
Code hooks capture stdout as structured JSON — any stray print to stdout
corrupts the hook response and breaks the integration.

Prefix convention:
  [Stitch OK]      — operation succeeded
  [Stitch INFO]    — informational status
  [Stitch WARNING] — something is wrong but recoverable
  [Stitch ERROR]   — operation failed, fix needed
  [Stitch FIX]     — actionable fix instruction
"""

from __future__ import annotations

import sys


_PREFIX = "Stitch"


def ok(message: str):
    """Success message — operation completed."""
    print(f"  [{_PREFIX} OK] {message}", file=sys.stderr)


def info(message: str):
    """Informational status — what's happening now."""
    print(f"  [{_PREFIX}] {message}", file=sys.stderr)


def warn(message: str, fix: str = ""):
    """Warning — something is wrong but we can continue."""
    print(f"  [{_PREFIX} WARNING] {message}", file=sys.stderr)
    if fix:
        print(f"  [{_PREFIX} FIX] {fix}", file=sys.stderr)


def error(message: str, fix: str = ""):
    """Error — operation failed, fix needed."""
    print(f"  [{_PREFIX} ERROR] {message}", file=sys.stderr)
    if fix:
        print(f"  [{_PREFIX} FIX] {fix}", file=sys.stderr)


def status(phase: str, detail: str):
    """Phase-based status update — shows what Stitch is doing step by step."""
    print(f"  [{_PREFIX} {phase}] {detail}", file=sys.stderr)


def saved(what: str, detail: str = ""):
    """Confirm something was saved to disk."""
    msg = f"  [{_PREFIX} SAVED] {what}"
    if detail:
        msg += f" — {detail}"
    print(msg, file=sys.stderr)


def skipped(what: str, reason: str):
    """Something was intentionally skipped (dedup, etc.)."""
    print(f"  [{_PREFIX} SKIPPED] {what}: {reason}", file=sys.stderr)


def troubleshoot(problem: str, fix: str):
    """Explicit troubleshooting guidance for agents and users."""
    print(f"  [{_PREFIX} PROBLEM] {problem}", file=sys.stderr)
    print(f"  [{_PREFIX} FIX] {fix}", file=sys.stderr)
