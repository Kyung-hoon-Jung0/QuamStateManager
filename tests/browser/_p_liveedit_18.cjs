const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

async function setSearch(page, t) {
  await page.click('#explorer-search');
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  if (t) await page.keyboard.type(t, { delay: 12 }); else await page.keyboard.press('Backspace');
  await H.sleep(2200);
}

async function editLeaf(page, path, text, opts) {
  const o = opts || {};
  const nodeSel = '.tree-node[data-path="' + path + '"]';
  const r = { path };
  r.diag = await page.evaluate((sel) => {
    const n = document.querySelector(sel);
    return { nodeExists: !!n, vis: n ? !!n.offsetParent : null,
             html: n ? n.outerHTML.slice(0, 200) : null,
             totalNodes: document.querySelectorAll('.tree-node').length,
             search: (document.querySelector('#explorer-search')||{}).value };
  }, nodeSel);
  const el = await page.$(nodeSel + ' .tree-val');
  if (!el) { r.error = 'value span not found'; return r; }
  r.before = await page.$eval(nodeSel + ' .tree-row', e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 100));
  await page.$eval(nodeSel, e => e.scrollIntoView({ block: 'center' }));
  await H.sleep(200);
  await page.evaluate(sel => { const v = document.querySelector(sel + ' .tree-val'); if (v) v.click(); }, nodeSel);
  await H.sleep(700);
  await page.evaluate(sel => { const i = document.querySelector(sel + ' input'); if (i) i.focus(); }, nodeSel);
  const inp = await page.$(nodeSel + ' input');
  r.editorOpened = !!inp;
  if (!inp) { r.rowNow = await page.$eval(nodeSel + ' .tree-row', e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 140)); return r; }
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  if (text === '') { await page.keyboard.press('Delete'); } else { await page.keyboard.type(text, { delay: o.delay || 6 }); }
  await page.keyboard.press('Enter');
  await H.sleep(1800);
  r.after = await page.evaluate(s => {
    const n = document.querySelector(s);
    return n ? n.querySelector('.tree-row').textContent.replace(/\s+/g, ' ').trim().slice(0, 160) : 'NODE GONE';
  }, nodeSel);
  return r;
}

async function safeEdit(page, path, text, opts) {
  try { return await editLeaf(page, path, text, opts); } catch (e) { return { path, crash: String(e).slice(0,160) }; }
}

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = { edits: [] };
  const resp = [];
  page.on('response', async r => {
    if (/\/field\/edit/.test(r.url())) { let b = ''; try { b = (await r.text()).slice(0, 260); } catch (e) { } resp.push(r.status() + ' ' + b.replace(/\\n/g, '')); }
  });
  const dialogs = []; page.on('dialog', async d => { dialogs.push(d.message()); await d.accept(); });
  await H.goto(page, '/', 3000);
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('#pending-tray button, #pending-tray a')].find(x => /Discard all/i.test(x.textContent));
    if (b) b.click();
  });
  await H.sleep(2500);
  out.trayStart = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  await page.evaluate(() => document.querySelector('a[hx-get="/explorer"]').click());
  await H.sleep(6000);

  // 1. __class__ protection
  await setSearch(page, '__class__');
  out.edits.push(await safeEdit(page, 'qubits.q1.__class__', 'garbage.Class'));
  out.respAfterClass = resp.slice(); resp.length = 0;

  // 2. numeric
  await setSearch(page, 'f_01');
  out.edits.push(await safeEdit(page, 'qubits.q1.f_01', '4333000123'));
  out.respNum = resp.slice(); resp.length = 0;

  // 3. leading zero on numeric
  out.edits.push(await safeEdit(page, 'qubits.q1.f_01', '007'));
  out.respLead = resp.slice(); resp.length = 0;

  // 4. text field: leading zero / empty / long
  await setSearch(page, 'grid_location');
  out.edits.push(await safeEdit(page, 'qubits.q1.grid_location', '01,0'));
  out.respGridLead = resp.slice(); resp.length = 0;
  out.edits.push(await safeEdit(page, 'qubits.q1.grid_location', ''));
  out.respGridEmpty = resp.slice(); resp.length = 0;
  out.edits.push(await safeEdit(page, 'qubits.q1.grid_location', 'L'.repeat(300), { delay: 1 }));
  out.respGridLong = resp.slice(); resp.length = 0;

  // 5. pointer node
  await setSearch(page, 'qubit_control');
  out.edits.push(await safeEdit(page, 'qubit_pairs.q1-2.qubit_control', 'q3'));
  out.respPtrPlain = resp.slice(); resp.length = 0;
  out.toastsAfterPtr = await page.evaluate(() => [...document.querySelectorAll('.toast')].map(t => t.textContent.trim()).slice(0, 3));
  out.edits.push(await safeEdit(page, 'qubit_pairs.q1-2.qubit_control', '#/qubits/q3'));
  out.respPtrOk = resp.slice(); resp.length = 0;

  out.tray = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  out.dialogs = dialogs;
  out.shot = await H.shot(page, 'le18-explorer-matrix');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
