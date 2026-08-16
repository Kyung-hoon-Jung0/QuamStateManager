const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

async function search(page, term) {
  await page.click('#bulk-search');
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  if (term) await page.keyboard.type(term, { delay: 15 }); else await page.keyboard.press('Backspace');
  await H.sleep(1200);
  return page.evaluate(() => {
    const vis = (el) => !!(el.offsetParent);
    const tb = document.querySelector('#bulk-table');
    const heads = [...tb.querySelectorAll('thead tr:last-child th')];
    const visCols = heads.filter(vis).map(h => h.textContent.trim().split('\n')[0].slice(0, 26));
    const rows = [...tb.querySelectorAll('tbody tr')];
    const visRows = rows.filter(vis).map(r => r.getAttribute('data-qubit'));
    const pt = document.querySelector('#bulk-pair-table');
    const pvisCols = pt ? [...pt.querySelectorAll('thead tr:last-child th')].filter(vis).length : null;
    return {
      count: (document.querySelector('#bulk-search-count') || {}).textContent?.trim(),
      hint: (() => { const h = document.querySelector('#bulk-dyncol-hint'); return h && !h.hidden ? h.textContent.trim() : null; })(),
      offer: (() => { const o = document.querySelector('#bulk-chip-offer'); return o && !o.hidden ? o.textContent.replace(/\s+/g, ' ').trim() : null; })(),
      nVisCols: visCols.length, visCols: visCols.slice(0, 12),
      nVisRows: visRows.length, visRows: visRows.slice(0, 22),
      pairVisCols: pvisCols,
    };
  });
}

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);
  out.chips = await page.evaluate(() => [...document.querySelectorAll('#bulk-chipbar .bulk-chip')].map(b => b.textContent.trim()));
  out.baseline = await search(page, '');
  for (const t of ['x180', 'amp', 'x180 amp', 'x180 | length', 'freq', 'q3', 'amplified', 'zzqqnonsense']) {
    out['s_' + t] = await search(page, t);
  }
  out.shotAmplified = await (async () => { await search(page, 'amplified'); return H.shot(page, 'le13-search-amplified'); })();
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
