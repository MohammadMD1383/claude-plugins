# agy-link

**Use Claude models hosted on Google Antigravity from Claude Code, and watch the Antigravity quota above the prompt.**

```text
  AGY 5h ━━━─── 38% ↻ 2h14m  ·  week ━───── 12% ↻ 4d4h
```

A local gateway ([OmniRoute](https://github.com/diegosouzapw/OmniRoute) or [9router](https://github.com/decolua/9router)) does the actual work of turning Claude Code's Anthropic-format requests into Antigravity calls. This plugin is the Claude Code side of that setup:

| It does | How |
| --- | --- |
| Shows your Antigravity 5-hour and weekly quota, with reset countdowns | Reads the gateway's usage endpoint and draws a band above the prompt. Stacks under [usage-bar](../usage-bar) when both are installed. |
| `/agy` | Prints every quota window as text. |
| Optionally points a session at the gateway | `activate` sets `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` at session start. Off by default. |

This is a **mod**: a plugin of function hooks that runs inside Claude Code. It needs Claude Code with mods (2.1.287+); tested on 2.1.289. It starts no processes and sends requests only to the gateway you configure.

## Why a gateway, and why this isn't the gateway

Claude Code can't show Antigravity's limits on its own. Its built-in limit display is fed by `anthropic-ratelimit-unified-*` response headers, and in testing those headers produced no limits while Claude Code was authenticated with a gateway token or API key (tested on 2.1.289 against a mock gateway). So the quota has to be read from the gateway's own API, which is what this plugin does.

A mod can't replace the gateway either. A `turn.step` hook can answer a model request by itself (verified: it completes a turn with no API request), but a mod's `$.http.fetch` returns the whole body at once rather than streaming, and the hook never sees the raw request. A gateway is the better home for translation. See [docs/agy-translator-handoff.md](../../docs/agy-translator-handoff.md) for the full analysis.

## Set up

### 1. Run a gateway and connect your Antigravity account

Follow the gateway's own instructions. OmniRoute and 9router both listen on `http://localhost:20128` by default.

> **Read this first.** Google's terms treat reaching Antigravity through third-party tools as a violation. In February 2026 Google suspended paying Antigravity subscribers who used OpenClaw's OAuth integration, later reinstated many with a final warning, and users of `antigravity-claude-proxy` reported bans in March. Routing Claude Code through a gateway uses the same kind of access. Use an account you can afford to lose, not your main Google account.

### 2. Point Claude Code at the gateway

The most predictable way is the `env` block of a **project or local** settings file, so only that project talks to the gateway and every other session keeps using Anthropic:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:20128",
    "ANTHROPIC_AUTH_TOKEN": "<your gateway API key>",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1"
  }
}
```

With discovery on, the gateway's Claude models appear in `/model`. Otherwise name one explicitly (`/model <id>` or `ANTHROPIC_MODEL`), using an id from your gateway's `GET /v1/models`.

Always set `ANTHROPIC_AUTH_TOKEN`. With only `ANTHROPIC_BASE_URL`, a saved claude.ai login can still be the active credential and would be sent to the gateway.

### 3. Install this plugin

```text
/plugin marketplace add MohammadMD1383/claude-plugins
/plugin install agy-link@mohammadmd-plugins
```

Then set the gateway key in `/plugin` → agy-link (it is stored in your system's secure storage). If your gateway isn't on `localhost:20128`, set the gateway URL too.

## `activate`

With `activate` on, the mod sets `ANTHROPIC_BASE_URL` (the gateway URL) and `ANTHROPIC_AUTH_TOKEN` (the gateway key) when a session starts, so you don't need step 2. I tested that this redirects the session's requests, and that they carry the gateway key. Things to know:

- It refuses to do anything without a gateway key, so a claude.ai login is never sent to the gateway by this plugin.
- Claude Code decides how it authenticates at startup. In testing, mod-activated requests still carried the `oauth-2025-04-20` beta header, and the `/v1/models` discovery didn't run. If that matters for your gateway, use the `env` block instead.
- It applies to every session where the plugin is enabled, so enable the plugin only in the project you want, and expect every session there to fail while the gateway is down.
- Check your gateway's request log once to confirm the credential it receives is your gateway key.

## Where the numbers come from

The mod calls `GET <gateway>/api/usage/om-usage?format=json` with `Authorization: Bearer <gateway key>` every `poll_seconds` (default 60). It understands three response shapes:

| Source | Shape |
| --- | --- |
| OmniRoute | `{ providers: [{ provider: "antigravity", plan, quotas }] }` from `/api/usage/om-usage?format=json`. The key needs the gateway's *usage command* permission. |
| 9router | `{ plan, quotas }` from `/api/usage/<connectionId>` (the connection id is in its dashboard). Set `usage_url` to that URL. 9router guards `/api/usage` with its dashboard login unless login is turned off, and this plugin sends only the Bearer key, so it works there only with login off (read from its source, not tried against a running 9router). |
| Your own translator | `{ "windows": [{ "label": "5h", "percentUsed": 38, "resetsAt": "2026-10-04T14:14:00Z" }] }` from any URL, set as `usage_url`. |

In the first two, `quotas` is a map of rows `{ used, total, remainingPercentage, resetAt }`. Both gateways build them from Google's `fetchAvailableModels` quota info. The mod reads the rows both gateways name `claude_gpt_session` and `claude_gpt_weekly` (Claude and GPT-OSS share them), or `gemini_session` and `gemini_weekly`. If a gateway has no such rows, it shows the most used per-model row of the family instead.

Behaviour worth knowing:

- The band is hidden unless this session's `ANTHROPIC_BASE_URL` is the gateway, so normal Anthropic sessions don't show it. Set `show_when` to `always` to change that.
- If a read fails, the last reading stays, with the reason beside it (`gateway unreachable`, `gateway key rejected`, `key may not read usage`, `HTTP 500`). Before the first reading, only the reason shows.
- A window whose reset time has passed reads `0%` rather than stale.
- A narrow terminal drops the meters, then the countdowns.

## Configuration

Change these in `/config` or `/plugin`, or under `pluginConfigs` in `settings.json`.

| Option | Default | What it does |
| --- | --- | --- |
| `gateway_url` | `http://localhost:20128` | Gateway base URL, no path. |
| `gateway_key` | none | The gateway's API key. Sensitive. Never use a claude.ai or Anthropic credential here. |
| `activate` | `false` | Set `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` at session start. Needs `gateway_key`. |
| `usage_url` | empty | Full URL of the quota endpoint. Empty means OmniRoute's. |
| `family` | `claude` | `claude`, `gemini` or `all`. |
| `show_when` | `gateway` | `gateway` or `always`. |
| `poll_seconds` | `60` | How often to read the usage endpoint (15 to 3600). |
| `show_reset` | `true` | Show reset countdowns. |
| `warn_pct` | `70` | A window turns yellow from this percentage used. |
| `critical_pct` | `90` | A window turns red from this percentage used. |

## What was and wasn't tested

Tested on Claude Code 2.1.289. With `claude plugin test` (24 tests): response parsing, rendering, stacking under another band, error states, `/agy`, activation and the key guard. With a real `claude -p` session against a mock gateway: activation redirecting the session's requests, and an authenticated usage poll. The band itself was not looked at in a live terminal.

Not tested: a live OmniRoute or 9router with a real Antigravity account. The response shapes come from reading both gateways' source (OmniRoute at its 2 October 2026 commit, 9router at 1 October), and can change. If a gateway changes its format, `usage_url` plus the normalized shape is the escape hatch.

## Data handling

The plugin reads one URL, the usage endpoint of the gateway you configure, with the gateway key. It sends nothing anywhere else, stores nothing outside Claude Code's own plugin settings, and adds nothing to Claude's context.

## License

MIT, see [LICENSE](LICENSE).
