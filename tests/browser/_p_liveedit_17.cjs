const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const P = 'ports.analog_outputs.con1.4.1.output_mode';
const VAL = '.tree-node[data-path="' + P + '"] .tree-val';

const nodeState = (page) => page.evaluate(p => {
  const n = document.querySelector('.tree-node[data-path="' + p + '"]');
  if (!n) return 'ABSENT';
  const inp = n.querySelector('input, select, textarea');
  return {
    visible: !!n.offsetParent,
    text: n.querySelector('.tree-row').textContent.replace(/\s+/g, ' ').trim().slice(0, 120),
    editor: inp ? { tag: inp.tagName, type: inp.type, value: inp.value, list: inp.getAttribute('list') || '' } : null,
    datalistOptions: (() => {
      const l = inp && inp.getAttribute('list');
      if (!l) return null;
      const dl = document.getElementById(l);
      return dl ? [...dl.options].map(o => o.value) : 'MISSING';
    })(),
  };
}, P);

async function setSearch(page, t) {
  await page.click('#explorer-search');
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  if (t) await page.keyboard.type(t, { delay: 15 }); else await page.keyboard.press('Backspace');
  await H.sleep(2200);
}

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  const resp = [];
  page.on('response', async r => {
    if (/\/field\/(edit|peek)/.test(r.url())) { let b = ''; try { b = (await r.text()).slice(0, 220); } catch (e) { } resp.push(r.status() + ' ' + r.url().replace('http://127.0.0.1:8822', '') + ' ' + b); }
  });
  const dialogs = []; page.on('dialog', async d => { dialogs.push(d.message()); await d.accept(); });
  await H.goto(page, '/', 3000);
  await page.evaluate(() => document.querySelector('a[hx-get="/explorer"]').click());
  await H.sleep(6000);

  await setSearch(page, 'output_mode');
  out.beforeClick = await nodeState(page);
  await page.click(VAL);
  await H.sleep(600);
  out.afterClick = await nodeState(page);
  out.shotEditor = await H.shot(page, 'le17-editor-open');

  // type direct + Enter
  const inpSel = '.tree-node[data-path="' + P + '"] input';
  if (await page.$(inpSel)) {
    await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
    await page.keyboard.type('direct', { delay: 30 });
    await page.keyboard.press('Enter');
    await H.sleep(2000);
  }
  out.afterCommit = await nodeState(page);
  out.resp1 = resp.slice(); resp.length = 0;
  out.tray1 = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));
  out.shotCommit = await H.shot(page, 'le17-after-commit');

  // now filter by VALUE 'amplified' and try to edit the still-amplified sibling 4.2
  await setSearch(page, 'amplified');
  const P2 = 'ports.analog_outputs.con1.4.2.output_mode';
  out.sib = await page.evaluate(p => {
    const n = document.querySelector('.tree-node[data-path="' + p + '"]');
    return n ? { vis: !!n.offsetParent, txt: n.textContent.replace(/\s+/g, ' ').trim().slice(0, 80) } : 'ABSENT';
  }, P2);
  const V2 = '.tree-node[data-path="' + P2 + '"] .tree-val';
  if (await page.$(V2)) {
    await page.click(V2);
    await H.sleep(500);
    out.sibEditorOpen = await page.evaluate(p => {
      const n = document.querySelector('.tree-node[data-path="' + p + '"]');
      const i = n && n.querySelector('input');
      return i ? { v: i.value, focused: document.activeElement === i } : 'NO-INPUT';
    }, P2);
    await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
    await page.keyboard.type('direct', { delay: 40 });
    out.sibMidType = await page.evaluate(p => {
      const n = document.querySelector('.tree-node[data-path="' + p + '"]');
      const i = n && n.querySelector('input');
      return { nodeVisible: n ? !!n.offsetParent : 'GONE', inputValue: i ? i.value : 'NO-INPUT', focused: i ? document.activeElement === i : false };
    }, P2);
    await page.keyboard.press('Enter');
    await H.sleep(2000);
    out.sibAfter = await page.evaluate(p => {
      const n = document.querySelector('.tree-node[data-path="' + p + '"]');
      return n ? { vis: !!n.offsetParent, txt: n.textContent.replace(/\s+/g, ' ').trim().slice(0, 80) } : 'ABSENT';
    }, P2);
    out.resp2 = resp.slice();
    out.shotSib = await H.shot(page, 'le17-sibling-filtered');
  }
  out.dialogs = dialogs;
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
