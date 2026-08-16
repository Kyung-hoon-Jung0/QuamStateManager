const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await H.goto(page, '/', 3000);
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
  await H.sleep(2500);
  const out = [];
  for (let i = 0; i < 3; i++) {
    let t = Date.now();
    await page.evaluate(() => document.querySelector('.bulk-seg[data-pane="allvalues"]').click());
    await page.waitForFunction(() => {
      const p = document.querySelector('[data-bulk-pane="allvalues"]');
      return p && p.offsetParent !== null;
    }, { timeout: 60000 }).catch(() => {});
    const toFlat = Date.now() - t;
    await H.sleep(2500);
    const flat = await page.evaluate(() => {
      const p = document.querySelector('[data-bulk-pane="allvalues"]');
      return { rows: p ? p.querySelectorAll('tr').length : -1, inputs: p ? p.querySelectorAll('input').length : -1, text: p ? p.textContent.replace(/\s+/g, ' ').slice(0, 160) : '' };
    });
    t = Date.now();
    await page.evaluate(() => document.querySelector('.bulk-seg[data-pane="grid"]').click());
    await page.waitForFunction(() => {
      const p = document.querySelector('[data-bulk-pane="grid"]');
      return p && p.offsetParent !== null && document.querySelectorAll('.bulk-cell').length > 4000;
    }, { timeout: 60000 }).catch(() => {});
    const back = Date.now() - t;
    await H.sleep(1500);
    out.push({ i, toFlatMs: toFlat, backToTableMs: back, flat });
    console.error(JSON.stringify(out[out.length - 1]).slice(0, 300));
  }
  // search inside flat view (all leaves of a 20Q chip)
  await page.evaluate(() => document.querySelector('.bulk-seg[data-pane="allvalues"]').click());
  await H.sleep(2500);
  const flatSearch = await page.evaluate(async () => {
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    const inp = p ? p.querySelector('input[type=search], input[placeholder]') : null;
    if (!inp) return { none: true, html: p ? p.innerHTML.slice(0, 400) : '' };
    const t0 = performance.now();
    inp.value = 'amplitude';
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(r => setTimeout(r, 2500));
    return { placeholder: inp.placeholder, ms: Math.round(performance.now() - t0), rows: p.querySelectorAll('tr').length };
  });
  console.log(JSON.stringify({ out, flatSearch, errors: H.errors(page).slice(0, 6) }, null, 1));
  await H.shot(page, 'perf20q-flatview');
  await browser.close();
})();
