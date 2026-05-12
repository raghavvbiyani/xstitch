# Context Compaction Hooks

Stitch cannot assume every AI tool exposes a "before compaction" event. The integration now uses hard lifecycle hooks where they exist and falls back to frequent checkpoints everywhere else.

## What Was Going Wrong

Manual `snap` entries only save the message the agent pushes plus repository state. If the agent completes research inside the chat but does not call `snap` or `checkpoint` before the tool compacts, that research can be lost. A later agent then sees only the older saved task state and may repeat the same file reads, web research, or log investigation.

## Current Strategy

| Tool | Pre-compaction support | Stitch behavior |
|------|------------------------|-----------------|
| Claude Code | `PreCompact` for manual and automatic compaction | Save a rich `hook-pre-compact` snapshot, copy the last 1 MB of the session transcript into the task folder, and record recent tool activity before compaction runs. |
| Gemini CLI | `PreCompress` | Save the same pre-summarization checkpoint before Gemini summarizes history. Gemini marks this hook advisory/asynchronous, so it cannot block compression. |
| Codex | No documented first-class pre-compact hook found | Use MCP/instructions plus proactive `checkpoint` before long research, after decisions, after failed experiments, and every 2-3 minutes. |
| Cursor, Windsurf, Zed, Copilot CLI, Aider | No stable documented pre-compaction lifecycle hook in the integrations Stitch can configure today | Use injected instructions, MCP snapshots where available, and proactive `checkpoint` cadence. |

## What Gets Saved

Pre-compaction checkpoints include:

- the active task and current handoff bundle
- recent significant tool actions from the session
- trigger metadata such as `auto`, `manual`, `PreCompact`, or `PreCompress`
- the transcript tail path when the tool provides `transcript_path`
- normal git/repo snapshot metadata from `capture_snapshot`

Transcript tails are stored under:

```text
~/.ahcp/projects/<project-key>/tasks/<task-id>/compact-transcripts/
```

Only the tail is copied to keep the checkpoint bounded and avoid turning Stitch into a raw-chat archive.

## Operational Rule For Agents

Hooks are a safety net, not the only source of truth. Agents must still push context when work becomes meaningful:

```bash
stitch snap -m "what was done + outcome"
stitch decide -p "problem" -c "chosen" -a "alternatives" -r "why"
stitch checkpoint -s "summary" -d "decisions" -e "experiments" -f "failures" -q "questions"
```

Run `checkpoint` before context-heavy research, before likely compaction, and before switching tools. This is mandatory for tools that do not expose a pre-compaction hook.

## References

- Claude Code hooks: `PreCompact` and `PostCompact` lifecycle events.
  <https://docs.claude.com/en/docs/claude-code/hooks>
- Gemini CLI hooks: `BeforeAgent`, `AfterTool`, `SessionEnd`, and `PreCompress`.
  <https://geminicli.com/docs/hooks/reference/>
- Codex pre-compact hook tracking issue documenting that first-class `pre_compact` / `post_compact` hooks were not available in the checked CLI build.
  <https://github.com/openai/codex/issues/16098>
