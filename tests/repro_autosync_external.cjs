/* Round 3. Rounds 1-2 could not reproduce because they held the one thing the
 * customer's bench does constantly: ANOTHER PROGRAM WRITING THE CHIP.
 *
 * Evidence this is their shape, not a guess:
 *   instances/4928.json  chip_path = D:\work\...\260907_KRS_5Q  (SM on :5050)
 *   ~/.qualibrate/config.toml  state_path = the same folder
 *   qualibrate.log  "Saving machine to active path <that folder>" every 30-60s
 *
 * So: arm Auto-Sync (pull + push), let an outside writer touch the live chip
 * the way a node's machine.save() does, then press Enter in Live Edit and
 * record what the user is left looking at.
 *
 * argv[2]=out.json argv[3]=cdp argv[4]=base argv[5]=liveFolder
 */
const fs = require('fs');
const path = require('path');
const OUT = process.argv[2], CDP = process.argv[3], BASE = process.argv[4];
const LIVE = process.argv[5];
const log = [], errors = [];

// What a qualibrate node does at the end of a run: rewrite state.json with a
// new fitted value. Deliberately a DIFFERENT leaf from the one being edited in
// the browser, so nothing here is a genuine edit-vs-edit conflict.
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
  const snap = async (label) => {
    const s = await ev(`(function(){
      var tr=document.getElementById('pending-tray');
      var body=document.body.innerHTML;
      return {auto:tr&&tr.getAttribute('data-auto-apply'),
              count:tr&&tr.getAttribute('data-change-count'),
              wdirty:tr&&tr.getAttribute('data-working-dirty'),
              pillOn:!!document.querySelector('.auto-apply-pill.auto-apply-on'),
              pillOff:!!document.querySelector('.auto-apply-pill.auto-apply-off'),
              conflict:!!document.querySelector('.pending-tray-conflict'),
              pullApply:body.indexOf('Pull &amp; apply')>=0||body.indexOf('Pull & apply')>=0,
              banner:/changed on disk|live chip changed/.test(body),
              bannerVisible:(function(){
                 var b=document.querySelector('#live-diverged-slot');
                 if(!b||!b.textContent.trim())return false;
                 var el=b.firstElementChild||b;
                 return !!(el.offsetParent||el.getClientRects().length);})(),
              saysHowMany:/\\d+\\s+values? differ/.test(body),
              saysTurnedOff:/has been turned/.test(body),
              saysStillOn:/still on and is resolving/.test(body),
              pillBlocked:!!document.querySelector('.auto-apply-pill.auto-apply-blocked'),
              bannerText:(function(){
                 var b=document.querySelector('#live-diverged-slot');
                 return b?b.textContent.replace(/\\s+/g,' ').trim().slice(0,220):null;})()};})()`);
    log.push({ at: label, ui: s });
    return s;
  };

  await send('Runtime.enable'); await send('Page.enable');
  await send('Page.navigate', { url: BASE + '/bulk' });
  await sleep(9000);

  await ev(`fetch('/auto-sync/set', { method: 'POST',
     headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'HX-Request': 'true' },
     body: '${process.argv[6] === 'pushonly' ? 'pull=0&pull_replace=0&push=1' : 'pull=1&pull_replace=0&push=1'}' }).then(function(r){return r.status;})`);
  await ev(`(async function () {
    var html = await (await fetch('/state/tray', { headers: { 'HX-Request': 'true' } })).text();
    var t = document.getElementById('pending-tray');
    if (t) t.outerHTML = html;
    return true;
  })()`);
  await sleep(1200);
  await snap('armed, nothing happening');

  // ── an outside program writes the chip, exactly like a node finishing ──
  const wrote = externalWrite(3.21e-5);
  // give SM's drift poll a chance to notice, the way it would on the bench
  await sleep(14000);
  await snap('after the outside write (no user action)');

  // ── now the user edits a cell and presses Enter ────────────────────────
  const at = await ev(`(function () {
    var td = document.querySelector('tr[data-qubit="q1"] td[data-col-key="saturation_amplitude"]');
    var inp = td && td.querySelector('input.bulk-cell');
    if (!inp) return { __no: 'no cell' };
    inp.scrollIntoView({ block: 'center' });
    var r = inp.getBoundingClientRect();
    return { x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2),
             dot: inp.getAttribute('data-dot-path') };
  })()`);
  if (!at.__no) {
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: at.x, y: at.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: at.x, y: at.y, button: 'left', clickCount: 1 });
    await sleep(200);
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65 });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65 });
    for (const ch of '0.0099') {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, key: ch });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
    }
    await sleep(150);
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    await sleep(6000);
  }
  const afterEnter = await snap('after Enter with the chip moved underneath');

  // ── and a SECOND edit: is auto-sync still alive at all? ────────────────
  if (!at.__no) {
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: at.x, y: at.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: at.x, y: at.y, button: 'left', clickCount: 1 });
    await sleep(200);
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65 });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', modifiers: 2, windowsVirtualKeyCode: 65 });
    for (const ch of '0.0088') {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, key: ch });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
    }
    await sleep(150);
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    await sleep(7000);
  }
  await snap('after a SECOND edit');

  const disk = (() => {
    try {
      const j = JSON.parse(fs.readFileSync(path.join(LIVE, 'state.json'), 'utf8'));
      return { q5T1: j.qubits.q5.T1,
               edited: j.qubits.q1.xy.operations.saturation.amplitude };
    } catch (e) { return 'ERR ' + e.message; }
  })();

  fs.writeFileSync(OUT, JSON.stringify({ wrote, log, disk, errors }, null, 2));
  for (const l of log) {
    console.log('[' + l.at + ']');
    console.log('   auto=' + l.ui.auto, 'pillOn=' + l.ui.pillOn, 'pillOff=' + l.ui.pillOff,
      'count=' + l.ui.count, 'wdirty=' + l.ui.wdirty);
    console.log('   conflict=' + l.ui.conflict, 'pull&apply=' + l.ui.pullApply,
      'banner=' + l.ui.banner, 'pillBlocked=' + l.ui.pillBlocked,
      '| saysTurnedOff=' + l.ui.saysTurnedOff, 'saysStillOn=' + l.ui.saysStillOn);
    if (l.ui.bannerText) console.log('   banner: ' + l.ui.bannerText);
  }
  console.log('on disk:', JSON.stringify(disk), '(outside write was', wrote + ')');
  console.log('console errors:', errors.length);
  ws.close();
}
main().catch(e => { console.error('DRIVER FAILED', e); process.exit(1); });
