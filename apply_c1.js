// JXA: additively apply keywords from the "Trip Keywords" library to variants in the open Capture One catalog.
// usage: osascript -l JavaScript apply_c1.js <plan.json> <result.json> [dry]
// plan: {"folder": "<abs folder path>", "library": "Trip Keywords",
//        "items": [{"filename": "_A3A3807.CR3", "path": "<abs path>", "keywords": ["Motorsport/Car Colour/Green", ...]}]}
// Never removes keywords, never changes ratings/colour tags. Keywords not found in the library are skipped (no flat ones).
ObjC.import('Foundation');
function readJSON(p){ return JSON.parse($.NSString.stringWithContentsOfFileEncodingError(p,4,null).js); }
function writeJSON(p,o){ $.NSString.alloc.initWithUTF8String(JSON.stringify(o,null,1)).writeToFileAtomicallyEncodingError(p,true,4,null); }
function kwList(v){ return v.keywords().map(k=>{const pa=k.parent(); return (pa? pa.split('|').join('/')+'/' : '')+k.name();}); }

// ---- folder scope (2026-11-09 fix): never trust whatever collection Capture One is showing.
// Selects a smart album "<shoot> · Apply scope" (group "Trips Local AI", rule folderPath contains <shoot>,
// same mechanism as cull_album.py), keeps only variants whose image is directly in <folder>, and the caller
// restores the previous collection afterwards.
function scopeToFolder(C1, d, folder){
  const shoot=folder.replace(/\/+$/,'').split('/').pop();
  const GROUP='Trips Local AI', NAME=shoot+' · Apply scope';
  const esc=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const rules='<?xml version="1.0" encoding="UTF-8"?><MatchOperator Kind="AND"><Condition Enabled="YES"><Key>folderPath</Key><Operator>6</Operator><Criterion>'+esc(shoot)+'</Criterion></Condition></MatchOperator>';
  // (JXA cannot filter on the kind enumeration in whose(); filter by name, then check kind)
  let gs=d.collections.whose({name:GROUP})().filter(c=>c.kind()==='group');
  // (JXA make() fails with "Can't convert types"; push a new Collection object instead)
  if(!gs.length){ d.collections.push(C1.Collection({kind:'group', name:GROUP})); gs=d.collections.whose({name:GROUP})().filter(c=>c.kind()==='group'); }
  const g=gs[0];
  let ss=g.collections.whose({name:NAME})().filter(c=>c.kind()==='smart album');
  if(!ss.length){ g.collections.push(C1.Collection({kind:'smart album', name:NAME, rules:rules})); ss=g.collections.whose({name:NAME})().filter(c=>c.kind()==='smart album'); }
  if(!ss.length) throw new Error('could not create scope collection '+NAME);
  const s=ss[0];
  d.currentCollection=s;
  const cur=d.currentCollection();
  if(cur.name()!==NAME) throw new Error('could not select scope collection '+NAME+' (showing '+cur.name()+')');
  return NAME;
}
function folderOf(p){ return String(p).replace(/\/[^\/]*$/,''); }
function run(argv){
  const plan=readJSON(argv[0]); const dry=argv[2]==='dry';
  const C1=Application('Capture One'); const d=C1.currentDocument();
  const prev=d.currentCollection();
  try { return runScoped(argv, plan, dry, C1, d); }
  finally { if(plan.scope_folder){ try{ d.currentCollection=prev; }catch(e){} } }
}
function runScoped(argv, plan, dry, C1, d){
  const lib=C1.keywordLibraries.whose({name:plan.library||'Trip Keywords'});
  if(!lib.length) throw new Error('keyword library not found');
  const L=lib[0]; const kwCache={};
  function libKw(path){
    if(path in kwCache) return kwCache[path];
    const parts=path.split('/'); const name=parts.pop(); const parent=parts.join('|');
    const m=L.keywords.whose({name:name,parent:parent}); kwCache[path]=m.length? m[0]:null; return kwCache[path];
  }
  let scope=null, scopedPaths=null;
  if(plan.scope_folder){
    const folder=String(plan.scope_folder).replace(/\/+$/,'');
    const name=scopeToFolder(C1, d, folder);
    const paths=d.variants.parentImage.path();             // bulk read, scope collection only
    scopedPaths=paths;
    const inFolder=paths.filter(p=>folderOf(p)===folder);
    const have={}; inFolder.forEach(p=>{ have[p.split('/').pop()]=1; });
    const missing=plan.items.filter(it=>!have[it.filename]).map(it=>it.filename);
    scope={collection:name, folder:folder, variants:paths.length, in_folder:inFolder.length,
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
