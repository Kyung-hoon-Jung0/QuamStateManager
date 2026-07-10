/* Node self-check for the converter's safe expression parser + worked examples.
 * Loads web/static/calc.js with window/document stubs and asserts window.calcEval.
 * Run: node tests/calc_selfcheck.cjs   (also driven by tests/test_calc.py when node exists). */
const fs = require('fs');
const path = require('path');

global.window = {};
global.document = { readyState: 'complete', getElementById: () => null, addEventListener: () => {} };
global.navigator = {};

const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'calc.js'), 'utf8');
// eslint-disable-next-line no-eval
eval(src);
const E = global.window.calcEval;
if (typeof E !== 'function') { console.error('calcEval not exposed'); process.exit(1); }

let fails = 0;
function ok(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); fails++; } }
function approx(label, got, want, tol) {
    tol = tol || 1e-6;
    if (!(got && got.ok)) { console.error('FAIL: ' + label + ' did not evaluate (' + JSON.stringify(got) + ')'); fails++; return; }
    if (Math.abs(got.value - want) > tol) { console.error('FAIL: ' + label + ' = ' + got.value + ', want ' + want); fails++; }
}
function bad(label, expr) {
    const r = E(expr);
    if (r && r.ok) { console.error('FAIL: ' + label + ' should NOT evaluate, got ' + r.value); fails++; }
}

// ── worked examples (the user's own + the spec's verified values) ──
approx("headline 0.5*10^(-25/20)", E('0.5*10^(-25/20)'), 0.0281171, 1e-6);
approx("** alias same", E('0.5*10**(-25/20)'), 0.0281171, 1e-6);
approx("full-scale Vpk @FSP=-11", E('sqrt(2*50*10^(-11/10)/1000)'), 0.0891251, 1e-6);
approx("round-trip -25 dB", E('20*log10(0.0281171/0.5)'), -25.0, 1e-3);
approx("0 dBm Vrms @50", E('sqrt(10^(0/10)/1000*50)'), 0.2236068, 1e-6);

// ── parser correctness ──
approx("log10(100)=2", E('log10(100)'), 2, 1e-12);
approx("log(e)=1 (ln)", E('log(e)'), 1, 1e-12);
approx("ln alias", E('ln(e)'), 1, 1e-12);
approx("pow right-assoc 2^3^2=512", E('2^3^2'), 512, 1e-9);
approx("unary exponent 2^-3", E('2^-3'), 0.125, 1e-12);
approx("nested parens", E('(1+2)*(3+4)'), 21, 1e-12);
approx("exp notation", E('1e-3*1000'), 1, 1e-12);
ok(Math.abs(E('log10(100)').value - E('log(100)').value) > 1, 'log10 and log(ln) must be DISTINCT');

// ── safety: never resolve a global, never throw, never leak Inf/NaN ──
bad("window", 'window');
bad("fetch", 'fetch(1)');
bad("constructor", 'constructor');
bad("this", 'this');
// prototype-chain names as CALL forms must be 'unknown' at the lookup (hasOwnProperty guard)
bad("constructor(5)", 'constructor(5)');
bad("toString(1)", 'toString(1)');
bad("valueOf(1)", 'valueOf(1)');
bad("hasOwnProperty(1)", 'hasOwnProperty(1)');
bad("unknown fn", 'foo(2)');
bad("divide by zero", '1/0');
bad("log10(-1)=NaN", 'log10(-1)');
bad("empty", '');
bad("trailing garbage", '2+');

if (fails) { console.error('\n' + fails + ' self-check failure(s)'); process.exit(1); }
console.log('calc.js self-check: all assertions passed');
