#!/bin/bash
# Installs ~/Applications/Trips Local AI.app and adds it to the Dock (does not remove other icons).
set -euo pipefail
APP="$HOME/Applications/Trips Local AI.app"
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/Applications"
rm -rf "$APP"
# Prefer bundled app if present, else rebuild minimal opener
if [ -d "$ROOT/Trips Local AI.app" ]; then
  cp -R "$ROOT/Trips Local AI.app" "$APP"
else
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Trip Local AI</string>
  <key>CFBundleIdentifier</key><string>com.trips.localai.panel</string>
  <key>CFBundleExecutable</key><string>TripsLocalAI</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
</dict></plist>
PLIST
  cat > "$APP/Contents/MacOS/TripsLocalAI" <<'SH'
#!/bin/bash
open "http://127.0.0.1:8765/"
SH
  chmod +x "$APP/Contents/MacOS/TripsLocalAI"
fi
# Backup dock then append tile
defaults export com.apple.dock "$HOME/localai/gui/dock-backup-$(date +%Y%m%d-%H%M%S).plist" 2>/dev/null || true
python3 - <<'PY'
import plistlib, subprocess
from pathlib import Path
export_path = Path("/tmp/dock-current.plist")
subprocess.check_call(["defaults", "export", "com.apple.dock", str(export_path)])
dock = plistlib.load(open(export_path, "rb"))
app_url = f"file://{Path.home()}/Applications/Trips%20Local%20AI.app/"
apps = dock.get("persistent-apps", [])
def is_ours(tile):
    return tile.get("tile-data", {}).get("file-data", {}).get("_CFURLString", "") == app_url
if not any(is_ours(t) for t in apps):
    apps.append({"tile-type":"file-tile","tile-data":{"file-data":{"_CFURLString":app_url,"_CFURLStringType":15},"file-label":"Trip Local AI","bundle-identifier":"com.trips.localai.panel"}})
    dock["persistent-apps"] = apps
    plistlib.dump(dock, open("/tmp/dock-new.plist","wb"), fmt=plistlib.FMT_BINARY)
    subprocess.check_call(["defaults", "import", "com.apple.dock", "/tmp/dock-new.plist"])
    subprocess.call(["killall", "Dock"])
    print("Dock updated")
else:
    print("Already in Dock")
PY
echo "App installed at $APP"
