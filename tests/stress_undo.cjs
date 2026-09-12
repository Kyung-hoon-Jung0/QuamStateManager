/* Ctrl+Z, which now WRITES the chip (docs/160) — a hostile round in real
 * headless Chrome, against a COPY of the customer 20Q chip.
 *
 * The oracle is not the screen: after every gesture the driver reads the chip's
 * OWN state.json / wiring.json off disk, diffs them against the previous
 * snapshot, and asks the covenant's question — did anything change, and does a
 * press the person could see account for every byte of it?
 *
 * argv[2] = json out path, argv[3] = CDP port, argv[4] = base url,
 * argv[5] = comma-separated phases (default all), argv[6] = chip folder
 */
const fs = require('fs');
const path = require('path');
const OUT = process.argv[2] || 'undo.json';
const CDP = process.argv[3] || '9462';
const BASE = process.argv[4] || 'http://127.0.0.1:5462';
const PHASES = (process.argv[5] || 'A,B,C,D,E,F,G,H,I,J').split(',').map(s => s.trim());
const CHIP = process.argv[6] || 'C:/Users/KyunghoonJung/AppData/Local/Temp/claude/D--work-statemanager/dd0fa2c3-e492-405d-8783-2c62cd30ba4a/scratchpad/chip_5462/quam_state';
const SHOTDIR = OUT.replace(/[^\\/]*$/, '');

const errors = [];
const results = [];
const notes = {};
const fileEvents = [];       // every observed file change, with the gesture that preceded it
function ok(name, cond, detail) {
  results.push({ name: name, pass: !!cond, detail: detail === undefined ? null : detail });
  if (!cond) console.log('  FAIL  ' + name + '  ' + JSON.stringify(detail || null).slice(0, 400));
  else console.log('  ok    ' + name);
  return !!cond;
}

/* ── the file oracle ─────────────────────────────────────────────────── */
function readChip() {
  const out = {};
  for (const f of ['state.json', 'wiring.json']) {
    const p = path.join(CHIP, f);
    let txt = null;
    for (let i = 0; i < 8; i++) {
      try { txt = fs.readFileSync(p, 'utf8'); break; } catch (e) { }
    }
    out[f] = txt;
  }
  return out;
}
function flat(obj, prefix, acc) {
  acc = acc || {};
  if (obj !== null && typeof obj === 'object' && !Array.isArray(obj)) {
    for (const k of Object.keys(obj)) flat(obj[k], prefix ? prefix + '.' + k : k, acc);
  } else {
    acc[prefix] = Array.isArray(obj) ? JSON.stringify(obj) : obj;
  }
  return acc;
}
function snap(tag) {
  // Memory: a parsed 435 KB state.json flattens to a big object, and a phase
  // holds a dozen snapshots — keep ONLY the flat maps and a hash, never the
  // raw text (an earlier run of this driver died with the whole session's raw
  // strings still reachable).
  const raw = readChip();
  const s = { tag: tag, at: Date.now(), sha: {}, flat: {} };
  for (const f of ['state.json', 'wiring.json']) {
    s.sha[f] = raw[f] == null ? null
      : require('crypto').createHash('sha256').update(raw[f]).digest('hex').slice(0, 16);
  }
  try { s.flat.state = flat(JSON.parse(raw['state.json']), ''); } catch (e) { s.flat.state = { __unparsable: String(e) }; }
  try { s.flat.wiring = flat(JSON.parse(raw['wiring.json']), ''); } catch (e) { s.flat.wiring = { __unparsable: String(e) }; }
  return s;
}
function diff(a, b) {
  const out = [];
  for (const file of ['state', 'wiring']) {
    const A = a.flat[file] || {}, B = b.flat[file] || {};
    const keys = new Set(Object.keys(A).concat(Object.keys(B)));
    for (const k of keys) {
      const x = A[k], y = B[k];
      if (x === y) continue;
      if (typeof x === 'number' && typeof y === 'number' && Number.isNaN(x) && Number.isNaN(y)) continue;
      out.push({ file: file, path: k, from: x === undefined ? '<absent>' : x, to: y === undefined ? '<absent>' : y });
    }
  }
  return out;
}
const PRISTINE = (function () {
  const dir = CHIP.replace(/quam_state\/?$/, 'pristine');
  const raw = {};
  for (const f of ['state.json', 'wiring.json']) {
    try { raw[f] = fs.readFileSync(path.join(dir, f), 'utf8'); } catch (e) { raw[f] = null; }
  }
  const s = { tag: 'pristine', at: 0, sha: {}, flat: {} };
  try { s.flat.state = flat(JSON.parse(raw['state.json']), ''); } catch (e) { s.flat.state = {}; }
  try { s.flat.wiring = flat(JSON.parse(raw['wiring.json']), ''); } catch (e) { s.flat.wiring = {}; }
  return s;
})();
let _last = null;
function step(label) {                 // diff against the previous snapshot
  const now = snap(label);
  const d = _last ? diff(_last, now) : [];
  _last = now;
  fileEvents.push({ label: label, at: now.at, changed: d.length, diff: d.slice(0, 40) });
  return d;
}

async function main() {
  const targets = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(res => ws.onopen = res);
  let id = 0; const pending = new Map();
  const reqs = [];
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      errors.push({ kind: 'exception', at: Date.now(), text: (d.exception && (d.exception.description || d.exception.value)) || d.text });
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      errors.push({ kind: 'console.error', at: Date.now(), text: (m.params.args || []).map(a => a.value || a.description || '').join(' ') });
    }
    if (m.method === 'Network.requestWillBeSent') reqs.push({ t: Date.now(), url: m.params.request.url, method: m.params.request.method });
  };
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => {
    const rr = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) {
      const ex = rr.result.exceptionDetails.exception;
      throw new Error((ex && (ex.description || ex.value)) || 'eval failed: ' + expr.slice(0, 140));
    }
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(res => setTimeout(res, ms));
  const until = async (expr, ms) => {
    const t = Date.now();
    while (Date.now() - t < ms) { const v = await ev(expr); if (v) return v; await sleep(120); }
    return await ev(expr);
  };
  const typeChar = async (ch) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  };
  const KEYS = { Enter: 13, Tab: 9, Escape: 27, ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39, Backspace: 8, Delete: 46, z: 90, Z: 90, s: 83, End: 35, Home: 36 };
  const press = async (key, mods, opts) => {
    const p = { type: 'rawKeyDown', key: key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key] };
    if (mods) p.modifiers = mods;              // 2 = Ctrl, 8 = Shift, 10 = Ctrl+Shift
    if (opts && opts.autoRepeat) p.autoRepeat = true;
    if (key === 'Enter' || key === 'Tab') { p.text = key === 'Enter' ? '\r' : '\t'; p.type = 'keyDown'; }
    await send('Input.dispatchKeyEvent', p);
    if (!(opts && opts.noUp))
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: key, windowsVirtualKeyCode: KEYS[key], modifiers: mods || 0 });
  };
  const ctrlZ = (o) => press('z', 2, o);
  const ctrlShiftZ = (o) => press('Z', 10, o);
  const shot = async (tag) => {
    try {
      const s = await send('Page.captureScreenshot', { format: 'png' });
      const p = SHOTDIR + 'un_' + tag + '.png';
      fs.writeFileSync(p, Buffer.from(s.result.data, 'base64'));
      return p;
    } catch (e) { return null; }
  };
  const clickSel = async (sel, nth) => {
    const box = await ev(`(function(){var els=document.querySelectorAll(${JSON.stringify(sel)});
      var e=els[${nth || 0}]; if(!e) return null; e.scrollIntoView({block:'center',inline:'center'});
      var r=e.getBoundingClientRect(); if(r.width<=0||r.height<=0) return {off:1};
      return {x:r.left+r.width/2,y:r.top+r.height/2};})()`);
    if (!box || box.off) return false;
    if (box.x < 0 || box.y < 0 || box.x > 1500 || box.y > 1000) return false;
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    return true;
  };
  // the tray's OWN declared change count (the attribute the app itself keys on)
  const trayN = () => ev(`(function(){var t=document.getElementById('pending-tray');
      if(!t) return null; var v=t.getAttribute('data-change-count');
      return v==null? null : +v;})()`);
  const trayTxt = () => ev(`(function(){var e=document.querySelector('.tray-indicator'); return e? e.textContent.replace(/\\s+/g,' ').trim():null;})()`);
  const toasts = () => ev(`(function(){return Array.prototype.map.call(document.querySelectorAll('.toast, #toast-container > *'), function(t){return t.textContent.replace(/\\s+/g,' ').trim();});})()`);
  // the app's last toast text, captured cumulatively by a hook we install
  const lastToasts = () => ev(`(window.__seenToasts||[]).slice(-8)`);
  const installToastHook = () => ev(`(function(){ if (window.__toastHooked) return 1;
      window.__seenToasts=[]; var f=window.showToast;
      window.showToast=function(msg, lvl){ try{window.__seenToasts.push(String(lvl||'')+': '+String(msg));}catch(e){}
        return f.apply(this, arguments); };
      window.__toastHooked=1; return 1;})()`);
  const quiet = async (ms) => {            // wait until no XHR for ms, cap 12s
    const t0 = Date.now();
    while (Date.now() - t0 < 12000) {
      const n = reqs.length; await sleep(ms);
      if (reqs.length === n) return true;
    }
    return false;
  };

  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  /* ═══ boot ═════════════════════════════════════════════════════════ */
  _last = snap('boot');
  notes.boot_hash = _last.sha['state.json'];
  await send('Page.navigate', { url: BASE + '/bulk' });
  const mounted = await until(`!!(document.getElementById('bulk-table') && window.UndoQueue)`, 40000);
  await sleep(2500);
  await ev(`window.confirm=function(){return true}; window.prompt=function(){return ''}; window.alert=function(){}; 1`);
  await installToastHook();
  ok('the grid mounts and UndoQueue exists', !!mounted);
  const undoLiveOn = await ev(`(function(){var b=document.getElementById('undo-live-toggle'); return b? b.getAttribute('data-on'):null;})()`);
  ok('“Ctrl+Z writes live” is ON by default', undoLiveOn === '1', undoLiveOn);
  let d = step('after load');
  ok('loading the chip wrote nothing', d.length === 0, d);

  /* a helper: edit a grid cell by (qubit,column) and Enter-commit it */
  const SEL = (q, col) => 'tr[data-qubit="' + q + '"] td[data-col-key="' + col + '"] input.bulk-cell';
  const cellInfo = (q, col) => ev(`(function(){
     var c=document.querySelector(${JSON.stringify(SEL(q, col))});
     if(!c) return null;
     return {v:c.value, path:c.getAttribute('data-dot-path'), resolved:c.getAttribute('data-resolved'),
             orig:c.getAttribute('data-orig'), ro:c.readOnly,
             mod:c.classList.contains('bulk-cell-modified')};})()`);
  const editCell = async (q, col, value) => {
    const okf = await ev(`(function(){var c=document.querySelector(${JSON.stringify(SEL(q, col))});
       if(!c||c.readOnly) return 0; var td=c.closest('td'); if(td) td.classList.remove('bulk-col-hidden');
       c.scrollIntoView({block:'center',inline:'center'}); c.focus(); c.select(); return 1;})()`);
    if (!okf) return false;
    await sleep(120);
    // clear with real keys
    for (let i = 0; i < 26; i++) await press('Backspace');
    for (const ch of String(value)) await typeChar(ch);
    await sleep(80);
    await press('Enter');
    await sleep(700);
    return true;
  };
  const applyLive = async () => {
    const clicked = await clickSel('.btn-apply-live');
    if (!clicked) return false;
    await sleep(600);
    await until(`!window._applyInFlight`, 15000);
    await quiet(450);
    await sleep(400);
    return true;
  };

  /* A CLEAN START: a leftover unapplied edit in the tray would ride the next
     Apply and make every diff lie about which gesture wrote what. Discard
     anything pending before the phases begin, and say what was found. */
  const trayAtBoot = await trayN();
  notes.tray_at_boot = trayAtBoot;
  if (trayAtBoot && trayAtBoot > 0) {
    await ev(`(function(){ if(!window.htmx) return 0;
       htmx.ajax('POST','/discard_all',{target:'#pending-tray',swap:'outerHTML'}); return 1;})()`);
    await sleep(2500);
    notes.tray_after_discard = await trayN();
  }
  d = step('boot: tray cleared');
  ok('a leftover tray is discarded without writing the chip', d.length === 0,
     { boot: trayAtBoot, after: notes.tray_after_discard, diff: d });

  const P = (x) => PHASES.indexOf(x) >= 0;
  const num = (v) => Number(String(v).replace(/,/g, ''));

  /* pick a stable numeric column present on the qubit grid */
  const cols = await ev(`(function(){var t=document.getElementById('bulk-table');
     return Array.prototype.map.call(t.querySelectorAll('thead th[data-col-key]'), function(h){return h.getAttribute('data-col-key');}).filter(Boolean).slice(0,400);})()`);
  notes.some_cols = cols.slice(0, 40);
  // a fixed list of INDEPENDENT numeric columns (no known mirror/bundle), one
  // qubit each so no two probes collide on the same leaf
  const WANT = ['T1', 'T2ramsey', 'T2echo', 'readout_threshold', 'chi', 'anharmonicity',
                'readout_iw_angle', 'depletion_time', 'z_settle_time', 'gate_fidelity_avg'];
  // the wanted columns may be SERVER-COLD (empty td, value in the cold map) —
  // hydrate them through the grid's own API before probing
  await ev(`(function(){var ks=${JSON.stringify(WANT)};
     if(!(window.BulkEdit && BulkEdit.hydrateColumn)) return 0;
     return Promise.all(ks.map(function(k){ return BulkEdit.hydrateColumn(k).catch(function(){return 0;}); }))
       .then(function(){return 1;});})()`);
  await sleep(900);
  const probe = await ev(`(function(){
     var want=${JSON.stringify(WANT)}; var rows=Array.prototype.map.call(
       document.querySelectorAll('#bulk-table tbody tr[data-qubit]'), function(r){return r.getAttribute('data-qubit');});
     var out=[];
     for (var i=0;i<want.length;i++){
       for (var j=0;j<rows.length;j++){
         var td=document.querySelector('tr[data-qubit="'+rows[j]+'"] td[data-col-key="'+want[i]+'"]');
         if(!td) continue;
         var c=td.querySelector('input.bulk-cell');
         if(!c||c.readOnly||c.getAttribute('data-is-pointer')) continue;
         var num=Number(String(c.value).replace(/,/g,''));
         if (!isFinite(num) || String(c.value).trim()==='' || Math.abs(num)<1e-12) continue;
         out.push({q:rows[j], col:want[i], path:c.getAttribute('data-dot-path'),
                   resolved:c.getAttribute('data-resolved'), v:String(c.value), num:num,
                   hidden: td.classList.contains('bulk-col-hidden')});
         break;
       }
     }
     return out;})()`);
  notes.numeric_cells = probe;

  /* ═══ A · edit → Apply → Ctrl+Z → Ctrl+Shift+Z ═════════════════════ */
  if (P('A')) {
    console.log('--- PHASE A');
    const target = probe.find(p => p.col && p.col.indexOf('f_01') < 0) || probe[0];
    notes.A_target = target;
    const before = await cellInfo(target.q, target.col);
    const newVal = String(Number((num(before.v) * 1.013).toPrecision(12)));
    await editCell(target.q, target.col, newVal);
    const t1 = await trayN();
    d = step('A: edit staged (not applied)');
    ok('A1 an edit alone writes NOTHING to the chip', d.length === 0, { tray: t1, diff: d });
    ok('A1b …and the tray says there is something unapplied', t1 >= 1, t1);

    await applyLive();
    d = step('A: Apply to live');
    notes.A_apply_diff = d;
    ok('A2 Apply writes exactly the edited leaves', d.length >= 1 && d.length <= 3, d);
    const appliedPaths = d.map(x => x.path);

    await ctrlZ(); await sleep(1400); await quiet(400);
    d = step('A: Ctrl+Z');
    notes.A_undo_diff = d;
    const backPaths = d.map(x => x.path).sort();
    ok('A3 Ctrl+Z writes the chip back', d.length >= 1, d);
    ok('A3b …and it touches exactly the paths the apply did',
      JSON.stringify(backPaths) === JSON.stringify(appliedPaths.slice().sort()), { applied: appliedPaths, undone: backPaths });
    const backToStart = d.every(x => {
      const a = notes.A_apply_diff.find(y => y.path === x.path && y.file === x.file);
      return a && String(a.from) === String(x.to);
    });
    ok('A3c …to the exact values it held before the apply', backToStart, { undo: d, apply: notes.A_apply_diff });
    notes.A_toasts_after_undo = await lastToasts();

    await ctrlShiftZ(); await sleep(1400); await quiet(400);
    d = step('A: Ctrl+Shift+Z');
    notes.A_redo_diff = d;
    ok('A4 Ctrl+Shift+Z writes it forward again', d.length >= 1 && d.every(x => {
      const a = notes.A_apply_diff.find(y => y.path === x.path && y.file === x.file);
      return a && String(a.to) === String(x.to);
    }), { redo: d, apply: notes.A_apply_diff });
    await shot('A_after_redo');
  }

  /* ═══ B · ten in a row, fast ══════════════════════════════════════ */
  if (P('B')) {
    console.log('--- PHASE B');
    // build a real stack: FIVE separate edit→Apply gestures = five journal units
    const base = snap('B: before the five applies');
    const built = [];
    for (let i = 0; i < 5; i++) {
      const t = probe[i % probe.length];
      const cur = await cellInfo(t.q, t.col);
      if (!cur) continue;
      const nv = String(Number((num(cur.v) * (1.05 + 0.01 * i)).toPrecision(12)));
      await editCell(t.q, t.col, nv);
      await applyLive();
      built.push({ q: t.q, col: t.col, path: t.path, val: nv });
    }
    const applied = snap('B: five applied');
    _last = applied;
    notes.B_built = built;
    notes.B_build_diff = diff(base, applied);
    ok('B0 five edit→Apply gestures wrote five (or more) leaves',
      notes.B_build_diff.length >= built.length, notes.B_build_diff);

    // FIVE fast Ctrl+Z presses over the five units: back to where B started
    for (let i = 0; i < 5; i++) { await ctrlZ(); await sleep(150); }
    await sleep(3000); await quiet(600); await sleep(1500);
    const s1 = snap('B: after five fast Ctrl+Z'); _last = s1;
    notes.B_five_undo_vs_base = diff(base, s1);
    notes.B_five_undo_toasts = await lastToasts();
    ok('B1 five fast Ctrl+Z over five units lands exactly where they started',
      notes.B_five_undo_vs_base.length === 0, notes.B_five_undo_vs_base.slice(0, 12));

    for (let i = 0; i < 5; i++) { await ctrlShiftZ(); await sleep(150); }
    await sleep(3000); await quiet(600); await sleep(1500);
    const s2 = snap('B: after five fast Ctrl+Shift+Z'); _last = s2;
    notes.B_five_redo_vs_applied = diff(applied, s2);
    notes.B_five_redo_toasts = await lastToasts();
    ok('B2 five fast Ctrl+Shift+Z puts all five back, byte-for-byte',
      notes.B_five_redo_vs_applied.length === 0, notes.B_five_redo_vs_applied.slice(0, 12));

    // 10 ALTERNATING presses, faster than a round trip
    const s3 = snap('B: before alternating');
    for (let i = 0; i < 10; i++) {
      if (i % 2 === 0) await ctrlZ(); else await ctrlShiftZ();
      await sleep(120);
    }
    await sleep(3500); await quiet(600); await sleep(1200);
    const s4 = snap('B: after 10 alternating'); _last = s4;
    notes.B_alt = diff(s3, s4);
    notes.B_alt_toasts = await lastToasts();
    ok('B3 five undo / five redo alternating leaves the chip where it began',
      notes.B_alt.length === 0, notes.B_alt);

    // WALK PAST THE BOTTOM: 20 fast presses over a journal holding fewer
    // units -- the chip must reach the PRISTINE copy and stop there.
    for (let i = 0; i < 20; i++) { await ctrlZ(); await sleep(130); }
    await sleep(4000); await quiet(700); await sleep(2000);
    const sBottom = snap('B: after 20 fast Ctrl+Z'); _last = sBottom;
    notes.B_bottom_vs_pristine = diff(PRISTINE, sBottom);
    notes.B_bottom_toasts = await lastToasts();
    ok('B4 twenty presses over a shorter journal reach the ORIGINAL chip and stop',
      notes.B_bottom_vs_pristine.length === 0, notes.B_bottom_vs_pristine.slice(0, 12));
    for (let i = 0; i < 3; i++) { await ctrlZ(); await sleep(500); }
    await sleep(2500); await quiet(600);
    const sBottom2 = snap('B: three more presses at the bottom'); _last = sBottom2;
    notes.B_past_bottom = diff(sBottom, sBottom2);
    ok('B4b …and three more presses at the bottom change nothing',
      notes.B_past_bottom.length === 0, notes.B_past_bottom);

    for (let i = 0; i < 20; i++) { await ctrlShiftZ(); await sleep(130); }
    await sleep(4000); await quiet(700); await sleep(2000);
    const sTop = snap('B: after 20 fast redo'); _last = sTop;
    notes.B_top_vs_applied = diff(applied, sTop);
    notes.B_top_toasts = await lastToasts();
    ok('B5 twenty redos walk back up to the applied state, byte-for-byte',
      notes.B_top_vs_applied.length === 0, notes.B_top_vs_applied.slice(0, 12));
    await shot('B_after');
  }

  /* ═══ C · the held key ════════════════════════════════════════════ */
  if (P('C')) {
    console.log('--- PHASE C');
    // build a stack: 4 separate applied edits, each its own leaf
    const built = [];
    for (let i = 0; i < 4; i++) {
      const t = probe[(i + 5) % probe.length];
      const cur = await cellInfo(t.q, t.col);
      if (!cur) continue;
      const nv = String(Number((num(cur.v) * (1.07 + 0.01 * i)).toPrecision(12)));
      const pre = snap('C: before apply ' + i);
      await editCell(t.q, t.col, nv);
      await applyLive();
      const post = snap('C: apply ' + i); _last = post;
      built.push({ q: t.q, col: t.col, path: t.path, val: nv, diff: diff(pre, post) });
    }
    notes.C_built = built.map(b => ({ col: b.col, diff: b.diff }));
    ok('C0 four applies each wrote their own leaf',
      built.length === 4 && built.every(b => b.diff.length === 1), notes.C_built);

    const s0 = snap('C: before the held key'); _last = s0;
    // ONE real keydown, then 25 autoRepeat repeats, then keyUp — a held Ctrl+Z
    await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'z', modifiers: 2, windowsVirtualKeyCode: 90, nativeVirtualKeyCode: 90 });
    for (let i = 0; i < 25; i++) {
      await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'z', modifiers: 2, windowsVirtualKeyCode: 90, nativeVirtualKeyCode: 90, autoRepeat: true });
      await sleep(30);
    }
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'z', modifiers: 2, windowsVirtualKeyCode: 90 });
    await sleep(4000); await quiet(700); await sleep(2500);
    const s1 = snap('C: after the held key'); _last = s1;
    const heldDiff = diff(s0, s1);
    notes.C_held_diff = heldDiff;
    notes.C_held_toasts = await lastToasts();
    const newest = built[built.length - 1];
    ok('C1 a HELD Ctrl+Z walks exactly ONE step, not 26',
      heldDiff.length === 1, heldDiff);
    ok('C1b …and the one step is the newest applied gesture, back to its own old value',
      heldDiff.length === 1 && newest && heldDiff[0].path === newest.diff[0].path
      && String(heldDiff[0].to) === String(newest.diff[0].from),
      { held: heldDiff, newest: newest && newest.diff });
    await shot('C_after_hold');
    notes.C_after_hold_tray = await trayTxt();

    // and a held Ctrl+Shift+Z is one step forward, too
    const s2 = snap('C: before the held redo'); _last = s2;
    await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Z', modifiers: 10, windowsVirtualKeyCode: 90, nativeVirtualKeyCode: 90 });
    for (let i = 0; i < 25; i++) {
      await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Z', modifiers: 10, windowsVirtualKeyCode: 90, nativeVirtualKeyCode: 90, autoRepeat: true });
      await sleep(30);
    }
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Z', modifiers: 10, windowsVirtualKeyCode: 90 });
    await sleep(4000); await quiet(700); await sleep(2500);
    const s3 = snap('C: after the held redo'); _last = s3;
    notes.C_held_redo = diff(s2, s3);
    ok('C2 a HELD Ctrl+Shift+Z walks exactly ONE step forward',
      notes.C_held_redo.length === 1 && String(notes.C_held_redo[0].to) === String(newest.diff[0].to),
      { redo: notes.C_held_redo, newest: newest && newest.diff });
  }

  /* ═══ D · a press during an apply in flight ═══════════════════════ */
  if (P('D')) {
    console.log('--- PHASE D');
    // two known units first, so the two in-flight presses have a target
    const t1 = probe[1], t2 = probe[2];
    const c1 = await cellInfo(t1.q, t1.col);
    const preU1 = snap('D: before unit 1');
    await editCell(t1.q, t1.col, String(Number((num(c1.v) * 1.11).toPrecision(12))));
    await applyLive();
    const afterU1 = snap('D: unit 1 applied'); _last = afterU1;
    notes.D_unit1 = diff(preU1, afterU1);

    const c2 = await cellInfo(t2.q, t2.col);
    const nv2 = String(Number((num(c2.v) * 1.13).toPrecision(12)));
    await editCell(t2.q, t2.col, nv2);
    const preU2 = snap('D: unit 2 staged'); _last = preU2;
    // click apply, then press Ctrl+Z TWICE while the write is in flight
    await clickSel('.btn-apply-live');
    await sleep(70);
    await ctrlZ();
    await sleep(50);
    await ctrlZ();
    await until(`!window._applyInFlight`, 25000);
    await sleep(5000); await quiet(800); await sleep(2500);
    const after = snap('D: apply + 2 presses during it'); _last = after;
    notes.D_vs_preU2 = diff(preU2, after);
    notes.D_vs_preU1 = diff(preU1, after);
    notes.D_toasts = await lastToasts();
    notes.D_tray = await trayTxt();
    // the apply lands, then the two presses walk two units back: unit 2 and
    // unit 1 — so the chip is exactly where it was before unit 1 was applied
    ok('D1 an apply + two Ctrl+Z during its flight = the apply, then two whole steps back',
      notes.D_vs_preU1.length === 0,
      { vs_before_unit1: notes.D_vs_preU1, vs_unit2_staged: notes.D_vs_preU2, tray: notes.D_tray, toasts: notes.D_toasts });
    ok('D1b …and no press was silently swallowed or double-counted',
      notes.D_vs_preU2.length <= 1, notes.D_vs_preU2);
    await shot('D_after');
    // put the two back so later phases start from a known place
    await ctrlShiftZ(); await sleep(1800); await quiet(500);
    await ctrlShiftZ(); await sleep(1800); await quiet(500);
    const back = snap('D: redone'); _last = back;
    notes.D_redo_back = diff(after, back);
  }

  /* ═══ E · focus tiers ═════════════════════════════════════════════ */
  if (P('E')) {
    console.log('--- PHASE E');
    // E1: dirty grid cell — Ctrl+Z must undo the TYPING, not write the chip
    const t = probe[3] || probe[0];
    await ev(`(function(){var c=document.querySelector(${JSON.stringify(SEL(probe[3].q, probe[3].col))});
       if(!c) return 0; c.scrollIntoView({block:'center',inline:'center'}); c.focus(); c.select(); return 1;})()`);
    for (let i = 0; i < 24; i++) await press('Backspace');
    for (const ch of '0.5') await typeChar(ch);
    await sleep(300);
    const s0 = snap('E: dirty cell typed'); _last = s0;
    await ctrlZ(); await sleep(2000); await quiet(500);
    const s1 = snap('E: Ctrl+Z in a dirty cell'); _last = s1;
    notes.E_dirty_diff = diff(s0, s1);
    const cellNow = await cellInfo(t.q, t.col);
    ok('E1 Ctrl+Z with uncommitted typing in a grid cell writes NOTHING to the chip',
      notes.E_dirty_diff.length === 0, notes.E_dirty_diff);
    ok('E1b …and it restores the cell to its committed value',
      cellNow && String(cellNow.v) === String(cellNow.orig), cellNow);

    // E2: a plain text field with uncommitted typing (the search box)
    await ev(`(function(){var e=document.getElementById('bulk-search'); if(!e) return 0; e.focus(); e.value='ampl'; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
    await sleep(600);
    const s2 = snap('E: search typed'); _last = s2;
    await ctrlZ(); await sleep(2000); await quiet(500);
    const s3 = snap('E: Ctrl+Z in the search box'); _last = s3;
    notes.E_search_diff = diff(s2, s3);
    ok('E2 Ctrl+Z inside a text field never reaches the chip', notes.E_search_diff.length === 0, notes.E_search_diff);
    await ev(`(function(){var e=document.getElementById('bulk-search'); if(e){e.value=''; e.dispatchEvent(new Event('input',{bubbles:true}));} return 1;})()`);
    await sleep(900);

    // E3: a CLEAN grid cell focused — the press falls through to the server
    // tier and, by docs/160, writes the chip exactly one step
    await ev(`(function(){var c=document.querySelector(${JSON.stringify(SEL(probe[4].q, probe[4].col))});
       if(!c) return 0; c.scrollIntoView({block:'center',inline:'center'}); c.focus(); return 1;})()`);
    await sleep(400);
    const s4 = snap('E: clean cell focused'); _last = s4;
    await ctrlZ(); await sleep(2500); await quiet(600); await sleep(1200);
    const s5 = snap('E: Ctrl+Z from a clean cell'); _last = s5;
    notes.E_clean_diff = diff(s4, s5);
    notes.E_clean_toasts = await lastToasts();
    ok('E3 Ctrl+Z from a CLEAN grid cell walks the journal (one step, one gesture)',
      notes.E_clean_diff.length >= 1 && notes.E_clean_diff.length <= 3,
      { diff: notes.E_clean_diff, toasts: notes.E_clean_toasts });
    await ctrlShiftZ(); await sleep(2500); await quiet(600); await sleep(1000);
    const s6 = snap('E: redo'); _last = s6;
    notes.E_clean_redo = diff(s5, s6);
    ok('E3b …and Ctrl+Shift+Z puts that step back',
      JSON.stringify(notes.E_clean_redo.map(x => x.path).sort())
      === JSON.stringify(notes.E_clean_diff.map(x => x.path).sort()),
      { undo: notes.E_clean_diff, redo: notes.E_clean_redo });
  }

  /* ═══ F · the refusals ════════════════════════════════════════════ */
  if (P('F')) {
    console.log('--- PHASE F');
    // apply one fresh edit so there is something to undo
    const t = probe[6] || probe[0];
    const cur = await cellInfo(t.q, t.col);
    const nv = String(Number((num(cur.v) * 1.17).toPrecision(12)));
    const preF = snap('F: before the edit'); _last = preF;
    await editCell(t.q, t.col, nv);
    await applyLive();
    d = step('F: edit applied'); notes.F_apply_diff = d;
    ok('F0a the fresh edit reached the chip', d.length === 1, d);

    // ---- the machine-wide setting OFF -------------------------------
    await ev(`window.toggleUndoLive(); 1`);
    await sleep(1200);
    const off = await ev(`(function(){var b=document.getElementById('undo-live-toggle'); return b? b.getAttribute('data-on'):null;})()`);
    ok('F0 the Settings toggle turns “Ctrl+Z writes live” OFF', off === '0', off);
    const s0 = snap('F: setting OFF'); _last = s0;
    await ctrlZ(); await sleep(2500); await quiet(600); await sleep(1200);
    const s1 = snap('F: Ctrl+Z with the setting OFF'); _last = s1;
    notes.F_off_diff = diff(s0, s1);
    notes.F_off_tray = await trayTxt();
    notes.F_off_trayN = await trayN();
    notes.F_off_toasts = await lastToasts();
    ok('F1 with the setting OFF a Ctrl+Z writes NOTHING to the chip', notes.F_off_diff.length === 0, notes.F_off_diff);
    ok('F1b …and it STAGES instead (the tray holds exactly one)', notes.F_off_trayN === 1,
       { tray: notes.F_off_tray, n: notes.F_off_trayN });
    await shot('F_off_staged');

    // Ctrl+Shift+Z un-stages it (still OFF)
    await ctrlShiftZ(); await sleep(2500); await quiet(600); await sleep(1000);
    const s2 = snap('F: Ctrl+Shift+Z un-stages'); _last = s2;
    notes.F_unstage_diff = diff(s1, s2);
    notes.F_unstage_trayN = await trayN();
    ok('F2 Ctrl+Shift+Z un-stages the step and still writes nothing',
      notes.F_unstage_diff.length === 0 && notes.F_unstage_trayN === 0,
      { diff: notes.F_unstage_diff, tray: notes.F_unstage_trayN });

    // stage again, then reach the chip the docs/107 way: an explicit Apply
    await ctrlZ(); await sleep(2500); await quiet(600); await sleep(1000);
    const s3 = snap('F: staged again'); _last = s3;
    notes.F_stage2_diff = diff(s2, s3);
    ok('F3 staging again still writes nothing', notes.F_stage2_diff.length === 0, notes.F_stage2_diff);
    await applyLive();
    const s4 = snap('F: Apply pressed on the staged step'); _last = s4;
    notes.F_apply_staged = diff(s3, s4);
    ok('F4 an explicit Apply then writes exactly that one step back',
      notes.F_apply_staged.length === 1
      && String(notes.F_apply_staged[0].to) === String(notes.F_apply_diff[0].from),
      { applied: notes.F_apply_staged, original: notes.F_apply_diff });

    // ---- back ON ----------------------------------------------------
    await ev(`window.toggleUndoLive(); 1`);
    await sleep(1200);
    const on = await ev(`(function(){var b=document.getElementById('undo-live-toggle'); return b? b.getAttribute('data-on'):null;})()`);
    ok('F5 the toggle goes back ON', on === '1', on);
    const s5 = snap('F: setting back ON'); _last = s5;
    notes.F_toggle_diff = diff(s4, s5);
    ok('F5b …and flipping the setting itself writes nothing', notes.F_toggle_diff.length === 0, notes.F_toggle_diff);
    notes.F_tray_after = await trayTxt();
    notes.F_trayN_after = await trayN();

    // ---- the tray-not-empty gate ------------------------------------
    // setting ON, but a staged jrn: step already sits in the tray: the door
    // pushes the WHOLE working copy, so the walk must STAY staged and say so.
    const s6 = snap('F: before the gate probe'); _last = s6;
    await ctrlZ(); await sleep(2500); await quiet(600); await sleep(1000);   // step 1: writes live (tray empty)
    const s7 = snap('F: gate probe, first press'); _last = s7;
    notes.F_gate_first = diff(s6, s7);
    notes.F_gate_trayN1 = await trayN();
    // now put a NORMAL unapplied edit in the tray, then press again
    const t2 = probe[7] || probe[1];
    const c2 = await cellInfo(t2.q, t2.col);
    await editCell(t2.q, t2.col, String(Number((num(c2.v) * 1.19).toPrecision(12))));
    const s8 = snap('F: a pending edit in the tray'); _last = s8;
    notes.F_pending_trayN = await trayN();
    await ctrlZ(); await sleep(2500); await quiet(600); await sleep(1000);   // undoes the pending edit (tray group)
    await ctrlZ(); await sleep(2500); await quiet(600); await sleep(1200);   // now the journal again
    const s9 = snap('F: two presses over a pending edit'); _last = s9;
    notes.F_gate_two = diff(s8, s9);
    notes.F_gate_toasts = await lastToasts();
    ok('F6 a press over a PENDING tray edit undoes the edit first, then walks the journal',
      notes.F_gate_two.length >= 1, { diff: notes.F_gate_two, toasts: notes.F_gate_toasts });
    notes.F_end_trayN = await trayN();
    // walk back up so later phases start clean
    for (let i = 0; i < 3; i++) { await ctrlShiftZ(); await sleep(1800); await quiet(400); }
    await sleep(1500);
    const s10 = snap('F: walked back up'); _last = s10;
    notes.F_final_trayN = await trayN();
    notes.F_final_tray = await trayTxt();
  }

  /* ═══ G · a gesture that touched several leaves at once ═══════════ */
  if (P('G')) {
    console.log('--- PHASE G');
    // a leftover staged step from an earlier phase would ride this phase's
    // Apply and make the diff unattributable -- start from an empty tray
    if ((await trayN()) > 0) {
      await ev(`(function(){htmx.ajax('POST','/discard_all',{target:'#pending-tray',swap:'outerHTML'}); return 1;})()`);
      await sleep(2500);
    }
    notes.G_tray_at_start = await trayN();
    d = step('G: tray cleared');
    ok('G-pre the tray starts empty and clearing it wrote nothing',
       notes.G_tray_at_start === 0 && d.length === 0, { tray: notes.G_tray_at_start, diff: d });
    // the coupled f_01 <-> xy.RF_frequency mirror: one cell edit, two leaves
    const f01 = await ev(`(function(){
       var td=document.querySelector('#bulk-table tbody td[data-col-key="f_01"]');
       if(!td) return null; var c=td.querySelector('input.bulk-cell'); if(!c||c.readOnly) return null;
       return {q:td.closest('tr').getAttribute('data-qubit'), col:'f_01', v:c.value,
               path:c.getAttribute('data-dot-path')};})()`);
    notes.G_f01 = f01;
    if (f01) {
      const nv = String(num(f01.v) + 1e6);
      await editCell(f01.q, 'f_01', nv);
      const tray = await trayN();
      notes.G_tray_after_edit = tray;
      d = step('G: f_01 edit staged');
      ok('G0 the f_01 edit stages (nothing on the chip yet)', d.length === 0, d);
      await applyLive();
      d = step('G: f_01 applied'); notes.G_apply_diff = d;
      ok('G1 one f_01 gesture writes BOTH leaves (f_01 and its RF_frequency)',
        d.length >= 2, d);
      const s0 = snap('G: applied'); _last = s0;
      await ctrlZ(); await sleep(2000); await quiet(500);
      const s1 = snap('G: one Ctrl+Z'); _last = s1;
      notes.G_undo1 = diff(s0, s1);
      const reverted = notes.G_undo1.map(x => x.path).sort();
      const applied = notes.G_apply_diff.map(x => x.path).sort();
      ok('G2 ONE Ctrl+Z takes the whole gesture back (never half of it)',
        JSON.stringify(reverted) === JSON.stringify(applied),
        { applied: applied, reverted: reverted });
      await shot('G_after_undo');
      // and forward
      await ctrlShiftZ(); await sleep(2000); await quiet(500);
      const s2 = snap('G: redo'); _last = s2;
      notes.G_redo = diff(s1, s2);
      ok('G3 …and one Ctrl+Shift+Z puts the whole gesture back',
        JSON.stringify(notes.G_redo.map(x => x.path).sort()) === JSON.stringify(applied), notes.G_redo);
    } else ok('G0 an f_01 column exists on the grid', false, null);
  }

  /* ═══ H · across a save boundary ══════════════════════════════════ */
  if (P('H')) {
    console.log('--- PHASE H');
    const t = probe[5] || probe[0];
    const cur = await cellInfo(t.q, t.col);
    const nv = String(Number((num(cur.v) * 1.051).toPrecision(12)));
    await editCell(t.q, t.col, nv);
    // Save (working copy), then Apply
    const saved = await ev(`(function(){ if(!window.htmx) return 0;
       htmx.ajax('POST','/save',{target:'#pending-tray',swap:'outerHTML'}); return 1;})()`);
    await sleep(2200); await quiet(500);
    d = step('H: save'); notes.H_save_diff = d;
    ok('H0 a Save writes the working copy, not the chip', d.length === 0, d);
    await applyLive();
    d = step('H: apply after save'); notes.H_apply_diff = d;
    ok('H1 the apply after a save writes the chip', d.length >= 1, d);
    const s0 = snap('H: applied'); _last = s0;
    await ctrlZ(); await sleep(2200); await quiet(500);
    const s1 = snap('H: Ctrl+Z across the save boundary'); _last = s1;
    notes.H_undo = diff(s0, s1);
    notes.H_toasts = await lastToasts();
    ok('H2 Ctrl+Z crosses the save boundary and writes the chip back',
      notes.H_undo.length >= 1 && notes.H_undo.every(x => {
        const a = notes.H_apply_diff.find(y => y.path === x.path);
        return a && String(a.from) === String(x.to);
      }), { undo: notes.H_undo, apply: notes.H_apply_diff });
    await shot('H_after');
  }

  /* ═══ I · the other two focus tiers: inspector + Json tree ════════ */
  if (P('I')) {
    console.log('--- PHASE I');
    await send('Page.navigate', { url: BASE + '/qubit/q1' });
    const mounted2 = await until(`!!(document.querySelector('form.inline-edit input[name="value"]') && document.getElementById('pending-tray'))`, 30000);
    await sleep(1800);
    await ev(`window.confirm=function(){return true}; window.alert=function(){}; 1`);
    await installToastHook();
    ok('I0 the qubit inspector mounts with inline-edit fields and the tray', !!mounted2);

    const fld = await ev(`(function(){
       var ins=document.querySelectorAll('form.inline-edit input[name="value"]');
       for (var i=0;i<ins.length;i++){
         var v=String(ins[i].value).trim();
         // a REAL number, thousands-grouped the way the app groups them --
         // 1,0 (grid_location) is not one, and stripping its comma made the
         // first cut of this driver type garbage into a coordinate field
         if (!/^-?\\d{1,3}(,\\d{3})*(\\.\\d+)?([eE][-+]?\\d+)?$/.test(v)
             && !/^-?\\d+(\\.\\d+)?([eE][-+]?\\d+)?$/.test(v)) continue;
         var n=Number(v.replace(/,/g,''));
         if (!isFinite(n) || Math.abs(n)<1e-12) continue;
         var hh=ins[i].closest('form').querySelector('input[name="dot_path"]');
         if (hh && /grid_location|__class__/.test(hh.value)) continue;
         var f=ins[i].closest('form'); var h=f.querySelector('input[name="dot_path"]');
         return {idx:i, value:v, path:h? h.value:null};
       }
       return null;})()`);
    notes.I_field = fld;
    ok('I0b …and it carries an editable numeric field', !!fld, fld);
    if (fld) {
      // I1: uncommitted typing → Ctrl+Z undoes the TYPING, never the chip
      await ev(`(function(){var ins=document.querySelectorAll('form.inline-edit input[name="value"]');
         var e=ins[${fld.idx}]; e.scrollIntoView({block:'center'}); e.focus(); e.select(); return 1;})()`);
      await sleep(300);
      for (let i = 0; i < 24; i++) await press('Backspace');
      for (const ch of '0.4242') await typeChar(ch);
      await sleep(400);
      const i0 = snap('I: inline field typed'); _last = i0;
      await ctrlZ(); await sleep(2200); await quiet(500);
      const i1 = snap('I: Ctrl+Z in a dirty inline field'); _last = i1;
      notes.I_dirty_diff = diff(i0, i1);
      const after = await ev(`(function(){var ins=document.querySelectorAll('form.inline-edit input[name="value"]');
         var e=ins[${fld.idx}]; return e? {v:e.value, base:e.getAttribute('data-committed')} : null;})()`);
      notes.I_field_after = after;
      ok('I1 Ctrl+Z with uncommitted typing in the INSPECTOR writes nothing to the chip',
        notes.I_dirty_diff.length === 0, notes.I_dirty_diff);
      ok('I1b …and the field goes back to its committed value',
        after && String(num(after.v)) === String(num(fld.value)), { after: after, was: fld.value });

      // I2: commit + Apply, then a CLEAN inline field → one live step
      await ev(`(function(){var ins=document.querySelectorAll('form.inline-edit input[name="value"]');
         var e=ins[${fld.idx}]; e.focus(); e.select(); return 1;})()`);
      for (let i = 0; i < 24; i++) await press('Backspace');
      const iv = String(Number((num(fld.value) * 1.23).toPrecision(12)));
      for (const ch of iv) await typeChar(ch);
      await press('Enter');
      await sleep(2500); await quiet(600);
      const i2 = snap('I: inline committed'); _last = i2;
      notes.I_commit_diff = diff(i1, i2);
      notes.I_commit_tray = await trayN();
      ok('I2 an inline commit alone writes nothing to the chip',
        notes.I_commit_diff.length === 0, { diff: notes.I_commit_diff, tray: notes.I_commit_tray });
      await applyLive();
      const i3 = snap('I: inline applied'); _last = i3;
      notes.I_apply_diff = diff(i2, i3);
      ok('I2b …and Apply writes it', notes.I_apply_diff.length >= 1, notes.I_apply_diff);

      await ev(`(function(){var ins=document.querySelectorAll('form.inline-edit input[name="value"]');
         var e=ins[${fld.idx}]; if(e){e.focus();} return 1;})()`);
      await sleep(500);
      await ctrlZ(); await sleep(3000); await quiet(700); await sleep(1500);
      const i4 = snap('I: Ctrl+Z from a clean inline field'); _last = i4;
      notes.I_clean_undo = diff(i3, i4);
      notes.I_clean_toasts = await lastToasts();
      ok('I3 Ctrl+Z from a CLEAN inspector field walks the journal and writes the chip',
        notes.I_clean_undo.length >= 1
        && notes.I_clean_undo.every(x => { const a = notes.I_apply_diff.find(y => y.path === x.path); return a && String(a.from) === String(x.to); }),
        { undo: notes.I_clean_undo, apply: notes.I_apply_diff, toasts: notes.I_clean_toasts });
      const shown = await ev(`(function(){var ins=document.querySelectorAll('form.inline-edit input[name="value"]');
         var e=ins[${fld.idx}]; return e? e.value : null;})()`);
      notes.I_shown_after_undo = shown;
      ok('I3b …and the inspector now shows what the file holds',
        shown != null && String(num(shown)) === String(num(fld.value)), { shown: shown, file: fld.value });
      await shot('I_inspector');
    }

    /* the Json tree */
    await send('Page.navigate', { url: BASE + '/explorer' });
    const treeUp = await until(`!!(document.getElementById('pending-tray') && document.querySelector('.tree-row, .tree-node, #json-tree'))`, 30000);
    await sleep(2500);
    await ev(`window.confirm=function(){return true}; window.alert=function(){}; 1`);
    await installToastHook();
    ok('I4 the Json tree page mounts', !!treeUp);
    const t5 = snap('I: tree, before the press'); _last = t5;
    await ev(`(function(){var r=document.querySelector('.tree-row'); if(r&&r.focus) r.focus(); else document.body.focus(); return 1;})()`);
    await sleep(500);
    await ctrlShiftZ(); await sleep(3000); await quiet(700); await sleep(1800);
    const t6 = snap('I: Ctrl+Shift+Z from the Json tree'); _last = t6;
    notes.I_tree_redo = diff(t5, t6);
    notes.I_tree_toasts = await lastToasts();
    ok('I5 a press from the Json Tree page walks the same journal and writes the chip',
      notes.I_tree_redo.length >= 1, { diff: notes.I_tree_redo, toasts: notes.I_tree_toasts });
    await ctrlZ(); await sleep(3000); await quiet(700); await sleep(1800);
    const t7 = snap('I: Ctrl+Z from the Json tree'); _last = t7;
    notes.I_tree_undo = diff(t6, t7);
    ok('I5b …and back again, over the same paths',
      notes.I_tree_undo.length >= 1
      && JSON.stringify(notes.I_tree_undo.map(x => x.path).sort())
      === JSON.stringify(notes.I_tree_redo.map(x => x.path).sort()),
      { redo: notes.I_tree_redo, undo: notes.I_tree_undo });
    await shot('I_tree');

    await send('Page.navigate', { url: BASE + '/bulk' });
    await until(`!!(document.getElementById('bulk-table') && window.UndoQueue)`, 40000);
    await sleep(2500);
    await ev(`window.confirm=function(){return true}; window.alert=function(){}; 1`);
    await installToastHook();
  }

  /* ═══ J · the FSP + amplitudes bundle ═════════════════════════════ */
  if (P('J')) {
    console.log('--- PHASE J');
    const powerCols = await ev(`(function(){
       var hs=document.querySelectorAll('#bulk-table thead th[data-col-key]');
       var keys=[]; for(var i=0;i<hs.length;i++) keys.push(hs[i].getAttribute('data-col-key'));
       return keys.filter(function(k){return /power|fsp|scale/i.test(k);});})()`);
    notes.J_power_cols = powerCols;
    let found = null;
    for (const k of powerCols) {
      await ev(`(function(){return window.BulkEdit && BulkEdit.hydrateColumn ? BulkEdit.hydrateColumn(${JSON.stringify(k)}).catch(function(){return 0;}) : 0;})()`);
      await sleep(800);
      const hit = await ev(`(function(){
         var tds=document.querySelectorAll('#bulk-table tbody td[data-col-key=' + JSON.stringify(${JSON.stringify(k)}) + ']');
         for (var i=0;i<tds.length;i++){
           var c=tds[i].querySelector('input.bulk-cell');
           if(!c||c.readOnly) continue;
           var p=c.getAttribute('data-resolved')||c.getAttribute('data-dot-path')||'';
           if (/full_scale_power_dbm$/.test(p))
             return {col:${JSON.stringify(k)}, q:tds[i].closest('tr').getAttribute('data-qubit'), v:c.value, path:p};
         }
         return null;})()`);
      if (hit) { found = hit; break; }
    }
    notes.J_fsp_cell = found;
    ok('J0 the grid carries a full_scale_power_dbm cell', !!found, { cols: powerCols, found: found });
    if (found) {
      const j0 = snap('J: before the FSP edit'); _last = j0;
      const nv = String(num(found.v) + 3);
      await editCell(found.q, found.col, nv);
      await sleep(2000);
      const popup = await ev(`(function(){var o=document.querySelector('.ch-overlay .fsp-card');
         if(!o) return null; var ov=o.closest('.ch-overlay');
         return {vis: ov.style.display!=='none',
                 text:o.textContent.replace(/\\s+/g,' ').slice(0,240),
                 amps:o.querySelectorAll('.fsp-amp-input').length,
                 buttons:Array.prototype.map.call(o.querySelectorAll('button'),function(b){return b.textContent.trim().slice(0,44);})};})()`);
      notes.J_popup = popup;
      ok('J1 an FSP edit opens the compensation offer instead of committing quietly',
        !!(popup && popup.vis && popup.amps > 0), popup);
      const j1 = snap('J: offer open'); _last = j1;
      notes.J_offer_diff = diff(j0, j1);
      ok('J1b …and nothing is on the chip while the offer is open',
        notes.J_offer_diff.length === 0, notes.J_offer_diff);

      const clicked = await ev(`(function(){var bs=document.querySelectorAll('.ch-overlay .fsp-card button');
         for (var i=0;i<bs.length;i++) if (/compensate/i.test(bs[i].textContent)) { bs[i].click(); return bs[i].textContent.trim(); }
         return null;})()`);
      notes.J_clicked = clicked;
      await sleep(3000); await quiet(700);
      notes.J_tray_after = await trayN();
      const j2 = snap('J: bundle staged'); _last = j2;
      notes.J_stage_diff = diff(j1, j2);
      ok('J2 accepting the bundle stages FSP + amplitudes and still writes nothing',
        notes.J_stage_diff.length === 0 && notes.J_tray_after >= 2,
        { diff: notes.J_stage_diff, tray: notes.J_tray_after, clicked: clicked });

      await applyLive();
      const j3 = snap('J: bundle applied'); _last = j3;
      notes.J_apply_diff = diff(j2, j3);
      ok('J3 Apply writes the FSP change AND its amplitudes together',
        notes.J_apply_diff.length >= 2
        && notes.J_apply_diff.some(x => /full_scale_power_dbm$/.test(x.path))
        && notes.J_apply_diff.some(x => /amplitude/i.test(x.path)),
        notes.J_apply_diff);
      await shot('J_applied');

      await ctrlZ(); await sleep(3500); await quiet(800); await sleep(2000);
      const j4 = snap('J: one Ctrl+Z'); _last = j4;
      notes.J_undo_diff = diff(j3, j4);
      notes.J_undo_toasts = await lastToasts();
      const appliedPaths = notes.J_apply_diff.map(x => x.path).sort();
      const undonePaths = notes.J_undo_diff.map(x => x.path).sort();
      ok('J4 ONE Ctrl+Z takes the whole FSP+amps bundle back — never half of it',
        JSON.stringify(appliedPaths) === JSON.stringify(undonePaths),
        { applied: appliedPaths, undone: undonePaths, toasts: notes.J_undo_toasts });
      ok('J4b …and every value is exactly what the chip held before the bundle',
        notes.J_undo_diff.length > 0 && notes.J_undo_diff.every(x => {
          const a = notes.J_apply_diff.find(y => y.path === x.path);
          return a && String(a.from) === String(x.to);
        }), { undo: notes.J_undo_diff, apply: notes.J_apply_diff });

      await ctrlShiftZ(); await sleep(3500); await quiet(800); await sleep(1800);
      const j5 = snap('J: one Ctrl+Shift+Z'); _last = j5;
      notes.J_redo_diff = diff(j4, j5);
      ok('J5 …and ONE Ctrl+Shift+Z puts the whole bundle back',
        JSON.stringify(notes.J_redo_diff.map(x => x.path).sort()) === JSON.stringify(appliedPaths),
        notes.J_redo_diff);
      await shot('J_after');
    }
  }

  /* ═══ wrap ════════════════════════════════════════════════════════ */
  notes.final_tray = await trayTxt();
  notes.final_toasts = await lastToasts();
  await shot('final');
  const final = snap('final');
  notes.final_state_sha = final.sha['state.json'];
  fs.writeFileSync(OUT, JSON.stringify({
    results: results, errors: errors, notes: notes, fileEvents: fileEvents,
    passed: results.filter(r => r.pass).length, failed: results.filter(r => !r.pass).length,
  }, null, 1));
  console.log('\n== ' + results.filter(r => r.pass).length + ' passed, ' + results.filter(r => !r.pass).length + ' failed, ' + errors.length + ' console/exception events');
  ws.close();
}
let _lastBootFlat = null;
main().catch(e => { console.error('DRIVER ERROR', e); try { fs.writeFileSync(OUT, JSON.stringify({ driverError: String(e && e.stack || e), results: results, errors: errors, notes: notes, fileEvents: fileEvents }, null, 1)); } catch (x) { } process.exit(1); });
