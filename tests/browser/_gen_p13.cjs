const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
const sel = (g, r, f) => `.gen-pop-in[data-group="${g}"][data-rid="${r}"][data-field="${f}"]`;

async function cellState(page, s) {
  return page.evaluate((q) => {
    const e = document.querySelector(q);
    if (!e) return 'MISSING ' + q;
    const td = e.parentNode;
    const flag = td && td.querySelector('.gen-cell-flag');
    return {
      value: e.value, cls: e.className, title: e.title,
      borderColor: getComputedStyle(e).borderColor,
      bg: getComputedStyle(e).backgroundColor,
      flag: flag ? { text: flag.textContent, cls: flag.className, title: flag.title } : null,
    };
  }, s);
}
async function typeCell(page, s, txt) {
  await page.evaluate((q) => { const e = document.querySelector(q); e.scrollIntoView({ block: 'center' }); e.focus(); e.value = ''; }, s);
  await page.type(s, txt, { delay: 25 });
  await H.sleep(900);
  const st = await cellState(page, s);
  await page.evaluate((q) => { document.querySelector(q).blur(); }, s);
  await H.sleep(600);
  return { typed: st, afterBlur: await cellState(page, s) };
}

(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = {};
  await L.walkToQubits(page);
  await L.setInput(page, '#gen-qubit-count', '4');
  await H.sleep(1200);
  await L.setInput(page, '#gen-qdac-ip', '192.168.88.244');
  await L.evClick(page, '#gen-next'); await H.sleep(1200);
  await L.evClick(page, '#gen-allocate-btn'); await H.sleep(9000);
  await L.evClick(page, '#gen-next'); await H.sleep(3000);

  out.step = (await L.info(page)).step;
  // baseline good value
  out.c1_good_rf_5GHz = await typeCell(page, sel('qubit', 'q1', 'RF_freq'), '5.1');
  // out of hardware reach
  out.c2_rf_25GHz = await typeCell(page, sel('qubit', 'q2', 'RF_freq'), '25');
  // absurdly low
  out.c3_rf_0p05GHz = await typeCell(page, sel('qubit', 'q3', 'RF_freq'), '0.05');
  // readout amplitude negative and >1
  out.resFields = await page.$$eval('#gen-pop-resonator .gen-pop-in[data-rid="q1"]', els => els.map(e => e.dataset.field + '|' + (e.dataset.dim || '')));
  out.pulseFields = await page.$$eval('#gen-pop-pulses .gen-pop-in[data-rid]', els => els.slice(0,20).map(e => e.dataset.rid + '/' + e.dataset.field + '|' + (e.dataset.dim || '')));
  out.pairFields = await page.$$eval('#gen-pop-pairs .gen-pop-in[data-rid]', els => els.slice(0,20).map(e => e.dataset.rid + '/' + e.dataset.field + '|' + (e.dataset.dim || '')));
  out.fluxFields = await page.$$eval('#gen-pop-flux .gen-pop-in[data-rid]', els => els.slice(0,12).map(e => e.dataset.rid + '/' + e.dataset.field + '|' + (e.dataset.dim || '')));
  console.log(JSON.stringify(out, null, 1));
  await H.shot(page, 'gen_12_validation');
  console.log('ERRORS', JSON.stringify(H.errors(page)));
  await browser.close();
})();
