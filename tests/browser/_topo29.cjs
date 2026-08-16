const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const m = (page)=>page.evaluate(()=>{
  const nav=document.querySelector('.topo-subnav'); const nr=nav.getBoundingClientRect();
  const tray=document.querySelector('#pending-tray');
  const btns=[...document.querySelectorAll('.topo-subnav-btn')].map(b=>{const q=b.getBoundingClientRect();
    return document.elementFromPoint(Math.round(q.x+q.width/2),Math.round(q.y+q.height/2))===b;});
  return {winY:Math.round(window.scrollY), docH:document.documentElement.scrollHeight, innerH:window.innerHeight,
    paneTop:Math.round(document.querySelector('#table-pane').scrollTop),
    navY:Math.round(nr.y), trayB:Math.round(tray.getBoundingClientRect().bottom), clear:btns.filter(Boolean).length};
});
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.evaluate(()=>{const x=document.querySelector('.tray-teach-x'); if(x)x.click();});
  await H.sleep(1500);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  for(let i=0;i<20;i++){ await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop += 500;}); await H.sleep(280); }
  await H.sleep(2000);
  await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop=3500; window.scrollTo(0,0);});
  await H.sleep(900);
  console.log('banner dismissed, window at top :', JSON.stringify(await m(page)));
  await page.evaluate(()=>window.scrollTo(0, 200));
  await H.sleep(900);
  console.log('banner dismissed, window scrolled:', JSON.stringify(await m(page)));
  await H.shot(page,'BUG-subnav-winscroll');
  await page.evaluate(()=>window.scrollTo(0,0));
  await H.sleep(900);
  console.log('window back to top             :', JSON.stringify(await m(page)));
  await browser.close();
})();
