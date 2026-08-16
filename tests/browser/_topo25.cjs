const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  // dismiss the teaching banner FIRST, at the top of the page where it is clickable
  const d = await page.evaluate(()=>{
    const x=document.querySelector('.tray-teach-x');
    if(!x) return 'no .tray-teach-x';
    x.click(); return 'clicked .tray-teach-x';
  });
  console.log('dismiss ->', d);
  await H.sleep(2000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  const top = await page.evaluate(()=>{
    const tray=document.querySelector('#pending-tray');
    return {teachPresent: !!document.querySelector('.tray-teach'), trayBottom: Math.round(tray.getBoundingClientRect().bottom)};
  });
  console.log('at page top:', JSON.stringify(top));
  for(let i=0;i<20;i++){ await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop += 500;}); await H.sleep(300); }
  await H.sleep(2500);
  const r = await page.evaluate(()=>{
    const nav=document.querySelector('.topo-subnav'); const nr=nav.getBoundingClientRect();
    const tray=document.querySelector('#pending-tray');
    const btns=[...document.querySelectorAll('.topo-subnav-btn')].map(b=>{
      const q=b.getBoundingClientRect(); const cx=Math.round(q.x+q.width/2), cy=Math.round(q.y+q.height/2);
      const el=document.elementFromPoint(cx,cy);
      return {v:b.getAttribute('data-view'), blockedBy: el===b?null:(el?el.tagName+'.'+String(el.className).slice(0,30):'none')};
    });
    return {teachPresent:!!document.querySelector('.tray-teach'),
      navRect:{y:Math.round(nr.y),b:Math.round(nr.bottom)},
      trayBottom: Math.round(tray.getBoundingClientRect().bottom),
      blocked: btns.filter(b=>b.blockedBy).map(b=>b.v+'<-'+b.blockedBy), clear: btns.filter(b=>!b.blockedBy).map(b=>b.v)};
  });
  console.log('scrolled, teaching dismissed:', JSON.stringify(r,null,1));
  await H.shot(page,'topo-15-subnav-nobanner');
  // try an actual click on 'calibration'
  const before = await page.evaluate(()=>Math.round(document.querySelector('#table-pane').scrollTop));
  const p = await page.evaluate(()=>{const b=document.querySelector('.topo-subnav-btn[data-view="calibration"]');const q=b.getBoundingClientRect();return{x:Math.round(q.x+q.width/2),y:Math.round(q.y+q.height/2)};});
  await page.mouse.click(p.x,p.y);
  await H.sleep(3000);
  const after = await page.evaluate(()=>({top:Math.round(document.querySelector('#table-pane').scrollTop),
    active:[...document.querySelectorAll('.topo-subnav-btn')].filter(b=>b.classList.contains('active')).map(b=>b.getAttribute('data-view'))[0]}));
  console.log('click calibration: paneTop',before,'->',after.top,'active=',after.active);
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
