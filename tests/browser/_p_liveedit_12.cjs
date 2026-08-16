const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  const dialogs = [];
  page.on('dialog', async d => { dialogs.push(d.type() + ': ' + d.message()); await d.accept(); });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);
  out.freshValues = await page.evaluate(() => {
    const keys = ['q1-2', 'q5-6', 'q5-10', 'q3-4'];
    const r = {};
    keys.forEach(k => {
      const el = document.querySelector('input[data-dot-path="qubit_pairs.' + k + '.qubit_control"]');
      if (el) r[k] = el.value;
    });
    return r;
  });
  out.trayBefore = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  // open tray drawer + Discard all
  out.trayButtons = await page.evaluate(() => [...document.querySelectorAll('#pending-tray button, #pending-tray a')].map(b => b.textContent.replace(/\s+/g, ' ').trim()).slice(0, 20));
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('#pending-tray button, #pending-tray a')].find(x => /Review/i.test(x.textContent));
    if (b) b.click();
  });
  await H.sleep(1500);
  out.afterReviewClickButtons = await page.evaluate(() => [...document.querySelectorAll('#pending-tray button, #pending-tray a')].map(b => b.textContent.replace(/\s+/g, ' ').trim()).slice(0, 25));
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('#pending-tray button, #pending-tray a')].find(x => /Discard all/i.test(x.textContent));
    if (b) b.click();
  });
  await H.sleep(3000);
  out.dialogs = dialogs;
  out.trayAfter = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
