const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const fs=require('fs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  // scroll to the very bottom in steps to force any lazy section to mount
  for (let i=0;i<12;i++){ await page.evaluate(()=>window.scrollBy(0, 900)); await H.sleep(500); }
  await H.sleep(2500);
  const d = await page.evaluate(() => {
    const all=[...document.querySelectorAll('svg')].map(s=>{
      const r=s.getBoundingClientRect();
      return {cls:s.getAttribute('class'), id:s.id, w:Math.round(r.width), h:Math.round(r.height),
        parent:(s.parentElement.id||s.parentElement.className||'').toString().slice(0,60),
        nCircle:s.querySelectorAll('circle').length, nLine:s.querySelectorAll('line').length,
        nText:s.querySelectorAll('text').length};
    });
    return {
      svgs: all,
      big: all.filter(s=>s.w>200&&s.h>200),
      canvases: [...document.querySelectorAll('canvas')].length,
      plotly: [...document.querySelectorAll('.js-plotly-plot')].length,
      scrollH: document.documentElement.scrollHeight,
      topoIds: [...document.querySelectorAll('[id*="topo"],[id*="map"],[id*="diagram"]')].map(e=>e.id),
      bodyTextHasLogical: /logical layout|Logical layout/.test(document.body.innerText),
      sectionHeads: [...document.querySelectorAll('h2,h3')].map(e=>e.textContent.trim().slice(0,50)),
    };
  });
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/_topo_svgcensus.json', JSON.stringify(d,null,1));
  console.log(JSON.stringify({big:d.big, nSvg:d.svgs.length, canvases:d.canvases, plotly:d.plotly, topoIds:d.topoIds, logical:d.bodyTextHasLogical, heads:d.sectionHeads}, null, 1));
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
