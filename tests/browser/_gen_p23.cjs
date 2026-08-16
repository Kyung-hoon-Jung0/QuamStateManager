const fs = require('fs');
const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = { builds: [] };
  page.on('response', async (r) => {
    if (/regenerate\/build/.test(r.url())) {
      let b = ''; try { b = (await r.text()); } catch (e) {}
      out.builds.push({ status: r.status(), body: b.slice(0, 1200), len: b.length });
    }
  });
  await H.goto(page, '/', 2500);
  await page.evaluate(() => { const b = document.querySelector('[aria-controls="config-subnav"]'); if (b) b.click(); });
  await H.sleep(600);
  await page.click('a[hx-get="/regenerate"]');
  await H.sleep(20000);
  // straight to step 7
  await page.evaluate(() => { for (let i = 0; i < 6; i++) { const b = document.querySelector('#gen-next'); if (b) b.click(); } });
  await H.sleep(6000);
  out.stepA = await L.info(page);
  await L.setInput(page, '#gen-output-path', 'C:\\Users\\KyunghoonJung\\AppData\\Local\\Temp\\smgen_regen_out\\quam_state');
  await H.sleep(800);
  await L.evClick(page, '#gen-next'); await H.sleep(6000);
  out.stepB = await L.info(page);
  out.review = await page.$eval('#gen-review', e => e.innerText.slice(0, 1200)).catch(() => 'ERR');
  out.genBtn = await page.$eval('#gen-next', e => e.innerText.trim());
  // press Generate — no QDAC IP set
  await L.evClick(page, '#gen-next');
  await H.sleep(15000);
  out.afterFirstPress = { msg: (await L.info(page)).msg,
    panel: await page.$eval('#gen-build-result', e => ({ hidden: e.hidden, txt: e.innerText.slice(0, 600) })).catch(() => 'ERR') };
  await H.shot(page, 'gen_27_regen_build_attempt');
  out.errors = H.errors(page);
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/p23.json', JSON.stringify(out, null, 1), 'utf8');
  console.log('written');
  await browser.close();
})();
