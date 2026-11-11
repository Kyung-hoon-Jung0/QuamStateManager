'use strict';
// Port play set popup (Stage S1, D-1..D-13): driven through the real port-play.js under jsdom.
const fs = require('fs'), path = require('path'), assert = require('assert');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..'), staticDir = path.join(root, 'quam_state_manager/web/static');
const dom = new JSDOM('<body><div id="table-pane"></div><table><tbody id="gen-port-offsets"></tbody></table></body>', {
  runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/generate'});
const w = dom.window, d = w.document;
const calls = [], replies = {run: [], status: []}, plots = [], toasts = [];
function answer(body) { return Promise.resolve({ok: true, json: () => Promise.resolve(body)}); }
w.fetch = (u, init) => {
  const url = String(u), body = init && init.body ? JSON.parse(init.body) : null;
  calls.push({url, body});
  if (/\/confirm$/.test(url)) return answer({ok: true, token: 't'});
  if (/\/port-play\/run$/.test(url)) return answer(replies.run.shift() || {ok: true, id: 'r1'});
  if (/\/port-test\/status/.test(url)) return answer(replies.status.length > 1 ? replies.status.shift() : replies.status[0] || {state: 'none'});
  if (/\/stop$/.test(url)) return answer({ok: true, stopping: false});
  if (/\/port-play\/sim-check$/.test(url)) return answer(replies.simCheck || {ok: true, saas: true, reason: null});
  if (/\/port-play\/script\.py$/.test(url)) return Promise.resolve({ok: true, headers: {get: () => 'attachment; filename="port_play_x.py"'},
    blob: () => Promise.resolve('print(1)')});
  return new Promise(() => {});
};
w.Plotly = {newPlot(div, data, layout) { plots.push({div, data, layout}); }};
w.showToast = (msg) => toasts.push(msg);
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
w.eval(fs.readFileSync(path.join(staticDir, 'port-play.js'), 'utf8'));
const PP = w.PortPlay;
const changes = []; d.addEventListener('port-play:change', e => changes.push(e.detail));
const sleep = ms => new Promise(r => setTimeout(r, ms));
let n = 0;
const ok = (c, m) => { assert(c, m); n++; };
const ctrl = fems => [{con: 1, fems}];
let spec = {};
const source = {spec: () => spec, python: () => 'py', changed() {}};
const fresh = (fems, extra) => { w.sessionStorage.clear(); spec = Object.assign({instruments: {controllers: ctrl(fems)}}, extra || {}); };
const openAt = (slot, port, kind) => { PP.open({con: 1, slot, port, femType: kind === 'mw' ? 'mw' : 'lf'}, source); return d.getElementById('port-play-dialog'); };
const rowsOf = p => Array.from(p.querySelectorAll('[data-row]'));
const set = (el, v) => { el.value = v; el.dispatchEvent(new w.Event('input', {bubbles: true})); };
const pickPin = (row, pin) => { const s = row.querySelector('[data-pin]'); s.value = pin; s.dispatchEvent(new w.Event('change', {bubbles: true})); };
const confirmBody = () => calls.filter(c => /port-play\/confirm/.test(c.url)).pop().body;
const MW3 = [{slot: 1, fem: 'mw'}, {slot: 2, fem: 'mw'}, {slot: 3, fem: 'lf'}];

(async () => {
  // D-1: every allocated output may join; no "At most" cap.
  fresh(MW3);
  let p = openAt(1, 1, 'mw');
  const add = p.querySelector('[data-add-port]');
  const seen = new Set();
  for (let i = 0; i < 30; i++) { add.click(); seen.add(rowsOf(p).length); ok(!/At most/.test(add.title), 'D-1 never "At most"'); }
  ok(seen.has(4) && seen.has(8) && seen.has(24), 'D-1 reaches 4th, 8th, 24th row');
  ok(rowsOf(p).length === 24 && add.disabled && /No other output is free/.test(add.title), 'D-1 disables only when none is free');
  PP.close();

  // D-2: the opener is row 1, removable; the main entry is whichever row is first.
  fresh(MW3);
  p = openAt(1, 1, 'mw');
  ok(p.querySelector('#port-play-title').textContent === 'con1 \u00b7 FEM 1 \u00b7 OUT 1 (MW)', 'D-12 single title');
  p.querySelector('[data-add-port]').click();
  ok(p.querySelector('#port-play-title').textContent === 'Play set (2 outputs)', 'D-2 set title');
  ok(/whole run/.test(p.querySelector('[data-seconds]').closest('label').textContent), 'D-2 auto-stop applies to the whole run');
  rowsOf(p)[0].querySelector('[data-remove]').click();
  ok(rowsOf(p).length === 1, 'D-2 opener row removable');
  p.querySelector('[data-hwok]').checked = true;
  p.querySelector('[data-play]').click(); await sleep(10);
  let b = confirmBody();
  ok(b.kind === 'mw' && b.slot === 1 && b.port === 2 && !('extra' in b), 'D-2 main entry is the former row 2');
  PP.close();

  // D-3: kit table, grouped options, bulk adds.
  fresh(MW3);
  p = openAt(1, 1, 'mw');
  ok(p.querySelector('table.ui-table [data-row]'), 'D-3 rows sit in the kit table');
  const groups = rowsOf(p)[0].querySelectorAll('[data-pin] optgroup');
  ok(groups.length === 3 && groups[0].label === 'con1 \u00b7 FEM 1 (MW)', 'D-3 options grouped chassis -> FEM');
  ok(Array.from(rowsOf(p)[0].querySelectorAll('[data-pin] option')).every(o => /^(OUT|AO) \d \((MW|LF)\)$/.test(o.textContent)), 'D-3 option text');
  p.querySelector('[data-add-fem]').click();
  ok(rowsOf(p).length === 8, 'D-3 other outputs of this FEM: 7 added');
  PP.close();
  fresh([{slot: 1, fem: 'mw'}, {slot: 2, fem: 'mw'}, {slot: 4, fem: 'mw'}]);
  p = openAt(1, 3, 'mw');
  ok(/OUT 3 on every FEM/.test(p.querySelector('[data-add-every]').textContent), 'D-3 every-FEM label');
  p.querySelector('[data-add-every]').click();
  ok(rowsOf(p).length === 3 && rowsOf(p).every(r => /\/3$/.test(r.querySelector('[data-pin]').value)), 'D-3 OUT n on every FEM: 3 rows');
  PP.close();

  // D-4: a new row lands on the same FEM and kind, values copied.
  fresh(MW3, {port_offsets: {'con1/3/5': {offset: 0.3, output_mode: 'amplified'}}});
  p = openAt(3, 5, 'lf');
  p.querySelector('[data-add-port]').click();
  let r2 = rowsOf(p)[1];
  ok(r2.querySelector('[data-pin]').value === 'con1/3/6' && r2.getAttribute('data-kind') === 'lf', 'D-4 next free on the same FEM');
  ok(r2.querySelector('[data-mode]').value === 'amplified' && Number(r2.querySelector('[data-offset]').value) === 0.3, 'D-4 values copied');
  set(rowsOf(p)[0].querySelector('[data-offset]'), '0.1');
  const applyLf = p.querySelector('[data-apply-kind="lf"]');
  ok(!applyLf.hidden, 'D-4 apply-to-all shown with 2 LF rows');
  applyLf.click();
  ok(Number(rowsOf(p)[1].querySelector('[data-offset]').value) === 0.1, 'D-4 apply to all LF rows');

  // D-5: the set is remembered, the tick is not.
  p.querySelector('[data-hwok]').checked = true;
  p.querySelector('[data-close]').click();
  p = openAt(3, 6, 'lf');
  ok(rowsOf(p).map(r => r.querySelector('[data-pin]').value).join() === 'con1/3/5,con1/3/6', 'D-5 same rows on reopen at row 2');
  ok(!p.querySelector('[data-hwok]').checked, 'D-5 tick unticked');
  ok(!/hwok|hardware_ok/.test(w.sessionStorage.getItem('quam_gen_port_play_set')), 'D-5 tick never stored');
  ok(/^Closing this popup stops every output; the set itself is kept\./.test(p.querySelector('[data-together] .ui-note').textContent), 'D-5 sentence');

  // D-9: LF layout and words.
  ok(p.querySelector('[data-hold-qm] [data-open-qm]') && !p.querySelector('[data-hold-qm]').open, 'D-9 Open QM under a collapsed disclosure');
  ok(/Hold the QM open \(advanced\)/.test(p.querySelector('[data-hold-qm] summary').textContent), 'D-9 disclosure label');
  ok(/Saved to this wizard draft only; the open chip and the instrument do not change until Build\./.test(p.querySelector('[data-save-wrap]').textContent), 'D-9 save sentence');
  ok(p.querySelector('details[data-loopcal]') && !p.querySelector('[data-loopcal]').open, 'D-9 loopback collapsed');
  ok(/Auto-stop after \(s\)/.test(p.textContent) && !/Max time/.test(p.textContent), 'D-9 auto-stop label');
  ok(/Refused if another program holds any of these ports\./.test(p.textContent), 'D-9 refusal sentence');
  ok(!p.querySelector('[data-sim]').closest('[data-run-actions]') && p.querySelector('[data-play]').closest('[data-run-actions]'), 'D-9 simulate apart from Play/Stop');
  PP.renderOffsets({port_offsets: {}}, () => {});
  ok(/No saved offsets yet/.test(d.getElementById('gen-port-offsets').textContent), 'D-9 empty-state row');
  // D-10: actions live in the sticky dialog foot.
  ok(['[data-play]', '[data-stop]', '[data-sim]'].every(s => p.querySelector(s).closest('.ui-dialog-foot')), 'D-10 actions in .ui-dialog-foot');
  PP.close();

  // D-11 + D-12: RF primary, inline validation, max input; one port notation.
  fresh(MW3);
  w.localStorage.clear();
  p = openAt(1, 1, 'mw');
  let row = rowsOf(p)[0];
  set(row.querySelector('[data-rf]'), '5.05');
  ok(Number(row.querySelector('[data-lo]').value) === 5 && Number(row.querySelector('[data-if]').value) === 50, 'D-11 RF 5.05 -> LO 5 / IF 50');
  set(row.querySelector('[data-lo]'), '8');
  ok(row.querySelector('[data-lo]').getAttribute('aria-invalid') === 'true', 'D-11 LO 8 band 1 invalid on input');
  ok(/band 3/.test(row.querySelector('[data-lo-reason]').textContent) && /50 MHz - 5\.5 GHz/.test(row.querySelector('[data-lo-reason]').textContent), 'D-11 reason names range and band 3');
  // D-7: the refusal clears on the next edit.
  p.querySelector('[data-sim]').click();
  ok(p.querySelector('[data-message]').textContent !== '', 'D-7 refusal shown');
  set(row.querySelector('[data-lo]'), '5');
  ok(p.querySelector('[data-message]').textContent === '', 'D-7 refusal cleared by LO 5');
  ok(!row.querySelector('[data-lo]').hasAttribute('aria-invalid'), 'D-11 valid LO clears aria-invalid');
  set(row.querySelector('[data-fsp]'), '9'); set(row.querySelector('[data-amp]'), '0.999');
  set(p.querySelector('[data-max-in]'), '0');
  ok(!p.querySelector('[data-power-warn]').hidden && p.querySelector('[data-power-warn]').classList.contains('ui-callout--danger'), 'D-11 danger callout above max input');
  set(p.querySelector('[data-max-in]'), '');
  ok(p.querySelector('[data-power-warn]').hidden, 'D-11 blank max input -> no callout');
  p.querySelector('[data-add-port]').click(); p.querySelector('[data-add-fem]').click();
  ok(!/con\d\/\d\/\d \((MW|LF)\)/.test(p.textContent) && !/con\d:\d-\d-\d/.test(p.textContent), 'D-12 no raw pin text');
  PP.close();

  // D-6: failure as a sentence; "Play again" only once running.
  fresh(MW3);
  p = openAt(1, 1, 'mw');
  p.querySelector('[data-hwok]').checked = true;
  replies.run.push({ok: true, id: 'f1'});
  replies.status = [{state: 'done', id: 'f1', result: {ok: false, error_kind: 'connect',
    error: 'Could not reach the OPX at h (cluster c).', detail: 'QmServerDetectionError: raw text'}}];
  p.querySelector('[data-play]').click(); await sleep(40);
  ok(p.querySelector('[data-play]').textContent === 'Play', 'D-6 failed run keeps the label Play');
  const notice = p.querySelector('[data-error] .ui-notice--danger');
  ok(notice && /Could not reach the OPX/.test(notice.textContent), 'D-6 danger notice with the sentence');
  ok(/raw text/.test(p.querySelector('[data-error] .ui-disclosure').textContent), 'D-6 raw text under Details');
  ok(p.querySelector('[data-error] [data-edit-host]'), 'D-6 connect failure links to the host step');
  replies.run.push({ok: true, id: 'g1'});
  replies.status = [{state: 'running', id: 'g1', phase: 'running'}];
  p.querySelector('[data-play]').click(); await sleep(40);
  ok(p.querySelector('[data-play]').textContent === 'Play again', 'D-6 Play again once seen running');
  // S3 contract: live() names the running set; one event per change.
  const live = PP.live();
  ok(live && live.id === 'g1' && live.members.length === 1 && live.members[0].pin === 'con1/1/1' &&
     live.members[0].label === 'con1 · FEM 1 · OUT 1 (MW)' && live.max_s === 300, 'S3 live() set');
  ok(changes.length && changes[changes.length - 1].id === 'g1', 'S3 port-play:change on start');
  // D-13 toast: a pane swap while running stops and says so.
  d.dispatchEvent(new w.CustomEvent('htmx:beforeSwap', {detail: {target: d.getElementById('table-pane')}}));
  ok(toasts.some(t => t === 'Port play stopped \u2014 outputs off.'), 'D-13 navigation toast');
  ok(!d.getElementById('port-play-dialog'), 'D-13 swap closes the popup');
  ok(PP.live() === null && changes[changes.length - 1] === null, 'S3 live() null and event after stop');
  // D-26 (S1 half): a verdict goes to source.setCheck when the source has it.
  const checks = [];
  source.setCheck = (pin, v) => checks.push([pin, v]);
  fresh(MW3); p = openAt(1, 2, 'mw');
  p.querySelector('[data-check-note]').value = 'scope ok';
  p.querySelector('[data-verdict="ok"]').click();
  ok(checks.length === 1 && checks[0][0] === 'con1/1/2' && checks[0][1].verdict === 'ok' && checks[0][1].note === 'scope ok', 'D-26 setCheck');
  PP.close(); delete source.setCheck;
  fresh(MW3); p = openAt(1, 2, 'mw');
  ok(!p.querySelector('[data-check]'), 'D-26 no verdict UI without setCheck');
  PP.close();

  // D-8 + D-13: labelled traces, shared y range per kind, previous simulation kept.
  fresh(MW3);
  p = openAt(3, 5, 'lf');
  pickPin(rowsOf(p)[0], 'con1/3/5');
  p.querySelector('[data-add-port]').click(); pickPin(rowsOf(p)[1], 'con1/1/1');
  const tr = (con, slot, port, kind, member, peak, edge) => Object.assign({con, slot, port, kind, member, peak, n: 4, step: 1,
    first_edge_ns: edge, re: [0, peak, peak, 0]}, kind === 'mw' ? {im: [0, 0, 0, 0]} : {rate: 2e9});
  const simRes = id => ({state: 'done', id, meta: {plan: {expected_peak: null, plays: [{con: 'con1', slot: 3, port: 5, expected_peak: 0},
    {con: 'con1', slot: 1, port: 1, expected_peak: 0.1}]}}, result: {ok: true, simulated: true, backend_label: 'Sim',
    traces: {'con1:3-5': tr('con1', 3, 5, 'lf', true, 0.2, 161), 'con1:1-1-1': tr('con1', 1, 1, 'mw', true, 0.1, 0),
      'con1:3-6': tr('con1', 3, 6, 'lf', false, 0, undefined), 'con1:dig:1': {digital: true, re: [0], n: 1}}}});
  replies.run.push({ok: true, id: 's1'}); replies.status = [simRes('s1')];
  plots.length = 0;
  p.querySelector('[data-sim]').click(); await sleep(40);
  const box = p.querySelector('[data-sim-result]');
  ok(!box.hidden && /Simulation, not hardware/.test(box.textContent), 'D-13 result labelled simulation');
  const lfPlot = plots.find(x => x.div.getAttribute('data-sim-fig') === 'lf'), mwPlot = plots.find(x => x.div.getAttribute('data-sim-fig') === 'mw');
  ok(plots.length === 2 && lfPlot.data.length === 1 && mwPlot.data.length === 1, 'D-13 one figure per kind, traces == members');
  ok(plots.every(x => { const r = x.layout.yaxis.range, ys = [].concat(...x.data.map(t => t.y));
    return Array.isArray(r) && r.length === 2 && r.every(Number.isFinite) && r[0] <= Math.min(...ys) && r[1] >= Math.max(...ys); }), 'D-13 one shared y range per kind');
  ok(/ns/.test(lfPlot.layout.xaxis.title.text), 'D-13 x axis in ns');
  ok(lfPlot.data[0].x[1] === 0.5 && mwPlot.data[0].x[1] === 1, 'S2 rate: x axis is real ns (2 GSa/s -> 0.5 ns)');
  const simRows = box.querySelectorAll('[data-sim-table] [data-sim-port]');
  ok(simRows.length === 2 && /AO 5/.test(simRows[0].textContent) && /161/.test(simRows[0].textContent), 'D-13 per-port table in set order');
  ok(/-161/.test(simRows[1].textContent) && /0\.1000/.test(simRows[1].textContent), 'D-13 delta to row 1 and expected per port');
  ok(/1 other declared output stayed at their offsets/.test(box.querySelector('[data-sim-others]').textContent), 'D-13 non-members in one line');
  ok(!/con1:3-5|con1:1-1-1/.test(box.textContent), 'D-12 sim never shows the raw key');
  replies.run.push({ok: true, id: 's2'}); replies.status = [simRes('s2')];
  p.querySelector('[data-sim]').click(); await sleep(40);
  const prev = p.querySelector('[data-sim-prev]');
  ok(!prev.hidden && !prev.open && prev.querySelector('[data-sim-table]') && !box.hidden && box.querySelector('[data-sim-table]'), 'D-8 both simulations on the page, older collapsed');
  PP.close();

  // D-25 (S1 half): chip values prefill, with a badge and the read-only sentence.
  source.chipPort = pin => pin === 'con1/1/3' ? {band: 2, lo_hz: 6e9, fsp: 4} : null;
  fresh(MW3); p = openAt(1, 3, 'mw');
  row = rowsOf(p)[0];
  ok(row.querySelector('[data-band]').value === '2' && Number(row.querySelector('[data-lo]').value) === 6 &&
     row.querySelector('[data-fsp]').value === '4' && !row.querySelector('[data-chip-badge]').hidden &&
     !p.querySelector('[data-chip-note]').hidden, 'D-25 chip values prefill');
  PP.close(); delete source.chipPort;

  // Integration (S1 <-> S2): waveform / sim_ns reach the request; sim-check gates Simulate; script export.
  fresh(MW3); p = openAt(3, 5, 'lf');
  p.querySelector('[data-hwok]').checked = true;
  p.querySelector('[data-play]').click(); await sleep(10);
  b = confirmBody();
  ok(!('waveform' in b) && !('sim_ns' in b), 'D-18 default request carries no waveform/sim_ns');
  p.querySelector('[data-waveform]').value = 'edge'; set(p.querySelector('[data-sim-ns]'), '20000');
  replies.run.push({ok: true, id: 'e1'}); replies.status = [{state: 'none'}];
  p.querySelector('[data-sim]').click(); await sleep(10);
  b = confirmBody();
  ok(b.waveform === 'edge' && b.sim_ns === 20000, 'D-18 edge + sim_ns sent');
  w.URL.createObjectURL = () => 'blob:x'; w.URL.revokeObjectURL = () => {};
  p.querySelector('[data-script]').click(); await sleep(10);
  const sc = calls.filter(c => /script\.py$/.test(c.url)).pop();
  ok(sc && sc.body.waveform === 'edge' && sc.body.port === 5, 'D-21 script export posts the play request');
  ok(/Script downloaded: port_play_x\.py/.test(p.querySelector('[data-message]').textContent), 'D-21 download named');
  ok(!p.querySelector('[data-sim]').disabled, 'D-19 Simulate enabled when a simulator exists');
  PP.close();
  replies.simCheck = {ok: true, saas: false, local_host: null, reason: 'Simulate needs a QOP host in step 2.'};
  fresh(MW3); p = openAt(3, 5, 'lf'); await sleep(10);
  ok(p.querySelector('[data-sim]').disabled && p.querySelector('[data-sim]').title === 'Simulate needs a QOP host in step 2.' &&
     !p.querySelector('[data-sim-reason]').hidden, 'D-19 Simulate disabled with the reason');
  PP.close(); delete replies.simCheck;
  console.log('port play set selfcheck ok (' + n + ' assertions)');
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
