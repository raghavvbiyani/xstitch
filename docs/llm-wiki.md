# Stitch LLM Wiki

Stitch now includes an optional LLM-wiki scaffold for durable project knowledge.
It is inspired by Andrej Karpathy's LLM Wiki pattern: raw sources stay
immutable, agents compile reusable markdown synthesis, `index.md` is the
navigation surface, and `log.md` is an append-only audit trail.

## Why This Belongs in Stitch

Snapshots and decisions are task history. They are chronological and precise,
but they are not always the best shape for long-lived project knowledge.

The wiki layer is for synthesized knowledge that should survive many tasks:

- project architecture notes
- recurring workflows
- source summaries
- cross-task decisions
- contradictions and open questions
- reusable answers from prior investigations

This reduces future rediscovery without dumping every raw snapshot into the
next agent's context.

## Commands

```bash
stitch wiki init
stitch wiki status
stitch wiki log --kind ingest --subject "README" --message "Summarized setup flow and updated project overview."
```

The wiki lives under the current project's Stitch storage:

```text
~/.ahcp/projects/<project-key>/wiki/
├── schema.md
├── index.md
├── log.md
├── raw/
├── sources/
└── pages/
```

## Operating Rules

- `raw/` is immutable source material. Agents read it but do not rewrite it.
- `sources/` contains one summary page per raw input, with provenance.
- `pages/` contains generated project/topic/entity pages.
- `index.md` is updated after each ingest or reusable query.
- `log.md` is append-only and should get one entry per ingest/query/lint pass.
- Contradictions are not overwritten silently. Agents should record them and ask
  the user when the interpretation matters.

## Integration With Routing

The wiki does not replace task matching. It complements it:

- auto-route decides whether to load a task, ask for confirmation, or start new
- task snapshots preserve exact chronology
- the wiki preserves durable synthesis that future tasks can reuse

When in doubt, an agent should ask the user before committing ambiguous context
into the wiki.
