const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  await H.goto(page, '/', 2500);
  await page.click('a[hx-get="/generate"]');
  await H.sleep(6000);
  const out = {};
  out.rootPresent = await page.$('#generate-root') ? true : false;
  out.progress = await page.$eval('#gen-progress', e => e.textContent).catch(e => 'ERR ' + e.message);
  out.envHTML = await page.$eval('#gen-env-list', e => e.innerText.slice(0, 1200)).catch(e => 'ERR');
  out.envRows = await page.$$eval('#gen-env-list *[data-env],#gen-env-list button,#gen-env-list label', els =>
    els.slice(0, 40).map(e => ({ tag: e.tagName, cls: e.className, txt: (e.innerText || '').trim().slice(0, 90) })));
  await H.shot(page, 'gen_01_step1_env');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
