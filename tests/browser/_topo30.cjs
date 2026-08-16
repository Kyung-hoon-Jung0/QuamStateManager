const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.evaluate(()=>{const x=document.querySelector('.tray-teach-x'); if(x)x.click();});
  await H.sleep(1500);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  for(let i=0;i<20;i++){ await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop += 500;}); await H.sleep(280); }
  await H.sleep(2500);
  for (const v of ['topology','overview','distributions','gate','fidelity','coherence','frequencies','calibration','trends']) {
    await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop=3500; window.scrollTo(0,0);});
    await H.sleep(900);
    const p = await page.evaluate((view)=>{const b=document.querySelector('.topo-subnav-btn[data-view="'+view+'"]');const q=b.getBoundingClientRect();
      const cx=Math.round(q.x+q.width/2),cy=Math.round(q.y+q.height/2);
      return {x:cx,y:cy,hit:document.elementFromPoint(cx,cy)===b};},v);
    if(!p.hit){ console.log(v,'SKIP - occluded'); continue; }
    await page.mouse.click(p.x,p.y);
    await H.sleep(3200);
    const a = await page.evaluate(()=>({top:Math.round(document.querySelector('#table-pane').scrollTop),
      active:[...document.querySelectorAll('.topo-subnav-btn')].filter(b=>b.classList.contains('active')).map(b=>b.getAttribute('data-view'))[0],
      atTop:(()=>{const s=[...document.querySelectorAll('#topo-metric-panels [data-group],.topo-section')]
        .map(e=>({n:e.getAttribute('data-group')||e.id||e.getAttribute('data-topo-section'),t:Math.round(e.getBoundingClientRect().top)}))
        .filter(x=>x.t>-60&&x.t<260).sort((a,b)=>a.t-b.t)[0];return s;})()}));
    console.log(v.padEnd(13),'| 3500 ->',String(a.top).padStart(5),'| active=',String(a.active).padEnd(13),'| atTop=',JSON.stringify(a.atTop));
  }
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
