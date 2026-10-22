/* Measure the topbar ladder on an isolated fixture server. Never applies edits.
 * Usage: node tests/browser/journeys/topbar_ladder.cjs <port>
 * Optional: EDIT_PATH and EDIT_VALUE select one staged field via /field/edit,
 * following the setup helper in hub_versions.cjs. Run only in the browser phase.
 */
'use strict';
const { open } = require('./cdp.cjs');
const port = process.argv[2];
if (!port || ['5050', '5060', '5061'].includes(port)) throw new Error('Supply an isolated fixture server port');
const base = `http://127.0.0.1:${port}`;
const verdict = { measurements: [], staging: null, reload: false, errors: [] };
const measure = `JSON.stringify((function(){
  var bar=document.querySelector('.topbar'), root=parseFloat(getComputedStyle(document.documentElement).fontSize);
  function visible(el){var r=el.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden';}
  var nodes=Array.from(bar.querySelectorAll('li, a, button, input, select, [role="status"]')).filter(visible);
  var items=nodes.map(function(el){
    var r=el.getBoundingClientRect(), text=(el.innerText||el.value||'').replace(/\\s+/g,' ').trim();
    var title=el.getAttribute('title')||el.getAttribute('aria-label')||'';
    if(!title){var child=el.querySelector('[title], [aria-label]');if(child)title=child.getAttribute('title')||child.getAttribute('aria-label');}
    var words=text.replace(/[^a-zA-Z0-9]+/g,'').length>0;
    var ranges=[], walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
    while(walker.nextNode()){
      var node=walker.currentNode;if(!node.textContent.trim()||!visible(node.parentElement))continue;
      var range=document.createRange();range.selectNodeContents(node);
      Array.from(range.getClientRects()).forEach(function(t){ranges.push(t.left>=r.left-1&&t.right<=r.right+1);});
    }
    return {id:el.id||el.className||el.tagName,text:text,title:title,widthRem:r.width/root,heightRem:r.height/root,
      topRem:r.top/root,whole:el.scrollWidth<=el.clientWidth+1&&ranges.every(Boolean),labelled:words||!!title};
  });
  return {width:innerWidth,heightRem:bar.getBoundingClientRect().height/root,items:items,
    ok:items.length>0&&items.every(function(item){return item.whole&&item.labelled;})};
})())`;
async function snapshot(page, label) {
  const result = JSON.parse(await page.ev(measure));
  verdict.measurements.push({ label, ...result });
}
async function click(page, selector) {
  // Poll: a popover can still be opening while the server answers another request.
  let point = null;
  for (let i = 0; i < 20 && !point; i++) {
    point = await page.ev(`(function(){var el=document.querySelector(${JSON.stringify(selector)});if(!el)return null;var r=el.getBoundingClientRect();return r.width&&r.height?[r.left+r.width/2,r.top+r.height/2]:null;})()`);
    if (!point) await page.sleep(250);
  }
  if (!point) throw new Error('Missing visible control: ' + selector);
  await page.click(point[0], point[1]);
  await page.sleep(400);
}
// Same staging route and request structure used by hub_versions.cjs setup().
async function stage(page) {
  const field = await page.ev(`(function(){var inputs=Array.from(document.querySelectorAll('input.bulk-cell[data-dot-path]'));
    var input=inputs.find(function(el){return el.value.trim()!==''&&Number.isFinite(Number(el.value));});
    return input?{path:input.dataset.dotPath,value:Number(input.value)}:null;})()`);
  const dotPath = process.env.EDIT_PATH || (field && field.path);
  const value = process.env.EDIT_VALUE === undefined ? (field && String(field.value === 0 ? 1 : field.value * 1.01)) : process.env.EDIT_VALUE;
  if (!dotPath || value === null) throw new Error('Supply EDIT_PATH and EDIT_VALUE for a numeric fixture field');
  const clean = await page.ev(`(function(){var tray=document.getElementById('pending-tray');return !!tray&&tray.dataset.workingDirty!=='1'&&!(Number(tray.dataset.changeCount)>0);})()`);
  if (!clean) throw new Error('The fixture must start without pending edits');
  const result = await page.ev(`(async function(){var form=new FormData();form.append('dot_path',${JSON.stringify(dotPath)});form.append('value',${JSON.stringify(value)});
    var response=await fetch('/field/edit',{method:'POST',body:form,headers:{'X-SM-Actor':'operator'}});return response.status;})()`);
  if (result !== 200) throw new Error('Staging failed: ' + result);
  await page.send('Page.reload');
  await page.sleep(2500);
  const pending = await page.ev(`(function(){var tray=document.getElementById('pending-tray');return !!tray&&(tray.dataset.workingDirty==='1'||Number(tray.dataset.changeCount)>0);})()`);
  verdict.staging = { performed: true, pending };
  if (!pending) throw new Error('The staged edit did not appear in the tray');
}
(async () => {
  const page = await open(base + '/bulk', 1600, 950);
  try {
    await page.sleep(2500);
    await snapshot(page, '1600 rest');
    await stage(page);
    if (verdict.staging.performed) await snapshot(page, '1600 staged');
    // A sync panel the server opened on its own (pending edit vs live) keeps
    // focus and swallows the first outside click; close it the way a user
    // would, and record that it was open.
    verdict.syncPanelOpenBeforeReload = await page.ev(`typeof _syncPanelOpen==='function'&&_syncPanelOpen()`);
    if (verdict.syncPanelOpenBeforeReload) { await page.key('Escape', 'Escape', 27); await page.sleep(300); }
    await click(page, '#topbar-reload');
    await click(page, '.sm-reload-opt[data-mode="state"]');
    await page.sleep(3000);
    verdict.reload = await page.ev(`document.readyState==='complete'&&!document.querySelector('.sm-reload-pop:not([hidden])')`);
    await snapshot(page, '1600 after reload');
    // Undo only our staged setup edit, through the existing journey setup route.
    if (verdict.staging.performed) {
      await page.ev(`fetch('/undo',{method:'POST',headers:{'X-SM-Actor':'operator'}})`);
      await page.send('Page.reload');
      await page.sleep(2500);
    }
    for (const width of [1280, 950]) {
      await page.send('Emulation.setDeviceMetricsOverride', { width, height: 950, deviceScaleFactor: 1, mobile: false });
      await page.sleep(1500);
      await snapshot(page, width + ' rest');
    }
    verdict.errors = page.errors(0);
    verdict.ok = verdict.reload && verdict.errors.length === 0 && verdict.measurements.every(result => result.ok);
  } finally { await page.close(); }
  console.log(JSON.stringify(verdict, null, 2));
  // Exit explicitly: the CDP socket otherwise keeps Node alive after close().
  process.exit(verdict.ok ? 0 : 1);
})().catch(error => { verdict.ok=false; verdict.errors.push(String(error)); console.log(JSON.stringify(verdict,null,2)); process.exit(1); });
