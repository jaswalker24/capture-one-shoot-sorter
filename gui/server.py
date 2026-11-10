#!/usr/bin/env python3
"""Trips Local AI control panel — LAN web UI on port 8765.

Stdlib only (no Flask/FastAPI) so it can run while a catalog job uses the venv.
Auth: shared secret in ~/localai/gui/auth.token → cookie after POST /api/login.
"""
from __future__ import annotations
import hashlib, hmac, json, os, secrets, subprocess, sys, threading, time, uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOME = Path.home() / "localai"
OUT = HOME / "out"
GUI = HOME / "gui"
STATIC = GUI / "static"
JOBS = GUI / "jobs"
HISTORY = GUI / "history"
AUTH_FILE = GUI / "auth.token"
PORT = int(os.environ.get("LOCALAI_GUI_PORT", "8765"))
HOST = os.environ.get("LOCALAI_GUI_HOST", "0.0.0.0")
IMPORTS = Path("/Volumes/Photos/Camera Imports")
EXPORTS = Path("/Volumes/Photos/Camera Exports")

sys.path.insert(0, str(HOME))
import jobrunner as JR
import schedule_ops as SO
import notify as NF
import undo_run as UR
import profiles_ops as PR
sys.path.insert(0, str(GUI))
import review as RV  # race-number review queue (additive)
import people_pass as PP  # face clusters / People/<Name>
import preview_ops as PV  # keyword preview before apply

JOBS.mkdir(parents=True, exist_ok=True)
HISTORY.mkdir(parents=True, exist_ok=True)
STATIC.mkdir(parents=True, exist_ok=True)

# session tokens (in-memory; reset on restart — re-login with shared secret)
_SESSIONS: set[str] = set()
_QUEUE_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None
_CURRENT_PROC: subprocess.Popen | None = None
_PAUSE = threading.Event()  # set = paused (don't start next)
_PAUSE.clear()


def _token() -> str:
    return AUTH_FILE.read_text().strip() if AUTH_FILE.exists() else ""


def _session_ok(handler) -> bool:
    raw = handler.headers.get("Cookie", "")
    c = SimpleCookie()
    try:
        c.load(raw)
    except Exception:
        return False
    morsel = c.get("localai_session")
    return bool(morsel and morsel.value in _SESSIONS)


def _json(handler, code, obj):
    body = json.dumps(obj, ensure_ascii=False).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler):
    n = int(handler.headers.get("Content-Length") or 0)
    if n <= 0:
        return {}
    return json.loads(handler.rfile.read(n).decode() or "{}")


def list_years():
    if not IMPORTS.is_dir():
        return []
    names = [p.name for p in IMPORTS.iterdir() if p.is_dir() and not p.name.startswith(".")]
    # Prefer calendar years first, then other import roots
    years = sorted([n for n in names if n.isdigit() and len(n) == 4], reverse=True)
    other = sorted([n for n in names if n not in years], reverse=True)
    return years + other


def list_folders(year: str):
    d = IMPORTS / year
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.iterdir(), key=lambda x: x.name, reverse=True):
        if p.is_dir() and not p.name.startswith("."):
            # Do not scan every file on the NAS here — that is too slow over SMB.
            out.append({"name": p.name, "path": str(p), "images": None})
    return out


def list_export_folders():
    if not EXPORTS.is_dir():
        return []
    out = []
    for p in sorted(EXPORTS.iterdir(), key=lambda x: x.name, reverse=True):
        if p.is_dir() and not p.name.startswith("."):
            out.append({"name": p.name, "path": str(p)})
    return out[:80]


def sys_stats():
    stats = {"cpu_idle_pct": None, "mem_used_gb": None, "mem_total_gb": None,
             "mem_pressure": None, "caption_model_loaded": False, "ollama_models": [],
             "fullrun_running": False, "gui_queue_running": False}
    try:
        # CPU idle from top
        top = subprocess.run(["top", "-l", "1", "-n", "0"], capture_output=True, text=True, timeout=5)
        for line in top.stdout.splitlines():
            if "CPU usage" in line:
                # e.g. CPU usage: 5.12% user, 7.69% sys, 87.17% idle
                import re
                m = re.search(r"([\d.]+)% idle", line)
                if m:
                    stats["cpu_idle_pct"] = float(m.group(1))
            if "PhysMem" in line:
                stats["mem_line"] = line.strip()
    except Exception as e:
        stats["cpu_error"] = str(e)
    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
        page = 16384
        nums = {}
        for line in vm.stdout.splitlines():
            if "page size of" in line.lower():
                import re as _re
                m = _re.search(r"(\d+)\s*bytes", line)
                if m: page = int(m.group(1))
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                vs = v.strip().rstrip(".")
                if not vs.isdigit():
                    continue
                nums[k.strip()] = int(vs) * page
        free = nums.get("Pages free", 0) + nums.get("Pages speculative", 0)
        used = nums.get("Pages active", 0) + nums.get("Pages wired down", 0) + nums.get("Pages occupied by compressor", 0)
        total = free + used + nums.get("Pages inactive", 0)
        stats["mem_used_gb"] = round(used / (1 << 30), 2)
        stats["mem_total_gb"] = round(total / (1 << 30), 2)
        stats["mem_compressor_gb"] = round(nums.get("Pages occupied by compressor", 0) / (1 << 30), 2)
    except Exception as e:
        stats["mem_error"] = str(e)
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "2", "http://127.0.0.1:11434/api/ps"],
                           capture_output=True, text=True)
        models = json.loads(r.stdout or "{}").get("models") or []
        stats["ollama_models"] = [m.get("name") or m.get("model") for m in models]
        stats["caption_model_loaded"] = any("qwen" in (n or "").lower() or "vl" in (n or "").lower()
                                            for n in stats["ollama_models"])
    except Exception:
        pass
    try:
        r = subprocess.run(["pgrep", "-f", "fullrun.py"], capture_output=True)
        stats["fullrun_running"] = r.returncode == 0
    except Exception:
        pass
    with _QUEUE_LOCK:
        stats["gui_queue_running"] = any(
            j.get("status") == "running" for j in _load_queue_unlocked()
        )
        stats["paused"] = _PAUSE.is_set()
    # NAS / disk / C1 / thermal
    try:
        stats["nas_mounted"] = IMPORTS.is_dir()
    except Exception:
        stats["nas_mounted"] = False
    try:
        import shutil
        du = shutil.disk_usage(str(Path.home()))
        stats["disk_home_free_gb"] = round(du.free / (1 << 30), 1)
        stats["disk_home_total_gb"] = round(du.total / (1 << 30), 1)
    except Exception as e:
        stats["disk_home_error"] = str(e)
    try:
        if stats.get("nas_mounted"):
            import shutil
            nu = shutil.disk_usage(str(IMPORTS))
            stats["disk_nas_free_gb"] = round(nu.free / (1 << 30), 1)
            stats["disk_nas_total_gb"] = round(nu.total / (1 << 30), 1)
    except Exception as e:
        stats["disk_nas_error"] = str(e)
    try:
        r = subprocess.run(["pgrep", "-x", "Capture One"], capture_output=True)
        stats["capture_one_running"] = r.returncode == 0
    except Exception:
        stats["capture_one_running"] = None
    try:
        # thermal / memory pressure via `memory_pressure` / `pmset -g therm`
        mp = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=3)
        line = next((ln for ln in mp.stdout.splitlines() if "free percentage" in ln.lower() or "pressure" in ln.lower()), "")
        stats["memory_pressure_line"] = line.strip()[:160] or None
        th = subprocess.run(["pmset", "-g", "therm"], capture_output=True, text=True, timeout=3)
        stats["thermal"] = th.stdout.strip()[:200] if th.returncode == 0 else None
    except Exception as e:
        stats["thermal_error"] = str(e)
    try:
        stats["notify"] = NF.status()
    except Exception as e:
        stats["notify_error"] = str(e)
    return stats


def _job_path(jid: str) -> Path:
    return JOBS / f"{jid}.json"


def _load_queue_unlocked():
    jobs = []
    for p in sorted(JOBS.glob("*.json"), key=lambda x: x.stat().st_mtime):
        try:
            j = json.load(open(p))
            j["_file"] = str(p)
            jobs.append(j)
        except Exception:
            pass
    # active first by queue_order, then history-ish
    jobs.sort(key=lambda j: (0 if j.get("status") in ("queued", "running", "paused") else 1,
                             j.get("queue_order", 9999), j.get("created", "")))
    return jobs


def _next_queue_order():
    qs = [j.get("queue_order", 0) for j in _load_queue_unlocked() if j.get("status") in ("queued", "running")]
    return (max(qs) + 1) if qs else 1


def _archive_if_done(job):
    if job.get("status") in ("done", "error", "cancelled"):
        dest = HISTORY / f"{job['id']}.json"
        src = _job_path(job["id"])
        if src.exists():
            dest.write_text(src.read_text())
            # keep a stub in jobs? leave the file; UI filters. Optionally remove from active list via status.


def _worker_loop():
    global _CURRENT_PROC
    while True:
        if _PAUSE.is_set():
            time.sleep(1)
            continue
        job = None
        with _QUEUE_LOCK:
            for j in _load_queue_unlocked():
                if j.get("status") == "queued":
                    job = j
                    break
        if not job:
            time.sleep(1)
            continue
        path = _job_path(job["id"])
        # mark running
        job["status"] = "running"
        json.dump(job, open(path, "w"), indent=1)
        try:
            # run as subprocess so cancel can kill it, and so imports of torch etc stay isolated
            env = {**os.environ, "LOCALAI_NO_APPLY_STEP": "1"}
            proc = subprocess.Popen(
                [str(HOME / "venv/bin/python"), str(HOME / "jobrunner.py"), str(path)],
                cwd=str(HOME), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _CURRENT_PROC = proc
            # update pid into job file periodically is handled by jobrunner
            while proc.poll() is None:
                # honour cancel
                try:
                    cur = json.load(open(path))
                except Exception:
                    cur = {}
                if cur.get("cancel_requested") and proc.poll() is None:
                    try:
                        # kill process group children: jobrunner + tag children
                        subprocess.run(["pkill", "-P", str(proc.pid)], capture_output=True)
                        proc.terminate()
                        try:
                            proc.wait(timeout=10)
                        except Exception:
                            proc.kill()
                    except Exception:
                        pass
                    break
                if _PAUSE.is_set() and proc.poll() is None:
                    # pause means don't start NEXT; current continues
                    pass
                time.sleep(1)
            proc.wait()
        except Exception as e:
            try:
                cur = json.load(open(path))
                cur["status"] = "error"
                cur["error"] = str(e)
                json.dump(cur, open(path, "w"), indent=1)
            except Exception:
                pass
        finally:
            _CURRENT_PROC = None
            try:
                cur = json.load(open(path))
                _archive_if_done(cur)
            except Exception:
                pass
        time.sleep(0.5)


def ensure_worker():
    global _WORKER
    # Test / secondary servers set LOCALAI_GUI_NO_WORKER=1 so they never touch the live queue.
    if os.environ.get("LOCALAI_GUI_NO_WORKER") == "1":
        return
    if _WORKER and _WORKER.is_alive():
        return
    _WORKER = threading.Thread(target=_worker_loop, name="localai-queue", daemon=True)
    _WORKER.start()


class Handler(BaseHTTPRequestHandler):
    server_version = "TripsLocalAIGui/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _serve_static(self, rel):
        # prevent path escape
        rel = rel.lstrip("/")
        if not rel or rel == "/":
            rel = "index.html"
        path = (STATIC / rel).resolve()
        if not str(path).startswith(str(STATIC.resolve())) or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        ctype = "text/html; charset=utf-8"
        if path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif path.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif path.suffix == ".svg":
            ctype = "image/svg+xml"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path.startswith("/api/"):
            if path == "/api/login_status":
                return _json(self, 200, {"ok": _session_ok(self)})
            if not _session_ok(self):
                return _json(self, 401, {"error": "login required"})
            if path == "/api/health":
                return _json(self, 200, {"ok": True, "port": PORT})
            if path.startswith("/api/review/"):
                return self._review_get(path, parse_qs(u.query))
            if path == "/api/stats":
                return _json(self, 200, sys_stats())
            if path == "/api/years":
                return _json(self, 200, {"years": list_years()})
            if path == "/api/folders":
                qs = parse_qs(u.query)
                year = (qs.get("year") or [""])[0]
                return _json(self, 200, {"folders": list_folders(year)})
            if path == "/api/exports":
                return _json(self, 200, {"folders": list_export_folders()})
            if path == "/api/passes":
                # Rank available (calibration fixed) but score-kw default off
                passes = []
                for p in JR.PASSES:
                    item = dict(p)
                    item["available"] = True
                    if p["id"] == "rank":
                        item["keywords_default"] = True
                        item["note"] = p.get("note") or "Calibrated: top 10% General/Rank/Best, next 20% General/Rank/Good."
                    passes.append(item)
                return _json(self, 200, {"passes": passes, "default_order": JR.DEFAULT_ORDER,
                                         "profiles": ["motorsport", "gig", "kyoto"]})
            if path == "/api/queue":
                with _QUEUE_LOCK:
                    jobs = _load_queue_unlocked()
                # strip huge logs for list; keep last 30 lines
                slim = []
                for j in jobs:
                    j2 = {k: v for k, v in j.items() if k != "log" and k != "traceback"}
                    log = j.get("log") or []
                    j2["log_tail"] = log[-30:]
                    slim.append(j2)
                return _json(self, 200, {"jobs": slim, "paused": _PAUSE.is_set()})
            if path.startswith("/api/job/"):
                jid = path.split("/")[-1]
                p = _job_path(jid)
                if not p.exists():
                    hp = HISTORY / f"{jid}.json"
                    if hp.exists():
                        p = hp
                    else:
                        return _json(self, 404, {"error": "not found"})
                j = json.load(open(p))
                return _json(self, 200, j)

            if path == "/api/schedule/list":
                return _json(self, 200, {"schedules": SO.list_schedule_files(),
                                         "camera_timezones": list(SO.CAMERA_TZ.keys())})
            if path == "/api/schedule/preview":
                qs = parse_qs(u.query)
                sp = (qs.get("path") or [""])[0]
                if not sp or not Path(sp).is_file():
                    return _json(self, 400, {"error": "schedule path required"})
                data = json.load(open(sp))
                confirmed_only = (qs.get("confirmed_only") or ["1"])[0] != "0"
                return _json(self, 200, SO.validate_schedule(data, confirmed_only))

            if path == "/api/profiles":
                return _json(self, 200, {"profiles": PR.list_profiles(), "catalogs": PR.catalogs()})
            if path == "/api/profiles/get":
                qs = parse_qs(u.query)
                pid = (qs.get("id") or [""])[0]
                pr = PR.get_profile(pid)
                if not pr: return _json(self, 404, {"error": "not found"})
                return _json(self, 200, {"profile": pr})
            if path == "/api/presets":
                pf = GUI / "presets.json"
                return _json(self, 200, json.load(open(pf)) if pf.exists() else {"presets": []})
            if path == "/api/notify/status":
                return _json(self, 200, NF.status())
            if path == "/api/preview":
                qs = parse_qs(u.query)
                try:
                    pv = PV.load((qs.get("shoot") or [""])[0], (qs.get("id") or [""])[0])
                except FileNotFoundError:
                    return _json(self, 404, {"error": "preview not found"})
                pv["check"] = PV.check_status(pv["id"])
                return _json(self, 200, pv)
            if path == "/api/preview/check_status":
                qs = parse_qs(u.query)
                return _json(self, 200, PV.check_status((qs.get("id") or [""])[0]))
            if path == "/api/history/detail":
                qs = parse_qs(u.query)
                jid = (qs.get("id") or [""])[0]
                for base in (HISTORY, JOBS):
                    hp = base / f"{jid}.json"
                    if hp.exists():
                        return _json(self, 200, json.load(open(hp)))
                return _json(self, 404, {"error": "not found"})

            if path == "/api/history":
                items = []
                for p in sorted(HISTORY.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
                    try:
                        j = json.load(open(p))
                        items.append({k: j.get(k) for k in
                                      ("id", "folder", "folder_name", "profile", "status", "created", "started", "finished",
                                       "passes", "pass_results", "verify", "error", "sample", "dry_run",
                                       "added_keywords", "undo_result_files", "notify")}
                                     | {"keyword_count": sum(len(v) for v in (j.get("added_keywords") or {}).values()),
                                        "variant_count": len(j.get("added_keywords") or {})})
                    except Exception:
                        pass
                return _json(self, 200, {"history": items})

            if path == "/api/people/shoots":
                return _json(self, 200, {"shoots": PP.list_shoots_with_people()})
            if path == "/api/people/library":
                return _json(self, 200, PP.library_summary())
            if path == "/api/people/clusters":
                qs = parse_qs(u.query)
                shoot = (qs.get("shoot") or [""])[0]
                try:
                    return _json(self, 200, PP.get_clusters(shoot))
                except FileNotFoundError:
                    return _json(self, 404, {"error": "no people run for shoot"})
                except Exception as e:
                    return _json(self, 400, {"error": str(e)})
            if path == "/api/people/sheet":
                qs = parse_qs(u.query)
                shoot = (qs.get("shoot") or [""])[0]
                try:
                    cl = int((qs.get("cluster") or ["0"])[0])
                except ValueError:
                    return _json(self, 400, {"error": "bad cluster"})
                sheet = (OUT / shoot / "people" / f"person_{cl:03d}.jpg").resolve()
                root = (OUT / shoot / "people").resolve()
                if not str(sheet).startswith(str(root)) or not sheet.is_file():
                    self.send_error(404); return
                data = sheet.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return

            return _json(self, 404, {"error": "unknown api"})
        # static
        if path in ("/review", "/review/"):
            return self._serve_static("review.html")
        if path in ("/people", "/people/"):
            return self._serve_static("people.html")
        return self._serve_static(path)

    def _review_get(self, path, qs):
        g = lambda k, d="": (qs.get(k) or [d])[0]
        try:
            kind = g("kind", "numbers")  # numbers (race numbers) | focus (AF focus Missed)
            if path == "/api/review/shoots":
                return _json(self, 200, {"shoots": RV.shoots(kind), "kind": kind})
            if path == "/api/review/queue":
                return _json(self, 200, RV.queue(g("shoot"), g("status", "pending"), g("only_numbers") == "1", kind))
            if path == "/api/review/apply_status":
                return _json(self, 200, {"apply": RV.apply_status(g("shoot"), kind)})
            if path == "/api/review/thumb":
                t = RV.thumb(g("shoot"), g("file"), kind)
                if not t:
                    self.send_error(404); return
                data = t.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=86400")
                self.end_headers(); self.wfile.write(data); return
        except ValueError as e:
            return _json(self, 400, {"error": str(e)})
        return _json(self, 404, {"error": "unknown api"})

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        if path == "/api/login":
            data = _read_json(self)
            secret = (data.get("token") or "").strip()
            expected = _token()
            if not expected or not hmac.compare_digest(secret, expected):
                time.sleep(0.4)
                return _json(self, 403, {"error": "bad token"})
            sid = secrets.token_urlsafe(24)
            _SESSIONS.add(sid)
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", f"localai_session={sid}; Path=/; HttpOnly; SameSite=Lax")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not _session_ok(self):
            return _json(self, 401, {"error": "login required"})

        data = _read_json(self)

        if path == "/api/review/decide":
            try:
                d = RV.decide(data.get("shoot") or "", data.get("filename") or "", data.get("action") or "", data.get("numbers"),
                              data.get("kind") or "numbers")
            except ValueError as e:
                return _json(self, 400, {"error": str(e)})
            return _json(self, 200, {"ok": True, "decision": d})
        if path == "/api/review/apply":
            try:
                return _json(self, 200, {"ok": True, "apply": RV.start_apply(data.get("shoot") or "", data.get("kind") or "numbers")})
            except (ValueError, RuntimeError) as e:
                return _json(self, 409, {"error": str(e)})


        if path == "/api/people/name":
            try:
                info = PP.name_cluster(data.get("shoot") or "", int(data.get("cluster")), data.get("name") or "")
            except (ValueError, KeyError, FileNotFoundError) as e:
                return _json(self, 400, {"error": str(e)})
            except Exception as e:
                return _json(self, 500, {"error": str(e)})
            return _json(self, 200, {"ok": True, **info})
        if path == "/api/people/library/rename":
            try:
                person = PP.rename_person(data.get("id") or "", data.get("name") or "")
            except KeyError:
                return _json(self, 404, {"error": "not found"})
            except ValueError as e:
                return _json(self, 400, {"error": str(e)})
            return _json(self, 200, {"ok": True, "person": person})
        if path == "/api/people/library/delete":
            ok = PP.delete_person(data.get("id") or "")
            return _json(self, 200 if ok else 404, {"ok": ok})
        if path == "/api/people/reveal":
            try:
                return _json(self, 200, PP.reveal_in_finder(data.get("path") or ""))
            except ValueError as e:
                return _json(self, 400, {"error": str(e)})
            except FileNotFoundError as e:
                return _json(self, 404, {"error": str(e)})
            except Exception as e:
                return _json(self, 500, {"error": str(e)})

        if path == "/api/queue/add":
            folder = Path(data.get("folder") or "")
            if not folder.is_dir():
                return _json(self, 400, {"error": f"folder not found: {folder}"})
            if data.get("profile_id"):
                pr = PR.get_profile(data["profile_id"])
                if pr:
                    fields = PR.job_fields_from_profile(pr)
                    for k,v in fields.items():
                        data.setdefault(k, v)
                    if fields.get("kind") == "exports":
                        return _json(self, 400, {"error": "use Exports tab for captions profiles"})
            # refuse Camera Exports for catalog passes (captions are separate)
            passes = data.get("passes") or list(JR.DEFAULT_ORDER)
            passes = [p for p in passes if p in JR.PASS_BY_ID]
            if not passes:
                return _json(self, 400, {"error": "no valid passes"})
            jid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
            with _QUEUE_LOCK:
                job = {
                    "id": jid,
                    "folder": str(folder),
                    "folder_name": folder.name,
                    "profile": data.get("profile") or "motorsport",
                    "context": data.get("context") or "",
                    "passes": passes,
                    "apply_each": bool(data.get("apply_each", True)),
                    "dry_run": bool(data.get("dry_run", False)),
                    "ocr_missing_car_number": bool(data.get("ocr_missing_car_number", False)),
                    "apply_rank_keywords": bool(data.get("apply_rank_keywords", True)),
                    # AF focus check (Canon AF points) inside the sharpness pass; off -> LOCALAI_AFPOINTS=0
                    "afpoints": bool(data.get("afpoints", True)),
                    "afpoints_annotate": max(0, min(50, int(data.get("afpoints_annotate") or 0))),
                    # Keyword preview: run passes without writing, then wait for approval (awaiting_approval)
                    "preview_before_apply": bool(data.get("preview_before_apply", False)),
                    "sample": int(data.get("sample") or 0),
                    "schedule_path": data.get("schedule_path") or "",
                    "camera_offset": data.get("camera_offset") or "",
                    "status": "queued",
                    "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "queue_order": _next_queue_order(),
                    "log": [],
                    "pass_results": {},
                    "added_keywords": {},
                }
                json.dump(job, open(_job_path(jid), "w"), indent=1, ensure_ascii=False)
            ensure_worker()
            return _json(self, 200, {"ok": True, "job": job})

        if path == "/api/preview/build":
            folder = Path(data.get("folder") or "")
            if not folder.is_dir():
                return _json(self, 400, {"error": f"folder not found: {folder}"})
            passes = [p for p in (data.get("passes") or []) if p in JR.PASS_BY_ID]
            if not passes:
                return _json(self, 400, {"error": "no valid passes"})
            try:
                pv = PV.build(str(folder), passes, data.get("profile") or "motorsport",
                              afpoints=bool(data.get("afpoints", True)),
                              apply_rank_keywords=bool(data.get("apply_rank_keywords", True)))
            except Exception as e:
                return _json(self, 500, {"error": str(e)})
            return _json(self, 200, {"ok": True, "shoot": pv["shoot"], "id": pv["id"],
                                     "keywords_new": pv["keywords_new"], "frames_with_new": pv["frames_with_new"]})

        if path == "/api/preview/check":
            try:
                return _json(self, 200, {"ok": True, "check": PV.start_c1_check(data.get("shoot") or "", data.get("id") or "", RV.catalog_busy)})
            except FileNotFoundError:
                return _json(self, 404, {"error": "preview not found"})
            except RuntimeError as e:
                return _json(self, 409, {"error": str(e)})

        if path == "/api/preview/approve":
            shoot, pid = data.get("shoot") or "", data.get("id") or ""
            if not data.get("confirm"):
                return _json(self, 400, {"error": "confirm=true required"})
            try:
                ap = PV.approve(shoot, pid, data.get("exclude_keywords") or [], data.get("exclude_passes") or [],
                                data.get("exclude_files") or [])
            except FileNotFoundError:
                return _json(self, 404, {"error": "preview not found"})
            except ValueError as e:
                return _json(self, 400, {"error": str(e)})
            jid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
            folder = Path(ap["folder"])
            with _QUEUE_LOCK:
                job = {"id": jid, "folder": str(folder), "folder_name": folder.name, "profile": ap["profile"],
                       "context": "", "passes": ap["passes"], "apply_each": True, "dry_run": False,
                       "apply_only": True, "approved_plan": ap["plan_path"], "preview_of": pid,
                       "ocr_missing_car_number": False, "apply_rank_keywords": True, "sample": 0,
                       "status": "queued", "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "queue_order": _next_queue_order(), "log": [], "pass_results": {}, "added_keywords": {}}
                json.dump(job, open(_job_path(jid), "w"), indent=1, ensure_ascii=False)
                src = _job_path(pid)
                if src.exists():
                    j = json.load(open(src))
                    if j.get("status") == "awaiting_approval":
                        j["status"] = "done"; j["approved_job"] = jid
                        j["approved"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                        json.dump(j, open(src, "w"), indent=1, ensure_ascii=False)
                        _archive_if_done(j)
            ensure_worker()
            return _json(self, 200, {"ok": True, "job": job, "approved": ap})

        if path == "/api/preview/discard":
            p = _job_path(data.get("id") or "")
            if not p.exists():
                return _json(self, 404, {"error": "not found"})
            with _QUEUE_LOCK:
                j = json.load(open(p))
                if j.get("status") != "awaiting_approval":
                    return _json(self, 409, {"error": "job is not awaiting approval"})
                j["status"] = "done"; j["preview_discarded"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                json.dump(j, open(p, "w"), indent=1, ensure_ascii=False)
                _archive_if_done(j)
            return _json(self, 200, {"ok": True})

        if path == "/api/queue/reorder":
            order = data.get("order") or []  # list of job ids for queued ones
            with _QUEUE_LOCK:
                for i, jid in enumerate(order, 1):
                    p = _job_path(jid)
                    if not p.exists():
                        continue
                    j = json.load(open(p))
                    if j.get("status") == "queued":
                        j["queue_order"] = i
                        json.dump(j, open(p, "w"), indent=1)
            return _json(self, 200, {"ok": True})

        if path == "/api/queue/pause":
            _PAUSE.set()
            return _json(self, 200, {"ok": True, "paused": True})

        if path == "/api/queue/resume":
            _PAUSE.clear()
            ensure_worker()
            return _json(self, 200, {"ok": True, "paused": False})

        if path == "/api/queue/cancel":
            jid = data.get("id")
            p = _job_path(jid or "")
            if not p.exists():
                return _json(self, 404, {"error": "not found"})
            j = json.load(open(p))
            j["cancel_requested"] = True
            if j.get("status") == "queued":
                j["status"] = "cancelled"
                j["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            json.dump(j, open(p, "w"), indent=1)
            return _json(self, 200, {"ok": True, "job": j})

        
        if path == "/api/schedule/save":
            raw = data.get("json")
            if data.get("csv"):
                try:
                    raw = SO.csv_to_schedule(data["csv"], data.get("name") or "Imported schedule",
                                            data.get("timezone") or "Asia/Tokyo",
                                            int(data.get("buffer_min") or 3))
                except Exception as e:
                    return _json(self, 400, {"error": str(e)})
            if not isinstance(raw, dict):
                return _json(self, 400, {"error": "provide json object or csv text"})
            confirmed_only = bool(data.get("confirmed_only", True))
            preview = SO.validate_schedule(raw, confirmed_only)
            path_out = SO.save_schedule(raw, data.get("filename"))
            return _json(self, 200, {"ok": True, "path": str(path_out), "preview": preview})

        if path == "/api/schedule/dry_run":
            folder = Path(data.get("folder") or "")
            sp = Path(data.get("schedule_path") or "")
            if not folder.is_dir():
                return _json(self, 400, {"error": "folder not found"})
            if not sp.is_file():
                return _json(self, 400, {"error": "schedule not found"})
            try:
                summary = SO.dry_run(folder, sp,
                                     camera_tz=data.get("camera_tz") or "Europe/London",
                                     buffer_min=int(data.get("buffer_min") or 3),
                                     confirmed_only=bool(data.get("confirmed_only", True)),
                                     sample=int(data.get("sample") or 0))
            except Exception as e:
                return _json(self, 500, {"error": str(e)})
            return _json(self, 200, {"ok": True, "summary": summary})

        if path == "/api/schedule/apply":
            folder = Path(data.get("folder") or "")
            sp = data.get("schedule_path")
            if not folder.is_dir():
                return _json(self, 400, {"error": "folder not found"})
            try:
                sched_data = json.load(open(sp)) if sp else data.get("json")
                if not sched_data and not sp:
                    return _json(self, 400, {"error": "schedule_path or json required"})
                work = SO.prepare_apply_schedule(sched_data if isinstance(sched_data, dict) else None,
                                                 sp, int(data.get("buffer_min") or 3),
                                                 bool(data.get("confirmed_only", True)))
                cam_off = SO.camera_offset_for(data.get("camera_tz") or "Europe/London", force=True)
            except Exception as e:
                return _json(self, 400, {"error": str(e)})
            jid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
            with _QUEUE_LOCK:
                job = {
                    "id": jid, "folder": str(folder), "folder_name": folder.name,
                    "profile": "gig", "context": data.get("context") or "",
                    "passes": ["schedule"], "apply_each": True,
                    "dry_run": bool(data.get("dry_run", False)),
                    "schedule_path": str(work),
                    "camera_offset": cam_off,
                    "camera_tz": data.get("camera_tz") or "Europe/London",
                    "buffer_min": int(data.get("buffer_min") or 3),
                    "ocr_missing_car_number": False, "apply_rank_keywords": False,
                    "sample": int(data.get("sample") or 0),
                    "status": "queued",
                    "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "queue_order": _next_queue_order(),
                    "log": [], "pass_results": {}, "added_keywords": {},
                }
                json.dump(job, open(_job_path(jid), "w"), indent=1, ensure_ascii=False)
            ensure_worker()
            return _json(self, 200, {"ok": True, "job": job})


        if path == "/api/profiles/defaults":
            st = data.get("shoot_type") or "Gig"
            try:
                return _json(self, 200, {"profile": PR.build_from_shoot_type(st, data.get("overrides") or {})})
            except Exception as e:
                return _json(self, 400, {"error": str(e)})

        if path == "/api/profiles/save":
            try:
                pr = PR.save_profile(data.get("profile") or data, overwrite=True)
            except Exception as e:
                return _json(self, 400, {"error": str(e)})
            return _json(self, 200, {"ok": True, "profile": pr, "profiles": PR.list_profiles()})

        if path == "/api/profiles/delete":
            try:
                PR.delete_profile(data.get("id") or "")
            except Exception as e:
                return _json(self, 400, {"error": str(e)})
            return _json(self, 200, {"ok": True, "profiles": PR.list_profiles()})

        if path == "/api/profiles/duplicate":
            try:
                pr = PR.duplicate_profile(data.get("id") or "", data.get("name"))
            except Exception as e:
                return _json(self, 400, {"error": str(e)})
            return _json(self, 200, {"ok": True, "profile": pr, "profiles": PR.list_profiles()})

        if path == "/api/profiles/reorder":
            order = PR.reorder(data.get("order") or [])
            return _json(self, 200, {"ok": True, "order": order, "profiles": PR.list_profiles()})

        if path == "/api/presets/save":
            presets = data.get("presets")
            if not isinstance(presets, list):
                return _json(self, 400, {"error": "presets list required"})
            # Safety: catalog presets must never include captions
            for pr in presets:
                if pr.get("kind") != "exports" and "captions" in (pr.get("passes") or []):
                    return _json(self, 400, {"error": f"catalog preset {pr.get('name')} cannot include captions"})
            json.dump({"presets": presets}, open(GUI / "presets.json", "w"), indent=2)
            return _json(self, 200, {"ok": True, "presets": presets})

        if path == "/api/notify/set":
            # Only toggles / non-secret fields via API; tokens must be edited in notify.json on disk
            cur = NF.load_cfg()
            if "enabled" in data:
                cur["enabled"] = bool(data["enabled"])
            if "email_enabled" in data:
                cur.setdefault("email", {})["enabled"] = bool(data["email_enabled"])
            if "telegram_enabled" in data:
                cur.setdefault("telegram", {})["enabled"] = bool(data["telegram_enabled"])
            # Never accept bot_token over the API into logs — require file edit
            json.dump(cur, open(NF.CFG, "w"), indent=2)
            NF.CFG.chmod(0o600)
            return _json(self, 200, {"ok": True, "status": NF.status()})

        if path == "/api/history/undo_preview":
            jid = data.get("id")
            job = None
            for base in (HISTORY, JOBS):
                hp = base / f"{jid}.json"
                if hp.exists():
                    job = json.load(open(hp)); break
            if not job:
                return _json(self, 404, {"error": "job not found"})
            plan = UR.collect_added_keywords(job)
            return _json(self, 200, {"ok": True, "plan": plan})

        if path == "/api/history/undo":
            jid = data.get("id")
            apply = bool(data.get("apply", False))
            confirm = bool(data.get("confirm", False))
            if apply and not confirm:
                return _json(self, 400, {"error": "confirm=true required to remove keywords"})
            job = None
            job_path = None
            for base in (HISTORY, JOBS):
                hp = base / f"{jid}.json"
                if hp.exists():
                    job = json.load(open(hp)); job_path = hp; break
            if not job:
                return _json(self, 404, {"error": "job not found"})
            try:
                res = UR.run_undo(job, apply=apply)
            except Exception as e:
                return _json(self, 500, {"error": str(e)})
            if apply and res.get("ok"):
                job["undo"] = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "removed": res.get("removed"),
                               "result_path": res.get("result_path")}
                json.dump(job, open(job_path, "w"), indent=1)
            return _json(self, 200, res)


        if path == "/api/exports/captions":
            # separate export-only panel — does NOT go through catalog queue apply
            folder = Path(data.get("folder") or "")
            if not folder.is_dir():
                return _json(self, 400, {"error": "folder not found"})
            if "Camera Exports" not in str(folder):
                return _json(self, 400, {"error": "captions only allowed under Camera Exports"})
            profile = data.get("profile") or "gig"
            context = data.get("context") or ""
            sample = int(data.get("sample") or 0)
            cmd = [str(HOME / "tag"), "captions", str(folder), "--profile", profile]
            if context:
                cmd += ["--context", context]
            if sample:
                cmd += ["--sample", str(sample)]
            # fire and forget into a history job record
            jid = "cap-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:5]
            logf = HOME / "logs" / "gui" / f"{jid}.log"
            logf.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen(cmd, stdout=open(logf, "w"), stderr=subprocess.STDOUT)
            meta = {"id": jid, "type": "captions", "folder": str(folder), "profile": profile,
                    "status": "running", "pid": proc.pid, "log": str(logf),
                    "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
            json.dump(meta, open(HISTORY / f"{jid}.json", "w"), indent=1)

            def _watch():
                proc.wait()
                meta["status"] = "done" if proc.returncode == 0 else "error"
                meta["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                meta["rc"] = proc.returncode
                json.dump(meta, open(HISTORY / f"{jid}.json", "w"), indent=1)
            threading.Thread(target=_watch, daemon=True).start()
            return _json(self, 200, {"ok": True, "job": meta})

        return _json(self, 404, {"error": "unknown api"})


def main():
    if not AUTH_FILE.exists() or not _token():
        AUTH_FILE.write_text(secrets.token_urlsafe(24))
        AUTH_FILE.chmod(0o600)
        print("generated auth token at", AUTH_FILE)
    try:
        PR.seed_if_empty()
    except Exception as _e:
        print("profile seed:", _e, flush=True)
    ensure_worker()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Trips Local AI control panel on http://{HOST}:{PORT}", flush=True)
    print(f"Auth token file: {AUTH_FILE}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
