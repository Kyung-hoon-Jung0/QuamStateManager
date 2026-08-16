const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  for(let i=0;i<20;i++){ await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop += 500;}); await H.sleep(300); }
  await H.sleep(2500);

  const probe = async (label) => page.evaluate((lab)=>{
    const nav=document.querySelector('.topo-subnav');
    const nr=nav.getBoundingClientRect();
    const cs=getComputedStyle(nav);
    const btns=[...document.querySelectorAll('.topo-subnav-btn')].map(b=>{
      const r=b.getBoundingClientRect();
      const cx=Math.round(r.x+r.width/2), cy=Math.round(r.y+r.height/2);
      const el=document.elementFromPoint(cx,cy);
      return {v:b.getAttribute('data-view'), cx, cy, blockedBy: el===b?null:(el?el.tagName+'.'+String(el.className).slice(0,34):'none')};
    });
    const tray=document.querySelector('#pending-tray')||document.querySelector('.tray');
    const bar=document.querySelector('.topbar')||document.querySelector('header');
    return {label:lab, navPos:cs.position, navStickyTop:cs.top,
      navRect:{y:Math.round(nr.y), b:Math.round(nr.bottom), h:Math.round(nr.height)},
      trayBottom: tray? Math.round(tray.getBoundingClientRect().bottom):null,
      barBottom: bar? Math.round(bar.getBoundingClientRect().bottom):null,
      blocked: btns.filter(b=>b.blockedBy).map(b=>b.v+'<-'+b.blockedBy),
      clear: btns.filter(b=>!b.blockedBy).map(b=>b.v)};
  }, label);

  console.log('A) scrolled down, teaching banner present:');
  console.log(JSON.stringify(await probe('scrolled'),null,1));

  // dismiss the teaching banner (the ✕ in the tray)
  const dismissed = await page.evaluate(()=>{
    const x=[...document.querySelectorAll('button,a,span')].find(e=>e.className && String(e.className).match(/tray-teach-close|teach-close/));
    if(x){x.click();return 'clicked '+x.className;}
    const t=document.querySelector('.tray-teach'); if(t){const b=t.parentElement.querySelector('button'); if(b){b.click(); return 'clicked sibling btn';}}
    return 'not found';
  });
  console.log('dismiss ->', dismissed);
  await H.sleep(2000);
  console.log('B) after dismissing the teaching banner:');
  console.log(JSON.stringify(await probe('dismissed'),null,1));
  await H.shot(page,'topo-14-subnav-blocked');
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
