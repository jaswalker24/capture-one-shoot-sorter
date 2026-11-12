// JXA: additively apply keywords from the configured keyword library to variants in the open Capture One catalog.
// usage: osascript -l JavaScript apply_c1.js <plan.json> <result.json> [dry]
// plan: {"folder": "<abs folder path>", "library": "Trip Keywords",
//        "items": [{"filename": "_A3A3807.CR3", "path": "<abs path>", "keywords": ["Motorsport/Car Colour/Green", ...]}]}
// Never removes keywords, never changes ratings/colour tags. Keywords not found in the library are skipped (no flat ones).

function findKeywordLibrary(C1, preferred) {
  // Prefer the name in the plan (from config.json keyword_library). Fall back
  // to Trip Keywords, then the legacy Trip Keywords name so an older catalog still applies.
  var names = [];
  if (preferred) names.push(preferred);
  names.push("Trip Keywords", "Trip Keywords");
  var seen = {};
  var tried = [];
  for (var i = 0; i < names.length; i++) {
    var n = names[i];
    if (!n || seen[n]) continue;
    seen[n] = true;
    tried.push(n);
    var lib = C1.keywordLibraries.whose({name: n});
    if (lib.length) return lib;
  }
  throw new Error("keyword library not found (tried " + tried.join(", ") + ")");
}

ObjC.import('Foundation');
function readJSON(p){ return JSON.parse($.NSString.stringWithContentsOfFileEncodingError(p,4,null).js); }
function writeJSON(p,o){ $.NSString.alloc.initWithUTF8String(JSON.stringify(o,null,1)).writeToFileAtomicallyEncodingError(p,true,4,null); }
function kwList(v){ return v.keywords().map(k=>{const pa=k.parent(); return (pa? pa.split('|').join('/')+'/' : '')+k.name();}); }

// ---- folder scope (2026-11-10): never trust whatever collection Capture One is showing.
// Select the catalog folder collection whose `folder` path equals the job folder (exact match).
// Fall back to a uniquely named catalog folder under the Camera Imports tree.
// Do not use folderPath smart albums: on Capture One 16.8.5.30 those rules can return 0
// variants even when the catalog folder itself has every frame and the same path.
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
// Folder-scoped fast path: bulk reads (ids, paths, keywords, ratings, colours) and one applyKeyword call per
// keyword for all its frames, addressed by variant id. Before/after per frame as in the per-item path.
function bulkApply(argv, plan, dry, C1, d, scope, libKw){
  const vs=d.variants; const ids=vs.id(), paths=vs.parentImage.path();
  const byPath={}; paths.forEach((p,i)=>{ (byPath[p]=byPath[p]||[]).push(i); });
  const readKw=()=>{ const kn=vs.keywords.name(), kp=vs.keywords.parent();
    return i=>(kn[i]||[]).map((n,j)=>{ const pa=(kp[i]||[])[j]; return (pa? String(pa).split('|').join('/')+'/' : '')+n; }); };
  const kwAt=readKw(), rt=vs.rating(), ct=vs.colorTag();
  const res={dry:dry, collection:scope.collection, scope:scope, mode:'bulk', before:[], applied:[], skipped:[], failures:[], after:[]};
  const groups={};
  for(const it of plan.items){
    const ix=byPath[it.path]||[];
    if(ix.length!==1){ res.failures.push({filename:it.filename, reason: ix.length? 'multiple variants':'not in Capture One under '+scope.folder}); continue; }
    const i=ix[0];
    const b={id:ids[i], filename:it.filename, keywords:kwAt(i), rating:rt[i], color:ct[i]};
    res.before.push(b);
    for(const p of it.keywords){
      if(b.keywords.indexOf(p)>=0){ res.skipped.push({filename:it.filename, keyword:p, reason:'already applied'}); continue; }
      const k=libKw(p);
      if(!k){ res.skipped.push({filename:it.filename, keyword:p, reason:'not in keyword library'}); continue; }
      (groups[p]=groups[p]||{k:k, t:[]}).t.push(b);
    }
  }
  for(const p of Object.keys(groups)){
    const g=groups[p];
    if(dry){ g.t.forEach(b=>res.applied.push({filename:b.filename, id:b.id, keyword:p, dry:true})); continue; }
    try{ C1.applyKeyword(g.k,{to:g.t.map(b=>d.variants.byId(b.id))}); g.t.forEach(b=>res.applied.push({filename:b.filename, id:b.id, keyword:p})); }
    catch(e){
      for(const b of g.t){   // fall back to one frame at a time
        try{ C1.applyKeyword(g.k,{to:[d.variants.byId(b.id)]}); res.applied.push({filename:b.filename, id:b.id, keyword:p}); }
        catch(e2){ res.failures.push({filename:b.filename, keyword:p, reason:String(e2)}); }
      }
    }
  }
  if(dry){ res.after=res.before.map(b=>Object.assign({}, b, {lost:[], flags_changed:false})); }
  else {
    const ids2=vs.id(), kw2=readKw(), rt2=vs.rating(), ct2=vs.colorTag(); const pos={}; ids2.forEach((x,i)=>{ pos[x]=i; });
    for(const b of res.before){
      const i=pos[b.id];
      if(i===undefined){ res.failures.push({filename:b.filename, reason:'post-check: variant not readable'}); continue; }
      const a={id:b.id, filename:b.filename, keywords:kw2(i), rating:rt2[i], color:ct2[i]};
      a.lost=b.keywords.filter(x=>a.keywords.indexOf(x)<0);
      a.flags_changed=(a.rating!==b.rating||a.color!==b.color);
      if(a.lost.length||a.flags_changed) res.failures.push({filename:b.filename, reason:'post-check: lost='+a.lost+' flags_changed='+a.flags_changed});
      res.after.push(a);
    }
  }
  res.catalog_keywords=d.keywords().map(k=>({name:k.name(), parent:k.parent()}));
  writeJSON(argv[1],res);
  return JSON.stringify({applied:res.applied.length, skipped:res.skipped.length, failures:res.failures.length});
}

function run(argv){
  const plan=readJSON(argv[0]); const dry=argv[2]==='dry';
  const C1=Application('Capture One'); const d=C1.currentDocument();
  const prev=d.currentCollection();
  try { return runScoped(argv, plan, dry, C1, d); }
  finally { if(plan.scope_folder){ try{ d.currentCollection=prev; }catch(e){} } }
}
function runScoped(argv, plan, dry, C1, d){
  const lib=findKeywordLibrary(C1, plan.library);
  const L=lib[0]; const kwCache={};
  function libKw(path){
    if(path in kwCache) return kwCache[path];
    const parts=path.split('/'); const name=parts.pop(); const parent=parts.join('|');
    const m=L.keywords.whose({name:name,parent:parent}); kwCache[path]=m.length? m[0]:null; return kwCache[path];
  }
  let scope=null, scopedPaths=null;
  if(plan.scope_folder){
    const folder=String(plan.scope_folder).replace(/\/+$/,'');
    const scoped=scopeToFolder(C1, d, folder);
    const name=scoped.name;
    const paths=d.variants.parentImage.path();             // bulk read, scope collection only
    scopedPaths=paths;
    const inFolder=paths.filter(p=>folderOf(p)===folder);
    const have={}; inFolder.forEach(p=>{ have[p.split('/').pop()]=1; });
    const missing=plan.items.filter(it=>!have[it.filename]).map(it=>it.filename);
    scope={collection:name, how:scoped.how, folder:folder, variants:paths.length, in_folder:inFolder.length,
           expected:plan.expected_count||null, plan_items:plan.items.length, plan_missing:missing.length, missing_examples:missing.slice(0,10)};
    let why=null;
    if(!inFolder.length) why='no variants of '+folder+' in Capture One';
    else if(plan.items.length && missing.length>Math.max(2, 0.05*plan.items.length)) why=missing.length+' of '+plan.items.length+' frames are not in Capture One under '+folder;
    else if(plan.expected_count && inFolder.length < 0.9*plan.expected_count) why='Capture One has '+inFolder.length+' variants for '+folder+' but the folder has '+plan.expected_count+' images';
    if(why){
      const res={dry:dry, collection:name, scope:scope, refused:why, before:[], applied:[], skipped:[], after:[],
                 failures:plan.items.map(it=>({filename:it.filename, reason:'refused: '+why}))};
      writeJSON(argv[1],res);
      return JSON.stringify({refused:why});
    }
  }
  if(scope && !plan.items.some(it=>it.description)) return bulkApply(argv, plan, dry, C1, d, scope, libKw);
  // variants of current collection, indexed by name (base name without extension)
  const cur=d.currentCollection().name();
  const vs=d.variants; const names=vs.name(); const idx={};
  // id-based references: stay on the same variant even if something else switches the collection mid-run
  const vids=scopedPaths? vs.id() : null;
  names.forEach((n,i)=>{ (idx[n]=idx[n]||[]).push(i); });
  const res={dry:dry, collection:cur, scope:scope, before:[], applied:[], skipped:[], failures:[], after:[]};
  for(const it of plan.items){
    const base=it.filename.replace(/\.[^.]+$/,'');
    const cands=(idx[base]||[]).filter(i=>{ if(scopedPaths) return scopedPaths[i]===it.path;
                     try{return vs[i].parentImage().path()===it.path}catch(e){return false} })
                   .map(i=>vids? d.variants.byId(vids[i]) : vs[i]);
    if(cands.length!==1){ res.failures.push({filename:it.filename, reason: cands.length? 'multiple variants':'variant not in current collection ('+cur+')'}); continue; }
    const v=cands[0];
    const b={id:v.id(), filename:it.filename, keywords:kwList(v), rating:v.rating(), color:v.colorTag()};
    res.before.push(b);
    const todo=[];
    for(const p of it.keywords){
      if(b.keywords.indexOf(p)>=0){ res.skipped.push({filename:it.filename, keyword:p, reason:'already applied'}); continue; }
      const k=libKw(p);
      if(!k){ res.skipped.push({filename:it.filename, keyword:p, reason:'not in keyword library'}); continue; }
      todo.push([p,k]);
    }
    for(const [p,k] of todo){
      if(dry){ res.applied.push({filename:it.filename, id:b.id, keyword:p, dry:true}); continue; }
      try{ C1.applyKeyword(k,{to:[v]}); res.applied.push({filename:it.filename, id:b.id, keyword:p}); }
      catch(e){ res.failures.push({filename:it.filename, keyword:p, reason:String(e)}); }
    }
    if(it.description){
      let cur=''; try{ cur=v.contentDescription()||''; }catch(e){}
      if(cur.trim()!==''){ res.skipped.push({filename:it.filename, field:'content description', reason:'description not empty (kept; text goes to sidecar)'}); }
      else if(dry){ res.applied.push({filename:it.filename, id:b.id, description:it.description, dry:true}); }
      else { try{ v.contentDescription=it.description; res.applied.push({filename:it.filename, id:b.id, description:it.description}); }
             catch(e){ res.failures.push({filename:it.filename, field:'content description', reason:String(e)}); } }
    }
    const a={id:v.id(), filename:it.filename, keywords:kwList(v), rating:v.rating(), color:v.colorTag()};
    a.lost=b.keywords.filter(x=>a.keywords.indexOf(x)<0);
    a.flags_changed=(a.rating!==b.rating||a.color!==b.color);
    if(a.lost.length||a.flags_changed) res.failures.push({filename:it.filename, reason:'post-check: lost='+a.lost+' flags_changed='+a.flags_changed});
    res.after.push(a);
  }
  res.catalog_keywords=d.keywords().map(k=>({name:k.name(), parent:k.parent()}));
  writeJSON(argv[1],res);
  return JSON.stringify({applied:res.applied.length, skipped:res.skipped.length, failures:res.failures.length});
}
