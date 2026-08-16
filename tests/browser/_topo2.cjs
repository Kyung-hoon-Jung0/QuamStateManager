const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  const shot = await H.shot(page, 'topo-01-chipstatus');
  const info = await page.evaluate(() => {
    const svgs = [...document.querySelectorAll('svg')].map(s => ({
      id: s.id, cls: s.getAttribute('class'), w: s.getBoundingClientRect().width,
      h: s.getBoundingClientRect().height, vb: s.getAttribute('viewBox'),
      parentId: s.parentElement && s.parentElement.id,
      nText: s.querySelectorAll('text').length,
      nCircle: s.querySelectorAll('circle').length,
      nRect: s.querySelectorAll('rect').length,
      nLine: s.querySelectorAll('line').length,
      nPath: s.querySelectorAll('path').length,
      nPoly: s.querySelectorAll('polygon,polyline').length,
    })).filter(s => s.w > 60 && s.h > 60);
    const heroEl = document.querySelector('#topo-hero');
    return {
      title: document.title,
      svgs,
      hero: heroEl ? {html_len: heroEl.innerHTML.length, rect: heroEl.getBoundingClientRect()} : null,
      hasLogicalNote: document.body.innerText.includes('logical layout') || document.body.innerText.toLowerCase().includes('logical layout'),
      sections: [...document.querySelectorAll('[id^="chip-"], section[id], h2, h3')].map(e=>e.id||e.textContent.trim().slice(0,40)).slice(0,60),
    };
  });
  console.log(JSON.stringify({shot, info, errors: H.errors(page)}, null, 1));
  await browser.close();
})();
