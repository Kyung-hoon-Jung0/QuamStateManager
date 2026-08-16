const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  const tabs=['topology','overview','distributions','gate','fidelity','coherence','frequencies','calibration','trends'];
  for(const v of tabs){
    const p = await page.evaluate((view)=>{
      const b=document.querySelector('.topo-subnav-btn[data-view="'+view+'"]');
      b.scrollIntoView({block:'center'});
      const r=b.getBoundingClientRect();
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
    }, v);
    await H.sleep(400);
    await page.mouse.click(p.x,p.y);
    await H.sleep(2600);
    const s = await page.evaluate(()=>{
      const act=[...document.querySelectorAll('.topo-subnav-btn')].filter(b=>b.classList.contains('active')).map(b=>b.getAttribute('data-view'));
      // whatever section is at the top of the pane now
      const secs=[...document.querySelectorAll('.topo-section')].map(e=>({id:e.id||e.getAttribute('data-topo-section'),
        top:Math.round(e.getBoundingClientRect().top), h:Math.round(e.getBoundingClientRect().height),
        txt:e.innerText.replace(/\s+/g,' ').slice(0,260)}));
      const atTop = secs.filter(x=>x.top>-100&&x.top<300).sort((a,b)=>a.top-b.top)[0];
      const plots=[...document.querySelectorAll('.js-plotly-plot')].length;
      const svgs=[...document.querySelectorAll('svg')].filter(s=>{const r=s.getBoundingClientRect();return r.width>200&&r.height>200;}).length;
      return {active:act, atTop, plots, bigSvgs:svgs};
    });
    console.log('---', v, JSON.stringify(s.active), 'plots=',s.plots,'bigSvgs=',s.bigSvgs);
    console.log('   atTop:', JSON.stringify(s.atTop));
    await H.shot(page,'sec-'+v);
  }
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
