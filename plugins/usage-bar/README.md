# usage-bar

**A one-line bar above the prompt showing how much room you have left.**

```text
  ctx ━━━─── 84k/200k  ·  5h ━━──── 31% ↻ 2h14m  ·  week ━━━━━━ 93% ↻ 3d4h
╭──────────────────────────────────────────────────────────────────────────╮
│ >                                                                        │
╰──────────────────────────────────────────────────────────────────────────╯
```

| Section | What it shows |
| --- | --- |
| `ctx` | Tokens in the context window, used/total (`84k/200k`). Before the first response it shows `–/200k`. While you're viewing a subagent's transcript, this section is labelled `agent` and shows that subagent's context instead. |
| `5h` | How much of your 5-hour limit you've used, and how long until it resets. |
| `week` | How much of your weekly limit you've used, and how long until it resets. |

The labels and track are dim. A section turns **yellow** from 70% and **red** from 90%. Both thresholds can be changed.

This is a **mod**: a plugin of function hooks that draws into Claude Code's own interface. It needs Claude Code 2.1.288 or later. It runs no scripts, makes no network calls and adds nothing to Claude's context.

## Install

```text
/plugin marketplace add MohammadMD1383/claude-plugins
/plugin install usage-bar@mohammadmd-plugins
```

## Where the numbers come from

Everything comes from Claude Code itself. The mod doesn't poll or send any requests.

- **Context** updates after every model request, so it moves with each tool call during a long turn, not only when the turn ends. Claude Code only knows the size when a response finishes, so this is as live as it gets. Each subagent's context is tracked the same way. A subagent on the main model shares its window; one on another model is shown against the standard 200k window.
- **Limits** arrive with each response, and also mid-turn whenever a window moves by a whole point. They appear only on Pro and Max plans, after the first response of the session. With an API key, Bedrock or Vertex, only `ctx` is shown.
- **Reset countdowns** are relative (`42m`, `2h14m`, `3d4h`), so they're right in any time zone. They update every 30 seconds, also while you're idle.
- **Resets while idle:** when a window's reset time passes with no new response, the bar resets it on its own. The 5-hour window drops to `0%` with no countdown, because a new window only starts with your next message. The weekly window drops to `0%` and counts down to the same time next week.
- **Other sessions:** usage from Claude on other devices or sessions shows up with this session's next response, not while it's idle.
- **After `/clear` or `/resume`:** the bar stays. After `/clear` the context shows `–/200k` until the next response. The limits keep their last reading until a response brings a new one.
- **Narrow terminals:** if the line doesn't fit, the meters go first, then the countdowns.

## Configuration

Change these in `/config`, or under `pluginConfigs` in `settings.json`. Every section can be switched off.

| Option | Default | What it does |
| --- | --- | --- |
| `show_context` | `true` | Show the context-window section. |
| `show_five_hour` | `true` | Show the 5-hour limit section. |
| `show_weekly` | `true` | Show the weekly limit section. |
| `show_reset` | `true` | Show reset countdowns. |
| `show_meters` | `true` | Show the small meter beside each value. |
| `placement` | `above-prompt` | `above-prompt` gives the bar its own line above the input. `footer` appends a compact, dim version to the hint line under the input (`? for shortcuts · ctx 84k/200k · 5h 31% ↻ 2h14m`). The footer is drawn only in the terminal and always shows the main conversation's context. |
| `warn_pct` | `70` | The percentage at which a section turns yellow. |
| `critical_pct` | `90` | The percentage at which a section turns red. |

Example, to show only the limits, without meters:

```json
{
  "pluginConfigs": {
    "usage-bar@mohammadmd-plugins": {
      "options": { "show_context": false, "show_meters": false }
    }
  }
}
```

To hide the bar for a moment, collapse the band with `ctrl+x ctrl+a`.

## Works well with

[usage-guard](../usage-guard). usage-bar shows you the numbers. usage-guard makes Claude act on them by conserving budget and writing a hand-off before the limit.

## Development

```bash
claude plugin validate .
claude plugin test .          # formatting, sections, widths, colors, footer, per-step and subagent context, idle resets, /clear
claude --plugin-dir .         # try it locally; edits hot-reload
```

Layout:

- `hooks/register.tsx` holds the hooks. `session.start` (and `classic.SessionStart` after a `/clear` or `/resume`, which fire no `session.start`) seeds the figures, `session.measure` stores them, `turn.step` refreshes the context after each model request (per subagent too), a 30-second clock redraws the countdowns, and `ui.render` draws the `AbovePrompt` band (or the `PromptHint` tail).
- `hooks/format.ts` holds the pure formatting and fitting logic.
- `types/index.d.ts` is the session-state contract.

## License

MIT
