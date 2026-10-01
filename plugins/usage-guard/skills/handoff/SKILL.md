---
description: Checkpoint the work and write an agent-agnostic HANDOFF.md so Claude, Codex or a human can resume
argument-hint: "[extra notes for the hand-off]"
disable-model-invocation: true
---
Hand the current work off cleanly, as if you are about to lose access mid-task. Spend as few tool calls and tokens as possible: don't re-read files you already know, don't explore.

1. **Stabilize.** Finish the edit in progress only if it is a few lines away; otherwise revert it. Leave no broken syntax or half-applied refactors. Run a quick check (build, typecheck or the narrowest relevant test) only if it is cheap.
2. **Write the hand-off note** at the repo root, in `${user_config.handoff_file}` (replace older content, including any `usage-guard:auto` block). Write it for a reader with zero context: another agent such as Codex, a human, or a future Claude session. Use these sections, keeping each terse:
   - **Goal**: what the overall task or milestone is, and where its spec or backlog lives.
   - **Done**: completed items, with commit hashes.
   - **In progress**: the exact files and functions touched, what works, what's left.
   - **Next steps**: ordered and concrete, so the next agent can start immediately.
   - **Decisions and gotchas**: choices made and why, traps found, things not to retry.
   - **Verify**: the commands that prove the work (build, tests, lint).
3. **Sync the tracker.** If the project keeps a task list, backlog or milestone file, update item statuses so they match reality.
4. **Checkpoint.** If this is a git repo and the user hasn't said otherwise, stage your changes plus the hand-off note (never secrets or build output) and commit on the current branch with `wip: checkpoint (see <hand-off file>)`. Don't push unless that is already the workflow.
5. **Report.** Tell the user in at most three lines: what state things are in, where the hand-off note is, and (if known) when the usage limit resets.

Additional notes from the user: $ARGUMENTS
