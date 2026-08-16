const fs = require('fs');
const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
const sel = (g, r, f) => `.gen-pop-in[data-group="${g}"][data-rid="${r}"][data-field="${f}"]`;
async function typeCell(page, s, txt) {
  const ok = await page.evaluate((q) => { const e = document.querySelector(q); if (!e) return false; e.scrollIntoView({ block: 'center' }); e.focus(); e.value = ''; return true; }, s);
  if (!ok) return 'MISSING';
  await page.type(s, txt, { delay: 20 });
  await H.sleep(400);
  await page.evaluate((q) => { document.querySelector(q).blur(); }, s);
  await H.sleep(450);
  return 'ok';
}
async function cell(page, s) {
  return page.evaluate((q) => { const e = document.querySelector(q); return e ? { v: e.value, cls: e.className, t: e.title, dis: e.disabled } : 'MISSING'; }, s);
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
  for (const [q, f] of [['q1','5.1'],['q2','5.2'],['q3','5.3'],['q4','5.4']])
    await typeCell(page, sel('qubit', q, 'RF_freq'), f);
  for (const [q, f] of [['q1','7.1'],['q2','7.15'],['q3','7.2'],['q4','7.25']])
    await typeCell(page, sel('resonator', q, 'RF_freq'), f);
  await H.sleep(1500);
  // Was the AUTO-written LO (6.175 on q1, 1.075 GHz from its 5.1 RF) flagged?
  out.autoLoCell_q1 = await cell(page, sel('qubit', 'q1', 'LO_frequency'));
  out.rfCell_q1 = await cell(page, sel('qubit', 'q1', 'RF_freq'));

  // POWER MODE: it is a <select>, flip to absolute
  out.flip = await page.evaluate(() => {
    const s = document.querySelector('.gen-pop-powermode select');
    if (!s) return 'no select';
    s.value = 'absolute';
    s.dispatchEvent(new Event('change', { bubbles: true }));
    return 'ok';
  });
  await H.sleep(2000);
  out.afterAbs = {
    pulseHeader: await page.$eval('#gen-pop-pulses', e => e.innerText.split('\n').slice(0, 8).join(' | ')),
    x180amp: await cell(page, sel('pulses', 'q1', 'x180_amplitude')),
    fsp: await cell(page, sel('qubit', 'q1', 'full_scale_power_dbm')),
  };
  await typeCell(page, sel('pulses', 'q1', 'x180_amplitude'), '-20');
  await H.sleep(1200);
  out.afterTypeDbm = {
    x180amp: await cell(page, sel('pulses', 'q1', 'x180_amplitude')),
    fsp: await cell(page, sel('qubit', 'q1', 'full_scale_power_dbm')),
  };
  await H.shot(page, 'gen_16_absolute_power');
  // flip back
  await page.evaluate(() => { const s = document.querySelector('.gen-pop-powermode select'); s.value = 'manual'; s.dispatchEvent(new Event('change', { bubbles: true })); });
  await H.sleep(1500);
  out.afterBack = {
    x180amp: await cell(page, sel('pulses', 'q1', 'x180_amplitude')),
    fsp: await cell(page, sel('qubit', 'q1', 'full_scale_power_dbm')),
  };
  out.errors = H.errors(page);
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/p16.json', JSON.stringify(out, null, 1), 'utf8');
  console.log('written');
  await browser.close();
})();
