const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  await page.evaluate(()=>document.querySelector('#topo-hero').scrollIntoView({block:'center'}));
  await H.sleep(1000);

  const target = await page.evaluate(()=>{
    const g=document.querySelector('g[data-hero-qubit="q10"] circle');
    const r=g.getBoundingClientRect();
    return {x:r.x+r.width/2, y:r.y+r.height/2};
  });
  // hover, then wait past the 260ms intent
  await page.mouse.move(target.x-200, target.y-200);
  await H.sleep(300);
  await page.mouse.move(target.x, target.y);
  await H.sleep(120);
  const at120 = await page.evaluate(()=>{
    const els=[...document.querySelectorAll('.topo-popup,.qubit-popup,[class*="popup"]')].filter(e=>e.offsetParent!==null);
    return els.map(e=>({cls:e.className, txt:e.innerText.slice(0,60)}));
  });
  await H.sleep(900);
  const after = await page.evaluate(()=>{
    const els=[...document.querySelectorAll('[class*="popup"],[id*="popup"]')].filter(e=>{
      const r=e.getBoundingClientRect(); return r.width>10&&r.height>10&&getComputedStyle(e).visibility!=='hidden'&&getComputedStyle(e).display!=='none';
    });
    return els.map(e=>({cls:e.className, id:e.id, w:Math.round(e.getBoundingClientRect().width), h:Math.round(e.getBoundingClientRect().height), txt:e.innerText.slice(0,400)}));
  });
  await H.shot(page,'topo-04-hover-q10');
  console.log('AT120ms:', JSON.stringify(at120));
  console.log('AFTER ~1s:', JSON.stringify(after,null,1));
  console.log('errors', JSON.stringify(H.errors(page)));
  await browser.close();
})();
