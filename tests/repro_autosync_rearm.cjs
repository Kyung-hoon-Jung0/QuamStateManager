/* docs/187 (3) claims the conflict tray lets you turn Auto-Sync back on.
 * A pill that renders is not the same as a pill that WORKS: the popup is
 * fetched into #auto-sync-pop-host, which lives in base.html OUTSIDE the tray,
 * and is positioned in viewport coordinates (docs/126 -- it once opened at the
 * left edge of the window). So drive it: reach the disarmed conflict state,
 * click the pill, check the popup opens ON SCREEN, submit it, and confirm the
 * session is armed again.
 *
 * argv[2]=out.json argv[3]=cdp argv[4]=base argv[5]=liveFolder
 */
const fs = require('fs');
const path = require('path');
const OUT = process.argv[2], CDP = process.argv[3], BASE = process.argv[4];
const LIVE = process.argv[5];
const checks = [], errors = [];
function ok(n, c, d) { checks.push({ name: n, pass: !!c, detail: d === undefined ? null : d }); }

function externalWrite(val) {
  const p = path.join(LIVE, 'state.json');
  const raw = fs.readFileSync(p, 'utf8');
  const j = JSON.parse(raw);
  j.qubits.q5.T1 = val;
  const crlf = raw.indexOf('\r\n') >= 0;
  let out = JSON.stringify(j, null, 4);
  if (crlf) out = out.replace(/\n/g, '\r\n');
  fs.writeFileSync(p, out, 'utf8');
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
  const click = async (x, y) => {
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  };

  await send('Runtime.enable'); await send('Page.enable');
  await send('Page.navigate', { url: BASE + '/bulk' });
  await sleep(9000);

  // ── reach the disarmed conflict state: push-only + an outside write ────
  await ev(`fetch('/auto-sync/set', { method: 'POST',
     headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'HX-Request': 'true' },
     body: 'pull=0&pull_replace=0&push=1' }).then(function(r){return r.status;})`);
  await ev(`(async function () {
    var h = await (await fetch('/state/tray', { headers: { 'HX-Request': 'true' } })).text();
    var t = document.getElementById('pending-tray'); if (t) t.outerHTML = h; return true; })()`);
  await sleep(1000);
  await ev(`fetch('/field/edit', { method: 'POST',
     headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
     body: 'dot_path=qubits.q1.T1&value=1.77e-5&expect_chip='
           + encodeURIComponent(window.__chipToken || '') }).then(function(r){return r.status;})`);
  externalWrite(5.55e-5);
  // the flusher presses apply-to-live itself; give it the tray mutation
  await ev(`(async function () {
    var h = await (await fetch('/state/tray', { headers: { 'HX-Request': 'true' } })).text();
    var t = document.getElementById('pending-tray'); if (t) t.outerHTML = h; return true; })()`);
  await sleep(5000);

  const st = await ev(`(function(){
    var pill = document.querySelector('#pending-tray .auto-apply-pill');
    var r = pill ? pill.getBoundingClientRect() : null;
    return { conflict: !!document.querySelector('.pending-tray-conflict'),
             saysTurnedOff: /has been turned/.test(document.body.innerHTML),
             pillClass: pill ? pill.className : null,
             pillVisible: !!(r && r.width > 0 && r.height > 0),
             x: r ? Math.round(r.left + r.width/2) : null,
             y: r ? Math.round(r.top + r.height/2) : null,
             hostExists: !!document.getElementById('auto-sync-pop-host') };})()`);
  ok('the conflict tray is up and says the session was turned off',
     st.conflict && st.saysTurnedOff, st);
  ok('it carries a VISIBLE pill', st.pillVisible, st);
  ok('the pill reads OFF, not ON',
     !!st.pillClass && st.pillClass.indexOf('auto-apply-off') >= 0, st.pillClass);
  ok('the popup host survives the tray swap (it lives in base.html)',
     st.hostExists, st);

  // ── press it ──────────────────────────────────────────────────────────
  if (st.pillVisible) {
    await click(st.x, st.y);
    await sleep(2500);
  }
  const pop = await ev(`(function(){
    var p = document.getElementById('auto-sync-pop');
    if (!p) return { open: false };
    var r = p.getBoundingClientRect();
    return { open: true,
             onScreen: r.left >= 0 && r.top >= 0
                    && r.left + r.width <= window.innerWidth + 1
                    && r.width > 40 && r.height > 40,
             left: Math.round(r.left), top: Math.round(r.top),
             w: Math.round(r.width), h: Math.round(r.height),
             vw: window.innerWidth,
             hasPull: !!document.getElementById('as-pull'),
             hasPush: !!document.getElementById('as-push'),
             hasForm: !!p.querySelector('form') };})()`);
  ok('clicking the pill opens the Auto-Sync popup', pop.open, pop);
  ok('…and it opens ON SCREEN (docs/126: it once opened at the left edge)',
     pop.open && pop.onScreen, pop);
  ok('…with the switches in it', pop.open && pop.hasPull && pop.hasPush, pop);

  // ── arm it again, through the popup's own form ─────────────────────────
  if (pop.open && pop.hasForm) {
    await ev(`(function(){
      var pull = document.getElementById('as-pull');
      var push = document.getElementById('as-push');
      if (pull && !pull.checked) pull.click();
      if (push && !push.checked) push.click();
      var f = document.querySelector('#auto-sync-pop form');
      if (f && window.htmx) { window.htmx.trigger(f, 'submit'); return 'submitted'; }
      var b = f && f.querySelector('button[type="submit"], button:not([type])');
      if (b) { b.click(); return 'clicked'; }
      return 'no form path';
    })()`);
    await sleep(3500);
  }
  const after = await ev(`(async function(){
    var s = null;
    // /auto-apply/gate, NOT /auto-apply/status -- the latter is a 404, and my
    // first version's try/catch swallowed that into armedServerSide:false, i.e.
    // a check that could only ever fail. Guessed route names have cost this
    // session three checks now.
    try { s = await (await fetch('/auto-apply/gate')).json(); } catch (e) {}
    var t = document.getElementById('pending-tray');
    return { armedServerSide: !!(s && s.armed),
             trayAuto: t && t.getAttribute('data-auto-apply'),
             pillOn: !!document.querySelector('.auto-apply-pill.auto-apply-on') };})()`);
  ok('the session is ARMED AGAIN from the conflict tray', after.armedServerSide, after);
  ok('…and the tray now shows it on', after.pillOn || after.trayAuto === '1', after);

  const pass = checks.filter(c => c.pass).length;
  fs.writeFileSync(OUT, JSON.stringify({ checks, st, pop, after, errors }, null, 2));
  console.log('RE-ARM pass: ' + pass + '/' + checks.length + '   console errors: ' + errors.length);
  checks.filter(c => !c.pass).forEach(c =>
    console.log('  FAIL ' + c.name + '  ' + JSON.stringify(c.detail)));
  errors.slice(0, 5).forEach(e => console.log('   err:', e));
  ws.close();
}
main().catch(e => { console.error('DRIVER FAILED', e); process.exit(1); });
