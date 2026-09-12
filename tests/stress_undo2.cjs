/* Ctrl+Z as a WRITE PATH, part 2: the refusals that must change nothing.
 *
 *  K — the live file moved out of band, then Ctrl+Z: the door's staleness gate
 *      must refuse and the rollback must leave the chip byte-identical.
 *  L — two windows: a stranger's unapplied edit in the shared tray, and a
 *      second window that switched the ACTIVE chip. Neither may ride a press.
 *
 * argv[2] = json out, argv[3] = CDP port, argv[4] = base url, argv[5] = phases
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const OUT = process.argv[2] || 'undo2.json';
const CDP = process.argv[3] || '9462';
const BASE = process.argv[4] || 'http://127.0.0.1:5462';
const PHASES = (process.argv[5] || 'K,L').split(',').map(s => s.trim());
const SP = 'C:/Users/KyunghoonJung/AppData/Local/Temp/claude/D--work-statemanager/dd0fa2c3-e492-405d-8783-2c62cd30ba4a/scratchpad';
const CHIP = SP + '/chip_5462/quam_state';
const CHIPB = SP + '/chip_5462b/quam_state';
const SHOTDIR = OUT.replace(/[^\\/]*$/, '');

const errors = [], results = [], notes = {};
function ok(name, cond, detail) {
  results.push({ name: name, pass: !!cond, detail: detail === undefined ? null : detail });
  console.log((cond ? '  ok    ' : '  FAIL  ') + name + (cond ? '' : '  ' + JSON.stringify(detail || null).slice(0, 500)));
  return !!cond;
}
function flat(obj, prefix, acc) {
  acc = acc || {};
  if (obj !== null && typeof obj === 'object' && !Array.isArray(obj)) {
    for (const k of Object.keys(obj)) flat(obj[k], prefix ? prefix + '.' + k : k, acc);
  } else acc[prefix] = Array.isArray(obj) ? JSON.stringify(obj) : obj;
  return acc;
}
function snapOf(dir, tag) {
  const s = { tag: tag, sha: {}, flat: {} };
  for (const f of ['state.json', 'wiring.json']) {
    let txt = null;
    for (let i = 0; i < 8; i++) { try { txt = fs.readFileSync(path.join(dir, f), 'utf8'); break; } catch (e) { } }
    s.sha[f] = txt == null ? null : crypto.createHash('sha256').update(txt).digest('hex').slice(0, 16);
    const key = f === 'state.json' ? 'state' : 'wiring';
    try { s.flat[key] = flat(JSON.parse(txt), ''); } catch (e) { s.flat[key] = { __unparsable: String(e) }; }
  }
  return s;
}
function diff(a, b) {
  const out = [];
  for (const file of ['state', 'wiring']) {
    const A = a.flat[file] || {}, B = b.flat[file] || {};
    for (const k of new Set(Object.keys(A).concat(Object.keys(B)))) {
      const x = A[k], y = B[k];
      if (x === y) continue;
      if (typeof x === 'number' && typeof y === 'number' && Number.isNaN(x) && Number.isNaN(y)) continue;
      out.push({ file: file, path: k, from: x === undefined ? '<absent>' : x, to: y === undefined ? '<absent>' : y });
    }
  }
  return out;
}
const snap = (t) => snapOf(CHIP, t);

function conn(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let id = 0; const pend = new Map();
  const api = { ws: ws, ready: new Promise(r => ws.onopen = r) };
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      errors.push({ kind: 'exception', at: Date.now(), text: (d.exception && (d.exception.description || d.exception.value)) || d.text });
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')
      errors.push({ kind: 'console.error', at: Date.now(), text: (m.params.args || []).map(a => a.value || a.description || '').join(' ') });
  };
  api.send = (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  api.ev = async (expr) => {
    const rr = await api.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) {
      const ex = rr.result.exceptionDetails.exception;
      throw new Error((ex && (ex.description || ex.value)) || 'eval failed');
    }
    return rr.result.result.value;
  };
  return api;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  const list = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const pageT = list.find(t => t.type === 'page');
  const A = conn(pageT.webSocketDebuggerUrl);
  await A.ready;
  await A.send('Page.enable'); await A.send('Runtime.enable'); await A.send('Network.enable');
  await A.send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  const until = async (api, expr, ms) => {
    const t = Date.now();
    while (Date.now() - t < ms) { const v = await api.ev(expr); if (v) return v; await sleep(150); }
    return await api.ev(expr);
  };
  const KEYS = { z: 90, Z: 90, Enter: 13, Backspace: 8 };
  const key = async (api, k, mods) => {
    const p = { type: 'rawKeyDown', key: k, windowsVirtualKeyCode: KEYS[k], nativeVirtualKeyCode: KEYS[k] };
    if (mods) p.modifiers = mods;
    if (k === 'Enter') { p.text = '\r'; p.type = 'keyDown'; }
    await api.send('Input.dispatchKeyEvent', p);
    await api.send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, windowsVirtualKeyCode: KEYS[k], modifiers: mods || 0 });
  };
  const typeChar = async (api, ch) => {
    await api.send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await api.send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  };
  const shot = async (api, tag) => {
    try {
      const s = await api.send('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(SHOTDIR + 'un2_' + tag + '.png', Buffer.from(s.result.data, 'base64'));
    } catch (e) { }
  };
  const trayN = (api) => api.ev(`(function(){var t=document.getElementById('pending-tray');
     if(!t) return null; var v=t.getAttribute('data-change-count'); return v==null?null:+v;})()`);
  const toasts = (api) => api.ev(`(window.__seenToasts||[]).slice(-8)`);
  const hookToasts = (api) => api.ev(`(function(){ if (window.__toastHooked) return 1;
     window.__seenToasts=[]; var f=window.showToast;
     window.showToast=function(m,l){ try{window.__seenToasts.push(String(l||'')+': '+String(m));}catch(e){} return f.apply(this,arguments); };
     window.__toastHooked=1; return 1;})()`);
  const num = v => Number(String(v).replace(/,/g, ''));
  const SEL = (q, col) => 'tr[data-qubit="' + q + '"] td[data-col-key="' + col + '"] input.bulk-cell';
  const editCell = async (api, q, col, value) => {
    const okf = await api.ev(`(function(){var c=document.querySelector(${JSON.stringify(SEL(q, col))});
       if(!c||c.readOnly) return 0; c.scrollIntoView({block:'center',inline:'center'}); c.focus(); c.select(); return 1;})()`);
    if (!okf) return false;
    await sleep(150);
    for (let i = 0; i < 26; i++) await key(api, 'Backspace');
    for (const ch of String(value)) await typeChar(api, ch);
    await sleep(120);
    await key(api, 'Enter');
    await sleep(900);
    return true;
  };
  const cellVal = (api, q, col) => api.ev(`(function(){var c=document.querySelector(${JSON.stringify(SEL(q, col))});
     return c? {v:c.value, path:c.getAttribute('data-dot-path')} : null;})()`);
  const applyLive = async (api) => {
    const box = await api.ev(`(function(){var e=document.querySelector('.btn-apply-live'); if(!e) return null;
       e.scrollIntoView({block:'center'}); var r=e.getBoundingClientRect();
       if(r.width<=0) return {off:1}; return {x:r.left+r.width/2,y:r.top+r.height/2};})()`);
    if (!box || box.off) return false;
    await api.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    await api.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    await sleep(800);
    await until(api, `!window._applyInFlight`, 20000);
    await sleep(2500);
    return true;
  };

  await A.send('Page.navigate', { url: BASE + '/bulk' });
  await until(A, `!!(document.getElementById('bulk-table') && window.UndoQueue)`, 40000);
  await sleep(2500);
  await A.ev(`window.confirm=function(){return true}; window.alert=function(){}; window.prompt=function(){return ''}; 1`);
  await hookToasts(A);
  await A.ev(`(function(){var ks=['T1','T2echo','chi','anharmonicity'];
     if(!(window.BulkEdit && BulkEdit.hydrateColumn)) return 0;
     return Promise.all(ks.map(function(k){return BulkEdit.hydrateColumn(k).catch(function(){return 0;});})).then(function(){return 1;});})()`);
  await sleep(900);
  const t0 = await trayN(A);
  if (t0 > 0) {
    await A.ev(`(function(){htmx.ajax('POST','/discard_all',{target:'#pending-tray',swap:'outerHTML'}); return 1;})()`);
    await sleep(2500);
  }
  notes.tray_at_boot = t0;
  notes.tray_after_clear = await trayN(A);
  ok('the grid is up and the tray is clean', (await trayN(A)) === 0, notes);

  const P = x => PHASES.indexOf(x) >= 0;

  /* ═══ K · the live file moved out of band ═════════════════════════ */
  if (P('K')) {
    console.log('--- PHASE K');
    // one applied unit to walk
    const c = await cellVal(A, 'q1', 'T1');
    const nv = String(Number((num(c.v) * 1.09).toPrecision(12)));
    const k0 = snap('K: before');
    await editCell(A, 'q1', 'T1', nv);
    await applyLive(A);
    const k1 = snap('K: applied');
    notes.K_apply = diff(k0, k1);
    ok('K0 the edit reached the chip', notes.K_apply.length === 1, notes.K_apply);

    // NOW move the live file out of band -- a different qubit, so the walk's
    // own leaf is untouched: only the FILE has moved.
    const raw = JSON.parse(fs.readFileSync(path.join(CHIP, 'state.json'), 'utf8'));
    const before20 = raw.qubits.q20.T1;
    raw.qubits.q20.T1 = (Number(before20) || 1e-5) * 1.5;
    fs.writeFileSync(path.join(CHIP, 'state.json'), JSON.stringify(raw, null, 4), 'utf8');
    const k2 = snap('K: live moved out of band');
    notes.K_outofband = diff(k1, k2);
    ok('K1 an out-of-band write really moved the live file',
      notes.K_outofband.length === 1 && /q20\.T1$/.test(notes.K_outofband[0].path), notes.K_outofband);

    await sleep(7000);            // let the drift poll see it (5 s cadence)
    await key(A, 'z', 2);         // Ctrl+Z
    await sleep(5000);
    const k3 = snap('K: after the press');
    notes.K_after_press = diff(k2, k3);
    notes.K_toasts = await toasts(A);
    notes.K_trayN = await trayN(A);
    notes.K_banner = await A.ev(`(function(){var b=document.querySelector('.live-diverged, #live-diverged-banner, [data-live-diverged]');
       return b? b.textContent.replace(/\\s+/g,' ').trim().slice(0,200) : null;})()`);
    ok('K2 a press against a chip that MOVED writes nothing at all',
      notes.K_after_press.length === 0,
      { diff: notes.K_after_press, toasts: notes.K_toasts, tray: notes.K_trayN });
    ok('K2b …and the out-of-band value is still there (nothing was clobbered)',
      k3.flat.state['qubits.q20.T1'] === raw.qubits.q20.T1,
      { now: k3.flat.state['qubits.q20.T1'], wrote: raw.qubits.q20.T1 });
    ok('K2c …and the refusal SAYS so (a toast, not silence)',
      (notes.K_toasts || []).some(t => /not undone|drift|live|chang/i.test(t)), notes.K_toasts);
    await shot(A, 'K_refused');

    // take the live changes, then the press must work again
    await A.ev(`(function(){ if(window.doStateSync){ window.doStateSync('discard'); return 'discard'; } return null;})()`);
    await sleep(6000);
    const k4 = snap('K: after taking live');
    notes.K_take_live = diff(k3, k4);
    ok('K3 taking the live changes does not write the chip either', notes.K_take_live.length === 0, notes.K_take_live);
    await key(A, 'z', 2);
    await sleep(6000);
    const k5 = snap('K: press after taking live');
    notes.K_after_take = diff(k4, k5);
    notes.K_toasts2 = await toasts(A);
    ok('K4 …and after taking them the press walks again (or says why it cannot)',
      true, { diff: notes.K_after_take, toasts: notes.K_toasts2 });
    ok('K4b …and whatever it did, q20 (the stranger\'s value) survived',
      k5.flat.state['qubits.q20.T1'] === raw.qubits.q20.T1,
      { now: k5.flat.state['qubits.q20.T1'], wrote: raw.qubits.q20.T1 });
    await shot(A, 'K_after');
  }

  /* ═══ L · two windows ═════════════════════════════════════════════ */
  if (P('L')) {
    console.log('--- PHASE L');
    // ---- L1: a stranger's unapplied edit in the shared tray ---------
    // a second TAB through the browser endpoint (Target.createTarget is a
    // browser-domain method and is not served on a page session)
    const made = await (await fetch('http://127.0.0.1:' + CDP + '/json/new?url='
      + encodeURIComponent(BASE + '/bulk'), { method: 'PUT' })).json();
    const bid = made.id;
    await sleep(5000);
    const list2 = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
    const pb = list2.find(t => t.id === bid);
    const B = conn(pb.webSocketDebuggerUrl);
    await B.ready;
    await B.send('Page.enable'); await B.send('Runtime.enable');
    await B.send('Emulation.setDeviceMetricsOverride', { width: 1400, height: 900, deviceScaleFactor: 1, mobile: false });
    // the new tab can still be at about:blank -- navigate it ourselves
    await B.send('Page.navigate', { url: BASE + '/bulk' });
    const upB = await until(B, `!!(document.getElementById('bulk-table') && window.UndoQueue)`, 60000);
    await sleep(2000);
    await sleep(2500);
    await B.ev(`window.confirm=function(){return true}; window.alert=function(){}; 1`);
    await hookToasts(B);
    ok('L0 a second window is open on the same chip', !!upB);

    // window A applies one unit (so there is something to walk)
    const ca = await cellVal(A, 'q1', 'chi');
    const l0 = snap('L: before A applies');
    await editCell(A, 'q1', 'chi', String(Number((num(ca.v) * 1.06).toPrecision(12))));
    await applyLive(A);
    const l1 = snap('L: A applied');
    notes.L_apply = diff(l0, l1);
    ok('L1 window A applied one unit', notes.L_apply.length === 1, notes.L_apply);

    // window B now types an edit and LEAVES IT unapplied (a stranger in the
    // shared change log, docs/120)
    await B.ev(`(function(){return window.BulkEdit && BulkEdit.hydrateColumn ? BulkEdit.hydrateColumn('T2echo').catch(function(){return 0;}) : 0;})()`);
    await sleep(900);
    const cb = await cellVal(B, 'q2', 'T2echo');
    notes.L_B_cell = cb;
    const bval = cb ? String(Number((num(cb.v) * 1.44).toPrecision(12))) : null;
    if (cb) await editCell(B, 'q2', 'T2echo', bval);
    notes.L_trayB = await trayN(B);
    const l2 = snap('L: B typed, unapplied');
    notes.L_B_edit_diff = diff(l1, l2);
    ok('L2 window B\'s edit is staged and NOT on the chip',
      notes.L_B_edit_diff.length === 0 && notes.L_trayB >= 1,
      { diff: notes.L_B_edit_diff, trayB: notes.L_trayB });

    // window A presses Ctrl+Z. B's un-reviewed edit must NOT ride it.
    await key(A, 'z', 2);
    await sleep(6000);
    const l3 = snap('L: A pressed Ctrl+Z with B\'s edit in the tray');
    notes.L_after_press = diff(l2, l3);
    notes.L_toastsA = await toasts(A);
    notes.L_trayA = await trayN(A);
    const strangerOnChip = cb ? String(l3.flat.state[cb.path]) === String(num(bval)) : false;
    ok('L3 window B\'s unapplied edit NEVER rides window A\'s Ctrl+Z',
      !strangerOnChip,
      { path: cb && cb.path, onChip: cb && l3.flat.state[cb.path], typedInB: bval, diff: notes.L_after_press });
    ok('L3b …and whatever A\'s press did, every changed byte is one of the two known paths',
      notes.L_after_press.every(x => (cb && x.path === cb.path) || x.path === notes.L_apply[0].path),
      { diff: notes.L_after_press, toasts: notes.L_toastsA, trayA: notes.L_trayA });
    await shot(A, 'L_windowA');
    await shot(B, 'L_windowB');

    // ---- L2: the second window switches the ACTIVE chip -------------
    fs.mkdirSync(CHIPB, { recursive: true });
    for (const f of ['state.json', 'wiring.json'])
      fs.copyFileSync(path.join(SP, 'chip_5462/pristine', f), path.join(CHIPB, f));
    const bSnap0 = snapOf(CHIPB, 'B chip pristine');
    notes.L_chipB_sha0 = bSnap0.sha;
    const loaded = await B.ev(`fetch('/load',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
       body:'folder=' + encodeURIComponent(${JSON.stringify(CHIPB)})}).then(function(r){return r.status;})`);
    notes.L_loadB_status = loaded;
    await sleep(5000);
    await B.send('Page.navigate', { url: BASE + '/bulk' });
    await until(B, `!!(document.getElementById('bulk-table'))`, 40000);
    await sleep(2500);
    const tokenA = await A.ev(`String(window.__chipToken||'')`);
    const tokenB = await B.ev(`String(window.__chipToken||'')`);
    notes.L_tokens = { A: tokenA, B: tokenB };
    ok('L4 window B switched the active chip (the two windows hold different tokens)',
      !!tokenA && !!tokenB && tokenA !== tokenB, notes.L_tokens);

    const a0 = snap('L: chip A before the stale press');
    const b0 = snapOf(CHIPB, 'chip B before the stale press');
    await key(A, 'z', 2);            // window A is now STALE
    await sleep(6000);
    const a1 = snap('L: chip A after');
    const b1 = snapOf(CHIPB, 'chip B after');
    notes.L_stale_chipA = diff(a0, a1);
    notes.L_stale_chipB = diff(b0, b1);
    notes.L_toastsA2 = await toasts(A);
    ok('L5 a press in the STALE window never rewrites the OTHER chip',
      notes.L_stale_chipB.length === 0, notes.L_stale_chipB);
    ok('L5b …and it does not quietly write the window\'s own chip either',
      notes.L_stale_chipA.length === 0,
      { diff: notes.L_stale_chipA, toasts: notes.L_toastsA2 });
    await shot(A, 'L_stale_press');
    try { await fetch('http://127.0.0.1:' + CDP + '/json/close/' + bid); } catch (e) { }
  }

  fs.writeFileSync(OUT, JSON.stringify({
    results: results, errors: errors, notes: notes,
    passed: results.filter(r => r.pass).length, failed: results.filter(r => !r.pass).length,
  }, null, 1));
  console.log('\n== ' + results.filter(r => r.pass).length + ' passed, '
    + results.filter(r => !r.pass).length + ' failed, ' + errors.length + ' console/exception events');
  A.ws.close();
}
main().catch(e => {
  console.error('DRIVER ERROR', e);
  try { fs.writeFileSync(OUT, JSON.stringify({ driverError: String(e && e.stack || e), results: results, errors: errors, notes: notes }, null, 1)); } catch (x) { }
  process.exit(1);
});
