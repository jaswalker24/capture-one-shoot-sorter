#!/usr/bin/env python3
"""Per-shoot cull shortlist albums in Capture One (albums only; no image, keyword, star or colour changes).
usage: venv/bin/python cull_album.py <shoot folder path> [--dry]

Creates or refreshes, in the collection group "Trips Local AI":
  "<shoot> · Cull: Sharp + Burst Best"  General>Quality>Sharp AND General>Burst Best
  "<shoot> · Cull: Rank Best"           General>Rank>Best
  "<shoot> · Cull: Shortlist"           (Sharp AND Burst Best) OR Rank Best
All three exclude General>Quality>Motion Blur and Soft Focus, and only include frames
whose file sits in the shoot folder. Membership is computed from the catalog folder
collection (exact folder path, else the unique folder of that name under Camera Imports)
and the matching variants are added to ordinary albums. folderPath smart albums are
not used, because those rules can come back empty on Capture One 16.8.5.30.

Re-running deletes and recreates the three albums. The current collection is restored
afterwards. Keywords, ratings and colour tags on the images are not changed.
"""
import argparse, json, subprocess, sys, tempfile
from pathlib import Path

HOME = Path.home() / "localai"
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(HOME))
import scope_folder as S  # noqa: E402

CFG = json.load(open(HOME / "config.json")).get("cull_album", {}) if (HOME / "config.json").exists() else {}
GROUP = CFG.get("group", "Trips Local AI")

DUMP_JS = r'''
ObjC.import('Foundation');
function posixFolder(c){
  try{
    const f=c.folder();
    if(f===undefined||f===null) return '';
    return String(f).replace(/^file:\/\//,'').replace(/\/+$/,'');
  }catch(e){ return ''; }
}
function isCatalogFolder(c){
  try{ return c.kind()==='catalog folder'; }catch(e){ return false; }
}
function catalogFoldersNamed(d, shoot){
  const direct=d.collections.whose({name:shoot})().filter(isCatalogFolder);
  if(direct.length) return direct;
  const acc=[];
  const roots=d.collections.whose({name:'Camera Imports'})().filter(isCatalogFolder);
  function walk(parent){
    let kids; try{ kids=parent.collections(); }catch(e){ return; }
    for(const c of kids){
      try{ if(isCatalogFolder(c) && c.name()===shoot) acc.push(c); }catch(e){}
      walk(c);
    }
  }
  roots.forEach(walk);
  return acc;
}
function scopeToFolder(C1, d, folder){
  const want=folder.replace(/\/+$/,'');
  const shoot=want.split('/').pop();
  const named=catalogFoldersNamed(d, shoot);
  const exact=named.filter(c=>posixFolder(c)===want);
  let chosen=null, how=null;
  if(exact.length===1){ chosen=exact[0]; how='exact-path'; }
  else if(exact.length>1) throw new Error('multiple catalog folders at '+want);
  else {
    const under=named.filter(c=>{
      const p=posixFolder(c);
      return p.indexOf('/Camera Imports/')>=0 || p.endsWith('/Camera Imports');
    });
    if(under.length===1){ chosen=under[0]; how='name-under-camera-imports'; }
    else if(under.length>1) throw new Error('multiple catalog folders named '+shoot+' under Camera Imports');
    else throw new Error('catalog folder not found for '+want);
  }
  d.currentCollection=chosen;
  const cur=d.currentCollection();
  if(cur.name()!==chosen.name()) throw new Error('could not select catalog folder '+shoot+' (showing '+cur.name()+')');
  return {name:cur.name(), how:how, folder:posixFolder(cur)||want};
}
function folderOf(p){ return String(p).replace(/\/[^\/]*$/,''); }
function run(argv){
  const folder=argv[0].replace(/\/+$/,'');
  const out=argv[1];
  const C1=Application('Capture One'); const d=C1.currentDocument();
  const prev=d.currentCollection();
  try{
    const scoped=scopeToFolder(C1, d, folder);
    const vs=d.variants, ids=vs.id(), paths=vs.parentImage.path();
    const kn=vs.keywords.name(), kp=vs.keywords.parent();
    const frames=[];
    for(let i=0;i<ids.length;i++){
      if(folderOf(paths[i])!==folder) continue;
      const kws=(kn[i]||[]).map((n,j)=>{ const pa=(kp[i]||[])[j]; return (pa? String(pa).split('|').join('/')+'/':'')+n; });
      frames.push({id:ids[i], path:paths[i], keywords:kws});
    }
    const rec={scope:scoped, folder:folder, frames:frames};
    $.NSString.alloc.initWithUTF8String(JSON.stringify(rec)).writeToFileAtomicallyEncodingError(out,true,4,null);
    return frames.length;
  } finally { try{ d.currentCollection=prev; }catch(e){} }
}
'''

FILL_JS = r'''
ObjC.import('Foundation');
function run(argv){
  const plan=JSON.parse($.NSString.stringWithContentsOfFileEncodingError(argv[0],4,null).js);
  const C1=Application('Capture One'); const d=C1.currentDocument();
  const prev=d.currentCollection();
  const GROUP=plan.group;
  try{
    if(plan.folder){
      const want=String(plan.folder).replace(/\/+$/,'');
      const shoot=want.split('/').pop();
      const named=d.collections.whose({name:shoot})().filter(c=>{ try{ return c.kind()==='catalog folder'; }catch(e){ return false; } });
      const exact=named.filter(c=>{ try{ return String(c.folder()).replace(/\/+$/,'')===want; }catch(e){ return false; } });
      if(exact.length===1) d.currentCollection=exact[0];
      else if(named.length===1) d.currentCollection=named[0];
      else throw new Error('catalog folder not found for '+want+' when filling cull albums');
    }
    let gs=d.collections.whose({name:GROUP})().filter(c=>c.kind()==='group');
    if(!gs.length){ d.collections.push(C1.Collection({kind:'group', name:GROUP})); gs=d.collections.whose({name:GROUP})().filter(c=>c.kind()==='group'); }
    if(!gs.length) throw new Error('could not create group '+GROUP);
    const g=gs[0];
    const folderCol=d.currentCollection();
    function showFolder(){
      if(folderCol) d.currentCollection=folderCol;
    }
    const counts={}; const addFailures={};
    for(const album of plan.albums){
      showFolder();
      const existing=g.collections.whose({name:album.name})();
      let a=null;
      for(const o of existing){
        try{
          if(o.kind()==='album'){ if(!a) a=o; }
          else { try{ C1.delete(o); }catch(e){} }
        }catch(e){}
      }
      if(!a){
        g.collections.push(C1.Collection({kind:'album', name:album.name}));
        const created=g.collections.whose({name:album.name})().filter(c=>c.kind()==='album');
        if(!created.length) throw new Error('could not create album '+album.name);
        a=created[0];
      }
      const ids=album.ids||[];
      const CHUNK=25;
      const failed=[];
      for(let i=0;i<ids.length;i+=CHUNK){
        showFolder();
        const slice=ids.slice(i,i+CHUNK);
        const vs=[];
        for(const id of slice){
          try{ const v=d.variants.byId(id); v.id(); vs.push(v); }
          catch(e){ failed.push(String(id)); }
        }
        if(!vs.length) continue;
        try{ C1.addInside(a, {variants:vs}); }
        catch(e){
          for(const v of vs){
            showFolder();
            try{ C1.addInside(a, {variants:[v]}); }
            catch(e2){ try{ failed.push(String(v.id())); }catch(e3){ failed.push('unknown'); } }
          }
        }
      }
      showFolder();
      counts[album.name]=a.variants().length;
      if(failed.length) addFailures[album.name]=failed;
    }
    const out={group:GROUP, albums:counts, addFailures:addFailures};
    $.NSString.alloc.initWithUTF8String(JSON.stringify(out)).writeToFileAtomicallyEncodingError(argv[1],true,4,null);
    return JSON.stringify(counts);
  } finally { try{ d.currentCollection=prev; }catch(e){} }
}
'''


def osascript_js(source, args, timeout=600):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(source)
        path = f.name
    r = subprocess.run(["osascript", "-l", "JavaScript", path, *args],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise SystemExit("osascript failed: " + (r.stderr or r.stdout))
    return r.stdout


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("shoot"); ap.add_argument("--dry", action="store_true"); a = ap.parse_args()
    folder = str(Path(a.shoot).expanduser().resolve() if Path(a.shoot).exists() else Path(a.shoot)).rstrip("/")
    # If the user passed a path that exists, resolve it; otherwise keep the given path
    p = Path(a.shoot)
    folder = str(p.resolve()) if p.exists() else str(p)
    folder = folder.rstrip("/")
    shoot = Path(folder).name
    outdir = HOME / "out" / shoot
    outdir.mkdir(parents=True, exist_ok=True)
    dump_path = outdir / "cull-albums-dump.json"
    plan_path = outdir / "cull-albums-plan.json"
    result_path = outdir / "cull-albums-result.json"
    (outdir / "cull-albums-dump.js").write_text(DUMP_JS)
    (outdir / "cull-albums-fill.js").write_text(FILL_JS)
    if a.dry:
        print("dry: would dump variants from catalog folder", folder)
        print("dry: would write albums in group", GROUP)
        return
    osascript_js(DUMP_JS, [folder, str(dump_path)])
    dump = json.load(open(dump_path))
    groups = S.cull_groups(dump.get("frames") or [])
    plan = {"group": GROUP, "folder": folder, "scope": dump.get("scope"),
            "albums": [{"name": f"{shoot} · {suffix}", "ids": ids} for suffix, ids in groups.items()]}
    json.dump(plan, open(plan_path, "w"), indent=1)
    osascript_js(FILL_JS, [str(plan_path), str(result_path)], timeout=900)
    res = json.load(open(result_path))
    res["scope"] = dump.get("scope")
    res["frames_in_folder"] = len(dump.get("frames") or [])
    json.dump(res, open(result_path, "w"), indent=1, ensure_ascii=False)
    for name, n in (res.get("albums") or {}).items():
        print(f"{name}\t{n}")
    print("current collection restored; frames_in_folder=", res["frames_in_folder"],
          "scope=", json.dumps(res.get("scope")))


if __name__ == "__main__":
    main()
