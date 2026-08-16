const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const P = 'ports.analog_outputs.con1.4.1.output_mode';

async function gotoExplorer(page) {
  await H.goto(page, '/', 3000);
  await page.evaluate(() => document.querySelector('a[hx-get="/explorer"]').click());
  await H.sleep(6000);
}

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  const resp = [];
  page.on('response', async r => {
    if (/\/field\/(edit|peek)/.test(r.url())) { let b = ''; try { b = (await r.text()).slice(0, 300); } catch (e) { } resp.push(r.status() + ' ' + r.url().replace('http://127.0.0.1:8822', '') + ' ' + b); }
  });
  const dialogs = []; page.on('dialog', async d => { dialogs.push(d.message()); await d.accept(); });
  await gotoExplorer(page);

  // search to reveal the node
  await page.click('#explorer-search');
  await page.keyboard.type('output_mode', { delay: 15 });
  await H.sleep(2500);
  out.node = await page.evaluate(p => {
    const n = document.querySelector('.tree-node[data-path="' + p + '"]');
    return n ? { html: n.outerHTML.slice(0, 700), visible: !!n.offsetParent } : 'ABSENT';
  }, P);
  out.shot0 = await H.shot(page, 'le16-explorer-node');
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
