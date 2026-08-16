const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.evaluate(()=>{const x=document.querySelector('.tray-teach-x'); if(x)x.click();});
  await H.sleep(1500);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  for(let i=0;i<20;i++){ await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop += 500;}); await H.sleep(300); }
  await H.sleep(2500);
  console.log('groups present:', await page.evaluate(()=>[...new Set([...document.querySelectorAll('#topo-metric-panels [data-group]')].map(e=>e.getAttribute('data-group')))]));
  for (const v of ['calibration','fidelity','coherence','frequencies']) {
    await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop=4000;});
    await H.sleep(1200);
    const before = await page.evaluate(()=>Math.round(document.querySelector('#table-pane').scrollTop));
    const p = await page.evaluate((view)=>{const b=document.querySelector('.topo-subnav-btn[data-view="'+view+'"]');const q=b.getBoundingClientRect();
      const cx=Math.round(q.x+q.width/2),cy=Math.round(q.y+q.height/2);
      return {x:cx,y:cy,hit:(document.elementFromPoint(cx,cy)===b)};},v);
    await page.mouse.click(p.x,p.y);
    await H.sleep(3200);
    const a = await page.evaluate(()=>({top:Math.round(document.querySelector('#table-pane').scrollTop),
      active:[...document.querySelectorAll('.topo-subnav-btn')].filter(b=>b.classList.contains('active')).map(b=>b.getAttribute('data-view'))[0],
      atTop:(()=>{const s=[...document.querySelectorAll('#topo-metric-panels [data-group], .topo-section')]
        .map(e=>({n:(e.getAttribute('data-group')||e.id||e.getAttribute('data-topo-section')),t:Math.round(e.getBoundingClientRect().top)}))
        .filter(x=>x.t>-60&&x.t<300).sort((a,b)=>a.t-b.t)[0]; return s;})()}));
    console.log(v.padEnd(13),'clickHitButton=',p.hit,'| paneTop',before,'->',a.top,'| active=',a.active,'| atTop=',JSON.stringify(a.atTop));
    await H.shot(page,'tabland-'+v);
  }
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
