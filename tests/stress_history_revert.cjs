/* docs/186 in a REAL browser — the panel the customer photographed.
 *
 * Two asks: the applied-to-live log must stop opening itself, and the value
 * history must let you put a previous value back. Both are things you SEE, so
 * both get checked where they are seen.
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
    if (m.method === 'Page.javascriptDialogOpening') {
      ws.send(JSON.stringify({ id: ++id, method: 'Page.handleJavaScriptDialog',
                               params: { accept: m.params.type === 'beforeunload' } }));
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
      throw new Error(String((rr.result.exceptionDetails.exception || {}).description || '').slice(0, 250));
    }
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  await send('Runtime.enable'); await send('Page.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true }).catch(() => {});
  await send('Emulation.setDeviceMetricsOverride',
    { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  // A first visit: the preference must not be carried in from an earlier run.
  await send('Page.navigate', { url: BASE + '/qubits' });
  await sleep(3000);
  await ev(`try { sessionStorage.removeItem('quam_applied_log_open'); } catch (e) {} 1`);
  // The applied log only renders inside an ARMED session (docs/117) — which is
  // also the state the customer's screenshot was taken in ("auto mode를 켜고나서").
  const armed = await ev(`(async function () {
    var r = await fetch('/auto-apply/arm', { method: 'POST',
      headers: { 'HX-Request': 'true' } });
    return r.status;
  })()`);
  ok('the auto-apply session arms', armed === 200, armed);

  // ── the log stays collapsed ───────────────────────────────────────────────
  await send('Page.navigate', { url: BASE + '/qubits' });
  await sleep(5000);
  const log0 = await ev(`(function () {
    var l = document.getElementById('applied-log');
    if (!l) return { present: false };
    var list = l.querySelector('.applied-log-list');
    return { present: true,
             collapsed: l.classList.contains('applied-log-collapsed'),
             listVisible: !!(list && list.offsetParent !== null) };
  })()`);
  ok('the applied-to-live log is present', log0.present, log0);
  ok('…and collapsed on a first visit', log0.collapsed && !log0.listVisible, log0);

  // one click opens it, and that choice IS remembered
  await ev(`(function () {
    var b = document.querySelector('.applied-log-toggle');
    if (b) b.click();
    return 1;
  })()`);
  await sleep(400);
  const log1 = await ev(`(function () {
    var l = document.getElementById('applied-log');
    var list = l && l.querySelector('.applied-log-list');
    return { collapsed: l && l.classList.contains('applied-log-collapsed'),
             listVisible: !!(list && list.offsetParent !== null),
             stored: (function () { try {
               return sessionStorage.getItem('quam_applied_log_open'); } catch (e) { return null; } })() };
  })()`);
  ok('a click opens it', !log1.collapsed && log1.listVisible, log1);
  ok('…and the deliberate open is remembered', log1.stored === '1', log1);

  // ── the value history offers a Revert ─────────────────────────────────────
  // Give the field a past value the honest way: snapshot, change it, apply,
  // snapshot again.
  const built = await ev(`(async function () {
    async function post(u, body) {
      var r = await fetch(u, { method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded',
                   'HX-Request': 'true' },
        body: body || '' });
      return r.status;
    }
    var a = await post('/state-history/snapshot');
    var b = await post('/field/edit', 'dot_path=qubits.q1.T1&value=9.9e-5');
    var c = await post('/state/apply-to-live');
    var d = await post('/state-history/snapshot');
    return { snap1: a, edit: b, apply: c, snap2: d };
  })()`);
  ok('the fixture built real history', built.snap1 === 200 && built.edit === 200
     && built.apply === 200 && built.snap2 === 200, built);

  // open the panel the way a person does: the 🕘 on an inspector row
  await send('Page.navigate', { url: BASE + '/qubit/q1' });
  await sleep(5000);
  const opened = await ev(`(async function () {
    var btns = document.querySelectorAll('form.inline-edit');
    for (var i = 0; i < btns.length; i++) {
      var h = btns[i].querySelector('input[name="dot_path"]');
      if (h && h.value === 'qubits.q1.T1') {
        var clock = btns[i].querySelector('.field-hist-btn');
        if (clock) { clock.click(); return true; }
      }
    }
    return false;
  })()`);
  ok('the 🕘 on the T1 row opens the panel', opened);
  await sleep(2500);

  const panel = await ev(`(function () {
    var p = document.getElementById('field-history-panel');
    if (!p || p.style.display === 'none') return { open: false };
    var rev = p.querySelector('.fh-revert');
    var delta = p.querySelector('.fh-revert-delta');
    var r = p.getBoundingClientRect();
    return {
      open: true,
      hasRevert: !!rev,
      revertText: rev ? rev.textContent.replace(/\\s+/g, ' ').trim() : null,
      revertValue: rev ? rev.getAttribute('data-value') : null,
      revertPath: rev ? rev.getAttribute('data-path') : null,
      hasDelta: !!delta,
      deltaText: delta ? delta.textContent.replace(/\\s+/g, ' ').trim() : null,
      sameRow: !!(rev && delta && rev.parentElement === delta.parentElement),
      overflowX: p.scrollWidth > p.clientWidth + 1,
      width: Math.round(r.width)
    };
  })()`);
  ok('the history panel is open', panel.open, panel);
  ok('it offers a Revert', panel.hasRevert && /Revert/.test(panel.revertText || ''),
     panel.revertText);
  ok('…carrying its own path, so it works with no edit input on screen',
     panel.revertPath === 'qubits.q1.T1', panel.revertPath);
  ok('…and the diff of what reverting would do, beside it',
     panel.hasDelta && panel.sameRow, panel);
  ok('the panel does not scroll sideways', !panel.overflowX,
     { w: panel.width });

  // Captured HERE, before the press: Revert closes the panel, so a shot taken
  // afterwards is a picture of the page without the thing being checked.
  await send('Page.captureScreenshot', { format: 'png' }).then(function (r) {
    if (r && r.result && r.result.data) {
      fs.writeFileSync(OUT.replace(/\.json$/, '_panel.png'),
                       Buffer.from(r.result.data, 'base64'));
    }
  });

  // ── pressing it stages, and does NOT touch the live chip ──────────────────
  // DISARM first. The log section above armed the session on purpose, and an
  // armed session flushes a staged edit to the chip within the same second
  // (docs/117) — so measuring "did the tray count rise" while it is on
  // measures the flusher, not the button. The button's own promise is the
  // disarmed one: staged, not yet on the live chip.
  const disarmed = await ev(`(async function () {
    var r = await fetch('/auto-apply/disarm', { method: 'POST',
      headers: { 'HX-Request': 'true' } });
    return r.status;
  })()`);
  ok('the session disarms for the staging check', disarmed === 200, disarmed);

  const before = await ev(`(async function () {
    var r = await fetch('/state/tray', { headers: { 'HX-Request': 'true' } });
    var h = await r.text();
    var m = /data-change-count="(\\d+)"/.exec(h);
    return m ? m[1] : null;
  })()`);
  // The row a PERSON would press: one whose value differs from the "now" line.
  // Reverting to the value the chip already holds is a no-op the server
  // correctly stages nothing for, and clicking the first button blindly was
  // hitting exactly that.
  const picked = await ev(`(function () {
    var p = document.getElementById('field-history-panel');
    var now = p.querySelector('.fh-currentline code');
    var nowV = now ? now.textContent.trim() : null;
    var btns = p.querySelectorAll('.fh-revert');
    for (var i = 0; i < btns.length; i++) {
      var v = btns[i].getAttribute('data-value');
      if (v && v !== nowV) { btns[i].click(); return { now: nowV, clicked: v }; }
    }
    return { now: nowV, clicked: null,
             offered: Array.prototype.map.call(btns, function (b) {
               return b.getAttribute('data-value'); }) };
  })()`);
  ok('a past value — one the chip does not already hold — is offered',
     !!picked.clicked, picked);
  await sleep(2500);
  const after = await ev(`(async function () {
    var r = await fetch('/state/tray', { headers: { 'HX-Request': 'true' } });
    var h = await r.text();
    var m = /data-change-count="(\\d+)"/.exec(h);
    // The CHANGE LIST, not the whole tray: the applied-to-live log lives in the
    // same markup and already names this path from the fixture's own apply, so
    // a bare indexOf was true BEFORE the press and proved nothing.
    return { count: m ? m[1] : null };
  })()`);
  // The COUNT, and only the count. `tray-change-path` is reused by the
  // applied-to-live log in the same markup, so "the path appears" was true
  // before the press as well — it proved nothing.
  ok('pressing Revert stages an edit',
     Number(after.count) > Number(before || 0), { before, after, picked });

  fs.writeFileSync(OUT, JSON.stringify({ results, log0, log1, built, panel,
                                         before, after, errors }, null, 1));
  const bad = results.filter(r => !r.pass);
  console.log('history revert: ' + (results.length - bad.length) + '/' + results.length
              + '  console errors: ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + ' ' + JSON.stringify(b.detail).slice(0, 300)));
  errors.slice(0, 3).forEach(e => console.log('  ERR ' + e));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
