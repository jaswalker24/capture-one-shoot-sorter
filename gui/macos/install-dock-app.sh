#!/bin/bash
# Installs ~/Applications/Trips Local AI.app and adds it to the Dock (does not remove other icons).
set -euo pipefail
APP="$HOME/Applications/Trips Local AI.app"
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/Applications"
rm -rf "$APP" "$HOME/Applications/Trip Local AI.app" "$HOME/Applications/Grok Local AI.app"
if [ -d "$ROOT/Trips Local AI.app" ]; then
  cp -R "$ROOT/Trips Local AI.app" "$APP"
else
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Trips Local AI</string>
  <key>CFBundleDisplayName</key><string>Trips Local AI</string>
  <key>CFBundleIdentifier</key><string>com.trips.localai.panel</string>
  <key>CFBundleExecutable</key><string>TripsLocalAI</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
</dict></plist>
PLIST
  cat > "$APP/Contents/MacOS/TripsLocalAI" <<'SH'
#!/bin/bash
TOKEN_FILE="$HOME/localai/gui/auth.token"
BASE="http://127.0.0.1:8765/"
if [ -f "$TOKEN_FILE" ]; then
  TOKEN=$(tr -d '\r\n' < "$TOKEN_FILE")
  open "${BASE}?token=${TOKEN}"
else
  open "$BASE"
fi
SH
  chmod +x "$APP/Contents/MacOS/TripsLocalAI"
fi
# Update the Dock tile unless this is a re-run that should not bounce the Dock.
if [ "${LOCALAI_SKIP_DOCK_RESTART:-0}" = "1" ]; then
  echo "App installed at $APP (Dock left unchanged)"
  exit 0
fi
defaults export com.apple.dock "$HOME/localai/gui/dock-backup-$(date +%Y%m%d-%H%M%S).plist" 2>/dev/null || true
python3 - <<'PY'
import plistlib, subprocess
from pathlib import Path
export_path = Path("/tmp/dock-current.plist")
subprocess.check_call(["defaults", "export", "com.apple.dock", str(export_path)])
dock = plistlib.load(open(export_path, "rb"))
drop_urls = {
    f"file://{Path.home()}/Applications/Grok%20Local%20AI.app/",
    f"file://{Path.home()}/Applications/Trips%20Local%20AI.app/",
}
apps = []
for tile in dock.get("persistent-apps", []):
    url = tile.get("tile-data", {}).get("file-data", {}).get("_CFURLString", "")
    label = tile.get("tile-data", {}).get("file-label", "")
    bid = tile.get("tile-data", {}).get("bundle-identifier", "")
    old_bids = {"com.trips.localai.panel", "com.grok.localai.panel"}
    if url in drop_urls or label in ("Trip Local AI", "Trips Local AI", "Grok Local AI") or bid in old_bids:
        continue
    apps.append(tile)
apps.append({"tile-type":"file-tile","tile-data":{"file-data":{"_CFURLString":f"file://{Path.home()}/Applications/Trips%20Local%20AI.app/","_CFURLStringType":15},"file-label":"Trips Local AI","bundle-identifier":"com.trips.localai.panel"}})
dock["persistent-apps"] = apps
plistlib.dump(dock, open("/tmp/dock-new.plist","wb"), fmt=plistlib.FMT_BINARY)
subprocess.check_call(["defaults", "import", "com.apple.dock", "/tmp/dock-new.plist"])
subprocess.call(["killall", "Dock"])
print("Dock updated with Trips Local AI")
PY
echo "App installed at $APP"
