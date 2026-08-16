const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

async function setSearch(page, sel, t) {
  await page.evaluate(s => { const e = document.querySelector(s); e.focus(); e.select(); }, sel);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.press('Backspace'); await H.sleep(300);
  if (t) await page.keyboard.type(t, { delay: 18 });
  await H.sleep(2200);
}
const overlays = (page) => page.evaluate(() => {
  const els = [...document.querySelectorAll('.tfx-overlay, .modal, .popup, .fsp-overlay, [role="dialog"]')].filter(e => e.offsetParent);
  return els.map(e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 220));
});

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  const posted = [];
  page.on('request', r => { if (/\/field\/edit/.test(r.url())) posted.push(new Date().toISOString().slice(17) + ' ' + (r.postData() || '').slice(0, 160)); });
  const dialogs = []; page.on('dialog', async d => { dialogs.push(d.message()); await d.accept(); });
  await H.goto(page, '/', 3000);
  await page.evaluate(() => { const b = [...document.querySelectorAll('#pending-tray button')].find(x => /Discard all/i.test(x.textContent)); if (b) b.click(); });
  await H.sleep(2500);
  out.trayStart = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  await page.evaluate(() => document.querySelector('a[hx-get="/explorer"]').click());
  await H.sleep(6000);
  await setSearch(page, '#explorer-search', 'grid_location');
  const NS = '.tree-node[data-path="qubits.q1.grid_location"]';
  out.before = await page.$eval(NS + ' .tree-row', e => e.textContent.replace(/\s+/g, ' ').trim());

  // step 1: type 1,2 into the string field
  await page.$eval(NS, e => e.scrollIntoView({ block: 'center' }));
  await page.click(NS + ' .tree-val');
  await H.sleep(600);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('1,2', { delay: 50 });
  await page.keyboard.press('Enter');
  await H.sleep(500);
  out.overlays_t0_5 = await overlays(page);
  await H.sleep(2500);
  out.after1 = await page.$eval(NS + ' .tree-row', e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 120));
  out.toasts1 = await page.evaluate(() => [...document.querySelectorAll('.toast')].map(t => t.textContent.trim()));
  out.overlays1 = await overlays(page);
  out.shot1 = await H.shot(page, 'le23-after-1comma2');
  out.posted1 = posted.slice(); posted.length = 0;

  // step 2: now type 3,4 (field now holds numeric text "12")
  await page.click(NS + ' .tree-val');
  await H.sleep(600);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('3,4', { delay: 50 });
  await page.keyboard.press('Enter');
  await H.sleep(800);
  out.overlays_step2_t0_8 = await overlays(page);
  out.shot2a = await H.shot(page, 'le23-step2-immediately');
  await H.sleep(3000);
  out.after2 = await page.$eval(NS + ' .tree-row', e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 150));
  out.overlays2 = await overlays(page);
  out.posted2 = posted.slice();
  out.shot2b = await H.shot(page, 'le23-step2-after');
  out.dialogs = dialogs;
  out.tray = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
