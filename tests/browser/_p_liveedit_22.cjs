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
  const out = { cases: [] };
  let resp = [];
  page.on('response', async r => {
    if (/\/field\/edit/.test(r.url())) { let b = ''; try { b = (await r.text()).slice(0, 200); } catch (e) { } resp.push(r.status() + ' ' + b.split('"tray_html"')[0]); }
  });
  let posted = [];
  page.on('request', r => { if (/\/field\/edit/.test(r.url())) posted.push((r.postData() || '').slice(0, 200)); });
  const dialogs = []; page.on('dialog', async d => { dialogs.push(d.message()); await d.accept(); });
  await H.goto(page, '/', 3000);
  await page.evaluate(() => document.querySelector('a[hx-get="/explorer"]').click());
  await H.sleep(6000);
  await setSearch(page, '#explorer-search', 'grid_location');
  const NS = '.tree-node[data-path="qubits.q1.grid_location"]';

  for (const typed of ['1,2', '3,4', 'A,1']) {
    resp = []; posted = [];
    const c = { typed };
    c.before = await page.$eval(NS + ' .tree-row', e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 60));
    await page.$eval(NS, e => e.scrollIntoView({ block: 'center' }));
    await page.click(NS + ' .tree-val');
    await H.sleep(600);
    await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
    await page.keyboard.type(typed, { delay: 40 });
    c.inputBeforeEnter = await page.$eval(NS + ' input', i => i.value).catch(() => 'no-input');
    await page.keyboard.press('Enter');
    await H.sleep(2000);
    c.after = await page.$eval(NS + ' .tree-row', e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 80));
    c.posted = posted.slice(); c.resp = resp.slice();
    out.cases.push(c);
  }
  out.tray = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  out.shot = await H.shot(page, 'le22-comma');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
