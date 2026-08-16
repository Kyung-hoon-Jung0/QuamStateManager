const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  for(let i=0;i<20;i++){ await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop += 500;}); await H.sleep(350); }
  await H.sleep(3000);
  await page.evaluate(()=>{
    window.__calls=[];
    const orig=window.setChipStatusView;
    window.setChipStatusView=function(v,b,s){window.__calls.push(v);return orig.apply(this,arguments);};
    document.querySelector('#table-pane').scrollTop=3000;
  });
  await H.sleep(1200);
  const geo = await page.evaluate(()=>{
    const b=document.querySelector('.topo-subnav-btn[data-view="calibration"]');
    const nav=b.closest('.topo-subnav');
    const r=b.getBoundingClientRect(), nr=nav.getBoundingClientRect();
    const cx=Math.round(r.x+r.width/2), cy=Math.round(r.y+r.height/2);
    const el=document.elementFromPoint(cx,cy);
    return {btn:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
      navPos:getComputedStyle(nav).position, navTop:Math.round(nr.y),
      cx,cy, hit: el? el.tagName+'.'+String(el.className).slice(0,40)+'|'+el.getAttribute('data-view'):null,
      same: el===b, winY: window.scrollY};
  });
  console.log('GEO:', JSON.stringify(geo,null,1));
  await page.mouse.click(geo.cx, geo.cy);
  await H.sleep(3000);
  const r = await page.evaluate(()=>({calls:window.__calls, top:Math.round(document.querySelector('#table-pane').scrollTop),
    active:[...document.querySelectorAll('.topo-subnav-btn')].filter(b=>b.classList.contains('active')).map(b=>b.getAttribute('data-view'))[0],
    calibTop: Math.round(document.querySelector('#topo-metric-panels [data-group="calibration"]').getBoundingClientRect().top)}));
  console.log('AFTER CLICK:', JSON.stringify(r));
  await browser.close();
})();
