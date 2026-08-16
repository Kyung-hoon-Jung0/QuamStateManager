const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const SEL = 'input[data-dot-path="qubits.q2.z.opx_output.output_mode"]';

const snap = (page) => page.evaluate((s) => ({
  cell: (document.querySelector(s) || {}).value ?? 'ABSENT',
  bulkTable: !!document.querySelector('#bulk-table'),
  explorer: !!document.querySelector('#explorer-search'),
  url: location.pathname + location.search,
  tray: (document.querySelector('#pending-tray') || {}).textContent?.replace(/\s+/g, ' ').match(/(\d+) unsaved/)?.[0] || 'none',
}), SEL);

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  const net = [];
  page.on('request', r => { if (!/static|\.png|\.css/.test(r.url())) net.push('REQ ' + r.method() + ' ' + r.url().replace('http://127.0.0.1:8822', '')); });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);
  out.clean = await snap(page);
  net.length = 0;

  // one numeric edit? no - do the string edit again: amplified -> direct
  await page.$eval(SEL, el => el.scrollIntoView({ block: 'center' }));
  await page.click(SEL);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('direct', { delay: 25 });
  await page.keyboard.press('Enter');
  await H.sleep(1500);
  out.afterEdit = await snap(page);
  out.netEdit = net.slice(); net.length = 0;

  // Ctrl+Z: focus body first (not in an input) to be like a user pressing after commit
  await page.keyboard.down('Control'); await page.keyboard.press('KeyZ'); await page.keyboard.up('Control');
  await H.sleep(2500);
  out.afterUndo = await snap(page);
  out.netUndo = net.slice(); net.length = 0;
  out.shotUndo = await H.shot(page, 'le6-after-undo');

  // Ctrl+Shift+Z redo from wherever we are
  await page.keyboard.down('Control'); await page.keyboard.down('Shift'); await page.keyboard.press('KeyZ');
  await page.keyboard.up('Shift'); await page.keyboard.up('Control');
  await H.sleep(2500);
  out.afterRedo = await snap(page);
  out.netRedo = net.slice();
  out.shotRedo = await H.shot(page, 'le6-after-redo');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
