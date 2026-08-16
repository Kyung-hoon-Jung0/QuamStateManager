const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  for(let i=0;i<20;i++){ await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop += 500;}); await H.sleep(350); }
  await H.sleep(3000);
  const exists = await page.evaluate(()=>({
    fidelity: !!document.querySelector('#topo-metric-panels [data-group="fidelity"]'),
    coherence: !!document.querySelector('#topo-metric-panels [data-group="coherence"]'),
    frequency: !!document.querySelector('#topo-metric-panels [data-group="frequency"]'),
    calibration: !!document.querySelector('#topo-metric-panels [data-group="calibration"]'),
    groups: [...new Set([...document.querySelectorAll('#topo-metric-panels [data-group]')].map(e=>e.getAttribute('data-group')))],
  }));
  console.log('TARGET SECTIONS EXIST?', JSON.stringify(exists));
  for (const v of ['fidelity','coherence','frequencies','calibration']) {
    await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop=3000;});
    await H.sleep(1200);
    const before = await page.evaluate(()=>Math.round(document.querySelector('#table-pane').scrollTop));
    const p = await page.evaluate((view)=>{const b=document.querySelector('.topo-subnav-btn[data-view="'+view+'"]');const r=b.getBoundingClientRect();return{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),vis:r.top>0&&r.bottom<1000};},v);
    await page.mouse.click(p.x,p.y);
    await H.sleep(3500);
    const a = await page.evaluate(()=>({top:Math.round(document.querySelector('#table-pane').scrollTop),
      active:[...document.querySelectorAll('.topo-subnav-btn')].filter(b=>b.classList.contains('active')).map(b=>b.getAttribute('data-view'))[0],
      head: (()=>{const el=document.elementFromPoint(900,140); if(!el) return null; const c=el.closest('.topo-section,.topo-section-title'); return c? c.innerText.replace(/\s+/g,' ').slice(0,70):null;})()}));
    console.log(v.padEnd(13),'btnVisible=',p.vis,'paneTop',before,'->',a.top,'active=',a.active,'| atTop:',JSON.stringify(a.head));
    await H.shot(page,'land-'+v);
  }
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
