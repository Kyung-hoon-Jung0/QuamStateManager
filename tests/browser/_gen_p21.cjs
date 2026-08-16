const fs = require('fs');
const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = {};
  page.on('response', async (r) => {
    if (/regenerate\/reconstruct/.test(r.url())) {
      try { const j = JSON.parse(await r.text());
        out.reconstruct = { ok: j.ok, error: j.error, notes: j.notes,
          source_name: j.source_name, mixed_gates: j.mixed_gates,
          nQubits: (j.spec && j.spec.qubits || []).length,
          nPairs: (j.spec && j.spec.qubit_pairs || []).length,
          pairs: (j.spec && j.spec.qubit_pairs || []).slice(0, 8),
          popKeys: j.spec && j.spec.populate ? Object.keys(j.spec.populate) : null,
          popPairCount: j.spec && j.spec.populate && j.spec.populate.pairs ? Object.keys(j.spec.populate.pairs).length : 0,
          popQubitCount: j.spec && j.spec.populate && j.spec.populate.qubit ? Object.keys(j.spec.populate.qubit).length : 0,
          instruments: j.spec && j.spec.instruments ? JSON.stringify(j.spec.instruments).slice(0, 700) : null,
          gate: j.spec && j.spec.pair_gate, arch: j.spec && j.spec.chip_arch,
        };
      } catch (e) { out.reconstructErr = String(e); }
    }
  });
  await H.goto(page, '/', 2500);
  await page.evaluate(() => { const b = document.querySelector('[aria-controls="config-subnav"]'); if (b) b.click(); });
  await H.sleep(600);
  out.subnavCollapsed = await page.$eval('#config-subnav', e => e.className);
  await page.click('a[hx-get="/regenerate"]');
  await H.sleep(20000);
  out.status = await page.$eval('#regen-status', e => ({ hidden: e.hidden, cls: e.className, txt: e.innerText.slice(0, 800) })).catch(() => 'MISSING');
  out.chip = await page.$eval('#regen-chip', e => e.innerText).catch(() => 'MISSING');
  out.meta = await page.$eval('#regen-meta', e => e.innerText).catch(() => 'MISSING');
  out.title = await page.$eval('#regen-body .gen-title', e => e.innerText).catch(() => 'MISSING');
  out.step = await page.$eval('.gen-panel.active', e => e.dataset.step).catch(() => 'MISSING');
  await H.shot(page, 'gen_23_regenerate');
  out.errors = H.errors(page);
  fs.writeFileSync('D:/work/statemanager-cfb/tests/browser/_shots/p21.json', JSON.stringify(out, null, 1), 'utf8');
  console.log('written');
  await browser.close();
})();
