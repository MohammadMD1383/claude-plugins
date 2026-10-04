# agy-probe

The throwaway harness behind the findings in [../agy-translator-handoff.md](../agy-translator-handoff.md). Not a plugin to install.

- `mock-gateway.mjs`: an Anthropic-format gateway on `127.0.0.1:8799`. Streams a canned reply, serves `/v1/models` and an OmniRoute-shaped `/api/usage/om-usage`, and sends `anthropic-ratelimit-unified-*` headers. Its log holds method, URL, beta header and whether a credential matched the dummy one. It never records a credential.
- `probe/`: a mod that writes what a mod can see (`session.measure` rate limits, the model on each `turn.step`, `$.http.fetch` to loopback, `$.env.set('ANTHROPIC_BASE_URL')`, answering `turn.step` itself) into `/tmp/probe/*.json`. Driven by env vars `PROBE_SET_BASE_URL`, `PROBE_FETCH`, `PROBE_PROVIDER`.
- `run.sh`: one `claude -p` turn in an **empty environment** (`env -i`).

Run the harness only with `env -i`. With `ANTHROPIC_BASE_URL` pointed at a local server, Claude Code sends whatever credential it can find to that server, and in a managed or logged-in environment that includes a real one.
