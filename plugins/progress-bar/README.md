# progress-bar

**A progress bar above the prompt for long work. Claude fills one in itself, and any script or command can fill in its own, with a warning when a job stalls or dies.**

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

And for the 40-minute shell job you cannot ask Claude about, because asking costs a turn and Claude can only guess:

```text
  ⠹ ━━━━━━━━━━━━──────────── 118/240 49%  screenshots · 21m · ~22m left · 5.6/min · page-118.png
  ⚠ ━━━━━━━━━━━━──────────── 118/240 49%  screenshots · no progress for 4m · 21m · last: page-118.png
  ✕ ━━━━━━━━━━━━──────────── 118/240 49%  screenshots · died at 118/240, process gone · after 21m
  ✓ ━━━━━━━━━━━━━━━━━━━━━━━━ 240/240 ✓  screenshots · done in 41m
```

This is a **mod**: a plugin of function hooks that draws into Claude Code's own interface. It needs Claude Code 2.1.291 or later. Claude's bar needs nothing else. Background jobs need `python3` (standard library only) and run a small helper process for the session; turn them off with `watch_jobs`. No network port is opened.

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

## Background jobs: `pbar`

Claude's percentage is an estimate it has to spend a turn to change. A long shell job (a test suite, a build, 240 screenshots) deserves a bar that is *measured*, not guessed, and that nobody has to ask. So jobs report to the bar themselves, with no model involved.

```bash
# Wrap anything. Its output and exit code pass through unchanged.
pbar run --label "screenshots" --total 240 --match 'saved' -- node shoot.js

# Or a pipeline.
./convert.sh | pbar pipe --label "convert" --total 500

# Or report from inside your own script.
pbar start deploy --label "Deploy" --total 12
pbar tick deploy --step "uploading assets"       # one more item done
pbar set deploy 9/12                              # or: pbar set deploy 75%
pbar done deploy                                  # or: pbar fail deploy --msg "exit 2"
```

Inside Claude Code's shell `pbar` is already on `PATH`. Claude is told to wrap commands that will take more than a couple of minutes in `pbar run` and run them in the background, so the bar appears without you asking. From a terminal of your own, `pbar` finds the newest running session by itself (`bin/pbar` is in the plugin's folder).

What `pbar run` follows, in order:

1. **`--total N`**: every output line is one item (or only lines matching `--match REGEX`).
2. Otherwise **fractions and percentages in the output** (`[118/240]`, `43%`), ignoring colors, carriage-return progress bars, dates and version numbers.
3. Otherwise nothing to measure, so the bar shows a block sliding along, one step per report. The row still tells you the job is alive and for how long it has been quiet.

### Is it done, or hung?

| The row says | It means |
| --- | --- |
| `⠹` (cyan, turning) | Reports are arriving. The spinner moves one step per report, so **a frozen spinner is a frozen job**. |
| `⚠` (yellow) `no progress for 4m` | The bar has not moved for longer than `stall_after` (default 2 minutes). A job whose items take minutes each is given proportionally more time, up to an hour. |
| `✕` (red) `died at 118/240, process gone` | The process `pbar run` watches is gone and never said so (killed, out of memory, closed terminal). Noticed within a few seconds. |
| `✕` (red) `failed at 118/240: exit 2` | The command ended with an error. |
| `✓` (green) `done in 41m` | It finished. |

Trouble sorts to the top; three rows show at once and the rest are counted. `✕` dismisses a finished row, and your next prompt clears the finished ones that succeeded.

You hear about it too: a stalled, dead, failed or finished job raises a toast and a native notification (`notify`), so you can leave the screen. Claude is **not** woken by default, since that costs a turn. Set `wake_claude` to `on-failure` (failures, deaths, stalls) or `always` (finishes too) and Claude is handed a short message about the job and picks the work back up. Claude can also read the jobs at any time with the `job_status` tool, instantly and without a shell.

### The socket

Reports travel over a unix socket the plugin listens on for the session, whose path is in `$PROGRESS_BAR_SOCK`. It is owner-only, there is no port and no HTTP, and every message is one JSON object per line. Anything that can write to a unix socket can report, no `pbar` needed:

```bash
echo '{"id":"deploy","op":"set","done":9,"total":12,"step":"uploading"}' | nc -U "$PROGRESS_BAR_SOCK"
```

| Field | Meaning |
| --- | --- |
| `id` | Letters, digits, `.`, `_`, `-`, up to 48. Required. Reusing an id updates that row. |
| `op` | `start` (begins the job over), `set`, `done` or `fail`. Required. |
| `label` | The name shown. Defaults to the id. |
| `done`, `total` | Items finished and in all. `inc` adds to the count instead. |
| `percent` | 0-100, for a job that does not count items. |
| `step` | A few words on what it is doing now. |
| `pid` | A process whose death means the job died. Only `pbar run` and `pipe` set it. |
| `idle` | Seconds the job has already been quiet, if it knows. |
| `msg` | Why it failed, or a closing note. |

Malformed lines are ignored. The plugin stamps times itself; it never trusts a sender's clock.

### Time left


For Claude's bar, the estimate is the speed its reported percentage has moved since its first report of the task, extrapolated to 100. It appears once the percentage has moved at least 5 points over at least 30 seconds, and counts down between reports. It is only as good as Claude's percentages, so it is prefixed with `~`. Tasks whose steps take very different times (a 30-second edit, then a 20-minute test suite) will make it jump. A job's estimate works the same way from its measured count, and also shows items per minute.

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
| `watch_jobs` | `true` | Run the socket hub, put `pbar` on the shell's `PATH` and show jobs. Needs `python3`. Off removes everything in the Background jobs section. |
| `stall_after` | `120` | Seconds of silence before a job is called stalled, 10 to 3600. |
| `notify` | `true` | Toast and native notification when a job finishes, fails, dies or stalls. |
| `wake_claude` | `off` | `off`, `on-failure` or `always`: start a turn for Claude when a job ends. |

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
claude plugin test .          # formatting, estimates, layout, the tools, jobs, stalls, deaths, the prompt sections, drawing, /clear
python3 -m unittest discover -s tests -v    # pbar and its hub, against a real socket
claude --plugin-dir .         # try it locally; edits hot-reload
```

Layout:

- `hooks/register.tsx` holds the hooks. `session.start` registers the tools, starts the hub and a 2-second clock that only runs while a task or job is unfinished (and checks that each job's process is still there), `tool.call` serves them, `tool.describe` and `tool.check` keep it listed and unprompted, `prompt.compose` adds the instruction, and `ui.render` draws the `AbovePrompt` band.
- `hooks/format.ts` holds the pure logic for Claude's bar: validating a report, durations, the estimate, the layout.
- `hooks/jobs.ts` holds the pure logic for jobs: reading a message, stalls, rows, what to announce.
- `bin/pbar` is the CLI and, as `pbar hub`, the socket listener the mod keeps alive with `$.process.spawn`; its output is the mod's input.
- `types/index.d.ts` is the session-state contract.

## License

MIT
