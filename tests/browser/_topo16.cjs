const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  const tray = await page.evaluate(()=>{const t=document.querySelector('#pending-tray');return t?t.innerText.replace(/\s+/g,' ').slice(0,120):null;});
  console.log('TRAY after reload:', JSON.stringify(tray));
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  const tabs = await page.evaluate(()=>[...document.querySelectorAll('.topo-subnav-btn')].map(b=>b.getAttribute('data-view')));
  console.log('TABS:', JSON.stringify(tabs));
  const report={};
  for (const v of tabs) {
    await page.click('.topo-subnav-btn[data-view="'+v+'"]');
    await H.sleep(2200);
    const s = await page.evaluate((view)=>{
      const secs=[...document.querySelectorAll('.topo-section')].filter(e=>{
        const r=e.getBoundingClientRect(); const st=getComputedStyle(e);
        return st.display!=='none' && st.visibility!=='hidden' && r.height>2;
      });
      // what's actually near the top of the viewport now
      const vis = secs.map(e=>({id:e.id||e.getAttribute('data-topo-section'), top:Math.round(e.getBoundingClientRect().top), h:Math.round(e.getBoundingClientRect().height),
        txt:e.innerText.replace(/\s+/g,' ').slice(0,180)}));
      const empties = [...document.querySelectorAll('.topo-section')].map(e=>({id:e.id||e.getAttribute('data-topo-section'), len:e.innerText.trim().length}));
      return {nVisible:secs.length, vis: vis.filter(x=>x.top>-200&&x.top<900).slice(0,3), empties, scrollY: Math.round(window.scrollY)};
    }, v);
    report[v]={scrollY:s.scrollY, near:s.vis.map(x=>x.id+' @'+x.top), sample: (s.vis[0]||{}).txt};
    await H.shot(page,'topo-tab-'+v);
  }
  console.log(JSON.stringify(report,null,1));
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
