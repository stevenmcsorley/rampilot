#!/usr/bin/env bash
set -euo pipefail
# Only remove known RAMPilot files. Keep user preferences.
rm -f -- "$HOME/.local/bin/rampilot" "$HOME/.local/share/applications/io.github.rampilot.RAMPilot.desktop" "$HOME/.local/share/icons/hicolor/scalable/apps/io.github.rampilot.RAMPilot.svg"
RAMPILOT_INSTALLED="$HOME/.local/share/rampilot"
if [[ -d "$RAMPILOT_INSTALLED" ]]; then
  RAMPILOT_ARCHIVE="$(mktemp -d "$HOME/.local/share/rampilot-uninstalled.XXXXXX")"
  mv -- "$RAMPILOT_INSTALLED" "$RAMPILOT_ARCHIVE/app"
  echo "Application files moved to $RAMPILOT_ARCHIVE/app for recovery. Preferences retained in your XDG config directory."
fi
