const fs = require('fs');
const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
async function navRect(page) {
  return page.evaluate(() => {
    const g = (s) => { const e = document.querySelector(s); if (!e) return 'MISSING';
      const r = e.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), disp: getComputedStyle(e).display, vis: getComputedStyle(e).visibility }; };
    return { next: g('#gen-next'), back: g('#gen-back'), nav: g('.gen-nav'),
      nextTop: g('#gen-next-top'), step: (document.querySelector('.gen-panel.active') || {}).dataset ? document.querySelector('.gen-panel.active').dataset.step : null };
  });
}
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = {};
  await L.walkToQubits(page);
  await L.setInput(page, '#gen-qubit-count', '4'); await H.sleep(1000);
  out.wide_step4 = await navRect(page);
  await L.setInput(page, '#gen-qdac-ip', '192.168.88.244');
  await L.evClick(page, '#gen-next'); await H.sleep(1200);
  out.wide_step5 = await navRect(page);
  await L.evClick(page, '#gen-allocate-btn'); await H.sleep(9000);
  await L.evClick(page, '#gen-next'); await H.sleep(2500);
  out.wide_step6 = await navRect(page);
  await page.setViewport({ width: 800, height: 700 }); await H.sleep(1200);
  out.narrow_step6 = await navRect(page);
  out.overflow = await page.evaluate(() => {
    const w = document.documentElement.clientWidth;
    const bad = [];
    document.querySelectorAll('#generate-root *').forEach(e => {
      const r = e.getBoundingClientRect();
      if (r.width > 0 && r.right > w + 2) bad.push(e.tagName + '.' + (e.className || '').toString().slice(0, 40) + ' right=' + Math.round(r.right));
    });
    return { clientW: w, bodyScrollW: document.body.scrollWidth, first: bad.slice(0, 8), n: bad.length };
  });
  await H.shot(page, 'gen_19_narrow_step6');
  await page.setViewport({ width: 1600, height: 1000 }); await H.sleep(800);
  // env selection restore check
  await H.goto(page, '/', 2500);
  await page.click('a[hx-get="/generate"]'); await H.sleep(9000);
  out.reload = await page.evaluate(() => ({
    step: document.querySelector('.gen-panel.active').dataset.step,
    selectedRows: [...document.querySelectorAll('.gen-env-row')].filter(e => /selected/.test(e.className)).map(e => e.dataset.python),
    rowCount: document.querySelectorAll('.gen-env-row').length,
  }));
  const envs = await page.evaluate(async () => (await (await fetch('/generate/envs')).json()).selected);
  out.serverSelected = envs;
  // go back to step 1 and look
  await page.evaluate(() => { for (let i = 0; i < 6; i++) { const b = document.querySelector('#gen-back'); if (b && !b.disabled) b.click(); } });
  await H.sleep(1500);
  out.backToStep1 = await page.evaluate(() => ({
    step: document.querySelector('.gen-panel.active').dataset.step,
    selectedRows: [...document.querySelectorAll('.gen-env-row')].filter(e => /selected/.test(e.className)).map(e => e.dataset.python),
    nextDisabled: document.querySelector('#gen-next').disabled,
  }));
  await H.shot(page, 'gen_20_reload_step1');
  out.errors = H.errors(page);
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/p18.json', JSON.stringify(out, null, 1), 'utf8');
  console.log('written');
  await browser.close();
})();
