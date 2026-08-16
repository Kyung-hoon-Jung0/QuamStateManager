const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await page.evaluateOnNewDocument(() => {
    window.__rs = [];
    const EA = EventTarget.prototype.addEventListener;
    const ER = EventTarget.prototype.removeEventListener;
    EventTarget.prototype.addEventListener = function (t, f, o) {
      if (this === window && t === 'resize') {
        window.__rs.push({ phase: window.__phase || 'boot', stack: (new Error()).stack.split('\n').slice(2, 4).join(' | ').slice(0, 260) });
      }
      return EA.call(this, t, f, o);
    };
    EventTarget.prototype.removeEventListener = function (t, f, o) {
      if (this === window && t === 'resize') window.__rs.push({ phase: 'REMOVE:' + (window.__phase || '?'), stack: '' });
      return ER.call(this, t, f, o);
    };
  });
  const client = await page.target().createCDPSession();
  await client.send('DOMDebugger.enable').catch(() => {});
  const winResize = async () => {
    const { result } = await client.send('Runtime.evaluate', { expression: 'window' });
    const l = await client.send('DOMDebugger.getEventListeners', { objectId: result.objectId, depth: 0 });
    const rs = l.listeners.filter(x => x.type === 'resize');
    return { count: rs.length, locs: rs.map(x => (x.scriptId ? '' : '') + 'line' + x.lineNumber).slice(0, 20) };
  };

  await H.goto(page, '/', 3000);
  const base = await winResize();

  const visits = [];
  for (let i = 0; i < 10; i++) {
    await page.evaluate((n) => { window.__phase = n; }, 'bulk#' + i);
    await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
    await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
    await H.sleep(700);
    const afterBulk = await winResize();
    await page.evaluate((n) => { window.__phase = n; }, 'qubits#' + i);
    await page.evaluate(() => document.querySelector('a[hx-get="/qubits"]').click());
    await H.sleep(700);
    const afterQubits = await winResize();
    visits.push({ i, afterBulk: afterBulk.count, afterQubits: afterQubits.count });
  }
  const stacks = await page.evaluate(() => window.__rs);
  // fire a resize and see how long the handlers take now
  const resizeCost = await page.evaluate(async () => {
    const t0 = performance.now();
    window.dispatchEvent(new Event('resize'));
    await new Promise(r => requestAnimationFrame(r));
    return Math.round(performance.now() - t0);
  });
  console.log(JSON.stringify({ base, visits, resizeCostMs: resizeCost, stacks: stacks.slice(0, 14), stackCount: stacks.length, errors: H.errors(page).slice(0, 5) }, null, 1));
  await browser.close();
})();
