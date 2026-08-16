const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);
  out.tray = await page.evaluate(() => document.querySelector('#pending-tray').textContent.replace(/\s+/g, ' ').match(/\d+ unsaved/)?.[0] || 'clean');

  // pair grid presence + headers
  out.pairHeaders = await page.evaluate(() => {
    const t = document.querySelectorAll('table.bulk-table');
    return [...t].map(tb => ({ id: tb.id, headers: [...tb.querySelectorAll('thead th')].map(h => h.textContent.trim().replace(/\s+/g, ' ')).slice(0, 40) }));
  });
  // any cell whose dot-path is a pointer to a qubit
  out.pointerCells = await page.evaluate(() => {
    const r = [];
    document.querySelectorAll('input[data-is-pointer="1"]').forEach(el => {
      r.push({ p: el.getAttribute('data-dot-path'), v: el.value, res: el.getAttribute('data-resolved') });
    });
    return { n: r.length, sample: r.slice(0, 8) };
  });
  out.qubitControlCells = await page.evaluate(() => {
    const r = [];
    document.querySelectorAll('[data-dot-path]').forEach(el => {
      const p = el.getAttribute('data-dot-path') || '';
      if (/qubit_control|qubit_target|moving_qubit/.test(p)) r.push({ p, v: el.value, tag: el.tagName, ro: !!el.readOnly });
    });
    return r.slice(0, 10);
  });
  // Flat View pane: does it list pointers / __class__?
  out.chips = await page.evaluate(() => [...document.querySelectorAll('#bulk-chipbar .bulk-chip')].map(b => b.textContent.trim()));
  out.searchCount = await page.evaluate(() => document.querySelector('#bulk-search-count')?.textContent);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
