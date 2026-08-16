const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const SEL = 'input[data-dot-path="qubits.q2.z.opx_output.output_mode"]';

async function cell(page, sel) {
  return page.evaluate(s => {
    const el = document.querySelector(s);
    if (!el) return 'ABSENT';
    const tr = el.closest('tr'), td = el.closest('td');
    return {
      value: el.value, cls: el.className, orig: el.getAttribute('data-orig'),
      rowHidden: tr ? (tr.offsetParent === null) : null,
      tdDisplay: td ? getComputedStyle(td).display : null,
      visible: !!(el.offsetParent),
    };
  }, sel);
}

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  const net = [];
  page.on('response', r => { const u = r.url(); if (/\/field\/|\/undo|\/redo|bulk/.test(u)) net.push(r.status() + ' ' + r.request().method() + ' ' + u.replace('http://127.0.0.1:8822', '')); });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);

  out.start = await cell(page, SEL);

  // --- set back to amplified by typing
  await page.$eval(SEL, el => el.scrollIntoView({ block: 'center' }));
  await page.click(SEL);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('amplified', { delay: 30 });
  await page.keyboard.press('Enter');
  await H.sleep(1500);
  out.afterAmplified = await cell(page, SEL);

  // --- Ctrl+Z undo
  await page.keyboard.down('Control'); await page.keyboard.press('KeyZ'); await page.keyboard.up('Control');
  await H.sleep(1800);
  out.afterUndo = await cell(page, SEL);
  out.trayAfterUndo = await page.evaluate(() => (document.querySelector('#pending-tray') || {}).textContent?.replace(/\s+/g, ' ').trim().slice(0, 160));

  // --- Ctrl+Shift+Z redo
  await page.keyboard.down('Control'); await page.keyboard.down('Shift'); await page.keyboard.press('KeyZ');
  await page.keyboard.up('Shift'); await page.keyboard.up('Control');
  await H.sleep(1800);
  out.afterRedo = await cell(page, SEL);
  out.trayAfterRedo = await page.evaluate(() => (document.querySelector('#pending-tray') || {}).textContent?.replace(/\s+/g, ' ').trim().slice(0, 160));

  // --- search for "amplified": is the row/cell still reachable & editable?
  const searchSel = await page.evaluate(() => {
    const cands = [...document.querySelectorAll('input[type="search"], input[placeholder]')]
      .map(e => ({ id: e.id, ph: e.placeholder, cls: e.className }));
    return cands;
  });
  out.searchInputs = searchSel;
  console.log(JSON.stringify(out, null, 1));
  out.shot = await H.shot(page, 'le4-state');
  out.errors = H.errors(page);
  console.log(JSON.stringify({ shot: out.shot, errors: out.errors, net: net.slice(-12) }, null, 1));
  await browser.close();
})();
