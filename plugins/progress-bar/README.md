# progress-bar

**A progress bar above the prompt that Claude fills in itself: an exact percentage, what it is doing now, time elapsed and time left.**

```text
  ━━━━━━━━━━────────────── 43%  Updating the auth tests · 6m12s · ~8m left
╭──────────────────────────────────────────────────────────────────────────╮
│ >                                                                        │
╰──────────────────────────────────────────────────────────────────────────╯
```

A long task with no sign of progress is the old "Installing..." screen. This plugin gives Claude a `set_progress` tool and tells it to call the tool as it works. You glance at the bar, see `43%`, and know whether you can leave.

| The bar says | It means |
| --- | --- |
| `━━━━──── 43%  Refactoring auth · 6m12s · ~8m left` | Claude is working. Cyan bar, elapsed time, and an estimate of the time left. |
| `━━━━──── 43%  Refactoring auth · 6m12s · last update 4m ago` | Claude is working but has not reported for a few minutes. Treat the percentage as old. |
| `━━━━──── 43%  Refactoring auth · 6m12s · paused` | Claude's turn ended before 100%: it stopped, probably to ask you something. The bar is dim and the clock has stopped. |
| `━━━━━━━━ 100% ✓  All tests pass · done in 9m41s` | Claude reported the task finished. The bar is green and shows the total time. |

This is a **mod**: a plugin of function hooks that draws into Claude Code's own interface. It needs Claude Code 2.1.291 or later. It runs no scripts and makes no network calls.

## Install

```text
/plugin marketplace add MohammadMD1383/claude-plugins
/plugin install progress-bar@mohammadmd-plugins
```

## How it works

- **A tool.** The plugin registers `set_progress` (listed to Claude as `mcp__progress-bar__set_progress`) with two inputs: `percent` (0-100) and an optional `step` (a few words on what it is doing). Claude Code never asks you to approve it, and it is always in Claude's tool list, not hidden behind tool search.
- **An instruction.** A short section is added to Claude's system prompt. It asks Claude to report once after sizing up a task, then at each real milestone (not after every tool call), to count work still ahead (tests, verification) in the percentage, to lower the number when it finds more work than it expected, and to report 100 only when everything is finished and verified. It is skipped for quick questions and one-step edits, and where nothing is drawn (`claude -p`, the SDK).
- **A bar.** The bar draws above the prompt in the terminal and the desktop app. It shrinks on a narrow terminal, dropping words from the end first and never the percentage.
- **Subagents do not move it.** Only the main conversation reports, so the bar always describes the whole task.

### Time left

The estimate is the speed Claude's reported percentage has moved since its first report of the task, extrapolated to 100. It appears once the percentage has moved at least 5 points over at least 30 seconds, and counts down between reports. It is only as good as Claude's percentages, so it is prefixed with `~`. Tasks whose steps take very different times (a 30-second edit, then a 20-minute test suite) will make it jump.

### Clearing

- The next prompt you send clears a bar that reached 100%. A bar left unfinished stays, since the next prompt is usually the same task.
- `✕` (shown when Claude is not working) hides the bar until Claude's next report.
- `/clear` empties it. To collapse the band for a moment, use `ctrl+x ctrl+a`.

## Configuration

Change these in `/config`, or under `pluginConfigs` in `settings.json`.

| Option | Default | What it does |
| --- | --- | --- |
| `auto_report` | `true` | Add the instruction to Claude's system prompt. Turn it off to only have the tool available, and ask for progress in your own words or in `CLAUDE.md`. |
| `show_eta` | `true` | Show `~12m left` once there is enough to estimate from. |
| `bar_width` | `24` | Cells in the bar, 10 to 60. |

Example, to get a short bar without estimates:

```json
{
  "pluginConfigs": {
    "progress-bar@mohammadmd-plugins": {
      "options": { "show_eta": false, "bar_width": 12 }
    }
  }
}
```

## Works well with

[usage-bar](../usage-bar), which shows how much context and usage limit is left, in the band next to this one's.

## Development

```bash
claude plugin validate .
claude plugin test .          # formatting, estimates, layout, the tool, the prompt section, permissions, drawing, /clear
claude --plugin-dir .         # try it locally; edits hot-reload
```

Layout:

- `hooks/register.tsx` holds the hooks. `session.start` registers the tool and starts a 5-second clock that only runs while a task is unfinished, `tool.call` serves it, `tool.describe` and `tool.check` keep it listed and unprompted, `prompt.compose` adds the instruction, and `ui.render` draws the `AbovePrompt` band.
- `hooks/format.ts` holds the pure logic: validating a report, durations, the estimate, the layout.
- `types/index.d.ts` is the session-state contract.

## License

MIT
