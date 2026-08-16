const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  const dr = await page.evaluate(async()=>{const r=await fetch('/discard_all',{method:'POST'});return r.status;});
  console.log('discard_all ->', dr);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  await page.evaluate(()=>document.querySelector('#topo-hero').scrollIntoView({block:'center'}));
  await H.sleep(1000);

  const measure = ()=> page.evaluate(()=>{
    const svg=document.querySelector('.topo-hero-svg');
    const host=document.querySelector('#topo-hero');
    const sc=document.querySelector('.topo-hero-scroll');
    const sr=svg.getBoundingClientRect(), hr=host.getBoundingClientRect();
    const scr = sc? sc.getBoundingClientRect():null;
    // extremes of drawn content in screen coords
    let minx=1e9,maxx=-1e9,miny=1e9,maxy=-1e9;
    svg.querySelectorAll('g[data-hero-qubit] circle').forEach(c=>{const r=c.getBoundingClientRect();
      minx=Math.min(minx,r.x);maxx=Math.max(maxx,r.right);miny=Math.min(miny,r.y);maxy=Math.max(maxy,r.bottom);});
    return {svg:{x:Math.round(sr.x),w:Math.round(sr.width),h:Math.round(sr.height)},
      host:{x:Math.round(hr.x),w:Math.round(hr.width)},
      scroll: scr? {w:Math.round(scr.width),h:Math.round(scr.height), sw: sc.scrollWidth, sh: sc.scrollHeight, ow: sc.clientWidth, oh: sc.clientHeight}:null,
      content:{minx:Math.round(minx),maxx:Math.round(maxx),miny:Math.round(miny),maxy:Math.round(maxy)},
      collapsed: document.body.classList.contains('sidebar-collapsed')||document.documentElement.classList.contains('sidebar-collapsed'),
      qspan: Math.round(maxx-minx)};
  });
  const before = await measure();
  await H.shot(page,'topo-10-before-collapse');
  console.log('BEFORE:', JSON.stringify(before));
  // collapse sidebar via the hamburger
  await page.click('#sidebar-toggle, .sidebar-toggle, button[onclick*="toggleSidebar"], #hamburger').catch(async()=>{
    await page.evaluate(()=>{ if(window.toggleSidebar) window.toggleSidebar(); });
  });
  await H.sleep(2500);
  const after = await measure();
  await H.shot(page,'topo-11-after-collapse');
  console.log('AFTER COLLAPSE:', JSON.stringify(after));
  // expand back
  await page.click('#sidebar-toggle, .sidebar-toggle, button[onclick*="toggleSidebar"], #hamburger').catch(()=>{});
  await H.sleep(2200);
  const back = await measure();
  console.log('AFTER EXPAND:', JSON.stringify(back));
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
