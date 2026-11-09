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
function run(argv){
  const plan=JSON.parse($.NSString.stringWithContentsOfFileEncodingError(argv[0],4,null).js);
  const outPath=argv[1]; const dry=argv.length>2 && argv[2]==='dry';
  const C1=Application('Capture One'), d=C1.currentDocument();
  const lib=C1.keywordLibraries.whose({name:plan.library||'Trip Keywords'});
  if(!lib.length) throw new Error('keyword library not found');
  const L=lib[0]; const kwCache={};
  const res={dry:!!dry, removed:[], skipped:[], failures:[], before:[], after:[]};
  for(const it of (plan.items||[])){
    let v;
    try{ v=d.variants.whose({name:it.filename})[0]; if(!v) throw new Error('not in catalog'); }
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
