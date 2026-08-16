const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await H.goto(page, '/', 3000);
  const pages = [
    { sel: 'a[hx-get="/bulk"]', ready: () => document.querySelectorAll('.bulk-cell').length > 4000, name: 'bulk' },
    { sel: 'a[hx-get="/topology"]', ready: () => !!document.getElementById('topo-hero'), name: 'chipstatus' },
    { sel: 'a[hx-get="/explorer"]', ready: () => !!document.getElementById('explorer-tree-state'), name: 'explorer' },
    { sel: 'a[hx-get="/pulses"]', ready: () => !!document.getElementById('pulses-table'), name: 'pulses' },
    { sel: 'a[hx-get="/datasets"]', ready: () => true, name: 'datasets' },
    { sel: 'a[hx-get="/diagnostics"]', ready: () => true, name: 'diagnostics' },
    { sel: 'a[hx-get="/qubits"]', ready: () => true, name: 'qubits' },
    { sel: 'a[hx-get="/pairs"]', ready: () => true, name: 'pairs' },
    { sel: 'a[hx-get="/instrument"]', ready: () => true, name: 'instrument' },
    { sel: 'a[hx-get="/state-history"]', ready: () => true, name: 'statehistory' },
  ];
  const out = [];
  for (let round = 0; round < 3; round++) {
    for (const p of pages) {
      const t = Date.now();
      await page.evaluate((s) => { const e = document.querySelector(s); if (e) e.click(); }, p.sel);
      try { await page.waitForFunction(p.ready, { timeout: 60000 }); } catch (e) {}
      await H.sleep(1400);
      const d = await page.evaluate(() => {
        const ids = {}; const dup = [];
        document.querySelectorAll('[id]').forEach(e => { ids[e.id] = (ids[e.id] || 0) + 1; });
        Object.entries(ids).forEach(([k, v]) => { if (v > 1) dup.push(k + '×' + v); });
        return { dup, total: Object.keys(ids).length };
      });
      out.push({ round, page: p.name, ms: Date.now() - t, dup: d.dup, idCount: d.total });
      console.error(p.name + ' r' + round + ' ' + (Date.now() - t) + 'ms dup=' + JSON.stringify(d.dup));
    }
  }
  console.log(JSON.stringify({ out, errors: H.errors(page).slice(0, 8) }, null, 1));
  await browser.close();
})();
