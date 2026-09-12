/* Round 2 of the customer auto-sync report.
 *
 * Round 1 (three Enters, three DIFFERENT rows, 4s apart) did not reproduce, so
 * this varies the three things round 1 held constant:
 *   - all three Auto-Sync switches on (pull + replace + push), the real default
 *   - repeated Enters on the SAME row
 *   - rapid succession, so a flush is still in flight when the next lands
 *
 * Enter in the grid is not a cell commit -- it calls BulkEdit.applyRow(), and
 * that path is gated on `!b.disabled` while applyRow sets `disabled = true` and
 * restores it only inside its own .then. So the state worth recording after
 * every press is: the row button, the shared _applyInFlight latch the flusher
 * waits on, the tray, and what actually reached the live file.
 *
 * argv[2]=out.json argv[3]=cdp argv[4]=base argv[5]=liveFolder
 */
const fs = require('fs');
const path = require('path');
const OUT = process.argv[2], CDP = process.argv[3], BASE = process.argv[4];
const LIVE = process.argv[5];
const steps = [], errors = [];

function onDisk(dot) {
  try {
    let o = JSON.parse(fs.readFileSync(path.join(LIVE, 'state.json'), 'utf8'));
    for (const seg of dot.split('.')) o = o[seg];
    return o;
  } catch (e) { return 'ERR'; }
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

  // all three switches — the real default
  await ev(`fetch('/auto-sync/set', { method: 'POST',
     headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'HX-Request': 'true' },
     body: 'pull=1&pull_replace=1&push=1' }).then(function(r){return r.status;})`);
  await ev(`(async function () {
    var html = await (await fetch('/state/tray', { headers: { 'HX-Request': 'true' } })).text();
    var t = document.getElementById('pending-tray');
    if (t) t.outerHTML = html;
    return true;
  })()`);
  await sleep(1200);
  const armed = await ev(`(function(){var t=document.getElementById('pending-tray');
     return {auto:t&&t.getAttribute('data-auto-apply')};})()`);

  // Four presses on the SAME row, alternating between two cells of that row.
  const ROW = 'q1';
  const COLS = ['saturation_amplitude', 'saturation_amplitude',
                'saturation_amplitude', 'saturation_amplitude'];
  const VALS = ['0.0061', '0.0062', '0.0063', '0.0064'];
  const GAPS = [3500, 400, 400, 3500];   // two slow, two inside a flush

  let dot = null;
  for (let i = 0; i < COLS.length; i++) {
    const at = await ev(`(function () {
      var td = document.querySelector('tr[data-qubit="${ROW}"] td[data-col-key="${COLS[i]}"]');
      if (!td) return { __no: 'no cell' };
      var inp = td.querySelector('input.bulk-cell');
      if (!inp) return { __no: 'no input' };
      inp.scrollIntoView({ block: 'center' });
      var r = inp.getBoundingClientRect();
      return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2),
               dot: inp.getAttribute('data-dot-path') };
    })()`);
    if (at.__no) { steps.push({ i: i + 1, error: at.__no }); continue; }
    dot = at.dot;

    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: at.x, y: at.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: at.x, y: at.y, button: 'left', clickCount: 1 });
    await sleep(200);
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65 });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65 });
    for (const ch of VALS[i]) {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, key: ch });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
    }
    await sleep(120);

    // state the instant BEFORE Enter
    const pre = await ev(`(function(){
      var tr=document.querySelector('tr[data-qubit="${ROW}"]');
      var b=tr&&tr.querySelector('.bulk-row-apply');
      var inp=tr&&tr.querySelector('td[data-col-key="${COLS[i]}"] input.bulk-cell');
      return {btnDisabled:b?b.disabled:null, btnText:b?b.textContent:null,
              typed:inp?inp.value:null, dirty:inp?inp.classList.contains('dirty'):null,
              latch:!!window._applyInFlight};})()`);

    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    await sleep(GAPS[i]);

    const post = await ev(`(function(){
      var t=document.getElementById('pending-tray');
      var tr=document.querySelector('tr[data-qubit="${ROW}"]');
      var b=tr&&tr.querySelector('.bulk-row-apply');
      var body=document.body.innerHTML;
      return {btnDisabled:b?b.disabled:null, btnText:b?b.textContent:null,
              latch:!!window._applyInFlight,
              auto:t&&t.getAttribute('data-auto-apply'),
              count:t&&t.getAttribute('data-change-count'),
              dirty:t&&t.getAttribute('data-working-dirty'),
              conflict:!!document.querySelector('.pending-tray-conflict'),
              pullApply:body.indexOf('Pull &amp; apply')>=0||body.indexOf('Pull & apply')>=0};})()`);

    steps.push({ i: i + 1, want: VALS[i], gap: GAPS[i], pre: pre, post: post,
                 disk: dot ? onDisk(dot) : null });
  }

  // let the pollers run with nothing happening, then look again
  await sleep(12000);
  const settle = await ev(`(function(){
    var t=document.getElementById('pending-tray');
    var body=document.body.innerHTML;
    return {auto:t&&t.getAttribute('data-auto-apply'),
            count:t&&t.getAttribute('data-change-count'),
            dirty:t&&t.getAttribute('data-working-dirty'),
            latch:!!window._applyInFlight,
            conflict:!!document.querySelector('.pending-tray-conflict'),
            pullApply:body.indexOf('Pull &amp; apply')>=0||body.indexOf('Pull & apply')>=0,
            banner:body.indexOf('live chip changed since you loaded it')>=0};})()`);

  fs.writeFileSync(OUT, JSON.stringify({ armed, steps, settle, errors }, null, 2));
  console.log('armed:', JSON.stringify(armed), 'dot:', dot);
  for (const s of steps) {
    if (s.error) { console.log(' #' + s.i, 'ERROR', s.error); continue; }
    console.log(' #' + s.i, 'want', s.want, 'gap', s.gap,
      '| PRE btnDisabled=' + s.pre.btnDisabled, 'typed=' + s.pre.typed, 'dirty=' + s.pre.dirty, 'latch=' + s.pre.latch,
      '| POST btnDisabled=' + s.post.btnDisabled, 'count=' + s.post.count, 'wdirty=' + s.post.dirty,
      'latch=' + s.post.latch, 'conflict=' + s.post.conflict, 'pull&apply=' + s.post.pullApply,
      '| disk=' + s.disk);
  }
  console.log('after 12s idle:', JSON.stringify(settle));
  console.log('console errors:', errors.length);
  errors.slice(0, 6).forEach(e => console.log('   ', e));
  ws.close();
}
main().catch(e => { console.error('DRIVER FAILED', e); process.exit(1); });
