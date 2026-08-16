const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const PC = 'input[data-dot-path="qubit_pairs.q1-2.qubit_control"]';

const scanCtl = (page) => page.evaluate(() => {
  const r = {};
  document.querySelectorAll('#bulk-pair-table input[data-dot-path$=".qubit_control"]').forEach(el => {
    r[el.getAttribute('data-dot-path')] = el.value + ' | orig=' + el.getAttribute('data-orig');
  });
  return r;
});

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  const posts = [];
  const dialogs = [];
  page.on('dialog', async d => { dialogs.push(d.type() + ': ' + d.message()); await d.accept(); });
  page.on('request', r => { if (/field\/edit/.test(r.url())) posts.push((r.postData() || '').slice(0, 900)); });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);
  out.before = await scanCtl(page);

  await page.$eval(PC, el => el.scrollIntoView({ block: 'center', inline: 'center' }));
  await page.click(PC);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('#/qubits/q7', { delay: 30 });
  // do NOT press Enter — click "Apply all (pairs)" instead
  out.applyBtn = await page.evaluate(() => {
    const bs = [...document.querySelectorAll('button')].filter(b => /Apply all \(pairs\)/.test(b.textContent));
    return bs.map(b => ({ txt: b.textContent.trim(), disabled: b.disabled }));
  });
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find(b => /Apply all \(pairs\)/.test(b.textContent));
    if (b) b.click();
  });
  await H.sleep(3000);
  out.posts = posts.slice();
  out.after = await scanCtl(page);
  out.tray = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  out.shot = await H.shot(page, 'le11-applyall');
  out.dialogs = dialogs;
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
