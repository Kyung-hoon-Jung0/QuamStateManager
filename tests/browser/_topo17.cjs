const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  const panes = await page.evaluate(()=>{
    const cands=['#table-pane','#main-pane','.main-pane','body','html'];
    const o={};
    cands.forEach(s=>{const e=document.querySelector(s); if(e) o[s]={sh:e.scrollHeight, ch:e.clientHeight, st:e.scrollTop, overflow:getComputedStyle(e).overflowY};});
    o.window={scrollY:window.scrollY, docH:document.documentElement.scrollHeight, innerH:window.innerHeight};
    return o;
  });
  console.log('PANES:', JSON.stringify(panes,null,1));
  const tabs=['topology','overview','distributions','gate','fidelity','coherence','frequencies','calibration','trends'];
  for(const v of tabs){
    await page.click('.topo-subnav-btn[data-view="'+v+'"]');
    await H.sleep(2000);
    const s=await page.evaluate((view)=>{
      const act=[...document.querySelectorAll('.topo-subnav-btn')].filter(b=>b.classList.contains('active')||b.getAttribute('aria-selected')==='true').map(b=>b.getAttribute('data-view'));
      const tp=document.querySelector('#table-pane');
      const sel={topology:'#sec-topology',overview:'[data-topo-section="overview"]',distributions:'[data-topo-section="distributions"]',
        gate:'[data-topo-section="2qrb"]',fidelity:'[data-topo-section="metrics"]',coherence:'[data-topo-section="metrics"]',
        frequencies:'[data-topo-section="metrics"]',calibration:'[data-topo-section="metrics"]',trends:'[data-topo-section="trends"]'};
      const el=document.querySelector(sel[view]);
      return {active:act, paneScrollTop: tp?Math.round(tp.scrollTop):null, winY:Math.round(window.scrollY),
        targetTop: el?Math.round(el.getBoundingClientRect().top):null,
        targetH: el?Math.round(el.getBoundingClientRect().height):null,
        targetTxt: el?el.innerText.replace(/\s+/g,' ').slice(0,110):null};
    }, v);
    console.log(v.padEnd(14), JSON.stringify(s));
  }
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
