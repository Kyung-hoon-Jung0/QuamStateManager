const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const R = [];
function rec(k, v) { R.push(Object.assign({ step: k }, v)); console.error('  ' + k + ' ' + JSON.stringify(v).slice(0, 400)); }

(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await page.evaluateOnNewDocument(() => {
    window.__long = [];
    try {
      new PerformanceObserver((l) => { l.getEntries().forEach(e => window.__long.push(Math.round(e.duration))); }).observe({ entryTypes: ['longtask'] });
    } catch (e) {}
  });
  const errs = [];
  page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await H.goto(page, '/', 3000);

  // --- clear localStorage first so the history cache starts empty
  await page.evaluate(() => { try { localStorage.removeItem('htmx-history-cache'); } catch (e) {} });
  const errB = errs.length;

  // --- /bulk: resource timing + long tasks
  await page.evaluate(() => { window.__long = []; });
  let t = Date.now();
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
  const bulkWall = Date.now() - t;
  await H.sleep(2500);
  const bulkBreak = await page.evaluate(() => {
    const r = performance.getEntriesByType('resource').filter(e => e.name.indexOf('/bulk') >= 0).slice(-1)[0];
    return {
      resourceMs: r ? Math.round(r.duration) : null,
      ttfbMs: r ? Math.round(r.responseStart - r.startTime) : null,
      downloadMs: r ? Math.round(r.responseEnd - r.responseStart) : null,
      bytes: r ? r.transferSize : null,
      longTasks: window.__long.slice(),
      longTaskTotal: window.__long.reduce((a, b) => a + b, 0),
    };
  });
  rec('bulk_nav_breakdown', Object.assign({ wallMs: bulkWall }, bulkBreak));
  const lsAfterBulk = await page.evaluate(() => {
    let n = 0; try { n = (localStorage.getItem('htmx-history-cache') || '').length; } catch (e) {}
    return { historyCacheBytes: n };
  });
  rec('history_cache_after_one_bulk', Object.assign(lsAfterBulk, { newErrors: errs.length - errB, errs: errs.slice(errB, errB + 3) }));

  // --- navigate away then Back (browser back button) — does the pane restore?
  await page.evaluate(() => document.querySelector('a[hx-get="/pulses"]').click());
  await page.waitForFunction(() => !!document.getElementById('pulses-table'), { timeout: 60000 }).catch(() => {});
  await H.sleep(1200);
  const errB2 = errs.length;
  t = Date.now();
  await page.goBack({ waitUntil: 'domcontentloaded' }).catch(() => {});
  await H.sleep(3000);
  const backState = await page.evaluate(() => ({
    url: location.pathname,
    cells: document.querySelectorAll('.bulk-cell').length,
    hasGrid: !!document.getElementById('bulk-search'),
    paneText: (document.getElementById('table-pane') || {}).textContent.slice(0, 60).replace(/\s+/g, ' '),
  }));
  rec('browser_back_after_bulk', Object.assign({ ms: Date.now() - t, newErrors: errs.length - errB2 }, backState));
  await H.shot(page, 'perf20q-back-button');

  // --- editing a cell: type a value into a grid cell, measure commit staging
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
  await H.sleep(2500);
  const cellSel = await page.evaluate(() => {
    const c = [...document.querySelectorAll('#bulk-table input.bulk-cell')].find(e => e.value && !isNaN(parseFloat(e.value)) && !e.disabled);
    if (!c) return null;
    c.id = c.id || 'perfprobe-cell';
    return { id: c.id, path: c.getAttribute('data-path') || '', val: c.value };
  });
  if (cellSel) {
    const errB3 = errs.length;
    t = Date.now();
    await page.focus('#' + cellSel.id);
    await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
    await page.keyboard.type('0.1234', { delay: 25 });
    const typeMs = Date.now() - t;
    await H.sleep(900);
    const after = await page.evaluate((id) => {
      const c = document.getElementById(id);
      return { value: c.value, dirty: (c.className || '').indexOf('dirty') >= 0 || c.classList.contains('bulk-cell-modified'), cls: c.className.slice(0, 60) };
    }, cellSel.id);
    rec('cell_typing', Object.assign({ typeMs, cell: cellSel.path.slice(0, 60) }, after, { newErrors: errs.length - errB3 }));
    // undo it (Ctrl+Z) so nothing is left staged
    await page.keyboard.down('Control'); await page.keyboard.press('KeyZ'); await page.keyboard.up('Control');
    await H.sleep(1200);
    const undone = await page.evaluate((id) => document.getElementById(id).value, cellSel.id);
    rec('cell_undo', { value: undone, original: cellSel.val });
  } else rec('cell_typing', { skipped: 'no numeric cell found' });

  // --- Datasets filter chips: rapid clicking
  await page.evaluate(() => document.querySelector('a[hx-get="/datasets"]').click());
  await H.sleep(3500);
  const chipInfo = await page.evaluate(async () => {
    const chips = [...document.querySelectorAll('#table-pane .chip, #table-pane .ds-chip, #table-pane [class*=chip]')].filter(e => e.offsetParent);
    if (!chips.length) return { none: true, sample: [...document.querySelectorAll('#table-pane *')].slice(0, 0) };
    const out = [];
    for (let i = 0; i < Math.min(10, chips.length * 2); i++) {
      const c = chips[i % chips.length];
      const t0 = performance.now();
      c.click();
      await new Promise(r => requestAnimationFrame(r));
      out.push({ label: c.textContent.trim().slice(0, 14), ms: Math.round(performance.now() - t0) });
    }
    return { clicks: out, count: chips.length };
  });
  rec('dataset_chip_spam', chipInfo);
  await H.shot(page, 'perf20q-datasets-chips');

  console.log(JSON.stringify({ results: R, errors: H.errors(page).slice(0, 20) }, null, 1));
  await browser.close();
})();
