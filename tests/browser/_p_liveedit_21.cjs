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
    if (/\/field\/edit/.test(r.url())) { let b = ''; try { b = (await r.text()).slice(0, 160); } catch (e) { } resp.push(r.status() + ' ' + b.split('"tray_html"')[0]); }
  });
  const dialogs = []; page.on('dialog', async d => { dialogs.push(d.message()); await d.accept(); });
  await H.goto(page, '/', 3000);
  // discard leftovers
  await page.evaluate(() => { const b = [...document.querySelectorAll('#pending-tray button')].find(x => /Discard all/i.test(x.textContent)); if (b) b.click(); });
  await H.sleep(2500);
  out.trayStart = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));

  // ---- EXPLORER: retype the field's OWN value "1,0"
  await page.evaluate(() => document.querySelector('a[hx-get="/explorer"]').click());
  await H.sleep(6000);
  await setSearch(page, '#explorer-search', 'grid_location');
  const NS = '.tree-node[data-path="qubits.q1.grid_location"]';
  out.explorerBefore = await page.$eval(NS + ' .tree-row', e => e.textContent.replace(/\s+/g, ' ').trim());
  await page.$eval(NS, e => e.scrollIntoView({ block: 'center' }));
  await page.click(NS + ' .tree-val');
  await H.sleep(600);
  out.editorValue = await page.$eval(NS + ' input', i => i.value);
  resp = [];
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('1,0', { delay: 40 });
  await page.keyboard.press('Enter');
  await H.sleep(2000);
  out.explorerAfter = await page.$eval(NS + ' .tree-row', e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 120));
  out.explorerResp = resp.slice();

  // ---- GRID: same field via the "Grid loc" column
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await H.sleep(9000);
  const GS = 'input[data-dot-path="qubits.q2.grid_location"]';
  out.gridCellBefore = await page.evaluate(s => { const e = document.querySelector(s); return e ? e.value : 'ABSENT'; }, GS);
  if (await page.$(GS)) {
    await page.$eval(GS, e => e.scrollIntoView({ block: 'center', inline: 'center' }));
    await page.click(GS);
    resp = [];
    await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
    await page.keyboard.type('2,0', { delay: 40 });
    await page.keyboard.press('Enter');
    await H.sleep(2000);
    out.gridCellAfter = await page.evaluate(s => { const e = document.querySelector(s); return e ? { v: e.value, orig: e.getAttribute('data-orig') } : 'ABSENT'; }, GS);
    out.gridResp = resp.slice();
  }
  out.tray = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  out.shot = await H.shot(page, 'le21-gridloc');
  out.dialogs = dialogs;
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
