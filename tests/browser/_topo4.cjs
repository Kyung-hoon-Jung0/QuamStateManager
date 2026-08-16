const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const fs = require('fs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  const d = await page.evaluate(() => {
    const svg = document.querySelector('.topo-hero-svg');
    const nodes = [...svg.querySelectorAll('g[data-hero-qubit]')].map(g => {
      const c = g.querySelector('circle');
      const ts = [...g.querySelectorAll('text')].map(t=>t.textContent);
      return {id: g.getAttribute('data-hero-qubit'), cx:+c.getAttribute('cx'), cy:+c.getAttribute('cy'), r:+c.getAttribute('r'), texts: ts, stroke: c.getAttribute('stroke'), fill: c.getAttribute('fill')};
    });
    const edges = [...svg.querySelectorAll('g[data-hero-pair]')].map(g => {
      const l = g.querySelector('line');
      return {id: g.getAttribute('data-hero-pair'), x1:+l.getAttribute('x1'), y1:+l.getAttribute('y1'), x2:+l.getAttribute('x2'), y2:+l.getAttribute('y2'), stroke:l.getAttribute('stroke')};
    });
    // role markers: any g with data-cm-at / data-cm-pair
    const marks = [...svg.querySelectorAll('g[data-cm-at],g[data-cm-pair],g[data-cm-freq]')].map(g => ({
      at: g.getAttribute('data-cm-at'), pair: g.getAttribute('data-cm-pair'), freq: g.getAttribute('data-cm-freq'),
      cls: g.getAttribute('class'),
      html: g.outerHTML.slice(0,400)
    }));
    // all loose texts not inside a qubit g
    const loose = [...svg.querySelectorAll('text')].filter(t => !t.closest('g[data-hero-qubit]')).map(t => ({s:t.textContent, x:+t.getAttribute('x'), y:+t.getAttribute('y'), cls:t.getAttribute('class'), parent: t.parentElement.getAttribute('class'), pAt: t.parentElement.getAttribute('data-cm-at'), pPair: t.parentElement.getAttribute('data-cm-pair')}));
    return {nodes, edges, marks: marks.slice(0,6), nMarks: marks.length, loose};
  });
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/_topo_geom.json', JSON.stringify(d,null,1));
  console.log('nodes', d.nodes.length, 'edges', d.edges.length, 'marks', d.nMarks, 'loose', d.loose.length);
  console.log(JSON.stringify(d.nodes.slice(0,3),null,1));
  console.log(JSON.stringify(d.marks,null,1));
  await browser.close();
})();
