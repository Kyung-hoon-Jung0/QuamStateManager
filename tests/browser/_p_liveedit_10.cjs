const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const PC = 'input[data-dot-path="qubit_pairs.q1-2.qubit_control"]';

const scanCtl = (page) => page.evaluate(() => {
  const r = [];
  document.querySelectorAll('#bulk-pair-table input[data-dot-path$=".qubit_control"]').forEach(el => {
    r.push({ p: el.getAttribute('data-dot-path'), v: el.value, orig: el.getAttribute('data-orig'), cls: el.className, res: el.getAttribute('data-resolved') });
  });
  return r.slice(0, 8);
});

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  const posts = [];
  page.on('request', r => { if (/field\/edit/.test(r.url())) posts.push(r.url().replace('http://127.0.0.1:8822', '') + ' :: ' + (r.postData() || '').slice(0, 500)); });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);
  out.trayAtStart = await page.evaluate(() => document.querySelector('#pending-tray').textContent.replace(/\s+/g, ' ').match(/\d+ unsaved/)?.[0] || 'clean');
  out.before = await scanCtl(page);

  await page.$eval(PC, el => el.scrollIntoView({ block: 'center', inline: 'center' }));
  await page.click(PC);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('#/qubits/q5', { delay: 30 });
  out.duringTyping = await scanCtl(page);
  await page.keyboard.press('Enter');
  await H.sleep(2000);
  out.afterCommit = await scanCtl(page);
  out.posts = posts.slice();
  out.tray = await page.evaluate(() => {
    const t = document.querySelector('#pending-tray');
    return { count: t.getAttribute('data-change-count'), txt: t.textContent.replace(/\s+/g, ' ').slice(0, 200) };
  });
  out.shot = await H.shot(page, 'le10-mirror');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
