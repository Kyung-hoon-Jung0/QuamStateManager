const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  const t0 = Date.now();
  const st = await H.goto(page, '/', 2500);
  const shellMs = Date.now() - t0;

  const t1 = Date.now();
  await page.click('a[hx-get="/bulk"]');
  // wait until bulk cells exist
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 100, { timeout: 60000 });
  const bulkMs = Date.now() - t1;
  await H.sleep(4000);

  const info = await page.evaluate(() => {
    const q = (s) => document.querySelectorAll(s).length;
    const ids = {};
    document.querySelectorAll('[id]').forEach(e => { ids[e.id] = (ids[e.id] || 0) + 1; });
    const dupes = Object.entries(ids).filter(([k, v]) => v > 1);
    return {
      cells: q('.bulk-cell'),
      rows: q('#bulk-table tbody tr') || q('table tbody tr'),
      inputs: q('input'),
      searchBoxes: [...document.querySelectorAll('input[type=search], input[placeholder]')].map(e => (e.id || '') + '|' + (e.className || '') + '|' + (e.placeholder || '')).slice(0, 20),
      buttons: [...document.querySelectorAll('button')].map(b => (b.id || '') + '|' + (b.className || '').slice(0, 40) + '|' + (b.textContent || '').trim().slice(0, 30)).slice(0, 60),
      tables: q('table'),
      dupIds: dupes.slice(0, 20),
      panes: [...document.querySelectorAll('#table-pane, #inspector-pane')].map(e => e.id),
      mem: performance.memory ? { used: performance.memory.usedJSHeapSize, total: performance.memory.totalJSHeapSize } : null,
    };
  });
  console.log(JSON.stringify({ shellMs, status: st, bulkMs, info, errors: H.errors(page) }, null, 1));
  await H.shot(page, 'perf20q-bulk');
  await browser.close();
})();
