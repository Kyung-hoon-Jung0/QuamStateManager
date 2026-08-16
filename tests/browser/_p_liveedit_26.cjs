const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const A = 'input[data-dot-path="qubits.q1.f_01"]';

async function clickChip(page, label) {
  await page.evaluate(l => { const b = [...document.querySelectorAll('#bulk-chipbar .bulk-chip')].find(x => x.textContent.trim() === l); if (b) b.click(); }, label);
  await H.sleep(1200);
}
const offerState = (page) => page.evaluate(() => {
  const o = document.querySelector('#bulk-chip-offer');
  const cols = [...document.querySelectorAll('#bulk-table thead tr:last-child th')].filter(t => t.offsetParent).length;
  const pcols = [...document.querySelectorAll('#bulk-pair-table thead tr:last-child th')].filter(t => t.offsetParent).length;
  const rows = [...document.querySelectorAll('#bulk-table tbody tr')].filter(t => t.offsetParent).length;
  return { hidden: o ? o.hidden : 'no-el', text: o ? o.textContent.replace(/\s+/g, ' ').trim() : null, cols, pcols, rows, search: document.querySelector('#bulk-search').value, count: document.querySelector('#bulk-search-count').textContent.trim() };
});

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  await H.goto(page, '/', 3000);
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await H.sleep(9000);

  // (a) Escape
  await page.$eval(A, e => e.scrollIntoView({ block: 'center', inline: 'center' }));
  await page.click(A);
  out.escStart = await page.evaluate(s => ({ v: document.querySelector(s).value }), A);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('123', { delay: 30 });
  await H.sleep(300);
  await page.keyboard.press('Escape');
  await H.sleep(1200);
  out.escAfter = await page.evaluate(s => { const e = document.querySelector(s); return { v: e.value, orig: e.getAttribute('data-orig'), cls: e.className, focused: document.activeElement === e }; }, A);
  out.escShot = await H.shot(page, 'le26-escape');
  // second Escape?
  await page.keyboard.press('Escape'); await H.sleep(800);
  out.escAfter2 = await page.evaluate(s => { const e = document.querySelector(s); return { v: e.value, cls: e.className }; }, A);
  // clean it up: restore by typing orig then Escape? just set back manually
  await page.click(A);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('4333000000', { delay: 15 });
  await page.keyboard.press('Escape');
  await H.sleep(500);

  // (c) offer
  out.offer_none = await offerState(page);
  await clickChip(page, 'x180'); await clickChip(page, 'Coherence');
  out.offer_x180_coh = await offerState(page);
  out.shotCombo = await H.shot(page, 'le26-combo-zero');
  await page.evaluate(() => document.querySelectorAll('#bulk-chipbar .bulk-chip[aria-pressed="true"]').forEach(b => b.click()));
  await H.sleep(1000);
  // typed nonsense
  await page.click('#bulk-search');
  await page.keyboard.type('zzqq', { delay: 20 });
  await H.sleep(1500);
  out.offer_typed_nonsense = await offerState(page);
  // typed two real terms that can't co-occur
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('x180 coherence', { delay: 20 });
  await H.sleep(1500);
  out.offer_typed_combo = await offerState(page);
  out.shotTyped = await H.shot(page, 'le26-typed-zero');
  out.errors = H.errors(page);
  out.tray = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
