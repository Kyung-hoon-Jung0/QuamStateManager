const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  const info = await page.evaluate(()=>{
    const bar=document.querySelector('.topo-hero-bar');
    const btns=[...document.querySelectorAll('.topo-hero-mbtn')].map(b=>({t:b.textContent.trim(), key:b.getAttribute('data-hero-metric'), sel:b.getAttribute('aria-selected'), r:b.getBoundingClientRect()}));
    const legend=document.querySelector('.topo-hero-legend');
    const nodeTexts={};
    document.querySelectorAll('g[data-hero-qubit]').forEach(g=>{nodeTexts[g.getAttribute('data-hero-qubit')]=[...g.querySelectorAll('text')].map(t=>t.textContent);});
    const fills={};
    document.querySelectorAll('g[data-hero-qubit] circle').forEach(c=>{fills[c.parentElement.getAttribute('data-hero-qubit')]={fill:getComputedStyle(c).fill, cls:c.getAttribute('class'), attrFill:c.getAttribute('fill')};});
    return {barExists:!!bar, barRect: bar?bar.getBoundingClientRect():null, btns, legend: legend?legend.innerText:null, nodeTexts, fills};
  });
  console.log(JSON.stringify(info,null,1).slice(0,4000));
  await browser.close();
})();
