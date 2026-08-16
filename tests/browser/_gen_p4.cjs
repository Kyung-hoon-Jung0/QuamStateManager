const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

async function stepInfo(page) {
  return page.evaluate(() => {
    const p = document.querySelector('.gen-panel.active');
    return {
      step: p ? p.getAttribute('data-step') : null,
      progress: (document.querySelector('#gen-progress') || {}).textContent,
      msg: (() => { const m = document.querySelector('#gen-message'); return m && !m.hidden ? m.innerText.trim().slice(0, 300) : null; })(),
    };
  });
}
async function next(page, ms) { await page.click('#gen-next'); await H.sleep(ms || 900); }

(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = { trace: [] };
  await H.goto(page, '/', 2500);
  await page.click('a[hx-get="/generate"]');
  await H.sleep(6000);

  // Step 1: pick cqt
  await page.click('.gen-env-row[data-python="D:\\\\miniconda3\\\\envs\\\\cqt\\\\python.exe"]');
  await H.sleep(2500);
  out.trace.push({ where: 'after env pick', ...(await stepInfo(page)),
    selected: await page.$$eval('.gen-env-row', els => els.filter(e => /selected|active/.test(e.className)).map(e => e.className + '|' + e.getAttribute('data-python'))) });
  await next(page, 1200);
  out.trace.push({ where: 'step2 network', ...(await stepInfo(page)) });

  // Step 2: network
  await page.type('#gen-net-host', '192.168.88.10');
  await page.type('#gen-net-cluster', 'probe_cluster');
  await next(page, 1500);
  out.trace.push({ where: 'step3 chassis', ...(await stepInfo(page)) });
  out.chassisText = await page.$eval('#gen-chassis-list', e => e.innerText.slice(0, 600)).catch(() => 'ERR');
  out.slotCount = await page.$$eval('#gen-chassis-list [data-slot]', e => e.length).catch(() => -1);
  await H.shot(page, 'gen_02_chassis');
  console.log(JSON.stringify(out, null, 1));
  console.log('ERRORS', JSON.stringify(H.errors(page)));
  await browser.close();
})();
