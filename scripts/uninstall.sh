#!/bin/sh
set -eu

APP_ROOT=${CORRAL_PRODUCTIVITY_APP_DIR:-"$HOME/Library/Application Support/Corral/Agentic Productivity/app"}
STATE_ROOT=${CORRAL_PRODUCTIVITY_STATE_DIR:-"$HOME/Library/Application Support/Corral/Agentic Productivity"}
LAUNCH_AGENTS_DIR=${CORRAL_PRODUCTIVITY_LAUNCH_AGENTS_DIR:-"$HOME/Library/LaunchAgents"}
PLIST="$LAUNCH_AGENTS_DIR/com.corral.agentic-productivity.plist"
LABEL=com.corral.agentic-productivity

if command -v launchctl >/dev/null 2>&1; then
  launchctl bootout "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null 2>&1 || true
fi

if [ -f "$PLIST" ]; then
  rm -- "$PLIST"
fi
if [ -d "$APP_ROOT" ]; then
  rm -rf -- "$APP_ROOT"
fi

printf '%s\n' 'Uninstalled the Agentic Productivity app and LaunchAgent.'
printf 'Preserved metrics: %s\n' "$STATE_ROOT/metrics.sqlite3"
printf '%s\n' 'The Discord webhook remains in macOS Keychain.'
