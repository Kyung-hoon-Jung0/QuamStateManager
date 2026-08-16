const fs=require('fs');
const truth=JSON.parse(fs.readFileSync('D:/work/statemanager-cfb/tests/browser/_shots/_chip_truth.json','utf8'));
const g=JSON.parse(fs.readFileSync('D:/work/statemanager-cfb/tests/browser/_shots/_topo_rects.json','utf8'));
const nm=r=>String(r||'').split('/').pop();
const N=g.nodes;
// 1. M beside its own end
const byPair={}; g.marks.forEach(m=>{(byPair[m.pair]=byPair[m.pair]||{})[m.role]=m;});
const wrong=[];
for(const [pid,p] of Object.entries(truth.pairs)){
  const c=nm(p.control), t=nm(p.target), mv=p.moving==='control'?c:t;
  const M=byPair[pid].M, other=(mv===c)?t:c;
  const d1=Math.hypot(M.r.cx-N[mv].cx,M.r.cy-N[mv].cy), d2=Math.hypot(M.r.cx-N[other].cx,M.r.cy-N[other].cy);
  if(!(d1<d2)) wrong.push({pid,mv,d1:+d1.toFixed(1),d2:+d2.toFixed(1)});
}
console.log('M nearer its own qubit: wrong=',wrong.length, JSON.stringify(wrong.slice(0,6)));
// also C nearer control, T nearer target
const wrong2=[];
for(const [pid,p] of Object.entries(truth.pairs)){
  const c=nm(p.control), t=nm(p.target);
  const C=byPair[pid].C, T=byPair[pid].T;
  const cd=Math.hypot(C.r.cx-N[c].cx,C.r.cy-N[c].cy) < Math.hypot(C.r.cx-N[t].cx,C.r.cy-N[t].cy);
  const td=Math.hypot(T.r.cx-N[t].cx,T.r.cy-N[t].cy) < Math.hypot(T.r.cx-N[c].cx,T.r.cy-N[c].cy);
  if(!cd||!td) wrong2.push({pid,cOk:cd,tOk:td});
}
console.log('C/T on correct end: wrong=',wrong2.length, JSON.stringify(wrong2.slice(0,6)));
// 2. chevrons: apex toward LOWER f_01
const F=truth.qubits;
const cbad=[]; const cskip=[];
g.chev.forEach(ch=>{
  const [a,b]=ch.key.split('|');
  const fa=F[a]&&F[a].f01, fb=F[b]&&F[b].f01;
  if(typeof fa!=='number'||typeof fb!=='number'){cskip.push(ch.key);return;}
  const lower = fa<fb?a:b;
  // apex = middle point of polyline
  const apex=ch.pts[1], p0=ch.pts[0], p2=ch.pts[2];
  // direction of apex relative to the segment's base midpoint
  const baseMid=[(p0[0]+p2[0])/2,(p0[1]+p2[1])/2];
  const dir=[apex[0]-baseMid[0], apex[1]-baseMid[1]];
  // node coords in SVG user space: recover from edges? use screen: convert via svg rect scale
  cbad.push({key:ch.key, lower, dir, title:ch.title});
});
// verify direction using screen coords: chevron rect center vs node centers along dir
const res=[];
g.chev.forEach(ch=>{
  const [a,b]=ch.key.split('|');
  const fa=F[a].f01, fb=F[b].f01;
  const lower=fa<fb?a:b, higher=fa<fb?b:a;
  const apex=ch.pts[1], p0=ch.pts[0], p2=ch.pts[2];
  const baseMid=[(p0[0]+p2[0])/2,(p0[1]+p2[1])/2];
  const dir=[apex[0]-baseMid[0], apex[1]-baseMid[1]];
  // node direction in screen space (same orientation as svg user space, uniform scale, no flip)
  const nd=[N[lower].cx-N[higher].cx, N[lower].cy-N[higher].cy];
  const dot=dir[0]*nd[0]+dir[1]*nd[1];
  const dMHz=Math.abs(fa-fb)/1e6;
  res.push({key:ch.key, pointsAtLower: dot>0, dMHz:+dMHz.toFixed(1), title:ch.title});
});
const bad=res.filter(r=>!r.pointsAtLower);
console.log('chevrons=',res.length,'pointing at LOWER f_01 =', res.length-bad.length, 'WRONG=',bad.length);
if(bad.length) console.log(JSON.stringify(bad,null,1));
// title arithmetic check
const tbad=[];
res.forEach(r=>{
  const m=/Δf_01:\s*(\S+)\s*\+([\d.]+)\s*MHz vs (\S+)/.exec(r.title||'');
  if(!m){tbad.push({t:r.title, why:'unparsed'});return;}
  const hi=m[1], val=+m[2], lo=m[3];
  const real=(F[hi].f01-F[lo].f01)/1e6;
  if(Math.abs(real-val)>0.6||real<0) tbad.push({title:r.title, computed:real});
});
console.log('chevron title arithmetic bad=',tbad.length, JSON.stringify(tbad.slice(0,5)));
// pairs with no chevron
const chevKeys=new Set(g.chev.map(c=>c.key.split('|').sort().join('|')));
const missing=Object.entries(truth.pairs).filter(([pid,p])=>{
  const k=[nm(p.control),nm(p.target)].sort().join('|'); return !chevKeys.has(k);
}).map(x=>x[0]);
console.log('pairs without chevron:', missing.length, missing);
// font sizes of role markers
const fss=new Set(g.marks.map(m=>m.fs)); console.log('role marker font sizes:', [...fss]);
