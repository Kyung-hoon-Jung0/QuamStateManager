const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const SEL = 'input[data-dot-path="qubits.q2.z.opx_output.output_mode"]';

const snap = (page) => page.evaluate((s) => {
  const pane = document.querySelector('#table-pane');
  return {
    cellPresent: !!document.querySelector(s),
    cellValue: (document.querySelector(s) || {}).value,
    searchBox: !!document.querySelector('#bulk-search'),
    bulkTable: !!document.querySelector('#bulk-table'),
    rows: document.querySelectorAll('#bulk-table tbody tr').length,
    inputs: document.querySelectorAll('#bulk-table input').length,
    paneHead: pane ? pane.textContent.replace(/\s+/g, ' ').trim().slice(0, 200) : null,
    tray: (document.querySelector('#pending-tray') || {}).textContent?.replace(/\s+/g, ' ').match(/(\d+) unsaved/)?.[0] || 'none',
  };
}, SEL);

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);
  out.onLoad = await snap(page);

  // Undo everything currently pending via Ctrl+Z presses (max 6)
  const seq = [];
  for (let i = 0; i < 6; i++) {
    await page.keyboard.down('Control'); await page.keyboard.press('KeyZ'); await page.keyboard.up('Control');
    await H.sleep(1600);
    seq.push(await snap(page));
    if (seq[seq.length - 1].tray === 'none') break;
  }
  out.undoSeq = seq;
  out.shot = await H.shot(page, 'le5-after-undos');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
