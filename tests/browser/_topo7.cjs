const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const fs=require('fs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  await page.evaluate(()=>{const e=document.querySelector('#topo-hero'); e.scrollIntoView({block:'center'});});
  await H.sleep(1200);
  const d = await page.evaluate(() => {
    const svg=document.querySelector('.topo-hero-svg');
    const sr=svg.getBoundingClientRect();
    const R=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,r:r.right,b:r.bottom};};
    const items=[];
    svg.querySelectorAll('g[data-hero-qubit]').forEach(g=>items.push({k:'node',id:g.getAttribute('data-hero-qubit'),r:R(g.querySelector('circle'))}));
    svg.querySelectorAll('g.cm-role').forEach(g=>{const t=g.querySelector('text');items.push({k:'role',id:t.textContent+'@'+g.getAttribute('data-cm-at')+'/'+g.getAttribute('data-cm-pair'),r:R(t)});});
    // qubit label texts
    const labels=[];
    svg.querySelectorAll('g[data-hero-qubit] text').forEach(t=>labels.push({id:t.textContent,r:R(t),fs:getComputedStyle(t).fontSize,fill:getComputedStyle(t).fill}));
    // off-canvas check
    const outside=items.filter(i=>i.r.x<sr.x-1||i.r.y<sr.y-1||i.r.r>sr.x+sr.width+1||i.r.b>sr.y+sr.height+1);
    return {svgRect:{x:sr.x,y:sr.y,w:sr.width,h:sr.height}, items, labels, outside};
  });
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/_topo_overlap.json', JSON.stringify(d,null,1));
  // overlap detection node-vs-role and role-vs-role
  const ov=(a,b)=>!(a.r<=b.x||b.r<=a.x||a.b<=b.y||b.b<=a.y);
  const hits=[];
  for(let i=0;i<d.items.length;i++)for(let j=i+1;j<d.items.length;j++){
    const A=d.items[i],B=d.items[j];
    if(A.k==='node'&&B.k==='node'){ if(ov(A.r,B.r)) hits.push(['node-node',A.id,B.id]); }
    else if(ov(A.r,B.r)) hits.push([A.k+'-'+B.k,A.id,B.id]);
  }
  console.log('svgRect', JSON.stringify(d.svgRect));
  console.log('items', d.items.length, 'offCanvas', d.outside.length, JSON.stringify(d.outside.slice(0,5)));
  console.log('overlaps', hits.length);
  const tally={}; hits.forEach(h=>tally[h[0]]=(tally[h[0]]||0)+1);
  console.log(JSON.stringify(tally), JSON.stringify(hits.slice(0,12)));
  console.log('label sample', JSON.stringify(d.labels.slice(0,4)));
  // clipped zoom of a busy region
  const box = {x: d.svgRect.x+300, y: d.svgRect.y+150, width: 620, height: 420};
  await page.screenshot({path:'D:/work/statemanager-cfb/tests/browser/_shots/topo-03-zoom.png', clip: box});
  await browser.close();
})();
