const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await H.goto(page, '/', 3000);
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
  await H.sleep(2500);
  await page.evaluate(() => document.querySelector('.bulk-seg[data-pane="allvalues"]').click());
  await H.sleep(5000);
  await page.evaluate(() => {
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    [...p.querySelectorAll('button')].find(x => /expand all/i.test(x.textContent)).click();
  });
  await H.sleep(4000);
  const res = await page.evaluate(async () => {
    const pane = document.getElementById('table-pane');
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    const marks = [];
    for (let i = 0; i < 12; i++) {
      pane.scrollTop = pane.scrollTop + 900;
      await new Promise(r => setTimeout(r, 400));
      marks.push({ top: pane.scrollTop, h: pane.scrollHeight, rows: p.querySelectorAll('tr').length });
    }
    return marks;
  });
  await H.shot(page, 'perf20q-flat-scrolled');
  console.log(JSON.stringify({ res, errors: H.errors(page).slice(0, 4) }, null, 1));
  await browser.close();
})();
