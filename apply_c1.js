// JXA: additively apply keywords from the "Trip Keywords" library to variants in the open Capture One catalog.
// usage: osascript -l JavaScript apply_c1.js <plan.json> <result.json> [dry]
// plan: {"folder": "<abs folder path>", "library": "Trip Keywords",
//        "items": [{"filename": "_A3A3807.CR3", "path": "<abs path>", "keywords": ["Motorsport/Car Colour/Green", ...]}]}
// Never removes keywords, never changes ratings/colour tags. Keywords not found in the library are skipped (no flat ones).
ObjC.import('Foundation');
function readJSON(p){ return JSON.parse($.NSString.stringWithContentsOfFileEncodingError(p,4,null).js); }
function writeJSON(p,o){ $.NSString.alloc.initWithUTF8String(JSON.stringify(o,null,1)).writeToFileAtomicallyEncodingError(p,true,4,null); }
function kwList(v){ return v.keywords().map(k=>{const pa=k.parent(); return (pa? pa.split('|').join('/')+'/' : '')+k.name();}); }
function run(argv){
  const plan=readJSON(argv[0]); const dry=argv[2]==='dry';
  const C1=Application('Capture One'); const d=C1.currentDocument();
  const lib=C1.keywordLibraries.whose({name:plan.library||'Trip Keywords'});
  if(!lib.length) throw new Error('keyword library not found');
  const L=lib[0]; const kwCache={};
  function libKw(path){
    if(path in kwCache) return kwCache[path];
    const parts=path.split('/'); const name=parts.pop(); const parent=parts.join('|');
    const m=L.keywords.whose({name:name,parent:parent}); kwCache[path]=m.length? m[0]:null; return kwCache[path];
  }
  // variants of current collection, indexed by name (base name without extension)
  const cur=d.currentCollection().name();
  const vs=d.variants; const names=vs.name(); const idx={};
  names.forEach((n,i)=>{ (idx[n]=idx[n]||[]).push(i); });
  const res={dry:dry, collection:cur, before:[], applied:[], skipped:[], failures:[], after:[]};
  for(const it of plan.items){
    const base=it.filename.replace(/\.[^.]+$/,'');
    const cands=(idx[base]||[]).map(i=>vs[i]).filter(v=>{ try{return v.parentImage().path()===it.path}catch(e){return false} });
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
