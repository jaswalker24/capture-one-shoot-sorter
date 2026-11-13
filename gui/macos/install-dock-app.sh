#!/bin/bash
# Installs ~/Applications/Trips Local AI for Lightroom.app and adds it to the Dock.
# Does not touch the Capture One edition (Trips Local AI.app) or any other Dock icons.
set -euo pipefail
APP="$HOME/Applications/Trips Local AI for Lightroom.app"
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/Applications"
# Replace only this edition's app; never delete the Capture One edition.
rm -rf "$APP"
if [ -d "$ROOT/Trips Local AI for Lightroom.app" ]; then
  cp -R "$ROOT/Trips Local AI for Lightroom.app" "$APP"
else
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Trips Local AI for Lightroom</string>
  <key>CFBundleDisplayName</key><string>Trips Local AI for Lightroom</string>
  <key>CFBundleIdentifier</key><string>com.trips.localai-lr.panel</string>
  <key>CFBundleExecutable</key><string>TripsLocalAILR</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
</dict></plist>
PLIST
  cat > "$APP/Contents/MacOS/TripsLocalAILR" <<'SH'
#!/bin/bash
TOKEN_FILE="${LOCALAI_HOME:-$HOME/localai-lr}/gui/auth.token"
BASE="http://127.0.0.1:8766/"
if [ -f "$TOKEN_FILE" ]; then
  TOKEN=$(tr -d '\r\n' < "$TOKEN_FILE")
  open "${BASE}?token=${TOKEN}"
else
  open "$BASE"
fi
SH
  chmod +x "$APP/Contents/MacOS/TripsLocalAILR"
fi
chmod +x "$APP/Contents/MacOS/TripsLocalAILR" 2>/dev/null || true
if [ "${LOCALAI_SKIP_DOCK_RESTART:-0}" = "1" ]; then
  echo "App installed at $APP (Dock left unchanged)"
  exit 0
fi
BACKUP_DIR="${LOCALAI_HOME:-$HOME/localai-lr}/gui"
mkdir -p "$BACKUP_DIR"
defaults export com.apple.dock "$BACKUP_DIR/dock-backup-$(date +%Y%m%d-%H%M%S).plist" 2>/dev/null || true
python3 - <<'PY'
import plistlib, subprocess
from pathlib import Path
export_path = Path("/tmp/dock-current-lr.plist")
subprocess.check_call(["defaults", "export", "com.apple.dock", str(export_path)])
dock = plistlib.load(open(export_path, "rb"))
app_url = f"file://{Path.home()}/Applications/Trips%20Local%20AI%20for%20Lightroom.app/"
apps = []
for tile in dock.get("persistent-apps", []):
    url = tile.get("tile-data", {}).get("file-data", {}).get("_CFURLString", "")
    label = tile.get("tile-data", {}).get("file-label", "")
    bid = tile.get("tile-data", {}).get("bundle-identifier", "")
    if url == app_url or label == "Trips Local AI for Lightroom" or bid == "com.trips.localai-lr.panel":
        continue
    apps.append(tile)
apps.append({"tile-type":"file-tile","tile-data":{"file-data":{"_CFURLString":app_url,"_CFURLStringType":15},"file-label":"Trips Local AI for Lightroom","bundle-identifier":"com.trips.localai-lr.panel"}})
dock["persistent-apps"] = apps
plistlib.dump(dock, open("/tmp/dock-new-lr.plist","wb"), fmt=plistlib.FMT_BINARY)
subprocess.check_call(["defaults", "import", "com.apple.dock", "/tmp/dock-new-lr.plist"])
subprocess.call(["killall", "Dock"])
print("Dock updated with Trips Local AI for Lightroom")
PY
echo "App installed at $APP"
