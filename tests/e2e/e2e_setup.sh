#!/usr/bin/env bash
#
# Stitch Cross-Agent E2E Test — Phase 0 Setup
#
# Creates an isolated test project, initializes Stitch, seeds a task with
# a deliberate dead-end decision and a skeleton implementation file.
# Run this ONCE before starting the 4-phase cross-agent test.
#
# Usage:
#   bash e2e_setup.sh          # fresh setup (destroys previous test data)
#   bash e2e_setup.sh --clean  # only clean up, don't recreate
#
set -euo pipefail

TEST_DIR="/tmp/stitch-e2e-crossagent"
Stitch="python3 -m xstitch.cli"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[setup]${NC} $*"; }
ok()    { echo -e "${GREEN}[  ok ]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn ]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

# --- Clean up ---
if [ -d "$TEST_DIR" ]; then
    info "Removing previous test project at $TEST_DIR"
    rm -rf "$TEST_DIR"
fi

if [ "${1:-}" = "--clean" ]; then
    ok "Cleaned up. Exiting."
    exit 0
fi

# --- Verify Stitch is installed ---
info "Verifying Stitch installation..."
$Stitch --help >/dev/null 2>&1 || fail "Stitch not installed. Run: pip3 install -e /path/to/xstitch"
ok "Stitch is installed"

# --- Create test project ---
info "Creating test project at $TEST_DIR"
mkdir -p "$TEST_DIR"
cd "$TEST_DIR"

git init
git config user.email "e2e-test@xstitch.dev"
git config user.name "Stitch E2E Test"

cat > README.md << 'READMEEOF'
# Rate Limiter Library

A Python rate-limiting library (E2E test project for Stitch cross-agent verification).

## Status

Work in progress — being built across multiple AI agents to test context handoff.
READMEEOF

cat > rate_limiter.py << 'PYEOF'
"""Rate limiter library — work in progress.

This file was created during initial project setup.
The implementation will be built incrementally across multiple AI agent sessions.
"""


class RateLimiter:
    """Base rate limiter interface.

    Subclasses should implement the `allow` method with a specific algorithm.
    """

    def allow(self, key: str) -> bool:
        """Check if a request identified by `key` should be allowed."""
        raise NotImplementedError
PYEOF

git add -A
git commit -m "Initial project setup: README and rate_limiter skeleton"
ok "Git repo initialized with initial commit"

# --- Initialize Stitch ---
info "Running Stitch auto-setup..."
$Stitch auto-setup
ok "Stitch auto-setup complete"

# --- Create the task ---
info "Creating the rate limiter task..."
$Stitch task new "Build rate limiter library" \
    -o "Create a Python rate-limiting library with pluggable algorithms and storage backends. Must support token bucket and/or sliding window, work across multiple processes, and include comprehensive tests." \
    -t "python,rate-limiter,library,backend"
ok "Task created"

# --- Seed the dead-end decision (sliding window too complex for MVP) ---
info "Seeding dead-end decision: sliding window abandoned..."
$Stitch decide \
    -p "Rate limiting algorithm for MVP" \
    -c "ABANDONED — sliding window is too complex for MVP" \
    -a "token bucket,sliding window,fixed window,leaky bucket" \
    -r "Tried implementing sliding window with sorted sets for O(log n) window checks. The implementation became too complex: needed atomic cleanup of expired entries, a sorted container for efficient range queries, and careful handling of clock skew. For an MVP this is overengineered. Abandoning this approach. Next session should pick a simpler algorithm like token bucket or fixed window."

# --- Seed snapshot about the failed attempt ---
info "Seeding dead-end snapshot..."
$Stitch snap -m "FAILED APPROACH: Spent 45 minutes on sliding window implementation. Got bogged down in sorted-set cleanup logic and atomic operations for concurrent access. The partial code was deleted — it was not salvageable. DO NOT retry sliding window for MVP. Start fresh with a simpler algorithm (token bucket recommended)."

# --- Seed another snapshot about project exploration ---
$Stitch snap -m "Project exploration: Reviewed existing rate limiter libraries (limits, ratelimit, throttle). Our library needs to be simpler with zero external dependencies, support pluggable backends, and work in multi-process environments."

# --- Update task state ---
info "Setting task state..."
$Stitch task update \
    --state "Sliding window approach ABANDONED after 45 min of failed implementation. Project skeleton created with base RateLimiter class. No working algorithm yet." \
    --next "1. Choose a simpler algorithm (token bucket or fixed window recommended). 2. Implement the core limiter class. 3. Add basic in-memory storage. 4. Write initial tests."

# --- Commit Stitch data ---
git add -A
git commit -m "Stitch: task created with dead-end decision seeded"
ok "Stitch data committed"

# --- Summary ---
echo ""
echo "=============================================="
echo -e "${GREEN}Phase 0 Setup Complete${NC}"
echo "=============================================="
echo ""
echo "  Test project: $TEST_DIR"
echo "  Task: Build rate limiter library"
echo ""
echo "  Seeded data:"
echo "    - 1 dead-end decision (sliding window abandoned)"
echo "    - 2 snapshots (failed approach + project exploration)"
echo "    - Task state + next steps set"
echo "    - Skeleton rate_limiter.py"
echo ""
echo "  Next: Run Phase 1 with your first agent (see E2E_TEST_PLAN.md)"
echo ""
echo "  Verify baseline:"
echo "    python3 $(dirname "$0")/e2e_verify.py --phase 0 --project $TEST_DIR"
echo ""
