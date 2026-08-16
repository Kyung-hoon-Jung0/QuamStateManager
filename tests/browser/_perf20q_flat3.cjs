const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await H.goto(page, '/', 3000);
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
  await H.sleep(2500);
  await page.evaluate(() => document.querySelector('.bulk-seg[data-pane="allvalues"]').click());
  await H.sleep(4000);

  const one = await page.evaluate(async () => {
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    const rows = [...p.querySelectorAll('tr')];
    const target = rows.find(r => /qubits/.test(r.textContent));
    if (!target) return { none: true };
    const before = p.querySelectorAll('tr').length;
    const t = performance.now();
    target.click();
    await new Promise(r => setTimeout(r, 1500));
    return { ms: Math.round(performance.now() - t), before, after: p.querySelectorAll('tr').length, inputs: p.querySelectorAll('input').length };
  });

  const all = await page.evaluate(async () => {
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    const b = [...p.querySelectorAll('button')].find(x => /expand all/i.test(x.textContent));
    const t = performance.now();
    b.click();
    const marks = [];
    for (let i = 0; i < 8; i++) {
      await new Promise(r => setTimeout(r, 1500));
      marks.push({ at: Math.round(performance.now() - t), rows: p.querySelectorAll('tr').length, inputs: p.querySelectorAll('input').length });
    }
    return marks;
  });

  // search across all leaves
  const search = await page.evaluate(async () => {
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    const inp = p.querySelector('input[placeholder*="Search"]');
    const t = performance.now();
    inp.value = 'x180 amplitude';
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    const syncMs = Math.round(performance.now() - t);
    await new Promise(r => setTimeout(r, 3000));
    return { syncMs, rows: p.querySelectorAll('tr').length, text: p.textContent.replace(/\s+/g, ' ').slice(200, 460) };
  });
  await H.shot(page, 'perf20q-flatview-expandall');
  console.log(JSON.stringify({ oneGroup: one, expandAllProgress: all, search, errors: H.errors(page).slice(0, 6) }, null, 1));
  await browser.close();
})();
