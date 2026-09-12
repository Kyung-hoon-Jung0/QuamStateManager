/* Customer report (2026-09-12), two symptoms that may be one mechanism:
 *
 *   "live edit에서 enter를 누르면 반영이 그 다음부터 auto sync가 깨짐"
 *   "이유없이 pull&apply 문구가 뜬다 (외부 변경 없는데도)"
 *
 * Nothing outside SM writes the chip in this run -- the live folder is a fresh
 * copy nobody else has open -- so any conflict tray that appears is SM's own.
 *
 * Drives REAL Enter keypresses in the Live Edit grid with Auto-Sync push
 * armed, one edit at a time, and after each one records what the tray says and
 * what the live file on disk actually holds.
 *
 * argv[2]=out.json argv[3]=cdp argv[4]=base argv[5]=liveFolder
 */
const fs = require('fs');
const OUT = process.argv[2], CDP = process.argv[3], BASE = process.argv[4];
const LIVE = process.argv[5];
const path = require('path');
const rounds = [], errors = [];

function liveT1(q) {
  try {
    const j = JSON.parse(fs.readFileSync(path.join(LIVE, 'state.json'), 'utf8'));
    const p0 = j.qubits[q]['saturation_pulse'] || {};
    return p0.amplitude;
  } catch (e) { return 'READ-ERR ' + e.message; }
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
  await send('Runtime.enable'); await send('Page.enable'); await send('Input.enable').catch(() => {});

  // ── open the grid ──────────────────────────────────────────────────────
  await send('Page.navigate', { url: BASE + '/bulk' });
  await sleep(9000);

  // ── arm Auto-Sync with push, through the real door ─────────────────────
  const arm = await ev(`(async function () {
    var r = await fetch('/auto-sync/set', { method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded',
                 'HX-Request': 'true' },
      body: 'pull=1&pull_replace=0&push=1' });
    return { status: r.status };
  })()`);
  // The page was rendered BEFORE arming, so its tray still carries the old
  // attributes and the observer would never see data-auto-apply. Swap in the
  // real tray the way every other surface does.
  await ev(`(async function () {
    var html = await (await fetch('/state/tray', { headers: { 'HX-Request': 'true' } })).text();
    var t = document.getElementById('pending-tray');
    if (t) t.outerHTML = html;
    return true;
  })()`);
  await sleep(1500);
  const armed = await ev(`(function () {
    var t = document.getElementById('pending-tray');
    return { auto: t && t.getAttribute('data-auto-apply'),
             seq: t && t.getAttribute('data-seq') };
  })()`);

  // ── N edits, each committed with a REAL Enter ──────────────────────────
  // saturation_amplitude: visible, plain-numeric, and not an FSP-linked
  // amplitude (editing one of those opens the compensation offer, which is a
  // different conversation from the one this report is about).
  const COL = 'saturation_amplitude';
  const QS = ['q1', 'q2', 'q3'];
  for (let i = 0; i < QS.length; i++) {
    const q = QS[i];
    const want = '0.00' + (i + 6);

    // focus the cell's input the way a person does: click it.
    const at = await ev(`(function () {
      var td = document.querySelector('tr[data-qubit="${q}"] td[data-col-key="${COL}"]');
      if (!td) return { __no: 'no ${COL} cell for ${q}' };
      var inp = td.querySelector('input.bulk-cell');
      if (!inp) return { __no: 'no input in the ${q} ${COL} cell' };
      inp.scrollIntoView({ block: 'center' });
      var r = inp.getBoundingClientRect();
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
               before: inp.value, dot: inp.getAttribute('data-dot-path') };
    })()`);
    if (at.__no) { rounds.push({ q: q, error: at.__no }); continue; }

    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: at.x, y: at.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: at.x, y: at.y, button: 'left', clickCount: 1 });
    await sleep(300);
    // select-all then type the new value, character by character
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65 });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65 });
    for (const ch of want) {
      // `text` on keyDown ONLY -- putting it on char as well types twice
      await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, key: ch });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
    }
    await sleep(200);
    // THE ENTER the report is about
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });

    await sleep(4000);   // let the observer flush and the response land

    const after = await ev(`(function () {
      var t = document.getElementById('pending-tray');
      var body = document.body.innerHTML;
      return {
        auto: t && t.getAttribute('data-auto-apply'),
        count: t && t.getAttribute('data-change-count'),
        dirty: t && t.getAttribute('data-working-dirty'),
        seq: t && t.getAttribute('data-seq'),
        conflictTray: !!document.querySelector('.pending-tray-conflict'),
        saysPullApply: body.indexOf('Pull &amp; apply') >= 0 || body.indexOf('Pull & apply') >= 0,
        saysChanged: body.indexOf('live chip changed since you loaded it') >= 0,
        cell: (function () {
          var td = document.querySelector('tr[data-qubit="${q}"] td[data-col-key="${COL}"]');
          var inp = td && td.querySelector('input.bulk-cell');
          return inp ? inp.value : null;
        })()
      };
    })()`);

    rounds.push({ n: i + 1, q: q, wanted: want, dot: at.dot, ui: after, onDisk: liveT1(q) });
  }

  // ── what the server thinks now ─────────────────────────────────────────
  const final = await ev(`(async function () {
    var s = await (await fetch('/auto-apply/status')).json().catch(function () { return null; });
    var d = await (await fetch('/state/drift', { headers: { 'HX-Request': 'true' } })).text().catch(function () { return ''; });
    return { status: s, driftMentionsDiverged: d.indexOf('diverged') >= 0 || d.indexOf('changed since') >= 0 };
  })()`);

  fs.writeFileSync(OUT, JSON.stringify({ arm, armed, rounds, final, errors }, null, 2));
  console.log('arm:', JSON.stringify(arm), 'tray armed:', JSON.stringify(armed));
  for (const r of rounds) {
    if (r.error) { console.log('  round', r.q, 'ERROR', r.error); continue; }
    console.log('  #' + r.n, r.q, 'wanted', r.wanted,
      '| auto=' + r.ui.auto, 'count=' + r.ui.count, 'dirty=' + r.ui.dirty,
      '| conflict=' + r.ui.conflictTray, 'pull&apply=' + r.ui.saysPullApply,
      '| onDisk=' + r.onDisk);
  }
  console.log('final:', JSON.stringify(final));
  console.log('console errors:', errors.length);
  errors.slice(0, 5).forEach(e => console.log('   ', e));
  ws.close();
}
main().catch(e => { console.error('DRIVER FAILED', e); process.exit(1); });
