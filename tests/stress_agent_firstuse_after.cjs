/* The first-use fixes, verified in the browser that found the defects.
 *
 * Every one of these was measured broken on a4db80d and is re-measured here:
 *   - nothing focused the composer (activeElement BODY at 0/800/2500 ms)
 *   - a plain sentence typed blind went nowhere (19 characters swallowed)
 *   - a leading "/" was taken by the topbar search and replaced the pane
 *   - "?" opened the keyboard cheat sheet instead of typing a question mark
 *   - Setup's step-4 error rendered 792 px above the scroller and self-deleted
 *
 * argv[2]=out.json argv[3]=cdp argv[4]=base
 */
const fs = require('fs');
const OUT = process.argv[2], CDP = process.argv[3], BASE = process.argv[4];
const results = [], errors = [];
function ok(n, c, d) { results.push({ name: n, pass: !!c, detail: d === undefined ? null : d }); }

async function main() {
  const t = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = t.find(x => x.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  let id = 0; const pend = new Map();
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') {
      const s = String((m.params.exceptionDetails.exception || {}).description || m.params.exceptionDetails.text);
      if (!/unsafe-eval/.test(s)) errors.push(s.slice(0, 180));
    }
  };
  const send = (mm, p = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method: mm, params: p })); });
  const ev = async x => {
    const rr = await send('Runtime.evaluate', { expression: x, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) throw new Error(String((rr.result.exceptionDetails.exception || {}).description || '').slice(0, 200));
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const typeChar = async ch => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
    await sleep(14);
  };
  const typeBlind = async text => { for (const ch of text) await typeChar(ch); await sleep(300); };

  await send('Runtime.enable'); await send('Page.enable');
  // the static JS is what is under test; a cached copy would measure
  // the previous build
  await send('Network.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  /* ── the composer takes the caret ─────────────────────────────────── */
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(5000);
  const focused = await ev(`(function(){
    var a = document.activeElement;
    return { tag: a && a.tagName, cls: a && a.className,
             isComposer: !!(a && a.classList && a.classList.contains('ag-input')) };
  })()`);
  ok('the composer has the caret on load', focused.isComposer, focused);

  /* ── a plain sentence typed blind lands in it ─────────────────────── */
  await typeBlind('calibrate q0 readout');
  const plain = await ev(`(function(){
    return { composer: (document.querySelector('#agent-home .ag-input')||{}).value,
             gsearch: (document.getElementById('global-search')||{}).value };
  })()`);
  ok('19 blind characters land in the composer, not nowhere',
     plain.composer === 'calibrate q0 readout', plain);

  /* ── "/" is a character here, not the global search ───────────────── */
  // guarded: with the fix reverted the hijack REPLACES the pane, so the
  // composer is gone — that is the defect, and it must read as a failure
  // rather than as a crashed driver.
  await ev(`(function(){var t=document.querySelector('#agent-home .ag-input'); if(!t) return 0; t.value=''; t.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  await typeBlind('/run 05_power_rabi q1');
  const slash = await ev(`(function(){
    return { composer: (document.querySelector('#agent-home .ag-input')||{}).value,
             gsearch: (document.getElementById('global-search')||{}).value,
             noResults: /No results/.test(document.getElementById('table-pane').textContent) };
  })()`);
  ok('a leading "/" stays in the composer', slash.composer === '/run 05_power_rabi q1', slash);
  ok('…and the Agent page is not replaced by a search', !slash.noResults, slash);

  /* ── "?" types a question mark ────────────────────────────────────── */
  await ev(`(function(){var t=document.querySelector('#agent-home .ag-input'); if(!t) return 0; t.value=''; t.focus(); t.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  await send('Input.dispatchKeyEvent', { type: 'keyDown', text: '?', unmodifiedText: '?', key: '?' });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: '?' });
  await sleep(400);
  const q = await ev(`(function(){
    return { composer: (document.querySelector('#agent-home .ag-input')||{}).value,
             sheet: !!document.getElementById('kb-cheatsheet') };
  })()`);
  ok('"?" types a question mark instead of opening the cheat sheet',
     q.composer === '?' && !q.sheet, q);

  /* ── …and the caret is NOT stolen from somewhere else ─────────────── */
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(2500);
  await ev(`(function(){var s=document.getElementById('sidebar-filter-input'); if(s) s.focus(); return 1;})()`);
  await sleep(3000);
  const kept = await ev(`(function(){ var a=document.activeElement; return a && a.id; })()`);
  ok('a caret already somewhere else is left alone',
     kept === 'sidebar-filter-input' || kept === '', { activeId: kept });

  /* ── Setup answers beside the control that failed ─────────────────── */
  await send('Page.navigate', { url: BASE + '/agent/setup' });
  await sleep(5000);
  const before = await ev(`document.querySelectorAll('.ag-err').length`);
  const shown = await ev(`(async function(){
    var i = document.getElementById('as-jroot');
    if (!i) return { err: 'no journal input' };
    i.value = 'Z:\\\\nope\\\\journal';
    var b = Array.prototype.slice.call(document.querySelectorAll('#as-journal button'))
      .filter(function(x){ return /Use this folder/.test(x.textContent); })[0];
    if (!b) return { err: 'no button' };
    var btnTop = b.getBoundingClientRect().top;
    b.click();
    await new Promise(function(r){ setTimeout(r, 2500); });
    var e = document.querySelector('#as-journal .ag-err');
    var r = e && e.getBoundingClientRect();
    var pane = document.getElementById('table-pane');
    var pr = pane && pane.getBoundingClientRect();
    return { inSection: !!e, text: e ? e.textContent.slice(0, 80) : null,
             errTop: r ? Math.round(r.top) : null, btnTop: Math.round(btnTop),
             paneTop: pr ? Math.round(pr.top) : null,
             visible: !!(r && r.top > (pr ? pr.top : 0) - 5 && r.top < 1000) };
  })()`);
  ok('the failure is rendered inside the section that failed', shown.inSection, shown);
  ok('…on screen, not above the scroller', shown.visible, shown);
  ok('…and it names the path it could not use',
     shown.text && /Z:/.test(shown.text), shown.text);
  await sleep(7000);
  const still = await ev(`!!document.querySelector('#as-journal .ag-err')`);
  ok('…and it is still there after 7 s (it used to delete itself at 6)', still);
  void before;

  fs.writeFileSync(OUT, JSON.stringify({ results, errors }, null, 1));
  const bad = results.filter(r => !r.pass);
  console.log('first-use checks: ' + (results.length - bad.length) + '/' + results.length
              + '  console errors: ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + ' ' + JSON.stringify(b.detail).slice(0, 220)));
  errors.slice(0, 4).forEach(e => console.log('  ERR ' + e));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
