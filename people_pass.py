#!/usr/bin/env python3
"""People pass: local face detect (Apple Vision) + ArcFace embed + cluster + library match.

All on-device. Produces:
  out/<shoot>/people-proposals.json   additive keywords People/<Name> for XMP
  out/<shoot>/people/clusters.json    anonymous + named clusters for the GUI
  out/<shoot>/people/person_XXX.jpg   contact sheets
  ~/localai/people/library.json       persistent named people (centroids)

Never writes ratings/colours. MP4s ignored via tagger.list_images. Captions unused.
Low-confidence matches go to needs_review (tentative_keywords only).
"""
from __future__ import annotations
import json, math, os, time, uuid
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

HOME = Path(os.environ.get("LOCALAI_HOME") or (Path.home() / "localai-lr"))
PEOPLE_DIR = HOME / "people"
LIB_PATH = PEOPLE_DIR / "library.json"
OUT = HOME / "out"

# Cosine similarity thresholds (ArcFace embeddings are L2-normalised)
MATCH_SIM = float(os.environ.get("LOCALAI_FACE_MATCH", "0.55"))
REVIEW_SIM = float(os.environ.get("LOCALAI_FACE_REVIEW", "0.42"))
CLUSTER_EPS = float(os.environ.get("LOCALAI_FACE_EPS", "0.45"))  # DBSCAN cosine distance = 1 - sim
MIN_SAMPLES = int(os.environ.get("LOCALAI_FACE_MIN_SAMPLES", "2"))
FACE_MIN_PX = int(os.environ.get("LOCALAI_FACE_MIN_PX", "64"))


def _load_lib() -> list[dict]:
    PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
    (PEOPLE_DIR / "faces").mkdir(exist_ok=True)
    if not LIB_PATH.exists():
        return []
    try:
        data = json.load(open(LIB_PATH))
        return list(data.get("people") or [])
    except Exception:
        return []


def _save_lib(people: list[dict]):
    PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LIB_PATH.with_suffix(".tmp")
    payload = {"updated": time.strftime("%Y-%m-%dT%H:%M:%S"), "people": people}
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    tmp.replace(LIB_PATH)


def library_summary() -> dict:
    people = _load_lib()
    return {
        "count": len(people),
        "library_path": str(LIB_PATH),
        "people_dir": str(PEOPLE_DIR),
        "people": [
            {"id": p["id"], "name": p["name"], "face_count": p.get("face_count", 0),
             "updated": p.get("updated"), "shoots": p.get("shoots") or []}
            for p in people
        ],
    }


def upsert_person(name: str, centroid: np.ndarray, face_count: int = 1,
                  shoot: str | None = None, rep_jpg: Path | None = None) -> dict:
    """Add or merge a named person into the persistent library."""
    name = (name or "").strip()
    if not name:
        raise ValueError("empty name")
    cen = np.asarray(centroid, np.float32).reshape(-1)
    cen = cen / (np.linalg.norm(cen) + 1e-9)
    people = _load_lib()
    # match existing by name (case-insensitive) or by very high centroid sim
    hit = None
    for p in people:
        if p["name"].lower() == name.lower():
            hit = p
            break
    if hit is None:
        for p in people:
            pc = np.asarray(p["centroid"], np.float32)
            if float(cen @ pc) >= 0.70:
                hit = p
                # keep the user-supplied name
                hit["name"] = name
                break
    if hit is None:
        hit = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "centroid": cen.tolist(),
            "face_count": 0,
            "shoots": [],
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        people.append(hit)
    # running mean of centroids
    n0 = int(hit.get("face_count") or 0)
    old = np.asarray(hit["centroid"], np.float32)
    merged = (old * max(n0, 1) + cen * max(face_count, 1)) / (max(n0, 1) + max(face_count, 1))
    merged = merged / (np.linalg.norm(merged) + 1e-9)
    hit["centroid"] = merged.astype(np.float32).tolist()
    hit["face_count"] = n0 + max(face_count, 1)
    hit["name"] = name
    hit["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if shoot:
        shoots = list(hit.get("shoots") or [])
        if shoot not in shoots:
            shoots.append(shoot)
        hit["shoots"] = shoots[-40:]
    if rep_jpg and Path(rep_jpg).exists():
        dest = PEOPLE_DIR / "faces" / f"{hit['id']}.jpg"
        try:
            Image.open(rep_jpg).convert("RGB").save(dest, quality=90)
            hit["rep"] = str(dest)
        except Exception:
            pass
    _save_lib(people)
    return hit


def rename_person(person_id: str, new_name: str) -> dict:
    people = _load_lib()
    for p in people:
        if p["id"] == person_id:
            p["name"] = new_name.strip()
            p["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _save_lib(people)
            return p
    raise KeyError(person_id)


def delete_person(person_id: str) -> bool:
    people = _load_lib()
    n = len(people)
    people = [p for p in people if p["id"] != person_id]
    if len(people) == n:
        return False
    _save_lib(people)
    return True


def _match_library(centroid: np.ndarray, library: list[dict]):
    """Return (best_person_or_None, best_sim)."""
    if not library:
        return None, 0.0
    cen = centroid / (np.linalg.norm(centroid) + 1e-9)
    best, best_sim = None, -1.0
    for p in library:
        pc = np.asarray(p["centroid"], np.float32)
        sim = float(cen @ pc)
        if sim > best_sim:
            best, best_sim = p, sim
    return best, best_sim


def run_people_pass(folder: Path, files: list[Path] | None = None, *,
                    sample: int = 0, resume: bool = True,
                    match_sim: float = MATCH_SIM, review_sim: float = REVIEW_SIM,
                    cluster_eps: float = CLUSTER_EPS, proposals_only: bool = True) -> dict:
    """Detect/embed/cluster faces for a shoot and write proposals + contact sheets.

    proposals_only is informational (this function never writes sidecars).
    """
    import features as F

    folder = Path(folder).resolve()
    shoot = folder.name
    out_dir = OUT / shoot
    people_out = out_dir / "people"
    people_out.mkdir(parents=True, exist_ok=True)
    crops_dir = people_out / "crops"
    crops_dir.mkdir(exist_ok=True)

    if files is None:
        files = F.T.list_images(folder)
    else:
        files = [Path(p) for p in files if Path(p).suffix.lower() not in getattr(F.T, "SKIP_EXT", {".mp4", ".mov", ".avi", ".mkv"})
                 and not Path(p).name.lower().startswith("_schedule")]
    files = sorted(files)
    if sample and len(files) > sample:
        files = files[:sample]

    c = F.db()
    library = _load_lib()
    t0 = time.time()
    detections = []  # dicts per face
    per_file_stats = []
    n_detect = n_embed = 0

    for i, p in enumerate(files, 1):
        t1 = time.time()
        sha = F.file_sha(c, p)
        # Always re-detect for this pass's contact sheets; skip DB faces_done only for embed cache reuse
        im, full = F.T.load_preview(p, sha, F.CFG["preview_max_px"], want_full=True)
        src = full or im
        tp = HOME / "cache" / f"_people_{os.getpid()}.jpg"
        src.save(tp, quality=92)
        arr = np.asarray(src.convert("RGB"))
        try:
            dets = F.detect_faces(str(tp), src.width, src.height)
        finally:
            tp.unlink(missing_ok=True)
        kept = 0
        file_faces = []
        for box, five, aligned in dets:
            n_detect += 1
            if min(box[2], box[3]) < FACE_MIN_PX:
                continue
            try:
                emb, crop = F.embed_face(arr, five)
            except Exception as e:
                file_faces.append({"error": str(e), "bbox": [round(v) for v in box]})
                continue
            n_embed += 1
            kept += 1
            fid_local = f"{sha[:12]}_{kept}"
            crop_path = crops_dir / f"{fid_local}.jpg"
            Image.fromarray(crop).save(crop_path, quality=90)
            # also store in global faces table for search reuse (non-destructive insert)
            try:
                cur = c.execute(
                    "INSERT INTO faces(sha256,bbox,size_px,emb) VALUES(?,?,?,?)",
                    (sha, json.dumps([round(v) for v in box]), float(min(box[2], box[3])),
                     emb.astype(np.float32).tobytes()),
                )
                face_id = cur.lastrowid
                Image.fromarray(crop).save(F.FACEDIR / f"{face_id}.jpg")
            except Exception:
                face_id = None
            rec = {
                "face_uid": fid_local,
                "face_id": face_id,
                "sha256": sha,
                "filename": p.name,
                "path": str(p),
                "bbox": [round(v) for v in box],
                "size_px": float(min(box[2], box[3])),
                "aligned": bool(aligned),
                "emb": emb.astype(np.float32),
                "crop": str(crop_path),
            }
            detections.append(rec)
            file_faces.append({k: rec[k] for k in ("face_uid", "bbox", "size_px", "aligned")})
        try:
            c.execute("INSERT OR IGNORE INTO faces_done VALUES(?)", (sha,))
            # ensure image row exists lightly for join
            c.execute(
                "INSERT OR IGNORE INTO images(sha256, path, filename, folder) VALUES(?,?,?,?)",
                (sha, str(p), p.name, shoot),
            )
            c.commit()
        except Exception:
            pass
        dt = time.time() - t1
        per_file_stats.append({"filename": p.name, "detected": len(dets), "embedded": kept, "sec": round(dt, 3)})
        print(f"[{i}/{len(files)}] {p.name}: {len(dets)} faces detected, {kept} embedded ({dt:.2f}s)", flush=True)

    # Cluster within this shoot
    clusters_meta = []
    labels = np.full(len(detections), -1, dtype=int)
    if detections:
        from sklearn.cluster import DBSCAN
        X = np.stack([d["emb"] for d in detections])
        lab = DBSCAN(eps=cluster_eps, min_samples=MIN_SAMPLES, metric="cosine").fit(X).labels_
        labels = lab
        order = [l for l, _ in Counter(l for l in lab if l >= 0).most_common()]
        remap = {l: i + 1 for i, l in enumerate(order)}
        next_k = len(remap) + 1
        for d, l in zip(detections, lab):
            if l >= 0:
                d["cluster"] = int(remap[l])
            else:
                # singleton: still nameable in the People tab
                d["cluster"] = next_k
                remap[f"singleton-{next_k}"] = next_k
                next_k += 1

        for k in sorted(remap.values()):
            members = [d for d in detections if d["cluster"] == k]
            cen = np.mean(np.stack([m["emb"] for m in members]), axis=0)
            cen = cen / (np.linalg.norm(cen) + 1e-9)
            person, sim = _match_library(cen, library)
            status = "anonymous"
            name = None
            person_id = None
            if person and sim >= match_sim:
                status = "matched"
                name = person["name"]
                person_id = person["id"]
            elif person and sim >= review_sim:
                status = "review"
                name = person["name"]
                person_id = person["id"]
            # contact sheet
            ids = members[:24]
            cols = min(8, max(1, len(ids)))
            rows = max(1, math.ceil(len(ids) / cols))
            sheet = Image.new("RGB", (112 * cols, 112 * rows), (20, 22, 28))
            for j, m in enumerate(ids):
                try:
                    sheet.paste(Image.open(m["crop"]).resize((112, 112)), ((j % cols) * 112, (j // cols) * 112))
                except Exception:
                    pass
            sheet_path = people_out / f"person_{k:03d}.jpg"
            sheet.save(sheet_path, quality=90)
            clusters_meta.append({
                "cluster": k,
                "size": len(members),
                "status": status,
                "name": name,
                "person_id": person_id,
                "similarity": round(float(sim), 4) if person else None,
                "centroid": cen.astype(np.float32).tolist(),
                "contact_sheet": str(sheet_path),
                "faces": [{"face_uid": m["face_uid"], "filename": m["filename"], "bbox": m["bbox"],
                           "crop": m["crop"]} for m in members[:40]],
            })
    else:
        remap = {}

    # Build per-image proposals
    by_file: dict[str, list] = defaultdict(list)
    for d in detections:
        by_file[d["filename"]].append(d)

    cluster_by_k = {c["cluster"]: c for c in clusters_meta}
    images = []
    for p in files:
        faces_out = []
        proposed = []
        tentative = []
        reasons = []
        needs = False
        for d in by_file.get(p.name, []):
            cl = d.get("cluster", -1)
            meta = cluster_by_k.get(cl)
            entry = {
                "face_uid": d["face_uid"],
                "bbox": d["bbox"],
                "cluster": cl,
                "size_px": d["size_px"],
            }
            if meta and meta["name"]:
                entry["name"] = meta["name"]
                entry["similarity"] = meta["similarity"]
                entry["status"] = meta["status"]
                kw = f"People/{meta['name']}"
                if meta["status"] == "matched":
                    if kw not in proposed:
                        proposed.append(kw)
                elif meta["status"] == "review":
                    needs = True
                    if kw not in tentative:
                        tentative.append(kw)
                    reasons.append(f"low-confidence match to {meta['name']} ({meta['similarity']})")
            elif cl < 0:
                entry["status"] = "unclustered"
            else:
                entry["status"] = "anonymous"
                entry["label"] = f"Person {cl:03d}"
            faces_out.append(entry)
        # sha
        sha = next((d["sha256"] for d in by_file.get(p.name, [])), None)
        if sha is None:
            try:
                sha = F.file_sha(c, p)
            except Exception:
                sha = None
        images.append({
            "filename": p.name,
            "sha256": sha,
            "needs_review": needs,
            "review_reasons": reasons,
            "proposed_keywords": proposed,
            "tentative_keywords": tentative,
            "faces": faces_out,
        })

    elapsed = time.time() - t0
    summary = {
        "folder": str(folder),
        "shoot": shoot,
        "profile": "people",
        "frames": len(files),
        "faces_detected": n_detect,
        "faces_embedded": n_embed,
        "clusters": len(clusters_meta),
        "matched": sum(1 for c in clusters_meta if c["status"] == "matched"),
        "review_clusters": sum(1 for c in clusters_meta if c["status"] == "review"),
        "anonymous": sum(1 for c in clusters_meta if c["status"] == "anonymous"),
        "unclustered_faces": sum(1 for d in detections if d.get("cluster", -1) < 0),
        "elapsed_sec": round(elapsed, 1),
        "sec_per_frame": round(elapsed / max(len(files), 1), 3),
        "detection_rate": round(sum(1 for s in per_file_stats if s["detected"] > 0) / max(len(files), 1), 3),
        "match_sim": match_sim,
        "review_sim": review_sim,
        "cluster_eps": cluster_eps,
        "library_people": len(library),
        "proposals_only": proposals_only,
    }

    clusters_path = people_out / "clusters.json"
    json.dump({"summary": summary, "clusters": [
        {k: v for k, v in c.items() if k != "centroid"} | {"centroid_dim": len(c["centroid"])}
        for c in clusters_meta
    ]}, open(clusters_path, "w"), indent=1, ensure_ascii=False)
    # keep centroids in a sidecar for naming
    json.dump({"clusters": [
        {"cluster": c["cluster"], "centroid": c["centroid"], "name": c["name"],
         "status": c["status"], "size": c["size"], "person_id": c["person_id"],
         "contact_sheet": c["contact_sheet"]}
        for c in clusters_meta
    ]}, open(people_out / "centroids.json", "w"))

    prop_path = out_dir / "people-proposals.json"
    prop = {"folder": str(folder), "profile": "people", "summary": summary, "images": images}
    json.dump(prop, open(prop_path, "w"), indent=1, ensure_ascii=False)

    # CSV for convenience
    import csv
    with open(people_out / "faces.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["person", "local_name", "status", "filename", "bbox", "face_uid", "similarity"])
        for d in detections:
            cl = d.get("cluster", -1)
            meta = cluster_by_k.get(cl)
            w.writerow([
                f"Person {cl:03d}" if cl > 0 else "unclustered",
                (meta or {}).get("name") or "",
                (meta or {}).get("status") or "unclustered",
                d["filename"], d["bbox"], d["face_uid"],
                (meta or {}).get("similarity"),
            ])

    print(
        f"people: {len(files)} frames, {n_detect} detected, {n_embed} embedded, "
        f"{len(clusters_meta)} clusters "
        f"(matched={summary['matched']} review={summary['review_clusters']} anon={summary['anonymous']}), "
        f"{summary['unclustered_faces']} unclustered; "
        f"{elapsed:.0f}s ({summary['sec_per_frame']:.2f}s/frame); -> {prop_path}",
        flush=True,
    )
    return {"summary": summary, "proposals": str(prop_path), "clusters": str(clusters_path),
            "clusters_meta": clusters_meta}


def name_cluster(shoot: str, cluster: int, name: str) -> dict:
    """Name an anonymous/review cluster; update library + rewrite proposals for that shoot."""
    people_out = OUT / shoot / "people"
    cen_path = people_out / "centroids.json"
    if not cen_path.exists():
        raise FileNotFoundError(f"no clusters for {shoot}")
    data = json.load(open(cen_path))
    hit = next((c for c in data["clusters"] if int(c["cluster"]) == int(cluster)), None)
    if not hit:
        raise KeyError(f"cluster {cluster}")
    cen = np.asarray(hit["centroid"], np.float32)
    person = upsert_person(name, cen, face_count=int(hit.get("size") or 1),
                           shoot=shoot, rep_jpg=hit.get("contact_sheet"))
    hit["name"] = person["name"]
    hit["status"] = "matched"
    hit["person_id"] = person["id"]
    json.dump(data, open(cen_path, "w"), indent=1)
    # refresh clusters.json display fields
    clp = people_out / "clusters.json"
    if clp.exists():
        cj = json.load(open(clp))
        for c in cj.get("clusters") or []:
            if int(c["cluster"]) == int(cluster):
                c["name"] = person["name"]
                c["status"] = "matched"
                c["person_id"] = person["id"]
        json.dump(cj, open(clp, "w"), indent=1, ensure_ascii=False)
    # rewrite proposals: promote People/Name for this cluster's faces
    prop_path = OUT / shoot / "people-proposals.json"
    if prop_path.exists():
        prop = json.load(open(prop_path))
        kw = f"People/{person['name']}"
        for img in prop.get("images") or []:
            for f in img.get("faces") or []:
                if int(f.get("cluster") or -1) == int(cluster):
                    f["name"] = person["name"]
                    f["status"] = "matched"
                    f["person_id"] = person["id"]
            # rebuild keywords from faces
            proposed, tentative, reasons = [], [], []
            needs = False
            for f in img.get("faces") or []:
                if f.get("status") == "matched" and f.get("name"):
                    k = f"People/{f['name']}"
                    if k not in proposed:
                        proposed.append(k)
                elif f.get("status") == "review" and f.get("name"):
                    needs = True
                    k = f"People/{f['name']}"
                    if k not in tentative:
                        tentative.append(k)
                    reasons.append(f"low-confidence match to {f['name']}")
            img["proposed_keywords"] = proposed
            img["tentative_keywords"] = tentative
            img["needs_review"] = needs
            img["review_reasons"] = reasons
        json.dump(prop, open(prop_path, "w"), indent=1, ensure_ascii=False)
    return {"person": {"id": person["id"], "name": person["name"], "face_count": person["face_count"]},
            "cluster": int(cluster), "shoot": shoot, "proposals": str(prop_path)}


def list_shoots_with_people() -> list[dict]:
    out = []
    if not OUT.exists():
        return out
    for d in sorted(OUT.iterdir(), key=lambda x: x.name, reverse=True):
        cl = d / "people" / "clusters.json"
        if cl.exists():
            try:
                s = json.load(open(cl)).get("summary") or {}
                out.append({"shoot": d.name, **{k: s.get(k) for k in
                            ("frames", "faces_embedded", "clusters", "matched", "anonymous", "elapsed_sec")}})
            except Exception as e:
                out.append({"shoot": d.name, "error": str(e)})
    return out


def get_clusters(shoot: str) -> dict:
    people_dir = (OUT / shoot / "people").resolve()
    cl = people_dir / "clusters.json"
    if not cl.exists():
        raise FileNotFoundError(shoot)
    data = json.load(open(cl))
    # Attach absolute paths for the GUI naming page (folder + each contact sheet)
    data["paths"] = {
        "people_dir": str(people_dir),
        "clusters_json": str(cl),
        "centroids_json": str(people_dir / "centroids.json"),
        "faces_csv": str(people_dir / "faces.csv"),
        "proposals_json": str((OUT / shoot / "people-proposals.json").resolve()),
        "library_json": str(LIB_PATH),
    }
    for c in data.get("clusters") or []:
        sheet = people_dir / f"person_{int(c['cluster']):03d}.jpg"
        c["sheet_path"] = str(sheet) if sheet.is_file() else None
        # keep relative contact_sheet if present; prefer absolute for UI
        if c.get("sheet_path"):
            c["contact_sheet"] = c["sheet_path"]
    return data


def allowed_reveal_roots() -> list[Path]:
    return [(OUT).resolve(), (PEOPLE_DIR).resolve()]


def is_allowed_reveal_path(path: str | Path) -> Path:
    """Resolve and ensure path is under ~/localai/out or ~/localai/people (no escape)."""
    raw = Path(path).expanduser()
    try:
        resolved = raw.resolve(strict=False)
    except Exception as e:
        raise ValueError(f"bad path: {e}") from e
    # Reject null bytes / empty
    if not str(path) or "\x00" in str(path):
        raise ValueError("empty path")
    allowed = False
    for root in allowed_reveal_roots():
        try:
            resolved.relative_to(root)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        raise ValueError("path not under ~/localai/out or ~/localai/people")
    if not resolved.exists():
        raise FileNotFoundError(str(resolved))
    return resolved


def reveal_in_finder(path: str | Path) -> dict:
    """Reveal a file/folder in Finder. Only paths under out/ or people/."""
    target = is_allowed_reveal_path(path)
    # open -R reveals in Finder; works for files and folders
    r = __import__("subprocess").run(
        ["open", "-R", str(target)],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout or f"open failed rc={r.returncode}").strip())
    return {"ok": True, "path": str(target)}
