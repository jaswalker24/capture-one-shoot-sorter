#!/usr/bin/env python3
"""Generalised scene / subject keyword pass for Trips Local AI.

Multi-label SigLIP (local, no caption model). Proposes General/Scene/* keywords
with per-label thresholds, a cap per frame, and conservative defaults (precision
over recall). Writes proposals only — it never talks to Capture One.

Image features come from the existing index embeddings when present, otherwise
from the cached preview (same SigLIP model as the tagger). MP4s are ignored.
"""
from __future__ import annotations

import argparse, csv, json, os, sys, time
from collections import Counter
from pathlib import Path

HOME = Path.home() / "localai"

# Heavy imports (tagger, torch/SigLIP, preview cache) are lazy so unit tests can
# import the vocabulary and selection logic without the localai runtime.

OUT = HOME / "out"
EMB = HOME / "index" / "siglip"
PREFIX = "General/Scene/"
DEFAULT_MAX_LABELS = 6
DEFAULT_TH = 0.88


def _runtime():
    """Load tagger/numpy/shacache once the pass actually runs."""
    sys.path.insert(0, str(HOME))
    os.environ.setdefault("HF_HOME", str(HOME / "models" / "hf"))
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    import numpy as np
    import tagger as T
    import shacache
    return np, T, shacache

# name -> (positive prompt, negative prompt, threshold)
# Thresholds are conservative starting points; tune after a sample review.
# Prompts describe the photograph, not the keyword name, so SigLIP can score them.
VOCAB: dict[str, tuple[str, str, float]] = {
    "Architecture": (
        "a photo of a building, temple, shrine, facade or architectural structure as the main subject",
        "a photo of a person, a car, a stage or a landscape with no building as the subject",
        0.88,
    ),
    "Street": (
        "a photo of a city street, shopping street, pavement, or a street seen through a gate, with people or shops",
        "a photo of a concert stage, a race track, an interior room, or a close-up that is not a street",
        0.88,
    ),
    "Alley": (
        "a photo of a narrow back alley or passageway between ordinary buildings, not a famous gate or a stage",
        "a photo of an open street, a temple gate, a concert stage, or a place that is not a narrow alley",
        0.92,
    ),
    "Cityscape": (
        "a wide photo of a city skyline or urban cityscape",
        "a photo that is not a city skyline or wide city view",
        0.90,
    ),
    "Landscape": (
        "a wide landscape photo of countryside, fields or hills, with no race track, cars or buildings as the subject",
        "a photo of a race track, cars, a city, an interior, a person, or a close subject rather than a landscape",
        0.92,
    ),
    "Nature": (
        "a nature photograph of a garden, forest, trees or plants filling the frame, with no car, race track or concert",
        "a photo of a racing car, a race track, a concert, a street, a person, or a man-made scene even if grass or trees are in the background",
        0.94,
    ),
    "Sky": (
        "a photo in which the sky, clouds or a dramatic sky is a main subject",
        "a photo in which the sky is absent or only a small background behind a car, person or building",
        0.94,
    ),
    "Water": (
        "a photo in which a sea, lake, river, canal or pond of water is clearly visible as a main subject",
        "a photo with no visible body of water, including a building or temple with no water in frame",
        0.94,
    ),
    "River": (
        "a photo of a river or canal with water clearly visible",
        "a photo that is not of a river or canal",
        0.90,
    ),
    "Mountain": (
        "a photo of a mountain, mountain range or large hill as a main subject",
        "a photo with no mountain or large hill",
        0.90,
    ),
    "Garden": (
        "a photo of a garden, landscaped grounds, ornamental trees and a pond or raked gravel",
        "a photo of a race track, a forest wilderness, a street or a place that is not a garden",
        0.88,
    ),
    "Bridge": (
        "a photo of a bridge as a main subject",
        "a photo that does not show a bridge",
        0.90,
    ),
    "Interior": (
        "a photo taken indoors, inside a room, hall, covered arcade, temple interior or venue",
        "an outdoor photo taken outside under the sky",
        0.90,
    ),
    "Shop": (
        "a photo of a shop front, store interior, or window display",
        "a photo that is not of a shop or store",
        0.90,
    ),
    "Market": (
        "a photo of a street market, food market or market stalls",
        "a photo that is not of a market",
        0.90,
    ),
    "Station": (
        "a photo of a train station, railway platform or transit concourse",
        "a photo that is not of a station or platform",
        0.90,
    ),
    "Night": (
        "an outdoor night photograph with a dark sky and artificial lights, lanterns, street lights or floodlights",
        "a photo taken in daylight, or an indoor concert or stage scene with no night sky",
        0.90,
    ),
    "Sunset": (
        "a photo taken at sunset or sunrise with a colourful sky and low sun",
        "a photo taken in the middle of the day, at night, or lit only by artificial lights",
        0.92,
    ),
    "Rain": (
        "a photo taken in the rain, with wet ground, raindrops or umbrellas",
        "a photo taken in dry weather with no rain",
        0.90,
    ),
    "Temple": (
        "a photo of a Japanese or East Asian temple building, temple roof, temple gate or temple grounds",
        "a photo of a concert, a race track, or a place that is not a temple",
        0.88,
    ),
    "Shrine": (
        "a photo of a Japanese shrine, a torii gate, or a large shrine or temple gate such as a mon",
        "a photo of a concert, a race track, or a place that is not a shrine or shrine gate",
        0.85,
    ),
    "Food": (
        "a photo of a meal, dishes, drinks or food as the main subject",
        "a photo that is not of food or a meal",
        0.90,
    ),
    "Signage": (
        "a photo of a large sign, lantern with writing, billboard or shop sign as a main subject",
        "a photo in which signs are absent or only a tiny background detail",
        0.88,
    ),
    "Neon": (
        "a photo of neon signs or colourful neon lights on a city street",
        "a photo without neon signs or neon lighting",
        0.90,
    ),
    "Transport": (
        "a photo of public transport, a train, a bus, or a transit scene",
        "a photo of a racing car on a track, a concert, or a scene with no transport",
        0.92,
    ),
    "Vehicle": (
        "a photo of a car, racing car, van or truck as a main subject",
        "a photo that is not of a car or other road vehicle",
        0.85,
    ),
    "Motorbike": (
        "a photo of a motorcycle or motorbike as a main subject",
        "a photo that is not of a motorcycle or motorbike",
        0.88,
    ),
    "Train": (
        "a photo of a train, railway carriage or locomotive",
        "a photo that is not of a train",
        0.88,
    ),
    "People": (
        "a photo in which one or more people are a clear main subject",
        "a photo of a garden, a building, a race track, a car, or a place with no person as the subject",
        0.92,
    ),
    "Portrait": (
        "a close-up portrait of a human face or a person, with the face filling much of the frame",
        "a photo of a car, a vehicle, a race track, an animal, or a wide scene that is not a portrait of a person",
        0.90,
    ),
    "Crowd": (
        "a photo of a crowd of many people, an audience or a packed gathering of humans",
        "a photo of racing cars, one or two people, or a scene with no crowd of people",
        0.95,
    ),
    "Animal": (
        "a photo of an animal, bird, fish, koi or pet as the main subject",
        "a photo with no animal",
        0.80,
    ),
    "Stage": (
        "a photo of a concert or performance stage with lights and performers",
        "a photo that is not of a performance stage",
        0.90,
    ),
    "Concert": (
        "a photo of a live music performance, a concert stage, or a musician performing under stage lights",
        "a photo of a race track, a street, a temple, or a place that is not a live concert",
        0.82,
    ),
    "Festival": (
        "a photo of an outdoor festival, fair or public celebration with crowds and stalls or stages",
        "a photo that is not of a festival or fair",
        0.90,
    ),
    "Motorsport": (
        "a photo of a racing car or racing cars on a race track, or motorsport action at a circuit",
        "a photo of a concert, a street, a temple, a garden, or a scene with no racing car and no race track",
        0.80,
    ),
    "Abstract": (
        "an abstract photograph of pure colour, texture or geometry, with no car, person, building or landscape visible",
        "a photo of a car, person, race track, concert, building or any recognisable subject, including a blurred one",
        0.97,
    ),
    "Detail": (
        "a tight close-up detail photo of a small object, texture, badge or part of something",
        "a wide or normal photo of a whole scene, person or vehicle",
        0.92,
    ),
}

# If the child is applied, also apply the parent(s) when the parent score is not tiny.
IMPLIES: dict[str, list[str]] = {
    "Motorbike": ["Vehicle"],
    "Train": ["Transport"],
    "Motorsport": ["Vehicle"],
    "River": ["Water"],
    "Portrait": ["People"],
    "Crowd": ["People"],
    "Stage": ["Concert"],
    "Garden": ["Nature"],
    "Alley": ["Street"],
}
# Implied parent is skipped if its own score is below this (stops Alley->Street on a 0.00 Street).
IMPLIES_MIN_SCORE = 0.25

# Keep only the higher-scoring label when both clear the threshold.
EXCLUSIVE: list[tuple[str, str]] = [
    ("Concert", "Motorsport"),
    ("Interior", "Landscape"),
    ("Interior", "Cityscape"),
    ("Portrait", "Crowd"),
    ("Sunset", "Night"),
]

# If the source label scores at least SUPPRESS_AT, drop the victims even if they also scored high.
# This stops grass-behind-a-racing-car being tagged Nature, or a car being tagged Portrait.
SUPPRESS_AT = 0.55
SUPPRESS: dict[str, list[str]] = {
    "Motorsport": ["Nature", "Landscape", "Garden", "Portrait", "People", "Crowd",
                   "Abstract", "Alley", "Street", "Concert", "Stage", "Temple", "Shrine", "Water"],
    "Vehicle": ["Nature", "Landscape", "Garden", "Portrait", "People", "Abstract",
                "Alley", "Street", "Concert", "Stage", "Water"],
    "Concert": ["Motorsport", "Vehicle", "Temple", "Nature", "Landscape", "Water", "Shrine", "Night", "Abstract"],
    "Stage": ["Motorsport", "Vehicle", "Nature", "Landscape", "Temple", "Water", "Shrine", "Night"],
    "Temple": ["Concert", "Stage", "Motorsport"],
    "Nature": ["People", "Crowd", "Transport", "Abstract", "Concert"],
    "Garden": ["People", "Crowd", "Transport", "Abstract", "Concert"],
}

# Only apply these labels if at least one of the required labels also hits.
REQUIRE_ANY: dict[str, list[str]] = {
    "Transport": ["Train", "Station"],
}


def keyword_path(name: str) -> str:
    return PREFIX + name


def load_cfg() -> dict:
    cfg_path = HOME / "config.json"
    cfg = json.load(open(cfg_path)) if cfg_path.exists() else {}
    scene = dict((cfg.get("features") or {}).get("scene") or {})
    return scene


def thresholds(scene_cfg: dict) -> dict[str, float]:
    out = {name: VOCAB[name][2] for name in VOCAB}
    for k, v in (scene_cfg.get("thresholds") or {}).items():
        if k in out:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                pass
    return out


def enabled_labels(scene_cfg: dict) -> list[str]:
    wanted = scene_cfg.get("enabled_labels")
    if not wanted:
        return list(VOCAB)
    return [n for n in VOCAB if n in set(wanted)]


def _clip(cfg=None):
    _, T, _ = _runtime()
    if cfg is None:
        cfg = json.load(open(HOME / "config.json"))
    return T.Clip(cfg)


def text_features(clip, labels: list[str]):
    """Encode pos/neg prompts. Returns (names, pos_neg_emb [L,2,D], scale)."""
    pos = [VOCAB[n][0] for n in labels]
    neg = [VOCAB[n][1] for n in labels]
    # encode in one batch so the cache key is stable
    all_p = pos + neg
    t = clip.text(all_p)
    import numpy as np
    arr = t.detach().float().cpu().numpy()
    L = len(labels)
    pos_e, neg_e = arr[:L], arr[L:]
    stacked = np.stack([pos_e, neg_e], axis=1)  # [L,2,D]
    scale = float(clip.model.logit_scale.exp().detach().cpu())
    return stacked, scale


def binary_probs(img_emb, stacked, scale: float):
    """img_emb [N,D] or [D], stacked [L,2,D] -> pos probabilities [N,L]."""
    import numpy as np
    x = np.asarray(img_emb, dtype=np.float32)
    if x.ndim == 1:
        x = x[None, :]
    # L2 in case float16 embeddings drifted
    nrm = np.linalg.norm(x, axis=-1, keepdims=True)
    nrm = np.clip(nrm, 1e-8, None)
    x = x / nrm
    # logits [N,L,2]
    logits = np.einsum("nd,ltd->nlt", x, stacked) * scale
    logits = logits - logits.max(axis=-1, keepdims=True)
    exp = np.exp(logits)
    return (exp[:, :, 0] / exp.sum(axis=-1))


def _parents_of(child: str) -> list[str]:
    """All implied ancestors (Motorbike -> Vehicle, Transport)."""
    out, seen, stack = [], set(), list(IMPLIES.get(child, []))
    while stack:
        p = stack.pop(0)
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        stack.extend(IMPLIES.get(p, []))
    return out


def _apply_suppress(hits: dict, scores: dict) -> None:
    """Drop conflicting labels. If two sources both clear SUPPRESS_AT, the higher score wins."""
    sources = [s for s in SUPPRESS if scores.get(s, 0) >= SUPPRESS_AT or s in hits]
    sources.sort(key=lambda s: (-scores.get(s, 0), s))
    dropped = set()
    for src in sources:
        if src in dropped:
            continue
        for v in SUPPRESS[src]:
            hits.pop(v, None)
            dropped.add(v)


def select_labels(scores: dict[str, float], th: dict[str, float], max_labels: int) -> list[str]:
    """Conservative multi-label selection with implications, exclusions, suppression and a cap."""
    hits = {n: s for n, s in scores.items() if s >= th.get(n, DEFAULT_TH)}
    _apply_suppress(hits, scores)
    for lab, need in REQUIRE_ANY.items():
        if lab in hits and not any(n in hits for n in need):
            hits.pop(lab, None)
    for a, b in EXCLUSIVE:
        if a in hits and b in hits:
            if hits[a] >= hits[b]:
                hits.pop(b, None)
            else:
                hits.pop(a, None)
    added = True
    while added:
        added = False
        for child in list(hits):
            for p in _parents_of(child):
                if p in hits:
                    continue
                if scores.get(p, 0) < IMPLIES_MIN_SCORE and p not in ("People",):
                    # People may be implied from a clear Portrait even if People raw score is modest
                    if not (child == "Portrait" and p == "People"):
                        continue
                hits[p] = scores.get(p, hits[child])
                added = True
    _apply_suppress(hits, scores)
    for lab, need in REQUIRE_ANY.items():
        if lab in hits and not any(n in hits for n in need):
            hits.pop(lab, None)
    if max_labels and len(hits) > max_labels:
        ranked = sorted(hits, key=lambda n: (-hits[n], n))
        keep: set[str] = set()
        for n in ranked:
            required = {p for c in keep for p in _parents_of(c) if p in hits}
            if len(keep) < max_labels or n in required:
                keep.add(n)
        required = {p for c in keep for p in _parents_of(c) if p in hits}
        while len(keep) > max_labels:
            removable = [n for n in reversed(ranked) if n in keep and n not in required]
            if not removable:
                break
            keep.remove(removable[0])
            required = {p for c in keep for p in _parents_of(c) if p in hits}
        keep |= {p for c in list(keep) for p in _parents_of(c) if p in hits}
        hits = {n: hits[n] for n in keep}
    return sorted(hits, key=lambda n: (-hits[n], n))



def list_folder_images(folder: Path) -> list[Path]:
    _, T, _ = _runtime()
    return T.list_images(folder)


def sha_of(path: Path) -> str:
    _, _, shacache = _runtime()
    return shacache.sha256_cached(path)


def load_embedding(sha: str):
    import numpy as np
    f = EMB / f"{sha[:24]}.npy"
    if not f.exists():
        return None
    return np.load(f).astype(np.float32)


def encode_image(clip, path: Path, sha: str, max_px: int):
    import numpy as np
    _, T, _ = _runtime()
    im, _ = T.load_preview(path, sha, max_px, want_full=False)
    f = clip.image(im)[0].detach().float().cpu().numpy()
    dest = EMB
    try:
        dest.mkdir(parents=True, exist_ok=True)
        if not (dest / f"{sha[:24]}.npy").exists():
            np.save(dest / f"{sha[:24]}.npy", f.astype(np.float16))
    except Exception:
        pass
    return f


def contact_sheets(folder: Path, recs: list[dict], outdir: Path, per_label: int = 6):
    """Write a small JPEG grid per label (and an index HTML) from cached previews."""
    from PIL import Image, ImageDraw, ImageFont
    outdir.mkdir(parents=True, exist_ok=True)
    by = {}
    for r in recs:
        for kw in r.get("proposed_keywords") or []:
            name = kw.split("/")[-1]
            by.setdefault(name, []).append(r)
    # also keep high-but-rejected scores for a "near miss" glance? skip — precision review first
    index_rows = []
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 16)
        font_sm = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 12)
    except Exception:
        font = ImageFont.load_default()
        font_sm = font
    for name, rows in sorted(by.items(), key=lambda kv: -len(kv[1])):
        rows = sorted(rows, key=lambda r: -float((r.get("scores") or {}).get(name, 0)))
        pick = rows[:per_label]
        tiles, cell = [], 280
        for r in pick:
            sha = r.get("sha256") or ""
            cache = HOME / "cache" / f"{sha[:24]}.jpg"
            if cache.exists():
                im = Image.open(cache).convert("RGB")
            else:
                im = Image.new("RGB", (cell, cell), (30, 30, 30))
            im.thumbnail((cell - 8, cell - 40), Image.LANCZOS)
            canvas = Image.new("RGB", (cell, cell), (18, 18, 18))
            x = (cell - im.size[0]) // 2
            canvas.paste(im, (x, 4))
            draw = ImageDraw.Draw(canvas)
            sc = float((r.get("scores") or {}).get(name, 0))
            label = f"{r['filename']}  {sc:.2f}"
            draw.text((6, cell - 22), label[:42], fill=(230, 230, 230), font=font_sm)
            tiles.append(canvas)
        if not tiles:
            continue
        cols = min(3, len(tiles))
        rows_n = (len(tiles) + cols - 1) // cols
        header = 36
        grid = Image.new("RGB", (cols * cell, header + rows_n * cell), (10, 10, 10))
        draw = ImageDraw.Draw(grid)
        draw.text((8, 8), f"{PREFIX}{name}  ·  {len(rows)} frames  ·  top {len(tiles)}", fill=(240, 240, 240), font=font)
        for i, t in enumerate(tiles):
            grid.paste(t, ((i % cols) * cell, header + (i // cols) * cell))
        dest = outdir / f"{name}.jpg"
        grid.save(dest, "JPEG", quality=88)
        index_rows.append((name, len(rows), dest.name, [r["filename"] for r in pick]))
    html = ["<html><head><meta charset=utf-8><title>Scene sample</title>",
            "<style>body{font:14px/1.4 -apple-system,sans-serif;background:#111;color:#eee;margin:24px}",
            "img{max-width:100%;height:auto;border-radius:6px} h2{margin-top:2em}</style></head><body>",
            f"<h1>Scene pass sample — {folder.name}</h1>"]
    for name, n, fn, files in index_rows:
        html.append(f"<h2>{PREFIX}{name} ({n})</h2><p>{', '.join(files)}</p><p><img src='{fn}'></p>")
    html.append("</body></html>")
    (outdir / "index.html").write_text("\n".join(html), encoding="utf-8")
    return index_rows


def summarise(recs: list[dict]) -> dict:
    counts = Counter()
    examples = {}
    for r in recs:
        for kw in r.get("proposed_keywords") or []:
            counts[kw] += 1
            examples.setdefault(kw, [])
            if len(examples[kw]) < 8:
                name = kw.split("/")[-1]
                examples[kw].append({"filename": r["filename"], "score": round(float((r.get("scores") or {}).get(name, 0)), 3)})
    n_any = sum(1 for r in recs if r.get("proposed_keywords"))
    return {
        "frames": len(recs),
        "frames_with_labels": n_any,
        "frames_unlabelled": len(recs) - n_any,
        "by_keyword": dict(counts.most_common()),
        "examples": examples,
        "errors": sum(1 for r in recs if r.get("error")),
    }


def run_scene_pass(folder: Path, files: list[Path] | None = None, sample: int = 0,
                   resume: bool = True, tag: str = "", max_labels: int | None = None,
                   sheets: bool = False, cfg_image: dict | None = None) -> dict:
    folder = Path(folder).resolve()
    scene_cfg = load_cfg()
    labels = enabled_labels(scene_cfg)
    th = thresholds(scene_cfg)
    cap = int(max_labels if max_labels is not None else scene_cfg.get("max_labels") or DEFAULT_MAX_LABELS)
    if files is None:
        files = list_folder_images(folder)
    else:
        _, T, _ = _runtime()
        files = [Path(p) for p in files if Path(p).suffix.lower() not in T.SKIP_EXT]
    if sample:
        files = T.spread(files, sample)
    outdir = OUT / folder.name
    outdir.mkdir(parents=True, exist_ok=True)
    stem = "scene-proposals" + (f"-{tag}" if tag else "")
    prog = outdir / f"{stem}.jsonl"
    recs = []
    done = {}
    if resume and prog.exists():
        for line in open(prog):
            try:
                r = json.loads(line)
                done[r["filename"]] = r
            except Exception:
                pass
        recs = [done[p.name] for p in files if p.name in done and not done[p.name].get("error")]
        files = [p for p in files if p.name not in {r["filename"] for r in recs}]
        print(f"resume: {len(recs)} already done, {len(files)} to go", flush=True)
    elif prog.exists():
        prog.unlink()

    print(f"{len(files) + len(recs)} images, scene pass, out={outdir}/{stem}.*  "
          f"labels={len(labels)} cap={cap}", flush=True)

    # hashes first (needed for embedding lookup)
    pending = []
    for p in files:
        try:
            sha = sha_of(p)
            pending.append((p, sha, load_embedding(sha)))
        except Exception as e:
            recs.append({"rel_path": str(p.relative_to(folder.parent)), "filename": p.name,
                         "error": str(e)[:300], "needs_review": True, "proposed_keywords": []})

    missing = [(p, sha) for p, sha, emb in pending if emb is None]
    have = [(p, sha, emb) for p, sha, emb in pending if emb is not None]
    clip = None
    cfg = cfg_image or (json.load(open(HOME / "config.json")) if (HOME / "config.json").exists() else {})
    if missing or True:
        # always need Clip for text features
        clip = _clip(cfg if cfg.get("siglip_model") else json.load(open(HOME / "config.json")))
    stacked, scale = text_features(clip, labels)

    # encode any missing embeddings
    encoded = {}
    for i, (p, sha) in enumerate(missing, 1):
        try:
            encoded[(p, sha)] = encode_image(clip, p, sha, int(cfg.get("preview_max_px") or 2400))
        except Exception as e:
            recs.append({"rel_path": str(p.relative_to(folder.parent)), "filename": p.name,
                         "sha256": sha, "error": str(e)[:300], "needs_review": True, "proposed_keywords": []})
            print(f"[{i}/{len(missing)}] {p.name} err={e}", flush=True)
            continue
        print(f"[{i}/{len(missing)}] encoded {p.name}", flush=True)

    items = have + [(p, sha, encoded[k]) for k in encoded for p, sha in [k]]
    # stable original order
    order = {p.name: i for i, (p, _sha, _e) in enumerate(pending)}
    items.sort(key=lambda t: order.get(t[0].name, 10**9))

    if items:
        import numpy as np
        embs = np.stack([t[2] for t in items], axis=0)
        probs = binary_probs(embs, stacked, scale)  # [N,L]
    else:
        import numpy as np
        probs = np.zeros((0, len(labels)), dtype=np.float32)

    pf = open(prog, "a")
    new_recs = []
    for i, ((p, sha, _), row) in enumerate(zip(items, probs), 1):
        scores = {labels[j]: float(row[j]) for j in range(len(labels))}
        chosen = select_labels(scores, th, cap)
        rec = {
            "rel_path": str(p.relative_to(folder.parent)),
            "filename": p.name,
            "sha256": sha,
            "proposed_keywords": [keyword_path(n) for n in chosen],
            "needs_review": False,
            "review_reasons": [],
            "scores": {n: round(scores[n], 4) for n in labels},
            "scene_labels": chosen,
        }
        new_recs.append(rec)
        recs.append(rec)
        pf.write(json.dumps(rec) + "\n")
        if i == 1 or i == len(items) or i % 100 == 0:
            print(f"[{i}/{len(items)}] {p.name} kw={rec['proposed_keywords']}", flush=True)
    pf.flush(); pf.close()

    # keep recs in filename order of the original request
    by_name = {r["filename"]: r for r in recs}
    wanted = []
    # include resumed + new in the input order we started with
    # (reconstruct from stem file later if needed)
    recs_out = list(by_name.values())

    payload = {
        "folder": str(folder),
        "profile": "scene",
        "pass": "scene",
        "max_labels": cap,
        "thresholds": th,
        "labels": labels,
        "images": recs_out,
        "summary": summarise(recs_out),
    }
    json.dump(payload, open(outdir / f"{stem}.json", "w"), indent=1)
    cols = ["rel_path", "filename", "sha256", "proposed_keywords", "needs_review", "review_reasons", "scores", "error"]
    with open(outdir / f"{stem}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in recs_out:
            row = {k: r.get(k) for k in cols}
            row["proposed_keywords"] = "; ".join(row["proposed_keywords"] or [])
            row["review_reasons"] = "; ".join(row["review_reasons"] or [])
            row["scores"] = json.dumps(row["scores"]) if row["scores"] else ""
            w.writerow(row)
    summ = payload["summary"]
    print(f"done {summ['frames']} images, {summ['frames_with_labels']} labelled, "
          f"{summ['frames_unlabelled']} unlabelled; keywords {summ['by_keyword']}", flush=True)
    if sheets:
        sheet_dir = outdir / "scene" / ("sheets" + (f"-{tag}" if tag else ""))
        rows = contact_sheets(folder, recs_out, sheet_dir)
        print(f"contact sheets: {sheet_dir}/index.html ({len(rows)} labels)", flush=True)
        payload["sheets"] = str(sheet_dir)
        json.dump(payload, open(outdir / f"{stem}.json", "w"), indent=1)
    return payload


def main():
    ap = argparse.ArgumentParser(description="Generalised scene keyword proposals (read-only, no Capture One writes)")
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--files", nargs="+")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--tag", default="", help="suffix for output names, e.g. sample")
    ap.add_argument("--max-labels", type=int, default=None)
    ap.add_argument("--sheets", action="store_true", help="write per-label contact sheets under out/<folder>/scene/")
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()
    if args.files:
        files = [Path(f) for f in args.files]
        folder = files[0].parent
    else:
        if not args.folder:
            ap.error("folder or --files required")
        folder = Path(args.folder).resolve()
        files = None
    run_scene_pass(folder, files=files, sample=args.sample, resume=(False if args.no_resume else True),
                   tag=args.tag, max_labels=args.max_labels, sheets=args.sheets)


if __name__ == "__main__":
    main()
