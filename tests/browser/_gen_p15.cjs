const fs = require('fs');
const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
const sel = (g, r, f) => `.gen-pop-in[data-group="${g}"][data-rid="${r}"][data-field="${f}"]`;
async function typeCell(page, s, txt) {
  const ok = await page.evaluate((q) => { const e = document.querySelector(q); if (!e) return false; e.scrollIntoView({ block: 'center' }); e.focus(); e.value = ''; return true; }, s);
  if (!ok) return 'MISSING';
  await page.type(s, txt, { delay: 20 });
  await H.sleep(500);
  await page.evaluate((q) => { document.querySelector(q).blur(); }, s);
  await H.sleep(500);
  return 'ok';
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
  out.bandWarnings = await page.$eval('#gen-band-warnings', e => e.innerText);
  out.los = await page.$$eval('.gen-pop-in[data-field="LO_frequency"]',
    els => els.map(e => e.dataset.group + '/' + e.dataset.rid + '=' + e.value));
  out.loMapVisible = await page.evaluate(() => !document.getElementById('gen-lo-map').hidden);
  out.loMap = await page.$eval('#gen-lo-map', e => e.innerText).catch(() => '');
  await H.shot(page, 'gen_14_band_warnings');

  // POWER MODE toggle
  out.powerModeControls = await page.$eval('#gen-pop-units', e => e.innerHTML.slice(0, 1500));
  const flipped = await page.evaluate(() => {
    const inputs = [...document.querySelectorAll('#gen-pop-units input[type=radio]')];
    const abs = inputs.find(i => /absolute/i.test(i.value) || /absolute/i.test((i.parentNode.innerText || '')));
    if (!abs) return 'NO ABS RADIO: ' + inputs.map(i => i.value + '/' + i.name).join(',');
    abs.click(); return 'clicked ' + abs.value;
  });
  out.powerFlip = flipped;
  await H.sleep(1500);
  out.afterFlip = await page.evaluate(() => {
    const a = document.querySelector('.gen-pop-in[data-group="pulses"][data-rid="q1"][data-field="x180_amplitude"]');
    const f = document.querySelector('.gen-pop-in[data-group="qubit"][data-rid="q1"][data-field="full_scale_power_dbm"]');
    return { ampTitle: a ? a.title : null, ampVal: a ? a.value : null,
             fspDisabled: f ? f.disabled : null, fspVal: f ? f.value : null,
             header: document.querySelector('#gen-pop-pulses').innerText.split('\n').slice(0,10).join(' | ') };
  });
  await H.shot(page, 'gen_15_power_mode');
  out.errors = H.errors(page);
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/p15.json', JSON.stringify(out, null, 1), 'utf8');
  console.log('written');
  await browser.close();
})();
