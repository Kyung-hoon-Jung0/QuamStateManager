const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const R = [];
function rec(k, ms, extra) { R.push(Object.assign({ step: k, ms }, extra || {})); console.error('  ' + k + ' = ' + ms + 'ms'); }

async function navClick(page, sel, readyFn, label, timeout) {
  const t = Date.now();
  await page.click(sel);
  try {
    await page.waitForFunction(readyFn, { timeout: timeout || 60000 });
  } catch (e) {
    rec(label, -1, { failed: String(e.message).slice(0, 120) });
    return -1;
  }
  const ms = Date.now() - t;
  rec(label, ms);
  return ms;
}

(async () => {
  const { browser, page } = await H.open({ port: 8866 });

  // ---- 1. shell cold load
  let t = Date.now();
  await page.goto('http://127.0.0.1:8866/', { waitUntil: 'load', timeout: 60000 });
  rec('shell_load_event', Date.now() - t);
  const navTiming = await page.evaluate(() => {
    const n = performance.getEntriesByType('navigation')[0];
    return n ? { domContentLoaded: Math.round(n.domContentLoadedEventEnd), loadEvent: Math.round(n.loadEventEnd), responseEnd: Math.round(n.responseEnd) } : null;
  });
  await H.sleep(3000); // let lazy fragments settle

  // ---- 2. Live State Edit
  await navClick(page, 'a[hx-get="/bulk"]',
    () => document.querySelectorAll('.bulk-cell').length > 100, 'nav_bulk_firstcells');
  // full settle: pair grid + all cells
  t = Date.now();
  await page.waitForFunction(() => {
    const s = document.getElementById('bulk-search');
    return s && document.querySelectorAll('.bulk-cell').length > 4000;
  }, { timeout: 60000 });
  rec('bulk_full_cells', Date.now() - t);
  const gridInfo = await page.evaluate(() => ({
    cells: document.querySelectorAll('.bulk-cell').length,
    rows: document.querySelectorAll('tr').length,
    tables: document.querySelectorAll('table').length,
    cols: (document.querySelectorAll('#bulk-table thead th') || []).length,
    html: document.getElementById('table-pane') ? document.getElementById('table-pane').innerHTML.length : 0,
  }));
  rec('bulk_dom_info', 0, gridInfo);

  // ---- 3. scroll whole grid top->bottom (measure frames)
  const scrollRes = await page.evaluate(async () => {
    const pane = document.getElementById('table-pane');
    const sc = pane && pane.scrollHeight > pane.clientHeight ? pane : document.scrollingElement;
    const start = performance.now();
    let maxFrame = 0, frames = 0;
    let last = performance.now();
    const target = sc.scrollHeight - sc.clientHeight;
    let y = 0;
    while (y < target) {
      y += 600;
      sc.scrollTop = y;
      await new Promise((r) => requestAnimationFrame(r));
      const now = performance.now();
      const d = now - last; last = now; frames++;
      if (d > maxFrame) maxFrame = d;
    }
    const total = performance.now() - start;
    // scroll back to top
    sc.scrollTop = 0;
    return { totalMs: Math.round(total), frames, maxFrameMs: Math.round(maxFrame), scrollHeight: sc.scrollHeight, clientHeight: sc.clientHeight, which: sc === pane ? 'table-pane' : 'document' };
  });
  rec('grid_scroll_full', scrollRes.totalMs, scrollRes);
  await H.sleep(1500);

  // ---- 4. typing in the grid search box
  await page.focus('#bulk-search');
  const typeRes = await page.evaluate(async () => {
    const el = document.getElementById('bulk-search');
    const out = [];
    const chars = 'amplitude';
    for (const c of chars) {
      el.value += c;
      const t0 = performance.now();
      el.dispatchEvent(new Event('input', { bubbles: true }));
      // let the app's handler (sync or debounced) run one frame
      await new Promise((r) => requestAnimationFrame(r));
      out.push(Math.round(performance.now() - t0));
    }
    return out;
  });
  rec('search_keystroke_sync_ms', Math.max(...typeRes), { perChar: typeRes });
  // wait for the filtered result to settle and count visible rows
  await H.sleep(1500);
  const afterSearch = await page.evaluate(() => {
    const vis = [...document.querySelectorAll('tr')].filter(r => r.offsetParent !== null).length;
    const cols = [...document.querySelectorAll('th')].filter(c => c.offsetParent !== null).length;
    return { visibleRows: vis, visibleCols: cols };
  });
  rec('search_result', 0, afterSearch);

  // real keystroke latency measured wall-clock through the browser input pipeline
  const t2 = Date.now();
  await page.keyboard.press('Backspace');
  await page.waitForFunction(() => true);
  rec('search_backspace_wall', Date.now() - t2);
  // clear
  await page.evaluate(() => { const el = document.getElementById('bulk-search'); el.value = ''; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await H.sleep(1200);

  console.log(JSON.stringify({ navTiming, results: R, errors: H.errors(page) }, null, 1));
  await H.shot(page, 'perf20q-after-search');
  await browser.close();
})();
