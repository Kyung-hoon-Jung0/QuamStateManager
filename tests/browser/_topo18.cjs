const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  const probe = await page.evaluate(()=>{
    const b=document.querySelector('.topo-subnav-btn[data-view="overview"]');
    b.scrollIntoView({block:'center'});
    const r=b.getBoundingClientRect();
    const cx=Math.round(r.x+r.width/2), cy=Math.round(r.y+r.height/2);
    const top=document.elementFromPoint(cx,cy);
    return {rect:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
      cx,cy, topEl: top? (top.tagName+'.'+String(top.className).slice(0,60)+' view='+top.getAttribute('data-view')) : null,
      isSame: top===b, hasOnclick: !!b.getAttribute('onclick'), onclick: b.getAttribute('onclick'),
      fnExists: typeof window.setChipStatusView};
  });
  console.log('PROBE:', JSON.stringify(probe,null,1));
  // click via real mouse at that point
  await page.mouse.click(probe.cx, probe.cy);
  await H.sleep(2200);
  const after = await page.evaluate(()=>{
    const act=[...document.querySelectorAll('.topo-subnav-btn')].filter(b=>b.classList.contains('active')).map(b=>b.getAttribute('data-view'));
    const tp=document.querySelector('#table-pane');
    const el=document.querySelector('[data-topo-section="overview"]');
    return {active:act, paneTop:Math.round(tp.scrollTop), overviewTop: Math.round(el.getBoundingClientRect().top)};
  });
  console.log('AFTER MOUSE CLICK:', JSON.stringify(after));
  // now call the API directly
  await page.evaluate(()=>window.setChipStatusView('overview', null, true));
  await H.sleep(2200);
  const after2 = await page.evaluate(()=>{
    const act=[...document.querySelectorAll('.topo-subnav-btn')].filter(b=>b.classList.contains('active')).map(b=>b.getAttribute('data-view'));
    const tp=document.querySelector('#table-pane');
    const el=document.querySelector('[data-topo-section="overview"]');
    return {active:act, paneTop:Math.round(tp.scrollTop), overviewTop: Math.round(el.getBoundingClientRect().top)};
  });
  console.log('AFTER DIRECT CALL:', JSON.stringify(after2));
  await H.shot(page,'topo-12-overview');
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
