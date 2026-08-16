const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const R = [];
function rec(k, ms, extra) { R.push(Object.assign({ step: k, ms }, extra || {})); console.error('  ' + k + ' = ' + ms + 'ms ' + (extra ? JSON.stringify(extra).slice(0, 200) : '')); }

async function waitFn(page, fn, label, timeout) {
  const t = Date.now();
  try { await page.waitForFunction(fn, { timeout: timeout || 60000 }); }
  catch (e) { rec(label, -1, { timedout: true }); return -1; }
  const ms = Date.now() - t; rec(label, ms); return ms;
}

(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await H.goto(page, '/', 3000);

  // ---------- Live State Edit ----------
  let t = Date.now();
  await page.click('a[hx-get="/bulk"]');
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 });
  rec('nav_bulk_total', Date.now() - t);
  await H.sleep(2500);

  // horizontal scroll across all 178 columns
  const hscroll = await page.evaluate(async () => {
    const cands = [...document.querySelectorAll('*')].filter(e => e.scrollWidth > e.clientWidth + 200);
    const sc = cands.sort((a, b) => (b.scrollWidth - b.clientWidth) - (a.scrollWidth - a.clientWidth))[0];
    if (!sc) return { none: true };
    const t0 = performance.now(); let last = t0, maxF = 0, frames = 0;
    const target = sc.scrollWidth - sc.clientWidth;
    for (let x = 0; x <= target; x += 800) {
      sc.scrollLeft = x;
      await new Promise(r => requestAnimationFrame(r));
      const now = performance.now(); const d = now - last; last = now; frames++;
      if (d > maxF) maxF = d;
    }
    const total = performance.now() - t0;
    sc.scrollLeft = 0;
    return { totalMs: Math.round(total), frames, maxFrameMs: Math.round(maxF), scrollWidth: sc.scrollWidth, cls: sc.className.slice(0, 60) };
  });
  rec('grid_hscroll_full', hscroll.totalMs || -1, hscroll);

  // Table View -> Flat View
  t = Date.now();
  await page.click('.bulk-seg[data-pane="allvalues"]');
  await page.waitForFunction(() => {
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    return p && p.offsetParent !== null && p.textContent.length > 200;
  }, { timeout: 90000 }).catch(() => {});
  rec('switch_to_flat_view', Date.now() - t);
  await H.sleep(2500);
  const flatInfo = await page.evaluate(() => {
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    return { rows: p ? p.querySelectorAll('tr').length : -1, html: p ? p.innerHTML.length : -1, visible: p ? p.offsetParent !== null : false };
  });
  rec('flat_view_info', 0, flatInfo);
  t = Date.now();
  await page.click('.bulk-seg[data-pane="grid"]');
  await page.waitForFunction(() => {
    const p = document.querySelector('[data-bulk-pane="grid"]');
    return p && p.offsetParent !== null;
  }, { timeout: 60000 }).catch(() => {});
  rec('switch_back_to_table_view', Date.now() - t);
  await H.sleep(1200);

  // ---------- Chip Status ----------
  t = Date.now();
  await page.click('a[hx-get="/topology"]');
  await page.waitForFunction(() => document.querySelector('.topo-dashboard') || document.getElementById('topo-hero'), { timeout: 90000 }).catch(() => {});
  rec('nav_chip_status', Date.now() - t);
  await H.sleep(3000);
  const heroOk = await page.evaluate(() => {
    const h = document.getElementById('topo-hero');
    return { heroSvg: h ? h.querySelectorAll('svg').length : -1, heroNodes: h ? h.querySelectorAll('circle,rect,polygon').length : -1, subnav: document.querySelectorAll('.topo-subnav-btn').length };
  });
  rec('chip_status_hero', 0, heroOk);

  const secs = await page.$$('.topo-subnav-btn');
  for (let i = 0; i < secs.length; i++) {
    const label = await page.evaluate(el => el.textContent.trim().slice(0, 24), secs[i]);
    const t0 = Date.now();
    await secs[i].click();
    await H.sleep(250);
    rec('chip_status_sec_' + i + '_' + label.replace(/\s+/g, '_'), Date.now() - t0);
  }
  await H.shot(page, 'perf20q-chipstatus');

  // ---------- Json Tree View (explorer) ----------
  t = Date.now();
  await page.click('a[hx-get="/explorer"]');
  await page.waitForFunction(() => document.getElementById('explorer-tree-state'), { timeout: 90000 }).catch(() => {});
  rec('nav_explorer', Date.now() - t);
  await H.sleep(2500);
  const expInfo = await page.evaluate(() => {
    const t = document.getElementById('explorer-tree-state');
    return { nodes: t ? t.querySelectorAll('*').length : -1, rows: t ? t.querySelectorAll('.jt-row, .json-row, li').length : -1 };
  });
  rec('explorer_info', 0, expInfo);
  // expand deep: click the first 6 expandable toggles found, timing each
  const expandTimings = [];
  for (let i = 0; i < 6; i++) {
    const r = await page.evaluate(async () => {
      const tree = document.getElementById('explorer-tree-state');
      if (!tree) return null;
      const togs = [...tree.querySelectorAll('[data-expandable], .jt-toggle, .json-toggle, summary')].filter(e => e.offsetParent !== null);
      // pick deepest not-yet-open
      const cand = togs.filter(e => e.tagName === 'SUMMARY' ? !e.parentElement.open : true);
      const el = cand[cand.length - 1] || togs[togs.length - 1];
      if (!el) return null;
      const before = tree.querySelectorAll('*').length;
      const t0 = performance.now();
      el.click();
      await new Promise(r => requestAnimationFrame(r));
      return { ms: Math.round(performance.now() - t0), before, after: tree.querySelectorAll('*').length };
    });
    if (!r) break;
    expandTimings.push(r);
    await H.sleep(200);
  }
  rec('explorer_expand_clicks', expandTimings.length ? Math.max(...expandTimings.map(x => x.ms)) : -1, { each: expandTimings });

  // explorer search typing
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
  rec('explorer_search_keystrokes', expSearch ? Math.max(...expSearch) : -1, { perChar: expSearch });
  await H.sleep(2000);

  // ---------- Pulses ----------
  t = Date.now();
  await page.click('a[hx-get="/pulses"]');
  await page.waitForFunction(() => document.getElementById('pulses-table'), { timeout: 90000 }).catch(() => {});
  rec('nav_pulses', Date.now() - t);
  await H.sleep(2500);
  const pulseInfo = await page.evaluate(() => ({
    rows: document.querySelectorAll('#pulses-table tbody tr').length,
    sparks: document.querySelectorAll('#pulses-table svg').length,
  }));
  rec('pulses_info', 0, pulseInfo);

  // ---------- Datasets ----------
  t = Date.now();
  await page.click('a[hx-get="/datasets"]');
  await page.waitForFunction(() => document.querySelector('#table-pane').textContent.includes('Datasets'), { timeout: 60000 }).catch(() => {});
  rec('nav_datasets_empty', Date.now() - t);
  await H.sleep(1500);

  console.log(JSON.stringify({ results: R, errors: H.errors(page) }, null, 1));
  await browser.close();
})();
