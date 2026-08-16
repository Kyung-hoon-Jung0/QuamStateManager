const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

async function setSearch(page, t) {
  await page.evaluate(() => { const s = document.querySelector('#explorer-search'); s.focus(); s.select(); });
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.press('Backspace');
  await H.sleep(400);
  if (t) await page.keyboard.type(t, { delay: 20 });
  await H.sleep(2500);
  return page.evaluate(() => document.querySelector('#explorer-search').value);
}

async function editLeaf(page, path, text) {
  const nodeSel = '.tree-node[data-path="' + path + '"]';
  const r = { path };
  r.pre = await page.evaluate(s => {
    const n = document.querySelector(s);
    if (!n) return 'ABSENT';
    return { vis: !!n.offsetParent, row: n.querySelector('.tree-row').textContent.replace(/\s+/g, ' ').trim().slice(0, 110) };
  }, nodeSel);
  if (r.pre === 'ABSENT' || !r.pre.vis) { r.skipped = 'node not visible'; return r; }
  await page.$eval(nodeSel, e => e.scrollIntoView({ block: 'center' }));
  await H.sleep(300);
  await page.click(nodeSel + ' .tree-val');
  await H.sleep(600);
  r.editor = await page.evaluate(s => {
    const i = document.querySelector(s + ' input');
    return i ? { v: i.value, focused: document.activeElement === i } : null;
  }, nodeSel);
  if (!r.editor) return r;
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  if (text === '') await page.keyboard.press('Delete'); else await page.keyboard.type(text, { delay: 4 });
  await page.keyboard.press('Enter');
  await H.sleep(2000);
  r.post = await page.evaluate(s => {
    const n = document.querySelector(s);
    return n ? n.querySelector('.tree-row').textContent.replace(/\s+/g, ' ').trim().slice(0, 170) : 'NODE GONE';
  }, nodeSel);
  return r;
}

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = { steps: [] };
  let resp = [];
  page.on('response', async r => {
    if (/\/field\/edit/.test(r.url())) { let b = ''; try { b = (await r.text()).slice(0, 200); } catch (e) { } resp.push(r.status() + ' ' + b.split('"tray_html"')[0]); }
  });
  const dialogs = []; page.on('dialog', async d => { dialogs.push(d.message()); await d.accept(); });
  await H.goto(page, '/', 3000);
  await page.evaluate(() => document.querySelector('a[hx-get="/explorer"]').click());
  await H.sleep(6000);

  const cases = [
    ['f_01', 'qubits.q1.f_01', '4333000123'],
    ['f_01', 'qubits.q1.f_01', '007'],
    ['grid_location', 'qubits.q1.grid_location', '01,0'],
    ['grid_location', 'qubits.q1.grid_location', ''],
    ['grid_location', 'qubits.q1.grid_location', 'L'.repeat(120)],
    ['qubit_control', 'qubit_pairs.q1-2.qubit_control', 'q3'],
    ['qubit_control', 'qubit_pairs.q1-2.qubit_control', '#/qubits/q3'],
  ];
  for (const [term, path, val] of cases) {
    resp = [];
    const sv = await setSearch(page, term);
    let e;
    try { e = await editLeaf(page, path, val); } catch (err) { e = { path, crash: String(err).slice(0, 120) }; }
    e.searchVal = sv; e.typed = val.length > 40 ? '(' + val.length + ' chars)' : val;
    e.resp = resp.slice();
    e.toasts = await page.evaluate(() => [...document.querySelectorAll('.toast')].map(t => t.textContent.trim()).slice(0, 2));
    out.steps.push(e);
  }
  out.tray = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  out.dialogs = dialogs;
  out.shot = await H.shot(page, 'le20-explorer-cases');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
