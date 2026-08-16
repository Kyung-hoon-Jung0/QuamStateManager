const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  // Fully build every lazy section: scroll the PANE to the bottom in steps.
  for(let i=0;i<20;i++){ await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop += 500;}); await H.sleep(400); }
  await H.sleep(3000);
  const h = await page.evaluate(()=>({sh:document.querySelector('#table-pane').scrollHeight, plots:document.querySelectorAll('.js-plotly-plot').length}));
  console.log('after full build:', JSON.stringify(h));
  await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop=0;});
  await H.sleep(1500);

  const TAB={topology:'#sec-topology',overview:'[data-topo-section="overview"]',distributions:'[data-topo-section="distributions"]',
    gate:'[data-topo-section="2qrb"]',fidelity:'[data-topo-section="metrics"]',coherence:'[data-topo-section="metrics"]',
    frequencies:'[data-topo-section="metrics"]',calibration:'[data-topo-section="metrics"]',trends:'[data-topo-section="trends"]'};
  for(const v of Object.keys(TAB)){
    // use the API the button uses — no coordinate races
    await page.evaluate((view)=>window.setChipStatusView(view,null,true), v);
    await H.sleep(3000);
    const s = await page.evaluate(()=>{
      const act=[...document.querySelectorAll('.topo-subnav-btn')].filter(b=>b.classList.contains('active')).map(b=>b.getAttribute('data-view'));
      const secs=[...document.querySelectorAll('.topo-section')].map(e=>({id:e.id||e.getAttribute('data-topo-section'),top:Math.round(e.getBoundingClientRect().top)}));
      const tp=document.querySelector('#table-pane');
      return {active:act, paneTop:Math.round(tp.scrollTop), secs, sh:tp.scrollHeight};
    });
    const want = await page.evaluate((sel)=>{const e=document.querySelector(sel);return e?Math.round(e.getBoundingClientRect().top):null;}, TAB[v]);
    console.log(v.padEnd(14),'active=',JSON.stringify(s.active),'paneTop=',s.paneTop,'targetTopOnScreen=',want,'docH=',s.sh);
  }
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
