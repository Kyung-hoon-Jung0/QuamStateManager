const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = { net: [] };
  page.on('response', async (r) => {
    if (/generate\//.test(r.url())) {
      let body = '';
      try { body = (await r.text()).slice(0, 900); } catch (e) { body = 'ERR'; }
      out.net.push({ url: r.url().replace(/^http:\/\/[^/]+/, ''), status: r.status(), body });
    }
  });
  page.on('request', (r) => {
    if (/generate\/allocate/.test(r.url())) out.reqBody = (r.postData() || '').slice(0, 2000);
  });
  out.atStep4 = await L.walkToQubits(page);
  await L.setInput(page, '#gen-qubit-count', '4');
  await H.sleep(1200);
  await L.evClick(page, '#gen-next'); await H.sleep(1200);
  await L.evClick(page, '#gen-allocate-btn');
  await H.sleep(9000);
  out.allocStatus = await page.$eval('#gen-allocate-status', e => e.innerText);
  out.allocStatusHTML = await page.$eval('#gen-allocate-status', e => e.innerHTML);
  out.msg = await page.evaluate(() => { const m = document.querySelector('#gen-message'); return m ? { hidden: m.hidden, txt: m.innerText.trim().slice(0,400) } : null; });
  await H.shot(page, 'gen_09_alloc_fail');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
