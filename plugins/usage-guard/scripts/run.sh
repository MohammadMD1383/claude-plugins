#!/bin/sh
# Launch usage_guard.py with whatever Python 3 is available. Exit 0 silently
# without one, so a missing interpreter never surfaces as a hook error.
dir=$(dirname "$0")
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    exec "$py" "$dir/usage_guard.py" "$@"
  fi
done
exit 0
