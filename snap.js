// usage: osascript -l JavaScript snap.js out.json [name ...]   (no names = whole current collection: rating/colour only)
//        osascript -l JavaScript snap.js out.json --folder <abs folder>   (rating/colour of that folder's variants only;
//        selects the "<shoot> · Apply scope" smart album, then restores the previous collection)
ObjC.import('Foundation');

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
