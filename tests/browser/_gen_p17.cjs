const fs = require('fs');
const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
const sel = (g, r, f) => `.gen-pop-in[data-group="${g}"][data-rid="${r}"][data-field="${f}"]`;
async function typeCell(page, s, txt) {
  const ok = await page.evaluate((q) => { const e = document.querySelector(q); if (!e) return false; e.scrollIntoView({ block: 'center' }); e.focus(); e.value = ''; return true; }, s);
  if (!ok) return 'MISSING';
  await page.type(s, txt, { delay: 20 }); await H.sleep(400);
  await page.evaluate((q) => { document.querySelector(q).blur(); }, s); await H.sleep(450);
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
  await L.evClick(page, '#gen-next'); await H.sleep(2500);
  await typeCell(page, sel('qubit', 'q1', 'RF_freq'), '5.1');
  await typeCell(page, sel('pulses', 'q1', 'x180_length'), '48');
  await H.sleep(800);

  // BACK to step 4 and forward again — do the typed values survive?
  await L.evClick(page, '#gen-back'); await H.sleep(900);
  await L.evClick(page, '#gen-back'); await H.sleep(900);
  out.afterTwoBack = await L.info(page);
  out.qubitCountStill = await page.$eval('#gen-qubit-count', e => e.value);
  await L.evClick(page, '#gen-next'); await H.sleep(1200);
  out.step5Table = await page.$eval('#gen-wiring-table', e => e.innerText.slice(0, 200));
  await L.evClick(page, '#gen-next'); await H.sleep(2500);
  out.afterForward = {
    step: (await L.info(page)).step,
    rf: await page.$eval(sel('qubit','q1','RF_freq'), e => e.value).catch(() => 'MISSING'),
    len: await page.$eval(sel('pulses','q1','x180_length'), e => e.value).catch(() => 'MISSING'),
  };

  // RELOAD mid-wizard (hard nav to '/', then click Generate Config again)
  await H.goto(page, '/', 2500);
  await page.click('a[hx-get="/generate"]'); await H.sleep(6000);
  out.afterReload = {
    step: (await L.info(page)).step,
    envSelected: await page.$$eval('.gen-env-row.selected', els => els.map(e => e.getAttribute('data-python'))),
    qubitCount: await page.$eval('#gen-qubit-count', e => e.value).catch(() => 'MISSING'),
    host: await page.$eval('#gen-net-host', e => e.value).catch(() => 'MISSING'),
    msg: (await L.info(page)).msg,
    resumeBanner: await page.evaluate(() => {
      const t = document.body.innerText;
      const m = t.match(/[^\n]*(draft|resume|restor|previous)[^\n]*/i);
      return m ? m[0].slice(0, 200) : null; }),
  };
  await H.shot(page, 'gen_17_after_reload');

  // RESIZE
  await page.setViewport({ width: 800, height: 700 });
  await H.sleep(1200);
  out.narrow = await page.evaluate(() => ({
    bodyScrollW: document.body.scrollWidth, inner: window.innerWidth,
    stepsVisible: !!document.querySelector('#gen-steps') && getComputedStyle(document.querySelector('#gen-steps')).display,
    nextVisible: (() => { const b = document.querySelector('#gen-next'); const r = b.getBoundingClientRect(); return { w: r.width, h: r.height, x: r.x }; })(),
  }));
  await H.shot(page, 'gen_18_narrow');
  out.errors = H.errors(page);
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/p17.json', JSON.stringify(out, null, 1), 'utf8');
  console.log('written');
  await browser.close();
})();
