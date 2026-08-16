const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const SEL = 'input[data-dot-path="qubits.q2.z.opx_output.output_mode"]';

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  await H.goto(page, '/', 3000);
  out.beforeNavExplorerEl = await page.evaluate(() => !!document.getElementById('explorer-tree-state'));
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);
  out.onBulkExplorerEl = await page.evaluate(() => !!document.getElementById('explorer-tree-state'));
  out.parkedNodes = await page.evaluate(() => {
    const hidden = [...document.querySelectorAll('[data-pane-parked], .pane-parked, [hidden]')].length;
    return { hidden, panes: [...document.querySelectorAll('#table-pane, #table-pane *[id^="explorer"]')].map(e => e.id).slice(0, 10) };
  });
  // instrument htmx.ajax to record call stacks
  await page.evaluate(() => {
    window.__ajaxLog = [];
    const orig = window.htmx.ajax;
    window.htmx.ajax = function (m, u, o) {
      window.__ajaxLog.push({ m, u, stack: (new Error()).stack.split('\n').slice(1, 6).join(' | ') });
      return orig.apply(this, arguments);
    };
  });
  // one edit then undo
  await page.$eval(SEL, el => el.scrollIntoView({ block: 'center' }));
  await page.click(SEL);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('direct', { delay: 25 });
  await page.keyboard.press('Enter');
  await H.sleep(1500);
  out.trayAfterEdit = await page.evaluate(() => document.querySelector('#pending-tray').textContent.replace(/\s+/g, ' ').match(/\d+ unsaved/)?.[0]);
  await page.keyboard.down('Control'); await page.keyboard.press('KeyZ'); await page.keyboard.up('Control');
  await H.sleep(2500);
  out.ajaxLog = await page.evaluate(() => window.__ajaxLog);
  out.after = await page.evaluate(() => ({
    explorer: !!document.querySelector('#explorer-search'),
    bulk: !!document.querySelector('#bulk-table'),
    tray: document.querySelector('#pending-tray').textContent.replace(/\s+/g, ' ').match(/\d+ unsaved/)?.[0] || 'none',
  }));
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
