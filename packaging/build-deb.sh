#!/usr/bin/env bash
set -euo pipefail
RAMPILOT_SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RAMPILOT_STAGE="$(mktemp -d)"
mkdir -p "$RAMPILOT_STAGE/DEBIAN" "$RAMPILOT_STAGE/opt/rampilot" "$RAMPILOT_STAGE/usr/bin" "$RAMPILOT_STAGE/usr/share/applications" "$RAMPILOT_STAGE/usr/share/icons/hicolor/scalable/apps" "$RAMPILOT_SOURCE/dist"
cp -R "$RAMPILOT_SOURCE/src" "$RAMPILOT_SOURCE/bin" "$RAMPILOT_STAGE/opt/rampilot/"
find "$RAMPILOT_STAGE/opt/rampilot" -type d -name __pycache__ -exec rm -r -- {} +
ln -s /opt/rampilot/bin/rampilot "$RAMPILOT_STAGE/usr/bin/rampilot"
cp "$RAMPILOT_SOURCE/packaging/io.github.rampilot.RAMPilot.desktop" "$RAMPILOT_STAGE/usr/share/applications/"
cp "$RAMPILOT_SOURCE/src/rampilot/data/icons/hicolor/scalable/apps/io.github.rampilot.RAMPilot.svg" "$RAMPILOT_STAGE/usr/share/icons/hicolor/scalable/apps/"
install -m 644 "$RAMPILOT_SOURCE/packaging/control" "$RAMPILOT_STAGE/DEBIAN/control"
dpkg-deb --root-owner-group --build "$RAMPILOT_STAGE" "$RAMPILOT_SOURCE/dist/rampilot_1.0.0_all.deb"
echo "Package built; temporary staging directory: $RAMPILOT_STAGE"
