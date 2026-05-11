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

Use snapshots for chronological task progress. Use decisions for explicit
tradeoffs. Use the wiki when the knowledge becomes reusable across future
tasks or when it would be expensive for the next agent to rediscover.

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
- Wiki pages should be concise synthesis, not raw chat dumps. Link back to
  AHCP task IDs, source summaries, or raw files when the details matter.
- Do not use the wiki to resolve an ambiguous task match. First ask the user;
  then write the confirmed reusable knowledge.

## Suggested Agent Workflow

1. Run `stitch auto "<user prompt>"` at session start.
2. If the result is `needs_confirmation`, ask the user to choose a candidate or start fresh before loading/writing context.
3. During work, keep task progress in snapshots and decisions.
4. When a finding becomes reusable, update `pages/` or `sources/`, refresh `index.md`, and append `stitch wiki log`.
5. Before handoff, make sure the wiki captures durable project knowledge while the task checkpoint captures exact current state.

## Integration With Routing

The wiki does not replace task matching. It complements it:

- auto-route decides whether to load a task, ask for confirmation, or start new
- task snapshots preserve exact chronology
- `initial_user_prompt` snapshots preserve the exact first request for new tasks
- the wiki preserves durable synthesis that future tasks can reuse

When in doubt, an agent should ask the user before committing ambiguous context
into the wiki.
