const fs = require('fs');
const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = { builds: [] };
  page.on('response', async (r) => {
    if (/regenerate\/build/.test(r.url())) {
      let b = ''; try { b = await r.text(); } catch (e) {}
      out.builds.push({ status: r.status(), len: b.length, head: b.slice(0, 300) });
      try { const j = JSON.parse(b); out.counters = j.result && j.result.merge ? j.result.merge : (j.merge || null);
            out.topKeys = Object.keys(j); if (j.result) out.resultKeys = Object.keys(j.result); } catch (e) {}
    }
  });
  await H.goto(page, '/', 2500);
  await page.evaluate(() => { const b = document.querySelector('[aria-controls="config-subnav"]'); if (b) b.click(); });
  await H.sleep(600);
  await page.click('a[hx-get="/regenerate"]');
  await H.sleep(20000);
  // fill the QDAC ip on step 4 to get past the gate
  await page.evaluate(() => { for (let i = 0; i < 3; i++) document.querySelector('#gen-next').click(); });
  await H.sleep(3000);
  await L.setInput(page, '#gen-qdac-ip', '192.168.88.244');
  await H.sleep(500);
  await page.evaluate(() => { for (let i = 0; i < 3; i++) { const b = document.querySelector('#gen-next'); if (b) b.click(); } });
  await H.sleep(6000);
  out.stepA = await L.info(page);
  await L.setInput(page, '#gen-output-path', 'C:\\Users\\KyunghoonJung\\AppData\\Local\\Temp\\smgen_regen_out2\\quam_state');
  await H.sleep(800);
  await L.evClick(page, '#gen-next'); await H.sleep(6000);
  out.stepB = await L.info(page);
  await L.evClick(page, '#gen-next');
  for (let i = 0; i < 30; i++) {
    await H.sleep(10000);
    const done = await page.$eval('#gen-build-result', e => !e.hidden && e.innerText.length > 5).catch(() => false);
    if (done) break;
  }
  out.panel = await page.$eval('#gen-build-result', e => ({ hidden: e.hidden, txt: e.innerText.slice(0, 3000) })).catch(() => 'ERR');
  await H.shot(page, 'gen_28_regen_build_result');
  out.errors = H.errors(page);
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/p24.json', JSON.stringify(out, null, 1), 'utf8');
  console.log('written');
  await browser.close();
})();
