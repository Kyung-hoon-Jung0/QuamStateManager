const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  // (1) screenshot of the blocked state (banner present, scrolled)
  let { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop=2500;});
  await H.sleep(1500);
  await H.shot(page,'BUG-subnav-hidden-under-tray');
  await page.screenshot({path:'D:/work/statemanager-cfb/tests/browser/_shots/BUG-subnav-hidden-zoom.png', clip:{x:280,y:0,width:1320,height:280}});
  await browser.close();

  // (2) Fidelity/Coherence fallback, banner dismissed so nothing is occluded
  ({ browser, page } = await H.open({ port: 8844 }));
  await H.goto(page, '/', 3000);
  await page.evaluate(()=>{const x=document.querySelector('.tray-teach-x'); if(x)x.click();});
  await H.sleep(1500);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  for(let i=0;i<20;i++){ await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop += 500;}); await H.sleep(280); }
  await H.sleep(2500);
  for (const v of ['topology','overview','distributions','gate','fidelity','coherence','frequencies','calibration','trends']) {
    await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop=3500;});
    await H.sleep(900);
    const p = await page.evaluate((view)=>{const b=document.querySelector('.topo-subnav-btn[data-view="'+view+'"]');const q=b.getBoundingClientRect();
      const cx=Math.round(q.x+q.width/2),cy=Math.round(q.y+q.height/2);
      return {x:cx,y:cy,hit:document.elementFromPoint(cx,cy)===b};},v);
    await page.mouse.click(p.x,p.y);
    await H.sleep(3200);
    const a = await page.evaluate(()=>({top:Math.round(document.querySelector('#table-pane').scrollTop),
      active:[...document.querySelectorAll('.topo-subnav-btn')].filter(b=>b.classList.contains('active')).map(b=>b.getAttribute('data-view'))[0]}));
    console.log(v.padEnd(13),'hit=',p.hit,'| 3500 ->',a.top,'| active=',a.active);
  }
  await H.shot(page,'BUG-fidelity-lands-top');
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
