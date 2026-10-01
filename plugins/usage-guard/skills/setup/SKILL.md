---
description: Install (or with "uninstall", remove) the usage-guard status line tap for live limit data
argument-hint: "[uninstall]"
disable-model-invocation: true
allowed-tools: Bash(sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh *)
---
!`sh ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh setup $ARGUMENTS --data=${CLAUDE_PLUGIN_DATA}`

Relay the output above to the user in one or two lines. Nothing else to do: Claude Code reloads the status line automatically.
