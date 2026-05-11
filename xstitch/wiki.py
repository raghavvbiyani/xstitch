"""Local LLM-wiki scaffold for Stitch projects.

The wiki is intentionally just markdown files under the project's Stitch
storage directory. Stitch does not call an LLM itself; it gives agents a
durable structure for compounding task knowledge across sessions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .store import Store


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def wiki_dir(store: Store) -> Path:
    return store.local_dir / "wiki"


def init_wiki(store: Store) -> Path:
    """Create the wiki scaffold if it does not already exist."""
    root = wiki_dir(store)
    raw = root / "raw"
    pages = root / "pages"
    sources = root / "sources"
    for d in (raw, pages, sources):
        d.mkdir(parents=True, exist_ok=True)

    _write_if_missing(root / "schema.md", _schema_md(store))
    _write_if_missing(root / "index.md", _index_md())
    _write_if_missing(root / "log.md", "# Stitch LLM Wiki Log\n\n")
    _write_if_missing(raw / "README.md", _raw_readme())
    _write_if_missing(sources / "README.md", _sources_readme())
    _write_if_missing(pages / "project-overview.md", _project_overview_md(store))

    append_log(store, "init", "wiki", "Initialized Stitch LLM wiki scaffold.")
    return root


def append_log(store: Store, kind: str, subject: str, message: str) -> Path:
    """Append one chronological wiki log entry."""
    root = wiki_dir(store)
    root.mkdir(parents=True, exist_ok=True)
    log = root / "log.md"
    if not log.exists():
        log.write_text("# Stitch LLM Wiki Log\n\n")
    entry = (
        f"## [{_now_iso()}] {kind} | {subject}\n\n"
        f"{message.strip() or '(no details)'}\n\n"
    )
    with log.open("a", encoding="utf-8") as f:
        f.write(entry)
    return log


def status(store: Store) -> dict:
    root = wiki_dir(store)
    files = list(root.rglob("*.md")) if root.exists() else []
    return {
        "path": str(root),
        "exists": root.exists(),
        "markdown_files": len(files),
        "schema": str(root / "schema.md"),
        "index": str(root / "index.md"),
        "log": str(root / "log.md"),
    }


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _schema_md(store: Store) -> str:
    return f"""# Stitch LLM Wiki Schema

This wiki is a persistent, agent-maintained markdown knowledge layer for one
project. It follows the LLM-wiki pattern: raw sources stay immutable, generated
pages accumulate synthesis, `index.md` is the navigation surface, and `log.md`
is an append-only audit trail.

Project: `{store.project_path}`

## Directories

- `raw/` — immutable source drops. Agents may read these files but must not edit
  them after ingestion.
- `sources/` — one generated source summary per raw input. Include provenance,
  date processed, key claims, and unresolved questions.
- `pages/` — generated project/topic/entity pages. These pages may be revised
  as new evidence arrives.
- `index.md` — content-oriented catalog. Update after every ingest or material
  query result.
- `log.md` — chronological append-only history of ingest/query/lint events.

## Agent Rules

1. Prefer provenance over polished prose. Every factual claim that comes from a
   source should point to `raw/` or `sources/`.
2. Never overwrite a contradiction silently. Add a "Contradictions / Open
   Questions" section and ask the user when the right interpretation matters.
3. When a user asks a question and the answer is reusable, file the answer back
   into `pages/` and append a `query` log entry.
4. Before a long handoff, update `pages/project-overview.md`, `index.md`, and
   `log.md` so the next agent can load context without rereading every task
   snapshot.
5. Keep raw task history in Stitch snapshots/decisions. Keep synthesized,
   reusable project knowledge here.

## Suggested Workflows

### Ingest

1. Add or identify a source under `raw/`.
2. Create/update a summary under `sources/`.
3. Update affected pages under `pages/`.
4. Update `index.md`.
5. Append to `log.md` with `stitch wiki log --kind ingest`.

### Query

1. Read `index.md` first.
2. Read only relevant `pages/` and `sources/`.
3. Answer with citations to wiki/source files.
4. If the answer is reusable, file it into `pages/` and append a `query` log.

### Lint

Periodically check for orphan pages, missing backlinks, stale claims, duplicate
concepts, contradictions, and pages that need sources.
"""


def _index_md() -> str:
    return """# Stitch LLM Wiki Index

## Project Pages

- [Project overview](pages/project-overview.md) — durable synthesis of the
  project's current state.

## Source Summaries

Add source summaries here after ingest.

## Open Questions

Track questions that need user input, source verification, or follow-up work.
"""


def _raw_readme() -> str:
    return """# Raw Sources

Drop immutable source material here: specs, transcripts, exported chats,
research notes, incident reports, PDFs converted to markdown, or other files an
agent should ingest into the wiki.

Agents should read these files and write summaries into `../sources/`, but
should not rewrite raw source files after ingestion.
"""


def _sources_readme() -> str:
    return """# Source Summaries

Create one markdown summary per raw source. Include:

- Source path
- Date ingested
- Key claims
- Links to affected wiki pages
- Contradictions or open questions
"""


def _project_overview_md(store: Store) -> str:
    return f"""# Project Overview

Project path: `{store.project_path}`

## Current Synthesis

No synthesis has been written yet.

## Durable Decisions

Add cross-task decisions that future agents should know without replaying every
task snapshot.

## Open Questions

Add unresolved questions that affect future work.
"""
