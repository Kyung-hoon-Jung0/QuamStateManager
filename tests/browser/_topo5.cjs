const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const fs = require('fs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  const d = await page.evaluate(() => {
    const svg = document.querySelector('.topo-hero-svg');
    const rc = e => { const r = e.getBoundingClientRect(); return {cx:r.x+r.width/2, cy:r.y+r.height/2, w:r.width, h:r.height, x:r.x, y:r.y}; };
    const nodes = {};
    svg.querySelectorAll('g[data-hero-qubit]').forEach(g => { nodes[g.getAttribute('data-hero-qubit')] = rc(g.querySelector('circle')); });
    const marks = [];
    svg.querySelectorAll('g.cm-role').forEach(g => {
      const t = g.querySelector('text');
      marks.push({role: t.textContent, at: g.getAttribute('data-cm-at'), pair: g.getAttribute('data-cm-pair'), r: rc(t), fs: getComputedStyle(t).fontSize});
    });
    const chev = [];
    svg.querySelectorAll('g.cm-freq').forEach(g => {
      const pl = g.querySelector('polyline');
      const pts = pl.getAttribute('points').split(' ').map(p=>p.split(',').map(Number));
      chev.push({key: g.getAttribute('data-cm-freq'), pts, title: g.querySelector('title') && g.querySelector('title').textContent, r: rc(pl)});
    });
    return {nodes, marks, chev, svgRect: rc(svg)};
  });
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/_topo_rects.json', JSON.stringify(d,null,1));
  console.log('nodes',Object.keys(d.nodes).length,'marks',d.marks.length,'chev',d.chev.length);
  console.log('node q1', JSON.stringify(d.nodes.q1), 'q2', JSON.stringify(d.nodes.q2));
  console.log('mark0', JSON.stringify(d.marks[0]));
  await browser.close();
})();
