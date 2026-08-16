const fs=require('fs');
const truth=JSON.parse(fs.readFileSync('D:/work/statemanager-cfb/tests/browser/_shots/_chip_truth.json','utf8'));
const geom=JSON.parse(fs.readFileSync('D:/work/statemanager-cfb/tests/browser/_shots/_topo_geom.json','utf8'));
const nm = r => String(r||'').split('/').pop();
const bad=[]; const ok=[];
const byPair={};
geom.loose.forEach(t=>{ (byPair[t.pPair]=byPair[t.pPair]||{})[t.s]={at:t.pAt,x:t.x,y:t.y}; });
for(const [pid,p] of Object.entries(truth.pairs)){
  const c=nm(p.control), tg=nm(p.target), mv = p.moving==='control'?c:(p.moving==='target'?tg:null);
  const g=byPair[pid];
  if(!g){bad.push({pid, why:'no markers rendered'}); continue;}
  const errs=[];
  if(g.C.at!==c) errs.push(`C at ${g.C.at} expected ${c}`);
  if(g.T.at!==tg) errs.push(`T at ${g.T.at} expected ${tg}`);
  if(g.M.at!==mv) errs.push(`M at ${g.M.at} expected ${mv} (moving=${p.moving})`);
  if(errs.length) bad.push({pid, errs}); else ok.push(pid);
}
console.log('ROLE CHECK ok=',ok.length,'bad=',bad.length);
if(bad.length) console.log(JSON.stringify(bad,null,1));

// M must be geometrically BESIDE its end (nearer to the moving qubit's node)
const nodes={}; geom.nodes.forEach(n=>nodes[n.id]=n);
const far=[];
for(const [pid,g] of Object.entries(byPair)){
  const m=g.M; const n=nodes[m.at];
  if(!n) continue;
  const other = (m.at===g.C.at)? g.T.at : g.C.at;
  const no=nodes[other];
  const d1=Math.hypot(m.x-n.cx, m.y-n.cy), d2=Math.hypot(m.x-no.cx, m.y-no.cy);
  if(d1>=d2) far.push({pid, mAt:m.at, dToOwn:+d1.toFixed(1), dToOther:+d2.toFixed(1)});
}
console.log('M-placement wrong-side count=', far.length, JSON.stringify(far.slice(0,8)));

// chevron: apex at LOWER f_01
const f=truth.qubits;
console.log('sample f_01', JSON.stringify(Object.fromEntries(Object.entries(f).slice(0,5))));
