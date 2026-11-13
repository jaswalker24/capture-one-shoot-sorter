#!/bin/bash
# Installs ~/Applications/Capture One Shoot Sorter.app and adds it to the Dock
# (does not remove other icons). Old Dock tiles for earlier names are replaced.
set -euo pipefail
APP="$HOME/Applications/Capture One Shoot Sorter.app"
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/Applications"
rm -rf "$APP" \
  "$HOME/Applications/Trips Local AI.app" \
  "$HOME/Applications/Trip Local AI.app" \
  "$HOME/Applications/Grok Local AI.app"
if [ -d "$ROOT/Capture One Shoot Sorter.app" ]; then
  cp -R "$ROOT/Capture One Shoot Sorter.app" "$APP"
else
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Capture One Shoot Sorter</string>
  <key>CFBundleDisplayName</key><string>Capture One Shoot Sorter</string>
  <key>CFBundleIdentifier</key><string>com.shootsorter.c1.panel</string>
  <key>CFBundleExecutable</key><string>CaptureOneShootSorter</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
</dict></plist>
PLIST
  cat > "$APP/Contents/MacOS/CaptureOneShootSorter" <<'SH'
#!/bin/bash
if [ -n "${LOCALAI_HOME:-}" ]; then
  TOKEN_FILE="$LOCALAI_HOME/gui/auth.token"
elif [ -n "${SHOOT_SORTER_C1_HOME:-}" ]; then
  TOKEN_FILE="$SHOOT_SORTER_C1_HOME/gui/auth.token"
elif [ -f "$HOME/localai/gui/auth.token" ]; then
  TOKEN_FILE="$HOME/localai/gui/auth.token"
else
  TOKEN_FILE="$HOME/shoot-sorter-c1/gui/auth.token"
fi
BASE="http://127.0.0.1:8765/"
if [ -f "$TOKEN_FILE" ]; then
  TOKEN=$(tr -d '\r\n' < "$TOKEN_FILE")
  open "${BASE}?token=${TOKEN}"
else
  open "$BASE"
fi
SH
  chmod +x "$APP/Contents/MacOS/CaptureOneShootSorter"
fi
# Update the Dock tile unless this is a re-run that should not bounce the Dock.
if [ "${LOCALAI_SKIP_DOCK_RESTART:-0}" = "1" ]; then
  echo "App installed at $APP (Dock left unchanged)"
  exit 0
fi
if [ -d "$HOME/localai/gui" ]; then
  DOCK_BACKUP_DIR="$HOME/localai/gui"
elif [ -d "$HOME/shoot-sorter-c1/gui" ]; then
  DOCK_BACKUP_DIR="$HOME/shoot-sorter-c1/gui"
else
  DOCK_BACKUP_DIR="$HOME/shoot-sorter-c1/gui"
  mkdir -p "$DOCK_BACKUP_DIR"
fi
defaults export com.apple.dock "$DOCK_BACKUP_DIR/dock-backup-$(date +%Y%m%d-%H%M%S).plist" 2>/dev/null || true
python3 - <<'PY'
import plistlib, subprocess
from pathlib import Path
export_path = Path("/tmp/dock-current.plist")
subprocess.check_call(["defaults", "export", "com.apple.dock", str(export_path)])
dock = plistlib.load(open(export_path, "rb"))
home = Path.home()
drop_urls = {
    f"file://{home}/Applications/Grok%20Local%20AI.app/",
    f"file://{home}/Applications/Trips%20Local%20AI.app/",
    f"file://{home}/Applications/Trip%20Local%20AI.app/",
    f"file://{home}/Applications/Capture%20One%20Shoot%20Sorter.app/",
}
old_labels = {
    "Trip Local AI", "Trips Local AI", "Grok Local AI", "Capture One Shoot Sorter",
}
old_bids = {
    "com.trips.localai.panel", "com.grok.localai.panel", "com.shootsorter.c1.panel",
}
apps = []
for tile in dock.get("persistent-apps", []):
    url = tile.get("tile-data", {}).get("file-data", {}).get("_CFURLString", "")
    label = tile.get("tile-data", {}).get("file-label", "")
    bid = tile.get("tile-data", {}).get("bundle-identifier", "")
    if url in drop_urls or label in old_labels or bid in old_bids:
        continue
    apps.append(tile)
apps.append({"tile-type":"file-tile","tile-data":{"file-data":{"_CFURLString":f"file://{Path.home()}/Applications/Capture%20One%20Shoot%20Sorter.app/","_CFURLStringType":15},"file-label":"Capture One Shoot Sorter","bundle-identifier":"com.shootsorter.c1.panel"}})
dock["persistent-apps"] = apps
plistlib.dump(dock, open("/tmp/dock-new.plist","wb"), fmt=plistlib.FMT_BINARY)
subprocess.check_call(["defaults", "import", "com.apple.dock", "/tmp/dock-new.plist"])
subprocess.call(["killall", "Dock"])
print("Dock updated with Capture One Shoot Sorter")
PY
echo "App installed at $APP"
