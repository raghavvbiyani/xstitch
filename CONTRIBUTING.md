# Contributing to Stitch

## Getting Started

1. Clone the repo:
   ```bash
   git clone https://github.com/raghavvbiyani/xstitch.git && cd xstitch
   ```

2. Install in editable mode:
   ```bash
   pip3 install -e .
   ```

3. Run tests:
   ```bash
   python3 -m pytest tests/ -q
   ```

4. Optional search dependencies:
   ```bash
   pip3 install -e ".[search]"
   ```

## Project Structure

```
xstitch/
├── xstitch/                    # Main package
│   ├── core/                # Re-exports: models, store, capture, log
│   ├── search/              # Search engine: BM25, fuzzy, embeddings, index
│   ├── integrations/        # Re-exports: tool registry, discovery, enforcement
│   │   └── tools/           # Per-tool integration definitions
│   ├── mcp/                 # Re-exports: MCP server, tools
│   ├── diagnostics/         # Re-exports: doctor, healthcheck
│   ├── automation/          # Re-exports: hooks, daemon, launchd
│   ├── models.py            # Task, Snapshot, Decision dataclasses (canonical)
│   ├── store.py             # Storage engine (canonical)
│   ├── mcp_server.py        # MCP server with dual-protocol (canonical)
│   ├── global_setup.py      # Tool registry + OOP hierarchy (canonical)
│   └── ...                  # Other canonical modules
├── tests/
│   ├── conftest.py          # Shared fixtures
│   ├── unit/                # Fast, isolated tests
│   ├── integration/         # Tests requiring subprocess or filesystem
│   └── e2e/                 # End-to-end cross-tool tests
├── docs/                    # Architecture, search design, adding-tools guide
├── pyproject.toml           # Build config, entry points, optional deps
└── README.md
```

## Architecture Note

The subpackages (`core/`, `integrations/`, `mcp/`, etc.) are re-export shims. Canonical code lives in the top-level files (e.g., `xstitch/store.py`, `xstitch/models.py`). This preserves `unittest.mock.patch` compatibility since patches target `xstitch.store.X` which is the actual module.

## Code Style

- Python 3.10+ (stdlib only for core, optional deps guarded)
- No external runtime dependencies
- Type hints encouraged
- Logging via `xstitch/log.py` to stderr (never pollute stdout, which is for MCP/CLI output)
- Atomic file operations: write to temp file + rename

## Testing

- Run all tests: `python3 -m pytest tests/ -q`
- Run only unit tests: `python3 -m pytest tests/unit/ -q`
- Run only integration tests: `python3 -m pytest tests/integration/ -q`
- New tests go in `tests/unit/` or `tests/integration/` (NOT `test_robustness.py`)
- Follow existing naming: `test_<module>.py`, class `Test<Feature>`, method `test_<behavior>`
- Use `patch("xstitch.<module>.<name>")` for mocking (target canonical modules)

## Adding a New Tool Integration

See [docs/adding-tools.md](docs/adding-tools.md) for the full guide. In short:

1. Create `xstitch/integrations/tools/mytool.py` subclassing `ToolIntegration`
2. Register via entry point in `pyproject.toml`
3. Add detection, global config, and project injection logic

External packages can register tools without modifying Stitch core.

## Pull Request Guidelines

- All tests must pass
- Include tests for new functionality
- Don't break existing working code
- Backward compatibility is critical (re-export shims exist for this reason)
