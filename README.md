# Local AI tagger (this-mac)

Proposes Capture One keywords and one-line draft captions for a shoot folder, fully on-device.
**Read-only on originals**: CR3s are only opened for reading (SHA-256 + embedded preview).
Previews are cached in `~/localai/cache/<sha256-prefix>.jpg`; nothing is written next to the originals.
Capture One is written to only through the additive apply path (see below): keywords from the Trip Keywords library are added, and IPTC Description is set only if it is empty. Nothing is ever removed, and ratings and colour tags are never touched.

## Run
```
~/localai/tag "/Volumes/Photos/Camera Imports/2026/<folder>" --profile motorsport|gig|kyoto \
    [--sample 40] [--limit N] [--no-caption] [--context "Britcar Into The Night, Brands Hatch"] [--tag dryrun]
```
- top-level files only (nested export folders skipped); MP4/MOV ignored.
- `--sample N` spreads N frames evenly across the folder; omit for the whole folder.
- `--no-caption` skips the Ollama model (SigLIP labels and Apple OCR only, ~0.5 s/image).
- `tag` starts the local Ollama server (127.0.0.1 only) if needed and runs at `nice 10`.

Output: `~/localai/out/<folder>/<profile>-proposals[-tag].json` and `.csv`
(rel_path, filename, sha256, proposed_keywords, tentative_keywords, needs_review + reasons,
car_colour, race_number + source, OCR candidates, label scores, caption, timings).

## Profiles and keywords
- motorsport: `Motorsport/Car Number/<n>`, `Motorsport/Car Colour/<Colour>`, `Motorsport/Night`.
  Number is proposed only when Apple Vision OCR and the VLM agree; a VLM-only, OCR-only or disagreeing
  read goes to `tentative_keywords` and needs_review.
- gig: `Gig/Crowd`, `Gig/Stage`, `Gig/Portrait`.
- kyoto: `Kyoto/Temple`, `Kyoto/Street`, `Kyoto/Nature`, `Kyoto/People`, `Kyoto/Landscape`, `Kyoto/Transit`, plus `Kyoto/Shrine Gate`, `Kyoto/Night`, `Kyoto/Rain`.

## Models (all local)
- SigLIP ViT-B-16-SigLIP-256 (open_clip, PyTorch MPS): zero-shot scene/shot/place and car colour.
- Apple Vision `VNRecognizeTextRequest` (pyobjc): race-number OCR on the full-res preview and 6 overlapping tiles.
- Ollama `qwen2.5vl:3b`: caption, race-number read, colour, torii/night/rain flags.

## Config
`~/localai/config.json`: model names and thresholds (label_min, colour_min, is_car_min, ocr_min,
ocr_min_height, binary_tag_min), preview sizes.

## Layout
`venv/` Python 3.12 venv · `python/` uv-managed Python · `bin/uv` · `ollama/` Ollama binary ·
`models/` Ollama + Hugging Face weights · `cache/` previews · `out/` proposals · `logs/`.
Stop Ollama: `kill $(cat ~/localai/logs/ollama.pid)`. Remove everything: `rm -rf ~/localai`.

## Keyword library
`~/localai/keywords/trip-keyword-library.txt` (tab-indented, 1,236 keywords) and `.csv`; rebuild with
`venv/bin/python keywords/build_library.py`. Loaded into Capture One as the keyword library "Trip Keywords"
(Keyword Library tool), file `~/Library/Application Support/Capture One/Keywords/Trip Keywords.cokeywordsdb`.
Tagger keyword paths (e.g. Motorsport/Car Colour/Grey) match the library's hierarchy 1:1.

## Applying to Capture One (--apply)
After tagging, the tagger always writes a Capture One plan (dry run): `/tmp/trip-localai-apply-<time>-{before,plan,result}.json`.
With `--apply` it ADDS the confident keywords via AppleScript (`apply_c1.py` + `apply_c1.js`):
- only images with needs_review = false, only `proposed_keywords` (never tentative/VLM-only numbers);
- SHA-256 of each original re-checked against the proposal before applying;
- keywords taken from the "Trip Keywords" library so they nest (Motorsport > Car Colour > Green); paths not in the library are skipped;
- variants matched by name + full path in Capture One's CURRENT collection (others are reported, not searched);
- never removes keywords; post-check fails loudly if any existing keyword or rating/colour tag changed.
SHA-256 values are cached in `index/catalog.sqlite` (path_cache), keyed by path, size and mtime. A changed size or mtime forces a full re-hash (`shacache.py`).
Re-apply an existing proposals file: `venv/bin/python apply_c1.py out/<folder>/<profile>-proposals.json [--apply]`.

## Feature commands (`~/localai/tag <cmd> …` → features.py)
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
| `name-person 3 "label"` | Gives an anonymous group a local label (your own; nothing is looked up) | — | — | — |
| `schedule <folder> [--schedule F] [--camera-offset +09:00]` | Matches capture time to the gig schedule, writing `Gig/Event`, `Venue`, `Act`, `Cheki`, `Meet & Greet` and `Changeover` | exiftool 13.61 | ~10 MB (bin/) | one exiftool call per batch |
| `captions --files <exports>` | **Export-only.** Writes draft captions to `out/salamander/captions-<time>.json`. It refuses anything not under `Camera Exports/` | qwen2.5vl:3b | 3.2 GB | about 3 s/image |

Feature proposals are written to `out/<folder>/{sharpness,bursts,rank,ocr,schedule}-proposals.json`. They are apply_c1-compatible: `venv/bin/python apply_c1.py <file> [--apply]`.

Face grouping is anonymous and local. Nobody is identified, and no lookups or cloud calls are made. Delete `index/faces*` and the faces tables to forget the groups.

## Capture One Scripts menu: Scripts ▸ Trips Local AI
These are in `~/Library/Scripts/Capture One Scripts/Trips Local AI/`, with sources in `scripts/src/`. Each one acts on the **selected variants**. It runs `scripts_run.py` in the background at `nice 10`, posts a notification when done, and logs to `logs/scripts/<time>-<action>.log`. To test from Terminal, pass variant names: `osascript ".../Rank Selection.scpt" _A3A5540 _A3A5600`.
- **Tag with Local AI**: runs the tagger (profile picked from the folder name: motorsport for BrandsHatch/Britcar/Silverstone…, otherwise gig; context from config `script_context`) and adds confident keywords.
- **Find Bursts in Selection**: adds `General/Burst Best` to the suggested frame of each burst. Ratings are untouched.
- **Rank Selection**: adds `General/Score/<bucket>`.
- **Check Sharpness**: adds `General/Quality/Sharp | Motion Blur | Soft Focus`.
- **Read Text in Frame**: sets the IPTC Description to the OCR text only if it is empty; otherwise the text goes to `out/<folder>/ocr.json` only.
- **Tag by Schedule**: adds `Gig/Event`, `Venue`, `Act`, `Cheki`, `Meet & Greet` and `Changeover` from `schedules/*.json`.
- **Draft Captions (Exports)**: writes a sidecar JSON for Salamander from exported files only. It never writes into Capture One.

## Schedule tagging
`schedules/example.json` (JST, CONFIRMED shows only) lists events (name, venue, date, window, `attend_until`, `buffer_min`), slots (act, start, end) and acts with unknown slots in `acts_tba`. It also covers no-photo acts, cheki and meet windows, and `only_inside_slots` events.
The camera offset comes from EXIF OffsetTimeOriginal, else config `camera_utc_offset`. Use `--camera-offset force:+09:00` to override a wrong camera clock zone.
Frames in a no-photo act's slot, or in overlapping events with no slot match, are marked needs_review and not applied. Acts with an unknown slot get only the event and venue.
**When TBA slots are published:**
1. Move the act from `acts_tba` into `slots` with its start and end times. Fix `window_end` if it was estimated.
2. Run `venv/bin/python tests_schedule.py`.
3. If the act or venue name is new, add it to `keywords/delta-<date>.txt` and import it into Capture One through the Trip Keywords library import, as done for delta-2026-11-09.
4. Re-run "Tag by Schedule" or `tag schedule <folder>` and then `apply_c1.py … --apply`. Applying is additive and safe to repeat.

## Full-folder run (resumable)
`nohup nice -n 10 venv/bin/python fullrun.py "<folder>" --profile motorsport --context "…" > logs/fullrun/<name>.log &`
The steps are: rating and colour snapshot → tagger (`--resume`) → index → sharpness → bursts → rank → OCR (`--resume`) → merge of all proposals → apply in chunks of 200 → after snapshot and diff.
Progress is kept in `out/<folder>/fullrun-state.json`. Re-running the same command resumes from the first unfinished step or chunk. Draft captions are never part of it.

## Limitations
- Sharpness is relative to the folder (percentiles), so an all-soft folder still gets some "Sharp" frames. Pans with intentional blur are flagged Motion Blur.
- The aesthetic score is a generic LAION model and is not tuned to motorsport or gig taste.
- Bursts need capture times within 2 s and very similar framing.
- OCR misses small, angled or motion-blurred text, and Japanese vertical text is weaker. Translation is literal.
- Journal and ask only know what is in the index and proposals; frames not indexed are invisible. The LLM can still over-generalise.
- Faces at stage distance, in profile or in heavy make-up often stay unclustered.


## Control panel

Web UI: http://127.0.0.1:8765/ or http://127.0.0.1:8765/ (LaunchAgent `com.trips.localai.gui`).
Dock app: **Trips Local AI** (opens already signed in on this Mac). Token file: `~/localai/gui/auth.token`.


## Rank, cull albums and race-number review (added 2026-11-09)
- **Rank** (`tag rank`, in fullrun and the panel by default): tiers are relative to the shoot. Weights live in `features.py` `RANK_DEFAULTS` and can be overridden in `config.json` under `"rank"`. Calibrated on Brands Hatch against your 3★+ frames: AUC 0.80 (the old aesthetic-only score was 0.62), and 16 of your 35 3★+ frames land in Best (top 10%).
- **Cull smart albums** (`venv/bin/python cull_album.py <folder>`, fullrun step `cull_albums`, panel pass "Cull smart albums"): in the collection group **Trips Local AI**, three keyword smart albums per shoot: `<shoot> · Cull: Sharp + Burst Best`, `<shoot> · Cull: Rank Best` and `<shoot> · Cull: Shortlist` (either of those). All exclude Motion Blur and Soft Focus and are limited to that shoot's folder. Re-running refreshes them, and only collections change.
- **Race-number review**: open the panel and click **Review race numbers**, or go to `http://<M5>:8765/review`. Keys: Enter/Y confirms, typing digits then Enter fixes, X/R rejects, S skips, ←/→ move, U undoes. **Apply reviewed numbers** adds only confirmed or fixed `Motorsport/Car Number/<n>` keywords, in chunks of 200. A star/colour snapshot is taken before and checked after. Decisions are saved in `out/<shoot>/review-decisions.json`, applied ones in `review-applied.json`, and undo data in `review-apply-*-c*-before.json`.
- `apply_chunked.py <proposals.json> --tag NAME [--prefix-keyword General/Rank/] --apply` is a generic additive apply with before/after snapshots.


### People pass (faces)
Optional local face recognition: Apple Vision detect + ArcFace (`models/faces/w600k_mbf.onnx`) embed,
cluster per shoot, match `~/localai/people/library.json`. Produces `out/<shoot>/people-proposals.json`
with additive `People/<Name>` keywords (apply only when you run with apply). Name clusters in the
People tab (`/people`). Off by default in the Run pass list.


## AF focus check (afpoints.py)
Canon bodies record the AF area they used in the CR3 maker notes. `afpoints.py` reads it with the bundled ExifTool (read-only), maps the box onto the oriented preview and compares sharpness **inside the AF box** with the sharpest region elsewhere in the frame. It runs at the end of `tag sharpness` (skip with `LOCALAI_AFPOINTS=0` or `"features": {"af_points": false}`), or on its own: `venv/bin/python afpoints.py "<shoot>" [--annotate 8]`.
- `General/Focus/On Target`: AF box sharp and the sharpest part of the frame. Applied with the Sharpness pass (panel) and by `fullrun.py`.
- `General/Focus/Missed`: AF box soft while something else is clearly sharp (front/back focus or AF on the wrong thing). **Never auto-applied**: written as needs-review and shown under **Review missed focus** (`/review?kind=focus`). Confirm adds the keyword, Reject adds nothing, and nothing is written until you press Apply (snapshot before, stars/colours checked after).
- Panel: Run tab → *AF focus check* tick box (on by default; off sets `LOCALAI_AFPOINTS=0` for the Sharpness pass) and *Annotated AF previews* (N previews with the AF box drawn, saved to `~/localai/out/afcheck/`).
- Outputs in `out/<shoot>/`: `afpoints-exif.json` (cache), `afpoints.csv`, `afpoints-proposals.json`; review decisions in `review-focus-decisions.json` / `review-focus-applied.json`. Thresholds: `config.json` → `"features": {"afpoints": {...}}` (calibrated on 2,332 Brands Hatch R6 frames).

## Keyword preview before apply (panel)
Tick **Preview keywords before applying** on the Run tab (or press **Preview keywords now** to use results already on disk). The passes run without writing anything; the job then waits in the queue as *awaiting approval*. **Preview & approve** shows, per frame, the keywords that would be added next to the ones already there, filterable by keyword/filename and pass; untick keywords or frames you don't want, optionally **Check against Capture One** (read-only dry run), then **Approve & apply**. Only the approved plan is written (additive, folder-scoped, with undo data). **Discard** closes it with nothing written.

## Auto-queue new shoots (Settings, off by default)
When switched on, the panel looks every ~30 minutes (in step with Capture One referencing new imports) in `[03] Photos/Camera Imports/<year>/` for new `YYYY-MM-DD…` folders and queues each one with the chosen default profile once Capture One has it (read-only check, only while no job is using the catalog) or once the folder has stopped changing (same image count/size on two looks in a row, nothing written for 20 minutes). Folders that already existed when you switched it on, and folders that already have a job, are never queued. MP4/MOV files are ignored as usual. Settings live in `gui/autoqueue.json`, progress in `gui/autoqueue-state.json`.

## Telegram alerts (job finished / failed / preview waiting)
1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token it gives you.
2. Send your new bot any message (e.g. `/start`), then open `https://api.telegram.org/bot<token>/getUpdates` in a browser and copy `"chat":{"id": …}`.
3. Put both in `~/localai/gui/notify.json` (copy `notify.example.json`; the file is untracked and must never be committed), and `chmod 600` it:
   `"enabled": true, "telegram": {"enabled": true, "bot_token": "<token>", "chat_id": "<id>"}`
4. Settings ▸ Notifications ▸ **Send Telegram test**. The token is only read from that file; the panel never accepts it over the API.
