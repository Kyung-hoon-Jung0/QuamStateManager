const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  const d = await page.evaluate(()=>{
    const out={};
    document.querySelectorAll('g[data-hero-qubit]').forEach(g=>{
      const id=g.getAttribute('data-hero-qubit');
      const cs=getComputedStyle(g);
      out[id]={gClass:g.getAttribute('class'), outline:cs.outline, outlineOffset:cs.outlineOffset, filter:cs.filter,
        circCls:g.querySelector('circle').getAttribute('class'),
        circStroke:getComputedStyle(g.querySelector('circle')).stroke,
        circStrokeW:getComputedStyle(g.querySelector('circle')).strokeWidth,
        nRect:g.querySelectorAll('rect').length, tab:g.getAttribute('tabindex')};
    });
    return {out, active: document.activeElement && (document.activeElement.tagName+'/'+(document.activeElement.getAttribute('data-hero-qubit')||document.activeElement.className))};
  });
  const keys=['q20','q15','q10','q19','q18','q1','q5'];
  keys.forEach(k=>console.log(k, JSON.stringify(d.out[k])));
  console.log('active', d.active);
  await browser.close();
})();
