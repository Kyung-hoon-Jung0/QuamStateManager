// JS side of the step-5 pin-rule parity pin (QA review of regenerate-r2-13 /
// regenerate-r2-24): the wizard's stalePinGroups / pinCollisionGroups /
// femKindAt and config_generator.validate_spec's stale-pin and port-collision
// checks are two spellings of one rule. tests/test_generate_pin_rules_parity.py
// runs the same spec fixtures through validate_spec (and run_build.
// _fem_kind_at) and compares line-for-line -- the search_query_parity
// precedent: a test that fails when either side moves alone.
//
// Usage: node tests/generate_pin_rules_parity.cjs <cases.json>
// Prints [{stale: [...], collide: [...], kinds: {"con/slot": kind|null}}] per case.
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

const casesPath = process.argv[2];
if (!casesPath) { console.error('usage: node generate_pin_rules_parity.cjs <cases.json>'); process.exit(3); }
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));

const ROOT = path.join(__dirname, '..');
const HTML = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_generate.html'), 'utf8');
const GEN_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'generate.js'), 'utf8');

const dom = new JSDOM(
  '<!DOCTYPE html><html><body><div id="table-pane">' + HTML + '</div></body></html>',
  { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
const win = dom.window;
win.NumberInput = {
  fit() {},
  attach(el) { try { el.type = 'text'; } catch (e) {} },
  format() {},
  strip(s) { return String(s == null ? '' : s).replace(/,/g, ''); }
};
win.armPlainResize = function () {};
win.renderInstrumentWiring = function () {};
win.confirm = function () { return true; };
win.fetch = function () { return new win.Promise(function () {}); };
new win.Function(GEN_JS).call(win);
const T = win.QuamGen._test;

const out = cases.map(function (c) {
  const spec = JSON.parse(JSON.stringify(c.spec));
  T.state.spec = spec;
  const who = idx => idx.map(i => spec.lines[i].element + ' ' + spec.lines[i].line).sort();
  const kinds = {};
  (c.probe || []).forEach(function (cs) { kinds[cs[0] + '/' + cs[1]] = T.femKindAt(cs[0], cs[1]); });
  return {
    stale: T.stalePinGroups().map(g => [g.con, g.slot, g.want, who(g.idx)]),
    collide: T.pinCollisionGroups().map(g => [g.con, g.slot, g.port, who(g.idx)]),
    kinds: kinds
  };
});
process.stdout.write(JSON.stringify(out));
