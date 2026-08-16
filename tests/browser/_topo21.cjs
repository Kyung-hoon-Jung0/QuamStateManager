const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  for(let i=0;i<20;i++){ await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop += 500;}); await H.sleep(350); }
  await H.sleep(3000);
  const g = await page.evaluate(()=>{
    const host=document.querySelector('#topo-metric-panels');
    return {
      hostExists: !!host,
      groups: [...document.querySelectorAll('#topo-metric-panels [data-group]')].map(e=>({g:e.getAttribute('data-group'), h:Math.round(e.getBoundingClientRect().height), t:e.innerText.replace(/\s+/g,' ').slice(0,70)})),
      hostTxt: host? host.innerText.replace(/\s+/g,' ').slice(0,400):null,
      panels: [...document.querySelectorAll('#topo-metric-panels > *')].map(e=>({id:e.id, cls:String(e.className).slice(0,40), grp:e.getAttribute('data-group')})),
    };
  });
  console.log(JSON.stringify(g,null,1));
  // Now click Fidelity for real and screenshot where we land
  await page.evaluate(()=>{document.querySelector('#table-pane').scrollTop=2000;});
  await H.sleep(1000);
  const before = await page.evaluate(()=>Math.round(document.querySelector('#table-pane').scrollTop));
  const p = await page.evaluate(()=>{const b=document.querySelector('.topo-subnav-btn[data-view="fidelity"]');const r=b.getBoundingClientRect();return{x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};});
  await page.mouse.click(p.x,p.y);
  await H.sleep(3000);
  const after = await page.evaluate(()=>({top:Math.round(document.querySelector('#table-pane').scrollTop),
    active:[...document.querySelectorAll('.topo-subnav-btn')].filter(b=>b.classList.contains('active')).map(b=>b.getAttribute('data-view')),
    firstVisible: (()=>{const s=[...document.querySelectorAll('.topo-section')].map(e=>({id:e.id||e.getAttribute('data-topo-section'),top:Math.round(e.getBoundingClientRect().top)})).filter(x=>x.top>-50&&x.top<400).sort((a,b)=>a.top-b.top)[0];return s;})()}));
  console.log('FIDELITY CLICK: paneTop', before, '->', after.top, JSON.stringify(after));
  await H.shot(page,'topo-13-fidelity-landing');
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
