const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

async function setSearch(page, t) {
  await page.click('#explorer-search');
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  if (t) await page.keyboard.type(t, { delay: 12 }); else await page.keyboard.press('Backspace');
  await H.sleep(2500);
}
const snap = (page) => page.evaluate(() => {
  const nodes = [...document.querySelectorAll('.tree-node')];
  const vis = nodes.filter(n => n.offsetParent);
  return {
    total: nodes.length, visible: vis.length,
    visPaths: vis.map(n => n.getAttribute('data-path')).slice(0, 25),
    hasQ1: !!document.querySelector('.tree-node[data-path="qubits.q1"]'),
    hasF01: !!document.querySelector('.tree-node[data-path="qubits.q1.f_01"]'),
    searchVal: (document.querySelector('#explorer-search') || {}).value,
    countTxt: [...document.querySelectorAll('#table-pane span, #table-pane p')].map(e => e.textContent.trim()).filter(t => /match|hidden|of \d/i.test(t)).slice(0, 4),
  };
});

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  await H.goto(page, '/', 3000);
  await page.evaluate(() => document.querySelector('a[hx-get="/explorer"]').click());
  await H.sleep(6000);
  out.initial = await snap(page);
  for (const t of ['f_01', '__class__', 'grid_location', 'output_mode']) {
    await setSearch(page, t);
    out['s_' + t] = await snap(page);
  }
  out.shot = await H.shot(page, 'le19-explorer-search-f01');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
