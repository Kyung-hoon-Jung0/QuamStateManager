/* docs/187 (2) in a real browser: a replace-pull discards unapplied edits, and
 * until this round NOTHING in the tree listened to the event that said so.
 *
 * Drives the panel's DEFAULT configuration -- all three switches on -- with an
 * outside writer, and asks the only question that matters to the person at the
 * bench: when my typed value goes away, does anything tell me?
 *
 * argv[2]=out.json argv[3]=cdp argv[4]=base argv[5]=liveFolder
 */
const fs = require('fs');
const path = require('path');
const OUT = process.argv[2], CDP = process.argv[3], BASE = process.argv[4];
const LIVE = process.argv[5];
const errors = [];

function externalWrite(val) {
  const p = path.join(LIVE, 'state.json');
  const raw = fs.readFileSync(p, 'utf8');
  const j = JSON.parse(raw);
  j.qubits.q5.T1 = val;
  const crlf = raw.indexOf('\r\n') >= 0;
  let out = JSON.stringify(j, null, 4);
  if (crlf) out = out.replace(/\n/g, '\r\n');
  fs.writeFileSync(p, out, 'utf8');
  return val;
}

async function main() {
  const t = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = t.find(x => x.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  let id = 0; const pend = new Map();
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
    if (m.method === 'Page.javascriptDialogOpening') {
      ws.send(JSON.stringify({ id: ++id, method: 'Page.handleJavaScriptDialog',
                               params: { accept: true } }));
      return;
    }
    if (m.method === 'Runtime.exceptionThrown') {
      const s = String((m.params.exceptionDetails.exception || {}).description
        || m.params.exceptionDetails.text);
      if (!/unsafe-eval/.test(s)) errors.push(s.slice(0, 200));
    }
  };
  const send = (mm, p = {}) => new Promise(r => {
    const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method: mm, params: p }));
  });
  const ev = async x => {
    const rr = await send('Runtime.evaluate',
      { expression: x, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) {
      return { __err: String((rr.result.exceptionDetails.exception || {}).description || '').slice(0, 300) };
    }
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  await send('Runtime.enable'); await send('Page.enable');
  await send('Page.navigate', { url: BASE + '/bulk' });
  await sleep(9000);

  // remember every toast the page raises from here on
  await ev(`(function(){ window.__toasts = [];
     var orig = window.showToast;
     window.showToast = function (m, l) { window.__toasts.push({ m: String(m), l: l });
       if (orig) return orig.apply(this, arguments); };
     return true; })()`);

  // the panel's DEFAULT: all three on
  await ev(`fetch('/auto-sync/set', { method: 'POST',
     headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'HX-Request': 'true' },
     body: 'pull=1&pull_replace=1&push=1' }).then(function(r){return r.status;})`);
  await ev(`(async function () {
    var h = await (await fetch('/state/tray', { headers: { 'HX-Request': 'true' } })).text();
    var t = document.getElementById('pending-tray'); if (t) t.outerHTML = h; return true; })()`);
  await sleep(1200);

  // the user types a value, and does NOT press Enter (it is unapplied work)
  const typed = await ev(`(function () {
    var td = document.querySelector('tr[data-qubit="q1"] td[data-col-key="saturation_amplitude"]');
    var inp = td && td.querySelector('input.bulk-cell');
    if (!inp) return { __no: 'no cell' };
    return { dot: inp.getAttribute('data-dot-path'), was: inp.value };
  })()`);
  await ev(`fetch('/field/edit', { method: 'POST',
     headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
     body: 'dot_path=' + encodeURIComponent(${JSON.stringify(typed.dot || '')})
           + '&value=0.00777&expect_chip=' + encodeURIComponent(window.__chipToken || '')
   }).then(function(r){return r.status;})`);

  // a node saves the chip
  const wrote = externalWrite(4.44e-5);

  // the armed pull runs (the client presses it when the server says it is due;
  // pressing it directly is the same door, without waiting out the 30s
  // ground-truth recheck)
  // The pull only runs once the server has RAISED live_diverged, and the
  // ground-truth content recheck is throttled to _LIVE_HASH_RECHECK_S (30s).
  // A 2s wait got a 204 ("nothing to pull") and proved nothing -- so poll the
  // drift route until the server says a pull is due, and say so if it never
  // does rather than reporting a silent pass.
  let due = false;
  for (let k = 0; k < 26 && !due; k++) {
    await sleep(2000);
    const d = await ev(`(async function () {
      var r = await fetch('/state/drift', { cache: 'no-store' });
      var j = null; try { j = await r.json(); } catch (e) {}
      return { auto_pull: !!(j && j.auto_pull), diverged: !!(j && j.diverged) };
    })()`);
    due = d.auto_pull || d.diverged;
  }
  if (!due) console.log('NOTE: the server never said a pull was due');
  const pulled = await ev(`(async function () {
    var r = await fetch('/auto-sync/pull', { method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'HX-Request': 'true' },
      body: 'dom_dirty=0' });
    var trig = r.headers.get('HX-Trigger') || '';
    // the client only sees this through htmx; fire it the way htmx would so the
    // listener under test is the one that runs
    try {
      var o = JSON.parse(trig);
      if (o.autoSyncPulled) {
        document.body.dispatchEvent(new CustomEvent('autoSyncPulled',
          { detail: o.autoSyncPulled, bubbles: true }));
      }
    } catch (e) {}
    return { status: r.status, trig: trig.slice(0, 300) };
  })()`);
  await sleep(800);

  const told = await ev(`(function(){
     return { toasts: (window.__toasts || []).map(function(t){return t.l + ': ' + t.m;}),
              anyWarn: (window.__toasts || []).some(function(t){
                 return /replace/i.test(t.m); }),
              namesWayBack: (window.__toasts || []).some(function(t){
                 return /State History/.test(t.m); }) };})()`);

  fs.writeFileSync(OUT, JSON.stringify({ typed, wrote, pulled, told, errors }, null, 2));
  console.log('pull:', JSON.stringify(pulled));
  console.log('told the user?', told.anyWarn, '| names the way back?', told.namesWayBack);
  told.toasts.forEach(t => console.log('   toast:', t));
  console.log('console errors:', errors.length);
  errors.slice(0, 5).forEach(e => console.log('   ', e));
  ws.close();
}
main().catch(e => { console.error('DRIVER FAILED', e); process.exit(1); });
