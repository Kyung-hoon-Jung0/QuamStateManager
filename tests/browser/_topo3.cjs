const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const fs = require('fs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  // scroll the map into view
  await page.evaluate(() => {
    const el = document.querySelector('#topo-hero');
    if (el) el.scrollIntoView({block:'center'});
  });
  await H.sleep(1200);
  await H.shot(page, 'topo-02-map');
  const d = await page.evaluate(() => {
    const svg = document.querySelector('.topo-hero-svg');
    const r = svg.getBoundingClientRect();
    // node circles: find g elements / circles with data attrs
    const attrs = new Set();
    svg.querySelectorAll('*').forEach(e => [...e.attributes].forEach(a => { if (a.name.startsWith('data-')) attrs.add(e.tagName+':'+a.name); }));
    const texts = [...svg.querySelectorAll('text')].map(t => ({s: t.textContent, x: +t.getAttribute('x'), y: +t.getAttribute('y'), cls: t.getAttribute('class'), fs: getComputedStyle(t).fontSize}));
    return {
      rect: {x:r.x,y:r.y,w:r.width,h:r.height},
      viewBox: svg.getAttribute('viewBox'),
      par: svg.getAttribute('preserveAspectRatio'),
      dataAttrs: [...attrs],
      textsCount: texts.length,
      texts: texts.slice(0,200),
      outer: svg.outerHTML.slice(0, 3000),
    };
  });
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/_topo_svg.json', JSON.stringify(d, null, 1));
  console.log(JSON.stringify({rect:d.rect, viewBox:d.viewBox, par:d.par, dataAttrs:d.dataAttrs, textsCount:d.textsCount}, null, 1));
  console.log('OUTER:', d.outer.slice(0,2500));
  await browser.close();
})();
