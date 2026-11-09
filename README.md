# Trip Local AI for Capture One

On-device photo tagging for Capture One on Apple silicon. Adds keywords only. It never changes your stars or colour tags, and it never sends your photos to the cloud.

---

## Simple setup guide

You need:

- An Apple-silicon Mac (M1 or newer)
- macOS 14 or newer
- Capture One 16.8 or newer
- About 15 GB free disk space the first time (models + cache)

### 1. Get the code

Clone this private repository (or unpack the tarball) somewhere convenient, for example your Desktop.

### 2. Run the installer

Open **Terminal** and run:

```bash
cd /path/to/this-repo
./install.sh
```

That puts everything in `~/localai`, creates a Python 3.12 environment, installs the packages, downloads a local copy of Ollama, pulls the small vision model, and copies the Scripts menu into Capture One.

The first run can take 10–20 minutes depending on your internet connection.

### 3. Start the local model server (if it is not already running)

```bash
~/localai/start-ollama.sh
```

It only listens on your Mac (`127.0.0.1`). Nothing is exposed to the network.

### 4. Import the keyword library in Capture One

1. Open Capture One.
2. Open the **Keyword Library** tool.
3. Create a library named exactly **Trip Keywords** (or import into one with that name).
4. Import `~/localai/keywords/trip-keyword-library.txt` (tab-indented list).

The Scripts menu actions look keywords up in this library, so the name must match.

### 5. Find the Scripts menu

In Capture One: **Scripts → Trip Local AI**. You should see:

- Tag with Local AI
- Find Bursts
- Rank Selection
- Check Sharpness
- Read Text in Frame
- Tag by Schedule
- Draft Captions (Exports) — export folders only

### 6. First test on a few frames

1. Select 3–5 images in Capture One.
2. Run **Scripts → Trip Local AI → Tag with Local AI**.
3. Wait for the macOS notification.
4. Check that keywords appeared and that stars / colour tags did not change.

The first tagging run also downloads the SigLIP image model into `~/localai/models/hf` (about 1–2 GB). Keep the Mac awake until it finishes.

### 7. Run a whole shoot from Terminal

Dry run first (writes proposals only, does not touch Capture One):

```bash
~/localai/tag "/path/to/your/shoot/folder" \
  --profile motorsport \
  --context "Britcar Into The Night, Brands Hatch" \
  --no-caption
```

Profiles: `motorsport`, `gig`, or `kyoto`.

When you are happy, apply confident keywords:

```bash
~/localai/tag "/path/to/your/shoot/folder" \
  --profile motorsport \
  --context "Britcar Into The Night, Brands Hatch" \
  --no-caption \
  --apply
```

Or run the full pipeline (tag → index → sharpness / bursts / rank → OCR → apply), still **without** draft captions:

```bash
~/localai/venv/bin/python ~/localai/fullrun.py \
  "/path/to/your/shoot/folder" \
  --profile motorsport \
  --context "Britcar Into The Night, Brands Hatch"
```

Use `--no-apply` on `fullrun.py` if you only want the proposal files.

---

---

## Control panel (web GUI)

A small local web app on the Mac lets you queue and reorder passes without using Terminal. It is meant for the headless M5 (Screen Sharing), and it also works from any device on your LAN.

### Start it

```bash
# one-time: install the LaunchAgent (survives reboots)
cp ~/localai/gui/com.trips.localai.gui.plist ~/Library/LaunchAgents/ 2>/dev/null ||   cp gui/com.trips.localai.gui.plist ~/Library/LaunchAgents/
# if you installed via install.sh, the plist already points at ~/localai
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.trips.localai.gui.plist
```

Or run in the foreground:

```bash
~/localai/venv/bin/python ~/localai/gui/server.py
```

Open:

- `http://127.0.0.1:8765/` on the Mac
- `http://127.0.0.1:8765/` or `http://<LAN-IP>:8765/` from another device on the network

Log in with the shared secret in `~/localai/gui/auth.token` (one line). The installer creates a random token; change it if you like.

Optional Dock shortcut:

```bash
~/localai/gui/macos/install-dock-app.sh
# or from this repo after install:
./gui/macos/install-dock-app.sh
```

### What the page does

- Pick a year / folder under `Camera Imports`, a profile (`motorsport` / `gig` / `kyoto`), and optional context text.
- Tick passes and drag (or use arrows) to order them: Tag with Local AI, Index, Sharpness, Bursts, Rank, Read Text in Frame, Tag by Schedule.
- **Apply keywords after each pass** is on by default (tag → apply → next pass).
- Caption model is never used for catalog jobs (`--no-caption` always).
- Optional: run the text pass only on motorsport frames still missing a car number.
- Rank can run for ordering CSVs; Score keywords stay off unless you tick “Apply Rank score keywords”.
- Queue: add several jobs, pause next, cancel, watch live progress and a log tail.
- Separate **Exports · Draft Captions** panel — only folders under `Camera Exports`.

Safety: additive keywords only; stars and colour tags are never written. Full jobs snapshot ratings/colours before and after (sample jobs skip the snapshot so they do not stall Capture One).


## What each Scripts menu action does

| Script | What it does |
|---|---|
| **Tag with Local AI** | Proposes scene / colour / number keywords for the selection and adds the confident ones. |
| **Find Bursts** | Groups near-duplicate frames and marks the best frame in each burst. |
| **Rank Selection** | Scores the selection and adds a quality-score keyword band. |
| **Check Sharpness** | Flags sharp, soft-focus and motion-blurred frames. |
| **Read Text in Frame** | Reads text in the frame (including Japanese) and, when empty, writes a short Description. |
| **Tag by Schedule** | Matches capture time to a gig schedule and adds Event / Venue / Act / Cheki keywords. |
| **Draft Captions (Exports)** | Writes draft captions for **export** folders only (for Salamander). It refuses camera originals. Keep this separate from catalog tagging. |

None of these change stars or colour tags. Keyword writes are additive only.

---

## Safety notes

- **Additive keywords only.** Existing keywords are never removed.
- **Stars and colour tags are never touched.**
- **Dry run by default** for Terminal tagging. Pass `--apply` only when you want Capture One updated.
- **Originals are read-only.** Previews are cached under `~/localai/cache/`; nothing is written next to your RAW files.
- **Everything stays on the Mac.** Ollama listens on localhost only. No cloud vision APIs.

---

## `--no-caption` (important for catalog runs)

Catalog / Scripts-menu tagging does **not** load the caption model. Terminal tagging should usually include `--no-caption` so only SigLIP labels and Apple Vision OCR run (about 0.5 s per image instead of about 3 s, and much less memory).

Draft captions are a separate path:

```bash
~/localai/tag captions --files /path/to/Camera\ Exports/image.jpg
```

or **Scripts → Trip Local AI → Draft Captions (Exports)** with export files selected.

---

## Layout

```
~/localai/
  tag                 wrapper command
  tagger.py           main tagger
  features.py         bursts, rank, sharpness, OCR, search, faces, captions, …
  fullrun.py          whole-shoot pipeline (no captions)
  schedule_tag.py     gig schedule matcher
  apply_c1.py/.js     additive Capture One writer
  config.json         local settings (from config.example.json)
  keywords/           Trip Keywords library source
  schedules/          confirmed-show schedules (JSON)
  scripts/src/        AppleScript sources for the Scripts menu
  venv/               Python 3.12 environment
  ollama/             standalone Ollama binary
  models/             Ollama + Hugging Face + optional weights
  cache/ index/ out/ logs/   working data (not in git)
```

---

## Config

Copy `config.example.json` to `config.json` (the installer does this). Useful keys:

- `thresholds` — confidence cut-offs for auto-tagging
- `script_profile_default` — profile used by the Scripts menu when the folder name does not match a pattern (`gig` by default)
- `profile_by_folder` — regex → profile map
- `script_context` — optional per-folder context strings for the Scripts menu
- `camera_utc_offset` — set if the camera clock is not in the schedule timezone (for example `"+00:00"` when shooting Japan on London time)

---

## Keyword library

Rebuild the tab-indented list and CSV:

```bash
~/localai/venv/bin/python ~/localai/keywords/build_library.py
```

Then re-import `trip-keyword-library.txt` into the **Trip Keywords** library in Capture One. The builder includes motorsport, gig, Kyoto, Japan, aviation, wildlife, static vehicles, film, general quality flags, and the confirmed Japan November 2026 event / venue / act names.

---

## Schedule tagging

Confirmed shows only live in `schedules/example.json`. Tag a folder:

```bash
~/localai/tag schedule "/path/to/japan/shoot" \
  --schedule ~/localai/schedules/example.json
```

Unit tests (no photos needed):

```bash
~/localai/venv/bin/python ~/localai/tests_schedule.py
```

---

## Troubleshooting

**Scripts menu is empty**  
Capture One only reads `~/Library/Scripts/Capture One Scripts/`. Re-run `./install.sh`, then restart Capture One.

**“keyword library not found”**  
The library must be named exactly `Trip Keywords`. Import `~/localai/keywords/trip-keyword-library.txt`.

**Nothing happens / notification says failed**  
Check the latest log in `~/localai/logs/scripts/`. Also confirm Ollama is up: `curl -s http://127.0.0.1:11434/api/version`.

**Mac feels memory-tight**  
Make sure catalog runs use `--no-caption` (Scripts menu already does). Quit other heavy apps on 16 GB machines during a full shoot.

**Variant not in current collection**  
Apply only runs against Capture One’s *current* collection. Select the album / folder that contains the frames before applying.

**First run is slow**  
SigLIP / CLIP weights download on first use into `~/localai/models/hf`. Later runs reuse them.

**Stop Ollama**

```bash
kill "$(cat ~/localai/logs/ollama.pid)"
```

**Remove everything**

```bash
rm -rf ~/localai
rm -rf "$HOME/Library/Scripts/Capture One Scripts/Trip Local AI"
```

---

## Licence / privacy

Private repository. Photos, proposals, caches and logs are excluded from git on purpose — do not commit them.
