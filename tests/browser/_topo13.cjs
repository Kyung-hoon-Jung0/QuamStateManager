const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const snap = async (page) => page.evaluate(()=>{
  const o={};
  document.querySelectorAll('g[data-hero-qubit]').forEach(g=>{
    const id=g.getAttribute('data-hero-qubit');
    const c=g.querySelector('circle');
    const ts=[...g.querySelectorAll('text')].map(t=>t.textContent);
    o[id]={val: ts[1], fill: getComputedStyle(c).fill, cls: c.getAttribute('class'), title: (g.querySelector('title')||{}).textContent};
  });
  const sel=[...document.querySelectorAll('.topo-hero-mbtn')].find(b=>b.getAttribute('aria-selected')==='true');
  return {nodes:o, selected: sel && sel.getAttribute('data-hero-metric'), legend: (document.querySelector('.topo-hero-legend')||{}).innerText};
});
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  const t1 = await snap(page);
  await H.shot(page,'topo-08-metric-T1');
  console.log('=== T1 ===', 'selected=',t1.selected, 'legend=',JSON.stringify(t1.legend));
  console.log(JSON.stringify(t1.nodes,null,0).replace(/},/g,'},\n'));
  // switch to Diagnostics
  await page.click('.topo-hero-mbtn[data-hero-metric="diag"]');
  await H.sleep(1500);
  const dg = await snap(page);
  await H.shot(page,'topo-09-metric-diag');
  console.log('=== DIAG ===','selected=',dg.selected,'legend=',JSON.stringify(dg.legend));
  const vals = Object.fromEntries(Object.entries(dg.nodes).map(([k,v])=>[k,v.val]));
  console.log(JSON.stringify(vals));
  // switch back
  await page.click('.topo-hero-mbtn[data-hero-metric="T1"]');
  await H.sleep(1200);
  const back = await snap(page);
  console.log('=== BACK TO T1 === q1=',JSON.stringify(back.nodes.q1),'q2=',JSON.stringify(back.nodes.q2));
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
