#!/bin/bash
# Runs one headless `claude -p` turn against a gateway on 127.0.0.1:8799 with the probe mod loaded,
# in an EMPTY environment (env -i) so no ambient credential can reach the gateway.
# usage: ./run.sh <name> <model> [ENV=VAL ...]
#   terminal 1:  node mock-gateway.mjs            (writes ./mock-gateway.log, never logs credentials)
#   terminal 2:  mkdir -p /tmp/probe; ./run.sh A agy/claude-sonnet-5-5 ANTHROPIC_BASE_URL=http://127.0.0.1:8799 ANTHROPIC_AUTH_TOKEN=sk-gw-dummy
here="$(cd "$(dirname "$0")" && pwd)"
name=$1; model=$2; shift 2
rm -rf "$here/.run/$name"; mkdir -p "$here/.run/$name/cfg" "$here/.run/$name/home" /tmp/probe
cd /tmp
env -i PATH="$PATH" HOME="$here/.run/$name/home" CLAUDE_CONFIG_DIR="$here/.run/$name/cfg" \
  NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost "$@" \
  timeout 90 claude -p "say hi" --plugin-dir "$here/probe" --model "$model" --output-format json \
  < /dev/null > "$here/.run/$name/out.json" 2> "$here/.run/$name/err.txt"
echo "== $name exit=$?"
python3 -c "import json,sys;d=json.load(open('$here/.run/$name/out.json'));print('result:',str(d.get('result'))[:200],'| is_error:',d.get('is_error'))"
for f in /tmp/probe/*.json; do echo "  $(basename "$f"): $(cut -c1-400 "$f")"; done
