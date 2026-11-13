#!/bin/bash
# Install Capture One Shoot Sorter into ~/shoot-sorter-c1 on macOS.
# If ~/localai already exists, that folder is kept and reused so a live
# install is not moved. Safe to re-run: existing config, token, models,
# venv and a running control panel are left alone.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_HOME="$HOME/shoot-sorter-c1"
LEGACY_HOME="$HOME/localai"
LAUNCH_AGENTS_DIR="${LOCALAI_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
EXIFTOOL_VER="13.61"
UV_VER="0.13.7"
OLLAMA_VER="0.40.15"
PYTHON_VER="3.12"
NEW_GUI_LABEL="com.shootsorter.c1.gui"
LEGACY_GUI_LABELS=("com.trips.localai.gui" "com.grok.localai.gui")
NEW_SCRIPTS_DIR="$HOME/Library/Scripts/Capture One Scripts/Capture One Shoot Sorter"
NEW_DOCK_APP="Capture One Shoot Sorter.app"

DEST=""
SCRIPTS_DIR=""
GUI_LABEL=""

DRY_RUN=0
SKIP_MODELS=0
SKIP_DOCK=0
SKIP_LAUNCH=0
SKIP_DOWNLOADS=0
WITH_TRANSLATION=0
DO_UNINSTALL=0

usage() {
  cat <<EOF
Usage: ./install.sh [options]

Installs Capture One Shoot Sorter into ~/shoot-sorter-c1 on an Apple-silicon Mac.
If ~/localai already exists, that folder is kept and reused.
Safe to re-run. Never overwrites an existing config.json or GUI token,
and never prints secrets.

Options:
  --dry-run                 Print what would happen; change nothing
  --skip-models             Skip Ollama model pulls and weight downloads
  --skip-downloads          Skip uv, ExifTool, Ollama and model downloads
  --skip-dock               Do not install or refresh the Dock shortcut
  --skip-launch-agents      Write no LaunchAgent and do not load one
  --with-translation-model  Also pull qwen3:8b (OCR translation, ~5 GB)
  --uninstall               Remove the LaunchAgent, Scripts menu, Dock app
                            and the runtime folder. Photos and the Capture
                            One catalog are not touched.
  -h, --help                Show this help

Environment:
  LOCALAI_HOME              Install prefix (default: ~/shoot-sorter-c1,
                            or ~/localai if that folder already exists)
  SHOOT_SORTER_C1_HOME      Same as LOCALAI_HOME (LOCALAI_HOME wins if both set)
  LOCALAI_SCRIPTS_DIR       Capture One Scripts folder
  LOCALAI_LAUNCH_AGENTS_DIR LaunchAgents folder
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip-models) SKIP_MODELS=1 ;;
    --skip-downloads) SKIP_DOWNLOADS=1; SKIP_MODELS=1 ;;
    --skip-dock) SKIP_DOCK=1 ;;
    --skip-launch-agents) SKIP_LAUNCH=1 ;;
    --with-translation-model) WITH_TRANSLATION=1 ;;
    --uninstall) DO_UNINSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

resolve_dest() {
  if [[ -n "${LOCALAI_HOME:-}" ]]; then
    DEST="$LOCALAI_HOME"
  elif [[ -n "${SHOOT_SORTER_C1_HOME:-}" ]]; then
    DEST="$SHOOT_SORTER_C1_HOME"
  elif [[ -d "$LEGACY_HOME" ]]; then
    DEST="$LEGACY_HOME"
  else
    DEST="$DEFAULT_HOME"
  fi
}

resolve_gui_label() {
  local label="" loaded="" f
  if [[ "$(uname -s)" == "Darwin" ]]; then
    local uid_n
    uid_n="$(id -u)"
    if launchctl print "gui/${uid_n}/${NEW_GUI_LABEL}" >/dev/null 2>&1; then
      loaded="$NEW_GUI_LABEL"
    else
      for label in "${LEGACY_GUI_LABELS[@]}"; do
        if launchctl print "gui/${uid_n}/${label}" >/dev/null 2>&1; then
          loaded="$label"
          break
        fi
      done
    fi
  fi
  if [[ -n "$loaded" ]]; then
    GUI_LABEL="$loaded"
    return
  fi
  if [[ -f "$LAUNCH_AGENTS_DIR/${NEW_GUI_LABEL}.plist" ]]; then
    GUI_LABEL="$NEW_GUI_LABEL"
    return
  fi
  for label in "${LEGACY_GUI_LABELS[@]}"; do
    if [[ -f "$LAUNCH_AGENTS_DIR/${label}.plist" ]]; then
      GUI_LABEL="$label"
      return
    fi
  done
  GUI_LABEL="$NEW_GUI_LABEL"
}

resolve_scripts_dir() {
  local d
  if [[ -n "${LOCALAI_SCRIPTS_DIR:-}" ]]; then
    SCRIPTS_DIR="$LOCALAI_SCRIPTS_DIR"
    return
  fi
  for d in \
    "$NEW_SCRIPTS_DIR" \
    "$HOME/Library/Scripts/Capture One Scripts/Trips Local AI" \
    "$HOME/Library/Scripts/Capture One Scripts/Trip Local AI" \
    "$HOME/Library/Scripts/Capture One Scripts/Grok Local AI"
  do
    if [[ -d "$d" ]]; then
      SCRIPTS_DIR="$d"
      return
    fi
  done
  SCRIPTS_DIR="$NEW_SCRIPTS_DIR"
}

resolve_dest
resolve_gui_label
resolve_scripts_dir

say() { printf '\n==> %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] %s\n' "$*"
    return 0
  fi
  "$@"
}

do_uninstall() {
  echo "Removing Capture One Shoot Sorter."
  echo "Photos and the Capture One catalog are not touched."
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] would unload LaunchAgents, remove Scripts menu, Dock app and $DEST"
    exit 0
  fi
  if [[ "$(uname -s)" == "Darwin" ]]; then
    UID_N="$(id -u)"
    launchctl bootout "gui/${UID_N}/${NEW_GUI_LABEL}" 2>/dev/null || true
    local label
    for label in "${LEGACY_GUI_LABELS[@]}"; do
      launchctl bootout "gui/${UID_N}/${label}" 2>/dev/null || true
    done
  fi
  rm -f "$HOME/Library/LaunchAgents/${NEW_GUI_LABEL}.plist"
  local label
  for label in "${LEGACY_GUI_LABELS[@]}"; do
    rm -f "$HOME/Library/LaunchAgents/${label}.plist"
  done
  rm -rf "$HOME/Library/Scripts/Capture One Scripts/Capture One Shoot Sorter"
  rm -rf "$HOME/Library/Scripts/Capture One Scripts/Trips Local AI"
  rm -rf "$HOME/Library/Scripts/Capture One Scripts/Trip Local AI"
  rm -rf "$HOME/Library/Scripts/Capture One Scripts/Grok Local AI"
  rm -rf "$HOME/Applications/Capture One Shoot Sorter.app"
  rm -rf "$HOME/Applications/Trips Local AI.app"
  rm -rf "$HOME/Applications/Trip Local AI.app"
  rm -rf "$HOME/Applications/Grok Local AI.app"
  rm -rf "$DEST"
  echo "Removed. You can delete the Dock tile if it is still there."
  exit 0
}

ARCH="$(uname -m)"
OS="$(uname -s)"
OS_OK=1

if [[ "$OS" != "Darwin" ]]; then
  OS_OK=0
  echo "This installer is for macOS only (found $OS)." >&2
  if [[ "$DRY_RUN" -eq 0 ]]; then
    exit 1
  fi
  echo "[dry-run] continuing so the remaining plan can be shown." >&2
fi

if [[ "$ARCH" != "arm64" ]]; then
  cat >&2 <<EOF
Apple silicon (arm64) is required. This Mac is $ARCH.

Capture One Shoot Sorter is not supported on Intel Macs, and it will not just run
slower. There is no MLX in this stack, Apple Vision and Ollama could
theoretically run on Intel, and the Python code falls back from MPS to
CPU when no Apple GPU is present — but the pinned PyTorch 2.15 wheels
are published only for macOS ARM. A working venv cannot be created on
Intel with the current requirements, so the installer stops here.
EOF
  if [[ "$DRY_RUN" -eq 0 ]]; then
    exit 1
  fi
  echo "[dry-run] continuing so the remaining plan can be shown." >&2
fi

if [[ "$OS" == "Darwin" ]] && have sw_vers; then
  MACOS_VER="$(sw_vers -productVersion)"
  MACOS_MAJOR="${MACOS_VER%%.*}"
  if [[ "$MACOS_MAJOR" -lt 14 ]]; then
    echo "macOS 14 or newer is required (found $MACOS_VER)." >&2
    if [[ "$DRY_RUN" -eq 0 ]]; then
      exit 1
    fi
  fi
fi

if [[ "$DO_UNINSTALL" -eq 1 ]]; then
  do_uninstall
fi

# uv wheel tag: aarch64-apple-darwin or x86_64-apple-darwin
UV_TRIPLE="aarch64-apple-darwin"
if [[ "$ARCH" == "x86_64" ]]; then
  UV_TRIPLE="x86_64-apple-darwin"
fi

say "Installing into $DEST"
if [[ "$DRY_RUN" -eq 0 ]]; then
  mkdir -p "$DEST"/{bin,models/{hf,aesthetic,faces},cache,out,logs/{scripts,gui},index,keywords,schedules,scripts/src,ollama,tools,gui,profiles,python}
fi

# --- prerequisites ---
say "Checking prerequisites"
MISSING=()
for cmd in curl tar unzip; do
  if ! have "$cmd"; then
    MISSING+=("$cmd")
  fi
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "Missing required tools: ${MISSING[*]}" >&2
  echo "Install Apple's Command Line Tools (xcode-select --install) and re-run." >&2
  if [[ "$DRY_RUN" -eq 0 ]]; then
    exit 1
  fi
fi

if have brew; then
  echo "Homebrew: $(brew --prefix 2>/dev/null || echo found)"
else
  echo "Homebrew is not required (uv, ExifTool and Ollama are installed into $DEST)."
  echo "You can install Homebrew later if you want a system-wide exiftool as a fallback."
fi

if [[ "$OS" == "Darwin" ]]; then
  if xcode-select -p >/dev/null 2>&1; then
    echo "Command Line Tools: $(xcode-select -p)"
  else
    echo "Command Line Tools are not installed. Some steps (compiling Scripts menu items) need them."
    echo "Run: xcode-select --install"
    if [[ "$DRY_RUN" -eq 0 && "$OS_OK" -eq 1 ]]; then
      echo "Continuing; Scripts menu items will fall back to precompiled copies if needed."
    fi
  fi
fi

# --- copy project files (never clobber runtime state or secrets) ---
say "Copying project files"
RSYNC_EXCLUDES=(
  --exclude '.git'
  --exclude '.gitignore'
  --exclude 'install.sh'
  --exclude 'venv'
  --exclude 'python'
  --exclude '.uvcache'
  --exclude 'models'
  --exclude 'ollama'
  --exclude 'cache'
  --exclude 'index'
  --exclude 'out'
  --exclude 'logs'
  --exclude 'people'
  --exclude 'config.json'
  --exclude 'gui/auth.token'
  --exclude 'gui/jobs'
  --exclude 'gui/history'
  --exclude 'gui/notify.json'
  --exclude 'gui/presets.json'
  --exclude 'gui/dock-backup-*.plist'
)
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] rsync -a ${RSYNC_EXCLUDES[*]} $ROOT/ $DEST/"
else
  rsync -a "${RSYNC_EXCLUDES[@]}" "$ROOT"/ "$DEST"/
  chmod +x "$DEST/tag" "$DEST/start-ollama.sh" "$DEST/test_scripts.sh" 2>/dev/null || true
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] copy config.example.json -> $DEST/config.json (only if missing)"
elif [[ ! -f "$DEST/config.json" ]]; then
  if [[ -f "$DEST/config.example.json" ]]; then
    cp "$DEST/config.example.json" "$DEST/config.json"
  elif [[ -f "$ROOT/config.example.json" ]]; then
    cp "$ROOT/config.example.json" "$DEST/config.json"
  else
    echo "config.example.json is missing from the release; write $DEST/config.json by hand." >&2
  fi
  say "Wrote $DEST/config.json from the example (existing files are never overwritten)"
else
  say "Keeping existing $DEST/config.json"
fi

# --- uv + Python 3.12 venv ---
say "Setting up uv and a Python $PYTHON_VER virtualenv"
UV="$DEST/bin/uv"
if [[ "$SKIP_DOWNLOADS" -eq 1 ]]; then
  echo "Skipping uv / Python downloads"
elif [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] ensure $UV ($UV_TRIPLE uv $UV_VER) and $DEST/venv with Python $PYTHON_VER"
  echo "[dry-run] $UV pip install -r $DEST/requirements.txt"
else
  if [[ ! -x "$UV" ]]; then
    say "Downloading uv $UV_VER"
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VER}/uv-${UV_TRIPLE}.tar.gz" \
      | tar -xz -C "$DEST/bin" --strip-components=1
    chmod +x "$DEST/bin/uv" "$DEST/bin/uvx" 2>/dev/null || chmod +x "$DEST/bin/uv"
  fi
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
fi

# --- ExifTool (portable; Homebrew is a fallback) ---
say "Installing ExifTool $EXIFTOOL_VER"
if [[ "$SKIP_DOWNLOADS" -eq 1 ]]; then
  echo "Skipping ExifTool download"
elif [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] install Image-ExifTool-$EXIFTOOL_VER into $DEST/tools and link $DEST/bin/exiftool"
else
  if [[ ! -x "$DEST/tools/Image-ExifTool-$EXIFTOOL_VER/exiftool" ]]; then
    if curl -fsSL "https://exiftool.org/Image-ExifTool-$EXIFTOOL_VER.tar.gz" \
      | tar -xz -C "$DEST/tools"; then
      :
    elif have brew; then
      echo "Direct ExifTool download failed; trying Homebrew"
      brew list exiftool >/dev/null 2>&1 || brew install exiftool
    else
      echo "Warning: ExifTool download failed. Install it later (brew install exiftool) or re-run." >&2
    fi
  fi
  if [[ -x "$DEST/tools/Image-ExifTool-$EXIFTOOL_VER/exiftool" ]]; then
    ln -sfn "$DEST/tools/Image-ExifTool-$EXIFTOOL_VER/exiftool" "$DEST/bin/exiftool"
  elif have exiftool; then
    ln -sfn "$(command -v exiftool)" "$DEST/bin/exiftool"
  fi
fi

# --- Ollama (standalone, localhost only) ---
say "Installing standalone Ollama $OLLAMA_VER into $DEST/ollama"
if [[ "$SKIP_DOWNLOADS" -eq 1 ]]; then
  echo "Skipping Ollama download"
elif [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] download ollama-darwin.tgz (v$OLLAMA_VER, universal) into $DEST/ollama"
else
  if [[ ! -x "$DEST/ollama/ollama" ]]; then
    TMP="$(mktemp -d)"
    curl -fsSL "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VER}/ollama-darwin.tgz" \
      -o "$TMP/ollama.tgz"
    tar -xzf "$TMP/ollama.tgz" -C "$TMP"
    BIN="$(find "$TMP" -type f -name ollama | head -1)"
    if [[ -z "$BIN" ]]; then
      echo "Ollama binary not found in the tarball." >&2
      rm -rf "$TMP"
      exit 1
    fi
    mkdir -p "$DEST/ollama"
    # Copy the binary and any libraries that shipped beside it.
    rsync -a "$(dirname "$BIN")"/ "$DEST/ollama"/
    chmod +x "$DEST/ollama/ollama"
    rm -rf "$TMP"
  fi
fi

pull_models() {
  local default_home="$DEFAULT_HOME"
  if [[ -d "$LEGACY_HOME" ]]; then
    default_home="$LEGACY_HOME"
  fi
  if [[ "$SKIP_MODELS" -eq 1 ]]; then
    echo "Skipping model downloads"
    return 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] start Ollama on 127.0.0.1:11434 and pull qwen2.5vl:3b"
    if [[ "$WITH_TRANSLATION" -eq 1 ]]; then
      echo "[dry-run] also pull qwen3:8b"
    fi
    echo "[dry-run] fetch aesthetic + face weights into $DEST/models"
    return 0
  fi

  if [[ "$DEST" != "$default_home" ]] && curl -sf http://127.0.0.1:11434/api/version >/dev/null; then
    echo "Ollama is already running on 11434, and the install prefix is not $default_home."
    echo "Skipping model pulls so a live server's models directory is not changed."
    return 0
  fi

  say "Starting Ollama and pulling qwen2.5vl:3b (about 3 GB, first time only)"
  export OLLAMA_MODELS="$DEST/models"
  export OLLAMA_HOST=127.0.0.1:11434
  if [[ -x "$DEST/start-ollama.sh" ]]; then
    # start-ollama.sh uses the resolved runtime folder unless LOCALAI_HOME is set
    LOCALAI_HOME="$DEST" "$DEST/start-ollama.sh" >/dev/null || true
  fi
  local n=0
  while [[ "$n" -lt 30 ]]; do
    curl -sf http://127.0.0.1:11434/api/version >/dev/null && break
    n=$((n + 1))
    sleep 1
  done
  if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null; then
    echo "Warning: Ollama did not start; pull models later with:" >&2
    echo "  LOCALAI_HOME=$DEST $DEST/start-ollama.sh" >&2
    echo "  OLLAMA_MODELS=$DEST/models $DEST/ollama/ollama pull qwen2.5vl:3b" >&2
    return 0
  fi
  OLLAMA_MODELS="$DEST/models" "$DEST/ollama/ollama" pull qwen2.5vl:3b
  if [[ "$WITH_TRANSLATION" -eq 1 ]]; then
    say "Pulling qwen3:8b (OCR translation, about 5 GB)"
    OLLAMA_MODELS="$DEST/models" "$DEST/ollama/ollama" pull qwen3:8b
  fi

  say "Fetching optional feature weights"
  local AES_URL AES_DST FACE_DST TMP ONNX
  AES_URL="https://github.com/christophschuhmann/improved-aesthetic-predictor/raw/main/sac+logos+ava1-l14-linearMSE.pth"
  AES_DST="$DEST/models/aesthetic/sac+logos+ava1-l14-linearMSE.pth"
  if [[ ! -f "$AES_DST" ]]; then
    curl -fsSL "$AES_URL" -o "$AES_DST" || echo "Warning: aesthetic weights download failed (rank will need them later)"
  fi
  FACE_DST="$DEST/models/faces/w600k_mbf.onnx"
  if [[ ! -f "$FACE_DST" ]]; then
    TMP="$(mktemp -d)"
    if curl -fsSL "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip" -o "$TMP/buffalo_s.zip"; then
      unzip -qo "$TMP/buffalo_s.zip" -d "$TMP"
      ONNX="$(find "$TMP" -name 'w600k_mbf.onnx' | head -1)"
      if [[ -n "$ONNX" ]]; then
        cp "$ONNX" "$FACE_DST"
      fi
    else
      echo "Warning: face model download failed (face grouping will need it later)"
    fi
    rm -rf "$TMP"
  fi
}
pull_models

# --- Capture One Scripts menu ---
say "Installing Capture One Scripts menu: $(basename "$SCRIPTS_DIR")"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] compile or copy scripts into $SCRIPTS_DIR"
else
  mkdir -p "$SCRIPTS_DIR"
  if have osacompile; then
    for src in "$DEST/scripts/src/"*.applescript; do
      [[ -f "$src" ]] || continue
      base="$(basename "$src" .applescript)"
      osacompile -o "$SCRIPTS_DIR/$base.scpt" "$src"
    done
  elif [[ -d "$ROOT/scripts/compiled" ]]; then
    rsync -a "$ROOT/scripts/compiled/" "$SCRIPTS_DIR/"
  fi
fi

# --- Keyword library (rebuild text/csv; import into Capture One is manual) ---
say "Keyword library"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] $DEST/venv/bin/python $DEST/keywords/build_library.py"
elif [[ -x "$DEST/venv/bin/python" && -f "$DEST/keywords/build_library.py" ]]; then
  "$DEST/venv/bin/python" "$DEST/keywords/build_library.py" >/dev/null || true
elif have python3 && [[ -f "$DEST/keywords/build_library.py" ]]; then
  python3 "$DEST/keywords/build_library.py" >/dev/null || true
fi

# --- GUI token, LaunchAgent, Dock shortcut ---
say "Control panel token, LaunchAgent and Dock shortcut"
TOKEN_FILE="$DEST/gui/auth.token"
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] create $TOKEN_FILE with chmod 600 if missing (never printed)"
else
  mkdir -p "$DEST/gui" "$DEST/logs/gui"
  if [[ ! -f "$TOKEN_FILE" ]]; then
    # Generate locally; never echo the value.
    if [[ -x "$DEST/venv/bin/python" ]]; then
      PY="$DEST/venv/bin/python"
    else
      PY="python3"
    fi
    "$PY" -c 'import secrets, sys; from pathlib import Path
p = Path(sys.argv[1])
p.write_text(secrets.token_urlsafe(24), encoding="utf-8")
p.chmod(0o600)
' "$TOKEN_FILE"
  else
    chmod 600 "$TOKEN_FILE" 2>/dev/null || true
  fi
fi

if [[ "$SKIP_LAUNCH" -eq 1 ]]; then
  echo "Skipping LaunchAgent"
elif [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] write $LAUNCH_AGENTS_DIR/$GUI_LABEL.plist (load only if not already running)"
else
  mkdir -p "$LAUNCH_AGENTS_DIR"
  PLIST_SRC="$ROOT/gui/com.shootsorter.c1.gui.plist"
  if [[ ! -f "$PLIST_SRC" ]]; then
    PLIST_SRC="$DEST/gui/com.shootsorter.c1.gui.plist"
  fi
  if [[ ! -f "$PLIST_SRC" ]]; then
    PLIST_SRC="$ROOT/gui/com.trips.localai.gui.plist"
  fi
  if [[ ! -f "$PLIST_SRC" ]]; then
    PLIST_SRC="$DEST/gui/com.trips.localai.gui.plist"
  fi
  if [[ -f "$PLIST_SRC" ]]; then
    # Fill __DEST__ / __HOME__ and point leftover legacy paths at DEST.
    sed -e "s|__DEST__|$DEST|g" \
        -e "s|__HOME__|$HOME|g" \
        -e "s|$HOME/localai|$DEST|g" \
        -e "s|$HOME/shoot-sorter-c1|$DEST|g" \
        "$PLIST_SRC" > "$LAUNCH_AGENTS_DIR/$GUI_LABEL.plist"
    if command -v plutil >/dev/null 2>&1; then
      plutil -replace Label -string "$GUI_LABEL" "$LAUNCH_AGENTS_DIR/$GUI_LABEL.plist" 2>/dev/null || true
    fi
    cp "$LAUNCH_AGENTS_DIR/$GUI_LABEL.plist" "$DEST/gui/$GUI_LABEL.plist"
    REAL_AGENTS="$HOME/Library/LaunchAgents"
    if [[ "$OS" == "Darwin" && "$LAUNCH_AGENTS_DIR" == "$REAL_AGENTS" ]]; then
      UID_N="$(id -u)"
      already=0
      if launchctl print "gui/${UID_N}/${GUI_LABEL}" >/dev/null 2>&1; then
        already=1
      else
        for label in "${LEGACY_GUI_LABELS[@]}"; do
          if launchctl print "gui/${UID_N}/${label}" >/dev/null 2>&1; then
            already=1
            break
          fi
        done
      fi
      if [[ "$already" -eq 1 ]]; then
        echo "Control panel LaunchAgent is already loaded; leaving it running."
        echo "To refresh after an update, use: $DEST/gui/reload_when_idle.py"
      else
        launchctl bootstrap "gui/${UID_N}" "$LAUNCH_AGENTS_DIR/$GUI_LABEL.plist"
        echo "Control panel LaunchAgent loaded."
      fi
    else
      echo "LaunchAgent written to $LAUNCH_AGENTS_DIR/$GUI_LABEL.plist (not loaded: non-standard folder)."
    fi
  fi
fi

if [[ "$SKIP_DOCK" -eq 1 ]]; then
  echo "Skipping Dock shortcut"
elif [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] install ~/Applications/Capture One Shoot Sorter.app and add it to the Dock if missing"
else
  mkdir -p "$DEST/gui/macos"
  if [[ -d "$ROOT/gui/macos" ]]; then
    rsync -a "$ROOT/gui/macos/" "$DEST/gui/macos/"
  fi
  if [[ "$OS" == "Darwin" && -x "$DEST/gui/macos/install-dock-app.sh" ]]; then
    # Do not bounce the Dock on a re-run when the new app is already present.
    # If only a previous-name app is present, update the Dock tile.
    if [[ -d "$HOME/Applications/Capture One Shoot Sorter.app" ]]; then
      LOCALAI_SKIP_DOCK_RESTART=1 "$DEST/gui/macos/install-dock-app.sh" || \
        echo "Warning: Dock shortcut refresh failed (the panel still works at http://127.0.0.1:8765/)"
    else
      "$DEST/gui/macos/install-dock-app.sh" || \
        echo "Warning: Dock shortcut install failed (the panel still works at http://127.0.0.1:8765/)"
    fi
  fi
fi

# Profiles (seed examples; do not delete user-added profiles)
if [[ "$DRY_RUN" -eq 0 && -d "$ROOT/profiles" ]]; then
  mkdir -p "$DEST/profiles"
  cp -R "$ROOT/profiles/"*.json "$DEST/profiles/" 2>/dev/null || true
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  FINISH_NOTE=" (dry run — nothing was changed)"
else
  FINISH_NOTE=""
fi
cat <<EOF

Installation finished${FINISH_NOTE}.

Next steps (one-time):

  1. Set your NAS photo folders in the control panel under
     Settings → Locations (Browse or paste), or edit $DEST/config.json
     (imports_root, exports_root, and optional photos_root /
     other_exports_root). Mount the volume before using the health strip.

  2. Grant permissions in System Settings → Privacy & Security:
       • Automation — allow Terminal, osascript and the control-panel
         Python to control Capture One.
       • Full Disk Access — give Capture One, Terminal and
         $DEST/venv/bin/python access if your photos live on a NAS
         or an external disk.

  3. Open Capture One 16.8.x (this setup is tested with 16.8.5.30).
     Window → Keyword Library: create or import a library named exactly
     "Trip Keywords", then import:
       $DEST/keywords/trip-keyword-library.txt
     Scripts → Capture One Shoot Sorter should list the tagging actions
     (an existing install may still use the previous Scripts menu name)
     (Draft Captions is for export folders only).

  4. Start the local model server if it is not already running:
       $DEST/start-ollama.sh
     It only listens on 127.0.0.1. Nothing is exposed to the network.

  5. Open the control panel: http://127.0.0.1:8765/
     or the "Capture One Shoot Sorter" Dock shortcut (signs you in locally).
     The login token is stored at $DEST/gui/auth.token (never share it).
     Settings → Locations sets the NAS paths; the Training tab can queue
     a rank-training job (NAS read-only, never writes to Capture One).

  6. First tagging run also downloads the SigLIP image model into
     $DEST/models/hf (about 1–2 GB). Keep the Mac awake until it finishes.

  Optional: OCR translation uses qwen3:8b. Pull it with:
    OLLAMA_MODELS=$DEST/models $DEST/ollama/ollama pull qwen3:8b
  or re-run: ./install.sh --with-translation-model

Quick smoke test:
  $DEST/start-ollama.sh
  $DEST/tag --help

See INSTALL.md for updating, uninstalling, and Intel Mac notes.
EOF
