const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

async function setSearch(page, sel, t) {
  await page.evaluate(s => { const e = document.querySelector(s); e.focus(); e.select(); }, sel);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.press('Backspace'); await H.sleep(300);
  if (t) await page.keyboard.type(t, { delay: 18 });
  await H.sleep(2200);
}

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  let resp = [];
  page.on('response', async r => {
    if (/\/field\/edit/.test(r.url())) { let b = ''; try { b = (await r.text()); } catch (e) { } resp.push(r.status() + ' ' + b.split('"tray_html"')[0].slice(0, 220)); }
  });
  const dialogs = []; page.on('dialog', async d => { dialogs.push(d.message()); await d.accept(); });
  await H.goto(page, '/', 3000);
  await page.evaluate(() => { const b = [...document.querySelectorAll('#pending-tray button')].find(x => /Discard all/i.test(x.textContent)); if (b) b.click(); });
  await H.sleep(2500);
  out.trayStart = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));

  // GRID first (clean state)
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await H.sleep(9000);
  const GS = 'input[data-dot-path="qubits.q2.grid_location"]';
  out.gridBefore = await page.evaluate(s => (document.querySelector(s) || {}).value, GS);
  await page.$eval(GS, e => e.scrollIntoView({ block: 'center', inline: 'center' }));
  await page.click(GS);
  resp = [];
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('2,1', { delay: 45 });
  await page.keyboard.press('Enter');
  await H.sleep(2500);
  out.gridAfter = await page.evaluate(s => { const e = document.querySelector(s); return e ? { v: e.value, orig: e.getAttribute('data-orig'), strnum: e.getAttribute('data-str-numeric') } : 'ABSENT'; }, GS);
  out.gridResp = resp.slice();
  out.gridShot = await H.shot(page, 'le24-grid-gridloc');

  // EXPLORER
  await page.evaluate(() => document.querySelector('a[hx-get="/explorer"]').click());
  await H.sleep(6000);
  await setSearch(page, '#explorer-search', 'grid_location');
  const NS = '.tree-node[data-path="qubits.q1.grid_location"]';
  out.expBefore = await page.$eval(NS + ' .tree-row', e => e.textContent.replace(/\s+/g, ' ').trim());
  await page.$eval(NS, e => e.scrollIntoView({ block: 'center' }));
  await page.click(NS + ' .tree-val');
  await H.sleep(600);
  resp = [];
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('1,2', { delay: 45 });
  await page.keyboard.press('Enter');
  await H.sleep(2500);
  out.expAfter = await page.$eval(NS + ' .tree-row', e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 120));
  out.expResp = resp.slice();

  // what does the tray say the change was?
  out.trayReview = await page.evaluate(() => {
    const b = [...document.querySelectorAll('#pending-tray button')].find(x => /Review/i.test(x.textContent));
    if (b) b.click();
    return null;
  });
  await H.sleep(1500);
  out.trayText = await page.evaluate(() => document.querySelector('#pending-tray').textContent.replace(/\s+/g, ' ').trim().slice(0, 700));
  out.trayShot = await H.shot(page, 'le24-tray');
  out.dialogs = dialogs;
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
