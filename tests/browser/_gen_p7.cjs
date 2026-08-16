const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
const names = (page) => page.$$eval('#gen-qubit-name-list input', els => els.map(e => e.value));
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = {};
  out.atStep4 = await L.walkToQubits(page);
  await L.setInput(page, '#gen-qubit-count', '4');
  await H.sleep(1200);
  out.names0 = await names(page);

  // zero_based scheme
  await L.setInput(page, '#gen-naming-preset', 'zero_based');
  await L.evClick(page, '#gen-naming-apply'); await H.sleep(700);
  out.namesZero = await names(page);
  out.noteZero = await page.$eval('#gen-naming-note', e => e.innerText);

  // custom prefix
  await L.setInput(page, '#gen-naming-preset', 'custom'); await H.sleep(300);
  out.customFieldsVisible = await page.evaluate(() => ({
    prefix: !document.getElementById('gen-naming-prefix-field').hidden,
    start: !document.getElementById('gen-naming-start-field').hidden }));
  await L.setInput(page, '#gen-naming-prefix', 'qx');
  await L.setInput(page, '#gen-naming-start', '7');
  await L.evClick(page, '#gen-naming-apply'); await H.sleep(700);
  out.namesCustom = await names(page);
  out.pairsAfterCustom = await page.$$eval('#gen-pair-list select', els => els.map(e => e.value)).catch(() => 'ERR');

  // per-qubit rename of the first
  await page.evaluate(() => {
    const i = document.querySelector('#gen-qubit-name-list input');
    i.focus(); i.value = 'qALPHA';
    i.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await H.sleep(800);
  out.namesRenamed = await names(page);
  out.pairsAfterRename = await page.$$eval('#gen-pair-list select', els => els.map(e => e.value)).catch(() => 'ERR');
  out.msgAfterRename = await page.evaluate(() => { const m = document.querySelector('#gen-message'); return m && !m.hidden ? m.innerText.trim() : null; });

  // invalid rename
  await page.evaluate(() => {
    const i = document.querySelector('#gen-qubit-name-list input');
    i.focus(); i.value = 'bad name!';
    i.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await H.sleep(600);
  out.namesAfterInvalid = await names(page);
  out.msgAfterInvalid = await page.evaluate(() => { const m = document.querySelector('#gen-message'); return m && !m.hidden ? m.innerText.trim() : null; });
  await H.shot(page, 'gen_06_rename');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
