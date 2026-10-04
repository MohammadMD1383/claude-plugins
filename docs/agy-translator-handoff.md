# Antigravity models in Claude Code: findings and a hand-off for a local translator

Written 4 October 2026 against Claude Code 2.1.289. Claude Code's mod API is early access and moves between releases; re-check anything below that matters before building on it.

## Verdict

| Question | Answer |
| --- | --- |
| Can Claude Code use Antigravity-hosted Claude models? | **Yes, with a gateway, and no mod is needed for it.** Point `ANTHROPIC_BASE_URL` at a local translator that speaks the Anthropic Messages API. OmniRoute and 9router already do this. |
| Can a mod do the translating itself? | **Technically yes, practically no.** A `turn.step` hook can answer a model request on its own (verified), but `$.http.fetch` returns the whole body at once rather than streaming, and the hook never sees the raw request. |
| Can a mod switch Claude Code onto a gateway? | **Yes.** `$.env.set('ANTHROPIC_BASE_URL', …)` in a `session.start` hook redirected the very next request (verified). |
| Can Claude Code show Antigravity's limits natively? | **No, not through gateway auth.** Its limit display reads `anthropic-ratelimit-unified-*` response headers, and with a gateway token or API key, mod-visible `rateLimits` stayed empty even with those headers sent (verified). |
| Can a mod show the limits? | **Yes.** Poll the gateway's usage API and draw a band. Built: [`plugins/agy-link`](../plugins/agy-link). |
| Is it safe? | **Not risk-free, and the risk is mostly on the Google side.** See [Risks](#risks). |

So the best integration is: a gateway for transport, a mod for activation and quota display. The mod is built. What is left is a gateway you control, if OmniRoute and 9router don't suit you. The rest of this document is the brief for that.

## What was verified, and how

Everything marked "verified" was run, not read. The harness is in [`docs/agy-probe`](agy-probe): a mock Anthropic-format gateway plus a probe mod, driven by real `claude -p` sessions in an empty environment.

| # | Experiment | Result |
| --- | --- | --- |
| 1 | `ANTHROPIC_BASE_URL` at the mock, `ANTHROPIC_AUTH_TOKEN` set, model `agy/claude-sonnet-5-5` | Works. The model id reaches the gateway verbatim. Claude Code first sent `HEAD /api/hello`, then `GET /v1/models?limit=1000` (only with `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`), then `POST /v1/messages?beta=true`. |
| 2 | Same, with the mock sending `anthropic-ratelimit-unified-5h-utilization`, `-5h-reset`, `-7d-*`, `-status`, `-representative-claim` | `session.measure` `rateLimits` was `[]`. Same with `ANTHROPIC_API_KEY` in place of the token. The header names are the ones in the binary, so this is the auth mode, not a typo. **Not tried with a real claude.ai login**, where the docs say the headers do drive the display. |
| 3 | Mod calls `$.env.set('ANTHROPIC_BASE_URL', mock)` in `session.start`, nothing set at launch | The request reached the mock. Claude Code builds its client per request. |
| 4 | Mod calls `$.http.fetch('http://127.0.0.1:8799/…')` | Works. Loopback is reachable. |
| 5 | Mod's `turn.step` hook yields a `text` and a `stop` chunk and never calls `next` | The turn completed with the mod's text and **no API request and no credentials**. |
| 6 | `agy-link` with `activate` on, gateway key from `pluginConfigs`, nothing else set | Session redirected to the mock with the gateway key; usage polled with `Bearer <key>`. (`/agy` is covered by the unit tests only, not that run.) |

Two observations from experiment 1 that matter for a translator, from the request Claude Code sent:

- The main request carried 38 tools, 3 system blocks, `thinking: {"type":"adaptive","display":"omitted"}`, `context_management`, `output_config: {"effort":"medium"}`, and about a dozen `anthropic-beta` values (`interleaved-thinking`, `context-management`, `prompt-caching-scope`, `effort`, `extended-cache-ttl`, `advisor-tool`, `mid-conversation-system`, …). It also sent two further tool-less requests on the same model (background work of some kind; I did not decode them).
- With a mod-set (rather than launch-time) `ANTHROPIC_AUTH_TOKEN`, the `anthropic-beta` header still included `oauth-2025-04-20`, and `/v1/models` discovery did not run. Auth mode is decided at startup.

## Why not translate inside a mod

The facts, so the next agent doesn't have to rediscover them:

- `turn.step` is a streaming hook: `yield` text, thinking, `tool` and `input` chunks and a `stop` chunk, and "yielding without `next` answers alone". That part works and could carry tool calls.
- A hook can't see the request. It gets `model`, `effort`, `messageCount`; the messages come from `$.session.messages()` (newest 4,096), the tools from `$.tool.list()`, the system prompt from `$.prompt.compose()`. It would have to rebuild the Anthropic request, including `cache_control`, the attribution block and thinking signatures, from those.
- `$.http.fetch` resolves `{ status, ok, headers, text }` once the body is read. No incremental body, so no live streaming; each step would appear all at once. `$.model.complete` is the same: whole, not streamed.
- Hook time is 10 s of the hook's own code, with awaits on `$` not counted, so long upstream calls are survivable. Limits like 4 MiB per file are irrelevant here.
- A mod can start a long-lived child with `$.process.spawn` (documented, CLI only, **not tried**), so a mod could launch a translator and also set the base URL. That is the one place a mod and a translator fit together.

If streaming matters to you (it does for a coding agent), the translator is a separate process.

## The brief for a local translator

A small local service that accepts Claude Code's requests and serves them from an Antigravity account. It does not belong in this repository.

### What Claude Code needs from it

From the [gateway compatibility guide](https://code.claude.com/docs/en/llm-gateway-protocol). Claude Code treats an `ANTHROPIC_BASE_URL` endpoint as the Claude API:

- `POST /v1/messages?beta=true` with streaming. Relay every event, in order, ending with `message_delta` (with `stop_reason`) and `message_stop`. A stream that ends early is treated as a dropped connection and retried; an event for a block that never started stops the stream. Emit `ping` events during silent gaps (Claude Code aborts a stream after 5 minutes without bytes, and Google's stream has no pings of its own).
- Optional: `POST /v1/messages/count_tokens` (without it `/context` is an estimate), `GET /v1/models` returning `{ data: [{ id, display_name, description }] }` in under 3 s with no redirects (ids must contain `claude` or `anthropic`), `HEAD /api/hello` (may 404).
- Request headers to read: `Authorization` and/or `x-api-key` (the gateway key), `anthropic-version`, `anthropic-beta`, `x-claude-code-session-id`, `x-claude-code-agent-id`. With `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1`: `x-claude-code-request-class`, `x-claude-code-prompt-id` and friends.
- Response headers worth setting: `content-type: text/event-stream`, integer `retry-after` (above 60 stops retries), `x-should-retry`, and the upstream's error bodies unwrapped. Claude Code's recovery matches on error wording.
- Errors Claude Code recovers from by itself when the body wording matches the Messages API: a rejected `thinking` field, a rejected `output_config.effort`, a rejected advisor tool, a `400` saying a thinking block is "bound to a different conversation" (it strips thinking and retries).
- Switches that make a non-Anthropic upstream easier: `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` (no `context_management`, beta tool fields, structured outputs, task budget), `CLAUDE_CODE_ATTRIBUTION_HEADER=0` (drops the attribution block from the system prompt), `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` (Opus/Sonnet 4.6 only). Fine-grained tool streaming is off behind a custom base URL.
- Prompt caching: forward `cache_control` where it appears. If the upstream can't cache, the cost is Google quota, not an error.
- Model naming: Claude Code recognises Anthropic ids; unknown gateway aliases get adaptive thinking, effort and context management fields as if they were current Claude models. Pin with `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL`, `modelOverrides` for capabilities, and `[1m]` or the documented window setting for the context size. Background tasks use the main model unless `ANTHROPIC_DEFAULT_HAIKU_MODEL` pins one.
- Auth: set `ANTHROPIC_AUTH_TOKEN`, not only `ANTHROPIC_BASE_URL` (see Risks).

Anthropic's position, from the same docs: it doesn't endorse or audit third-party gateways and doesn't support routing Claude Code to *non-Claude* models through one. Opus 5.5 and Sonnet 5.5 on Antigravity are Claude models, so the supported-use line isn't crossed, but the gateway still has to keep up with each Claude Code release.

### What Antigravity looks like upstream

Read from OmniRoute and 9router's source (not run against an account). Both translate Anthropic or OpenAI requests into Google's internal Code Assist API:

- Hosts: `https://daily-cloudcode-pa.googleapis.com` and `https://cloudcode-pa.googleapis.com`, path prefix `/v1internal`. Calls seen: `:loadCodeAssist`, `:onboardUser` (to get a project), `:generateContent`, `:fetchAvailableModels`, `:retrieveUserQuota` (Gemini CLI).
- Auth: Google OAuth with the IDE's own client. OmniRoute ships an `omniroute login antigravity` helper that runs the OAuth on your machine and prints a credential blob.
- Quota: `fetchAvailableModels` returns per-model `quotaInfo { remainingFraction, resetTime }`. Free-tier accounts only have a weekly quota and the per-model figures are misleading there (9router skips them). Both gateways also read a separate weekly quota and expose rows named `claude_gpt_session`, `claude_gpt_weekly`, `gemini_session`, `gemini_weekly`. Claude and GPT-OSS share one family.
- Requests are made to look like the IDE's: both gateways set an Antigravity client profile (user agent, request and session ids, IDE version headers). That is the part most exposed to Google's terms.
- Translators to reuse or crib from: OmniRoute `open-sse/translator/request/claude-to-gemini.ts` and `antigravity-to-openai.ts`; 9router `open-sse/translator/request/claude-to-openai.js`, `openai-to-gemini.js`, `antigravity-to-openai.js`, `open-sse/services/usage/google.js`.

Availability, as reported on 3 October: Opus 5.5 and Sonnet 5.5 are on Google AI Pro and Ultra only; accounts on a free trial are excluded; Opus 4.6, Sonnet 4.6 and GPT-OSS-120B are removed on 2 November.

### Quota back to Claude Code

Expose one small, stable endpoint and `agy-link` already reads it, with no code change:

```text
GET /usage            (any URL; set it as agy-link's usage_url; Bearer gateway key)
200 { "plan": "Google AI Pro",
      "windows": [ { "id": "session", "label": "5h",   "percentUsed": 38, "resetsAt": "2026-10-04T14:14:00Z" },
                   { "id": "weekly",  "label": "week", "percentUsed": 12, "resetsAt": "2026-10-08T16:00:00Z" } ] }
```

Also send the `anthropic-ratelimit-unified-5h-*` and `-7d-*` headers on responses. In my test they did nothing under gateway auth, but they cost nothing and are what the display reads for a claude.ai login. If a future Claude Code starts reading them under gateway auth, `usage-bar` would show the numbers with no further work.

### Suggested shape

- One process, one port, loopback only, a gateway key required on every route. No dashboard.
- Streaming first: pipe Google's stream through a translator that emits well-formed Anthropic events, with the ordering guarantees above.
- Tool calls and thinking are the hard part. Test a multi-tool turn, a long thinking pause, a tool call split across chunks, and a turn that gets a `429` mid-stream.
- Map upstream rate limiting to a real `429` with an integer `retry-after`, so Claude Code backs off instead of retrying hot.
- Keep the credential handling boring: tokens in the OS keychain or a `0600` file, refreshed by the service, never logged.
- Test it with the harness in `docs/agy-probe`, then with a real account.

## Risks

- **Terms of service.** Google treats reaching Antigravity through third-party tools as a violation. Reports in February 2026 had Google suspending paying subscribers who used OpenClaw's Antigravity OAuth, with a later partial reinstatement and a final warning; a forum post reports a ban after using `antigravity-claude-proxy` in March. A translator that makes requests look like the IDE's is the same kind of access. Use an account you can afford to lose.
- **Credentials sent to the wrong place.** With `ANTHROPIC_BASE_URL` set, Claude Code sends whatever credential it has to that URL. Docs: with only the base URL set, a saved claude.ai login stays the active credential and goes to the gateway. I hit this: in a managed environment my first test run delivered the environment's own session credential to my loopback mock (it stayed on the machine, but it was in my logs). `agy-link`'s `activate` refuses to run without a gateway key for this reason, but I could not test it with a real claude.ai login, so check your gateway's log once.
- **Everything you do goes through the gateway to Google.** Prompts, code and tool output. Treat the gateway as part of your trust boundary.
- **Silent breakage on Claude Code updates.** New betas and body fields arrive with releases; a gateway that rejects them fails with `400`s. The docs say to test against each release.
- **Response shapes of OmniRoute and 9router can change.** `agy-link` falls back to the normalized shape for that reason.

## Not verified

- Any live Antigravity account, OmniRoute or 9router run. All gateway facts come from reading source at OmniRoute's 2 October commit and 9router's 1 October commit.
- Limit headers with a real claude.ai login (experiment 2 covers gateway token and API key only).
- That a sensitive `userConfig` value reaches a mod's `options` from secure storage. In experiment 6 it came from `pluginConfigs` in `settings.json`.
- `$.process.spawn` launching a gateway from a mod.
- 9router's `/api/usage/<id>` with `requireLogin` on (it needs a dashboard session; the plugin sends a Bearer key only).
- Switching a live session onto and off the gateway mid-conversation. `$.env.set` works at `session.start`; what the API does with a conversation whose earlier thinking blocks came from another provider is the "bound to a different conversation" case, which Claude Code handles by stripping thinking.

## Sources

- Antigravity adding Claude Opus 5.5 and Sonnet 5.5: [Startup Fortune](https://startupfortune.com/google-antigravity-adds-anthropics-claude-opus-55-and-sonnet-55/), [explainx](https://www.explainx.ai/blog/antigravity-claude-opus-5-5-sonnet-5-5-pro-ultra-gpt-oss-removal-2026), [SmartScope](https://smartscope.blog/en/blog/antigravity-claude-55-pro-trial-model-retirement-2026/).
- Antigravity bans: [PiunikaWeb](https://piunikaweb.com/2026/02/23/google-antigravity-openclaw-ban/), [SecurityOnline](https://securityonline.info/the-antigravity-reinstatement-google-relents-on-ban-wave-but-issues-final-warning-on-openclaw-proxies/), [Google AI forum](https://discuss.ai.google.dev/t/warning-gemini-3-flash-told-me-to-use-antigravity-claude-proxy-yesterday-3-20-and-now-my-account-is-banned/134944), [gemini-cli discussion 20632](https://github.com/google-gemini/gemini-cli/discussions/20632).
- Claude Code: [gateway compatibility guide](https://code.claude.com/docs/en/llm-gateway-protocol), [LLM gateways](https://code.claude.com/docs/en/llm-gateway), [model configuration](https://code.claude.com/docs/en/model-config), [mods reference](https://code.claude.com/docs/en/plugins/mods/reference), [plugin manifest `userConfig`](https://code.claude.com/docs/en/plugins-reference#user-configuration).
- Gateways: [OmniRoute](https://github.com/diegosouzapw/OmniRoute), [9router](https://github.com/decolua/9router).
