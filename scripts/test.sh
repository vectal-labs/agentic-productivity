#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

for TEST_PYTHON in "${CORRAL_PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  [ -n "$TEST_PYTHON" ] || continue
  if "$TEST_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    exec "$TEST_PYTHON" -m unittest discover -s "$REPO_ROOT/tests" -p 'test_*.py' -v
  fi
done

printf '%s\n' 'tests require Python 3.11 or newer' >&2
exit 1
