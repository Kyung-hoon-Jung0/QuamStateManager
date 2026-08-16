const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const st = await H.goto(page, '/', 3000);
  const links = await page.$$eval('#sidebar a, #sidebar button', els => els.map(e => ({
    tag: e.tagName, text: (e.textContent || '').trim().slice(0, 40),
    hx: e.getAttribute('hx-get') || e.getAttribute('hx-post') || '', href: e.getAttribute('href') || '', id: e.id
  })));
  console.log(JSON.stringify({ status: st, links, errors: H.errors(page) }, null, 1));
  await H.shot(page, 'gen_00_shell');
  await browser.close();
})();
