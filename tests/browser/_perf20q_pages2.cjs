const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const R = [];
function rec(k, ms, extra) { R.push(Object.assign({ step: k, ms }, extra || {})); console.error('  ' + k + ' = ' + ms + 'ms ' + (extra ? JSON.stringify(extra).slice(0, 300) : '')); }

async function jsClick(page, sel) {
  await page.evaluate((s) => { const e = document.querySelector(s); if (!e) throw new Error('missing ' + s); e.click(); }, sel);
}

(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await H.goto(page, '/', 3000);

  // ---------- Json Tree View (explorer) ----------
  let t = Date.now();
  await jsClick(page, 'a[hx-get="/explorer"]');
  await page.waitForFunction(() => document.getElementById('explorer-tree-state'), { timeout: 90000 }).catch(() => {});
  rec('nav_explorer', Date.now() - t);
  await H.sleep(3000);
  const expInfo = await page.evaluate(() => {
    const tr = document.getElementById('explorer-tree-state');
    const cls = {};
    if (tr) tr.querySelectorAll('*').forEach(e => { const c = (e.className || '').toString().split(' ')[0]; if (c) cls[c] = (cls[c] || 0) + 1; });
    return { nodes: tr ? tr.querySelectorAll('*').length : -1, topClasses: Object.entries(cls).sort((a, b) => b[1] - a[1]).slice(0, 8) };
  });
  rec('explorer_info', 0, expInfo);

  // expand deep nodes repeatedly
  const expandTimings = [];
  for (let i = 0; i < 8; i++) {
    const r = await page.evaluate(async () => {
      const tree = document.getElementById('explorer-tree-state');
      if (!tree) return null;
      const togs = [...tree.querySelectorAll('.jt-key, .jt-toggle, .tree-toggle, [data-path]')].filter(e => e.offsetParent !== null);
      if (!togs.length) return { none: true, sample: tree.innerHTML.slice(0, 300) };
      const el = togs[togs.length - 1];
      const before = tree.querySelectorAll('*').length;
      const t0 = performance.now();
      el.click();
      await new Promise(r => requestAnimationFrame(r));
      await new Promise(r => setTimeout(r, 120));
      return { ms: Math.round(performance.now() - t0), before, after: tree.querySelectorAll('*').length, cls: (el.className || '').toString().slice(0, 40) };
    });
    if (!r || r.none) { rec('explorer_expand_none', -1, r || {}); break; }
    expandTimings.push(r);
    await H.sleep(150);
  }
  if (expandTimings.length) rec('explorer_expand_max', Math.max(...expandTimings.map(x => x.ms)), { each: expandTimings });

  // explorer search
  const expSearch = await page.evaluate(async () => {
    const el = document.getElementById('explorer-search');
    if (!el) return null;
    const out = [];
    for (const c of 'amplitude') {
      el.value += c;
      const t0 = performance.now();
      el.dispatchEvent(new Event('input', { bubbles: true }));
      await new Promise(r => requestAnimationFrame(r));
      out.push(Math.round(performance.now() - t0));
    }
    return out;
  });
  rec('explorer_search_keystrokes_sync', expSearch ? Math.max(...expSearch) : -1, { perChar: expSearch });
  await H.sleep(2500);
  const afterExpSearch = await page.evaluate(() => ({ nodes: document.getElementById('explorer-tree-state') ? document.getElementById('explorer-tree-state').querySelectorAll('*').length : -1 }));
  rec('explorer_after_search', 0, afterExpSearch);
  await H.shot(page, 'perf20q-explorer');

  // ---------- Pulses ----------
  t = Date.now();
  await jsClick(page, 'a[hx-get="/pulses"]');
  await page.waitForFunction(() => document.getElementById('pulses-table'), { timeout: 90000 }).catch(() => {});
  rec('nav_pulses', Date.now() - t);
  await H.sleep(2500);
  const pulseInfo = await page.evaluate(() => ({
    rows: document.querySelectorAll('#pulses-table tbody tr').length,
    sparks: document.querySelectorAll('#pulses-table svg').length,
  }));
  rec('pulses_info', 0, pulseInfo);
  await H.shot(page, 'perf20q-pulses');

  // ---------- Datasets (empty) ----------
  t = Date.now();
  await jsClick(page, 'a[hx-get="/datasets"]');
  await page.waitForFunction(() => (document.getElementById('table-pane') || {}).textContent.indexOf('Datasets') >= 0, { timeout: 60000 }).catch(() => {});
  rec('nav_datasets_empty', Date.now() - t);
  await H.sleep(1000);

  // ---------- add the real data root, then Datasets again ----------
  const addOk = await page.evaluate(() => {
    const el = document.getElementById('workspace-path-input');
    if (!el) return 'no-input';
    el.value = 'D:\\work\\Customer_Codes\\CQT\\data';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    const form = el.closest('form');
    if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); return 'submitted'; }
    return 'no-form';
  });
  rec('add_data_root', 0, { addOk });
  t = Date.now();
  await page.waitForFunction(() => document.body.textContent.indexOf('CQT') >= 0, { timeout: 180000 }).catch(() => {});
  rec('data_root_scan_visible', Date.now() - t);
  await H.sleep(3000);

  t = Date.now();
  await jsClick(page, 'a[hx-get="/datasets"]');
  await page.waitForFunction(() => {
    const p = document.getElementById('table-pane');
    return p && (p.querySelectorAll('tbody tr').length > 5 || p.textContent.indexOf('runs') >= 0);
  }, { timeout: 180000 }).catch(() => {});
  rec('nav_datasets_real', Date.now() - t);
  await H.sleep(4000);
  const dsInfo = await page.evaluate(() => ({
    rows: document.querySelectorAll('#table-pane tbody tr').length,
    text: (document.getElementById('table-pane') || {}).textContent.slice(0, 200).replace(/\s+/g, ' '),
  }));
  rec('datasets_info', 0, dsInfo);
  await H.shot(page, 'perf20q-datasets');

  console.log(JSON.stringify({ results: R, errors: H.errors(page) }, null, 1));
  await browser.close();
})();
