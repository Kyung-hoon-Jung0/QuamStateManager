const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  const st = await H.goto(page, '/', 3000);
  // find sidebar links
  const links = await page.$$eval('a[hx-get]', els => els.map(e => ({t: (e.textContent||'').trim().slice(0,40), h: e.getAttribute('hx-get')})));
  console.log(JSON.stringify({status: st, links}, null, 1));
  await browser.close();
})();
