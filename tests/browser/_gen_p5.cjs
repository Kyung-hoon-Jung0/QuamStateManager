const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  await H.goto(page, '/', 2500);
  await page.click('a[hx-get="/generate"]');
  await H.sleep(6000);
  await page.click('.gen-env-row[data-python="D:\\\\miniconda3\\\\envs\\\\cqt\\\\python.exe"]');
  await H.sleep(2000);
  await page.click('#gen-next'); await H.sleep(800);
  await page.click('#gen-next'); await H.sleep(1200);
  const slots = await page.$$eval('#gen-chassis-list [data-slot]', els => els.slice(0,2).map(e => ({
    html: e.outerHTML.slice(0,400), rect: e.getBoundingClientRect().toJSON() })));
  await page.evaluate(() => {
    const s = document.querySelector('#gen-chassis-list [data-slot]');
    s.scrollIntoView({ block: 'center' });
    s.click();
  });
  await H.sleep(700);
  const menu = await page.$eval('#gen-slot-menu', e => ({ hidden: e.hidden, html: e.innerHTML.slice(0, 900) }));
  console.log(JSON.stringify({ slots, menu, errors: H.errors(page) }, null, 1));
  await H.shot(page, 'gen_03_slotmenu');
  await browser.close();
})();
