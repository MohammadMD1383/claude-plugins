# usage-guard

**Make Claude Code aware of your claude.ai usage limits, so it wraps up before it's cut off.**

Claude Code can't see your plan's 5-hour and weekly limits. It will start a 50-task milestone at 92% and die mid-edit: half-applied refactors, nothing committed, no notes. Then you either wait hours for the reset or hand a confusing workspace to Codex or another agent.

usage-guard gives Claude that awareness, and it stays silent until it matters:

| Usage | What Claude is told | What else happens |
| --- | --- | --- |
| Normal | Nothing. **Zero tokens.** | |
| **Conserve** (≥75%, or ~45 min left at the current pace) | Work lean: targeted reads, no broad exploration or subagents, commit after each finished task. | Status line turns yellow. |
| **Wrap-up** (≥88%, or ~20 min left) | Write `HANDOFF.md` and commit it now. Then continue only in small committed steps, keeping the hand-off current. | New subagents are blocked. If Claude tries to end a turn with uncommitted work and no hand-off, it is sent back once to finish them. You see a notice. |
| **Critical** (≥95%, or ~8 min left) | Start nothing new. Make sure the hand-off is current and committed, tell the user, and stop. | |
| **Limit hit** | (Claude can't act any more.) | The plugin writes an emergency snapshot to `HANDOFF.md` without the model: branch, uncommitted files, your last request, open tasks. You also get a desktop notification. |
| **After reset** | One line: a recent `HANDOFF.md` exists, so read it before continuing previous work. | |

Every message tells Claude which window it is (5h or weekly), the usage percentage, the projected minutes left, and the reset time. That lets Claude make sensible calls: if the window resets in 10 minutes, it suggests pausing instead of handing off. For a model-specific weekly limit (Opus or Sonnet), it suggests switching models.

### It's been tested end to end with Claude Code

The test gave Claude an 8-task milestone with usage at 89%, then raised it to 96% after about 20 s. Claude did the following:

1. Told the user about the limit, wrote the plan to `HANDOFF.md` and committed it.
2. Implemented T1, T2 and T3, with one commit each and the backlog boxes ticked.
3. When the critical warning arrived, used one tool call to rewrite `HANDOFF.md` with exact next steps and gotchas, then committed `wip: checkpoint before usage limit`.
4. Stopped and reported: *"T1–T3 done; T4–T8 not started; hand-off in HANDOFF.md."*

Any agent can pick up from there.

## Install

```text
/plugin marketplace add MohammadMD1383/claude-plugins
/plugin install usage-guard@mohammadmd-plugins
```

Then, optionally but recommended:

```text
/usage-guard:setup
```

This taps Claude Code's status line feed, which carries exact limit data on every response. If you already have a status line, it is kept and wrapped, and a usage segment such as `5h 91% ~14m left · 7d 40%` is appended to it. Run `/usage-guard:setup uninstall` to restore your original status line.

Requirements: Python 3 on `PATH`. macOS and Linux have it out of the box; on Windows, install it from python.org.

## Where the numbers come from

1. **The status line feed (preferred).** Claude Code officially gives status line commands `rate_limits.five_hour` and `rate_limits.seven_day` (`used_percentage`, `resets_at`) after every API response, for Pro and Max plans. Behind a Claude apps gateway it also sends `spend_limit`. Hooks don't receive this data, so `/usage-guard:setup` records it to a small shared cache that the hooks read. This costs no tokens and no network calls.
2. **The usage endpoint (fallback).** Without the tap, a detached background process reads the same endpoint `/usage` uses, with your existing Claude Code login:
   - It polls at most every 5 minutes (2.5 minutes once usage is elevated) and backs off on 429.
   - It never refreshes or rotates your token.
   - It is skipped for Bedrock, Vertex, Foundry and custom base URLs.
   - On macOS the token is read from the Keychain, which may ask once for permission. Turn this off with `api_fallback: false` if you only want the status line.

The cache is account-wide, so several Claude sessions running in parallel all see the shared burn.

**Burn-rate projection.** A raw percentage misleads: 85% with three hours left can be fine, while 70% burning 2%/min is about to run out. usage-guard keeps a short history of readings and projects the minutes until the limit. That projection can escalate earlier than the percentage thresholds. It can also hold back a hand-off when the window will reset before you would run out.

## Why it stays cheap

You asked for a guard that doesn't eat the budget it protects:

- **No always-on context.** No MCP server, no CLAUDE.md, and every skill is `disable-model-invocation`, so not even a skill description sits in context. At normal usage the plugin adds **0 tokens**.
- **Escalation-only messages.** Each level is announced once (the full wrap-up message with the hand-off protocol is about 225 tokens), with a one-line reminder every 15 tool calls at wrap-up or above. A new prompt gets one status line, but only while usage is elevated.
- **Fast hooks.** About 40 ms each, stdlib Python, no network in the foreground. Every failure path exits 0 silently, so the plugin can never block or break a session.
- **The emergency snapshot is written by a script, not the model,** because by then there's no budget left to spend.

## Commands

| Command | What it does |
| --- | --- |
| `/usage-guard:status` | Per-window usage, burn rate, minutes to the limit, reset time, data source and age. |
| `/usage-guard:handoff [notes]` | Run the hand-off protocol now, for example before switching to Codex deliberately. |
| `/usage-guard:setup [uninstall]` | Install or remove the status line tap. |

## The hand-off file

`HANDOFF.md` sits at the repo root and is written for a reader with no context: Codex, a human, or a future Claude session. It has these sections:

- **Goal**: the overall task or milestone, and where its spec or backlog lives.
- **Done**: completed items, with commit hashes.
- **In progress**: exact files and functions touched, and what's left.
- **Next steps**: concrete and in order.
- **Decisions and gotchas**: choices made and why, traps found, things not to retry.
- **Verify**: the commands that prove the work.

To continue elsewhere, tell the other agent: *"Continue the work described in HANDOFF.md."* When a later Claude session starts, it is reminded that the file exists, and deletes it once the work is finished.

If the limit hits before Claude finishes the protocol, the auto-captured block, between `<!-- usage-guard:auto:start/end -->` markers, still records what changed and what was open.

## Configuration

The main options are prompted when you enable the plugin and can be changed under `/config`. All options can also go in `~/.claude/usage-guard.json`, or per project in `.claude/usage-guard.json`:

```json
{
  "notice_pct": 75, "wrapup_pct": 88, "critical_pct": 95,
  "notice_eta_min": 45, "wrapup_eta_min": 20, "critical_eta_min": 8,
  "wrapup_policy": "incremental",
  "handoff_file": "HANDOFF.md",
  "auto_commit": true,
  "block_subagents": true,
  "stop_guard": true,
  "auto_snapshot": true,
  "notify": true,
  "api_fallback": true,
  "api_poll_sec": 300,
  "remind_every": 15,
  "statusline_segment": "always"
}
```

The less obvious options:

- **`wrapup_policy`**: `incremental` (the default) hands off early and keeps working in small committed steps, so the remaining budget gets used. `stop` stops starting work at wrap-up.
- **`auto_commit`**: set it to `false` if Claude must never commit. The hand-off note is still written.
- **`statusline_segment`**: `always`, `elevated` (show the segment only while usage is elevated) or `never`.

Set `USAGE_GUARD_DISABLE=1` to switch the plugin off for one shell.

## Privacy, network and files

Everything the plugin runs, reads, sends and writes:

- **Runs:** `scripts/run.sh`, which starts `scripts/usage_guard.py` with your local Python 3. It also runs read-only `git` commands (`rev-parse`, `status`, `log`) in your project. It installs nothing and downloads no packages.
- **Network:** one request type only: `GET https://api.anthropic.com/api/oauth/usage`, the endpoint Claude Code's own `/usage` command reads. It is sent only by the fallback poller, in the background, at most every 2.5–5 minutes, and never when the status line tap is feeding data. No other host is ever contacted. There is no telemetry or analytics. Set `api_fallback: false` to make the plugin fully offline.
- **Credentials:** for that request only, the poller reads Claude Code's existing OAuth access token from Claude Code's own store: `~/.claude/.credentials.json`, the macOS Keychain item `Claude Code-credentials`, or `CLAUDE_CODE_OAUTH_TOKEN`. The token is sent only to `api.anthropic.com`. It is never logged, stored, refreshed or sent anywhere else.
- **Reads:** hook input from Claude Code, the tail of the session transcript and the session's task list. These are read only when the limit is hit, to write the emergency snapshot.
- **Writes, in your project:** only the hand-off file (`HANDOFF.md` by default), and only when the limit is hit or Claude follows the hand-off protocol. Commits happen only when Claude runs the protocol with `auto_commit` on. The plugin never pushes.
- **Writes, elsewhere:** usage readings and per-session notice state go in the plugin's data directory (`~/.claude/plugins/data/usage-guard-*`). `/usage-guard:setup` edits the `statusLine` entry of `~/.claude/settings.json`, after saving a backup to `settings.json.usage-guard.bak`, and `/usage-guard:setup uninstall` restores it.

## Limitations

- The fallback endpoint isn't a documented API and may change. The status line feed is official.
- Readings arrive per response, or every few minutes from the fallback. A single huge response can still overshoot, which is why the burn-rate projection warns early and the emergency snapshot exists.
- Usage credits: if extra usage is enabled, hitting the limit bills credits rather than stopping you. Claude is told so, and the thresholds stay the same.

## Development

```bash
python3 -m unittest discover -s tests -v   # 24 tests: parsing, levels, burn rate, hooks, snapshot, setup, fetch
claude plugin validate . --strict
claude --plugin-dir .                       # try it locally
```

Layout: this directory is the plugin; the marketplace manifest lives at the repository root. `scripts/usage_guard.py` is the single stdlib-only engine behind the hooks, the status line, the fetcher and the commands; `hooks/hooks.json` wires six events to it; `skills/` contains the three user-invoked commands.

## License

MIT
