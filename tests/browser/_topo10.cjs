const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8844 });
  await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/topology"]');
  await H.sleep(9000);
  const ctl = await page.evaluate(()=>{
    const sels=[...document.querySelectorAll('select')].map(s=>({id:s.id,cls:s.className,val:s.value,opts:[...s.options].map(o=>o.value+'|'+o.textContent.trim())}));
    const btns=[...document.querySelectorAll('button,[role="tab"],.chip,.seg-btn')].map(b=>({t:(b.textContent||'').trim().slice(0,28), cls:(b.className||'').toString().slice(0,50), id:b.id})).filter(b=>b.t);
    return {sels, btns: btns.slice(0,80)};
  });
  console.log('SELECTS:', JSON.stringify(ctl.sels,null,1));
  console.log('BUTTONS:', JSON.stringify(ctl.btns.map(b=>b.t)));
  await browser.close();
})();
