const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

(async () => {
  const { browser, page } = await H.open({ port: 8866 });

  await page.evaluateOnNewDocument(() => {
    window.__iv = { made: [], cleared: 0, live: 0 };
    const SI = window.setInterval, CI = window.clearInterval;
    window.setInterval = function (fn, ms) {
      const id = SI.apply(window, arguments);
      window.__iv.made.push({ id, ms, at: (window.__phase || 'boot'), stack: (new Error()).stack.split('\n').slice(2, 4).join(' | ').slice(0, 220) });
      window.__iv.live++;
      return id;
    };
    window.clearInterval = function (id) { window.__iv.cleared++; window.__iv.live--; return CI.apply(window, arguments); };
    // document/window level listener census
    window.__docL = {}; window.__winL = {};
    const EA = EventTarget.prototype.addEventListener;
    const ER = EventTarget.prototype.removeEventListener;
    EventTarget.prototype.addEventListener = function (t, f, o) {
      if (this === document) window.__docL[t] = (window.__docL[t] || 0) + 1;
      else if (this === window) window.__winL[t] = (window.__winL[t] || 0) + 1;
      else if (this === document.body) window.__docL['BODY:' + t] = (window.__docL['BODY:' + t] || 0) + 1;
      return EA.call(this, t, f, o);
    };
    EventTarget.prototype.removeEventListener = function (t, f, o) {
      if (this === document) window.__docL[t] = (window.__docL[t] || 0) - 1;
      else if (this === window) window.__winL[t] = (window.__winL[t] || 0) - 1;
      return ER.call(this, t, f, o);
    };
  });

  const consoleFull = [];
  page.on('console', async (m) => {
    if (m.type() === 'error' || /historyCache/.test(m.text())) {
      const args = [];
      for (const a of m.args()) { try { args.push(await a.jsonValue()); } catch (e) { args.push('<obj>'); } }
      consoleFull.push({ type: m.type(), text: m.text(), args: JSON.stringify(args).slice(0, 400) });
    }
  });

  const client = await page.target().createCDPSession();
  await client.send('DOMDebugger.enable').catch(() => {});
  const docListeners = async () => {
    const { result } = await client.send('Runtime.evaluate', { expression: 'document' });
    const a = await client.send('DOMDebugger.getEventListeners', { objectId: result.objectId, depth: 0 });
    const { result: r2 } = await client.send('Runtime.evaluate', { expression: 'window' });
    const b = await client.send('DOMDebugger.getEventListeners', { objectId: r2.objectId, depth: 0 });
    const cnt = (l) => l.listeners.reduce((m, x) => (m[x.type] = (m[x.type] || 0) + 1, m), {});
    return { document: cnt(a), window: cnt(b), docTotal: a.listeners.length, winTotal: b.listeners.length };
  };

  await H.goto(page, '/', 3000);
  const l0 = await docListeners();
  const iv0 = await page.evaluate(() => JSON.parse(JSON.stringify(window.__iv)));

  const pages = [
    { sel: 'a[hx-get="/bulk"]', ready: () => document.querySelectorAll('.bulk-cell').length > 4000, name: 'bulk' },
    { sel: 'a[hx-get="/topology"]', ready: () => !!document.getElementById('topo-hero'), name: 'chipstatus' },
    { sel: 'a[hx-get="/explorer"]', ready: () => !!document.getElementById('explorer-tree-state'), name: 'explorer' },
    { sel: 'a[hx-get="/pulses"]', ready: () => !!document.getElementById('pulses-table'), name: 'pulses' },
    { sel: 'a[hx-get="/datasets"]', ready: () => (document.getElementById('table-pane') || {}).textContent.indexOf('runs') >= 0, name: 'datasets' },
  ];
  const perPageIv = [];
  for (let round = 0; round < 3; round++) {
    for (const p of pages) {
      await page.evaluate((n) => { window.__phase = n; }, p.name + '#' + round);
      const before = await page.evaluate(() => window.__iv.made.length);
      await page.evaluate((s) => document.querySelector(s).click(), p.sel);
      try { await page.waitForFunction(p.ready, { timeout: 90000 }); } catch (e) {}
      await H.sleep(1500);
      const after = await page.evaluate(() => window.__iv.made.length);
      if (after > before) perPageIv.push({ page: p.name, round, added: after - before });
    }
  }
  const l1 = await docListeners();
  const iv1 = await page.evaluate(() => JSON.parse(JSON.stringify(window.__iv)));

  const diff = (a, b) => {
    const out = {};
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    keys.forEach(k => { const d = (b[k] || 0) - (a[k] || 0); if (d) out[k] = d; });
    return out;
  };

  console.log(JSON.stringify({
    baseline: { docTotal: l0.docTotal, winTotal: l0.winTotal, intervalsMade: iv0.made.length, live: iv0.live },
    after15navs: { docTotal: l1.docTotal, winTotal: l1.winTotal, intervalsMade: iv1.made.length, cleared: iv1.cleared, live: iv1.live },
    docListenerDelta: diff(l0.document, l1.document),
    winListenerDelta: diff(l0.window, l1.window),
    intervalsMade: iv1.made,
    perPageIntervalAdds: perPageIv,
    consoleFull: consoleFull.slice(0, 10),
    consoleFullCount: consoleFull.length,
    errors: H.errors(page),
  }, null, 1));
  await browser.close();
})();
