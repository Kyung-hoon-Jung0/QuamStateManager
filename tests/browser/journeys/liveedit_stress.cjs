/* Real Chrome regression journey. SM_STRESS_CHIP must name a disposable copy. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer-core');
const base = process.env.SM_STRESS_BASE || 'http://127.0.0.1:5784';
const chip = process.env.SM_STRESS_CHIP;
const out = process.env.SM_STRESS_OUT;
if (!chip || !out) throw Error('Set SM_STRESS_CHIP and SM_STRESS_OUT to scratch paths');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const pth = n => `qubits.q${n}.xy.operations.x180_DragCosine.alpha`;
const sel = p => `input[data-dot-path="${p}"]`;
function dig(o, p) { return p.split('.').reduce((a, k) => a[k], o); }
function disk(p) { return dig(JSON.parse(fs.readFileSync(path.join(chip, 'state.json'), 'utf8')), p); }
async function api(page, url, data) {
  return page.evaluate(async (url, data) => {
    const r = await fetch(url, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    const text = await r.text(); let body; try {body=JSON.parse(text)} catch {body=text}
    return {status:r.status, body};
  }, url, data);
}
async function peek(page, paths) { return (await api(page, '/field/peek?'+paths.map(p=>'dot_path='+encodeURIComponent(p)).join('&'))).body.values; }
async function search(page, text) {
  await page.click('#bulk-search');
  await page.keyboard.down('Control'); await page.keyboard.press('a'); await page.keyboard.up('Control');
  await page.keyboard.press('Backspace');
  await page.keyboard.type(text);
  await sleep(1200);
}
async function type(page, p, val) {
  if (!await page.$(sel(p))) {
    // Exercise the normal virtualized column renderer before clicking.
    const key = p.endsWith('.f_01') ? 'f_01' : 'xy_operations_x180_DragCosine_alpha';
    await page.evaluate(k=>window.BulkEdit._virtHydrateCols([k]),key);
  }
  await page.waitForSelector(sel(p));
  await page.$eval(sel(p),e=>e.scrollIntoView({block:'center',inline:'center'}));
  await sleep(300);
  const rect=await page.$eval(sel(p),e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,hidden:document.hidden,display:getComputedStyle(e).display,vis:getComputedStyle(e).visibility}});
  console.log('type rect',p,rect);
  await shot(page,'before-type');
  await page.click(sel(p));
  await page.keyboard.down('Control'); await page.keyboard.press('a'); await page.keyboard.up('Control');
  await page.keyboard.type(String(val));
}
async function shot(page, name) { await page.screenshot({path:path.join(out, name+'.png')}); }
(async () => {
  fs.mkdirSync(out, {recursive:true});
  const browser = await puppeteer.connect({browserURL:process.env.SM_STRESS_CDP || 'http://127.0.0.1:9344',defaultViewport:null});
  try {
    const a = await browser.newPage(); await a.setViewport({width:1600,height:1000});
    const session=await a.createCDPSession();
    const {windowId}=await session.send('Browser.getWindowForTarget');
    await session.send('Browser.setWindowBounds',{windowId,bounds:{windowState:'normal',width:1700,height:1120}});
    await a.setViewport({width:1600,height:1000});
    a.on('dialog', d => d.accept());
    await a.goto(base);
    await a.evaluate(async chip => fetch('/load',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'folder='+encodeURIComponent(chip)}),chip);
    await a.evaluate(()=>localStorage.setItem('quam_bulk_hidden_cols_v2',JSON.stringify(['xy_RF_frequency'])));
    await a.goto(base+'/bulk'); await a.bringToFront(); await sleep(4000);
    const freq='qubits.q1.f_01', twin='qubits.q1.xy.RF_frequency';
    const orig=disk(freq); assert.equal(disk(twin),orig);
    await search(a,'f_01');
    assert.equal(await a.$(sel(twin)),null,'partner must be cold');
    await type(a,freq,orig+100000); await a.keyboard.press('Enter'); await sleep(1200);
    await shot(a,'d1-after-enter');
    console.log('D1 browser',await a.evaluate(()=>({url:location.href,active:document.activeElement?.outerHTML?.slice(0,200),errors:[...document.querySelectorAll('.bulk-row-error')].filter(e=>!e.hidden).map(e=>e.innerText)})));
    assert.deepEqual(Object.values(await peek(a,[freq,twin])),[orig+100000,orig+100000]);
    await shot(a,'d1-staged');
    await api(a,'/state/apply-to-live',{});
    assert.equal(disk(freq),orig+100000); assert.equal(disk(twin),orig+100000);
    await api(a,'/undo',{});
    assert.equal(disk(freq),orig); assert.equal(disk(twin),orig);
    console.log('PASS D-1: cold twin, staged pair, disk pair, one undo');

    await a.reload(); await sleep(3000); await search(a,'x180_DragCosine alpha');
    await type(a,pth(1),'-0.071'); await a.keyboard.press('Enter'); await sleep(800);
    await a.click(sel(pth(1)));
    await a.keyboard.down('Shift'); await a.click(sel(pth(3))); await a.keyboard.up('Shift');
    await a.keyboard.down('Control'); await a.keyboard.press('d'); await a.keyboard.up('Control');
    await shot(a,'d2-filled');
    await a.click('#bulk-apply-all'); await sleep(2200);
    const vals=await peek(a,[pth(1),pth(2),pth(3)]);
    assert.deepEqual(Object.values(vals),[-.071,-.071,-.071]);
    await api(a,'/state/apply-to-live',{});
    for(let n=1;n<=3;n++) assert.equal(disk(pth(n)),-.071);
    await shot(a,'d2-applied');
    console.log('PASS D-2: Shift range, Ctrl+D, Apply all, server and disk');

    const b=await browser.newPage(); await b.goto(base+'/bulk');
    await a.bringToFront(); await sleep(5500);
    const oldSeq=await a.$eval('#pending-tray',t=>t.dataset.editSeq);
    await api(b,'/field/edit-batch',{updates:[{dot_path:pth(1),value:'-0.073'}]});
    await api(b,'/state/apply-to-live',{});
    // No poll has refreshed A since the foreign write: the journal must refuse.
    const undo=await a.evaluate(async seq=>{const r=await fetch('/undo',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'seen_edit_seq='+encodeURIComponent(seq)});return {status:r.status,text:await r.text()}},oldSeq);
    assert.equal(undo.status,409); assert(undo.text.includes(pth(1)));
    assert.equal((await peek(a,[pth(1)]))[pth(1)],-.073); assert.equal(disk(pth(1)),-.073);
    console.log('PASS D-4: unseen live undo refused with path; server and disk preserved');
    await a.bringToFront(); assert.equal(await a.evaluate(()=>document.hidden),false);
    await sleep(8000);
    assert.equal(await a.$eval(sel(pth(1)),e=>Number(e.value)),-.073);
    assert.equal(await a.$eval('#pending-tray',e=>e.dataset.changeCount),'0');
    await shot(a,'d3-followed');
    console.log('PASS D-3: visible A follows B within 8 seconds');
    for(const q of ['f_01>=4e9','amp>0.28','length=24..28']) {
      await search(a,q); assert((await a.$eval('#bulk-search-count',e=>e.textContent)).includes('only in the sidebar'));
    }
    await search(a,'RF_frequncy');
    assert((await a.evaluate(()=>document.body.innerText)).includes('RF_frequency'));
    await shot(a,'search-closest');
    await search(a,'power'); await a.keyboard.press('Escape');
    const power=await a.evaluate(()=>[...document.querySelectorAll('input[data-dot-path]')].find(e=>e.offsetWidth && /qubits\.q1\.xy.*full_scale_power/.test(e.dataset.dotPath))?.dataset.dotPath);
    assert(power,'XY power cell is visible');
    const resolved=await a.$eval(sel(power),e=>e.dataset.resolved||e.dataset.dotPath);
    const cellBefore=await a.$eval(sel(power),e=>e.value);
    const powerBefore=(await peek(a,[resolved]))[resolved];
    assert.notEqual(powerBefore,undefined);
    const fileBefore=fs.readFileSync(path.join(chip,'state.json'),'utf8');
    await type(a,power,3); await a.keyboard.press('Enter');
    await a.waitForSelector('.fsp-card',{visible:true});
    await a.evaluate(p=>window.FieldHistory.open(document.querySelector('#bulk-search'),p),power);
    assert.equal(await a.evaluate(()=>!!document.querySelector('#field-history-panel') && getComputedStyle(document.querySelector('#field-history-panel')).display!=='none'),false);
    await shot(a,'fsp-modal');
    for(const button of await a.$$('.fsp-card button')) {
      if ((await button.evaluate(e=>e.textContent)).trim()==='Cancel') {await button.click();break;}
    }
    await sleep(500);
    assert.equal(await a.$eval(sel(power),e=>e.value),cellBefore);
    assert.equal(await a.$eval(sel(power),e=>e.classList.contains('dirty')),false);
    assert.equal((await peek(a,[resolved]))[resolved],powerBefore);
    assert.equal(fs.readFileSync(path.join(chip,'state.json'),'utf8'),fileBefore);
    console.log('PASS FSP Cancel resets cell; modal blocks history; server and disk unchanged');
    for(const theme of ['light','dark']) {
      await a.evaluate(t=>document.documentElement.setAttribute('data-theme',t),theme);
      await search(a,'power'); await a.keyboard.press('Escape');
      await a.click(sel(power)); await shot(a,'power-'+theme);
    }
    console.log('PASS operator guidance, RF typo, light/dark screenshots');
  } finally { await browser.disconnect(); }
})().catch(e=>{console.error(e);process.exitCode=1});
