# Install Trips Local AI for Lightroom

On-device photo tagging for **Lightroom Classic**. It writes keywords into XMP sidecars only. It never changes your stars, colour labels or develop settings, and it never sends your photos to the cloud.

This edition is a separate application from the Capture One version. It installs into `~/localai-lr`, listens on port **8766**, and uses the LaunchAgent `com.trips.localai-lr.gui`. It does not touch `~/localai`, the Capture One Scripts menu, or the Capture One control panel.

## Quick start

You need an **Apple-silicon Mac (M1 or newer)**, **macOS 14 or newer**, **Lightroom Classic**, and about **15 GB** of free disk the first time. Intel Macs are not supported (the installer will say why and stop).

1. Check out the `lightroom` branch, or unzip a copy of this tree.
2. Open **Terminal**, go into the folder, and run:

```bash
cd /path/to/trips-local-ai
./install.sh
```

The first run can take 10–20 minutes, mostly downloading Python packages and the local vision model.

The installer is safe to re-run. It will not overwrite an existing `~/localai-lr/config.json` or the control-panel login token, and it will not restart a control panel that is already running.

3. Follow the “Next steps” the script prints (permissions, folders, Lightroom settings). Then open the **Trips Local AI for Lightroom** Dock shortcut, or go to http://127.0.0.1:8766/.

## What the installer does

It puts everything in `~/localai-lr` (or `$LOCALAI_HOME` if you set that):

- A Python 3.12 virtualenv and the pinned packages
- A portable ExifTool and a standalone Ollama (listens on `127.0.0.1` only)
- The `qwen2.5vl:3b` caption / VLM weights, plus optional ranking and face-grouping weights
- `config.json` from the example, **only if you do not already have one**
- A new login token at `~/localai-lr/gui/auth.token` with mode `600`, **only if missing**. The token is never printed
- The `com.trips.localai-lr.gui` Login item (LaunchAgent), loaded only if it is not already running
- The **Trips Local AI for Lightroom** app in `~/Applications` and a Dock shortcut

It does **not** install a Lightroom or Capture One plug-in, and it does not write to a catalogue. Homebrew is optional.

Useful flags:

```bash
./install.sh --dry-run                  # show the plan, change nothing
./install.sh --skip-models              # skip Ollama pulls and weight downloads
./install.sh --with-translation-model   # also pull qwen3:8b (~5 GB) for OCR translation
```

## Intel Macs

**Intel Macs are not supported.** The installer detects the architecture and stops. PyTorch 2.15 only publishes macOS ARM wheels, so the environment cannot be installed on Intel. Use an Apple-silicon Mac.

## Permissions

In **System Settings → Privacy & Security**, grant **Full Disk Access** to Terminal and `~/localai-lr/venv/bin/python` if your originals live on a NAS or an external disk. The control panel LaunchAgent is a Python process under your account. If a job cannot see a mounted volume, add that same Python path under Full Disk Access.

This edition does not need Automation permission for Lightroom. Sidecars are written while Lightroom can be closed.

## Lightroom setup (once)

1. Optionally import `~/localai-lr/keywords/trip-keyword-library.txt` with **Metadata → Import Keywords** so the keyword hierarchy already exists in the catalogue. The XMP writer does not require this; Lightroom will create keywords when it reads the sidecars.
2. Keep **Automatically write changes into XMP** **off** for folders this tool will tag, until you have read the new sidecars in. If Lightroom writes its own XMP first, it can overwrite keywords that have not been read yet.
3. After a job finishes, select the folder in Lightroom and choose **Metadata → Read Metadata from Files**. You should see a “metadata changed on disk” badge beforehand. Check that keywords appeared and that stars, labels and develop settings did not change.

### Smart collections for the cull

Lightroom cannot receive smart collections from outside, so the cull pass writes keywords you can filter on:

| Keyword | Meaning |
|---|---|
| `Cull | Sharp + Burst Best` | Sharp **and** Burst Best, and not Motion Blur or Soft Focus |
| `Cull | Rank Best` | Rank Best, and not blur |
| `Cull | Shortlist` | Either of the above |

In Lightroom, create three smart collections (Library Filter or a collection set) whose keyword rule is “contains words” / “contains” the matching `Cull` keyword. Re-running a job is safe: the same keywords are not added twice.

## NAS mounts and paths

1. Mount the volume that holds your photo folders.
2. In the control panel open **Settings → Locations** and set Camera Imports, Camera Exports, optional Photos root, Other Exports and Schedules, or edit `~/localai-lr/config.json`.

Defaults in the example config use generic `/Volumes/your-nas/...` paths. Auto-queue looks under Camera Imports for `YYYY-MM-DD…` folders and queues a job once the folder has **stopped changing**. There is no Lightroom import check; Lightroom does not need to be open.

## After a job: read the sidecars

The tool writes `<stem>.xmp` next to each image. Image bytes are never modified. For classic RAW files (CR3, ARW, NEF, RAF and so on) Lightroom reads that sidecar. For JPEG, TIFF, DNG and HEIC, Lightroom usually prefers metadata stored inside the file; this edition still writes a sidecar (it does not embed) and reports those files. Use **Read Metadata from Files** to pick the keywords up. If a sidecar already existed, a `.xmp.bak` copy is kept (`xmp_backup` in `config.json`, on by default).

Undo, from the History tab, removes only the keywords that job added.

## Updating

Pull or copy a newer tree, then re-run `./install.sh`. Existing config, token, models and a running panel are left alone. To load new panel code once the queue is idle:

```bash
~/localai-lr/venv/bin/python ~/localai-lr/gui/reload_when_idle.py
```

That command only restarts `com.trips.localai-lr.gui`. It will not touch a Capture One panel running under a different label.

## Uninstalling

```bash
./install.sh --uninstall
```

This removes `com.trips.localai-lr.gui`, `~/Applications/Trips Local AI for Lightroom.app` and `~/localai-lr`. Photos, Lightroom catalogues, XMP sidecars already written next to your images, and any Capture One install in `~/localai` are left alone. You can delete the Dock tile if it is still there.

## Troubleshooting

**Keywords are in the sidecar but not in Lightroom**  
Select the folder and use **Metadata → Read Metadata from Files**. Confirm “Automatically write changes into XMP” was off until after that read.

**Stars or a colour label look wrong**  
This tool never writes `xmp:Rating`, `xmp:Label` or Camera Raw develop settings. If something drifted, compare the current sidecar to the `.xmp.bak` next to it, or use Lightroom’s own metadata conflict badge.

**JPEG / DNG keywords did not appear**  
Those formats normally store metadata inside the file. The sidecar was still written; use Read Metadata from Files. Embedding into the image is not implemented.

**Panel will not start / wrong port**  
This edition listens on http://127.0.0.1:8766/. The Capture One edition, if installed, stays on 8765.

**First run is slow**  
SigLIP weights download on first use into `~/localai-lr/models/hf`. Later runs reuse them.
