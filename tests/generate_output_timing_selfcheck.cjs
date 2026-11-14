// Behavioral check: the Review row "Output timing" states what the build does
// with MW bands (run_build _align_output_timing) for the spec on screen.
//
//   one class: all band 2 -> flux 161 ns; bands 1 and 3 only -> flux 141 ns, no MW delay
//   mixed: band 1/3 outputs get 20 ns; the time-of-flight count names the shifted resonators
//   direction: xy band 3 with readout band 2 shifts no resonator
//   non-LF flux: the rule does not apply
//
// Run: node tests/generate_output_timing_selfcheck.cjs   (needs jsdom)
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}

const ROOT = path.join(__dirname, '..');
const HTML = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');
const GEN_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'generate.js'), 'utf8');

let fails = 0;
let asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

async function world() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.NumberInput = {
    fit() {}, attach(el) { try { el.type = 'text'; } catch (e) {} }, format() {},
    strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
  };
  win.armPlainResize = function () {};
  win.renderInstrumentWiring = function () {};
  win.confirm = function () { return true; };
  win.showToast = function () {};
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(GEN_JS).call(win);
  await new Promise(function (r) { win.setTimeout(r, 0); });
  const G = win.QuamGen;
  G.state.spec.instruments.controllers = [{ con: 1, fems: [{ slot: 1, fem: 'mw' }, { slot: 2, fem: 'lf' }] }];
  return { T: G._test, state: G.state };
}

function spec(qubits, fluxFem) {
  // qubits: [[id, xyBand, roBand], ...]; every line pinned
  const lines = [], qp = {}, rp = {};
  let port = 1;
  qubits.forEach(function (q) {
    lines.push({ element: q[0], line: 'drive', channel: { con: 1, slot: 1, out_port: port++ } });
    lines.push({ element: q[0], line: 'resonator', channel: { con: 1, slot: 1, out_port: 8, in_port: 1 } });
    lines.push({ element: q[0], line: 'flux', channel: { con: 1, slot: fluxFem || 2, out_port: port } });
    qp[q[0]] = { band: q[1] };
    rp[q[0]] = { band: q[2] };
  });
  return { lines: lines, populate: { qubit: qp, resonator: rp } };
}

(async function main() {
  const w = await world();
  const T = w.T;
  const txt = function (sp) { return T.outputTimingReviewText(sp); };

  let t = txt(spec([['q1', 2, 2], ['q2', 2, 2]]));
  ok(t === 'All MW outputs are in band 2: flux ports 161 ns.', '[one class] band 2: ' + t);

  t = txt(spec([['q1', 1, 3], ['q2', 1, 3]]));
  ok(t === 'All MW outputs are in bands 1 and 3: flux ports 141 ns.', '[one class] bands 1 and 3: ' + t);

  t = txt(spec([['q1', 2, 3]]));
  ok(t === 'MW bands 2 and 3 are in use: band 3 outputs get 20 ns so every MW output leaves ' +
     'together; flux ports 161 ns; time of flight +20 ns on 1 resonator.', '[mixed] xy 2, readout 3: ' + t);

  t = txt(spec([['q1', 2, 3], ['q2', 2, 3], ['q3', 2, 3], ['q4', 2, 3], ['q5', 2, 3]]));
  ok(/time of flight \+20 ns on 5 resonators\.$/.test(t), '[mixed] five resonators: ' + t);

  t = txt(spec([['q1', 3, 2]]));
  ok(t === 'MW bands 2 and 3 are in use: band 3 outputs get 20 ns so every MW output leaves ' +
     'together; flux ports 161 ns.', '[direction] xy 3, readout 2 moves no time of flight: ' + t);

  t = txt(spec([['q1', 2, 2], ['q2', 1, 2]]));
  ok(t.indexOf('MW bands 1 and 2 are in use: band 1 outputs get 20 ns') === 0, '[mixed] pair 2 / 1: ' + t);

  const noLf = spec([['q1', 2, 2]], 1);
  noLf.lines = noLf.lines.filter(function (l) { return l.line === 'flux'; });
  t = txt(noLf);
  ok(/not LF-FEM outputs/.test(t), '[non-LF] the rule does not apply: ' + t);

  w.state.mode = 'regenerate';
  t = txt(spec([['q1', 2, 3]]));
  ok(/Stored port delays and times of flight are kept/.test(t), '[regenerate] stored values kept: ' + t);

  console.log(fails ? fails + ' failure(s)' : 'OK (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
})();
