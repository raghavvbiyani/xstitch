"""DEPRECATED: Tests have moved to tests/unit/ and tests/integration/.

This file re-exports all original test classes so that
``pytest tests/test_robustness.py`` continues to discover them during
the transition period.  New tests should be added to the appropriate
sub-directory instead.
"""

from __future__ import annotations

# --- unit tests ---
from tests.unit.diagnostics.test_healthcheck import TestHealthCheck  # noqa: F401
from tests.unit.diagnostics.test_doctor import TestDoctor  # noqa: F401
from tests.unit.integrations.test_discovery import TestDiscovery  # noqa: F401
from tests.unit.integrations.test_enforcement import TestEnforcement  # noqa: F401
from tests.unit.integrations.test_registry import TestGlobalSetup  # noqa: F401
from tests.unit.integrations.test_registry import TestToolRegistryCompleteness  # noqa: F401
from tests.unit.core.test_store import TestStoreErrorHandling  # noqa: F401
from tests.unit.mcp.test_server import TestMCPServerStartup  # noqa: F401
from tests.unit.mcp.test_transport import TestMCPDualProtocol  # noqa: F401
from tests.unit.cli.test_commands import TestCLIIdFlag  # noqa: F401
from tests.unit.test_intelligence import TestIntelligence  # noqa: F401

# --- integration tests ---
from tests.integration.test_storage import TestStorageRelocation  # noqa: F401
from tests.integration.test_migration import TestMigration  # noqa: F401
from tests.integration.test_hook_handler import TestHookHandler  # noqa: F401
from tests.integration.test_ttl_cleanup import TestTTLCleanup  # noqa: F401
from tests.integration.test_toml_mcp import TestTomlMcpInjection  # noqa: F401
from tests.integration.test_mcp_e2e import TestMCPProtocolE2E  # noqa: F401
from tests.integration.test_tool_isolation import TestToolIsolation  # noqa: F401
