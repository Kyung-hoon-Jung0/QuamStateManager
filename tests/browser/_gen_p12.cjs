const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = {};
  await L.walkToQubits(page);
  await L.setInput(page, '#gen-qubit-count', '4');
  await H.sleep(1200);
  await L.setInput(page, '#gen-qdac-ip', '192.168.88.244');
  await L.evClick(page, '#gen-next'); await H.sleep(1200);
  await L.evClick(page, '#gen-allocate-btn'); await H.sleep(9000);
  await L.evClick(page, '#gen-next'); await H.sleep(3000);
  out.step = (await L.info(page)).step;
  out.sections = await page.evaluate(() => {
    const ids = ['gen-pop-qubit','gen-pop-resonator','gen-pop-flux','gen-pop-pulses','gen-pop-pairs'];
    const o = {};
    ids.forEach(id => {
      const h = document.getElementById(id);
      o[id] = h ? { text: h.innerText.slice(0, 400),
        inputs: [...h.querySelectorAll('input')].slice(0, 12).map(e => ({
          cls: e.className, field: e.getAttribute('data-field') || e.name || '',
          row: e.getAttribute('data-qubit') || e.getAttribute('data-row') || e.getAttribute('data-id') || '',
          ph: e.placeholder, val: e.value })) } : 'MISSING';
    });
    return o;
  });
  console.log(JSON.stringify(out, null, 1));
  await H.shot(page, 'gen_11_populate_full');
  await browser.close();
})();
