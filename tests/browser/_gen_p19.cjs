const fs = require('fs');
const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
const sel = (g, r, f) => `.gen-pop-in[data-group="${g}"][data-rid="${r}"][data-field="${f}"]`;
async function typeCell(page, s, txt) {
  const ok = await page.evaluate((q) => { const e = document.querySelector(q); if (!e) return false; e.scrollIntoView({ block: 'center' }); e.focus(); e.value = ''; return true; }, s);
  if (!ok) return 'MISSING';
  await page.type(s, txt, { delay: 15 }); await H.sleep(350);
  await page.evaluate((q) => { document.querySelector(q).blur(); }, s); await H.sleep(400);
  return 'ok';
}
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = {};
  await L.walkToQubits(page);
  await L.setInput(page, '#gen-qubit-count', '4'); await H.sleep(1200);
  await L.setInput(page, '#gen-qdac-ip', '192.168.88.244');
  await L.evClick(page, '#gen-next'); await H.sleep(1200);
  await L.evClick(page, '#gen-allocate-btn'); await H.sleep(9000);
  await L.evClick(page, '#gen-next'); await H.sleep(2500);
  for (const [q, f] of [['q1','5.1'],['q2','5.2'],['q3','5.3'],['q4','5.4']])
    await typeCell(page, sel('qubit', q, 'RF_freq'), f);
  for (const [q, f] of [['q1','7.1'],['q2','7.15'],['q3','7.2'],['q4','7.25']])
    await typeCell(page, sel('resonator', q, 'RF_freq'), f);
  await H.sleep(1000);
  // step 7
  await L.evClick(page, '#gen-next'); await H.sleep(1500);
  out.step7 = await L.info(page);
  out.scriptsPathDefault = await page.$eval('#gen-scripts-path', e => ({ v: e.value, ph: e.placeholder }));
  const OUTDIR = 'C:\\Users\\KyunghoonJung\\AppData\\Local\\Temp\\smgen_probe_out\\quam_state';
  await L.setInput(page, '#gen-output-path', OUTDIR);
  await H.sleep(900);
  out.scriptsPathFollowed = await page.$eval('#gen-scripts-path', e => e.value);
  await L.evClick(page, '#gen-next'); await H.sleep(4000);
  out.step8 = await L.info(page);
  out.review = await page.$eval('#gen-review', e => e.innerText.slice(0, 2500));
  await H.shot(page, 'gen_21_review');
  out.buildButtons = await page.$$eval('.gen-panel[data-step="8"] button', els => els.map(e => ({ id: e.id, txt: e.innerText.trim(), dis: e.disabled })));
  out.errors = H.errors(page);
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/p19.json', JSON.stringify(out, null, 1), 'utf8');
  console.log('written');
  await browser.close();
})();
