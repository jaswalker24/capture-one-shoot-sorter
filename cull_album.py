#!/usr/bin/env python3
"""Per-shoot cull shortlist smart albums in Capture One (keyword rules only; no image, keyword, star or colour changes).
usage: venv/bin/python cull_album.py <shoot folder path or name> [--dry]
Creates or refreshes, in the collection group "Trips Local AI":
  "<shoot> · Cull: Sharp + Burst Best"  General>Quality>Sharp AND General>Burst Best
  "<shoot> · Cull: Rank Best"           General>Rank>Best
  "<shoot> · Cull: Shortlist"           (Sharp AND Burst Best) OR Rank Best
All three exclude General>Quality>Motion Blur and Soft Focus and are limited to the shoot's folder (folderPath contains <shoot>).
Smart-album rule keys verified on Capture One 16.8.5 (2026-11-09): IB_S_CONTENT_KEYWORDS matches the full keyword path
("General|Quality|Sharp"), operator 6 = contains, 8 = does not contain; folderPath + 6 scopes to a folder.
The current collection is restored afterwards (making a collection would otherwise change what Capture One shows)."""
import argparse, json, subprocess, sys
from pathlib import Path
from xml.sax.saxutils import escape
HOME = Path.home() / "localai"
CFG = json.load(open(HOME / "config.json")).get("cull_album", {})
GROUP = CFG.get("group", "Trips Local AI")
KW, CONTAINS, NOT_CONTAINS, FOLDER = "IB_S_CONTENT_KEYWORDS", 6, 8, "folderPath"

def cond(key, op, crit):
    return f'<Condition Enabled="YES"><Key>{key}</Key><Operator>{op}</Operator><Criterion>{escape(crit)}</Criterion></Condition>'

def grp(*parts, kind="AND"):
    return f'<MatchOperator Kind="{kind}">' + "".join(parts) + "</MatchOperator>"

def doc(x): return '<?xml version="1.0" encoding="UTF-8"?>' + x

def albums(shoot):
    scope = cond(FOLDER, CONTAINS, shoot)
    noblur = cond(KW, NOT_CONTAINS, "General|Quality|Motion Blur") + cond(KW, NOT_CONTAINS, "General|Quality|Soft Focus")
    sharp_burst = grp(cond(KW, CONTAINS, "General|Quality|Sharp"), cond(KW, CONTAINS, "General|Burst Best"))
    best = cond(KW, CONTAINS, "General|Rank|Best")
    return [
        {"name": f"{shoot} · Cull: Sharp + Burst Best", "rules": doc(grp(scope, sharp_burst, noblur))},
        {"name": f"{shoot} · Cull: Rank Best", "rules": doc(grp(scope, best, noblur))},
        {"name": f"{shoot} · Cull: Shortlist", "rules": doc(grp(scope, grp(sharp_burst, best, kind="OR"), noblur))},
    ]

def q(s): return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

def applescript(spec):
    L = ['set out to ""', 'tell application "Capture One"', 'tell current document',
         'set prevColl to current collection',
         f'set gs to (collections whose name is {q(GROUP)} and kind is group)',
         f'if (count of gs) is 0 then', f'set g to make new collection with properties {{kind:group, name:{q(GROUP)}}}',
         'else', 'set g to item 1 of gs', 'end if']
    for a in spec:
        L += [f'set olds to (collections of g whose name is {q(a["name"])} and kind is smart album)',
              'repeat with o in olds', 'delete o', 'end repeat',
              f'set c to make new collection at g with properties {{kind:smart album, name:{q(a["name"])}, rules:{q(a["rules"])}}}',
              f'set out to out & {q(a["name"])} & tab & (count of variants of c) & linefeed']
    L += ['try', 'if prevColl is not missing value then set current collection to prevColl', 'end try',
          'set out to out & "current collection: " & (name of current collection)',
          'end tell', 'end tell', 'return out']
    return "\n".join(L)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("shoot"); ap.add_argument("--dry", action="store_true"); a = ap.parse_args()
    shoot = Path(a.shoot).name; spec = albums(shoot)
    d = HOME / "out" / shoot; d.mkdir(parents=True, exist_ok=True)
    src = applescript(spec); (d / "cull-albums.applescript").write_text(src)
    json.dump(spec, open(d / "cull-albums-spec.json", "w"), indent=1, ensure_ascii=False)
    if a.dry:
        print(src); return
    r = subprocess.run(["osascript", str(d / "cull-albums.applescript")], capture_output=True, text=True, timeout=600)
    if r.returncode: raise SystemExit("osascript failed: " + r.stderr)
    res = {}
    for line in r.stdout.strip().splitlines():
        if "\t" in line: n, c = line.split("\t"); res[n] = int(c)
        print(line)
    json.dump({"group": GROUP, "albums": res}, open(d / "cull-albums-result.json", "w"), indent=1, ensure_ascii=False)

if __name__ == "__main__":
    main()
