const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const PC = 'input[data-dot-path="qubit_pairs.q1-2.qubit_control"]';

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  const resp = [];
  page.on('response', async r => {
    if (/\/field\/edit/.test(r.url())) {
      let b = ''; try { b = (await r.text()).slice(0, 400); } catch (e) { }
      resp.push({ s: r.status(), u: r.url().replace('http://127.0.0.1:8822', ''), body: b });
    }
  });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);

  out.pointerCellsPair = await page.evaluate(() => {
    const r = [];
    document.querySelectorAll('#bulk-pair-table [data-dot-path]').forEach(el => {
      const p = el.getAttribute('data-dot-path') || '';
      if (/qubit_control|qubit_target|moving_qubit|opx_output$/.test(p)) r.push({ p, v: el.value, ro: !!el.readOnly, ptr: el.getAttribute('data-is-pointer'), title: (el.title || '').slice(0, 80) });
    });
    return r.slice(0, 8);
  });

  const have = await page.$(PC);
  out.found = !!have;
  if (have) {
    await page.$eval(PC, el => el.scrollIntoView({ block: 'center', inline: 'center' }));
    await H.sleep(400);
    out.shotPtr = await H.shot(page, 'le9-pointer-cell');
    // type plain text -> expect refusal
    await page.click(PC);
    await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
    await page.keyboard.type('q3', { delay: 30 });
    await page.keyboard.press('Enter');
    await H.sleep(2000);
    out.afterPlainText = await page.evaluate(s => { const e = document.querySelector(s); return e ? { v: e.value, cls: e.className, orig: e.getAttribute('data-orig') } : 'ABSENT'; }, PC);
    out.toast1 = await page.evaluate(() => [...document.querySelectorAll('.toast')].map(t => t.textContent.trim()).slice(0, 4));
    out.modal1 = await page.evaluate(() => {
      const m = document.querySelector('.modal, .tfx-overlay, dialog[open], .ptr-overlay, .fsp-overlay');
      return m ? m.textContent.replace(/\s+/g, ' ').trim().slice(0, 300) : null;
    });
    out.shotAfterPlain = await H.shot(page, 'le9-after-plaintext');
    out.resp1 = resp.slice(); resp.length = 0;

    // now a valid pointer
    await page.click(PC);
    await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
    await page.keyboard.type('#/qubits/q3', { delay: 30 });
    await page.keyboard.press('Enter');
    await H.sleep(2500);
    out.afterPointer = await page.evaluate(s => { const e = document.querySelector(s); return e ? { v: e.value, cls: e.className, orig: e.getAttribute('data-orig') } : 'ABSENT'; }, PC);
    out.modal2 = await page.evaluate(() => {
      const m = document.querySelector('.modal, .tfx-overlay, dialog[open], .ptr-overlay');
      return m ? m.textContent.replace(/\s+/g, ' ').trim().slice(0, 400) : null;
    });
    out.resp2 = resp.slice();
    out.shotAfterPtr = await H.shot(page, 'le9-after-pointer');
    out.tray = await page.evaluate(() => document.querySelector('#pending-tray').textContent.replace(/\s+/g, ' ').match(/\d+ unsaved/)?.[0] || 'clean');
  }
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
