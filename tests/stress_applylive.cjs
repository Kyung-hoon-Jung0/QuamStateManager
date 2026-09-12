/* LANE: Apply to live — the one door.
 *
 * /state/apply-to-live is the ONLY writer of the live files. This driver
 * presses it hostilely from the Live State Edit grid and the Review tray in
 * real headless Chrome over CDP, and after EVERY gesture re-hashes
 * state.json + wiring.json and diffs the parsed JSON against the previous
 * snapshot, so every byte that moves is attributed to a press that said it
 * would.
 *
 * argv[2] = json out path
 * argv[3] = CDP port
 * argv[4] = base url
 * argv[5] = chip dir (the live files this lane watches)
 * argv[6] = phase name (or "all")
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const OUT = process.argv[2];
const CDP = process.argv[3] || '9461';
const BASE = process.argv[4] || 'http://127.0.0.1:5461';
const CHIP = process.argv[5];
const PHASE = process.argv[6] || 'all';

const errors = [];
const results = [];
const timeline = [];   // every file-state transition, with the gesture that caused it
function ok(name, cond, detail) {
  results.push({ name, pass: !!cond, detail: detail === undefined ? null : detail });
  if (!cond) console.log('  FAIL ' + name + ' :: ' + JSON.stringify(detail));
}
function note(name, detail) { results.push({ name, pass: null, detail }); }

// ---------- file snapshotting ----------
function readChip() {
  const s = fs.readFileSync(path.join(CHIP, 'state.json'));
  const w = fs.readFileSync(path.join(CHIP, 'wiring.json'));
  return {
    state_sha: crypto.createHash('sha256').update(s).digest('hex'),
    wiring_sha: crypto.createHash('sha256').update(w).digest('hex'),
    state_len: s.length, wiring_len: w.length,
    state: JSON.parse(s.toString('utf8')),
    wiring: JSON.parse(w.toString('utf8')),
    mtime_state: fs.statSync(path.join(CHIP, 'state.json')).mtimeMs,
  };
}
function flat(o, prefix, out) {
  out = out || {}; prefix = prefix || '';
  if (o === null || typeof o !== 'object') { out[prefix] = o; return out; }
  if (Array.isArray(o)) { o.forEach((v, i) => flat(v, prefix + '.' + i, out)); return out; }
  const ks = Object.keys(o);
  if (!ks.length) { out[prefix] = '{}'; return out; }
  ks.forEach(k => flat(o[k], prefix ? prefix + '.' + k : k, out));
  return out;
}
function jdiff(a, b) {
  const fa = flat(a), fb = flat(b);
  const keys = new Set([...Object.keys(fa), ...Object.keys(fb)]);
  const d = [];
  for (const k of keys) {
    const x = fa[k], y = fb[k];
    if (!(k in fa)) { d.push({ path: k, from: '<absent>', to: y }); continue; }
    if (!(k in fb)) { d.push({ path: k, from: x, to: '<absent>' }); continue; }
    if (x !== y && !(Number.isNaN(x) && Number.isNaN(y))) d.push({ path: k, from: x, to: y });
  }
  return d;
}
let prev = null;
function snap(gesture) {
  const cur = readChip();
  const entry = { gesture, state_sha: cur.state_sha.slice(0, 12), wiring_sha: cur.wiring_sha.slice(0, 12), changed: false, diff: [] };
  if (prev) {
    const sc = prev.state_sha !== cur.state_sha;
    const wc = prev.wiring_sha !== cur.wiring_sha;
    entry.changed = sc || wc;
    entry.state_changed = sc; entry.wiring_changed = wc;
    if (sc) entry.diff = entry.diff.concat(jdiff(prev.state, cur.state).map(d => ({ f: 'state', ...d })));
    if (wc) entry.diff = entry.diff.concat(jdiff(prev.wiring, cur.wiring).map(d => ({ f: 'wiring', ...d })));
  }
  timeline.push(entry);
  prev = cur;
  return entry;
}

async function main() {
  const targets = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(res => ws.onopen = res);
  let id = 0; const pending = new Map();
  let dialogSeen = null; let dialogAccept = true;
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
    if (m.method === 'Page.javascriptDialogOpening') {
      dialogSeen = m.params.message;
      ws.send(JSON.stringify({ id: ++id, method: 'Page.handleJavaScriptDialog', params: { accept: dialogAccept } }));
    }
  };
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => {
    const rr = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) {
      const ex = rr.result.exceptionDetails.exception;
      throw new Error((ex && (ex.description || ex.value)) || 'eval failed: ' + expr.slice(0, 120));
    }
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(res => setTimeout(res, ms));
  const until = async (expr, ms) => {
    const t = Date.now();
    while (Date.now() - t < ms) { const v = await ev(expr); if (v) return v; await sleep(120); }
    return await ev(expr);
  };
  const nav = async (url) => {
    await send('Page.navigate', { url });
    await until('document.readyState==="complete"', 15000);
    await sleep(500);
  };
  const typeChar = async (ch) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  };
  const KEYS = { Enter: 13, Tab: 9, Escape: 27, ArrowDown: 40, ArrowUp: 38, Backspace: 8 };
  const press = async (key) => {
    const p = { type: 'rawKeyDown', key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key] };
    if (key === 'Enter' || key === 'Tab') { p.text = key === 'Enter' ? '\r' : '\t'; p.type = 'keyDown'; }
    await send('Input.dispatchKeyEvent', p);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key, windowsVirtualKeyCode: KEYS[key] });
  };
  const clickSel = async (sel) => {
    const box = await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;e.scrollIntoView({block:'center'});var r=e.getBoundingClientRect();return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2});})()`);
    if (!box) return false;
    const { x, y } = JSON.parse(box);
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
    return true;
  };
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    if (r.result && r.result.data) fs.writeFileSync(path.join(path.dirname(OUT), name + '.png'), Buffer.from(r.result.data, 'base64'));
  };

  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  // record every response status for the live-write door
  const netlog = [];
  ws.addEventListener('message', e => {
    const m = JSON.parse(e.data);
    if (m.method === 'Network.responseReceived') {
      const u = m.params.response.url;
      if (/apply-to-live|state\/sync|field\/edit|undo|revert/.test(u)) netlog.push({ url: u.replace(BASE, ''), status: m.params.response.status });
    }
  });

  const ctx = { ev, sleep, until, nav, press, typeChar, clickSel, shot, send,
    snap, ok, note, netlog, results, timeline, BASE, readChip, jdiff,
    dlg: {
      get last() { return dialogSeen; },
      clear() { dialogSeen = null; },
      set accept(v) { dialogAccept = v; },
      get accept() { return dialogAccept; },
    } };

  snap('BASELINE (before any gesture)');

  ctx.CHIP = CHIP;
  ctx.OTHER_CHIP = process.argv[7] || '';
  const phases = Object.assign({}, require('./stress_applylive_phases3.cjs'),
    require('./stress_applylive_phases.cjs'),
    require('./stress_applylive_phases2.cjs'),
    require('./stress_applylive_phases4.cjs'));
  for (const [name, fn] of Object.entries(phases)) {
    if (PHASE !== 'all' && PHASE !== name) continue;
    console.log('=== PHASE ' + name + ' ===');
    try { await fn(ctx); }
    catch (e) { ok('phase ' + name + ' completed', false, String(e && e.stack || e).slice(0, 600)); }
    snap('after phase ' + name);
  }

  fs.writeFileSync(OUT, JSON.stringify({ results, errors, timeline, netlog }, null, 1));
  const fails = results.filter(r => r.pass === false);
  console.log('\n== ' + results.filter(r => r.pass === true).length + ' pass, ' + fails.length + ' FAIL, ' + errors.length + ' console/exception ==');
  fails.forEach(f => console.log('  FAIL: ' + f.name));
  timeline.filter(t => t.changed).forEach(t => console.log('  WROTE @ ' + t.gesture + ' :: ' + t.diff.length + ' leaf(s)'));
  ws.close();
}
main().catch(e => { console.error(e); process.exit(1); });
