/* jsdom selfcheck for docs/141 4ae — the app's ONE search grammar reaching
 * the three boxes that were AND-only, and the Calculator's size memory.
 *
 * The placeholder now PROMISES `space = AND, | = OR` on every search box in
 * SM. Three of them did not have the OR: filterTable (the component tables),
 * filterDetailPanel (the inspector's in-panel search) and the all-values
 * grid's scoped box. A promise the surface cannot keep is the bug this pins.
 *
 * Run: node tests/search_hint_selfcheck.cjs  (driven by tests/test_search_hint.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const TABLE = '<table id="t1"><tbody>' +
  '<tr><td>qubits.q1</td><td>rabi</td></tr>' +
  '<tr><td>qubits.q2</td><td>ramsey</td></tr>' +
  '<tr><td>qubits.q3</td><td>readout</td></tr>' +
  '</tbody></table>';
const PANEL = '<div id="inspector-pane"><article class="qubit-detail"><details class="detail-section" open><summary>Resonator</summary>' +
  '<table class="prop-table"><tbody>' +
  '<tr><td>f_01</td><td>7000000000</td></tr>' +
  '<tr><td>T1</td><td>0.00002</td></tr>' +
  '<tr><td>anharmonicity</td><td>-150000000</td></tr>' +
  '</tbody></table></details></article></div>';
const CALC = '<div id="calc-popover" class="calc-popover calc-hidden"><div class="calc-header" id="calc-header">' +
  '<strong class="calc-title">Calculator</strong><span class="calc-header-tools"></span></div>' +
  '<div class="calc-body"><details class="calc-sec" open><summary class="calc-sec-label">S</summary>' +
  '<input type="text" id="calc-s1-dp" value="-25"><input type="text" id="calc-s1-amp" value="0.5">' +
  '<input type="text" id="calc-s1-from" value="10"><input type="text" id="calc-s1-to" value="-15">' +
  '<span id="calc-s1-k"></span><span id="calc-s1-anew"></span></details></div>' +
  '<div class="calc-foot"><input type="text" id="calc-expr" value="1+1"><span id="calc-expr-res"></span></div></div>' +
  '<button class="calc-btn" id="calc-btn"></button>';

const dom = new JSDOM('<!doctype html><html><body>' + TABLE + PANEL + CALC +
  '<input id="q1"><input id="q2">' +
  '</body></html>', { url: 'http://localhost/bulk', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.navigator = window.navigator; global.location = window.location;
const store = {};
global.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
window.localStorage = global.localStorage; global.sessionStorage = global.localStorage;
global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
// a ResizeObserver we DRIVE: calc.js's size memory hangs off it
let roCb = null, roTargets = [];
global.ResizeObserver = class { constructor(cb) { roCb = cb; } observe(el) { roTargets.push(el); } disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'calc.js'), 'utf8'));

(async function main() {
  await new Promise((r) => setTimeout(r, 20));
  const d = window.document;

  // ── filterTable: the component tables (Qubits / Flux / Pairs / …) ─────────
  {
    const box = d.getElementById('q1');
    const rows = () => Array.from(d.querySelectorAll('#t1 tbody tr'));
    const shown = () => rows().filter((r) => r.style.display !== 'none').map((r) => r.cells[0].textContent);
    const type = async (v) => {
      box.value = v; window.filterTable(box, 't1');
      await new Promise((r) => setTimeout(r, 260));      // _debounce
    };
    await type('q1');
    ok(shown().join() === 'qubits.q1', 'filterTable: a single term still filters as before');
    await type('q1 rabi');
    ok(shown().join() === 'qubits.q1', 'filterTable: space is AND');
    await type('q1 ramsey');
    ok(shown().length === 0, 'filterTable: AND really excludes');
    await type('rabi | ramsey');
    ok(shown().join() === 'qubits.q1,qubits.q2', 'filterTable: a standalone | is OR — the placeholder is honest now');
    await type('qubits rabi | ramsey');
    ok(shown().join() === 'qubits.q1,qubits.q2', 'filterTable: | binds tighter than the space');
    await type('');
    ok(shown().length === 3, 'filterTable: an empty query shows everything');
  }

  // ── filterDetailPanel: the inspector's in-panel search ────────────────────
  {
    const box = d.getElementById('q2');
    const rows = () => Array.from(d.querySelectorAll('.prop-table tbody tr'));
    const shown = () => rows().filter((r) => r.style.display !== 'none').map((r) => r.cells[0].textContent);
    const type = async (v) => {
      box.value = v; window.filterDetailPanel(box);
      await new Promise((r) => setTimeout(r, 260));
    };
    await type('t1');
    ok(shown().join() === 'T1', 'detail panel: a single term filters');
    await type('f_01 7000000000');
    ok(shown().join() === 'f_01', 'detail panel: space is AND');
    await type('t1 | anharmonicity');
    ok(shown().join() === 'T1,anharmonicity', 'detail panel: a standalone | is OR');
    await type('');
    ok(shown().length === 3, 'detail panel: cleared shows everything');
  }

  // ── the Calculator remembers its size (4ae) ───────────────────────────────
  {
    const p = d.getElementById('calc-popover');
    ok(!!window.CalcWindow, 'calc.js exposes its size plumbing');
    // no stored size -> the CSS size stands, nothing is written
    window.toggleCalc(d.getElementById('calc-btn'));
    ok(!p.classList.contains('calc-hidden'), 'the calculator opens');
    ok(p.style.width === '' && p.style.height === '', 'with nothing remembered it keeps its stylesheet size');
    ok(roTargets.indexOf(p) >= 0, 'and its size is being watched');
    // a user resize is remembered
    Object.defineProperty(p, 'offsetWidth', { value: 520, configurable: true });
    Object.defineProperty(p, 'offsetHeight', { value: 700, configurable: true });
    roCb();
    await new Promise((r) => setTimeout(r, 300));
    const stored = () => window.localStorage.getItem('quam_calc_size');
    ok(stored() === '{"w":520,"h":700}', 'a resize is stored: ' + stored());
    // and applied on the next open
    window.toggleCalc(d.getElementById('calc-btn'));   // close
    window.toggleCalc(d.getElementById('calc-btn'));   // open again
    ok(p.style.width === '520px' && p.style.height === '700px', 'reopening restores it: ' + p.style.width + '/' + p.style.height);
    // the viewport clamp must not overwrite the larger remembered size
    window.innerWidth = 400; window.innerHeight = 300;
    window.toggleCalc(d.getElementById('calc-btn'));
    window.toggleCalc(d.getElementById('calc-btn'));
    ok(p.style.width === '384px' && p.style.height === '284px', 'a small viewport clamps the applied size: ' + p.style.width);
    Object.defineProperty(p, 'offsetWidth', { value: 384, configurable: true });
    Object.defineProperty(p, 'offsetHeight', { value: 284, configurable: true });
    roCb();
    await new Promise((r) => setTimeout(r, 300));
    ok(stored() === '{"w":520,"h":700}',
       'but the clamp is NOT remembered as the user choice: ' + stored());
  }

  console.log(fails ? ('FAILED: ' + fails) : 'ALL OK (17 assertions)');
  process.exit(fails ? 1 : 0);
})();
