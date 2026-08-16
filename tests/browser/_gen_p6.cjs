const L = require('D:/work/statemanager-cfb/tests/browser/_gen_lib.cjs');
const H = L.H;
(async () => {
  const { browser, page } = await H.open({ port: 8855 });
  const out = {};
  out.atStep4 = await L.walkToQubits(page);
  // set 4 qubits
  await L.setInput(page, '#gen-qubit-count', '4');
  await H.sleep(1500);
  out.summary = await page.$eval('#gen-qubit-summary', e => e.innerText).catch(() => 'ERR');
  out.archNote = await page.$eval('#gen-chip-arch-note', e => e.innerText).catch(() => 'ERR');
  out.arch = await page.$eval('#gen-chip-arch', e => e.value);
  out.nameList = await page.$eval('#gen-qubit-name-list', e => e.innerText.replace(/\n/g, ' | ').slice(0, 400)).catch(() => 'ERR');
  out.pairList = await page.$eval('#gen-pair-list', e => e.innerText.replace(/\n/g, ' | ').slice(0, 400)).catch(() => 'ERR');
  out.topoCaption = await page.$eval('#gen-topo-caption', e => e.innerText).catch(() => 'ERR');
  await H.shot(page, 'gen_04_qubits');

  // naming scheme: grid
  await L.setInput(page, '#gen-naming-preset', 'grid');
  await H.sleep(300);
  await L.evClick(page, '#gen-naming-apply');
  await H.sleep(1000);
  out.afterGrid = await page.$eval('#gen-qubit-name-list', e => e.innerText.replace(/\n/g, ' | ').slice(0, 400)).catch(() => 'ERR');
  out.namingNote = await page.$eval('#gen-naming-note', e => e.innerText).catch(() => 'ERR');
  await H.shot(page, 'gen_05_naming_grid');
  out.errors = H.errors(page);
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
