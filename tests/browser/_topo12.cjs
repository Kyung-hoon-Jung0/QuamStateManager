const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(8000);
  // Set T1 on 5 of 20 qubits in the WORKING COPY only (no apply-to-live).
  const res = await page.evaluate(async () => {
    const vals = {q1: 2.5e-5, q5: 4.0e-5, q10: 6.0e-5, q15: 1.5e-5, q20: 8.0e-5};
    const out = [];
    for (const [q, v] of Object.entries(vals)) {
      const fd = new URLSearchParams({dot_path: 'qubits.'+q+'.T1', value: String(v)});
      const r = await fetch('/field/edit', {method:'POST', body: fd, headers:{'Content-Type':'application/x-www-form-urlencoded'}});
      let j=null; try{j=await r.json();}catch(e){j={raw:await r.text()};}
      out.push({q, status:r.status, ok:j&&j.ok, err:j&&j.error});
    }
    return out;
  });
  console.log('EDITS:', JSON.stringify(res));
  // re-render topology
  await page.click('a[hx-get="/qubits"]'); await H.sleep(2500);
  await page.click('a[hx-get="/topology"]'); await H.sleep(9000);
  const info = await page.evaluate(()=>{
    const btns=[...document.querySelectorAll('.topo-hero-mbtn')].map(b=>({t:b.textContent.trim(), key:b.getAttribute('data-hero-metric'), sel:b.getAttribute('aria-selected')}));
    return {btns};
  });
  console.log('METRIC BUTTONS NOW:', JSON.stringify(info.btns));
  await H.shot(page,'topo-07-metricbar');
  await browser.close();
})();
