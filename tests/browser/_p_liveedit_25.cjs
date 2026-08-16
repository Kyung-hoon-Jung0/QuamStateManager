const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

const chipState = (page) => page.evaluate(() => ({
  pressed: [...document.querySelectorAll('#bulk-chipbar .bulk-chip')].filter(b => b.getAttribute('aria-pressed') === 'true').map(b => b.textContent.trim()),
  mode: (document.querySelector('#bulk-chip-mode') || {}).textContent?.trim(),
  search: (document.querySelector('#bulk-search') || {}).value,
  count: (document.querySelector('#bulk-search-count') || {}).textContent?.trim(),
  offer: (() => { const o = document.querySelector('#bulk-chip-offer'); return o && !o.hidden ? o.textContent.replace(/\s+/g, ' ').trim() : null; })(),
  visCols: [...document.querySelectorAll('#bulk-table thead tr:last-child th')].filter(t => t.offsetParent).map(t => t.textContent.trim().split('\n')[0].slice(0, 24)),
}));

async function clickChip(page, label) {
  await page.evaluate(l => {
    const b = [...document.querySelectorAll('#bulk-chipbar .bulk-chip')].find(x => x.textContent.trim() === l);
    if (b) b.click();
  }, label);
  await H.sleep(1200);
}

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  const dialogs = []; page.on('dialog', async d => { dialogs.push(d.message()); await d.accept(); });
  await H.goto(page, '/', 3000);
  await page.evaluate(() => { const b = [...document.querySelectorAll('#pending-tray button')].find(x => /Discard all/i.test(x.textContent)); if (b) b.click(); });
  await H.sleep(2500);
  out.trayStart = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await H.sleep(9000);

  out.chip0 = await chipState(page);
  await clickChip(page, 'x180'); out.chip_x180 = await chipState(page);
  await clickChip(page, 'Amp'); out.chip_x180_amp = await chipState(page);
  await clickChip(page, 'Amp'); out.chip_release_amp = await chipState(page);
  await clickChip(page, 'x180'); out.chip_release_all = await chipState(page);
  // zero-match combo in AND
  await clickChip(page, 'x180'); await clickChip(page, 'Coherence');
  out.chip_zero = await chipState(page);
  out.shotZero = await H.shot(page, 'le25-chip-zero');
  // accept the "try OR?" offer if present
  const yes = await page.$('#bulk-chip-offer-yes');
  if (yes) {
    const visible = await page.evaluate(() => { const o = document.querySelector('#bulk-chip-offer'); return o && !o.hidden; });
    out.offerVisible = visible;
    if (visible) { await page.click('#bulk-chip-offer-yes'); await H.sleep(1500); out.chip_afterYes = await chipState(page); }
  }
  out.shotAfterYes = await H.shot(page, 'le25-after-yes');

  // clear chips
  await page.evaluate(() => {
    document.querySelectorAll('#bulk-chipbar .bulk-chip[aria-pressed="true"]').forEach(b => b.click());
  });
  await H.sleep(1500);
  out.chipCleared = await chipState(page);

  // ---- keyboard: Tab / Shift+Tab / Escape on a numeric cell
  const A = 'input[data-dot-path="qubits.q1.f_01"]';
  await page.$eval(A, e => e.scrollIntoView({ block: 'center', inline: 'center' }));
  await page.click(A);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('4333000999', { delay: 20 });
  await page.keyboard.press('Escape');
  await H.sleep(1200);
  out.afterEscape = await page.evaluate(s => { const e = document.querySelector(s); return { v: e.value, orig: e.getAttribute('data-orig'), cls: e.className, focused: document.activeElement === e }; }, A);

  await page.click(A);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('4333000999', { delay: 20 });
  await page.keyboard.press('Tab');
  await H.sleep(1500);
  out.afterTab = await page.evaluate(s => {
    const e = document.querySelector(s);
    const a = document.activeElement;
    return { cellValue: e.value, cellOrig: e.getAttribute('data-orig'), focusPath: a && a.getAttribute ? a.getAttribute('data-dot-path') : null, focusTag: a ? a.tagName : null };
  }, A);
  await page.keyboard.down('Shift'); await page.keyboard.press('Tab'); await page.keyboard.up('Shift');
  await H.sleep(800);
  out.afterShiftTab = await page.evaluate(() => { const a = document.activeElement; return { path: a && a.getAttribute ? a.getAttribute('data-dot-path') : null, tag: a ? a.tagName : null }; });
  out.tray = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  out.dialogs = dialogs;
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
