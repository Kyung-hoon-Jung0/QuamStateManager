const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.evaluate(()=>{const x=document.querySelector('.tray-teach-x'); if(x)x.click();});
  await H.sleep(1500);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  for(let i=0;i<20;i++){ await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop += 500;}); await H.sleep(280); }
  await H.sleep(4000);
  const s = await page.evaluate(()=>{
    const g=e=>e?{h:Math.round(e.getBoundingClientRect().height), txt:e.innerText.replace(/\s+/g,' ').slice(0,220)}:null;
    return {
      gate: g(document.querySelector('[data-topo-section="2qrb"]')),
      trends: g(document.querySelector('[data-topo-section="trends"]')),
      metrics: g(document.querySelector('[data-topo-section="metrics"]')),
      distributions: g(document.querySelector('[data-topo-section="distributions"]')),
      plots: document.querySelectorAll('.js-plotly-plot').length,
    };
  });
  console.log(JSON.stringify(s,null,1));
  // land on trends and screenshot
  await page.evaluate(()=>{window.scrollTo(0,0); window.setChipStatusView('trends',null,true);});
  await H.sleep(3500);
  await H.shot(page,'sec-trends-real');
  await page.evaluate(()=>{window.scrollTo(0,0); window.setChipStatusView('gate',null,true);});
  await H.sleep(3500);
  await H.shot(page,'sec-gate-real');
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
