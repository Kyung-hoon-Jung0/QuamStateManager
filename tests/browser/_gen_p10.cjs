const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = { net: [] };
  page.on('response', async (r) => {
    if (/generate\/allocate/.test(r.url())) {
      let b = ''; try { b = (await r.text()).slice(0, 400); } catch (e) {}
      out.net.push({ status: r.status(), body: b });
    }
  });
  await L.walkToQubits(page);
  await L.setInput(page, '#gen-qubit-count', '4');
  await H.sleep(1200);
  // FILL the QDAC IP even though no qubit uses a QDAC
  await L.setInput(page, '#gen-qdac-ip', '192.168.88.244');
  await H.sleep(400);
  await L.evClick(page, '#gen-next'); await H.sleep(1200);
  await L.evClick(page, '#gen-allocate-btn');
  await H.sleep(10000);
  out.allocStatus = await page.$eval('#gen-allocate-status', e => e.innerText);
  out.table = await page.$eval('#gen-wiring-table', e => e.innerText.slice(0, 800));
  out.diagramHasSvg = await page.$eval('#gen-wiring-diagram', e => !!e.querySelector('svg'));
  out.issues = await page.$eval('#gen-wiring-issues', e => e.innerText.slice(0, 300));
  await H.shot(page, 'gen_10_alloc_ok');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
