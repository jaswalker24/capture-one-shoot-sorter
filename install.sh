#!/bin/bash
# Install Trip Local AI for Capture One into ~/localai on an Apple-silicon Mac.
# Safe to re-run: it skips steps that are already done.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DEST="${LOCALAI_HOME:-$HOME/localai}"
SCRIPTS_DIR="$HOME/Library/Scripts/Capture One Scripts/Trip Local AI"
EXIFTOOL_VER="13.61"
OLLAMA_VER_HINT="0.40.x"
PYTHON_VER="3.12"

say() { printf '\n==> %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS only." >&2; exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Apple silicon (arm64) is required." >&2; exit 1
fi

say "Installing into $DEST"
mkdir -p "$DEST"/{bin,models/{hf,aesthetic,faces},cache,out,logs/scripts,index,keywords,schedules,scripts/src,ollama,tools}

# --- copy project files ---
say "Copying project files"
rsync -a --exclude '.git' --exclude 'install.sh' --exclude 'README.md' \
  --exclude 'scripts/compiled' \
  "$ROOT"/ "$DEST"/
# Keep a copy of compiled scripts for reference
mkdir -p "$DEST/scripts/compiled"
rsync -a "$ROOT/scripts/compiled/" "$DEST/scripts/compiled/" 2>/dev/null || true
chmod +x "$DEST/tag" "$DEST/start-ollama.sh" "$DEST/test_scripts.sh" 2>/dev/null || true

if [[ ! -f "$DEST/config.json" ]]; then
  cp "$DEST/config.example.json" "$DEST/config.json"
  say "Wrote $DEST/config.json from the example"
fi

# --- uv + Python 3.12 venv ---
say "Setting up uv and a Python $PYTHON_VER virtualenv"
if [[ ! -x "$DEST/bin/uv" ]]; then
  say "Downloading uv"
  curl -fsSL "https://github.com/astral-sh/uv/releases/download/0.13.7/uv-aarch64-apple-darwin.tar.gz" \
    | tar -xz -C "$DEST/bin" --strip-components=1
  chmod +x "$DEST/bin/uv" "$DEST/bin/uvx" 2>/dev/null || chmod +x "$DEST/bin/uv"
fi
UV="$DEST/bin/uv"
export UV_CACHE_DIR="$DEST/.uvcache"
export UV_PYTHON_INSTALL_DIR="$DEST/python"

if [[ ! -x "$DEST/venv/bin/python" ]]; then
  "$UV" python install "$PYTHON_VER"
  "$UV" venv --python "$PYTHON_VER" "$DEST/venv"
fi
# shellcheck disable=SC1091
source "$DEST/venv/bin/activate"
say "Installing Python packages (this can take several minutes)"
"$UV" pip install -r "$DEST/requirements.txt"

# --- ExifTool (portable) ---
say "Installing ExifTool $EXIFTOOL_VER"
if [[ ! -x "$DEST/tools/Image-ExifTool-$EXIFTOOL_VER/exiftool" ]]; then
  curl -fsSL "https://exiftool.org/Image-ExifTool-$EXIFTOOL_VER.tar.gz" \
    | tar -xz -C "$DEST/tools"
fi
ln -sfn "$DEST/tools/Image-ExifTool-$EXIFTOOL_VER/exiftool" "$DEST/bin/exiftool"

# --- Ollama (standalone, localhost only) ---
say "Installing standalone Ollama into $DEST/ollama"
if [[ ! -x "$DEST/ollama/ollama" ]]; then
  TMP=$(mktemp -d)
  curl -fsSL "https://ollama.com/download/ollama-darwin-arm64.tgz" -o "$TMP/ollama.tgz"
  tar -xzf "$TMP/ollama.tgz" -C "$TMP"
  # tarball layout varies; find the binary
  BIN=$(find "$TMP" -type f -name ollama | head -1)
  mkdir -p "$DEST/ollama"
  cp "$BIN" "$DEST/ollama/ollama"
  chmod +x "$DEST/ollama/ollama"
  rm -rf "$TMP"
fi

say "Starting Ollama and pulling qwen2.5vl:3b (about 3 GB, first time only)"
export OLLAMA_MODELS="$DEST/models"
export OLLAMA_HOST=127.0.0.1:11434
"$DEST/start-ollama.sh" >/dev/null || true
# wait for server
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:11434/api/version >/dev/null && break
  sleep 1
done
"$DEST/ollama/ollama" pull qwen2.5vl:3b

# --- optional feature weights (aesthetic ranking + face grouping) ---
say "Fetching optional feature weights"
AES_URL="https://github.com/christophschuhmann/improved-aesthetic-predictor/raw/main/sac+logos+ava1-l14-linearMSE.pth"
AES_DST="$DEST/models/aesthetic/sac+logos+ava1-l14-linearMSE.pth"
if [[ ! -f "$AES_DST" ]]; then
  curl -fsSL "$AES_URL" -o "$AES_DST" || echo "Warning: aesthetic weights download failed (rank will need them later)"
fi
# InsightFace buffalo_s MobileFaceNet (w600k_mbf.onnx) — used only by face grouping
FACE_DST="$DEST/models/faces/w600k_mbf.onnx"
if [[ ! -f "$FACE_DST" ]]; then
  # Official insightface model zoo mirror (buffalo_s pack contains w600k_mbf.onnx)
  TMP=$(mktemp -d)
  if curl -fsSL "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip" -o "$TMP/buffalo_s.zip"; then
    unzip -qo "$TMP/buffalo_s.zip" -d "$TMP"
    ONNX=$(find "$TMP" -name 'w600k_mbf.onnx' | head -1)
    if [[ -n "$ONNX" ]]; then cp "$ONNX" "$FACE_DST"; fi
  else
    echo "Warning: face model download failed (face grouping will need it later)"
  fi
  rm -rf "$TMP"
fi

# --- Capture One Scripts menu ---
say "Installing Capture One Scripts menu: Trip Local AI"
mkdir -p "$SCRIPTS_DIR"
# Prefer compiling from source so scripts always point at $HOME/localai
if have osacompile; then
  for src in "$DEST/scripts/src/"*.applescript; do
    base=$(basename "$src" .applescript)
    osacompile -o "$SCRIPTS_DIR/$base.scpt" "$src"
  done
else
  rsync -a "$ROOT/scripts/compiled/" "$SCRIPTS_DIR/"
fi

# --- Keyword library reminder ---
say "Keyword library"
python3 "$DEST/keywords/build_library.py" >/dev/null || true
cat <<EOF

Installation finished.

Next steps (one-time in Capture One):
  1. Open Capture One 16.8+.
  2. Window → Keyword Library (or the Keyword tool).
  3. Create / import a library named exactly: Trip Keywords
  4. Import the tab-indented file:
       $DEST/keywords/trip-keyword-library.txt
  5. Scripts menu → Trip Local AI should list the six actions
     (plus Draft Captions (Exports) for export folders only).

Quick smoke test from Terminal:
  $DEST/start-ollama.sh
  $DEST/tag --help
  # Then select a few frames in Capture One and run Scripts → Trip Local AI → Tag with Local AI

SigLIP / CLIP weights download themselves on first tagging run
(~1–2 GB into $DEST/models/hf). Keep the Mac awake for that.
EOF

# Control panel LaunchAgent (optional; starts on login)
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/localai/gui" "$HOME/localai/logs/gui"
if [ -f "$ROOT/gui/com.trips.localai.gui.plist" ]; then
  cp "$ROOT/gui/server.py" "$HOME/localai/gui/server.py"
  cp -R "$ROOT/gui/static" "$HOME/localai/gui/"
  cp "$ROOT/jobrunner.py" "$HOME/localai/jobrunner.py"
  # rewrite plist paths for this user
  sed "s|__HOME__|$HOME|g" "$ROOT/gui/com.trips.localai.gui.plist" > "$HOME/Library/LaunchAgents/com.trips.localai.gui.plist"
  cp "$HOME/Library/LaunchAgents/com.trips.localai.gui.plist" "$HOME/localai/gui/com.trips.localai.gui.plist"
  if [ ! -f "$HOME/localai/gui/auth.token" ]; then
    python3 -c 'import secrets; open("'"$HOME"'/localai/gui/auth.token","w").write(secrets.token_urlsafe(24))'
    chmod 600 "$HOME/localai/gui/auth.token"
  fi
  mkdir -p "$HOME/localai/gui/macos"
  cp -R "$ROOT/gui/macos/"* "$HOME/localai/gui/macos/" 2>/dev/null || true
  echo "Control panel installed. Start with: launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.trips.localai.gui.plist"
  echo "Open http://127.0.0.1:8765/  (token in ~/localai/gui/auth.token)"
fi

# Profiles (seed examples)
mkdir -p "$HOME/localai/profiles"
if [ -d "$ROOT/profiles" ]; then cp -R "$ROOT/profiles/"*.json "$HOME/localai/profiles/" 2>/dev/null || true; fi
