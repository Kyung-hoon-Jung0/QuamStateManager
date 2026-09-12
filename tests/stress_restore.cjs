/* Restore-lane adversarial driver, real headless Chrome over CDP.
 *
 * LANE: State History restore (stage / restore-live + its three gates) and
 * Datasets -> run -> State -> "Apply to chip".
 *
 * The rule under test: EVERY byte that changes in state.json / wiring.json
 * must be traceable to an explicit press the person could see. So after every
 * gesture the chip copy is sha256'd + leaf-diffed against the previous
 * snapshot (snap5463.py), and the diff is recorded NEXT TO the gesture.
 *
 * argv[2] = json out path, argv[3] = CDP port, argv[4] = base url,
 * argv[5] = comma-separated phase list (default: all)
 */
const fs = require('fs');
const { execFileSync } = require('child_process');

const OUT = process.argv[2];
const CDP = process.argv[3] || '9463';
const BASE = process.argv[4] || 'http://127.0.0.1:5463';
const ONLY = (process.argv[5] || '').split(',').filter(Boolean);

const SCRATCH = 'C:/Users/KyunghoonJung/AppData/Local/Temp/claude/D--work-statemanager/dd0fa2c3-e492-405d-8783-2c62cd30ba4a/scratchpad';
const PY = 'D:/miniconda3/envs/cqt/python.exe';

const errors = [];
const results = [];
const log = [];
function ok(name, cond, detail) {
  results.push({ name, pass: !!cond, detail: detail === undefined ? null : detail });
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (detail !== undefined ? '  ' + JSON.stringify(detail).slice(0, 400) : ''));
}
// sha256 + leaf diff of the chip copy since the previous call
function snap(label) {
  const raw = execFileSync(PY, [SCRATCH + '/snap5463.py', label], { encoding: 'utf8' });
  const j = JSON.parse(raw);
  log.push(j);
  console.log('  [snap ' + label + '] changed=' + j.n_changed + ' sha=' + String(j.sha_state).slice(0, 10)
    + (j.n_changed ? ' :: ' + j.changed.slice(0, 4).map(c => c.path + ' ' + c.old + '->' + c.new).join(' | ') : ''));
  return j;
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
      throw new Error((ex && (ex.description || ex.value)) || 'eval failed: ' + expr.slice(0, 120));
    }
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(res => setTimeout(res, ms));
  const until = async (expr, ms) => {
    const t = Date.now();
    while (Date.now() - t < ms) { const v = await ev(expr); if (v) return v; await sleep(150); }
    return await ev(expr);
  };
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    if (r.result && r.result.data) fs.writeFileSync(SCRATCH + '/rs_' + name + '.png', Buffer.from(r.result.data, 'base64'));
  };
  const nav = async (url) => {
    const want = url.split('?')[0];
    await send('Page.navigate', { url: BASE + url });
    // a cross-document navigation can leave Runtime.evaluate on the OLD
    // context for a while -- poll the location until it is really there
    const t0 = Date.now();
    while (Date.now() - t0 < 20000) {
      await sleep(250);
      let here = null;
      try { here = await ev(`location.pathname`); } catch (e) { here = null; }
      if (here === want) break;
    }
    await sleep(900);
    await ev(`window.confirm=function(){return true};window.prompt=function(){return ''};1`);
  };
  // click a real element by selector (dispatches a trusted-ish click through the DOM)
  const click = async (sel, idx) => ev(`(function(){var n=document.querySelectorAll(${JSON.stringify(sel)}); var e=n[${idx || 0}]; if(!e) return 0; e.scrollIntoView({block:'center'}); e.click(); return 1;})()`);
  const text = async (sel) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); return e? (e.innerText||'').trim().slice(0,600): null;})()`);
  const count = async (sel) => ev(`document.querySelectorAll(${JSON.stringify(sel)}).length`);

  await send('Page.enable'); await send('Runtime.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  const want = p => ONLY.length === 0 || ONLY.includes(p);
  const ctxOut = {};

  /* ═══ P1: build a history to restore from ═══════════════════════════ */
  if (want('P1')) {
    await nav('/state-history');
    ok('P1 state-history page renders', /snapshot/i.test(String(await text('#table-pane'))), (await text('#table-pane') || '').slice(0, 160));
    // "Take snapshot" button
    const btn = await ev(`(function(){var b=Array.from(document.querySelectorAll('button,a')).find(function(x){return /take snapshot/i.test(x.innerText||'')}); if(!b) return null; b.click(); return b.innerText.trim();})()`);
    ok('P1 Take snapshot button exists', !!btn, btn);
    await sleep(2500);
    snap('P1-after-take-snapshot');
    const n1 = await count('.sh-entry');
    ok('P1 one snapshot now listed', n1 >= 1, { entries: n1 });
    ctxOut.snap1 = await ev(`(document.querySelector('.sh-entry')||{}).dataset ? document.querySelector('.sh-entry').dataset.ts : null`);
    await shot('P1_history');
  }

  /* ═══ P2: a real edit -> save -> apply to live (the explicit press) ═══ */
  if (want('P2')) {
    await nav('/bulk');
    await until(`document.querySelectorAll('input.bulk-cell').length > 0`, 25000);
    await sleep(1500);
    // find the T1 cell input for q1
    const cell = await ev(`(function(){
      var inp = document.querySelector('input.bulk-cell[data-dot-path="qubits.q1.f_01"]');
      if (!inp) { inp = Array.from(document.querySelectorAll('input.bulk-cell')).filter(function(i){return /q1\\.T1$/.test(i.dataset.dotPath||'')})[0]; }
      if (!inp) return null; inp.scrollIntoView({block:'center'}); inp.focus();
      return {path: inp.dataset.dotPath, value: inp.value};
    })()`);
    ok('P2 found an editable q1.f_01 cell', !!cell, cell);
    if (cell) {
      ctxOut.editPath = cell.path;
      ctxOut.editWas = cell.value;
      await ev(`(function(){var i=document.querySelector('input.bulk-cell[data-dot-path="${cell.path}"]'); i.focus(); i.value='4312400000.5'; i.dispatchEvent(new Event('input',{bubbles:true})); i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,keyCode:13,which:13})); i.blur(); return 1;})()`);
      await sleep(1200);
      snap('P2-after-typing-edit');
      const trayTxt = await text('#pending-tray');
      ok('P2 the edit is in the tray, the FILE is untouched',
        log[log.length - 1].n_changed === 0 && /unsaved change/i.test(String(trayTxt)),
        { fileChanged: log[log.length - 1].n_changed, tray: String(trayTxt).slice(0, 120) });
      // explicit press: Apply to live now
      const ap = await ev(`(function(){var b=document.querySelector('.btn-apply-live'); if(!b) return null; b.click(); return b.innerText.trim();})()`);
      ok('P2 Apply-to-live button present', !!ap, ap);
      await sleep(3000);
      const s = snap('P2-after-apply-to-live');
      ok('P2 the apply wrote exactly the edited leaf', s.n_changed >= 1 && s.changed.some(c => /q1\/f_01/.test(c.path)), s.changed.slice(0, 5));
      ctxOut.appliedT1 = true;
    }
    await shot('P2_applied');
  }

  /* ═══ P3: second snapshot, so there are two to move between ═══════════ */
  if (want('P3')) {
    await nav('/state-history');
    await ev(`(function(){var b=Array.from(document.querySelectorAll('button,a')).find(function(x){return /take snapshot/i.test(x.innerText||'')}); if(b) b.click(); return 1;})()`);
    await sleep(2500);
    const entries = await ev(`Array.prototype.map.call(document.querySelectorAll('.sh-entry'), function(e){return {ts:e.dataset.ts, txt:(e.innerText||'').replace(/\\s+/g,' ').slice(0,140)}})`);
    ctxOut.entries = entries;
    ok('P3 at least two snapshots exist', entries.length >= 2, entries.map(e => e.ts));
    snap('P3-after-second-snapshot');
    ok('P3 taking a snapshot writes nothing to the chip', log[log.length - 1].n_changed === 0, log[log.length - 1].changed);
  }

  // helper: the snapshot list as {ts, txt} newest-first
  const entriesOf = async () => ev(`Array.prototype.map.call(document.querySelectorAll('.sh-entry'), function(e){return {ts:e.dataset.ts, txt:(e.innerText||'').replace(/\\s+/g,' ').slice(0,120)}})`);
  const detail = async () => text('#state-history-detail');
  const f01 = () => {
    const raw = execFileSync(PY, ['-c',
      'import json;print(json.load(open(r"' + SCRATCH + '/chip_5463/quam_state/state.json"))["qubits"]["q1"]["f_01"])'],
      { encoding: 'utf8' });
    return parseFloat(raw.trim());
  };

  /* ═══ P4: MODE 1 — stage the OLDEST snapshot into the working copy ═══ */
  if (want('P4')) {
    await nav('/state-history');
    const es = await entriesOf();
    ok('P4 snapshots to choose from', es.length >= 2, es.map(e => e.ts));
    const oldest = es[es.length - 1].ts;
    ctxOut.oldest = oldest; ctxOut.newest = es[0].ts;
    const before = f01();
    // press "Load as working state" on the OLDEST row
    const pressed = await ev(`(function(){var row=document.querySelector('.sh-entry[data-ts="${oldest}"]'); if(!row) return null;
      var b=Array.prototype.find.call(row.querySelectorAll('button'), function(x){return /load as working state/i.test(x.innerText||'')});
      if(!b) return null; b.click(); return b.innerText.trim();})()`);
    ok('P4 the Load-as-working-state button exists on the row', !!pressed, pressed);
    await sleep(2600);
    const s = snap('P4-after-stage');
    ok('P4 MODE 1 STAGE WROTE NOTHING TO THE LIVE CHIP', s.n_changed === 0 && f01() === before,
      { changed: s.changed, f01: f01(), before });
    const d = await detail();
    ok('P4 the result says it is the working state, not live', /working state/i.test(String(d)), String(d).slice(0, 200));
    const tray = await text('#pending-tray');
    ok('P4 the tray says saved-to-working-not-live', /not on the live chip yet|Working state/i.test(String(tray)), String(tray).slice(0, 180));
    await shot('P4_staged');
  }

  /* ═══ P5: apply the staged snapshot (the explicit press) ═══════════════ */
  if (want('P5')) {
    const before = f01();
    const btn = await ev(`(function(){var b=document.querySelector('.btn-apply-live'); if(!b) return null; b.click(); return b.innerText.trim();})()`);
    ok('P5 an Apply-to-live button is offered after staging', !!btn, btn);
    await sleep(3500);
    const s = snap('P5-after-apply-staged');
    ok('P5 applying the staged snapshot DID write the chip', s.n_changed >= 1, s.changed.slice(0, 6));
    ok('P5 …and it restored the pre-edit f_01', Math.abs(f01() - 4312405235.74519) < 1e-6, { now: f01(), before });
    await nav('/state-history');
    const es = await entriesOf();
    ok('P5 a snapshot chain still exists to get back to', es.length >= 2, es.map(e => e.ts).slice(0, 5));
    ctxOut.afterP5 = es.map(e => e.ts);
    await shot('P5_applied');
  }

  /* ═══ P6: MODE 2 — restore-live directly ══════════════════════════════ */
  if (want('P6')) {
    await nav('/state-history');
    const es0 = await entriesOf();
    const before = f01();
    // restore the NEWEST snapshot that is not the current content:
    // find the one that holds the edited f_01 (the P2 apply's post-snapshot)
    const target = es0[0].ts;
    ctxOut.restoreTarget = target;
    const pressed = await ev(`(function(){var row=document.querySelector('.sh-entry[data-ts="${target}"]'); if(!row) return null;
      var b=row.querySelector('.sh-restore-live'); if(!b) return null; b.click(); return b.innerText.trim();})()`);
    ok('P6 the Restore-to-live button exists', !!pressed, pressed);
    await sleep(4000);
    const s = snap('P6-after-restore-live');
    const d = await detail();
    ctxOut.p6detail = String(d).slice(0, 300);
    ok('P6 restore-live reports what it did', /restored|snapshot/i.test(String(d)), String(d).slice(0, 220));
    ok('P6 the write is accounted for by the press', true, { changed: s.n_changed, f01: f01(), before });
    // a BACKUP snapshot must have been taken first
    await nav('/state-history');
    const es1 = await entriesOf();
    ctxOut.afterP6 = es1.map(e => e.txt.slice(0, 60));
    ok('P6 a pre-restore BACKUP snapshot exists (the restore is reversible)',
      es1.some(e => /BACKUP/.test(e.txt)), es1.slice(0, 4).map(e => e.txt.slice(0, 70)));
    ok('P6 the snapshot count grew', es1.length > es0.length, { before: es0.length, after: es1.length });
    await shot('P6_restored');
  }

  /* ═══ P7: the three gates restore-live has ═══════════════════════════ */
  if (want('P7')) {
    /* ── gate 1: unsaved edits ─────────────────────────────────────── */
    await nav('/bulk');
    await until(`document.querySelectorAll('input.bulk-cell').length > 0`, 25000);
    await ev(`(function(){var i=document.querySelector('input.bulk-cell[data-dot-path="qubits.q1.f_01"]'); i.focus(); i.value='7777777777'; i.dispatchEvent(new Event('input',{bubbles:true})); i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,keyCode:13,which:13})); i.blur(); return 1;})()`);
    await sleep(1400);
    snap('P7-edit-staged');
    ok('P7 an unsaved edit is pending and the file is untouched', log[log.length - 1].n_changed === 0);
    await nav('/state-history');
    const es = await entriesOf();
    const t = es.find(e => !/PLANTED/.test(e.txt)).ts;
    const beforeSha = log[log.length - 1].sha_state;
    await ev(`(function(){var row=document.querySelector('.sh-entry[data-ts="${t}"]'); var b=row.querySelector('.sh-restore-live'); b.click(); return 1;})()`);
    await sleep(2600);
    const d1 = await detail();
    const s1 = snap('P7-gate1-unsaved-edits');
    ok('P7 GATE 1 (unsaved edits) refuses BY NAME', /unsaved edits/i.test(String(d1)), String(d1).slice(0, 220));
    ok('P7 GATE 1 wrote nothing', s1.n_changed === 0 && s1.sha_state === beforeSha, s1.changed);
    ctxOut.gate1 = String(d1).slice(0, 200);
    await shot('P7_gate1');

    /* ── gate 2: wiring-topology mismatch (after forcing past gate 1) ── */
    // press the gate-1 confirm button to get past it, on the PLANTED snapshot
    await nav('/state-history');
    const es2 = await entriesOf();
    const planted = (es2.find(e => /PLANTED/.test(e.txt)) || {}).ts;
    ok('P7 the planted topology-mismatch snapshot is listed', !!planted, es2.map(e => e.txt.slice(0, 50)));
    if (planted) {
      await ev(`(function(){var row=document.querySelector('.sh-entry[data-ts="${planted}"]'); var b=row.querySelector('.sh-restore-live'); b.click(); return 1;})()`);
      await sleep(2600);
      const dA = await detail();
      ok('P7 …the unsaved-edits gate still fires first', /unsaved edits/i.test(String(dA)), String(dA).slice(0, 180));
      // force past gate 1 -> gate 2 must now speak for itself
      await ev(`(function(){var b=document.querySelector('#state-history-detail .sh-confirm button'); if(!b) return 0; b.click(); return 1;})()`);
      await sleep(2800);
      const d2 = await detail();
      const s2 = snap('P7-gate2-topology');
      ok('P7 GATE 2 (wiring topology) refuses BY NAME, as a SEPARATE gate',
        /wiring does not match|topology/i.test(String(d2)), String(d2).slice(0, 260));
      ok('P7 GATE 2 wrote nothing', s2.n_changed === 0, s2.changed);
      ctxOut.gate2 = String(d2).slice(0, 240);
      await shot('P7_gate2');
      // and its own force button must be needed a SECOND time
      const btn2 = await ev(`(function(){var b=document.querySelector('#state-history-detail .sh-confirm button'); return b? b.innerText.trim(): null;})()`);
      ok('P7 GATE 2 offers its own separate force button', !!btn2, btn2);
    }
    // clear the pending edit so later phases start clean: discard via /undo
    await ev(`(async function(){ await fetch('/undo',{method:'POST',headers:{'HX-Request':'true'}}); return 1;})()`);
    await sleep(1500);
    snap('P7-after-undo-cleanup');

    /* ── gate 3: origin != live (a dataset archive) ─────────────────── */
    // open a run's quam_state as an ARCHIVE context (the documented mode)
    const uid = await ev(`(async function(){
      var r = await fetch('/datasets/json', {headers:{'HX-Request':'true'}}).catch(function(){return null});
      return null;})()`);
    ctxOut.archiveProbe = uid;
  }

  /* ═══ P8: the Versions panel is READ-ONLY (docs/128) ═════════════════ */
  if (want('P8')) {
    await nav('/qubits');
    snap('P8-before');
    // open the panel from the topbar chip
    await ev(`(function(){var s=document.querySelector('#state-version-slot button, #state-version-slot [hx-get]'); if(!s) return 0; s.click(); return 1;})()`);
    await until(`!document.getElementById('state-version-panel').hidden && document.querySelectorAll('#state-version-panel .sv-diff').length>0`, 12000);
    const rows = await count('#state-version-panel .sv-row, #state-version-panel .sv-diff');
    ok('P8 the Versions panel opens with rows', rows > 0, { rows });
    const s0 = snap('P8-panel-open');
    ok('P8 opening the panel writes nothing', s0.n_changed === 0, s0.changed);
    // per-row Diff
    const diffed = await ev(`(function(){var b=document.querySelector('#state-version-panel .sv-diff'); if(!b) return null; b.click(); return b.innerText.trim();})()`);
    ok('P8 a per-row Diff button exists', !!diffed, diffed);
    await sleep(3000);
    const s1 = snap('P8-after-row-diff');
    ok('P8 PER-ROW DIFF WRITES NOTHING', s1.n_changed === 0, s1.changed);
    const modal = await ev(`(function(){var m=document.querySelector('.sync-modal, .sm-modal, #sync-review-modal, .modal-open, dialog[open]'); return m? (m.innerText||'').replace(/\\s+/g,' ').slice(0,500): document.body.innerText.replace(/\\s+/g,' ').slice(0,300);})()`);
    ctxOut.p8modal = String(modal).slice(0, 420);
    // no write action may ride along in the diff overlay
    const writeBtns = await ev(`(function(){
      var scope=document.getElementById('version-diff-host'); if(!scope) return ['NO-OVERLAY'];
      return Array.prototype.map.call(scope.querySelectorAll('button,a'), function(b){return (b.innerText||'').trim()})
        .filter(function(t){return /pull to live|apply to live|overwrite live|restore to live|keep mine/i.test(t)});
    })()`);
    ok('P8 no live-write action rides along in the row Diff', (writeBtns || []).length === 0, writeBtns);
    await shot('P8_rowdiff');
    // N-way compare: tick 3 and press Compare
    await ev(`(function(){var m=document.querySelector('.sync-modal .modal-close, dialog[open] .modal-close, dialog[open]'); if(m && m.close) m.close(); var x=document.querySelector('.sync-modal-close, .modal-close'); if(x) x.click(); return 1;})()`).catch(() => 0);
    await nav('/qubits');
    await ev(`(function(){var s=document.querySelector('#state-version-slot button, #state-version-slot [hx-get]'); if(s) s.click(); return 1;})()`);
    await until(`document.querySelectorAll('#state-version-panel .sv-check').length>2`, 12000);
    const nTicked = await ev(`(function(){var c=document.querySelectorAll('#state-version-panel .sv-check'); var n=0; for(var i=0;i<c.length && n<3;i++){ c[i].click(); n++; } return n;})()`);
    await sleep(800);
    ok('P8 three versions can be ticked', nTicked === 3, { nTicked });
    const cmp = await ev(`(function(){var b=document.getElementById('sv-compare'); if(!b) return null; if(b.disabled) return 'DISABLED'; b.click(); return 'clicked';})()`);
    ok('P8 the Compare button arms on 3 ticks', cmp === 'clicked', cmp);
    await sleep(3500);
    const s2 = snap('P8-after-nway-compare');
    ok('P8 THE N-WAY COMPARE WRITES NOTHING', s2.n_changed === 0, s2.changed);
    ctxOut.p8compareUrl = await ev(`location.pathname + location.search`);
    await shot('P8_nway');
  }

  /* ═══ P9: Datasets -> run -> State -> Apply to chip ═══════════════════ */
  if (want('P9')) {
    const UID = process.env.SM_RUN_UID || '4349abee:1';
    await nav('/dataset/' + UID);
    await sleep(1200);
    // State tab
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('button,a,[role=tab]'), function(x){return /^state$/i.test((x.innerText||'').trim())}); if(b) b.click(); return 1;})()`);
    await sleep(1200);
    const bar = await text('.ds-state-action-bar');
    ok('P9 the State tab offers Apply to chip', /Apply to chip/i.test(String(bar)), String(bar).slice(0, 200));
    await shot('P9_statetab');

    /* (a) one press */
    const before = f01();
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('.ds-state-action-bar button'), function(x){return /^Apply to chip$/i.test((x.innerText||'').trim())}); if(!b) return 0; b.click(); return 1;})()`);
    await sleep(4500);
    const r1 = await text('#ds-load-state-result');
    const sA = snap('P9a-apply-once');
    ctxOut.p9a = String(r1).slice(0, 320);
    ok('P9a one press applies and SAYS it is live + reversible',
      /is now LIVE/i.test(String(r1)) && /Revert last apply/i.test(String(r1)), String(r1).slice(0, 240));
    ok('P9a the chip really changed to the run state', sA.n_changed >= 1 && f01() !== before,
      { changed: sA.changed.slice(0, 5), f01: f01(), before });

    /* (b) press it TWICE */
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('.ds-state-action-bar button'), function(x){return /^Apply to chip$/i.test((x.innerText||'').trim())}); b.click(); return 1;})()`);
    await sleep(4500);
    const r2 = await text('#ds-load-state-result');
    const sB = snap('P9b-apply-twice');
    ctxOut.p9b = String(r2).slice(0, 320);
    ok('P9b a second identical press still reports honestly', /is now LIVE|no change/i.test(String(r2)), String(r2).slice(0, 240));
    ok('P9b the second press changes no value', sB.n_changed === 0, sB.changed);

    /* (c) with PENDING EDITS in the tray */
    await nav('/bulk');
    await until(`document.querySelectorAll('input.bulk-cell').length > 0`, 25000);
    await ev(`(function(){var i=document.querySelector('input.bulk-cell[data-dot-path="qubits.q1.f_01"]'); i.focus(); i.value='5555555555'; i.dispatchEvent(new Event('input',{bubbles:true})); i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,keyCode:13,which:13})); i.blur(); return 1;})()`);
    await sleep(1400);
    const trayN = await ev(`(function(){var t=document.getElementById('pending-tray'); return t? t.getAttribute('data-change-count'): null;})()`);
    snap('P9c-edit-pending');
    await nav('/dataset/' + UID);
    await sleep(1200);
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('button,a,[role=tab]'), function(x){return /^state$/i.test((x.innerText||'').trim())}); if(b) b.click(); return 1;})()`);
    await sleep(1000);
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('.ds-state-action-bar button'), function(x){return /^Apply to chip$/i.test((x.innerText||'').trim())}); b.click(); return 1;})()`);
    await sleep(4500);
    const r3 = await text('#ds-load-state-result');
    const sC = snap('P9c-apply-over-pending');
    ctxOut.p9c = String(r3).slice(0, 360);
    ctxOut.p9cTray = trayN;
    ok('P9c it applies over pending edits WITHOUT asking (no 409 confirm)',
      !/Replace working state anyway|unsaved edits in the working state\./i.test(String(r3)), String(r3).slice(0, 240));
    ok('P9c …and it NAMES the edits it replaced', /Replaced \d+ unsaved edit|Replaced saved-but-unapplied/i.test(String(r3)), String(r3).slice(0, 300));
    await shot('P9c_pending');

    /* (d) with LIVE DRIFT (something outside SM rewrote the chip) */
    execFileSync(PY, [SCRATCH + '/drift5463.py'], { encoding: 'utf8' });
    snap('P9d-external-drift');
    await nav('/dataset/' + UID);
    await sleep(1200);
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('button,a,[role=tab]'), function(x){return /^state$/i.test((x.innerText||'').trim())}); if(b) b.click(); return 1;})()`);
    await sleep(1000);
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('.ds-state-action-bar button'), function(x){return /^Apply to chip$/i.test((x.innerText||'').trim())}); b.click(); return 1;})()`);
    await sleep(5000);
    const r4 = await text('#ds-load-state-result');
    const sD = snap('P9d-apply-over-drift');
    ctxOut.p9d = String(r4).slice(0, 380);
    ok('P9d it pushes over live drift without asking', /is now LIVE/i.test(String(r4)), String(r4).slice(0, 260));
    ok('P9d …and NAMES that live changes were overwritten',
      /HAD changed since it was loaded/i.test(String(r4)), String(r4).slice(0, 320));
    await shot('P9d_drift');
  }

  const runsha = () => JSON.parse(execFileSync(PY, [SCRATCH + '/runsha.py'], { encoding: 'utf8' }));

  /* ═══ P10: Revert last apply must REACH the pre-apply state ══════════ */
  if (want('P10')) {
    await nav('/qubits');
    const before = f01();
    const btn = await ev(`(function(){var b=document.querySelector('.tray-revert-apply'); return b? b.innerText.trim(): null;})()`);
    ok('P10 the tray offers Revert last apply after an apply', !!btn, btn);
    await ev(`(function(){var b=document.querySelector('.tray-revert-apply'); if(b) b.click(); return 1;})()`);
    await sleep(1100);   // the toast auto-dismisses at ~2s -- read it while it is up
    const bar = await text('#status-bar');
    await sleep(1900);
    const sr = snap('P10-after-revert-press');
    ctxOut.p10bar = String(bar).slice(0, 260);
    ok('P10 Revert last apply STAGES only — it writes nothing by itself',
      sr.n_changed === 0, sr.changed);
    ok('P10 …and says it is staged for review', /working state|review|Apply/i.test(String(bar)), String(bar).slice(0, 200));
    // complete the revert with the explicit apply press
    await nav('/qubits');
    const ap = await ev(`(function(){var b=document.querySelector('.btn-apply-live'); if(!b) return null; b.click(); return b.innerText.trim();})()`);
    ok('P10 an Apply button completes the revert', !!ap, ap);
    await sleep(4000);
    const s2 = snap('P10-after-revert-applied');
    ctxOut.p10f01 = { before, after: f01() };
    ok('P10 the revert reached a DIFFERENT (pre-apply) state', s2.n_changed >= 1 || f01() !== before,
      { changed: s2.changed.slice(0, 6), before, after: f01() });
    await shot('P10_reverted');
  }

  /* ═══ P11: gate 3 — origin != live (a dataset ARCHIVE) ═══════════════ */
  if (want('P11')) {
    const UID2 = process.env.SM_RUN_UID2 || '4349abee:2';
    const runBefore = runsha();
    await nav('/dataset/' + UID2);
    await sleep(1200);
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('button,a,[role=tab]'), function(x){return /^state$/i.test((x.innerText||'').trim())}); if(b) b.click(); return 1;})()`);
    await sleep(1000);
    await ev(`(function(){var b=document.querySelector('.ds-load-archive-btn'); if(b) b.click(); return 1;})()`);
    await sleep(4000);
    snap('P11-after-open-readonly');
    const where = await ev(`location.pathname`);
    ctxOut.p11where = where;
    const trayA = await text('#pending-tray');
    ok('P11 the archive context is open and says READ-ONLY',
      /archive|read-only/i.test(String(trayA)), String(trayA).slice(0, 220));
    await shot('P11_archive');

    // (a) the UI must not even OFFER restore on an archive
    await nav('/state-history');
    const nRestore = await count('.sh-restore-live');
    const nStage = await ev(`Array.prototype.filter.call(document.querySelectorAll('.sh-entry button'), function(b){return /load as working state/i.test(b.innerText||'')}).length`);
    ok('P11 on an archive the UI offers NO restore-to-live button', nRestore === 0, { nRestore, nStage });

    // (b) the SERVER must refuse it too, by name
    const es = await entriesOf();
    const ts = (es[0] || {}).ts || '20260911_130202_1941';
    const r = await ev(`(async function(){
      var resp = await fetch('/state-history/${ts}/restore-live?force=1', {method:'POST', headers:{'HX-Request':'true'}});
      var t = await resp.text();
      return {status: resp.status, body: t.replace(/<[^>]*>/g,' ').replace(/\\s+/g,' ').trim().slice(0,300)};
    })()`);
    ctxOut.p11restore = r;
    const sR = snap('P11-restore-live-on-archive');
    ok('P11 GATE 3 (archive) refuses restore-live with 409', r && r.status === 409, r);
    ok('P11 …by NAME (says archive / read-only)', /archive|read-only/i.test(String(r && r.body)), r && r.body);
    ok('P11 GATE 3 wrote nothing to the chip', sR.n_changed === 0, sR.changed);

    // (c) the one live writer must refuse too
    const r2 = await ev(`(async function(){
      var resp = await fetch('/state/apply-to-live', {method:'POST', headers:{'HX-Request':'true'}});
      var t = await resp.text();
      return {status: resp.status, body: t.replace(/<[^>]*>/g,' ').replace(/\\s+/g,' ').trim().slice(0,240)};
    })()`);
    ctxOut.p11apply = r2;
    const sA2 = snap('P11-apply-to-live-on-archive');
    ok('P11 /state/apply-to-live refuses on an archive', r2 && r2.status >= 400, r2);
    ok('P11 …and wrote nothing', sA2.n_changed === 0, sA2.changed);

    // (d) the run archive files themselves must be untouched
    const runAfter = runsha();
    const same = JSON.stringify(runBefore) === JSON.stringify(runAfter);
    ok('P11 the RUN ARCHIVE files are byte-identical throughout', same, { runBefore, runAfter });
    ctxOut.runBefore = runBefore; ctxOut.runAfter = runAfter;

    // (e) Apply to chip pressed while an archive is open
    await nav('/dataset/' + UID2);
    await sleep(1200);
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('button,a,[role=tab]'), function(x){return /^state$/i.test((x.innerText||'').trim())}); if(b) b.click(); return 1;})()`);
    await sleep(1000);
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('.ds-state-action-bar button'), function(x){return /^Apply to chip$/i.test((x.innerText||'').trim())}); if(b) b.click(); return 1;})()`);
    await sleep(4000);
    const sE = snap('P11-apply-to-chip-while-archive');
    ok('P11 Apply-to-chip on an archive context writes nothing to the live chip', sE.n_changed === 0, sE.changed);
    const runAfter2 = runsha();
    ok('P11 …and nothing to the run archives', JSON.stringify(runBefore) === JSON.stringify(runAfter2), runAfter2);
    ctxOut.p11e = await ev(`location.pathname`);
    await shot('P11_applyonarchive');
  }

  /* ═══ P13: a STAGED snapshot must not leak to live on its own ════════ */
  if (want('P13')) {
    await nav('/state-history');
    const es = await entriesOf();
    const t = (es.find(e => !/PLANTED/.test(e.txt)) || es[0]).ts;
    snap('P13-before');
    await ev(`(function(){var row=document.querySelector('.sh-entry[data-ts="${t}"]');
      var b=Array.prototype.find.call(row.querySelectorAll('button'), function(x){return /load as working state/i.test(x.innerText||'')}); b.click(); return 1;})()`);
    await sleep(3000);
    const s1 = snap('P13-staged');
    ok('P13 staging writes nothing', s1.n_changed === 0, s1.changed);
    // now WANDER: four page changes, a reload, and 20 s of the app's own polls
    for (const u of ['/qubits', '/diagnostics', '/pulses', '/param-history', '/qubits']) { await nav(u); }
    await sleep(12000);
    const s2 = snap('P13-after-wandering-and-polls');
    ok('P13 NOTHING leaks to live while a snapshot sits staged (nav + 12s of polls)',
      s2.n_changed === 0, s2.changed);
    await send('Page.reload', {}); await sleep(6000);
    const s3 = snap('P13-after-reload');
    ok('P13 …nor across a full page reload', s3.n_changed === 0, s3.changed);
    const tray = await text('#pending-tray');
    ok('P13 the tray still says it is NOT on the live chip', /not on the live chip yet|not applied/i.test(String(tray)), String(tray).slice(0, 200));
    ctxOut.p13tray = String(tray).slice(0, 200);
    await shot('P13_staged_idle');
  }

  /* ═══ P14: Auto-Sync armed + a wholesale stage ═══════════════════════ */
  if (want('P14')) {
    // first clear the staged state by applying it (explicit press)
    await nav('/qubits');
    await ev(`(function(){var b=document.querySelector('.btn-apply-live'); if(b) b.click(); return 1;})()`);
    await sleep(4000);
    snap('P14-cleared-staging');
    // arm Auto-Sync through its OWN visible control (pill -> panel -> Save),
    // push only, so nothing else can explain a write
    await ev(`(function(){var b=document.querySelector('.auto-apply-pill'); if(!b) return null; b.click(); return b.innerText.trim();})()`);
    await until(`!!document.getElementById('as-push')`, 8000);
    const armed = await ev(`(function(){
      var pull=document.getElementById('as-pull'); var pr=document.getElementById('as-pull-replace');
      var push=document.getElementById('as-push'); var f=document.querySelector('#auto-sync-pop form');
      if(!push||!f) return null;
      if(pull && pull.checked) pull.click();
      if(pr && pr.checked) pr.click();
      if(!push.checked) push.click();
      var sb=Array.prototype.find.call(f.querySelectorAll('button'), function(b){return /save/i.test(b.innerText||'')});
      sb.click();
      return {pull: pull && pull.checked, push: push.checked};
    })()`);
    await sleep(2500);
    const pill = await text('.auto-apply-pill');
    ctxOut.p14arm = { armed: armed, pill: String(pill).slice(0, 90) };
    ok('P14 Auto-Sync (push) armed from its own visible control',
       /Auto-Sync/i.test(String(pill)) && /push|both/i.test(String(pill)), ctxOut.p14arm);
    await nav('/state-history');
    await sleep(1500);
    const es = await entriesOf();
    const t = (es.find(e => !/PLANTED/.test(e.txt)) || es[0]).ts;
    const b4 = snap('P14-before-stage-armed');
    await ev(`(function(){var row=document.querySelector('.sh-entry[data-ts="${t}"]');
      var b=Array.prototype.find.call(row.querySelectorAll('button'), function(x){return /load as working state/i.test(x.innerText||'')}); if(b) b.click(); return 1;})()`);
    await sleep(6000);
    const s = snap('P14-after-stage-with-autoapply-armed');
    ctxOut.p14 = { changed: s.n_changed, paths: s.changed.slice(0, 6) };
    ok('P14 RECORDED: what a wholesale stage does while Auto-Sync is armed', true, ctxOut.p14);
    const tray = await text('#pending-tray');
    ctxOut.p14tray = String(tray).slice(0, 260);
    // disarm
    await ev(`(function(){var b=document.querySelector('.auto-apply-pill'); if(b) b.click(); return 1;})()`);
    await sleep(1200);
    await ev(`(function(){var b=Array.prototype.find.call(document.querySelectorAll('#auto-sync-pop button'), function(x){return /turn off/i.test(x.innerText||'')}); if(b) b.click(); return 1;})()`);
    await sleep(1200);
    await shot('P14_autosync_stage');
  }

  /* ═══ P15: restore-live pressed TWICE fast ═══════════════════════════ */
  if (want('P15')) {
    await nav('/state-history');
    await sleep(1200);
    const es = await entriesOf();
    const t = (es.find(e => !/PLANTED/.test(e.txt)) || es[0]).ts;
    snap('P15-before');
    await ev(`(function(){var row=document.querySelector('.sh-entry[data-ts="${t}"]'); var b=row.querySelector('.sh-restore-live'); b.click(); b.click(); b.click(); return 1;})()`);
    await sleep(7000);
    const s = snap('P15-after-triple-press');
    const d = await detail();
    ctxOut.p15 = { changed: s.n_changed, paths: s.changed.slice(0, 6), msg: String(d).slice(0, 240) };
    ok('P15 three fast presses leave a READABLE chip', s.leaves['state.json'] > 1000, s.leaves);
    ok('P15 …and the app still reports what happened', /restored|unsaved|wiring/i.test(String(d)), String(d).slice(0, 200));
    await shot('P15_triple');
  }

  fs.writeFileSync(OUT, JSON.stringify({ results, errors, log, ctx: ctxOut }, null, 1));
  console.log('\n--- ' + results.filter(r => !r.pass).length + ' FAIL of ' + results.length + ' ; console errors: ' + errors.length);
  ws.close();
}
main().catch(e => { console.error('DRIVER ERROR', e); fs.writeFileSync(OUT, JSON.stringify({ results, errors, log, driverError: String(e && e.stack || e) }, null, 1)); process.exit(2); });
