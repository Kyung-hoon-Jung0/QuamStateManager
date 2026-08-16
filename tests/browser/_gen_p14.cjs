const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
const sel = (g, r, f) => `.gen-pop-in[data-group="${g}"][data-rid="${r}"][data-field="${f}"]`;
async function cellState(page, s) {
  return page.evaluate((q) => {
    const e = document.querySelector(q);
    if (!e) return 'MISSING ' + q;
    const td = e.parentNode, flag = td && td.querySelector('.gen-cell-flag');
    return { value: e.value, cls: e.className, title: e.title,
      border: getComputedStyle(e).borderColor,
      flag: flag ? flag.className + ' :: ' + flag.title : null };
  }, s);
}
async function typeCell(page, s, txt) {
  const ok = await page.evaluate((q) => { const e = document.querySelector(q); if (!e) return false; e.scrollIntoView({ block: 'center' }); e.focus(); e.value = ''; return true; }, s);
  if (!ok) return 'MISSING ' + s;
  await page.type(s, txt, { delay: 25 });
  await H.sleep(900);
  const typed = await cellState(page, s);
  await page.evaluate((q) => { document.querySelector(q).blur(); }, s);
  await H.sleep(700);
  return { typed, afterBlur: await cellState(page, s) };
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

  // reasonable frequencies first so LO solving has something to work with
  for (const [q, f] of [['q1','5.1'],['q2','5.2'],['q3','5.3'],['q4','5.4']])
    await typeCell(page, sel('qubit', q, 'RF_freq'), f);
  for (const [q, f] of [['q1','7.1'],['q2','7.15'],['q3','7.2'],['q4','7.25']])
    await typeCell(page, sel('resonator', q, 'RF_freq'), f);
  await H.sleep(1200);
  out.loAfterSolve = await page.$$eval('.gen-pop-in[data-field="LO_frequency"]',
    els => els.map(e => e.dataset.group + '/' + e.dataset.rid + '=' + e.value));
  out.bandWarnings = await page.$eval('#gen-band-warnings', e => e.innerText.slice(0, 500));

  out.amp_negative   = await typeCell(page, sel('resonator', 'q1', 'readout_amplitude'), '-0.5');
  out.amp_over_one   = await typeCell(page, sel('resonator', 'q2', 'readout_amplitude'), '1.5');
  out.amp_ok         = await typeCell(page, sel('resonator', 'q3', 'readout_amplitude'), '0.05');
  out.x180_over_one  = await typeCell(page, sel('pulses', 'q1', 'x180_amplitude'), '2.5');
  out.x180_negative  = await typeCell(page, sel('pulses', 'q2', 'x180_amplitude'), '-0.3');
  // IF window: hand-set an LO 1.5 GHz away from the qubit RF
  out.lo_far         = await typeCell(page, sel('qubit', 'q1', 'LO_frequency'), '3.5');
  // demod hole: resonator LO == resonator RF (IF = 0)
  out.demod_hole     = await typeCell(page, sel('resonator', 'q1', 'LO_frequency'), '7.1');
  out.len_negative   = await typeCell(page, sel('pulses', 'q3', 'x180_length'), '-40');
  out.fsp_absurd     = await typeCell(page, sel('qubit', 'q3', 'full_scale_power_dbm'), '99');
  await H.shot(page, 'gen_13_amp_validation');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
