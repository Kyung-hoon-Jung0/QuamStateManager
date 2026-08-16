const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  out.status = await H.goto(page, '/', 3000);
  // sidebar links
  out.sidebar = await page.$$eval('a[hx-get]', els => els.map(e => e.getAttribute('hx-get') + ' :: ' + e.textContent.trim().slice(0, 40)));
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(10000);
  out.errAfterBulk = H.errors(page);
  out.cells = await page.$$eval('#table-pane [data-path]', els => els.length).catch(e => String(e));
  // find any cell whose data-path mentions output_mode
  out.outputModeCells = await page.evaluate(() => {
    const r = [];
    document.querySelectorAll('[data-path]').forEach(el => {
      const p = el.getAttribute('data-path') || '';
      if (p.includes('output_mode')) r.push({ path: p, tag: el.tagName, cls: el.className, txt: (el.textContent || '').trim().slice(0, 40) });
    });
    return r.slice(0, 20);
  });
  // column headers
  out.headers = await page.$$eval('#bulk-table thead th', els => els.map(e => e.textContent.trim().slice(0, 30))).catch(e => String(e));
  out.tables = await page.$$eval('table', els => els.map(e => e.id || e.className));
  out.shot = await H.shot(page, 'le1-bulk');
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
