/* A value on the dataset surfaces is copied and shown WHOLE, and only the
 * value. The REAL app.js under jsdom:
 *
 * datasets-r2-31 -- the Results / Figures fit tables' "Copy" (TSV) and the
 * single-cell click-copy took a value cell's textContent whenever it had no
 * data-copy, so the chrome rode along: "success\ttrue copy-only", a mapped
 * int row's "5 Apply →Go to state", a mapping row's inline <script> source.
 * Now data-copy wins, and otherwise a CLONE is stripped of buttons / scripts /
 * styles / the copy-only badge (the live cell is untouched).
 *
 * F21 -- the plot-apply popup's PREVIOUS value was cut to "6723520634.…"
 * (CSS ellipsis, pinned in tests/test_key_metric_nan.py); _setOldVal now
 * also carries the whole value as the slot's title, and clears it for null.
 *
 * Run: node tests/ds_value_text_selfcheck.cjs  (driven by tests/test_key_metric_nan.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }

const TAB = String.fromCharCode(9);
const dom = new JSDOM(`<!doctype html><html><body>
  <details open class="detail-section">
    <summary class="section-header">q3<button type="button" class="btn-sm outline section-copy-btn" id="copy-btn">Copy</button></summary>
    <table class="prop-table" role="grid">
      <thead><tr><th class="col-prop">Property</th><th class="col-val">Value</th></tr></thead>
      <tbody>
        <tr><td class="col-prop"><code>frequency</code></td>
            <td class="col-val" data-copy="6722216523.217444">
              6.722217e+09
              <button type="button" class="btn-sm outline fit-apply-btn">Apply &rarr;</button><button type="button" class="btn-sm outline fit-goto-btn">Go to state</button>
            </td></tr>
        <tr><td class="col-prop"><code>success</code></td>
            <td class="col-val" id="bool-cell">
              <span class="outcome-badge outcome-ok" id="bool-badge">true</span>
              <span class="fit-copy-only muted" id="copy-only-badge" title="not mapped">copy-only</span>
            </td></tr>
        <tr><td class="col-prop"><code>n_int_mapped</code></td>
            <td class="col-val">
              5
              <button type="button" class="btn-sm outline fit-apply-btn">Apply &rarr;</button><button type="button" class="btn-sm outline fit-goto-btn">Go to state</button>
            </td></tr>
        <tr><td class="col-prop"><code>candidates</code></td>
            <td class="col-val" data-copy='{"a": 1, "b": [2, 3]}'>
              <div class="ds-inline-tree" id="t1">{…}</div>
              <script>(function(){ renderJsonTree('t1', {"a": 1}); })();</script>
              <span class="fit-copy-only muted">copy-only</span>
            </td></tr>
        <tr><td class="col-prop"><code>legacy_tree</code></td>
            <td class="col-val">
              <div class="ds-inline-tree" id="t2">{…}</div>
              <script>(function(){ renderJsonTree('t2', {"x": 1}); })();</script>
              <span class="fit-copy-only muted">copy-only</span>
            </td></tr>
        <tr><td class="col-prop"><code>missing</code></td>
            <td class="col-val">
              <em class="null-value">not set</em>
              <span class="fit-copy-only muted">copy-only</span>
            </td></tr>
      </tbody>
    </table>
  </details>
  <div class="plot-apply-row"><span class="plot-apply-old-val muted" id="old-slot">…</span></div>
</body></html>`, { url: 'http://localhost/datasets', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.MouseEvent = window.MouseEvent;
function bridge(name, value) {
    try { global[name] = value; }
    catch (e) {
        try { Object.defineProperty(global, name, { value: value, configurable: true }); }
        catch (e2) { /* the realm already provides it */ }
    }
}
bridge('navigator', window.navigator);
bridge('location', window.location);
bridge('localStorage', window.localStorage);
bridge('sessionStorage', window.sessionStorage);
global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

window.eval(fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));

const doc = window.document;
const copied = [];
window.copyWithFeedback = function (text) { copied.push(String(text)); return Promise.resolve(true); };

// ── datasets-r2-31: the table Copy ───────────────────────────────────────
window.copyPropTable(doc.getElementById('copy-btn'), 'tsv');
const want = [
    'frequency' + TAB + '6722216523.217444',
    'success' + TAB + 'true',
    'n_int_mapped' + TAB + '5',
    'candidates' + TAB + '{"a": 1, "b": [2, 3]}',
    'legacy_tree' + TAB + '{…}',
    'missing' + TAB + 'not set',
].join('\n');
ok(copied.length === 1, 'Copy puts ONE clipboard payload');
const got = copied[0] || '';
ok(got === want, 'the TSV holds the values and nothing else:\n' + JSON.stringify(got));
ok(got.indexOf('copy-only') < 0, 'no "copy-only" badge text in the TSV');
ok(got.indexOf('Apply') < 0 && got.indexOf('Go to state') < 0, 'no button labels in the TSV');
ok(got.indexOf('renderJsonTree') < 0, 'no inline <script> source in the TSV');
ok(doc.getElementById('copy-only-badge') && doc.querySelectorAll('#bool-cell button, #bool-cell .fit-copy-only').length === 1,
   'the live cell keeps its badge (a CLONE is stripped)');

// ── datasets-r2-31: the single-cell click-copy ───────────────────────────
copied.length = 0;
doc.getElementById('copy-only-badge').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
ok(copied.length === 1 && copied[0] === 'true',
   'clicking the copy-only badge copies the value only: ' + JSON.stringify(copied[0]));
copied.length = 0;
doc.getElementById('bool-badge').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
ok(copied.length === 1 && copied[0] === 'true', 'clicking the value copies it: ' + JSON.stringify(copied[0]));

// ── F21: the previous value is carried whole ─────────────────────────────
// a top-level function declaration: the eval below defines it on the realm
// that ran it (Node's global here), not on the jsdom window
const setOldVal = window._setOldVal || global._setOldVal;
ok(typeof setOldVal === 'function', 'the shipped _setOldVal is reachable');
const slot = doc.getElementById('old-slot');
setOldVal(slot, 6723520634.123456);
ok(slot.textContent === '6723520634.123456', 'the slot shows the full value');
ok(slot.getAttribute('title') === '6723520634.123456', 'and carries it as its title');
setOldVal(slot, null);
ok(!slot.hasAttribute('title'), 'a null previous value clears the stale title');

process.exit(fails ? 1 : 0);
