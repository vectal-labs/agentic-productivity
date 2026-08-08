#!/bin/sh
set -eu

INSTALL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE_DIR=$(CDPATH= cd -- "$INSTALL_DIR/.." && pwd)
APP_ROOT=${CORRAL_PRODUCTIVITY_APP_DIR:-"$HOME/Library/Application Support/Corral/Agentic Productivity/app"}
STATE_ROOT=${CORRAL_PRODUCTIVITY_STATE_DIR:-"$HOME/Library/Application Support/Corral/Agentic Productivity"}
LAUNCH_AGENTS_DIR=${CORRAL_PRODUCTIVITY_LAUNCH_AGENTS_DIR:-"$HOME/Library/LaunchAgents"}
LOG_DIR=${CORRAL_PRODUCTIVITY_LOG_DIR:-"$HOME/Library/Logs/Corral"}
PLIST="$LAUNCH_AGENTS_DIR/com.corral.agentic-productivity.plist"
LABEL=com.corral.agentic-productivity
DRY_RUN=0
LOAD=1

for argument in "$@"; do
  case "$argument" in
    --dry-run) DRY_RUN=1 ;;
    --no-load) LOAD=0 ;;
    *) printf 'install: unknown argument: %s\n' "$argument" >&2; exit 2 ;;
  esac
done

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

if [ "$DRY_RUN" -eq 1 ]; then
  printf 'Would install app: %s\n' "$APP_ROOT"
  printf 'Would preserve state: %s\n' "$STATE_ROOT/metrics.sqlite3"
  printf 'Would install LaunchAgent: %s\n' "$PLIST"
  printf '%s\n' 'Would observe Cursor every 5 minutes and send a 90-day report at 08:00 with wake catch-up'
  exit 0
fi

mkdir -p "$APP_ROOT/agentic_productivity" "$STATE_ROOT" "$LAUNCH_AGENTS_DIR" "$LOG_DIR"
cp "$SOURCE_DIR"/agentic_productivity/*.py "$APP_ROOT/agentic_productivity/"
chmod 700 "$APP_ROOT" "$APP_ROOT/agentic_productivity"
chmod 700 "$STATE_ROOT" "$LOG_DIR"
chmod 600 "$APP_ROOT"/agentic_productivity/*.py

escape_sed() {
  printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

PYTHON_ESCAPED=$(escape_sed "$PYTHON_BIN")
APP_ESCAPED=$(escape_sed "$APP_ROOT")
OUT_ESCAPED=$(escape_sed "$LOG_DIR/agentic-productivity.out.log")
ERR_ESCAPED=$(escape_sed "$LOG_DIR/agentic-productivity.err.log")
PLIST_TEMP=$(mktemp "${TMPDIR:-/tmp}/corral-productivity-plist.XXXXXX")
trap 'rm -f -- "$PLIST_TEMP"' EXIT HUP INT TERM
sed \
  -e "s|__PYTHON__|$PYTHON_ESCAPED|g" \
  -e "s|__APP_DIR__|$APP_ESCAPED|g" \
  -e "s|__OUT_LOG__|$OUT_ESCAPED|g" \
  -e "s|__ERR_LOG__|$ERR_ESCAPED|g" \
  "$SOURCE_DIR/launchd/com.corral.agentic-productivity.plist.in" >"$PLIST_TEMP"
if command -v plutil >/dev/null 2>&1; then
  plutil -lint "$PLIST_TEMP" >/dev/null
fi
install -m 600 "$PLIST_TEMP" "$PLIST"

if [ "$LOAD" -eq 1 ] && command -v launchctl >/dev/null 2>&1; then
  launchctl bootout "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(/usr/bin/id -u)" "$PLIST"
  launchctl kickstart -k "gui/$(/usr/bin/id -u)/$LABEL"
fi

printf 'Installed: %s\n' "$APP_ROOT"
printf 'LaunchAgent: %s\n' "$PLIST"
printf 'State: %s\n' "$STATE_ROOT/metrics.sqlite3"
printf '%s\n' 'Schedule: Cursor observation every 5 minutes; 90-day report at 08:00 with wake catch-up'
