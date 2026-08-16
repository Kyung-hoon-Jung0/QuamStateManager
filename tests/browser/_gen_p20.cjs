const fs = require('fs');
const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
const sel = (g, r, f) => `.gen-pop-in[data-group="${g}"][data-rid="${r}"][data-field="${f}"]`;
async function typeCell(page, s, txt) {
  const ok = await page.evaluate((q) => { const e = document.querySelector(q); if (!e) return false; e.scrollIntoView({ block: 'center' }); e.focus(); e.value = ''; return true; }, s);
  if (!ok) return 'MISSING';
  await page.type(s, txt, { delay: 15 }); await H.sleep(300);
  await page.evaluate((q) => { document.querySelector(q).blur(); }, s); await H.sleep(350);
  return 'ok';
}
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = { net: [] };
  page.on('response', async (r) => {
    if (/generate\/build/.test(r.url())) { let b=''; try { b = (await r.text()).slice(0, 2500);} catch(e){} out.net.push({ url: r.url(), status: r.status(), body: b }); }
  });
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
  await L.evClick(page, '#gen-next'); await H.sleep(1500);
  await L.setInput(page, '#gen-output-path', 'C:\\Users\\KyunghoonJung\\AppData\\Local\\Temp\\smgen_probe_out\\quam_state');
  await H.sleep(600);
  await L.evClick(page, '#gen-next'); await H.sleep(4000);
  out.navOnStep8 = await page.evaluate(() => ({
    next: document.querySelector('#gen-next').innerText.trim(),
    nextTop: document.querySelector('#gen-next-top').innerText.trim(),
    cls: document.querySelector('#gen-next').className,
  }));
  // press it -> the real build
  await L.evClick(page, '#gen-next');
  await H.sleep(90000);
  out.buildPanel = await page.$eval('#gen-build-result', e => ({ hidden: e.hidden, txt: e.innerText.slice(0, 2000) })).catch(e => 'ERR');
  out.msg = (await L.info(page)).msg;
  await H.shot(page, 'gen_22_build_result');
  out.errors = H.errors(page);
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/p20.json', JSON.stringify(out, null, 1), 'utf8');
  console.log('written');
  await browser.close();
})();
