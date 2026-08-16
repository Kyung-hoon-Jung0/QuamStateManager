const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  await H.goto(page, '/', 2500);
  await page.click('a[hx-get="/generate"]');
  await H.sleep(6000);
  const out = { steps: [] };
  // dump the env list markup so we know what to click
  out.envMarkup = await page.$eval('#gen-env-list', e => e.innerHTML.slice(0, 3000));
  await browser.close();
  console.log(JSON.stringify(out, null, 1));
})();
