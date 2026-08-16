const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await H.goto(page, '/', 3000);
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
  await H.sleep(2500);
  await page.evaluate(() => document.querySelector('.bulk-seg[data-pane="allvalues"]').click());
  await H.sleep(5000);

  const pre = await page.evaluate(() => {
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    return { rows: p.querySelectorAll('tr').length, groups: p.querySelectorAll('tr').length, header: p.textContent.replace(/\s+/g, ' ').slice(0, 120) };
  });
  const t = Date.now();
  await page.evaluate(() => {
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    [...p.querySelectorAll('button')].find(x => /expand all/i.test(x.textContent)).click();
  });
  const marks = [];
  for (let i = 0; i < 6; i++) {
    await H.sleep(2000);
    marks.push(await page.evaluate((t0) => {
      const p = document.querySelector('[data-bulk-pane="allvalues"]');
      return { rows: p.querySelectorAll('tr').length, inputs: p.querySelectorAll('input').length };
    }));
  }
  await H.shot(page, 'perf20q-expandall-clean');
  const shown = await page.evaluate(() => {
    const p = document.querySelector('[data-bulk-pane="allvalues"]');
    const m = p.textContent.match(/Showing[^·]*/);
    return { showing: m ? m[0].trim() : null, scrollH: p.scrollHeight };
  });
  console.log(JSON.stringify({ pre, expandAllMs: Date.now() - t, marks, shown, errors: H.errors(page).slice(0, 5) }, null, 1));
  await browser.close();
})();
