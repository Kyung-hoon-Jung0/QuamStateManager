'use strict';
// Port play walk fixes (F1-F9), driven through the real port-play.js + generate.js under jsdom.
const fs = require('fs'), path = require('path'), assert = require('assert');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..'), staticDir = path.join(root, 'quam_state_manager/web/static');
const html = fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_generate.html'), 'utf8');
const dom = new JSDOM('<body><div id="table-pane">' + html + '</div></body>', {
  runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/generate'});
const w = dom.window;
const calls = [];
const replies = {run: [], status: []};
function answer(body) { return Promise.resolve({ok: true, json: () => Promise.resolve(body)}); }
w.fetch = (u, init) => {
  const url = String(u), body = init && init.body ? JSON.parse(init.body) : null;
  calls.push({url, body});
  if (/\/confirm$/.test(url)) return answer({ok: true, token: 't'});
  if (/\/port-play\/run$/.test(url)) return answer(replies.run.shift() || {ok: true, id: 'r1'});
  if (/\/port-play\/loopcal\/run$/.test(url)) return answer({ok: true, id: 'c1'});
  if (/\/port-test\/status/.test(url)) return answer(replies.status.length > 1 ? replies.status.shift() : replies.status[0] || {state: 'none'});
  if (/\/stop$/.test(url)) return answer({ok: true, stopping: true});
  return new Promise(() => {});
};
w.htmx = {ajax() {}, trigger() {}, process() {}, on() {}};
w.IntersectionObserver = w.ResizeObserver = class {observe() {} disconnect() {} unobserve() {}};
w.NumberInput = {fit() {}, attach() {}, format() {}, strip(s) {return String(s || '');}};
w.armPlainResize = () => {};
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
const body = w.document.body; body.remove();
w.eval(fs.readFileSync(path.join(staticDir, 'port-play.js'), 'utf8'));
w.document.documentElement.appendChild(body);
for (const file of ['app.js', 'generate.js']) w.eval(fs.readFileSync(path.join(staticDir, file), 'utf8'));
const G = w.QuamGen, d = w.document;
const sleep = ms => new Promise(r => setTimeout(r, ms));
let n = 0;
const ok = (c, m) => { assert(c, m); n++; };

(async () => {
  // F4: a wizard with no spec or draft starts with ONE chassis, not five.
  ok(d.getElementById('gen-chassis-count').getAttribute('value') === '1', 'F4 template default 1');
  w.confirm = () => true;
  G.init();
  ok(G.state.spec.instruments.controllers.length === 1, 'F4 first open seeds 1 chassis, got ' +
     G.state.spec.instruments.controllers.length);
  d.getElementById('gen-reset').click();   // Start over: the wizard's own fresh rack
  ok(G.state.spec.instruments.controllers.length === 1, 'F4 fresh wizard seeds 1 chassis, got ' +
     G.state.spec.instruments.controllers.length);
  ok(d.getElementById('gen-chassis-count').value === '1', 'F4 count field shows 1');

  G.state.spec.instruments.controllers = [1, 2].map(con => ({con, fems: [{slot: 1, fem: 'mw'}, {slot: 3, fem: 'lf'}]}));
  G.state.spec.qubits = []; G.state.spec.lines = []; G.state.env = 'py';
  G.state.spec.network = {host: 'h'};
  G._test.renderChassis();

  // F3: the hint names both FEM kinds.
  const hint = d.querySelector('#gen-port-play-panel .ui-note').textContent;
  ok(/LF-FEM/.test(hint) && /MW-FEM/.test(hint), 'F3 hint mentions LF and MW: ' + hint);

  // F9: the open state is remembered and comes back on a re-mount.
  d.getElementById('gen-port-play-toggle').click();
  ok(w.localStorage.getItem('quam_gen_port_play_open') === '1', 'F9 open state stored');
  const old = d.getElementById('gen-port-play-panel'), fresh = old.cloneNode(true);
  fresh.hidden = true; old.replaceWith(fresh);
  G._test.renderChassis();
  ok(!d.getElementById('gen-port-play-panel').hidden, 'F9 panel restored open after a re-mount');
  ok(d.getElementById('gen-port-play-toggle').getAttribute('aria-expanded') === 'true', 'F9 aria-expanded follows');

  const rack = d.getElementById('gen-port-play-rack');
  const open = (slot, port) => {
    rack.querySelector('.iw-port[data-con="2"][data-slot="' + slot + '"][data-port="' + port + '"][data-io="output"] button').click();
    return d.getElementById('port-play-dialog');
  };

  // F7: both popup kinds name their FEM kind.
  let p = open(3, 5);
  ok(/\(LF\)$/.test(p.querySelector('#port-play-title').textContent), 'F7 LF title');

  // F2: Simulate needs no safety box, sends action "sim", and labels the result a simulation.
  ok(!p.querySelector('[data-hwok]').checked, 'safety box unticked');
  replies.run.push({ok: true, id: 'sim1', simulated: true});
  replies.status = [{state: 'done', id: 'sim1', meta: {plan: {expected_peak: 0}},
    result: {ok: true, simulated: true, backend_label: 'QM-SaaS cloud simulator',
      traces: {'con2:3-5': {peak: 0.25, n: 4, step: 1, re: [0, 0.25, 0.25, 0]}, 'con2:dig:1': {digital: true, re: [0], n: 1}}}}];
  p.querySelector('[data-sim]').click();
  await sleep(60);
  const conf = calls.find(c => /port-play\/confirm/.test(c.url));
  ok(conf && conf.body.action === 'sim', 'F2 sim confirm carries action sim');
  const box = p.querySelector('[data-sim-result]');
  ok(!box.hidden, 'F2 sim result shown');
  ok(/Simulation, not hardware/.test(box.textContent), 'F2 labelled as simulation');
  ok(/QM-SaaS/.test(box.textContent), 'F2 names the simulator');
  ok(box.querySelectorAll('[data-sim-port]').length === 1 && box.querySelector('svg.pt-trace path.pt-re'), 'F2 one analog trace plotted');
  ok(/Simulation finished: no hardware/.test(p.querySelector('[data-message]').textContent), 'F2 message says no hardware');
  // A hardware Play still needs the box.
  calls.length = 0;
  p.querySelector('[data-play]').click();
  ok(/safety box/.test(p.querySelector('[data-message]').textContent) && !calls.length, 'F2 Play still gated');

  // F1: a stop that ended with an SDK error reads "Stopped.".
  p.querySelector('[data-hwok]').checked = true;
  replies.run.push({ok: true, id: 'r2'});
  replies.status = [{state: 'done', id: 'r2', result: {ok: false, stopped: true, error: 'TimeoutError: QM API call timed out'}}];
  p.querySelector('[data-play]').click();
  await sleep(60);
  ok(p.querySelector('[data-message]').textContent === 'Stopped.', 'F1 stopped wins over the error: ' +
     p.querySelector('[data-message]').textContent);
  ok(box.hidden, 'F1/F2 a new press clears the previous simulation');

  // F5: Play while our previous play stops: one 409, then wait on status, then ONE more run.
  calls.length = 0;
  replies.run.push({ok: false, stopping: true, id: 'r2', error: 'Stopping the previous play; waiting for its QM to close.'});
  replies.run.push({ok: true, id: 'r3'});
  replies.status = [{state: 'running', id: 'r2', stopping: true}, {state: 'running', id: 'r2', stopping: true},
    {state: 'done', id: 'r2', result: {ok: false, stopped: true}}, {state: 'running', id: 'r3', phase: 'connecting'}];
  p.querySelector('[data-play]').click();
  await sleep(2200);
  const runs = calls.filter(c => /port-play\/run$/.test(c.url)).length;
  ok(runs === 2, 'F5 exactly two run presses (one 409, one start), got ' + runs);
  ok(calls.some(c => /status\?id=r2/.test(c.url)), 'F5 waits on the old run status');
  p.querySelector('[data-close]').click();

  // F6: Play together rows show band ranges and are one row each with their own Remove.
  p = open(1, 4);
  ok(/\(MW\)$/.test(p.querySelector('#port-play-title').textContent), 'F7 MW title');
  ok(/50 MHz - 5\.5 GHz/.test(p.querySelector('[data-band]').textContent), 'own band select ranges');
  const sec = p.querySelector('[data-together]');
  sec.querySelector('[data-add-port]').click();
  const row = sec.querySelector('[data-row]');
  const pin = row.querySelector('[data-pin]');
  const mwOpt = Array.from(pin.options).find(o => o.getAttribute('data-kind') === 'mw');
  pin.value = mwOpt.value; pin.dispatchEvent(new w.Event('change'));
  const opts = Array.from(row.querySelectorAll('[data-band] option')).map(o => o.textContent);
  ok(opts.length === 3 && opts.every(t => /GHz/.test(t)), 'F6 row band options carry ranges: ' + opts);
  const line = row.querySelector('[data-line]');
  ok(row.classList.contains('pt-card') && line.classList.contains('pt-row') && sec.classList.contains('port-test'), 'F6 one card + one line per port');
  ok(line.lastElementChild.matches('[data-remove]'), 'F6 Remove is the last item of its own row');
  ok(line.querySelectorAll(':scope > label').length >= 5, 'F6 fields sit in the row');
  pin.value = Array.from(pin.options).find(o => o.getAttribute('data-kind') === 'lf').value;
  pin.dispatchEvent(new w.Event('change'));
  ok(line.lastElementChild.matches('[data-remove]') && !row.querySelector('[data-band]') && row.querySelector('[data-offset]'),
     'F6 switching kind replaces the fields, Remove stays last');
  row.querySelector('[data-remove]').click();
  ok(!sec.querySelector('[data-row]'), 'F6 Remove removes its row');
  p.querySelector('[data-close]').click();

  // F8: the calibration message shows the live iteration.
  p = open(3, 5);
  p.querySelector('[data-hwok]').checked = true;
  G.state.spec.port_offsets = {'con2/3/ai1': {offset: 0, gain_db: 0}};
  replies.status = [{state: 'running', id: 'c1', progress: ['Slope: offset +0.010000 V -> DC +1.000 mV',
    'Iter 2: offset +0.001000 V -> DC +0.020 mV']}];
  p.querySelector('[data-cal-input]').click();
  await sleep(80);
  ok(/Calibrating\.\.\. Iter 2: offset \+0\.001000 V -> DC \+0\.020 mV/.test(p.querySelector('[data-cal-result]').textContent),
     'F8 live iteration: ' + p.querySelector('[data-cal-result]').textContent);
  p.querySelector('[data-close]').click();
  console.log('port play walk selfcheck ok (' + n + ' assertions)');
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
