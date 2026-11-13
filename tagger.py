#!/usr/bin/env python3
"""
Local AI photo tagger for Capture One (Apple silicon).

READ-ONLY on originals: files are opened for reading only (hash + embedded preview).
Previews are cached under ~/localai/cache, outputs under ~/localai/out/<folder>/.
Never writes to Capture One - it only produces keyword PROPOSALS (json + csv).

Profiles: motorsport | gig | street (kyoto is an alias of street)
"""
import argparse, base64, csv, hashlib, io, json, os, re, subprocess, sys, time
from pathlib import Path

HOME = Path.home() / "localai"
os.environ.setdefault("HF_HOME", str(HOME / "models" / "hf"))
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np
import requests
from PIL import Image, ImageOps

RAW_EXT = {".cr3", ".cr2", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff"}
SKIP_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mts"}
# Event schedule posters (Schedules tree, e.g. _schedule.jpg) are never photos to tag, rank or cull.
SCHEDULE_PREFIX = "_schedule"


def is_schedule_file(p) -> bool:
    return Path(str(p)).name.lower().startswith(SCHEDULE_PREFIX)

# ---------------------------------------------------------------- profiles
COLOURS = ["red", "blue", "green", "yellow", "orange", "white", "black",
           "silver", "grey", "purple", "pink", "gold"]
COLOUR_KW = {"grey": "Grey", "silver": "Silver"}

PROFILES = {
    "motorsport": {
        "groups": {
            "scene": {
                "Car on track": "a photo of a racing car on a race track",
                "Paddock / people": "a photo of people in a motorsport paddock or pit lane",
                "Crowd": "a photo of spectators watching a race",
                "Other": "a photo of scenery, buildings or objects",
            },
        },
        "binary": {
            "Night": ("a photo of a race at night, dark sky, glowing headlights and floodlights", "a photo of a race in daylight or at sunset"),
        },
        "caption_prompt": ("You are helping a motorsport photographer. "
                           "Return JSON with keys: caption (one vivid line, max 20 words, no hashtags, "
                           "suitable as a draft social caption), race_number (the racing number "
                           "on the main car as a string, or null if unreadable), car_colour "
                           "(main body colour of the main car, one word, or null)."),
    },
    "gig": {
        "groups": {
            "shot": {
                "Gig/Crowd": "a photo of a concert crowd, audience seen from the stage or within the crowd",
                "Gig/Stage": "a wide photo of a concert stage with lights and a band performing",
                "Gig/Portrait": "a close-up portrait of a single musician performing on stage",
            },
        },
        "binary": {},
        "caption_prompt": ("You are helping a live music photographer. Return JSON with keys: "
                           "caption (one vivid line, max 20 words, no hashtags, suitable as a draft "
                           "social caption), shot_type (crowd, stage or portrait)."),
    },
    "street": {
        "groups": {
            "place": {
                "Street/Alley": "a photo of a narrow alley or passageway between buildings",
                "Street/Crowd": "a photo of a crowd of pedestrians on a street",
                "Street/Pedestrian": "a photo of one or more pedestrians walking on a street or pavement",
                "Street/Shopfront": "a photo of a shop front, storefront or window display on a street",
                "Street/Market": "a photo of a street market or market stalls",
                "Street/Crossing": "a photo of a pedestrian crossing or a street crossing",
                "Street/Cyclist": "a photo of a cyclist or bicycle on a street",
                "Street/Temple": "a photo of a temple building, temple roof, temple gate or temple grounds",
                "Street/Candid": "a candid photo of a person in a public place, not posing for the camera",
            },
        },
        "binary": {
            "Street/Shrine Gate": ("a photo of a shrine gate or temple gate as a main subject", "a photo of a place without a shrine gate or temple gate"),
            "Street/Night Street": ("a photo of a city street at night with artificial lights", "a photo of a street in daylight"),
            "Street/Rain": ("a photo taken in the rain with wet ground, raindrops or umbrellas", "a photo taken in dry weather"),
            "Street/Neon": ("a photo of neon signs or colourful neon lights on a city street", "a photo without neon signs or neon lighting"),
            "Street/Signage": ("a photo of a large sign, billboard, shop sign or written lantern as a main subject", "a photo in which signs are absent or only a tiny background detail"),
            "Street/Vending Machine": ("a photo of a vending machine as a main subject", "a photo with no vending machine"),
            "Street/Reflections": ("a photo of reflections in wet pavement, a window or glass", "a photo without noticeable reflections"),
            "Street/Silhouette": ("a photo of a silhouette against a bright sky or lights", "a photo in which the subject is lit from the front, not a silhouette"),
            "Street/Umbrella": ("a photo in which an umbrella is clearly visible", "a photo with no umbrella"),
            "Street/Lantern": ("a photo of a lantern or hanging lanterns as a main subject", "a photo with no lantern"),
        },
        "caption_prompt": ("You are helping a street photographer. Return JSON with keys: "
                           "caption (one vivid line, max 20 words, no hashtags, suitable as a draft "
                           "social caption), place_type (alley, crowd, shopfront, market, crossing, temple or candid), "
                           "shrine_gate (true if a shrine gate or temple gate is visible), night (true/false), rain (true/false)."),
        "vlm_flags": {"Street/Shrine Gate": "shrine_gate", "Street/Night Street": "night", "Street/Rain": "rain"},
    },
}
PROFILES["kyoto"] = PROFILES["street"]  # alias so older profiles keep working

# ---------------------------------------------------------------- helpers
def sha256_file(p, bs=4 * 1024 * 1024):
    h = hashlib.sha256()
    with open(p, "rb") as f:          # read-only
        while True:
            b = f.read(bs)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def _exiftool_preview(path: Path):
    """Read-only: pull PreviewImage / JpgFromRaw / ThumbnailImage via bundled exiftool."""
    et = HOME / "bin/exiftool"
    if not et.exists():
        return None
    for tag in ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage"):
        try:
            r = subprocess.run([str(et), "-b", tag, str(path)],
                               capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        data = r.stdout or b""
        if r.returncode == 0 and data[:3] == b"\xff\xd8\xff":
            try:
                return Image.open(io.BytesIO(data)).convert("RGB")
            except Exception:
                continue
    return None


def load_preview(path: Path, digest: str, max_px: int, want_full=False):
    """Embedded preview -> cached JPEG in ~/localai/cache (never next to originals).
    Returns (resized_preview, full_res_preview_or_None)."""
    cache = HOME / "cache" / f"{digest[:24]}.jpg"
    if cache.exists() and not want_full:
        return Image.open(cache).convert("RGB"), None
    ext = path.suffix.lower()
    if ext in RAW_EXT:
        import rawpy
        im = None
        flip = 0
        try:
            with rawpy.imread(str(path)) as raw:
                flip = raw.sizes.flip
                try:
                    th = raw.extract_thumb()
                    if th.format == rawpy.ThumbFormat.JPEG:
                        im = Image.open(io.BytesIO(th.data)).convert("RGB")
                    else:
                        im = Image.fromarray(th.data).convert("RGB")
                except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
                    # no embedded preview: quick half-size demosaic (already oriented)
                    try:
                        im = Image.fromarray(raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8))
                        flip = 0
                    except rawpy.LibRawError:
                        im = None
        except rawpy.LibRawError:
            im = None
        if im is None:
            im = _exiftool_preview(path)
            flip = 0
        if im is None:
            raise RuntimeError(f"no preview could be read from {path}")
        # embedded previews are stored unrotated; apply raw orientation
        if flip == 3:
            im = im.rotate(180, expand=True)
        elif flip == 5:
            im = im.rotate(90, expand=True)
        elif flip == 6:
            im = im.rotate(-90, expand=True)
    else:
        im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    full = im.copy() if want_full else None
    im.thumbnail((max_px, max_px), Image.LANCZOS)
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        im.save(cache, "JPEG", quality=90)
    return im, full

def tiled_ocr(full: Image.Image, cols=3, rows=2, overlap=0.15):
    """Vision OCR on the whole full-res preview plus overlapping tiles (small race numbers).
    Temp tiles live in ~/localai/cache/_ocr and are deleted afterwards.
    Returns list of (text, conf, rel_height_of_full_image)."""
    tmpdir = HOME / "cache" / "_ocr"; tmpdir.mkdir(parents=True, exist_ok=True)
    W, H = full.size
    boxes = [(0, 0, W, H)]
    tw, th_ = W / cols, H / rows
    for r in range(rows):
        for c in range(cols):
            x0 = max(0, int(c * tw - overlap * tw)); y0 = max(0, int(r * th_ - overlap * th_))
            x1 = min(W, int((c + 1) * tw + overlap * tw)); y1 = min(H, int((r + 1) * th_ + overlap * th_))
            boxes.append((x0, y0, x1, y1))
    out = []
    for i, b in enumerate(boxes):
        p = tmpdir / f"t{i}.jpg"
        full.crop(b).save(p, "JPEG", quality=92)
        try:
            for text, conf, h in vision_ocr(str(p)):
                out.append((text, conf, h * (b[3] - b[1]) / H))
        finally:
            p.unlink(missing_ok=True)
    return out

class Clip:
    def __init__(self, cfg):
        import torch, open_clip
        self.torch = torch
        self.dev = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model, _, self.pre = open_clip.create_model_and_transforms(
            cfg["siglip_model"], pretrained=cfg["siglip_pretrained"])
        self.model = self.model.to(self.dev).eval()
        self.tok = open_clip.get_tokenizer(cfg["siglip_model"])
        self._cache = {}

    def text(self, prompts):
        key = tuple(prompts)
        if key not in self._cache:
            with self.torch.no_grad():
                t = self.model.encode_text(self.tok(list(prompts)).to(self.dev))
                self._cache[key] = t / t.norm(dim=-1, keepdim=True)
        return self._cache[key]

    def image(self, im):
        with self.torch.no_grad():
            x = self.pre(im).unsqueeze(0).to(self.dev)
            f = self.model.encode_image(x)
            return f / f.norm(dim=-1, keepdim=True)

    def softmax(self, img_f, prompts):
        t = self.text(prompts)
        logits = (img_f @ t.T) * self.model.logit_scale.exp()
        return self.torch.softmax(logits, dim=-1)[0].detach().float().cpu().numpy()

def vision_ocr(jpeg_path: str):
    """Apple Vision text recognition. Returns list of (text, confidence, rel_height)."""
    import Vision, Quartz
    from Foundation import NSURL
    url = NSURL.fileURLWithPath_(jpeg_path)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    ok, err = handler.performRequests_error_([req], None)
    out = []
    for obs in (req.results() or []):
        for cand in obs.topCandidates_(3):
            out.append((str(cand.string()), float(cand.confidence()), float(obs.boundingBox().size.height)))
            break
    return out

NUM_RE = re.compile(r"^#?(\d{1,3})$")

def car_numbers(ocr, th):
    nums = {}
    for text, conf, h in ocr:
        for tok in re.split(r"[\s/|,-]+", text.strip()):
            m = NUM_RE.match(tok)
            if not m:
                continue
            n = str(int(m.group(1)))
            if h < th["ocr_min_height"]:
                continue
            score = conf
            if n not in nums or nums[n]["conf"] < score:
                nums[n] = {"number": n, "conf": round(score, 3), "rel_height": round(h, 3)}
    return sorted(nums.values(), key=lambda d: (-d["rel_height"], -d["conf"]))

CONTEXT = ""
LANG_RULE = (" Write the caption in British English only. Describe only what is visible; do not name a "
             "specific landmark, venue or city unless its name is clearly written in the photo.")

def vlm(cfg, im, prompt, timeout=180):
    prompt = prompt + LANG_RULE + (f" Context from the photographer: {CONTEXT}." if CONTEXT else "")
    im2 = im.copy()
    im2.thumbnail((cfg["vlm_max_px"], cfg["vlm_max_px"]))
    buf = io.BytesIO(); im2.save(buf, "JPEG", quality=88)
    r = requests.post(cfg["ollama_url"] + "/api/generate", json={
        "model": cfg["vlm_model"], "prompt": prompt, "images": [base64.b64encode(buf.getvalue()).decode()],
        "format": "json", "stream": False, "keep_alive": "5m",
        "options": {"temperature": 0.0, "num_predict": 160},
    }, timeout=timeout)
    r.raise_for_status()
    txt = r.json().get("response", "")
    try:
        return json.loads(txt)
    except Exception:
        return {"caption": txt.strip()[:200]}

def list_images(folder: Path):
    files = []
    for p in sorted(folder.iterdir()):       # top level only; nested export folders skipped
        if p.is_dir() or p.name.startswith(".") or is_schedule_file(p):
            continue
        ext = p.suffix.lower()
        if ext in SKIP_EXT:
            continue
        if ext in RAW_EXT or ext in IMG_EXT:
            files.append(p)
    return files

def spread(items, n):
    if not n or n >= len(items):
        return items
    idx = np.linspace(0, len(items) - 1, n).round().astype(int)
    return [items[i] for i in sorted(set(idx))]

# ---------------------------------------------------------------- main
def process(p, folder, profile, cfg, clip, args):
    th = cfg["thresholds"]; prof = PROFILES[profile]
    t0 = time.time()
    rec = {"rel_path": str(p.relative_to(folder.parent)), "filename": p.name}
    rec["sha256"] = __import__("shacache").sha256_cached(p)
    im, full = load_preview(p, rec["sha256"], cfg["preview_max_px"], want_full=(profile == "motorsport"))
    cache_path = str(HOME / "cache" / f"{rec['sha256'][:24]}.jpg")
    t_prev = time.time()
    f = clip.image(im)
    labels, keywords, review = {}, [], []
    for gname, opts in prof["groups"].items():
        probs = clip.softmax(f, list(opts.values()))
        ranked = sorted(zip(opts.keys(), probs.tolist()), key=lambda x: -x[1])
        labels[gname] = [(k, round(v, 3)) for k, v in ranked]
        top, conf = ranked[0]
        if profile != "motorsport":
            if conf >= th["label_min"]:
                keywords.append(top)
            else:
                review.append(f"low {gname} conf {conf:.2f}")
    for kw, (pos, neg) in prof["binary"].items():
        pr = float(clip.softmax(f, [pos, neg])[0])
        labels[kw] = round(pr, 3)
        if pr >= th["binary_tag_min"]:
            keywords.append(kw if "/" in kw else f"Motorsport/{kw}")

    rec["car_colour"] = None; rec["car_numbers"] = []
    if profile == "motorsport":
        is_car = dict(labels["scene"]).get("Car on track", 0)
        rec["is_car_conf"] = is_car
        if is_car >= th["is_car_min"]:
            cp = clip.softmax(f, [f"a photo of a {c} racing car" for c in COLOURS])
            cr = sorted(zip(COLOURS, cp.tolist()), key=lambda x: -x[1])
            labels["colour"] = [(k, round(v, 3)) for k, v in cr[:3]]
            if cr[0][1] >= th["colour_min"]:
                rec["car_colour"] = cr[0][0]
            else:
                review.append(f"colour unsure ({cr[0][0]} {cr[0][1]:.2f})")
            ocr = tiled_ocr(full if full is not None else im)
            rec["car_numbers"] = car_numbers(ocr, th)
        else:
            review.append("no car detected")
    t_clip = time.time()

    vl = {}
    if not args.no_caption:
        try:
            vl = vlm(cfg, im, prof["caption_prompt"])
        except Exception as e:
            vl = {"error": str(e)[:200]}
            review.append("caption failed")
    rec["caption"] = (vl.get("caption") or "").strip()
    # binary tags the VLM can confirm (e.g. torii gate, which SigLIP-B misses)
    for kw, key in prof.get("vlm_flags", {}).items():
        v = vl.get(key)
        flag = v is True or str(v).lower() in ("true", "yes")
        labels[kw + " (vlm)"] = flag
        if flag and kw not in keywords:
            keywords.append(kw)
    t_vlm = time.time()

    if profile == "motorsport" and rec.get("is_car_conf", 0) >= th["is_car_min"]:
        vnum = vl.get("race_number")
        vnum = str(int(re.sub(r"\D", "", str(vnum)))) if vnum and re.sub(r"\D", "", str(vnum)) else None
        rec["vlm_number"] = vnum
        ocr_best = next((n for n in rec["car_numbers"] if n["conf"] >= th["ocr_min"]), None)
        chosen, src = None, None
        if ocr_best and vnum and ocr_best["number"] == vnum:
            chosen, src = vnum, "ocr+vlm agree"
        elif ocr_best and not vnum:
            chosen, src = ocr_best["number"], "ocr only"; review.append("number from OCR only")
        elif vnum and any(vnum.endswith(n["number"]) or vnum.startswith(n["number"]) for n in rec["car_numbers"]):
            chosen, src = vnum, "vlm, partial ocr match"; review.append("number partially confirmed by OCR")
        elif ocr_best and vnum:
            # disagree: prefer an OCR candidate that matches the VLM, else flag
            match = next((n for n in rec["car_numbers"] if n["number"] == vnum), None)
            if match:
                chosen, src = vnum, "vlm matched secondary ocr"
            else:
                chosen, src = ocr_best["number"], "ocr/vlm disagree"; review.append(f"number disagree ocr {ocr_best['number']} vs vlm {vnum}")
        elif vnum:
            chosen, src = vnum, "vlm only"; review.append("number from VLM only")
        rec["race_number"] = chosen; rec["number_source"] = src
        if chosen and src in ("ocr+vlm agree", "vlm matched secondary ocr"):
            keywords.append(f"Motorsport/Car Number/{chosen}")
        elif chosen:
            rec.setdefault("tentative_keywords", []).append(f"Motorsport/Car Number/{chosen}")
        if rec["car_colour"]:
            keywords.append(f"Motorsport/Car Colour/{COLOUR_KW.get(rec['car_colour'], rec['car_colour'].title())}")
        vcol = (vl.get("car_colour") or "").lower().strip()
        rec["vlm_colour"] = vcol or None
        if rec["car_colour"] and vcol and vcol not in (rec["car_colour"],) and not (
                {vcol, rec["car_colour"]} <= {"grey", "silver", "gray"}):
            review.append(f"colour disagree siglip {rec['car_colour']} vs vlm {vcol}")

    rec["labels"] = labels
    rec["proposed_keywords"] = keywords
    rec["needs_review"] = bool(review)
    rec["review_reasons"] = review
    rec["timing_s"] = {"hash+preview": round(t_prev - t0, 2), "siglip+ocr": round(t_clip - t_prev, 2),
                       "caption": round(t_vlm - t_clip, 2), "total": round(t_vlm - t0, 2)}
    return rec

def main():
    ap = argparse.ArgumentParser(description="Local AI keyword/caption proposals (read-only)")
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--files", nargs="+", help="explicit image paths (e.g. a Capture One selection) instead of a folder")
    ap.add_argument("--resume", action="store_true", help="skip frames already in this run's .jsonl progress file")
    ap.add_argument("--profile", required=True, choices=list(PROFILES))
    ap.add_argument("--sample", type=int, default=0, help="evenly spread N images through the folder (0 = all)")
    ap.add_argument("--limit", type=int, default=0, help="first N images only")
    ap.add_argument("--no-caption", action="store_true", help="skip the Ollama caption/number model")
    ap.add_argument("--config", default=str(HOME / "config.json"))
    ap.add_argument("--tag", default="", help="suffix for output file names, e.g. dryrun")
    ap.add_argument("--apply", action="store_true",
                    help="after tagging, ADD confident keywords (needs_review=false, proposed only) to Capture One; default is a dry-run plan only")
    ap.add_argument("--context", default="", help='shoot context for captions, e.g. "Britcar Into The Night, Brands Hatch"')
    args = ap.parse_args()
    global CONTEXT
    CONTEXT = args.context

    cfg = json.load(open(args.config))
    if args.files:
        files = [Path(f) for f in args.files if Path(f).suffix.lower() not in SKIP_EXT and not is_schedule_file(f)]
        folder = files[0].parent
    else:
        folder = Path(args.folder).resolve()
        files = list_images(folder)
    if args.limit:
        files = files[:args.limit]
    files = spread(files, args.sample)
    outdir = HOME / "out" / folder.name
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.profile}-proposals" + (f"-{args.tag}" if args.tag else "")
    print(f"{len(files)} images, profile={args.profile}, out={outdir}/{stem}.*", flush=True)

    clip = Clip(cfg)
    recs, t_all = [], time.time()
    prog = outdir / f"{stem}.jsonl"
    if args.resume and prog.exists():
        done = {}
        for line in open(prog):
            try: r = json.loads(line); done[r["filename"]] = r
            except Exception: pass
        recs = [done[p.name] for p in files if p.name in done and not done[p.name].get("error")]
        files = [p for p in files if p.name not in {r["filename"] for r in recs}]
        print(f"resume: {len(recs)} already done, {len(files)} to go", flush=True)
    elif prog.exists():
        prog.unlink()
    pf = open(prog, "a")
    for i, p in enumerate(files, 1):
        try:
            r = process(p, folder, args.profile, cfg, clip, args)
        except Exception as e:
            r = {"rel_path": str(p.relative_to(folder.parent)), "filename": p.name, "error": str(e)[:300],
                 "needs_review": True}
        recs.append(r)
        print(f"[{i}/{len(files)}] {p.name} kw={r.get('proposed_keywords')} "
              f"rev={r.get("needs_review")} err={r.get("error","")} t={r.get('timing_s', {}).get('total')}s | {r.get('caption','')[:70]}", flush=True)
        pf.write(json.dumps(r) + "\n"); pf.flush()
        if i % 50 == 0 or i == len(files):
            json.dump({"folder": str(folder), "profile": args.profile, "config": cfg, "images": recs},
                      open(outdir / f"{stem}.json", "w"), indent=1)
    json.dump({"folder": str(folder), "profile": args.profile, "config": cfg, "images": recs},
              open(outdir / f"{stem}.json", "w"), indent=1)

    cols = ["rel_path", "filename", "sha256", "proposed_keywords", "tentative_keywords", "needs_review",
            "review_reasons", "car_colour", "vlm_colour", "race_number", "number_source", "car_numbers",
            "labels", "caption", "timing_s", "error"]
    with open(outdir / f"{stem}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in recs:
            row = {k: r.get(k) for k in cols}
            for k in ("proposed_keywords", "tentative_keywords", "review_reasons"):
                row[k] = "; ".join(row[k] or [])
            for k in ("car_numbers", "labels", "timing_s"):
                row[k] = json.dumps(row[k]) if row[k] else ""
            w.writerow(row)
    el = time.time() - t_all
    print(f"done {len(recs)} images in {el:.0f}s ({el/max(1,len(recs)):.1f}s/image)", flush=True)
    # Capture One step: dry-run plan by default, additive apply only with --apply
    sys.path.insert(0, str(HOME))
    from apply_c1 import apply_proposals
    if not os.environ.get("LOCALAI_NO_APPLY_STEP"):
        apply_proposals(str(outdir / f"{stem}.json"), apply=args.apply)

if __name__ == "__main__":
    main()
