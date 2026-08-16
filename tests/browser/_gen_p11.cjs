const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = {};
  page.on('response', async (r) => {
    if (/generate\/allocate/.test(r.url())) {
      try { out.alloc = JSON.parse(await r.text()); } catch (e) {}
    }
  });
  await L.walkToQubits(page);
  await L.setInput(page, '#gen-qubit-count', '4');
  await H.sleep(1200);
  await L.setInput(page, '#gen-qdac-ip', '192.168.88.244');
  await L.evClick(page, '#gen-next'); await H.sleep(1200);
  await L.evClick(page, '#gen-allocate-btn');
  await H.sleep(10000);
  out.allocKeys = out.alloc ? Object.keys(out.alloc.result.allocation) : null;
  out.couplerEntry = out.alloc ? JSON.stringify(out.alloc.result.allocation['q1-q2'] || null).slice(0,400) : null;
  out.warnings = out.alloc ? JSON.stringify(out.alloc.result.warnings || out.alloc.result.issues || null).slice(0,600) : null;
  out.resultKeys = out.alloc ? Object.keys(out.alloc.result) : null;
  out.issues = await page.$eval('#gen-wiring-issues', e => e.innerText.slice(0, 400));
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
