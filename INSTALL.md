# Install Capture One Shoot Sorter

On-device photo tagging for Capture One. It adds keywords only. It never changes your stars or colour tags, and it never sends your photos to the cloud.

## Quick start

You need an **Apple-silicon Mac (M1 or newer)**, **macOS 14 or newer**, **Capture One 16.8.x** (tested with 16.8.5.30), and about **15 GB** of free disk the first time.

1. Download the latest release zip from the GitHub Releases page of this private repository and unzip it (or clone the repo).
2. Open **Terminal**, go into the unzipped folder, and run:

```bash
cd /path/to/capture-one-shoot-sorter-1.1.0
./install.sh
```

The first run can take 10–20 minutes, mostly downloading Python packages and the local vision model.

The installer is safe to re-run. It will not overwrite an existing `config.json` or the control-panel login token, and it will not restart a control panel that is already running.

New installs go in `~/shoot-sorter-c1` and use the LaunchAgent `com.shootsorter.c1.gui`. If `~/localai` already exists, the installer keeps using that folder and the existing LaunchAgent so a live install is not moved or restarted. `LOCALAI_HOME` still overrides the path.

3. Follow the “Next steps” the script prints (permissions, keyword library, paths). Then open the **Capture One Shoot Sorter** Dock shortcut, or go to http://127.0.0.1:8765/.

## What the installer does

It puts everything in `~/shoot-sorter-c1` (or `~/localai` if that folder already exists, or `$LOCALAI_HOME` if you set that):

- A Python 3.12 virtualenv and the pinned packages
- A portable ExifTool and a standalone Ollama (listens on `127.0.0.1` only)
- The `qwen2.5vl:3b` caption / VLM weights, plus optional ranking and face-grouping weights
- `config.json` from the example, **only if you do not already have one**
- A new login token at `~/shoot-sorter-c1/gui/auth.token` with mode `600`, **only if missing**. The token is never printed
- The Capture One **Scripts → Capture One Shoot Sorter** menu items (an existing install keeps its current Scripts menu folder)
- The `com.shootsorter.c1.gui` Login item (LaunchAgent), loaded only if it is not already running. An existing LaunchAgent from an earlier install is left in place
- The **Capture One Shoot Sorter** app in `~/Applications` and a Dock shortcut

Homebrew is optional. If it is already installed, the script can use it as a fallback for ExifTool when the direct download fails. Apple’s Command Line Tools (`xcode-select --install`) are useful for compiling the Scripts menu items.

Useful flags:

```bash
./install.sh --dry-run                  # show the plan, change nothing
./install.sh --skip-models              # skip Ollama pulls and weight downloads
./install.sh --with-translation-model   # also pull qwen3:8b (~5 GB) for OCR translation
```

## Intel Macs

**Intel Macs are not supported.** The installer detects the architecture and stops with a clear error.

This is not a case of “it will just be slower”:

| Runtime | Used for | Intel? |
|---|---|---|
| **PyTorch 2.15 on MPS, with CPU fallback** | SigLIP tagging, scene tags, aesthetic ranking | The code would fall back to CPU, but **PyTorch 2.15 only publishes macOS ARM wheels**, so the environment cannot be installed |
| **Ollama** (llama.cpp / Metal, not MLX) | Caption model (`qwen2.5vl:3b`) and optional OCR translation (`qwen3:8b`) | The macOS binary is universal, but it is unused without the Python side |
| **Apple Vision + Core ML** | OCR, sharpness subject boxes, face detection; ArcFace embeddings use ONNX Runtime (Core ML then CPU) | These APIs exist on Intel, but again the rest of the stack will not install |
| **MLX** | — | Not used |

There is no supported Intel install path with the current requirements. Use an Apple-silicon Mac.

## Permissions

In **System Settings → Privacy & Security**, grant the following after the first Capture One or control-panel action prompts you (or add them by hand):

- **Automation** — allow Terminal, `osascript` and `~/shoot-sorter-c1/venv/bin/python` to control **Capture One**. Keyword applies will fail without this.
- **Full Disk Access** — give **Capture One**, Terminal and `~/shoot-sorter-c1/venv/bin/python` access if your originals live on a NAS or an external disk.

The control panel LaunchAgent is a Python process under your account. If a job cannot see a mounted volume, add that same Python path under Full Disk Access.

## NAS mounts and paths

The control panel’s health strip looks for your Camera Imports and Camera Exports folders.

1. Mount the volume that holds those folders (they stay off this Mac).
2. In the control panel open **Settings → Locations** and set the Camera Imports / Camera Exports (and optional Photos root and Other Exports) folders, or edit `~/shoot-sorter-c1/config.json`:


```json
"photos_root": "/Volumes/your-volume",
"imports_root": "/Volumes/your-volume/Camera Imports",
"exports_root": "/Volumes/your-volume/Camera Exports",
"other_exports_root": "/Volumes/your-volume/2025 Exports",
"schedules_root": "/Volumes/your-volume/Schedules",
"keyword_library": "Trip Keywords"
```

The Capture One library name is configurable (`keyword_library`). New installs use **Trip Keywords**. If your catalog already has a library under a different name, set `keyword_library` in `config.json` to that name instead of renaming the library.


Do not commit `config.json`; it is local to the machine. You can also set these later in **Settings → Locations** (Browse lists folders in the page so it works from another Mac, or paste a path. Optional “Open on M5 screen” opens a Finder dialog on the Mac running the panel without hanging this page, and does not change the path unless you pick a folder). A timestamped `config.json.bak-locations-*` backup is written on save. Use the **Training data** toggles on Camera Exports, Other Exports and Camera Imports to mark which folders train the personal ranking model (read only, never modified). **Schedules root** points at the Schedules tree (Year / YYYY-MM-DD / Event name). It defaults to Photos root/Schedules. A folder that holds a single image is read on the Schedule review page (Apple Vision OCR); the result stays on this Mac until you Confirm, and confirmed.json is the master. Posters are never moved, renamed or trashed. Any file named `_schedule*` is skipped by every pass, so schedule posters never get tagged, ranked or put in cull albums. Environment variables `LOCALAI_PHOTOS`, `LOCALAI_IMPORTS`, `LOCALAI_EXPORTS`, `LOCALAI_OTHER_EXPORTS` and `LOCALAI_SCHEDULES` still override config if set.

## First run

1. Start the local model server if it is not already running:

   ```bash
   ~/shoot-sorter-c1/start-ollama.sh
   ```

2. Open Capture One 16.8.x. In the **Keyword Library** tool, create a library named exactly **Trip Keywords** and import `~/shoot-sorter-c1/keywords/trip-keyword-library.txt`.

3. Confirm **Scripts → Capture One Shoot Sorter** lists the actions (Tag with Local AI, Find Bursts, Rank Selection, Check Sharpness, Read Text in Frame, Tag by Schedule, and Draft Captions for export folders only).

4. Open http://127.0.0.1:8765/ or the Dock app. Sign-in uses the local token file; it is never sent anywhere. The **Training** tab can queue a rank-training job (export roots, optional index-missing, auto-adopt only if held-out accuracy improves). Training never writes to Capture One, stars, colours or rank keywords.

5. Select 3–5 frames and run **Scripts → Capture One Shoot Sorter → Tag with Local AI**. Check that keywords appeared and that stars and colour tags did not change.

The first tagging run also downloads the SigLIP image model into `~/shoot-sorter-c1/models/hf` (about 1–2 GB). Keep the Mac awake until it finishes.

Catalog and Scripts-menu jobs never load the caption model. Draft Captions is only for export folders.

## Updating

Download a newer release (or `git pull` in a clone), then run `./install.sh` again. Your `config.json`, token, models, logs and job history are left in place.

If the control panel is already running, the installer will not restart it. To pick up GUI code after a job has finished:

```bash
~/shoot-sorter-c1/gui/reload_when_idle.py
```

Do not unload or restart the GUI or job runner while a job is running or queued.

## Uninstalling

This removes the tool only. It does not delete your photos, Capture One catalog, or keyword library inside Capture One.

```bash
# Stop the control panel first if a job is running, then:
./install.sh --uninstall
```

This unloads the LaunchAgent, removes the Scripts menu and Dock app, and deletes the runtime folder (`~/shoot-sorter-c1`, or `~/localai` if that is the folder in use). Photos and the Capture One catalog are not touched. You can then remove the **Capture One Shoot Sorter** tile from the Dock if it is still there.

## Options and layout

Runtime lives in `~/shoot-sorter-c1/` (or `~/localai/` on an existing install). The source, this guide and `install.sh` stay in the folder you unzipped (or in a git clone). See the main [README](README.md) for commands, passes, safety notes and the control-panel tabs.

The installer never prints or copies the GUI token. Notifications (Telegram or email) are configured in the untracked file `~/shoot-sorter-c1/gui/notify.json`; start from `gui/notify.example.json` and leave tokens out of git.
