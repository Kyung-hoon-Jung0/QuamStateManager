const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const R = [];
function rec(k, v) { R.push(Object.assign({ step: k }, v)); console.error('  ' + k + ' ' + JSON.stringify(v).slice(0, 300)); }

(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await page.evaluateOnNewDocument(() => {
    window.__winL = {};
    const EA = EventTarget.prototype.addEventListener;
    const ER = EventTarget.prototype.removeEventListener;
    EventTarget.prototype.addEventListener = function (t, f, o) {
      if (this === window) window.__winL[t] = (window.__winL[t] || 0) + 1;
      return EA.call(this, t, f, o);
    };
    EventTarget.prototype.removeEventListener = function (t, f, o) {
      if (this === window) window.__winL[t] = (window.__winL[t] || 0) - 1;
      return ER.call(this, t, f, o);
    };
  });
  const reqs = [];
  page.on('request', r => reqs.push({ t: Date.now(), u: r.url().replace('http://127.0.0.1:8866', '') }));
  const errs = [];
  page.on('console', m => { if (m.type() === 'error') errs.push({ t: Date.now(), text: m.text() }); });

  await H.goto(page, '/', 3000);

  // ---- repeated bulk visits: does the window listener set grow per mount?
  const counts = [];
  for (let i = 0; i < 6; i++) {
    await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
    await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
    await H.sleep(800);
    await page.evaluate(() => document.querySelector('a[hx-get="/pulses"]').click());
    await page.waitForFunction(() => !!document.getElementById('pulses-table'), { timeout: 60000 }).catch(() => {});
    await H.sleep(600);
    counts.push(await page.evaluate(() => JSON.parse(JSON.stringify(window.__winL))));
  }
  rec('window_listeners_per_bulk_visit', { first: counts[0], last: counts[counts.length - 1], all_beforeunload: counts.map(c => c.beforeunload || 0), all_resize: counts.map(c => c.resize || 0) });

  // ---- localStorage / htmx history cache state
  const ls = await page.evaluate(() => {
    let total = 0; const sizes = {};
    for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); const v = localStorage.getItem(k) || ''; sizes[k] = v.length; total += v.length + k.length; }
    return { total, sizes };
  });
  rec('localStorage', ls);

  // ---- rapid interaction: fast typing in the grid search
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
  await H.sleep(2000);
  const errBefore = errs.length;
  await page.focus('#bulk-search');
  let t = Date.now();
  await page.keyboard.type('amplitude flux readout', { delay: 8 });
  const typeWall = Date.now() - t;
  await H.sleep(1500);
  const typed = await page.evaluate(() => document.getElementById('bulk-search').value);
  rec('rapid_typing', { wallMs: typeWall, valueSurvived: typed, newErrors: errs.length - errBefore });

  // erase fast
  t = Date.now();
  for (let i = 0; i < 22; i++) await page.keyboard.press('Backspace');
  rec('rapid_backspace', { wallMs: Date.now() - t, value: await page.evaluate(() => document.getElementById('bulk-search').value) });
  await H.sleep(1200);

  // ---- Tab spam inside the grid
  const errB2 = errs.length;
  t = Date.now();
  const firstCell = await page.evaluate(() => {
    const c = document.querySelector('#bulk-table input.bulk-cell:not([disabled])');
    if (!c) return null; c.focus(); return c.getAttribute('data-path') || 'cell';
  });
  for (let i = 0; i < 60; i++) await page.keyboard.press('Tab');
  const tabRes = await page.evaluate(() => {
    const a = document.activeElement;
    return { tag: a.tagName, cls: (a.className || '').toString().slice(0, 40), path: a.getAttribute ? (a.getAttribute('data-path') || '') : '' };
  });
  rec('tab_spam_60', { wallMs: Date.now() - t, start: firstCell, end: tabRes, newErrors: errs.length - errB2 });

  // ---- rapid chip / control clicking on Chip Status
  await page.evaluate(() => document.querySelector('a[hx-get="/topology"]').click());
  await page.waitForFunction(() => !!document.getElementById('topo-hero'), { timeout: 60000 }).catch(() => {});
  await H.sleep(2500);
  const errB3 = errs.length;
  t = Date.now();
  const secTimes = await page.evaluate(async () => {
    const btns = [...document.querySelectorAll('.topo-subnav-btn')];
    const out = [];
    for (let round = 0; round < 3; round++) {
      for (const b of btns) {
        const t0 = performance.now();
        b.click();
        await new Promise(r => requestAnimationFrame(r));
        out.push({ label: b.textContent.trim().slice(0, 18), ms: Math.round(performance.now() - t0) });
      }
    }
    return out;
  });
  const worst = secTimes.slice().sort((a, b) => b.ms - a.ms).slice(0, 5);
  rec('chipstatus_section_spam', { clicks: secTimes.length, wallMs: Date.now() - t, worst, newErrors: errs.length - errB3 });

  // rapid palette / density preset clicking
  const errB4 = errs.length;
  const densRes = await page.evaluate(async () => {
    const btns = [...document.querySelectorAll('.density-preset')];
    const out = [];
    for (let i = 0; i < 12; i++) {
      const b = btns[i % btns.length];
      const t0 = performance.now();
      b.click();
      await new Promise(r => requestAnimationFrame(r));
      out.push(Math.round(performance.now() - t0));
    }
    return out;
  });
  rec('density_preset_spam', { max: Math.max(...densRes), each: densRes, newErrors: errs.length - errB4 });
  await H.shot(page, 'perf20q-chipstatus-after-spam');

  // ---- IDLE 2 minutes on Datasets (has a 15s poller) + tray/version pollers
  await page.evaluate(() => document.querySelector('a[hx-get="/datasets"]').click());
  await H.sleep(3000);
  const idleStart = Date.now();
  const errIdleStart = errs.length;
  const reqIdleStart = reqs.length;
  await H.sleep(125000);
  const idleReqs = reqs.filter(r => r.t >= idleStart);
  const byUrl = {};
  idleReqs.forEach(r => { const k = r.u.split('?')[0]; byUrl[k] = (byUrl[k] || 0) + 1; });
  rec('idle_125s', {
    requests: idleReqs.length,
    byUrl: Object.entries(byUrl).sort((a, b) => b[1] - a[1]).slice(0, 12),
    newConsoleErrors: errs.length - errIdleStart,
    errSample: errs.slice(errIdleStart, errIdleStart + 5).map(e => e.text),
  });
  const idleHeap = await page.evaluate(() => performance.memory ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) : null);
  rec('idle_heap_mb', { heapMB: idleHeap });

  // still responsive after idle?
  const t2 = Date.now();
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  const ok = await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).then(() => true).catch(() => false);
  rec('nav_after_idle', { ms: Date.now() - t2, ok });

  console.log(JSON.stringify({ results: R, totalRequests: reqs.length, allErrors: H.errors(page).slice(0, 30) }, null, 1));
  await browser.close();
})();
