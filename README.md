# Trips Local AI for Lightroom

On-device photo tagging for Lightroom Classic on Apple silicon. Adds keywords only, as XMP sidecars. It never changes your stars, colour labels or develop settings, and it never sends your photos to the cloud.

This is a separate application from the Capture One edition. Runtime, port and LaunchAgent are all different (`~/localai-lr`, port 8766, `com.trips.localai-lr.gui`), so both can be installed on the same Mac without overwriting each other.

---

## Setup

See **[INSTALL-LR.md](INSTALL-LR.md)** for the install guide: run `./install.sh`, grant disk access, then set Lightroom to read the sidecars.

You need an Apple-silicon Mac (M1 or newer), macOS 14 or newer, Lightroom Classic, and about 15 GB free the first time. Intel Macs are not supported (the installer will say why and stop).

```bash
cd /path/to/trips-local-ai-1.0.0
./install.sh
```

### First test on a few frames

1. Start the local model server if needed: `~/localai-lr/start-ollama.sh` (localhost only).
2. Optionally import `~/localai-lr/keywords/trip-keyword-library.txt` into Lightroom with **Metadata → Import Keywords**.
3. In the control panel, queue a small sample job (Sample = 3–5) on a test folder, with Apply after each pass on.
4. In Lightroom, select that folder and choose **Metadata → Read Metadata from Files**. Check that keywords appeared and that stars, colour labels and develop settings did not change. Keep **Automatically write changes into XMP** off until after that read.

The first tagging run also downloads the SigLIP image model into `~/localai-lr/models/hf` (about 1–2 GB). Keep the Mac awake until it finishes.

### Run a whole shoot from Terminal

Dry run first (writes proposals only, does not write sidecars):

```bash
~/localai-lr/tag "/path/to/your/shoot/folder" \
  --profile motorsport \
  --context "Britcar Into The Night, Brands Hatch" \
  --no-caption
```

Profiles: `motorsport`, `gig`, or `street` (`kyoto` is kept as an alias of `street`).

When you are happy, apply confident keywords to XMP sidecars:

```bash
~/localai-lr/tag "/path/to/your/shoot/folder" \
  --profile motorsport \
  --context "Britcar Into The Night, Brands Hatch" \
  --no-caption \
  --apply
```

Or run the full pipeline (tag → index → sharpness / bursts / rank → OCR → apply), still **without** draft captions:

```bash
~/localai-lr/venv/bin/python ~/localai-lr/fullrun.py \
  "/path/to/your/shoot/folder" \
  --profile motorsport \
  --context "Britcar Into The Night, Brands Hatch"
```

Use `--no-apply` on `fullrun.py` if you only want the proposal files.

---

---


## Control panel (Trips Local AI for Lightroom)

Local web UI: **http://127.0.0.1:8766/**.

LaunchAgent: `com.trips.localai-lr.gui`. Dock app: **Trips Local AI for Lightroom** (opens already signed in via `?token=` from `~/localai-lr/gui/auth.token`).

### Tabs

- **Run** — pick a **Profile** (or custom passes), folder, queue jobs with live progress. Apply-after-each-pass is default. Caption model is never used for catalog jobs.
- **Schedule importer** — load/paste JSON or CSV, preview events/slots/TBA/overlaps, dry-run EXIF mapping, queue additive schedule tagging (confirmed only by default).
- **History & undo** — past runs with keyword counts; preview then remove **only** keywords that run added (never stars/colours).
- **Training** — queue a rank-training job on folders marked as training data in Settings → Locations (optional index-missing, auto-adopt only if held-out accuracy improves). Shows leave-one-shoot-out AUC before/after, per-shoot results and label counts. Adopt and Roll back update `config.json` only, after a confirmation. Never writes sidecars, stars, colour labels or rank keywords.
- **Profiles** — create/edit from dropdowns (shoot type, pass bundle, camera TZ, buffer, keyword set, linked schedule, sharpness strictness, notifications). Shoot type auto-fills the rest. Supersedes the old Presets list. Stored in `~/localai-lr/profiles/`.
- **Settings** — **Locations** (Camera Imports, Camera Exports, Other Exports and the Photos root; Browse lists folders in the page so it works from another Mac (optional “Open on M5 screen” shows a Finder dialog on the Mac running the panel, without hanging this page); or paste a path; save writes `config.json` after a timestamped backup, without a restart or a library scan) and notification toggles. Telegram needs `bot_token` + `chat_id` in untracked `~/localai-lr/gui/notify.json` (see `notify.example.json`). The inbound Telegram bridge cannot send alerts. Email uses local sendmail when enabled (postfix is loopback-only unless a relay is configured).
- **Exports** — Draft Captions only for Camera Exports folders.

### Health strip

CPU, memory, NAS mount, free disk (M5 + NAS), Lightroom running (informational; writes do not need it open), Ollama/caption-model status, thermal/pressure, queue pause, notify on/off — refreshes every few seconds.

### Profiles (examples)

Seeded under `profiles/`: Gig standard, Idol gig, Motorsport full, Street photography, Schedule tags only, Exports only (Draft Captions). Catalog profiles must never include captions.


## What each pass does

| Pass | What it does |
|---|---|
| **Tag with Local AI** | Proposes scene / colour / number keywords for the selection and adds the confident ones. |
| **Find Bursts** | Groups near-duplicate frames and marks the best frame in each burst. |
| **Rank Selection** | Scores the selection and adds a quality-score keyword band. |
| **Check Sharpness** | Flags sharp, soft-focus and motion-blurred frames. |
| **Read Text in Frame** | Reads text in the frame (including Japanese). Captions and descriptions stay export-only and are not written into sidecars. |
| **Tag by Schedule** | Matches capture time to a gig schedule and adds Event / Venue / Act / Cheki keywords. |
| **Draft Captions (Exports)** | Writes draft captions for **export** folders only (for Salamander). It refuses camera originals. Keep this separate from catalog tagging. |

None of these change stars or colour tags. Keyword writes are additive only.

---

## Safety notes

- **Additive keywords only.** Existing keywords are never removed.
- **Stars and colour tags are never touched.**
- **Dry run by default** for Terminal tagging. Pass `--apply` only when you want XMP sidecars updated.
- **Image bytes are never modified.** Keywords are merged into `<stem>.xmp` next to each file. A `.xmp.bak` of an existing sidecar is kept by default. Previews are cached under `~/localai-lr/cache/`.
- **Next step in Lightroom:** select the folder, choose **Metadata → Read Metadata from Files**, and keep “Automatically write changes into XMP” off until the keywords have been read in.
- **Everything stays on the Mac.** Ollama listens on localhost only. No cloud vision APIs.

---

## `--no-caption` (important for catalog runs)

Catalog tagging (the control panel and `fullrun.py`) does **not** load the caption model. Terminal tagging should usually include `--no-caption` so only SigLIP labels and Apple Vision OCR run (about 0.5 s per image instead of about 3 s, and much less memory).

Draft captions are a separate path:

```bash
~/localai-lr/tag captions --files /path/to/Camera\ Exports/image.jpg
```

or the Exports tab in the control panel, with an export folder selected.

---

## Layout

```
~/localai-lr/
  tag                 wrapper command
  tagger.py           main tagger
  features.py         bursts, rank, sharpness, OCR, search, faces, scene, camera, captions, …
  fullrun.py          whole-shoot pipeline (no captions)
  schedule_tag.py     gig schedule matcher
  apply_xmp.py        additive Lightroom XMP sidecar writer
  config.json         local settings (from config.example.json)
  keywords/           keyword list (optional import into Lightroom)
  schedules/          start from `example.json` (copy and fill in confirmed shows)
  venv/               Python 3.12 environment
  ollama/             standalone Ollama binary
  models/             Ollama + Hugging Face + optional weights
  cache/ index/ out/ logs/   working data (not in git)
```

---

## Config

Copy `config.example.json` to `config.json` (the installer does this). Useful keys:

- `thresholds` — confidence cut-offs for auto-tagging
- `script_profile_default` — profile used when the folder name does not match a pattern (`gig` by default)
- `profile_by_folder` — regex → profile map
- `script_context` — optional per-folder context strings
- `camera_utc_offset` — set if the camera clock is not in the schedule timezone (for example `"+00:00"` when the camera stays on home time)
- `photos_root` / `imports_root` / `exports_root` / `other_exports_root` — NAS photo-folder roots. The control panel **Settings → Locations** section can set these (Browse lists folders in the page, or paste a path). Environment variables `LOCALAI_PHOTOS`, `LOCALAI_IMPORTS`, `LOCALAI_EXPORTS` and `LOCALAI_OTHER_EXPORTS` override config if set. The older `exports_2025_root` key and `LOCALAI_EXPORTS_2025` environment variable are still honoured as fallbacks. The default Other Exports folder on the NAS remains `2025 Exports` (the folder itself is not renamed).
- `training.sources` — which of those folders are marked as **Training data** for the personal ranking model (`exports_root` and `other_exports_root` default on; `imports_root` is also on by default so 3★+ star labels stay included). Marked folders are read only and are never modified. The Training tab uses only the marked sources.
- `xmp_backup` — when true (the default), a `.xmp.bak` copy of an existing sidecar is written before it is updated.

---

## Keyword library

Rebuild the tab-indented list and CSV:

```bash
~/localai-lr/venv/bin/python ~/localai-lr/keywords/build_library.py
```

Then optionally re-import `trip-keyword-library.txt` into Lightroom with **Metadata → Import Keywords**. The builder includes motorsport, gig, street photography, aviation, wildlife, static vehicles, film, general quality flags, Focus and Rank, generalised `General/Scene/*` keywords, `General/Camera/*` (lens, focal band, exposure flags, AF, IS) plus clip flags under `General/Quality`. New names can also be imported from a tab-indented `keywords/delta-*.txt` without replacing the rest of the library.

---

## Schedule tagging

Copy `schedules/example.json` and fill in confirmed shows only (times, venues, acts). The control panel Schedule importer can also load or paste a schedule. Tag a folder:

```bash
~/localai-lr/tag schedule "/path/to/shoot" \
  --schedule ~/localai-lr/schedules/example.json
```

Unit tests (no photos needed):

```bash
~/localai-lr/venv/bin/python ~/localai-lr/tests_schedule.py
```

---

## Troubleshooting

**Keywords are in the sidecar but not in Lightroom**  
Select the folder and choose **Metadata → Read Metadata from Files**. Keep “Automatically write changes into XMP” off until after that read.

**Nothing happens / a job says failed**  
Check the latest log in `~/localai-lr/logs/gui/`. Also confirm Ollama is up: `curl -s http://127.0.0.1:11434/api/version`.

**Mac feels memory-tight**  
Make sure catalog runs use `--no-caption` (the control panel already does). Quit other heavy apps on 16 GB machines during a full shoot.

**JPEG, TIFF, DNG or HEIC keywords did not appear**  
Those formats normally store metadata inside the file. This edition writes a sidecar and does not embed. Use Read Metadata from Files; if Lightroom still ignores the sidecar, that format is a known gap.

**First run is slow**  
SigLIP / CLIP weights download on first use into `~/localai-lr/models/hf`. Later runs reuse them.

**Stop Ollama**

```bash
kill "$(cat ~/localai-lr/logs/ollama.pid)"
```

**Remove everything**

See [INSTALL-LR.md](INSTALL-LR.md#uninstalling) for the full uninstall (LaunchAgent, Dock app, runtime). A short version:

```bash
./install.sh --uninstall
```

This does not remove a Capture One install in `~/localai`, nor sidecars already written next to your images.

---

## Licence / privacy

Private repository. Photos, proposals, caches and logs are excluded from git on purpose — do not commit them.

---

## Reference: commands, passes and panel features

Proposes keywords and one-line draft captions for a shoot folder, fully on-device.
Originals are opened for reading (SHA-256 + embedded preview). Previews are cached in `~/localai-lr/cache/<sha256-prefix>.jpg`.
Keywords are written only through the additive XMP path (see below). Sidecars are merged; ratings, colour labels and develop settings are never touched. Image bytes are never modified. Captions stay export-only and are not written into sidecars.

### Run
```
~/localai-lr/tag "/Volumes/Photos/Camera Imports/2026/<folder>" --profile motorsport|gig|street \
    [--sample 40] [--limit N] [--no-caption] [--context "Britcar Into The Night, Brands Hatch"] [--tag dryrun]
```
- top-level files only (nested export folders skipped); MP4/MOV ignored.
- `--sample N` spreads N frames evenly across the folder; omit for the whole folder.
- `--no-caption` skips the Ollama model (SigLIP labels and Apple OCR only, ~0.5 s/image).
- `tag` starts the local Ollama server (127.0.0.1 only) if needed and runs at `nice 10`.

Output: `~/localai-lr/out/<folder>/<profile>-proposals[-tag].json` and `.csv`
(rel_path, filename, sha256, proposed_keywords, tentative_keywords, needs_review + reasons,
car_colour, race_number + source, OCR candidates, label scores, caption, timings).

### Profiles and keywords
- motorsport: `Motorsport/Car Number/<n>`, `Motorsport/Car Colour/<Colour>`, `Motorsport/Night`.
  Number is proposed only when Apple Vision OCR and the VLM agree; a VLM-only, OCR-only or disagreeing
  read goes to `tentative_keywords` and needs_review.
- gig: `Gig/Crowd`, `Gig/Stage`, `Gig/Portrait`.
- street: `Street/Alley`, `Street/Crowd`, `Street/Pedestrian`, `Street/Shopfront`, `Street/Market`, `Street/Crossing`, `Street/Cyclist`, `Street/Temple`, `Street/Candid`, plus binaries such as `Street/Shrine Gate`, `Street/Night Street`, `Street/Rain`, `Street/Neon`, `Street/Signage`, `Street/Vending Machine`, `Street/Reflections`, `Street/Silhouette`, `Street/Umbrella`, `Street/Lantern`. (`kyoto` is an alias of this profile.)

### Models (all local)
- SigLIP ViT-B-16-SigLIP-256 (open_clip, PyTorch MPS): zero-shot scene/shot/place and car colour.
- Apple Vision `VNRecognizeTextRequest` (pyobjc): race-number OCR on the full-res preview and 6 overlapping tiles.
- Ollama `qwen2.5vl:3b`: caption, race-number read, colour, torii/night/rain flags.

### Applying to XMP sidecars (--apply)
After tagging, the tagger always writes a plan (dry run). With `--apply` it ADDS the confident keywords to `<stem>.xmp` next to each image (`apply_xmp.py`):
- only images with needs_review = false, only `proposed_keywords` (never tentative/VLM-only numbers);
- SHA-256 of each original re-checked against the proposal before applying;
- keywords stored as `dc:subject` (each path level) and `lr:hierarchicalSubject` (levels joined with `|`, so `General/Rank/Best` becomes `General|Rank|Best`);
- existing sidecars are merged; `xmp:Rating`, `xmp:Label`, `crs:*` develop settings and every other existing field are left alone;
- a `.xmp.bak` of an existing sidecar is written first (unless `xmp_backup` is false);
- writes are atomic; a repeat run adds no duplicates;
- JPEG, TIFF, DNG and HEIC are not embedded — a sidecar is written and the job reports that Lightroom must use **Read Metadata from Files**.
SHA-256 values are cached in `index/catalog.sqlite` (path_cache), keyed by path, size and mtime. A changed size or mtime forces a full re-hash (`shacache.py`).
Re-apply an existing proposals file: `venv/bin/python apply_xmp.py out/<folder>/<profile>-proposals.json [--apply]`.

### Cull keywords
The cull pass writes `Cull|Sharp + Burst Best`, `Cull|Rank Best` and `Cull|Shortlist` into the same sidecars (additive, idempotent). Build Lightroom smart collections on those keywords; see [INSTALL-LR.md](INSTALL-LR.md).

### Feature commands (`~/localai-lr/tag <cmd> …` → features.py)
Selection for most commands: a folder, `--files a.CR3 b.CR3`, `--sample N`, or `--start NAME --count N`.
| command | what it does | model | install | speed (M5) |
|---|---|---|---|---|
| `index <folder>` | Builds the index from the SigLIP embedding, LAION aesthetic score, subject sharpness, EXIF and capture time. It is incremental, keyed by SHA-256. Options: `--indexed --force` | SigLIP ViT-B-16-256 + CLIP ViT-L/14 + LAION v2 head | 1.5 GB + 3.2 GB + 4 MB | about 0.3–0.9 s/new image; first load about 9 s |
| `search "orange race car at night" [--folder F] [--top 20]` | Text search over the index, written to `out/search/*.csv` | SigLIP | (shared) | about 3.8 s/query incl. model load |
| `ocr <folder> [--translate] [--langs ja-JP,en-US] [--resume]` | Reads text in frame (en+ja) into `out/<folder>/ocr.json` and ocr-proposals (Description "Text in frame: … \|\| EN: …", lines with conf ≥ 0.5) | Apple Vision; qwen3:8b for translation | 0 + 5.2 GB | 0.4–0.6 s/image (+about 2–4 s per translated frame) |
| `bursts <folder>` | Finds near-duplicate runs (cos ≥ 0.88, anchor ≥ 0.82, gap ≤ 2 s) and picks a best frame (0.6 sharp + 0.4 aesthetic) as `General/Burst Best` | SigLIP embeddings | — | instant from index |
| `sharpness <folder>` | Measures subject-box (Vision saliency) tile Laplacian p90, normalised per folder, giving `General/Quality/{Sharp,Motion Blur,Soft Focus}` | OpenCV | — | instant from index |
| `rank <folder> [--top N] [--calibrate] [--no-keywords]` | Calibrated rank: 0.5·sharpness + 0.15·aesthetic + 0.3·burst-best − 0.1·other burst frames − 0.3·blur, tiered within the shoot. Top 10% `General/Rank/Best`, next 20% `General/Rank/Good` (on by default). `--calibrate` refits the weights against your own star ratings in the latest catalog snapshot. Writes `rank.csv`, `rank-topN.csv`, `rank-calibration.json` | LAION aesthetic v2 + index | — | instant from index |
| `journal <folder>` | Writes a markdown day summary to `out/<folder>/journal.md` | qwen3:8b (think off) | 5.2 GB | about 20 s |
| `ask "question" [--folder F]` | Answers from the index (search plus proposals and OCR context) and lists supporting frames | qwen3:8b | — | 9–12 s |
| `faces <folder>` | Groups faces anonymously (Person 001…) into `out/faces/faces.csv` with contact sheets | Apple Vision detect + ArcFace w600k_mbf (buffalo_s) + DBSCAN | 13.6 MB | about 0.3 s/image (12 gig frames in 4 s) |
| `scene <folder> [--sample N] [--sheets]` | Generalised multi-label scene/subject keywords under `General/Scene/*` (architecture, street, concert, motorsport, temple, food, …). Conservative per-label thresholds, cap of 6 labels per frame. Writes `scene-proposals.json` and optional contact sheets. No caption model. | SigLIP (index embeddings when present) | (shared) | about 0.01 s/image from index; first load a few seconds |
| `camera <folder> [--sample N] [--no-clip]` | Camera info from CR2/CR3 MakerNotes + a cheap preview clip check. Additive `General/Camera/*` keywords (lens, focal band, High ISO, Wide Open, Slow Shutter, High Speed drive, electronic shutter, AF, IS, orientation, Flash Fired) and `General/Quality/Clipped Highlights` / `Crushed Shadows`. Sub-second time, body serial, white balance, Picture Style and Highlight Tone Priority are stored in the proposals file, not keyworded. Fast; no caption model. | exiftool 13.61 + preview | (bundled) | EXIF about 0.1–0.2 s/image in batches; clip is cheap from the cached preview |
| `name-person 3 "label"` | Gives an anonymous group a local label (your own; nothing is looked up) | — | — | — |
| `schedule <folder> [--schedule F] [--camera-offset +09:00]` | Matches capture time to the gig schedule, writing `Gig/Event`, `Venue`, `Act`, `Cheki`, `Meet & Greet` and `Changeover` | exiftool 13.61 | ~10 MB (bin/) | one exiftool call per batch |
| `captions --files <exports>` | **Export-only.** Writes draft captions to `out/salamander/captions-<time>.json`. It refuses anything not under `Camera Exports/` | qwen2.5vl:3b | 3.2 GB | about 3 s/image |

Feature proposals are written to `out/<folder>/{sharpness,bursts,rank,ocr,schedule,scene,people,camera}-proposals.json`. They are apply_xmp-compatible: `venv/bin/python apply_xmp.py <file> [--apply]`.

Face grouping is anonymous and local. Nobody is identified, and no lookups or cloud calls are made. Delete `index/faces*` and the faces tables to forget the groups.

### Running passes from the panel or Terminal
There is no Lightroom plug-in and no Scripts menu in this edition. Queue jobs from the control panel, or run `~/localai-lr/tag` / `apply_xmp.py` from Terminal. Keywords are written to XMP sidecars; Lightroom picks them up with **Metadata → Read Metadata from Files**.

### Full-folder run (resumable)
`nohup nice -n 10 venv/bin/python fullrun.py "<folder>" --profile motorsport --context "…" > logs/fullrun/<name>.log &`
The steps are: rating and colour snapshot → tagger (`--resume`) → camera → scene → index → sharpness → bursts → rank → OCR (`--resume`) → merge of all proposals → apply in chunks of 200 → after snapshot and diff.
Progress is kept in `out/<folder>/fullrun-state.json`. Re-running the same command resumes from the first unfinished step or chunk. Draft captions are never part of it.

### Limitations
- Sharpness is relative to the folder (percentiles), so an all-soft folder still gets some "Sharp" frames. Pans with intentional blur are flagged Motion Blur.
- The aesthetic score is a generic LAION model and is not tuned to motorsport or gig taste.
- Bursts need capture times within 2 s and very similar framing.
- OCR misses small, angled or motion-blurred text, and Japanese vertical text is weaker. Translation is literal.
- Journal and ask only know what is in the index and proposals; frames not indexed are invisible. The LLM can still over-generalise.
- Faces at stage distance, in profile or in heavy make-up often stay unclustered.


### Control panel

Web UI: http://127.0.0.1:8766/ (LaunchAgent `com.trips.localai-lr.gui`).
Dock app: **Trips Local AI for Lightroom** (opens already signed in on this Mac). Token file: `~/localai-lr/gui/auth.token`.


### Rank, cull keywords and race-number review
- **Rank** (`tag rank`, in fullrun and the panel by default): tiers are relative to the shoot. Weights live in `features.py` `RANK_DEFAULTS` and can be overridden in `config.json` under `"rank"`. Calibrated on a motorsport shoot against 3★+ frames: AUC 0.80 (the old aesthetic-only score was 0.62), and 16 of 35 3★+ frames land in Best (top 10%). A later retraining that used Camera Exports as positives (plus 3★+ ratings) was evaluated leave-one-shoot-out on held-out shoots; held-out combined AUC was 0.747 vs 0.749 for the current weights, so the earlier weights stay the default. Rollback is `config.json` `rank.previous_weights` or deleting the `rank.weights` override. Re-run with `venv/bin/python tools/train_rank_exports.py` (read-only on the NAS; does not apply keywords or change stars).
- **Cull keywords** (`venv/bin/python cull_album.py <folder>`, fullrun step `cull_albums`, panel pass "Cull keywords"): writes additive `Cull|Sharp + Burst Best`, `Cull|Rank Best` and `Cull|Shortlist` into XMP sidecars (same membership rules: Sharp and Burst Best, or Rank Best, excluding Motion Blur and Soft Focus). Build Lightroom smart collections on those keywords (see INSTALL-LR.md). Re-running is idempotent. Stars, colour labels and develop settings are not changed.
- **Race-number review**: open the panel and click **Review race numbers**, or go to `http://<host>:8766/review`. Keys: Enter/Y confirms, typing digits then Enter fixes, X/R rejects, S skips, ←/→ move, U undoes. **Apply reviewed numbers** adds only confirmed or fixed `Motorsport/Car Number/<n>` keywords, in chunks of 200, to the sidecars. A star/colour snapshot is taken from existing sidecars before and checked after. Decisions are saved in `out/<shoot>/review-decisions.json`, applied ones in `review-applied.json`.
- `apply_chunked.py <proposals.json> --tag NAME [--prefix-keyword General/Rank/] --apply` is a generic additive apply with before/after snapshots.


#### People pass (faces)
Optional local face recognition: Apple Vision detect + ArcFace (`models/faces/w600k_mbf.onnx`) embed,
cluster per shoot, match `~/localai-lr/people/library.json`. Produces `out/<shoot>/people-proposals.json`
with additive `People/<Name>` keywords (apply only when you run with apply). Name clusters in the
People tab (`/people`). Off by default in the Run pass list.


### AF focus check (afpoints.py)
Canon bodies record the AF area they used in the CR3 maker notes. `afpoints.py` reads it with the bundled ExifTool (read-only), maps the box onto the oriented preview and compares sharpness **inside the AF box** with the sharpest region elsewhere in the frame. It runs at the end of `tag sharpness` (skip with `LOCALAI_AFPOINTS=0` or `"features": {"af_points": false}`), or on its own: `venv/bin/python afpoints.py "<shoot>" [--annotate 8]`.
- `General/Focus/On Target`: AF box sharp and the sharpest part of the frame. Applied with the Sharpness pass (panel) and by `fullrun.py`.
- `General/Focus/Missed`: AF box soft while something else is clearly sharp (front/back focus or AF on the wrong thing). **Never auto-applied**: written as needs-review and shown under **Review missed focus** (`/review?kind=focus`). Confirm adds the keyword, Reject adds nothing, and nothing is written until you press Apply (snapshot before, stars/colours checked after).
- Panel: Run tab → *AF focus check* tick box (on by default; off sets `LOCALAI_AFPOINTS=0` for the Sharpness pass) and *Annotated AF previews* (N previews with the AF box drawn, saved to `~/localai-lr/out/afcheck/`).
- Outputs in `out/<shoot>/`: `afpoints-exif.json` (cache), `afpoints.csv`, `afpoints-proposals.json`; review decisions in `review-focus-decisions.json` / `review-focus-applied.json`. Thresholds: `config.json` → `"features": {"afpoints": {...}}` (calibrated on 2,332 Brands Hatch R6 frames).

### Keyword preview before apply (panel)
#### Camera info pass

`camera_info.py` reads Canon MakerNotes (and standard EXIF) from each CR2/CR3 with the bundled ExifTool (read-only) and does a cheap highlight/shadow clip check on the embedded or cached preview. It is a selectable panel pass (`tag camera <folder>`), on by default in the Quick / Standard / Full bundles, and also runs in `fullrun.py`. Thresholds live under `"features": {"camera": {…}}` in `config.json` (High ISO 6400 / Very High ISO 12800, Wide Open within 1/3 stop, Slow Shutter relative to focal length with two extra stops when IS is on, conservative clip fractions).
- Keywords are additive: `General/Camera/Lens/…`, `Focal/{Wide,Normal,Tele,Super Tele}`, `High ISO` / `Very High ISO`, `Wide Open`, `Slow Shutter`, `Drive/High Speed`, `Shutter/Electronic` (full electronic only; Electronic First Curtain is not flagged), `AF/{Servo,One-Shot,Manual}`, `AF Area/…`, `Subject/{People,Animals,Vehicles}`, `Eye Detection`, `IS/On|Off`, `Flash Fired`, plus existing `General/Orientation/{Portrait,Landscape}` and `General/Quality/Clipped Highlights|Crushed Shadows`.
- White balance, Picture Style and Highlight Tone Priority are recorded in `camera-proposals.json` / `camera.csv` only, not as keywords. So are the body serial and sub-second capture time (for bursts and schedule matching).
- GPS is not present on R6 files unless the camera was linked to a phone via Camera Connect; the pass records whether a GPS tag exists but does not invent place keywords.
- Existing shoots are not auto-applied. New imports pick the pass up from the profile. You can generate proposals for an older folder with `tag camera "<folder>"` and preview them in the panel.

Tick **Preview keywords before applying** on the Run tab (or press **Preview keywords now** to use results already on disk). The passes run without writing anything; the job then waits in the queue as *awaiting approval*. **Preview & approve** shows, per frame, the keywords that would be added next to the ones already there, filterable by keyword/filename and pass; untick keywords or frames you don't want, optionally **Check existing XMP** (read-only dry run against current sidecars), then **Approve & apply**. Only the approved plan is written (additive, with undo data). **Discard** closes it with nothing written.

### Auto-queue new shoots (Settings, off by default)
When switched on, the panel looks every poll interval in `Camera Imports/<year>/` for `YYYY-MM-DD…` folders newer than the **last-run cutoff** (a date and time you can edit in Settings, Europe/London). A folder is newer if its name date is after the cutoff day, or it is a same-day folder created after the cutoff time. It is queued with the chosen default profile once the folder has stopped changing (same image count/size on two looks in a row, nothing written for 20 minutes). There is no Lightroom catalogue check; Lightroom does not need to be open. Already-run folders, and folders that already have a job, are never re-queued. After a successful auto-run the cutoff advances to that job's queue time so a folder that arrived mid-run is not missed. Draft Captions is never included; MP4/MOV files are ignored as usual. Settings (including the cutoff) live in `gui/autoqueue.json`, progress in `gui/autoqueue-state.json`. Off by default.

### Telegram alerts (job finished / failed / preview waiting)
1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token it gives you.
2. Send your new bot any message (e.g. `/start`), then open `https://api.telegram.org/bot<token>/getUpdates` in a browser and copy `"chat":{"id": …}`.
3. Put both in `~/localai-lr/gui/notify.json` (copy `notify.example.json`; the file is untracked and must never be committed), and `chmod 600` it:
   `"enabled": true, "telegram": {"enabled": true, "bot_token": "<token>", "chat_id": "<id>"}`
4. Settings ▸ Notifications ▸ **Send Telegram test**. The token is only read from that file; the panel never accepts it over the API.
