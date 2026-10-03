---
description: Show claude.ai usage-limit status as tracked by usage-guard
disable-model-invocation: true
allowed-tools: Bash(sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh *)
---
!`sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh status --data=${CLAUDE_PLUGIN_DATA}`

Show the report above to the user as-is in a code block. Add one sentence of advice only if a window is at wrap-up or critical.
