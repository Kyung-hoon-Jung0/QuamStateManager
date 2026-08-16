const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await page.evaluateOnNewDocument(() => {
    window.__frames = [];
    let last = performance.now();
    (function loop() {
      const n = performance.now();
      window.__frames.push(Math.round(n - last));
      last = n;
      requestAnimationFrame(loop);
    })();
  });
  const errs = [];
  page.on('console', m => { if (m.type() === 'error') errs.push({ t: Date.now(), text: m.text() }); });

  await H.goto(page, '/', 3000);
  await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });

  const visits = [];
  for (let i = 0; i < 8; i++) {
    const errBefore = errs.length;
    await page.evaluate(() => { window.__frames = []; });
    const t = Date.now();
    await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
    await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
    const wall = Date.now() - t;
    await H.sleep(2000);
    const f = await page.evaluate(() => {
      const fr = window.__frames.slice();
      return { worstGapMs: Math.max.apply(null, fr), gapsOver250: fr.filter(x => x > 250).length, gapsOver100: fr.filter(x => x > 100).length };
    });
    const ls = await page.evaluate(() => { try { return (localStorage.getItem('htmx-history-cache') || '').length; } catch (e) { return -1; } });
    visits.push({ visit: i + 1, navWallMs: wall, worstFrameGapMs: f.worstGapMs, gapsOver250: f.gapsOver250, historyCacheBytes: ls, newErrors: errs.length - errBefore, errTexts: errs.slice(errBefore).map(e => e.text) });
    console.error(JSON.stringify(visits[visits.length - 1]));
    // leave to pulses so the next click is a real swap
    await page.evaluate(() => document.querySelector('a[hx-get="/pulses"]').click());
    await page.waitForFunction(() => !!document.getElementById('pulses-table'), { timeout: 60000 }).catch(() => {});
    await H.sleep(800);
  }

  // Is the UI actually unresponsive during the mount? click a sidebar link mid-mount and see if it registers.
  await page.evaluate(() => { window.__clicks = []; document.addEventListener('click', () => window.__clicks.push(performance.now()), true); });
  const respo = await page.evaluate(async () => {
    const t0 = performance.now();
    document.querySelector('a[hx-get="/bulk"]').click();
    // Try to run a tiny task every 50ms and see when it actually runs
    const stamps = [];
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 50));
      stamps.push(Math.round(performance.now() - t0));
    }
    const gaps = [];
    for (let i = 1; i < stamps.length; i++) gaps.push(stamps[i] - stamps[i - 1]);
    return { worstTimerGapMs: Math.max.apply(null, gaps), gapsOver300: gaps.filter(g => g > 300).length };
  });

  console.log(JSON.stringify({ visits, mainThreadBlock: respo, allErrors: H.errors(page).slice(0, 20) }, null, 1));
  await browser.close();
})();
