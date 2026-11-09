#!/usr/bin/env python3
"""
Local AI features for Capture One (all on-device, originals read-only).

  index      build/extend the image index (SigLIP embedding, aesthetic score, sharpness, EXIF)
  search     text search over the index
  ocr        Apple Vision text in frame (en + ja [+ zh]), optional Japanese->English via Ollama
  bursts     near-duplicate burst groups with a suggested best frame
  sharpness  Quality/Sharp | Motion Blur | Soft Focus flags (subject region, per-folder normalised)
  rank       aesthetic ranking + top-N shortlist
  journal    markdown day summary for a folder (local LLM)
  ask        question answering over the local index (local LLM, simple RAG)
  faces      detect + embed + cluster faces into anonymous groups (Person 001 ...)
  name-person  give an anonymous face group a local name
  captions   draft captions (sidecar JSON for Salamander)
Index: ~/localai/index/catalog.sqlite (+ index/siglip/*.npy, keyed by SHA-256).
"""
import argparse, csv, json, math, os, re, sqlite3, subprocess, sys, time
from pathlib import Path

HOME = Path.home() / "localai"
sys.path.insert(0, str(HOME))
os.environ.setdefault("HF_HOME", str(HOME / "models" / "hf"))
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
import numpy as np
import requests
from PIL import Image
import tagger as T

IDX = HOME / "index"; EMB = IDX / "siglip"; FACEDIR = IDX / "faces"
DB = IDX / "catalog.sqlite"
OUT = HOME / "out"
CFG = json.load(open(HOME / "config.json"))
FCFG = {  # feature defaults (override in config.json under "features")
    "text_model": "qwen3:8b",
    "burst_gap_s": 2.0, "burst_min_cos": 0.88, "burst_anchor_cos": 0.82,
    "sharp_pct": 0.60, "blur_pct": 0.25, "motion_coherence": 0.30,
    "sharp_abs_min": 300.0, "blur_abs_max": 120.0,
    "face_min_px": 64, "face_eps": 0.55, "face_min_samples": 2,
    "ocr_langs": ["ja-JP", "en-US"], "ocr_min_conf": 0.3,
}
FCFG.update(CFG.get("features", {}))

# ------------------------------------------------------------------ db
def db():
    IDX.mkdir(parents=True, exist_ok=True); EMB.mkdir(exist_ok=True); FACEDIR.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS path_cache(path TEXT PRIMARY KEY, size INT, mtime REAL, sha256 TEXT);
    CREATE TABLE IF NOT EXISTS images(sha256 TEXT PRIMARY KEY, path TEXT, rel_path TEXT, filename TEXT, folder TEXT,
      capture_time TEXT, camera TEXT, lens TEXT, focal REAL, exposure REAL, fnumber REAL, iso REAL,
      width INT, height INT, aesthetic REAL, sharp_full REAL, sharp_subject REAL, tenengrad_subject REAL,
      coherence REAL, subject_box TEXT, caption TEXT, keywords TEXT, labels TEXT,
      ocr_text TEXT, ocr_ja INT, ocr_translation TEXT, indexed_at REAL);
    CREATE TABLE IF NOT EXISTS faces(face_id INTEGER PRIMARY KEY AUTOINCREMENT, sha256 TEXT, bbox TEXT, size_px REAL,
      emb BLOB, cluster INT);
    CREATE TABLE IF NOT EXISTS persons(cluster INT PRIMARY KEY, name TEXT, centroid BLOB);
    CREATE TABLE IF NOT EXISTS faces_done(sha256 TEXT PRIMARY KEY);
    """)
    return c

def file_sha(c, p: Path):
    st = p.stat()
    r = c.execute("SELECT size, mtime, sha256 FROM path_cache WHERE path=?", (str(p),)).fetchone()
    if r and r[0] == st.st_size and abs(r[1] - st.st_mtime) < 1e-3:
        return r[2]
    h = T.sha256_file(p)
    c.execute("INSERT OR REPLACE INTO path_cache VALUES(?,?,?,?)", (str(p), st.st_size, st.st_mtime, h)); c.commit()
    return h

def resolve_files(args):
    """--files a b c | folder [--sample N] [--start NAME --count N]"""
    if getattr(args, "files", None):
        return [Path(f) for f in args.files if Path(f).suffix.lower() not in T.SKIP_EXT]
    folder = Path(args.folder).resolve()
    if getattr(args, "indexed", False):   # only frames already in the index for this folder
        c = db()
        return sorted(Path(r[0]) for r in c.execute("SELECT path FROM images WHERE folder=? AND path IS NOT NULL", (folder.name,)))
    files = T.list_images(folder)
    if getattr(args, "start", None):
        names = [f.stem for f in files]
        i = names.index(Path(args.start).stem)
        files = files[i:i + (args.count or 20)]
    elif getattr(args, "sample", 0):
        files = T.spread(files, args.sample)
    return files

def folder_of(p: Path): return p.parent.name

def embedding(sha):
    f = EMB / f"{sha[:24]}.npy"
    return np.load(f).astype(np.float32) if f.exists() else None

# ------------------------------------------------------------------ models
_models = {}
def siglip():
    if "siglip" not in _models: _models["siglip"] = T.Clip(CFG)
    return _models["siglip"]

class Aesthetic:
    """LAION improved aesthetic predictor v2 (MLP on OpenAI CLIP ViT-L/14 embeddings), ~1-10 scale."""
    def __init__(self):
        import torch, open_clip
        self.torch = torch
        self.dev = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model, _, self.pre = open_clip.create_model_and_transforms("ViT-L-14", pretrained="openai")
        self.model = self.model.to(self.dev).eval()
        sd = torch.load(HOME / "models/aesthetic/sac+logos+ava1-l14-linearMSE.pth", map_location="cpu")
        nn = torch.nn
        self.mlp = nn.Sequential(nn.Linear(768, 1024), nn.Dropout(0.2), nn.Linear(1024, 128), nn.Dropout(0.2),
                                 nn.Linear(128, 64), nn.Dropout(0.1), nn.Linear(64, 16), nn.Linear(16, 1))
        self.mlp.load_state_dict({k.replace("layers.", ""): v for k, v in sd.items()})
        self.mlp = self.mlp.to(self.dev).eval()
    def score(self, im):
        with self.torch.no_grad():
            f = self.model.encode_image(self.pre(im).unsqueeze(0).to(self.dev))
            f = f / f.norm(dim=-1, keepdim=True)
            return float(self.mlp(f.float())[0, 0].cpu())
def aesthetic():
    if "aes" not in _models: _models["aes"] = Aesthetic()
    return _models["aes"]

# ------------------------------------------------------------------ vision helpers
def _vision_handler(path):
    import Vision
    from Foundation import NSURL
    return Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)

def subject_box(path):
    """Apple Vision objectness saliency -> (x0,y0,x1,y1) normalised, top-left origin; None if nothing."""
    import Vision
    h = _vision_handler(path)
    req = Vision.VNGenerateObjectnessBasedSaliencyImageRequest.alloc().init()
    h.performRequests_error_([req], None)
    res = req.results() or []
    if not res: return None
    objs = res[0].salientObjects() or []
    if not objs: return None
    best = max(objs, key=lambda o: o.confidence() * o.boundingBox().size.width * o.boundingBox().size.height)
    b = best.boundingBox()
    x0, y0, w, hh = b.origin.x, b.origin.y, b.size.width, b.size.height
    return (x0, 1 - (y0 + hh), x0 + w, 1 - y0)

def sharpness_metrics(im: Image.Image, box):
    import cv2
    g = np.asarray(im.convert("L"), dtype=np.float32)
    H, W = g.shape
    def metr(a):
        lap = cv2.Laplacian(a, cv2.CV_32F).var()
        gx = cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3); gy = cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3)
        ten = float(np.mean(gx * gx + gy * gy))
        jxx, jyy, jxy = float(np.sum(gx * gx)), float(np.sum(gy * gy)), float(np.sum(gx * gy))
        tr = jxx + jyy; det = math.sqrt(max(0.0, (jxx - jyy) ** 2 + 4 * jxy * jxy))
        coh = det / tr if tr > 0 else 0.0
        return float(lap), ten, coh
    full = metr(g)[0]
    if box is None or (box[2] - box[0]) * (box[3] - box[1]) > 0.85:
        box = (0.25, 0.25, 0.75, 0.75)   # centre crop fallback
    x0, y0, x1, y1 = int(box[0] * W), int(box[1] * H), int(box[2] * W), int(box[3] * H)
    crop = g[max(0, y0):max(y0 + 8, y1), max(0, x0):max(x0 + 8, x1)]
    _, ten, coh = metr(crop)
    # sharpest detail inside the subject: 90th percentile of Laplacian variance over a 6x6 tile grid
    # (whole-crop variance under-rates large, smooth subjects such as close-up car panels)
    th_, tw_ = max(8, crop.shape[0] // 6), max(8, crop.shape[1] // 6)
    tiles = [cv2.Laplacian(crop[y:y + th_, x:x + tw_], cv2.CV_32F).var()
             for y in range(0, crop.shape[0] - th_ + 1, th_) for x in range(0, crop.shape[1] - tw_ + 1, tw_)]
    lap = float(np.percentile(tiles, 90)) if tiles else metr(crop)[0]
    return full, lap, ten, coh, box

def exif_batch(paths):
    if not paths: return {}
    out = {}
    for i in range(0, len(paths), 200):
        chunk = [str(p) for p in paths[i:i + 200]]
        r = subprocess.run([str(HOME / "bin/exiftool"), "-j", "-n", "-q", "-DateTimeOriginal", "-SubSecTimeOriginal",
                            "-Model", "-LensModel", "-FocalLength", "-ExposureTime", "-FNumber", "-ISO"] + chunk,
                           capture_output=True, text=True)
        for d in json.loads(r.stdout or "[]"):
            t = d.get("DateTimeOriginal")
            if t:
                t = str(t).replace(":", "-", 2).replace(" ", "T")
                if d.get("SubSecTimeOriginal") is not None: t += f".{str(d['SubSecTimeOriginal']).zfill(2)}"
            out[d["SourceFile"]] = dict(capture_time=t, camera=d.get("Model"), lens=d.get("LensModel"),
                                        focal=d.get("FocalLength"), exposure=d.get("ExposureTime"),
                                        fnumber=d.get("FNumber"), iso=d.get("ISO"))
    return out

def import_proposals(c, folder_name):
    """merge captions/keywords/labels from all proposals files (tagger + features) into the index"""
    kw, cap, lab = {}, {}, {}
    for f in sorted((OUT / folder_name).glob("*-proposals*.json")):
        try: data = json.load(open(f))
        except Exception: continue
        tagger_file = data.get("profile") in ("motorsport", "gig", "kyoto")
        for r in data.get("images", []):
            sha = r.get("sha256")
            if not sha: continue
            kw.setdefault(sha, set()).update(r.get("proposed_keywords", []) + r.get("tentative_keywords", []))
            if tagger_file and r.get("caption"): cap[sha] = r["caption"]
            if tagger_file and r.get("labels"): lab[sha] = r["labels"]
    for sha, k in kw.items():
        c.execute("UPDATE images SET keywords=?, caption=COALESCE(?, caption), labels=COALESCE(?, labels) WHERE sha256=?",
                  (json.dumps(sorted(k)), cap.get(sha), json.dumps(lab[sha]) if sha in lab else None, sha))
    c.commit()

def t_parse(s):
    from datetime import datetime
    if not s: return None
    try: return datetime.fromisoformat(s[:26]).timestamp()
    except Exception: return None

# ------------------------------------------------------------------ index
def cmd_index(args):
    c = db(); files = resolve_files(args); t0 = time.time(); n_new = 0
    todo = []
    for p in files:
        sha = file_sha(c, p)
        r = c.execute("SELECT aesthetic FROM images WHERE sha256=?", (sha,)).fetchone()
        if r and r[0] is not None and (EMB / f"{sha[:24]}.npy").exists() and not args.force:
            continue
        todo.append((p, sha))
    ex = exif_batch([p for p, _ in todo])
    for i, (p, sha) in enumerate(todo, 1):
        t1 = time.time()
        im, _ = T.load_preview(p, sha, CFG["preview_max_px"])
        cache = HOME / "cache" / f"{sha[:24]}.jpg"
        f = siglip().image(im)[0].detach().float().cpu().numpy()
        np.save(EMB / f"{sha[:24]}.npy", f.astype(np.float16))
        aes = aesthetic().score(im)
        box = subject_box(cache)
        full, lap, ten, coh, box = sharpness_metrics(im, box)
        e = ex.get(str(p), {})
        c.execute("""INSERT INTO images(sha256,path,rel_path,filename,folder,width,height,aesthetic,sharp_full,sharp_subject,
                     tenengrad_subject,coherence,subject_box,capture_time,camera,lens,focal,exposure,fnumber,iso,indexed_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(sha256) DO UPDATE SET path=excluded.path, rel_path=excluded.rel_path, filename=excluded.filename,
                     folder=excluded.folder, width=excluded.width, height=excluded.height, aesthetic=excluded.aesthetic,
                     sharp_full=excluded.sharp_full, sharp_subject=excluded.sharp_subject, tenengrad_subject=excluded.tenengrad_subject,
                     coherence=excluded.coherence, subject_box=excluded.subject_box, capture_time=excluded.capture_time,
                     camera=excluded.camera, lens=excluded.lens, focal=excluded.focal, exposure=excluded.exposure,
                     fnumber=excluded.fnumber, iso=excluded.iso, indexed_at=excluded.indexed_at""",
                  (sha, str(p), str(p.relative_to(p.parent.parent)), p.name, folder_of(p), im.width, im.height, aes, full, lap,
                   ten, coh, json.dumps([round(x, 4) for x in box]), e.get("capture_time"), e.get("camera"), e.get("lens"),
                   e.get("focal"), e.get("exposure"), e.get("fnumber"), e.get("iso"), time.time()))
        c.commit(); n_new += 1
        print(f"[{i}/{len(todo)}] {p.name} aes={aes:.2f} sharp_subj={lap:.0f} coh={coh:.2f} {time.time()-t1:.2f}s", flush=True)
    for fn in {folder_of(p) for p in files}: import_proposals(c, fn)
    el = time.time() - t0
    print(f"index: {n_new} new/updated of {len(files)} files in {el:.0f}s"
          + (f" ({el/max(1,n_new):.2f}s/new image)" if n_new else "") + f"; total indexed {c.execute('SELECT count(*) FROM images').fetchone()[0]}")

# ------------------------------------------------------------------ search
def cmd_search(args):
    c = db(); s = siglip()
    q = s.text([f"a photo of {args.query}"])[0].detach().float().cpu().numpy()
    sql = "SELECT sha256, filename, folder, capture_time, caption FROM images"
    rows = c.execute(sql + (" WHERE folder=?" if args.folder else ""), ((args.folder,) if args.folder else ())).fetchall()
    scale = float(s.model.logit_scale.exp().detach()); lb = getattr(s.model, "logit_bias", None); bias = float(lb.detach()) if lb is not None else 0.0
    res = []
    for sha, fn, fo, ct, cap in rows:
        e = embedding(sha)
        if e is None: continue
        cos = float(e @ q / (np.linalg.norm(e) * np.linalg.norm(q)))
        res.append((cos, 1 / (1 + math.exp(-(cos * scale + bias))), fn, fo, ct, cap or ""))
    res.sort(reverse=True)
    res = res[:args.top]
    (OUT / "search").mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", args.query.lower()).strip("-")[:50]
    with open(OUT / "search" / f"{slug}.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["rank", "cosine", "siglip_prob", "filename", "folder", "capture_time", "caption"])
        for i, r in enumerate(res, 1):
            w.writerow([i, round(r[0], 4), round(r[1], 4)] + list(r[2:]))
            print(f"{i:2d}. {r[0]:.3f}  {r[3]}/{r[2]}  {r[5][:70]}")
    print("csv:", OUT / "search" / f"{slug}.csv")

# ------------------------------------------------------------------ ocr
JA_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff\uff66-\uff9f]")
def ocr_lines(path, langs):
    import Vision
    h = _vision_handler(path)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(langs)
    req.setUsesLanguageCorrection_(True)
    try: req.setAutomaticallyDetectsLanguage_(True)
    except Exception: pass
    h.performRequests_error_([req], None)
    out = []
    for o in (req.results() or []):
        cand = o.topCandidates_(1)
        if cand: out.append((str(cand[0].string()), float(cand[0].confidence())))
    return out

def llm(prompt, model=None, num_predict=600, temperature=0.3):
    r = requests.post(CFG["ollama_url"] + "/api/generate", json={
        "model": model or FCFG["text_model"], "prompt": prompt, "stream": False, "think": False, "keep_alive": "5m",
        "options": {"temperature": temperature, "num_predict": num_predict, "num_ctx": 8192}}, timeout=600)
    r.raise_for_status()
    return re.sub(r"<think>.*?</think>", "", r.json().get("response", ""), flags=re.S).strip()

def cmd_ocr(args):
    c = db(); files = resolve_files(args); langs = args.langs.split(",") if args.langs else FCFG["ocr_langs"]
    side = {}; t0 = time.time()
    prev = {}
    if getattr(args, "resume", False):
        for fo in {folder_of(p) for p in files}:
            f = OUT / fo / "ocr.json"
            if f.exists(): prev.update({r["filename"]: r for r in json.load(open(f))})
    for i, p in enumerate(files, 1):
        if p.name in prev:
            side.setdefault(folder_of(p), []).append(prev[p.name]); continue
        t1 = time.time(); sha = file_sha(c, p)
        im, full = T.load_preview(p, sha, CFG["preview_max_px"], want_full=True)
        src = full or im
        tmp = HOME / "cache" / "_ocr"; tmp.mkdir(parents=True, exist_ok=True)
        W, H = src.size; boxes = [(0, 0, W, H)] + [(int(x * W / 2 * 0.9), int(y * H / 2 * 0.9), int(min(W, (x + 1.1) * W / 2)), int(min(H, (y + 1.1) * H / 2))) for y in range(2) for x in range(2)]
        seen, lines = set(), []
        for k, b in enumerate(boxes):
            tp = tmp / f"o{k}.jpg"; src.crop(b).save(tp, quality=92)
            try:
                for t, cf in ocr_lines(tp, langs):
                    key = t.strip()
                    if cf >= FCFG["ocr_min_conf"] and key and key not in seen:
                        seen.add(key); lines.append({"text": key, "conf": round(cf, 2)})
            finally: tp.unlink(missing_ok=True)
        text = "\n".join(l["text"] for l in lines)
        has_ja = bool(JA_RE.search(text))
        tr = ""
        if has_ja and args.translate:
            ja = "\n".join(l["text"] for l in lines if JA_RE.search(l["text"]))
            tr = llm("Translate these Japanese text fragments read from signs in a travel photo into natural English. "
                     "Keep line order, one translation per line, no commentary.\n\n" + ja, num_predict=400)
        rec = {"filename": p.name, "sha256": sha, "lines": lines, "has_japanese": has_ja, "translation_en": tr,
               "seconds": round(time.time() - t1, 2)}
        side.setdefault(folder_of(p), []).append(rec)
        c.execute("INSERT OR IGNORE INTO images(sha256,path,filename,folder) VALUES(?,?,?,?)", (sha, str(p), p.name, folder_of(p)))
        c.execute("UPDATE images SET ocr_text=?, ocr_ja=?, ocr_translation=? WHERE sha256=?", (text, int(has_ja), tr, sha)); c.commit()
        print(f"[{i}/{len(files)}] {p.name} {len(lines)} lines ja={has_ja} {rec['seconds']}s | {text[:60]!r} {('-> ' + tr[:60]) if tr else ''}", flush=True)
    for fo, recs in side.items():
        d = OUT / fo; d.mkdir(parents=True, exist_ok=True)
        f = d / "ocr.json"
        old = {r["sha256"]: r for r in (json.load(open(f)) if f.exists() else [])}
        old.update({r["sha256"]: r for r in recs})
        json.dump(list(old.values()), open(f, "w"), ensure_ascii=False, indent=1)
        print("sidecar:", f)
        # apply_c1 proposals: write text into IPTC Description ONLY where it is empty (checked at apply time)
        props = []
        for r in recs:
            good = [l["text"] for l in r["lines"] if l["conf"] >= 0.5 and len(l["text"]) >= 2][:12]
            if not good: continue
            desc = "Text in frame: " + " | ".join(good)
            if r.get("translation_en"): desc += " || EN: " + r["translation_en"].replace("\n", " | ")
            props.append(dict(filename=r["filename"], sha256=r["sha256"], needs_review=False, proposed_keywords=[], description=desc[:400]))
        fp = [p for p in files if folder_of(p) == fo]
        write_proposals(str(fp[0].parent) if fp else fo, "ocr", props)
    print(f"ocr: {len(files)} images in {time.time()-t0:.0f}s")

# ------------------------------------------------------------------ sharpness
def pct_rank(vals):
    a = np.asarray(vals, dtype=float); order = a.argsort().argsort()
    return (order / max(1, len(a) - 1)).tolist()

def quality_rows(c, folder, shas=None):
    rows = c.execute("SELECT sha256, filename, sharp_subject, coherence, aesthetic, capture_time, folder FROM images "
                     "WHERE folder=? AND sharp_subject IS NOT NULL", (folder,)).fetchall()
    if not rows: return []
    pct = pct_rank([math.log1p(r[2]) for r in rows])
    apct = pct_rank([r[4] for r in rows])
    out = []
    for r, p, ap in zip(rows, pct, apct):
        sha, fn, lap, coh, aes, ct, fo = r
        flag = None
        if p >= FCFG["sharp_pct"] and lap >= FCFG["sharp_abs_min"]: flag = "Sharp"
        elif p <= FCFG["blur_pct"] or lap <= FCFG["blur_abs_max"]:
            flag = "Motion Blur" if coh >= FCFG["motion_coherence"] else "Soft Focus"
        out.append(dict(sha256=sha, filename=fn, sharp_subject=round(lap, 1), coherence=round(coh, 3), sharp_pct=round(p, 3),
                        aesthetic=round(aes, 3), aes_pct=round(ap, 3), capture_time=ct, folder=fo, flag=flag))
    if shas: out = [o for o in out if o["sha256"] in shas]
    return out

def write_proposals(folder_path, name, recs):
    """apply_c1-compatible proposals file"""
    d = OUT / Path(folder_path).name; d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}-proposals.json"
    json.dump({"folder": str(folder_path), "profile": name, "images": recs}, open(f, "w"), indent=1)
    return f

def folder_path_for(c, folder):
    r = c.execute("SELECT path FROM images WHERE folder=? LIMIT 1", (folder,)).fetchone()
    return str(Path(r[0]).parent) if r else folder

def target_folders(c, args):
    if getattr(args, "files", None):
        shas = {file_sha(c, Path(f)) for f in args.files}
        fos = sorted({folder_of(Path(f)) for f in args.files})
        return fos, shas
    return [Path(args.folder).name], None

def cmd_sharpness(args):
    c = db(); fos, shas = target_folders(c, args)
    for fo in fos:
        rows = quality_rows(c, fo, shas)
        d = OUT / fo; d.mkdir(parents=True, exist_ok=True)
        with open(d / "sharpness.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
        recs = [dict(filename=r["filename"], sha256=r["sha256"], needs_review=False,
                     proposed_keywords=[f"General/Quality/{r['flag']}"] if r["flag"] else []) for r in rows]
        pf = write_proposals(folder_path_for(c, fo), "sharpness", recs)
        from collections import Counter
        print(fo, dict(Counter(r["flag"] for r in rows)), "->", d / "sharpness.csv", pf)

# ------------------------------------------------------------------ bursts
def cmd_bursts(args):
    c = db(); fos, shas = target_folders(c, args)
    for fo in fos:
        rows = quality_rows(c, fo)
        if shas: rows = [r for r in rows if r["sha256"] in shas]   # selection: group only among selected frames
        rows.sort(key=lambda r: (t_parse(r["capture_time"]) or 0, r["filename"]))
        groups, cur = [], []
        prev_e, prev_t = None, None
        for r in rows:
            e = embedding(r["sha256"]); t = t_parse(r["capture_time"])
            e = e / np.linalg.norm(e)
            cos = float(e @ prev_e) if prev_e is not None else 0.0
            anchor = cur[0]["_e"] if cur else None
            same = prev_e is not None and cos >= FCFG["burst_min_cos"] and (anchor is None or float(e @ anchor) >= FCFG["burst_anchor_cos"]) and (
                t is None or prev_t is None or abs(t - prev_t) <= FCFG["burst_gap_s"])
            r["cos_prev"] = round(cos, 3); r["_e"] = e
            if same: cur.append(r)
            else:
                if len(cur) > 1: groups.append(cur)
                cur = [r]
            prev_e, prev_t = e, t
        if len(cur) > 1: groups.append(cur)
        if shas: groups = [g for g in groups if any(r["sha256"] in shas for r in g)]
        out = []
        for gi, g in enumerate(groups, 1):
            best = max(g, key=lambda r: 0.6 * r["sharp_pct"] + 0.4 * r["aes_pct"])
            for r in g:
                out.append(dict(group=gi, size=len(g), best=r is best, filename=r["filename"], capture_time=r["capture_time"],
                                cos_prev=r["cos_prev"], sharp_pct=r["sharp_pct"], aesthetic=r["aesthetic"], sha256=r["sha256"]))
        d = OUT / fo; d.mkdir(parents=True, exist_ok=True)
        with open(d / "bursts.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["group", "size", "best", "filename", "capture_time", "cos_prev", "sharp_pct", "aesthetic", "sha256"])
            w.writeheader(); w.writerows(out)
        recs = [dict(filename=r["filename"], sha256=r["sha256"], needs_review=False, proposed_keywords=["General/Burst Best"])
                for r in out if r["best"]]
        pf = write_proposals(folder_path_for(c, fo), "bursts", recs)
        print(f"{fo}: {len(rows)} indexed frames -> {len(groups)} burst groups ({sum(len(g) for g in groups)} frames); "
              f"csv {d/'bursts.csv'}; proposals {pf}")
        for gi, g in enumerate(groups[:8], 1):
            print(f"  group {gi}: " + ", ".join(("*" if r is max(g, key=lambda r: 0.6*r['sharp_pct']+0.4*r['aes_pct']) else "") + r["filename"] for r in g))

# ------------------------------------------------------------------ rank
def score_bucket(a):
    return "8+" if a >= 8 else ("Below 5" if a < 5 else str(int(a)))

def cmd_rank(args):
    c = db(); fos, shas = target_folders(c, args)
    for fo in fos:
        rows = quality_rows(c, fo, shas)
        for r in rows: r["rank_score"] = round(r["aesthetic"] + (0.5 * (r["sharp_pct"] - 0.5) if args.blend else 0), 3)
        rows.sort(key=lambda r: -r["rank_score"])
        d = OUT / fo; d.mkdir(parents=True, exist_ok=True)
        cap = dict(c.execute("SELECT sha256, caption FROM images WHERE folder=?", (fo,)).fetchall())
        fields = ["rank", "filename", "rank_score", "aesthetic", "sharp_pct", "flag", "capture_time", "caption", "sha256"]
        def dump(path, rr):
            with open(path, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore"); w.writeheader()
                for i, r in enumerate(rr, 1): w.writerow(dict(r, rank=i, caption=cap.get(r["sha256"]) or ""))
        dump(d / "rank.csv", rows); dump(d / f"rank-top{args.top}.csv", rows[:args.top])
        recs = [dict(filename=r["filename"], sha256=r["sha256"], needs_review=False,
                     proposed_keywords=[f"General/Score/{score_bucket(r['aesthetic'])}"]) for r in rows]
        pf = write_proposals(folder_path_for(c, fo), "rank", recs)
        print(f"{fo}: {len(rows)} ranked; aesthetic min/median/max = {min(r['aesthetic'] for r in rows):.2f}/"
              f"{np.median([r['aesthetic'] for r in rows]):.2f}/{max(r['aesthetic'] for r in rows):.2f}; top{args.top} -> {d/f'rank-top{args.top}.csv'}")
        for i, r in enumerate(rows[:min(5, args.top)], 1):
            print(f"  {i}. {r['filename']} aes={r['aesthetic']:.2f} sharp_pct={r['sharp_pct']:.2f} {r['flag'] or ''}")

# ------------------------------------------------------------------ captions (Salamander sidecar)
def cmd_captions(args):
    """Export-only: drafts captions for files under '[03] Photos/Camera Exports'. Never part of catalog tagging."""
    c = db(); files = resolve_files(args); out = []
    bad = [p for p in files if "/Camera Exports/" not in str(p)]
    if bad:
        sys.exit(f"captions is export-only: {len(bad)} file(s) are not under 'Camera Exports' (e.g. {bad[0]})")
    files = [p for p in files if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic")] or files
    prof = T.PROFILES[args.profile]
    T.CONTEXT = args.context or ""
    for i, p in enumerate(files, 1):
        t1 = time.time(); sha = file_sha(c, p)
        im, _ = T.load_preview(p, sha, CFG["preview_max_px"])
        vl = T.vlm(CFG, im, prof["caption_prompt"])
        cap = (vl.get("caption") or "").strip()
        c.execute("INSERT OR IGNORE INTO images(sha256,path,filename,folder) VALUES(?,?,?,?)", (sha, str(p), p.name, folder_of(p)))
        c.execute("UPDATE images SET caption=? WHERE sha256=?", (cap, sha)); c.commit()
        out.append({"filename": p.name, "folder": folder_of(p), "path": str(p), "sha256": sha, "caption_draft": cap,
                    "status": "draft - needs human approval before posting"})
        print(f"[{i}/{len(files)}] {p.name} {time.time()-t1:.1f}s | {cap}")
    d = OUT / "salamander"; d.mkdir(parents=True, exist_ok=True)
    f = d / f"captions-{time.strftime('%Y%m%d-%H%M%S')}.json"
    json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": CFG["vlm_model"], "items": out}, open(f, "w"), indent=1, ensure_ascii=False)
    print("sidecar:", f)

# ------------------------------------------------------------------ journal / ask
def folder_context(c, fo, limit=25):
    rows = c.execute("SELECT filename, capture_time, camera, lens, focal, aesthetic, caption, keywords, ocr_text, ocr_translation, sharp_subject "
                     "FROM images WHERE folder=? ORDER BY capture_time", (fo,)).fetchall()
    if not rows: return None, ""
    times = [r[1] for r in rows if r[1]]
    from collections import Counter
    kw = Counter(k.split("/")[-1] if "Car Number" not in k else k.split("/")[-1] for r in rows for k in json.loads(r[7] or "[]")
                 if "Car Number" not in k)
    nums = Counter(k.split("/")[-1] for r in rows for k in json.loads(r[7] or "[]") if "Car Number" in k)
    top = sorted(rows, key=lambda r: -(r[5] or 0))[:limit]
    lines = [f"Folder: {fo}", f"Frames indexed: {len(rows)}",
             f"Time span: {times[0] if times else '?'} to {times[-1] if times else '?'}",
             f"Camera/lens: {Counter(r[2] for r in rows).most_common(1)[0][0]} / {Counter(r[3] for r in rows).most_common(2)}",
             f"Keyword counts: {dict(kw.most_common(15))}", f"Car numbers seen: {dict(nums.most_common(15))}",
             "Best-scoring frames (time | file | aesthetic | caption | text seen):"]
    for r in sorted(top, key=lambda r: r[1] or ""):
        txt = (r[9] or r[8] or "").replace("\n", " / ")[:120]
        lines.append(f"- {r[1]} | {r[0]} | {r[5]:.1f} | {r[6] or ''} | {txt}")
    return rows, "\n".join(lines)

def cmd_journal(args):
    c = db(); fo = Path(args.folder).name
    import_proposals(c, fo)
    rows, ctx = folder_context(c, fo)
    if not rows: sys.exit(f"nothing indexed for {fo}; run: tag index <folder>")
    t0 = time.time()
    md = llm("You are writing a short photo-trip journal entry in British English for the photographer, from metadata of one "
             "shooting day. Use only the facts given; do not invent names, places or results. Format: a markdown title with "
             "the date, a 3-5 sentence summary, then 'Highlights' (3-6 bullets citing filenames), then 'Stats' bullets.\n\n" + ctx,
             num_predict=1100)
    d = OUT / fo; d.mkdir(parents=True, exist_ok=True)
    (d / "journal.md").write_text(md + "\n")
    print(md); print(f"\njournal -> {d/'journal.md'} ({time.time()-t0:.0f}s)")

def cmd_ask(args):
    c = db(); s = siglip()
    for (fo,) in c.execute("SELECT DISTINCT folder FROM images").fetchall(): import_proposals(c, fo)
    q = s.text([f"a photo of {args.question}"])[0].detach().float().cpu().numpy()
    rows = c.execute("SELECT sha256, filename, folder, capture_time, aesthetic, caption, keywords, ocr_text, ocr_translation FROM images").fetchall()
    scored = []
    words = [w for w in re.findall(r"[a-z0-9]+", args.question.lower()) if len(w) > 2]
    for r in rows:
        e = embedding(r[0]); sim = float(e @ q / (np.linalg.norm(e) * np.linalg.norm(q))) if e is not None else 0
        blob = " ".join(str(x or "") for x in r[5:]).lower()
        kw = sum(1 for w in words if w in blob)
        scored.append((sim + 0.03 * kw, r))
    scored.sort(key=lambda x: -x[0])
    from collections import Counter
    folders = Counter(r[2] for r in rows)
    ctx = [f"Index covers {len(rows)} frames in folders: {dict(folders)}"]
    for fo in folders:
        kc = Counter(k for r in rows if r[2] == fo for k in json.loads(r[6] or "[]"))
        ctx.append(f"{fo}: keyword counts {dict(kc.most_common(25))}")
    ctx.append("Most relevant frames (score | folder/file | time | aesthetic | caption | keywords | text in frame):")
    for sc, r in scored[:args.top]:
        ctx.append(f"- {sc:.3f} | {r[2]}/{r[1]} | {r[3]} | {(r[4] or 0):.1f} | {r[5] or ''} | {','.join(json.loads(r[6] or '[]'))} | {(r[8] or r[7] or '')[:100]}")
    t0 = time.time()
    ans = llm("Answer the photographer's question using ONLY the catalogue context below (local photo index). Cite filenames. "
              "Only list frames whose caption, keywords or text actually support the answer; if the context cannot answer it, say so. British English, concise.\n\nContext:\n" + "\n".join(ctx) +
              f"\n\nQuestion: {args.question}", num_predict=500, temperature=0.2)
    print(ans); print(f"\n({time.time()-t0:.0f}s, {len(rows)} frames in index)")

# ------------------------------------------------------------------ faces
ARC_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]], np.float32)
_face_sess = {}
def arcface():
    if "s" not in _face_sess:
        import onnxruntime as ort
        m = HOME / "models/faces/w600k_mbf.onnx"
        _face_sess["s"] = ort.InferenceSession(str(m), providers=["CoreMLExecutionProvider", "CPUExecutionProvider"])
    return _face_sess["s"]

def detect_faces(path, W, H):
    import Vision
    h = _vision_handler(path)
    rect = Vision.VNDetectFaceRectanglesRequest.alloc().init()
    h.performRequests_error_([rect], None)
    rects = list(rect.results() or [])
    if not rects: return []
    req = Vision.VNDetectFaceLandmarksRequest.alloc().init()
    req.setInputFaceObservations_(rects)
    _vision_handler(path).performRequests_error_([req], None)
    obs = list(req.results() or []) or rects   # landmarks can fail on stage make-up/lighting -> box-only fallback
    out = []
    for o in obs:
        b = o.boundingBox(); lm = o.landmarks() if hasattr(o, 'landmarks') else None
        box = [b.origin.x * W, (1 - b.origin.y - b.size.height) * H, b.size.width * W, b.size.height * H]
        if lm is None:   # no landmarks (heavy make-up, profile, stage light): approximate from the box
            x, y, w, hh = box
            out.append((box, np.array([[x + p[0] / 112 * w, y + p[1] / 112 * hh] for p in ARC_DST], np.float32), False)); continue
        def pts(region):
            if region is None: return None
            n = region.pointCount(); P = region.normalizedPoints()
            return np.array([[(b.origin.x + P[i].x * b.size.width) * W, (1 - (b.origin.y + P[i].y * b.size.height)) * H] for i in range(n)])
        le, re_, nose, lips = pts(lm.leftEye()), pts(lm.rightEye()), pts(lm.nose()), pts(lm.outerLips())
        if any(x is None or len(x) == 0 for x in (le, re_, nose, lips)):
            x, y, w, hh = box
            out.append((box, np.array([[x + p[0] / 112 * w, y + p[1] / 112 * hh] for p in ARC_DST], np.float32), False)); continue
        eyes = sorted([le.mean(0), re_.mean(0)], key=lambda p: p[0])
        mouth = sorted([lips[lips[:, 0].argmin()], lips[lips[:, 0].argmax()]], key=lambda p: p[0])
        nose_tip = nose[nose[:, 1].argmax()]
        five = np.array([eyes[0], eyes[1], nose_tip, mouth[0], mouth[1]], np.float32)
        out.append((box, five, True))
    return out

def embed_face(img_rgb, five):
    import cv2
    M, _ = cv2.estimateAffinePartial2D(five, ARC_DST, method=cv2.LMEDS)
    crop = cv2.warpAffine(img_rgb, M, (112, 112), borderValue=0)
    x = ((crop.astype(np.float32) - 127.5) / 127.5).transpose(2, 0, 1)[None]
    s = arcface(); e = s.run(None, {s.get_inputs()[0].name: x})[0][0]
    return e / np.linalg.norm(e), crop

def cmd_faces(args):
    c = db(); files = resolve_files(args) if (args.folder or args.files) else []
    t0 = time.time(); nf = 0
    for i, p in enumerate(files, 1):
        sha = file_sha(c, p)
        if c.execute("SELECT 1 FROM faces_done WHERE sha256=?", (sha,)).fetchone(): continue
        t1 = time.time()
        im, full = T.load_preview(p, sha, CFG["preview_max_px"], want_full=True)
        src = full or im
        tp = HOME / "cache" / "_faces.jpg"; src.save(tp, quality=92)
        arr = np.asarray(src)
        dets = detect_faces(tp, src.width, src.height); tp.unlink(missing_ok=True)
        kept = 0
        for box, five, _aligned in dets:
            if min(box[2], box[3]) < FCFG["face_min_px"]: continue
            e, crop = embed_face(arr, five)
            cur = c.execute("INSERT INTO faces(sha256,bbox,size_px,emb) VALUES(?,?,?,?)",
                            (sha, json.dumps([round(v) for v in box]), min(box[2], box[3]), e.astype(np.float32).tobytes()))
            Image.fromarray(crop).save(FACEDIR / f"{cur.lastrowid}.jpg"); kept += 1
        c.execute("INSERT OR IGNORE INTO faces_done VALUES(?)", (sha,)); c.commit(); nf += kept
        print(f"[{i}/{len(files)}] {p.name}: {len(dets)} faces detected, {kept} embedded ({time.time()-t1:.2f}s)", flush=True)
    if files: print(f"faces: {nf} new faces from {len(files)} images in {time.time()-t0:.0f}s")
    cluster_faces(c)

def cluster_faces(c):
    from sklearn.cluster import DBSCAN
    rows = c.execute("SELECT face_id, emb, sha256 FROM faces").fetchall()
    if not rows: print("no faces"); return
    X = np.stack([np.frombuffer(r[1], np.float32) for r in rows])
    lab = DBSCAN(eps=FCFG["face_eps"], min_samples=FCFG["face_min_samples"], metric="cosine").fit(X).labels_
    named = [(r[0], r[1], np.frombuffer(r[2], np.float32)) for r in c.execute("SELECT cluster,name,centroid FROM persons WHERE name IS NOT NULL AND centroid IS NOT NULL")]
    c.execute("DELETE FROM persons WHERE name IS NULL")
    # stable numbering: largest group first
    from collections import Counter
    order = [l for l, _ in Counter(l for l in lab if l >= 0).most_common()]
    remap = {l: i + 1 for i, l in enumerate(order)}
    for (fid, _, _), l in zip(rows, lab):
        c.execute("UPDATE faces SET cluster=? WHERE face_id=?", (remap.get(l, -1), fid))
    c.execute("DELETE FROM persons")
    for l, k in remap.items():
        cen = X[lab == l].mean(0); cen /= np.linalg.norm(cen)
        name = None
        for _, nm, nc in named:
            if float(cen @ nc) > 0.6: name = nm
        c.execute("INSERT OR REPLACE INTO persons VALUES(?,?,?)", (k, name, cen.astype(np.float32).tobytes()))
    c.commit()
    # outputs: csv + contact sheets
    d = OUT / "faces"; d.mkdir(parents=True, exist_ok=True)
    for f in d.glob("person_*.jpg"): f.unlink()
    rows = c.execute("SELECT f.face_id, f.cluster, i.folder, i.filename, f.bbox, p.name FROM faces f LEFT JOIN images i ON i.sha256=f.sha256 "
                     "LEFT JOIN persons p ON p.cluster=f.cluster ORDER BY f.cluster, f.face_id").fetchall()
    with open(d / "faces.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["person", "local_name", "folder", "filename", "bbox", "face_id"])
        for fid, cl, fo, fn, bb, nm in rows:
            w.writerow([f"Person {cl:03d}" if cl > 0 else "unclustered", nm or "", fo, fn, bb, fid])
    for k in remap.values():
        ids = [r[0] for r in rows if r[1] == k][:24]
        sheet = Image.new("RGB", (112 * min(8, len(ids)), 112 * math.ceil(len(ids) / 8)))
        for j, fid in enumerate(ids):
            sheet.paste(Image.open(FACEDIR / f"{fid}.jpg"), ((j % 8) * 112, (j // 8) * 112))
        sheet.save(d / f"person_{k:03d}.jpg")
    cnt = Counter(r[1] for r in rows)
    print(f"faces: {len(rows)} total, {len(remap)} groups, {cnt.get(-1,0)} unclustered; sizes " +
          ", ".join(f"Person {k:03d}={cnt[k]}" for k in sorted(remap.values())) + f"; csv {d/'faces.csv'}")

def cmd_name_person(args):
    c = db()
    if not c.execute("SELECT 1 FROM persons WHERE cluster=?", (args.person,)).fetchone(): sys.exit("no such group")
    c.execute("UPDATE persons SET name=? WHERE cluster=?", (args.name, args.person)); c.commit()
    print(f"Person {args.person:03d} -> {args.name!r} (stored only in {DB})")

# ------------------------------------------------------------------ schedule
def cmd_schedule(args):
    import schedule_tag as ST
    c = db(); files = resolve_files(args)
    folder = files[0].parent if files else Path(args.folder)
    off = args.camera_offset or CFG.get("camera_utc_offset")
    data = ST.schedule_proposals(files, args.schedule, folder, off, sha_fn=lambda p: file_sha(c, p))
    d = OUT / folder.name; d.mkdir(parents=True, exist_ok=True)
    f = d / "schedule-proposals.json"; json.dump(data, open(f, "w"), ensure_ascii=False, indent=1)
    from collections import Counter
    ev = Counter(r.get("event") for r in data["images"])
    print(f"schedule: {len(files)} frames; events {dict(ev)}; needs_review {sum(r['needs_review'] for r in data['images'])}; -> {f}")
    return f

# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(prog="tag", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    def sel(p, folder_required=False):
        p.add_argument("folder", nargs="?" if not folder_required else None)
        p.add_argument("--files", nargs="+"); p.add_argument("--sample", type=int, default=0)
        p.add_argument("--start"); p.add_argument("--count", type=int, default=0)
        p.add_argument("--indexed", action="store_true", help="re-use the frames already indexed for this folder")
    p = sp.add_parser("index"); sel(p); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_index)
    p = sp.add_parser("search"); p.add_argument("query"); p.add_argument("--folder"); p.add_argument("--top", type=int, default=10); p.set_defaults(fn=cmd_search)
    p = sp.add_parser("ocr"); sel(p); p.add_argument("--langs"); p.add_argument("--resume", action="store_true"); p.add_argument("--translate", action="store_true"); p.set_defaults(fn=cmd_ocr)
    p = sp.add_parser("sharpness"); sel(p); p.set_defaults(fn=cmd_sharpness)
    p = sp.add_parser("bursts"); sel(p); p.set_defaults(fn=cmd_bursts)
    p = sp.add_parser("rank"); sel(p); p.add_argument("--top", type=int, default=20); p.add_argument("--blend", action="store_true"); p.set_defaults(fn=cmd_rank)
    p = sp.add_parser("captions"); sel(p); p.add_argument("--profile", default="motorsport", choices=list(T.PROFILES)); p.add_argument("--context", default=""); p.set_defaults(fn=cmd_captions)
    p = sp.add_parser("schedule"); sel(p); p.add_argument("--schedule", default=str(HOME / "schedules/example.json"))
    p.add_argument("--camera-offset", help='camera UTC offset if EXIF OffsetTimeOriginal is missing, e.g. +00:00; prefix "force:" to override EXIF'); p.set_defaults(fn=cmd_schedule)
    p = sp.add_parser("journal"); p.add_argument("folder"); p.set_defaults(fn=cmd_journal)
    p = sp.add_parser("ask"); p.add_argument("question"); p.add_argument("--top", type=int, default=25); p.set_defaults(fn=cmd_ask)
    p = sp.add_parser("faces"); sel(p); p.set_defaults(fn=cmd_faces)
    p = sp.add_parser("name-person"); p.add_argument("person", type=int); p.add_argument("name"); p.set_defaults(fn=cmd_name_person)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
