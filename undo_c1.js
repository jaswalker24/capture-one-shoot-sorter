// JXA: remove keywords that a prior apply added. Never changes rating/colour.
// usage: osascript -l JavaScript undo_c1.js plan.json result.json [dry]
// plan.json: { library?: "Trip Keywords", items: [{filename, keywords: ["Gig/Act/X", ...]}] }
ObjC.import('Foundation');
function kwList(v){
  return v.keywords().map(k=>{const pa=k.parent(); return (pa? pa.split('|').join('/')+'/' : '')+k.name();});
}
function findKw(L, path, cache){
  if(cache[path]!==undefined) return cache[path];
  const parts=path.split('/'); const name=parts.pop(); const parent=parts.length? parts.join('|') : null;
  const m=L.keywords.whose({name:name, parent:parent});
  cache[path]=m.length? m[0]:null; return cache[path];
}
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
function run(argv){
  const plan=JSON.parse($.NSString.stringWithContentsOfFileEncodingError(argv[0],4,null).js);
  const C1=Application('Capture One'), d=C1.currentDocument(); const prev=d.currentCollection();
  try { return runScoped(argv, plan, C1, d); }
  finally { if(plan.scope_folder){ try{ d.currentCollection=prev; }catch(e){} } }
}
function runScoped(argv, plan, C1, d){
  const outPath=argv[1]; const dry=argv.length>2 && argv[2]==='dry';
  // folder scope: select the job's folder and match variants by image path, never by name alone
  let byName=null;
  if(plan.scope_folder){
    const folder=String(plan.scope_folder).replace(/\/+$/,'');
    scopeToFolder(C1, d, folder);
    const vs=d.variants, names=vs.name(), paths=vs.parentImage.path(), vids=vs.id(); byName={};
    // store variant ids (not indexes) so a collection switch mid-run can't retarget a removal
    names.forEach((n,i)=>{ if(folderOf(paths[i])===folder) (byName[n]=byName[n]||[]).push(vids[i]); });
    if(!Object.keys(byName).length){
      const res={dry:!!dry, refused:'no variants of '+folder+' in Capture One', removed:[], skipped:[], before:[], after:[],
                 failures:(plan.items||[]).map(it=>({filename:it.filename, reason:'refused: folder not found in Capture One'}))};
      $.NSString.alloc.initWithUTF8String(JSON.stringify(res)).writeToFileAtomicallyEncodingError(outPath,true,4,null);
      return 0;
    }
  }
  const lib=C1.keywordLibraries.whose({name:plan.library||'Trip Keywords'});
  if(!lib.length) throw new Error('keyword library not found');
  const L=lib[0]; const kwCache={};
  const res={dry:!!dry, removed:[], skipped:[], failures:[], before:[], after:[]};
  for(const it of (plan.items||[])){
    let v;
    try{
      if(byName){
        const ix=byName[String(it.filename).replace(/\.[^.]+$/,'')]||byName[it.filename]||[];
        if(ix.length!==1) throw new Error(ix.length? 'multiple variants in folder' : 'not in this folder in Capture One');
        v=d.variants.byId(ix[0]);
      } else { v=d.variants.whose({name:it.filename})[0]; }
      if(!v) throw new Error('not in catalog');
    }
    catch(e){ res.failures.push({filename:it.filename, reason:String(e)}); continue; }
    const b={id:v.id(), filename:it.filename, keywords:kwList(v), rating:v.rating(), color:v.colorTag()};
    res.before.push(b);
    for(const p of (it.keywords||[])){
      if(b.keywords.indexOf(p)<0){ res.skipped.push({filename:it.filename, keyword:p, reason:'not present'}); continue; }
      const k=findKw(L,p,kwCache);
      if(!k){ res.skipped.push({filename:it.filename, keyword:p, reason:'not in keyword library'}); continue; }
      if(dry){ res.removed.push({filename:it.filename, id:b.id, keyword:p, dry:true}); continue; }
      try{
        // Capture One JXA: removeKeyword
        C1.removeKeyword(k,{from:[v]});
        res.removed.push({filename:it.filename, id:b.id, keyword:p});
      }catch(e){ res.failures.push({filename:it.filename, keyword:p, reason:String(e)}); }
    }
    const a={id:v.id(), filename:it.filename, keywords:kwList(v), rating:v.rating(), color:v.colorTag()};
    // safety: ratings/colours must match before
    if(a.rating!==b.rating || a.color!==b.color){
      res.failures.push({filename:it.filename, reason:'rating/colour changed unexpectedly', before:{rating:b.rating,color:b.color}, after:{rating:a.rating,color:a.color}});
    }
    res.after.push(a);
  }
  $.NSString.alloc.initWithUTF8String(JSON.stringify(res)).writeToFileAtomicallyEncodingError(outPath,true,4,null);
  return res.removed.length;
}
