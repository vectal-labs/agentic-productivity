#!/bin/sh
set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

PYTHON_BIN=
for candidate in "${CORRAL_PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  [ -n "$candidate" ] || continue
  if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v "$candidate" 2>/dev/null || printf '%s' "$candidate")
    break
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  printf '%s\n' 'install: Python 3.11 or newer is required' >&2
  exit 1
fi

export CORRAL_PYTHON="$PYTHON_BIN"
cd "$SOURCE_DIR"
exec "$PYTHON_BIN" -m agentic_productivity.installer "$@"
