const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  const net = [];
  page.on('request', r => { if (r.url().indexOf('all-values') >= 0) net.push({ ev: 'req', t: Date.now(), u: r.url() }); });
  page.on('response', r => { if (r.url().indexOf('all-values') >= 0) net.push({ ev: 'res', t: Date.now(), status: r.status() }); });
  page.on('requestfailed', r => { if (r.url().indexOf('all-values') >= 0) net.push({ ev: 'fail', t: Date.now(), err: (r.failure() || {}).errorText }); });

  await H.goto(page, '/', 3000);
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
  await H.sleep(2500);

  const t0 = Date.now();
  await page.evaluate(() => document.querySelector('.bulk-seg[data-pane="allvalues"]').click());
  const samples = [];
  for (let i = 0; i < 12; i++) {
    await H.sleep(2000);
    const s = await page.evaluate(() => {
      const p = document.querySelector('[data-bulk-pane="allvalues"]');
      return { rows: p ? p.querySelectorAll('tr').length : -1, inputs: p ? p.querySelectorAll('input').length : -1, spin: !!document.querySelector('[data-bulk-pane="allvalues"] .htmx-request') };
    });
    samples.push(Object.assign({ atMs: Date.now() - t0 }, s));
  }
  await H.shot(page, 'perf20q-flatview-waited');
  const finalTxt = await page.evaluate(() => {
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    return p ? p.textContent.replace(/\s+/g, ' ').slice(0, 400) : '';
  });
  // now click "Expand all" if present
  const exp = await page.evaluate(async () => {
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    const b = [...p.querySelectorAll('button')].find(x => /expand all/i.test(x.textContent));
    if (!b) return { none: true };
    const t = performance.now();
    b.click();
    await new Promise(r => setTimeout(r, 4000));
    return { ms: Math.round(performance.now() - t), rows: p.querySelectorAll('tr').length, inputs: p.querySelectorAll('input').length };
  });
  await H.shot(page, 'perf20q-flatview-expanded');
  console.log(JSON.stringify({ samples, finalTxt, expandAll: exp, net, errors: H.errors(page).slice(0, 8) }, null, 1));
  await browser.close();
})();
