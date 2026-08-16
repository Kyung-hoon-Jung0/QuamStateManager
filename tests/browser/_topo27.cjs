const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const run = async (page, dismiss) => {
  if (dismiss) { await page.evaluate(()=>{const x=document.querySelector('.tray-teach-x'); if(x)x.click();}); await H.sleep(1500); }
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  for(let i=0;i<20;i++){ await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop += 500;}); await H.sleep(280); }
  await H.sleep(2500);
  const rows=[];
  for (const st of [0, 500, 1000, 2000, 3000, 4000, 5000, 6000, 6700]) {
    await page.evaluate(t=>{document.querySelector('#table-pane').scrollTop=t;}, st);
    await H.sleep(700);
    const r = await page.evaluate(()=>{
      const nav=document.querySelector('.topo-subnav'); const nr=nav.getBoundingClientRect();
      const tray=document.querySelector('#pending-tray');
      const btns=[...document.querySelectorAll('.topo-subnav-btn')].map(b=>{
        const q=b.getBoundingClientRect(); const el=document.elementFromPoint(Math.round(q.x+q.width/2),Math.round(q.y+q.height/2));
        return el===b;
      });
      return {navY:Math.round(nr.y), navB:Math.round(nr.bottom), trayB: Math.round(tray.getBoundingClientRect().bottom),
        winY: Math.round(window.scrollY), nClear: btns.filter(Boolean).length, n: btns.length};
    });
    rows.push({scrollTop:st, ...r});
  }
  return rows;
};
(async () => {
  let { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  console.log('=== teaching banner PRESENT ===');
  (await run(page,false)).forEach(r=>console.log(JSON.stringify(r)));
  await browser.close();

  ({ browser, page } = await H.open({ port: 8844 }));
  await H.goto(page, '/', 3000);
  console.log('=== teaching banner DISMISSED ===');
  (await run(page,true)).forEach(r=>console.log(JSON.stringify(r)));
  await H.shot(page,'topo-16-final');
  await browser.close();
})();
