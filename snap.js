// usage: osascript -l JavaScript snap.js out.json [name ...]   (no names = whole current collection: rating/colour only)
//        osascript -l JavaScript snap.js out.json --folder <abs folder>   (rating/colour of that folder's variants only;
//        selects the catalog folder collection for that path, then restores the previous collection)
ObjC.import('Foundation');

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
  const C=Application('Capture One'), d=C.currentDocument();
  let out={};
  if(argv[1]==='--folder'){
    const folder=String(argv[2]).replace(/\/+$/,''); const prev=d.currentCollection();
    try{
      scopeToFolder(C, d, folder);
      const vs=d.variants, ids=vs.id(), r=vs.rating(), c=vs.colorTag(), n=vs.name(), p=vs.parentImage.path();
      ids.forEach((id,i)=>{ if(folderOf(p[i])===folder) out[id]={name:n[i],rating:r[i],color:c[i]}; });
    } finally { try{ d.currentCollection=prev; }catch(e){} }
    $.NSString.alloc.initWithUTF8String(JSON.stringify(out)).writeToFileAtomicallyEncodingError(argv[0],true,4,null);
    return Object.keys(out).length;
  }
  const vs=d.variants;
  if(argv.length>1){
    for(const n of argv.slice(1)){ const v=d.variants.whose({name:n})[0];
      out[n]={id:v.id(),rating:v.rating(),color:v.colorTag(),desc:v.contentDescription(),
        kw:v.keywords().map(k=>(k.parent()?k.parent().split('|').join('/')+'/':'')+k.name()).sort()}; }
  } else {
    const ids=vs.id(), r=vs.rating(), c=vs.colorTag(), n=vs.name();
    ids.forEach((id,i)=>{ out[id]={name:n[i],rating:r[i],color:c[i]}; });
  }
  $.NSString.alloc.initWithUTF8String(JSON.stringify(out)).writeToFileAtomicallyEncodingError(argv[0],true,4,null);
  return Object.keys(out).length;
}
