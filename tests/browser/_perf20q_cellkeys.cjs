const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await page.evaluateOnNewDocument(() => {
    window.__long = [];
    try { new PerformanceObserver(l => l.getEntries().forEach(e => window.__long.push(Math.round(e.duration)))).observe({ entryTypes: ['longtask'] }); } catch (e) {}
  });
  await H.goto(page, '/', 3000);
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
  await H.sleep(3000);

  // in-page synchronous cost of one keystroke in a grid cell vs the search box
  const res = await page.evaluate(async () => {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const cell = [...document.querySelectorAll('#bulk-table input.bulk-cell')].find(e => e.value && !e.disabled);
    const out = { cellPath: cell ? (cell.getAttribute('data-path') || cell.name || '') : null, cell: [], cellFrame: [], search: [], searchFrame: [] };
    if (cell) {
      cell.focus();
      const orig = cell.value;
      for (let i = 0; i < 8; i++) {
        cell.value = '0.12345'.slice(0, 3 + (i % 5));
        const t0 = performance.now();
        cell.dispatchEvent(new Event('input', { bubbles: true }));
        out.cell.push(Math.round(performance.now() - t0));       // synchronous handler cost
        const t1 = performance.now();
        await new Promise(r => requestAnimationFrame(r));
        out.cellFrame.push(Math.round(performance.now() - t1));  // time to next frame
        await sleep(60);
      }
      cell.value = orig;
      cell.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(200);
    }
    const s = document.getElementById('bulk-search');
    s.focus();
    for (const c of 'ampl') {
      s.value += c;
      const t0 = performance.now();
      s.dispatchEvent(new Event('input', { bubbles: true }));
      out.search.push(Math.round(performance.now() - t0));
      const t1 = performance.now();
      await new Promise(r => requestAnimationFrame(r));
      out.searchFrame.push(Math.round(performance.now() - t1));
      await sleep(60);
    }
    s.value = ''; s.dispatchEvent(new Event('input', { bubbles: true }));
    return out;
  });

  // real keyboard: measure wall time of N presses in a cell vs in the search box
  const cellId = await page.evaluate(() => {
    const c = [...document.querySelectorAll('#bulk-table input.bulk-cell')].find(e => e.value && !e.disabled);
    if (!c) return null; c.id = 'perfcell'; return 'perfcell';
  });
  let cellWall = null, searchWall = null;
  if (cellId) {
    await page.focus('#perfcell');
    let t = Date.now();
    await page.keyboard.type('123456', { delay: 0 });
    cellWall = Date.now() - t;
    await H.sleep(600);
    // restore via Ctrl+Z
    await page.keyboard.down('Control'); await page.keyboard.press('KeyZ'); await page.keyboard.up('Control');
    await H.sleep(800);
  }
  await page.focus('#bulk-search');
  let t2 = Date.now();
  await page.keyboard.type('123456', { delay: 0 });
  searchWall = Date.now() - t2;
  await page.evaluate(() => { const s = document.getElementById('bulk-search'); s.value = ''; s.dispatchEvent(new Event('input', { bubbles: true })); });
  await H.sleep(800);

  const post = await page.evaluate(() => ({
    finalCell: (document.getElementById('perfcell') || {}).value,
    tray: (document.getElementById('pending-tray') || {}).textContent ? (document.getElementById('pending-tray').textContent.replace(/\s+/g, ' ').slice(0, 120)) : null,
    longTasks: window.__long.slice(-8),
  }));

  console.log(JSON.stringify({ inPage: res, realKeyboard: { sixKeysInCellMs: cellWall, sixKeysInSearchMs: searchWall }, post, errors: H.errors(page).slice(0, 6) }, null, 1));
  await browser.close();
})();
