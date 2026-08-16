const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  await page.evaluate(()=>document.querySelector('#topo-hero').scrollIntoView({block:'center'}));
  await H.sleep(1000);
  const pos = await page.evaluate(sel=>{const r=document.querySelector(sel).getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2};}, 'g[data-hero-qubit="q10"] circle');
  await page.mouse.click(pos.x,pos.y);
  await H.sleep(3500);
  const afterQ = await page.evaluate(()=>{
    const ip=document.querySelector('#inspector-pane'), tp=document.querySelector('#table-pane');
    return {url:location.pathname+location.search,
      inspector: ip? ip.innerText.slice(0,220):null,
      inspectorH: ip? Math.round(ip.getBoundingClientRect().height):null};
  });
  await H.shot(page,'topo-05-qubit-click');
  console.log('AFTER QUBIT CLICK:', JSON.stringify(afterQ,null,1));

  // back to topology, click an edge
  await page.click('a[hx-get="/topology"]');
  await H.sleep(8000);
  await page.evaluate(()=>document.querySelector('#topo-hero').scrollIntoView({block:'center'}));
  await H.sleep(900);
  const ep = await page.evaluate(()=>{
    const g=document.querySelector('g[data-hero-pair="q9-10"] .topo-hero-edge-hit');
    const r=g.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2, id:'q9-10'};
  });
  await page.mouse.click(ep.x,ep.y);
  await H.sleep(3500);
  const afterE = await page.evaluate(()=>{
    const ip=document.querySelector('#inspector-pane');
    return {url:location.pathname+location.search, inspector: ip? ip.innerText.slice(0,220):null};
  });
  await H.shot(page,'topo-06-edge-click');
  console.log('AFTER EDGE CLICK (q9-10):', JSON.stringify(afterE,null,1));
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
