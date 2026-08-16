const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  out.status = await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);
  out.err1 = H.errors(page);

  // inventory of output_mode cells (may be cold/detached)
  out.omCells = await page.evaluate(() => {
    const r = [];
    document.querySelectorAll('[data-dot-path]').forEach(el => {
      const p = el.getAttribute('data-dot-path') || '';
      if (p.includes('output_mode')) r.push({
        path: p, tag: el.tagName, type: el.type || '', value: el.value,
        readOnly: !!el.readOnly, disabled: !!el.disabled, list: el.getAttribute('list') || '',
        row: (el.closest('tr') || {}).getAttribute ? el.closest('tr').getAttribute('data-qubit') : '',
        colkey: (el.closest('td') || {}).getAttribute ? el.closest('td').getAttribute('data-col-key') : ''
      });
    });
    return r;
  });
  out.totalInputs = await page.$$eval('[data-dot-path]', e => e.length);
  // how many cold cells (virtualized)?
  out.coldTds = await page.evaluate(() => document.querySelectorAll('td[data-cold],td.bulk-cold').length);
  out.tdSample = await page.evaluate(() => {
    const td = document.querySelector('#bulk-table tbody td');
    return td ? td.outerHTML.slice(0, 400) : null;
  });
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
