const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = {};
  await H.goto(page, '/', 3000);
  // reset pending first via tray Discard all
  await page.evaluate(() => {
    const b = [...document.querySelectorAll('#pending-tray button, #pending-tray a')].find(x => /Discard all/i.test(x.textContent));
    if (b) b.click();
  });
  await H.sleep(2500);
  out.trayAfterDiscard = await page.evaluate(() => document.querySelector('#pending-tray').getAttribute('data-change-count'));

  // expand the Live State Edit subnav like a user, then click Json Tree View
  await page.evaluate(() => {
    const t = [...document.querySelectorAll('#sidebar .subnav-toggle, #sidebar button, #sidebar a')]
      .find(e => /Live State Edit/i.test(e.textContent) && e.getAttribute('hx-get') !== '/bulk');
    if (t) t.click();
  });
  await H.sleep(800);
  const vis = await page.evaluate(() => { const a = document.querySelector('a[hx-get="/explorer"]'); return !!(a && a.offsetParent); });
  if (vis) { await page.click('a[hx-get="/explorer"]'); }
  else { await page.evaluate(() => document.querySelector('a[hx-get="/explorer"]').click()); }
  out.explorerLinkWasVisible = vis;
  await H.sleep(6000);
  out.err = H.errors(page);
  out.explorerShape = await page.evaluate(() => {
    const root = document.querySelector('#table-pane');
    return {
      hasSearch: !!document.querySelector('#explorer-search'),
      treeNodes: document.querySelectorAll('.tree-node').length,
      sampleNode: (document.querySelector('.tree-node') || {}).outerHTML?.slice(0, 400),
      controls: [...root.querySelectorAll('button, .btn-sm')].map(b => b.textContent.replace(/\s+/g, ' ').trim()).slice(0, 18),
    };
  });
  // search "amplified" in the explorer
  await page.click('#explorer-search');
  await page.keyboard.type('amplified', { delay: 20 });
  await H.sleep(2500);
  out.afterSearch = await page.evaluate(() => {
    const nodes = [...document.querySelectorAll('.tree-node')].filter(n => n.offsetParent);
    return {
      n: nodes.length,
      paths: nodes.map(n => n.getAttribute('data-path')).filter(Boolean).slice(0, 20),
      counter: [...document.querySelectorAll('#table-pane .muted, #table-pane .tree-search-count')].map(e => e.textContent.trim()).filter(t => /match|of/i.test(t)).slice(0, 5),
    };
  });
  out.shot = await H.shot(page, 'le15-explorer-search');
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
