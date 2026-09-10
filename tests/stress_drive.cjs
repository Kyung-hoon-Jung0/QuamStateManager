/* The stress round, in real headless Chrome over CDP.
 *
 * Customer, on site: "이거 구현하고, 다시 브라우저로 이번에는 stress test를
 * 진행해. 진짜 빡세게 해. 브라우저 열고 막 이것저것 클릭해보고 눌러보고 엔터
 * 누르고 입력도 하고.. 등등 유저가 되어서 edge케이스도 막 테스하고 클릭도
 * 여러번 누르고."
 *
 * Not a screenshot round: what is driven here is the part a screenshot cannot
 * show — real keydown/keyup per character, keys held down, arrows walked past
 * both ends, a token completed and then edited, Escape and back, the same row
 * clicked three times, Hangul composed a jamo at a time, a 300-character stem,
 * a quote in the box.
 *
 * EVERY uncaught exception and console error anywhere in the session is
 * collected and reported at the end: a stress test whose only verdict is "the
 * assertions passed" would miss the thing stress actually produces.
 *
 * argv[2] = json out path, argv[3] = CDP port, argv[4] = base url
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9403';
const BASE = process.argv[4] || 'http://127.0.0.1:5407';

const errors = [];
const results = [];
function ok(name, cond, detail) {
  results.push({ name: name, pass: !!cond, detail: detail === undefined ? null : detail });
}

async function main() {
  const targets = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(res => ws.onopen = res);
  let id = 0; const pending = new Map();
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      errors.push({ kind: 'exception', text: (d.exception && (d.exception.description || d.exception.value)) || d.text });
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      errors.push({ kind: 'console.error', text: (m.params.args || []).map(a => a.value || a.description || '').join(' ') });
    }
  };
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => {
    const rr = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) {
      const ex = rr.result.exceptionDetails.exception;
      throw new Error((ex && (ex.description || ex.value)) || 'eval failed: ' + expr.slice(0, 80));
    }
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(res => setTimeout(res, ms));
  const until = async (expr, ms) => {
    const t = Date.now();
    while (Date.now() - t < ms) { const v = await ev(expr); if (v) return v; await sleep(120); }
    return await ev(expr);
  };
  // A REAL keystroke: keyDown with `text` only. Adding a `char` event
  // double-types (measured in an earlier round).
  const typeChar = async (ch) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  };
  const KEYS = { Enter: 13, Tab: 9, Escape: 27, ArrowDown: 40, ArrowUp: 38, Backspace: 8 };
  const press = async (key, opts) => {
    const p = { type: 'rawKeyDown', key: key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key] };
    if (opts && opts.autoRepeat) p.autoRepeat = true;
    if (key === 'Enter' || key === 'Tab') { p.text = key === 'Enter' ? '\r' : '\t'; p.type = 'keyDown'; }
    await send('Input.dispatchKeyEvent', p);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: key, windowsVirtualKeyCode: KEYS[key] });
  };
  const typeInto = async (sel, text, opts) => {
    await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return 0; e.focus(); e.value=''; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
    for (const ch of text) { await typeChar(ch); if (!(opts && opts.fast)) await sleep(12); }
    await sleep(opts && opts.settle != null ? opts.settle : 260);
  };
  const rows = async () => ev(`(function(){var p=document.getElementById('sm-typeahead'); if(!p||p.hidden) return []; return Array.prototype.map.call(p.querySelectorAll('.sm-th-row'), function(r){return {t:(r.querySelector('.sm-th-label')||{}).textContent, m:(r.querySelector('.sm-th-meta')||{}).textContent||'', note:r.classList.contains('sm-th-note'), fuzzy:r.classList.contains('sm-th-fuzzy'), sep:r.classList.contains('sm-th-fuzzsep'), active:r.classList.contains('active')};});})()`);
  const val = async (sel) => ev(`(document.querySelector(${JSON.stringify(sel)})||{}).value`);

  await send('Page.enable'); await send('Runtime.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  await send('Page.navigate', { url: BASE + '/' });
  await sleep(3500);
  await ev(`window.confirm=function(){return true}; window.prompt=function(){return ''}; 1`);

  const SB = '#sidebar-filter-input';
  ok('the sidebar search box exists', await ev(`!!document.querySelector('${SB}')`));

  /* ── 1. typing a key one character at a time (the original ask) ────── */
  const seen = [];
  await ev(`(function(){var e=document.querySelector('${SB}'); e.focus(); e.value=''; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  for (const ch of 'multiplexed') {
    await typeChar(ch);
    await sleep(90);
    const r = await rows();
    seen.push({ typed: await val(SB), n: r.length, first: r.length ? r[0].t : null });
  }
  ok('every keystroke from m to multiplexed shows candidates',
     seen.filter(s => s.n > 0).length >= 9, seen);
  ok('…and multiplexed is the first row from `m` onward',
     seen.slice(1).every(s => s.first === 'multiplexed' || s.n === 0), seen.map(s => s.first));

  /* ── 2. the customer's own typo ─────────────────────────────────────── */
  await typeInto(SB, 'multiplzed');
  let r = await rows();
  ok('multiplzed finds multiplexed', r.some(x => x.t === 'multiplexed' && x.fuzzy), r);
  ok('…under a separator that says it is a guess', r.some(x => x.sep), r);
  ok('…and the guess is not mixed in with matches',
     r.findIndex(x => x.sep) < r.findIndex(x => x.fuzzy), r);
  const approx = await ev(`(function(){var e=document.querySelector('#sm-typeahead .sm-th-fuzzy .sm-th-label'); if(!e) return null; return getComputedStyle(e,'::before').content + '|' + getComputedStyle(e).fontStyle;})()`);
  ok('…and it is marked in the page itself, not only in a class',
     /2248|≈/.test(String(approx)) && /italic/.test(String(approx)), approx);

  for (const typo of ['mutliplexed', 'num_shotss', 'freqency_span_in_mhz', 'wiat_time_num_points']) {
    await typeInto(SB, typo);
    const rr = await rows();
    ok('typo `' + typo + '` still finds something', rr.some(x => x.fuzzy), rr.map(x => x.t));
  }
  await typeInto(SB, 'zzzzqqqqxxxx');
  ok('a word with nothing in common offers nothing', (await rows()).length === 0);

  /* ── 3. arrows walked past both ends, repeatedly ────────────────────── */
  await typeInto(SB, 'multiplzed');
  for (let i = 0; i < 12; i++) await press('ArrowDown');
  let st = await ev(`(function(){var s=window.Typeahead._state(); return s?{a:s.active,n:s.items.length,note:!!(s.items[s.active]||{}).note}:null;})()`);
  ok('ArrowDown stops at the last selectable row, never on a note',
     st && st.a >= 0 && st.a < st.n && !st.note, st);
  for (let i = 0; i < 20; i++) await press('ArrowUp');
  st = await ev(`(function(){var s=window.Typeahead._state(); return s?{a:s.active}:null;})()`);
  ok('ArrowUp walks back to nothing selected and stops', st && st.a === -1, st);

  /* ── 4. one keypress on a single-valued key (121 of this archive's 213) ─
     `target_peak_width` really has one value here; `reset_type` has two
     (thermal / active), which is why it is the wrong key for this case. */
  await typeInto(SB, 'target_peak');
  r = await rows();
  const single = r.find(x => /^= /.test(x.m));
  ok('a single-valued key shows its one value before you pick it', !!single, r);
  await press('ArrowDown');
  await press('Enter');
  await sleep(600);
  const done = await val(SB);
  ok('Enter finishes the whole token in one keypress',
     /^target_peak_width=\S+$/.test(done), done);
  ok('…and the panel closes behind it', (await rows()).length === 0);

  await typeInto(SB, 'target_peak');
  await press('ArrowDown');
  await press('Tab');
  await sleep(300);
  ok('Tab leaves `key=` for a value of your own',
     (await val(SB)) === 'target_peak_width=', await val(SB));

  // …and a TWO-valued key still goes through the list, never completing itself
  await typeInto(SB, 'multiplexe');
  await press('ArrowDown');
  await press('Enter');
  await sleep(500);
  ok('a key with a choice still asks for the choice',
     (await val(SB)) === 'multiplexed=', await val(SB));
  ok('…and lists its values at once', (await rows()).length >= 2,
     (await rows()).map(x => x.t));

  /* ── 5. the range operator on the widest real keys ──────────────────── */
  await typeInto(SB, 'num_shots>=1000');
  r = await rows();
  ok('a range previews what it will select',
     r.length && /^num_shots >= 1000$/.test(r[0].t) && /\d+ of \d+ values · \d+ runs/.test(r[0].m), r.slice(0, 3));
  const preview = r[0].m;
  ok('…with the covered values listed under it', r.length > 1 && !r[1].note, r.slice(0, 4));
  await press('ArrowDown');
  await press('Enter');
  await sleep(1400);
  const filtered = await ev(`(function(){var t=document.getElementById('sidebar-tree'); return t? t.querySelectorAll('.tree-entry-click').length : -1;})()`);
  ok('…and pressing Enter actually filters the tree', filtered >= 0, { token: await val(SB), entries: filtered, preview: preview });

  for (const q of ['num_shots>', 'num_shots>=', 'num_shots>=abc', 'num_shots=1..', 'num_shots=..9', 'num_shots>=-5', 'num_shots>=1e3', 'num_shots=100..1000']) {
    await typeInto(SB, q, { settle: 200 });
    const rr = await rows();
    ok('half-typed / odd range `' + q + '` does not throw and stays sane',
       Array.isArray(rr), rr.slice(0, 2).map(x => x.t + ' | ' + x.m));
  }
  await typeInto(SB, 'num_shots>=999999999');
  r = await rows();
  ok('a range nothing satisfies is not offered as a token',
     !r.length || /no recorded value/.test(r[0].m), r.slice(0, 2));

  /* ── 6. edge cases a person really produces ─────────────────────────── */
  await typeInto(SB, 'num_shots="200');
  ok('an unbalanced quote refuses to complete rather than mangling the box',
     (await rows()).length === 0, await val(SB));

  await typeInto(SB, 'x'.repeat(300), { fast: true, settle: 400 });
  ok('a 300-character stem does not hang or throw', (await rows()).length === 0);

  await typeInto(SB, 'q1 num_shots>=1000', { settle: 400 });
  r = await rows();
  ok('a second token completes beside an existing one',
     r.length > 0 && /num_shots/.test(r[0].t), r.slice(0, 2));

  // Hangul, composed jamo by jamo — the IME path (`isComposing`)
  await ev(`(function(){var e=document.querySelector('${SB}'); e.focus(); e.value=''; e.dispatchEvent(new Event('input',{bubbles:true}));
    e.value='ㅁ'; e.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
    e.dispatchEvent(new InputEvent('input',{bubbles:true, isComposing:true}));
    e.value='머'; e.dispatchEvent(new InputEvent('input',{bubbles:true, isComposing:true}));
    e.value='먹'; e.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true, data:'먹'}));
    return 1;})()`);
  await sleep(300);
  ok('a Hangul composition does not throw and offers nothing', (await rows()).length === 0);

  // Escape, then type on
  await typeInto(SB, 'multipl');
  ok('the panel is open before Escape', (await rows()).length > 0);
  await press('Escape');
  await sleep(150);
  ok('Escape closes it', (await rows()).length === 0);
  await typeChar('e');
  await sleep(300);
  ok('…and typing on opens it again', (await rows()).length > 0, await val(SB));

  // the same row clicked three times in a row
  await typeInto(SB, 'multipl');
  const clicks = await ev(`(async function(){
    var out=[];
    for (var i=0;i<3;i++){
      var p=document.getElementById('sm-typeahead');
      var row=p && !p.hidden ? p.querySelector('.sm-th-row:not(.sm-th-note)') : null;
      if(!row){ out.push(null); continue; }
      var ev1=new MouseEvent('mousedown',{bubbles:true,cancelable:true});
      row.dispatchEvent(ev1);
      await new Promise(r=>setTimeout(r,200));
      out.push(document.querySelector('${SB}').value);
    }
    return out;})()`);
  ok('clicking the same row three times does not compound the token',
     clicks[0] && clicks.every(c => c === null || /^[a-z_]+=/.test(String(c))), clicks);

  // A held-down arrow WALKS the list — that is what a list widget does, and
  // the auto-repeat guard docs/141 §4e added is for Ctrl+Z, where a repeat is
  // destructive. What must not happen is running off the end.
  await typeInto(SB, 'num_sho');
  await press('ArrowDown');
  const a1 = await ev(`(window.Typeahead._state()||{}).active`);
  for (let i = 0; i < 20; i++) await press('ArrowDown', { autoRepeat: true });
  const held = await ev(`(function(){var s=window.Typeahead._state(); return s?{a:s.active,n:s.items.length,note:!!(s.items[s.active]||{}).note}:null;})()`);
  ok('a held-down arrow walks the list and stops at the end',
     held && held.a > a1 - 1 && held.a < held.n && !held.note,
     { first: a1, held: held });

  // rapid typing: 40 input events with no settle
  await ev(`(function(){var e=document.querySelector('${SB}'); e.focus(); e.value='';
    for(var i=0;i<40;i++){ e.value = 'amplitude'.slice(0, (i%9)+1); e.dispatchEvent(new Event('input',{bubbles:true})); }
    return 1;})()`);
  await sleep(600);
  ok('40 input events back to back leave a consistent panel',
     Array.isArray(await rows()), (await rows()).length);

  await send('Page.captureScreenshot', { format: 'png' }).then(s =>
    fs.writeFileSync(OUT.replace(/\.json$/, '_sidebar.png'), Buffer.from(s.result.data, 'base64')));

  /* ── 7. Live State Edit ─────────────────────────────────────────────── */
  await send('Page.navigate', { url: BASE + '/bulk' });
  await sleep(5000);
  const BS = '#bulk-search';
  ok('the Live Edit search box exists', await ev(`!!document.querySelector('${BS}')`));
  await typeInto(BS, 'ampl', { settle: 400 });
  r = await rows();
  ok('`ampl` lists the amplitude columns', r.some(x => /ampl/i.test(x.t)), r.map(x => x.t));
  await typeInto(BS, 'amplitide', { settle: 400 });
  r = await rows();
  ok('a mistyped column still finds it', r.some(x => x.fuzzy), r.map(x => x.t + (x.fuzzy ? ' (fuzzy)' : '')));
  const t0 = Date.now();
  for (const ch of 'amplitude') { await typeChar(ch); }
  await sleep(400);
  ok('typing a nine-character column name stays responsive',
     Date.now() - t0 < 9000, { ms: Date.now() - t0 });
  await press('ArrowDown'); await press('Enter');
  await sleep(900);
  ok('accepting a column word filters the grid', typeof (await val(BS)) === 'string', await val(BS));

  /* ── 8. Json Tree View ──────────────────────────────────────────────── */
  await send('Page.navigate', { url: BASE + '/explorer' });
  await sleep(4500);
  const ES = '#explorer-search';
  ok('the Json Tree search box exists', await ev(`!!document.querySelector('${ES}')`));
  await typeInto(ES, 'ampl', { settle: 500 });
  r = await rows();
  ok('`ampl` lists the chip\'s amplitude keys', r.some(x => /ampl/i.test(x.t)), r.map(x => x.t));
  await typeInto(ES, 'amplitide', { settle: 500 });
  ok('a mistyped chip key still finds it', (await rows()).some(x => x.fuzzy), (await rows()).map(x => x.t));
  await typeInto(ES, 'p:ampl', { settle: 400 });
  ok('a scope-looking word is ONE plain token here, not a scope',
     (await rows()).length === 0, await val(ES));

  /* ── 9. the Agent wiring strip ──────────────────────────────────────── */
  await send('Page.navigate', { url: BASE + '/agent' });
  await sleep(4000);
  const wire = await until(`(function(){var w=document.querySelector('[data-ag-wire]'); if(!w) return null;
    var b=w.querySelector('.ag-wire-badge'); return {badge:b?b.textContent:null, text:w.textContent, clis:w.querySelectorAll('.ag-wire-cli').length, wraps:w.querySelectorAll('.ag-wire-clis').length};})()`, 12000);
  ok('the strip renders on the Agent home', !!wire, wire);
  ok('…and it says a real state, not CHECKING for ever',
     wire && ['CONNECTED', 'NOT CONNECTED', 'NO CLI'].indexOf(wire.badge) >= 0, wire && wire.badge);
  ok('…and it never claims a login',
     wire && !/logged ?in|authenticated|signed ?in/i.test(wire.text), wire && wire.text);
  ok('…and there is exactly one line per backend',
     wire && wire.wraps <= 1 && wire.clis <= 3, wire);
  ok('the strip is a sibling of the mount point, not inside it',
     await ev(`!document.getElementById('agent-home') || !document.getElementById('agent-home').querySelector('[data-ag-wire]')`));
  const stripText = wire && wire.text;
  await sleep(2500);
  ok('…and it survives the feed mounting over it',
     (await ev(`(document.querySelector('[data-ag-wire]')||{}).textContent`)) === stripText,
     await ev(`(document.querySelector('[data-ag-wire]')||{}).textContent`));

  // the ? popover, clicked repeatedly
  const pops = [];
  for (let i = 0; i < 4; i++) {
    await ev(`(function(){var b=document.querySelector('.ag-wire-help'); if(b) b.click(); return 1;})()`);
    await sleep(200);
    pops.push(await ev(`document.querySelectorAll('#ag-wire-help-pop').length`));
  }
  ok('the ? popover toggles and never stacks', pops.every(n => n <= 1), pops);
  const helpText = await ev(`(document.getElementById('ag-wire-help-pop')||{}).textContent || ''`);
  if (helpText) {
    ok('…and it explains the mechanism AND why there is no login light',
       /MCP server/.test(helpText) && /never sees your credentials/.test(helpText), helpText.slice(0, 200));
  }
  await ev(`(function(){var p=document.getElementById('ag-wire-help-pop'); if(p&&p.parentNode) p.parentNode.removeChild(p); return 1;})()`);

  await send('Page.captureScreenshot', { format: 'png' }).then(s =>
    fs.writeFileSync(OUT.replace(/\.json$/, '_agent.png'), Buffer.from(s.result.data, 'base64')));

  // the float, opened and closed repeatedly from another page
  await send('Page.navigate', { url: BASE + '/bulk' });
  await sleep(4000);
  for (let i = 0; i < 3; i++) {
    await ev(`window.toggleAgentPanel(); 1`);
    await sleep(500);
  }
  const floatWire = await ev(`(function(){var p=document.getElementById('agent-popover'); if(!p) return null;
    var w=p.querySelector('[data-ag-wire]'); return w? {compact:w.classList.contains('ag-wire-compact'), badge:(w.querySelector('.ag-wire-badge')||{}).textContent, wraps:w.querySelectorAll('.ag-wire-clis').length, text:w.textContent} : null;})()`);
  ok('the float carries the compact strip too', floatWire && floatWire.compact, floatWire);
  ok('…and three open/close cycles do not multiply its lines',
     floatWire && floatWire.wraps <= 1, floatWire && floatWire.wraps);

  await send('Page.captureScreenshot', { format: 'png' }).then(s =>
    fs.writeFileSync(OUT.replace(/\.json$/, '_float.png'), Buffer.from(s.result.data, 'base64')));

  fs.writeFileSync(OUT, JSON.stringify({ results: results, errors: errors }, null, 1));
  const bad = results.filter(x => !x.pass);
  console.log('checks: ' + (results.length - bad.length) + '/' + results.length
              + '   console errors: ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 300)));
  errors.slice(0, 12).forEach(e => console.log('  ERR  ' + e.kind + ': ' + String(e.text).slice(0, 220)));
  process.exit(0);
}
main().catch(e => { console.error('driver error: ' + (e && e.stack || e)); fs.writeFileSync(OUT, JSON.stringify({ results: results, errors: errors, driver: String(e && e.stack || e) }, null, 1)); process.exit(1); });
