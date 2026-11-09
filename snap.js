// usage: osascript -l JavaScript snap.js out.json [name ...]   (no names = whole current collection: rating/colour only)
ObjC.import('Foundation');
function run(argv){
  const C=Application('Capture One'), d=C.currentDocument(), vs=d.variants;
  let out={};
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
