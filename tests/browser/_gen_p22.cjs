const fs = require('fs');
const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = {};
  page.on('request', (r) => {
    if (/regenerate\/build/.test(r.url())) out.buildReq = (r.postData() || '').slice(0, 1200);
  });
  await H.goto(page, '/', 2500);
  await page.evaluate(() => { const b = document.querySelector('[aria-controls="config-subnav"]'); if (b) b.click(); });
  await H.sleep(600);
  await page.click('a[hx-get="/regenerate"]');
  await H.sleep(20000);
  out.specQdac = await page.evaluate(() => JSON.stringify((window.QuamGen && QuamGen.state ? QuamGen.state.spec : {}).qdac || null));
  out.mode = await page.evaluate(() => (window.QuamGen && QuamGen.state ? QuamGen.state.mode : null));
  // step 4
  await page.evaluate(() => { for (let i = 0; i < 3; i++) document.querySelector('#gen-next').click(); });
  await H.sleep(3000);
  out.step4 = await L.info(page);
  out.qubitCount = await page.$eval('#gen-qubit-count', e => e.value).catch(() => 'MISSING');
  out.pairRows = await page.$$eval('#gen-pair-list select', els => els.length / 2).catch(() => -1);
  out.namingHidden = await page.$eval('#gen-naming', e => e.hidden).catch(() => 'MISSING');
  out.summary = await page.$eval('#gen-qubit-summary', e => e.innerText).catch(() => '');
  out.topoCaption = await page.$eval('#gen-topo-caption', e => e.innerText).catch(() => '');
  await H.shot(page, 'gen_24_regen_step4');
  // step 5
  await page.evaluate(() => document.querySelector('#gen-next').click());
  await H.sleep(2500);
  out.step5 = await L.info(page);
  out.wiringRows = await page.$$eval('#gen-wiring-table tr', els => els.length).catch(() => -1);
  out.wiringPins = await page.$$eval('#gen-wiring-table input', els => els.slice(0, 6).map(e => e.value)).catch(() => []);
  await H.shot(page, 'gen_25_regen_step5');
  // step 6
  await page.evaluate(() => document.querySelector('#gen-wiring-next').click());
  await H.sleep(6000);
  out.step6 = await L.info(page);
  out.popSample = await page.evaluate(() => {
    const g = (s) => { const e = document.querySelector(s); return e ? e.value : 'MISSING'; };
    return {
      q1_rf: g('.gen-pop-in[data-group="qubit"][data-rid="q1"][data-field="RF_freq"]'),
      q1_lo: g('.gen-pop-in[data-group="qubit"][data-rid="q1"][data-field="LO_frequency"]'),
      q1_res_rf: g('.gen-pop-in[data-group="resonator"][data-rid="q1"][data-field="RF_freq"]'),
      q1_x180len: g('.gen-pop-in[data-group="pulses"][data-rid="q1"][data-field="x180_length"]'),
      q1_x180amp: g('.gen-pop-in[data-group="pulses"][data-rid="q1"][data-field="x180_amplitude"]'),
      pairRows: document.querySelectorAll('#gen-pop-pairs tbody tr').length,
      errCells: document.querySelectorAll('.gen-cell-err').length,
      warnCells: document.querySelectorAll('.gen-cell-warn').length,
    };
  });
  out.bandWarnings = await page.$eval('#gen-band-warnings', e => e.innerText.slice(0, 900));
  await H.shot(page, 'gen_26_regen_step6');
  out.errors = H.errors(page);
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/p22.json', JSON.stringify(out, null, 1), 'utf8');
  console.log('written');
  await browser.close();
})();
