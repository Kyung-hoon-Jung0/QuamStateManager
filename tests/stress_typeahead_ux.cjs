/* What actually happens, key by key, when a person types into the box.
 *
 * Not "does it work" — what the INTERACTION is: what is selected when the
 * panel opens, what a bare Enter does, what ArrowDown+Enter does, what Tab
 * does, whether hovering with the mouse makes Enter take that row, and
 * whether the panel ever tells you any of it.
 *
 * argv[2]=out.json argv[3]=cdp argv[4]=base
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3];
const BASE = process.argv[4];
const obs = {};

async function main() {
  const t = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = t.find(x => x.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  let id = 0; const pend = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } };
  const send = (mm, p = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method: mm, params: p })); });
  const ev = async x => {
    const rr = await send('Runtime.evaluate', { expression: x, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) throw new Error(String((rr.result.exceptionDetails.exception || {}).description || '').slice(0, 200));
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const KEY = { Enter: 13, Tab: 9, Escape: 27, ArrowDown: 40, ArrowUp: 38 };
  const press = async k => {
    const p = { type: 'rawKeyDown', key: k, windowsVirtualKeyCode: KEY[k], nativeVirtualKeyCode: KEY[k] };
    if (k === 'Enter' || k === 'Tab') { p.type = 'keyDown'; p.text = k === 'Enter' ? '\r' : '\t'; }
    await send('Input.dispatchKeyEvent', p);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, windowsVirtualKeyCode: KEY[k] });
    await sleep(180);
  };
  const typeIn = async (sel, text) => {
    await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); e.focus(); e.value=''; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
    for (const ch of text) {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
      await sleep(12);
    }
    await sleep(320);
  };
  const snap = async () => ev(`(function(){
    var p = document.getElementById('sm-typeahead');
    var st = window.Typeahead._state();
    var rows = (p && !p.hidden) ? Array.prototype.map.call(p.querySelectorAll('.sm-th-row'), function(r){
      return { t:(r.querySelector('.sm-th-label')||{}).textContent,
               active: r.classList.contains('active'), note: r.classList.contains('sm-th-note') }; }) : [];
    return { open: !!(p && !p.hidden), active: st ? st.active : null, rows: rows,
             box: (document.querySelector('#sidebar-filter-input')||{}).value };
  })()`);

  await send('Runtime.enable'); await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: BASE + '/' });
  await sleep(4000);

  const SB = '#sidebar-filter-input';

  // 1. what is selected the moment the panel opens?
  await typeIn(SB, 'multipl');
  obs.on_open = await snap();

  // 2. a BARE Enter — no arrow first
  await press('Enter');
  await sleep(600);
  obs.bare_enter = await snap();

  // 3. ArrowDown then Enter
  await typeIn(SB, 'multipl');
  await press('ArrowDown');
  obs.after_one_arrow = await snap();
  await press('Enter');
  await sleep(600);
  obs.arrow_then_enter = await snap();

  // 4. Tab instead of Enter
  await typeIn(SB, 'multipl');
  await press('ArrowDown');
  await press('Tab');
  await sleep(400);
  obs.arrow_then_tab = await snap();

  // 5. Tab with NOTHING selected
  await typeIn(SB, 'multipl');
  await press('Tab');
  await sleep(300);
  obs.tab_with_nothing = await snap();

  // 6. does the MOUSE hovering a row make Enter take it?
  await typeIn(SB, 'multipl');
  const box = await ev(`(function(){
    var r = document.querySelector('#sm-typeahead .sm-th-row:not(.sm-th-note)');
    if (!r) return null; var b = r.getBoundingClientRect();
    return [b.left + b.width/2, b.top + b.height/2];
  })()`);
  if (box) {
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: box[0], y: box[1] });
    await sleep(250);
    obs.hovered = await snap();
    obs.hover_looks_selected = await ev(`(function(){
      var r = document.querySelector('#sm-typeahead .sm-th-row:not(.sm-th-note)');
      var a = document.querySelector('#sm-typeahead .sm-th-row.active');
      var hb = getComputedStyle(r).backgroundColor;
      // what an ACTIVE row looks like, for comparison
      r.classList.add('active');
      var ab = getComputedStyle(r).backgroundColor;
      r.classList.remove('active');
      return { hovered_bg: hb, active_bg: ab, same: hb === ab, any_active_row: !!a };
    })()`);
    await press('Enter');
    await sleep(600);
    obs.hover_then_enter = await snap();
  }

  // 7. does the panel tell the user any of this?
  obs.hints = await ev(`(function(){
    var p = document.getElementById('sm-typeahead');
    var inp = document.querySelector('#sidebar-filter-input');
    return {
      panel_text: p ? p.textContent.replace(/\\s+/g,' ').slice(0, 300) : null,
      panel_has_key_hint: p ? /↑|↓|Enter|Tab|Esc/i.test(p.textContent) : false,
      input_title: inp ? (inp.getAttribute('title')||'').slice(0, 200) : null,
      input_placeholder: inp ? inp.getAttribute('placeholder') : null,
      aria: inp ? { expanded: inp.getAttribute('aria-expanded'), active: inp.getAttribute('aria-activedescendant'),
                    role: inp.getAttribute('role'), autocomplete: inp.getAttribute('aria-autocomplete') } : null
    };
  })()`);

  fs.writeFileSync(OUT, JSON.stringify(obs, null, 1));
  console.log(JSON.stringify(obs, null, 1));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
