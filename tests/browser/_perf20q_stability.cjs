const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

(async () => {
  const { browser, page } = await H.open({ port: 8866 });

  // instrument BEFORE any app script runs
  await page.evaluateOnNewDocument(() => {
    window.__inst = { add: 0, remove: 0, mo: 0, moObserve: 0, moDisconnect: 0, io: 0, ioObserve: 0, ro: 0, timers: 0, intervals: 0 };
    const EA = EventTarget.prototype.addEventListener;
    const ER = EventTarget.prototype.removeEventListener;
    window.__byType = {};
    EventTarget.prototype.addEventListener = function (t, f, o) {
      window.__inst.add++;
      window.__byType[t] = (window.__byType[t] || 0) + 1;
      return EA.call(this, t, f, o);
    };
    EventTarget.prototype.removeEventListener = function (t, f, o) {
      window.__inst.remove++;
      window.__byType[t] = (window.__byType[t] || 0) - 1;
      return ER.call(this, t, f, o);
    };
    const MO = window.MutationObserver;
    window.MutationObserver = function (cb) {
      window.__inst.mo++;
      const m = new MO(cb);
      const ob = m.observe.bind(m), dc = m.disconnect.bind(m);
      m.observe = function () { window.__inst.moObserve++; return ob.apply(null, arguments); };
      m.disconnect = function () { window.__inst.moDisconnect++; return dc.apply(null, arguments); };
      return m;
    };
    if (window.IntersectionObserver) {
      const IO = window.IntersectionObserver;
      window.IntersectionObserver = function (cb, o) {
        window.__inst.io++;
        const i = new IO(cb, o);
        const ob = i.observe.bind(i);
        i.observe = function () { window.__inst.ioObserve++; return ob.apply(null, arguments); };
        return i;
      };
    }
    if (window.ResizeObserver) {
      const RO = window.ResizeObserver;
      window.ResizeObserver = function (cb) { window.__inst.ro++; return new RO(cb); };
    }
    const SI = window.setInterval;
    window.__intervals = 0;
    window.setInterval = function () { window.__intervals++; return SI.apply(window, arguments); };
  });

  const reqs = [];
  page.on('request', (r) => reqs.push({ t: Date.now(), u: r.url().replace('http://127.0.0.1:8866', '') }));

  const client = await page.target().createCDPSession();
  await client.send('HeapProfiler.enable');
  const gcAndMeasure = async () => {
    await client.send('HeapProfiler.collectGarbage');
    await H.sleep(300);
    return page.evaluate(() => {
      const ids = {}; let dup = 0; const dupNames = [];
      document.querySelectorAll('[id]').forEach(e => { ids[e.id] = (ids[e.id] || 0) + 1; });
      Object.entries(ids).forEach(([k, v]) => { if (v > 1) { dup++; if (dupNames.length < 12) dupNames.push(k + '×' + v); } });
      return {
        heapMB: performance.memory ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) : null,
        domNodes: document.querySelectorAll('*').length,
        listenersNet: window.__inst.add - window.__inst.remove,
        addTotal: window.__inst.add,
        removeTotal: window.__inst.remove,
        mo: window.__inst.mo, moObserve: window.__inst.moObserve, moDisconnect: window.__inst.moDisconnect,
        io: window.__inst.io, ioObserve: window.__inst.ioObserve, ro: window.__inst.ro,
        intervals: window.__intervals,
        dupIdCount: dup, dupIds: dupNames,
        topListenerTypes: Object.entries(window.__byType).sort((a, b) => b[1] - a[1]).slice(0, 6),
      };
    });
  };

  await H.goto(page, '/', 3000);
  const baseline = await gcAndMeasure();
  console.error('baseline ' + JSON.stringify(baseline));

  const pages = [
    { sel: 'a[hx-get="/bulk"]', ready: () => document.querySelectorAll('.bulk-cell').length > 4000, name: 'bulk' },
    { sel: 'a[hx-get="/topology"]', ready: () => !!document.getElementById('topo-hero'), name: 'chipstatus' },
    { sel: 'a[hx-get="/explorer"]', ready: () => !!document.getElementById('explorer-tree-state'), name: 'explorer' },
    { sel: 'a[hx-get="/pulses"]', ready: () => !!document.getElementById('pulses-table'), name: 'pulses' },
    { sel: 'a[hx-get="/qubits"]', ready: () => (document.getElementById('table-pane') || {}).textContent.indexOf('q1') >= 0, name: 'qubits' },
  ];

  const cycles = [];
  const navTimes = {};
  for (let round = 0; round < 4; round++) {
    for (const p of pages) {
      const t = Date.now();
      await page.evaluate((s) => document.querySelector(s).click(), p.sel);
      let ok = true;
      try { await page.waitForFunction(p.ready, { timeout: 90000 }); } catch (e) { ok = false; }
      const ms = Date.now() - t;
      (navTimes[p.name] = navTimes[p.name] || []).push(ok ? ms : -1);
      await H.sleep(1200);
    }
    const m = await gcAndMeasure();
    m.round = round + 1;
    cycles.push(m);
    console.error('round ' + (round + 1) + ' ' + JSON.stringify(m).slice(0, 400));
  }

  // does anything still work after 20 swaps? go to bulk, type a search, check filtering
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
  await H.sleep(2000);
  const stillWorks = await page.evaluate(async () => {
    const el = document.getElementById('bulk-search');
    if (!el) return { searchBox: false };
    const before = [...document.querySelectorAll('#bulk-table tbody tr')].filter(r => r.offsetParent !== null).length;
    el.value = 'zzzznotathing';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(r => setTimeout(r, 800));
    const after = [...document.querySelectorAll('#bulk-table tbody tr')].filter(r => r.offsetParent !== null).length;
    el.value = ''; el.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(r => setTimeout(r, 600));
    const restored = [...document.querySelectorAll('#bulk-table tbody tr')].filter(r => r.offsetParent !== null).length;
    return { searchBox: true, before, after, restored };
  });

  const final = await gcAndMeasure();
  console.log(JSON.stringify({
    baseline, cycles, final, navTimes, stillWorks,
    requestCount: reqs.length,
    errors: H.errors(page),
  }, null, 1));
  await H.shot(page, 'perf20q-after-20-navs');
  await browser.close();
})();
