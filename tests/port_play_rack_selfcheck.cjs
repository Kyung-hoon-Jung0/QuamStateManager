'use strict';
// Port play rack and wizard mount (D-23..D-26), driven through the real app.js + generate.js under jsdom.
const fs = require('fs'), path = require('path'), assert = require('assert');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..'), staticDir = path.join(root, 'quam_state_manager/web/static');
const html = fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_generate.html'), 'utf8');
const dom = new JSDOM('<body><div id="table-pane">' + html + '</div></body>', {
  runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/generate'});
const w = dom.window;
const calls = [];
let chipReply = null;
function answer(body) { return Promise.resolve({ok: true, json: () => Promise.resolve(body)}); }
w.fetch = (u, init) => {
  const url = String(u), body = init && init.body ? JSON.parse(init.body) : null;
  calls.push({url, body});
  if (/\/api\/instrument\/data$/.test(url)) return answer(chipReply || {error: 'no chip loaded'});
  if (/\/stop$/.test(url)) return answer({ok: true, stopping: true});
  return new Promise(() => {});
};
w.htmx = {ajax() {}, trigger() {}, process() {}, on() {}};
w.IntersectionObserver = w.ResizeObserver = class {observe() {} disconnect() {} unobserve() {}};
w.NumberInput = {fit() {}, attach() {}, format() {}, strip(s) {return String(s || '');}};
w.armPlainResize = () => {};
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
const opened = [];
// A stand-in popup: S3 only mounts it; the real popup is pinned by its own selfchecks.
w.eval(fs.readFileSync(path.join(staticDir, 'port-play.js'), 'utf8'));
const realChipPortOf = w.PortPlay.chipPortOf, realRackFromSpec = w.PortPlay.rackFromSpec;   // the one shared reader (D-25), kept real under the stub
w.PortPlay = {open(port, source) { opened.push({port, source}); }, close() {}, renderOffsets() {}, live: () => null,
  chipPortOf: realChipPortOf, rackFromSpec: realRackFromSpec};
for (const file of ['app.js', 'generate.js']) w.eval(fs.readFileSync(path.join(staticDir, file), 'utf8'));
const G = w.QuamGen, d = w.document;
const sleep = ms => new Promise(r => setTimeout(r, ms));
let n = 0;
const ok = (c, m) => { assert(c, m); n++; };
const key = (el, k) => el.dispatchEvent(new w.KeyboardEvent('keydown', {key: k, bubbles: true}));

(async () => {
  w.confirm = () => true;
  G.init();
  G.state.spec.instruments.controllers = [{con: 1, fems: [{slot: 1, fem: 'mw'}, {slot: 3, fem: 'lf'}]}];
  G.state.spec.qubits = []; G.state.spec.lines = []; G.state.env = 'py';
  G._test.renderChassis();
  d.getElementById('gen-port-play-toggle').click();
  const rack = () => d.getElementById('gen-port-play-rack');
  const cells = () => Array.from(rack().querySelectorAll('.iw-port[data-play="1"]'));

  // D-23: every playable output is a focusable button with a port name; unused circles carry numbers.
  ok(cells().length === 16, 'D-23 16 playable cells, got ' + cells().length);
  ok(cells().every(c => c.getAttribute('tabindex') === '0' && c.getAttribute('role') === 'button'), 'D-23 tabindex+role');
  const first = rack().querySelector('.iw-port[data-play="1"][data-slot="1"][data-port="1"]');
  ok(first.getAttribute('aria-label') === 'con1 FEM 1 OUT 1 (MW)', 'D-23 aria-label: ' + first.getAttribute('aria-label'));
  const lfCell = rack().querySelector('.iw-port[data-play="1"][data-slot="3"][data-port="2"]');
  ok(lfCell.getAttribute('aria-label') === 'con1 FEM 3 AO 2 (LF)', 'D-12 LF aria-label says AO: ' + lfCell.getAttribute('aria-label'));
  const nums = Array.from(rack().querySelectorAll('.iw-port-num')).map(t => t.textContent);
  ok(nums.length === 16, 'D-23 16 numbers, got ' + nums.length);
  ok(nums.join(',') === '1,2,3,4,5,6,7,8,1,2,3,4,5,6,7,8', 'D-23 numbers in order: ' + nums.join(','));
  first.focus();
  ok(d.activeElement === first, 'D-23 a port takes focus (Tab reaches it)');
  key(first, 'Enter');
  ok(opened.length === 1 && String(opened[0].port.port) === '1' && String(opened[0].port.slot) === '1', 'D-23 Enter opens the popup');
  key(first, ' ');
  ok(opened.length === 2, 'D-23 Space opens the popup');
  key(first, 'ArrowDown');
  ok(d.activeElement.getAttribute('data-port') === '2' && d.activeElement.getAttribute('data-slot') === '1', 'D-23 ArrowDown -> OUT 2');
  key(d.activeElement, 'End');
  ok(d.activeElement.getAttribute('data-port') === '8' && d.activeElement.getAttribute('data-slot') === '1', 'D-23 End -> OUT 8 of the same FEM');
  key(d.activeElement, 'Home');
  ok(d.activeElement === first, 'D-23 Home -> OUT 1');
  first.querySelector('circle').dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
  ok(opened.length === 3, 'D-23 a click on the circle opens the popup');
  first.querySelector('button').click();
  ok(opened.length === 4, 'the hover anchor still opens it once');

  // D-25: a chip-less wizard hands no chip values (popup keeps its defaults).
  ok(opened[0].source.chipPort('con1/1/1') === null, 'D-25 chip-less wizard -> chipPort null');

  // D-24: a running set rings its members and shows the LIVE bar with time left and Stop all.
  const now = Date.now() / 1000;
  w.PortPlay.live = () => ({id: 'run9', members: [{pin: 'con1/1/2'}, {pin: 'con1/3/4'}],
                            started_at: now, max_s: 90});
  d.dispatchEvent(new w.Event('port-play:change'));
  const bar = d.getElementById('gen-port-play-live');
  ok(!bar.hidden, 'D-24 LIVE bar shown while running');
  ok(/con1 · FEM 1 · OUT 2 \(MW\)/.test(bar.textContent) && /con1 · FEM 3 · AO 4 \(LF\)/.test(bar.textContent), 'D-24/D-12 bar fallback labels name AO for LF: ' + bar.textContent);
  ok(/stops in 1:(30|29)/.test(bar.textContent), 'D-24 stops in m:ss: ' + bar.textContent);
  const live = cells().filter(c => c.classList.contains('iw-port-live')).map(c => c.getAttribute('data-slot') + '/' + c.getAttribute('data-port'));
  ok(live.join(',') === '1/2,3/4', 'D-24 rings on members only: ' + live);
  bar.querySelector('[data-live-stop]').click();
  const stop = calls.filter(c => /\/port-play\/stop$/.test(c.url)).pop();
  ok(stop && stop.body.id === 'run9', 'D-24 Stop all posts the run id');
  w.PortPlay.live = () => null;
  d.dispatchEvent(new w.Event('port-play:change'));
  ok(bar.hidden && !cells().some(c => c.classList.contains('iw-port-live')), 'D-24 bar and rings go when the run ends');

  // D-26: a verdict is stored in the draft, colours the port, reaches the CSV and survives a reload.
  opened[0].source.setCheck('con1/3/1', {verdict: 'bad', note: 'no signal, "noisy"'});
  ok(G.state.spec.port_checks['con1/3/1'].verdict === 'bad', 'D-26 verdict stored in spec');
  ok(rack().querySelector('.iw-port[data-slot="3"][data-port="1"]').classList.contains('iw-port-bad'), 'D-26 rack colours by verdict');
  const draft = Object.keys(w.sessionStorage).map(k => w.sessionStorage.getItem(k)).find(v => /port_checks/.test(v || ''));
  ok(!!draft, 'D-26 verdict written to the draft');
  const saved = JSON.parse(draft).spec.port_checks['con1/3/1'];
  ok(saved && saved.verdict === 'bad' && saved.note === 'no signal, "noisy"' && /^\d{4}-/.test(saved.at),
     'D-26 the draft a reload reads carries verdict, note and time');
  const csv = G._test.portChecksCsv();
  ok(/^port,verdict,note,checked_at\n/.test(csv), 'D-26 CSV header');
  ok(/con1\/3\/1,bad,"no signal, ""noisy""",\d{4}-/.test(csv), 'D-26 CSV row: ' + csv);
  ok(!d.getElementById('gen-port-checks-table').hidden, 'D-26 checks table shown');

  // D-25: Modify mode reads the open chip's wiring and hands its port values to the popup.
  chipReply = {wiring: {}, instrument: {controllers: {con1: {fems: {
    '1': {type: 'mw-fem', output_ports: {'1': [{role: 'xy', element: 'q1', label: 'q1.xy', band: 1,
          lo_frequency: 3.5e9, full_scale_power_dbm: 0}]}},
    '3': {type: 'lf-fem', output_ports: {'2': [{role: 'z', element: 'q1', label: 'q1.z', output_mode: 'amplified', offset: 0.25}]}}}}}}};
  G.state.mode = 'regenerate';
  G._test.renderChassis();
  await sleep(20);
  ok(calls.some(c => /\/api\/instrument\/data$/.test(c.url)), 'D-25 Modify mode fetches the chip wiring');
  ok(/q1/.test(rack().textContent), 'D-25 rack labels come from the chip');
  rack().querySelector('.iw-port[data-play="1"][data-slot="1"][data-port="1"]').dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
  const cp = opened[opened.length - 1].source.chipPort('con1/1/1');
  ok(cp && cp.lo_hz === 3.5e9 && cp.fsp === 0 && cp.band === 1, 'D-25 MW chip values: ' + JSON.stringify(cp));
  const lf = opened[opened.length - 1].source.chipPort('con1/3/2');
  ok(lf && lf.output_mode === 'amplified' && lf.offset === 0.25, 'D-25 LF chip values: ' + JSON.stringify(lf));

  console.log('port play rack selfcheck ok (' + n + ' assertions)');
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
