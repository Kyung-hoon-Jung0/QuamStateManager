const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const SEL = 'input[data-dot-path="qubits.q2.z.opx_output.output_mode"]';

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  const dialogs = [];
  page.on('dialog', async d => { dialogs.push(d.type() + ': ' + d.message()); await d.accept(); });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);

  // filter to "amplified"
  await page.click('#bulk-search');
  await page.keyboard.type('amplified', { delay: 20 });
  await H.sleep(1200);
  out.filtered = await page.evaluate(() => ({
    count: document.querySelector('#bulk-search-count').textContent.trim(),
    rows: [...document.querySelectorAll('#bulk-table tbody tr')].filter(r => r.offsetParent).map(r => r.getAttribute('data-qubit')),
  }));

  // type into q2 cell under the filter
  await page.click(SEL);
  out.focusOk = await page.evaluate(s => document.activeElement === document.querySelector(s), SEL);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('direct', { delay: 60 });
  await H.sleep(900);
  out.midType = await page.evaluate(s => {
    const el = document.querySelector(s);
    const tr = el && el.closest('tr');
    return {
      value: el ? el.value : 'ABSENT',
      focused: document.activeElement === el,
      rowVisible: tr ? !!tr.offsetParent : null,
      count: document.querySelector('#bulk-search-count').textContent.trim(),
      visRows: [...document.querySelectorAll('#bulk-table tbody tr')].filter(r => r.offsetParent).map(r => r.getAttribute('data-qubit')),
    };
  }, SEL);
  out.shotMid = await H.shot(page, 'le14-midtype-filtered');
  await page.keyboard.press('Enter');
  await H.sleep(2000);
  out.afterEnter = await page.evaluate(s => {
    const el = document.querySelector(s);
    const tr = el && el.closest('tr');
    return {
      value: el ? el.value : 'ABSENT', orig: el ? el.getAttribute('data-orig') : null,
      rowVisible: tr ? !!tr.offsetParent : null,
      count: document.querySelector('#bulk-search-count').textContent.trim(),
      tray: document.querySelector('#pending-tray').getAttribute('data-change-count'),
    };
  }, SEL);
  out.shotAfter = await H.shot(page, 'le14-after-filtered-commit');

  // clear search, verify the value stuck
  await page.click('#bulk-search');
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.press('Backspace');
  await H.sleep(1200);
  out.afterClear = await page.evaluate(s => {
    const el = document.querySelector(s);
    return el ? { v: el.value, cls: el.className } : 'ABSENT';
  }, SEL);
  out.dialogs = dialogs;
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
