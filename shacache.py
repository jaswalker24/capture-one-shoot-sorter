"""SHA-256 of originals, cached in index/catalog.sqlite path_cache keyed by (path, size, mtime).
A changed size or mtime forces a full re-hash. Originals are only ever opened read-only."""
import hashlib, sqlite3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_home
DB = app_home.home() / "index" / "catalog.sqlite"

def _hash(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()

def sha256_cached(p):
    p = Path(p); st = p.stat()
    c = sqlite3.connect(DB, timeout=60)
    try:
        c.execute("CREATE TABLE IF NOT EXISTS path_cache(path TEXT PRIMARY KEY, size INT, mtime REAL, sha256 TEXT)")
        r = c.execute("SELECT size, mtime, sha256 FROM path_cache WHERE path=?", (str(p),)).fetchone()
        if r and r[0] == st.st_size and abs(r[1] - st.st_mtime) < 1e-3:
            return r[2]
        h = _hash(p)
        c.execute("INSERT OR REPLACE INTO path_cache VALUES(?,?,?,?)", (str(p), st.st_size, st.st_mtime, h)); c.commit()
        return h
    finally:
        c.close()
