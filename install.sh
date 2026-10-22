#!/usr/bin/env bash
# User-only installation. Dependencies are provided by Ubuntu, not pip.
set -euo pipefail
RAMPILOT_SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RAMPILOT_DEST="$HOME/.local/share/rampilot"
/usr/bin/python3 -c 'import gi, psutil, cairo; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Adw; assert Adw.get_minor_version() >= 5 or Adw.get_major_version() > 1, "Libadwaita 1.5+ required (Ubuntu 24.04+)"' || {
  echo 'Install dependencies: sudo apt install python3-gi python3-gi-cairo python3-psutil gir1.2-gtk-4.0 gir1.2-adw-1'
  exit 1
}
mkdir -p "$RAMPILOT_DEST" "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
cp -R "$RAMPILOT_SOURCE/src" "$RAMPILOT_SOURCE/bin" "$RAMPILOT_DEST/"
install -m 755 "$RAMPILOT_SOURCE/uninstall.sh" "$RAMPILOT_DEST/uninstall.sh"
ln -sfn "$RAMPILOT_DEST/bin/rampilot" "$HOME/.local/bin/rampilot"
sed "s|^Exec=.*|Exec=\"$RAMPILOT_DEST/bin/rampilot\"|" "$RAMPILOT_SOURCE/packaging/io.github.rampilot.RAMPilot.desktop" > "$HOME/.local/share/applications/io.github.rampilot.RAMPilot.desktop"
install -m 644 "$RAMPILOT_SOURCE/src/rampilot/data/icons/hicolor/scalable/apps/io.github.rampilot.RAMPilot.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/"
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
echo "Installed. Launch from Applications → RAMPilot, or $HOME/.local/bin/rampilot"
