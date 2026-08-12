/* docs/117 — the auto-apply flusher, under jsdom with the REAL auto-apply.js.
 *
 * What matters here is not that it fires, but that it fires EXACTLY once per
 * burst and never when it must not: the timing rule the user chose is
 * "immediate, and coalesce anything that arrives while a write is in flight".
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static',
                      'auto-apply.js');

const HTML = '<!doctype html><html><body>'
  + '<div id="status-bar"></div>'
  + '<li id="topbar-tray-slot">'
  + '<div id="pending-tray" data-change-count="0" data-working-dirty="0" data-seq="1">'
  + '<div id="applied-log"><button class="applied-log-toggle"></button></div>'
  + '</div></li></body></html>';

const dom = new JSDOM(HTML, { runScripts: 'outside-only', url: 'http://localhost/' });
const w = dom.window;

let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log('  ok  ' + name);
  else { failures++; console.error('FAIL  ' + name + (detail ? ' — ' + detail : '')); }
}
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

// ── a recording htmx stub whose promise we control ───────────────────────
const calls = [];
let settle = null;
w.htmx = {
  ajax: function (method, url, opts) {
    calls.push({ method: method, url: url, target: opts && opts.target });
    return new Promise(function (res) { settle = res; });
  },
};

w.eval(fs.readFileSync(SRC, 'utf8'));
w.document.dispatchEvent(new w.Event('DOMContentLoaded'));

const tray = () => w.document.getElementById('pending-tray');
function setTray(attrs) {
  const t = tray();
  Object.keys(attrs).forEach(function (k) { t.setAttribute(k, attrs[k]); });
}
function replaceTrayOuterHTML(attrs) {
  // how _swapPendingTray and OOB swaps both land: a NEW element, not a
  // mutated one. An event listener on the old node would miss this.
  const t = tray();
  const a = Object.assign({ 'data-change-count': '0', 'data-working-dirty': '0',
                            'data-auto-apply': '1', 'data-seq': '2' }, attrs);
  const attrStr = Object.keys(a).map(k => k + '="' + a[k] + '"').join(' ');
  t.outerHTML = '<div id="pending-tray" ' + attrStr + '></div>';
}

(async function () {
  // 1. disarmed: nothing ever fires
  setTray({ 'data-change-count': '3', 'data-working-dirty': '1' });
  await sleep(30);
  check('A1 disarmed tray never flushes', calls.length === 0, String(calls.length));

  // 2. armed + pending: immediate (no debounce)
  const t0 = Date.now();
  setTray({ 'data-auto-apply': '1' });
  await sleep(30);
  check('A2 armed + pending flushes immediately',
        calls.length === 1 && (Date.now() - t0) < 100, JSON.stringify(calls));
  check('A3 it presses the ONE live writer',
        calls[0] && calls[0].url === '/state/apply-to-live', JSON.stringify(calls[0]));
  check('A4 it targets the tray', calls[0] && calls[0].target === '#pending-tray');

  // 3. while in flight, further edits coalesce into exactly ONE follow-up
  setTray({ 'data-change-count': '4' });
  setTray({ 'data-change-count': '5' });
  setTray({ 'data-change-count': '6' });
  await sleep(30);
  check('A5 edits during a flush do not queue N writes', calls.length === 1,
        String(calls.length));
  settle();                       // the first write completes
  await sleep(30);
  check('A6 exactly one follow-up flush', calls.length === 2, String(calls.length));
  settle();
  await sleep(30);
  check('A7 and then it stops', calls.length === 2, String(calls.length));

  // 4. nothing pending ⇒ never flush (an empty tray must not be pushed)
  const n = calls.length;
  setTray({ 'data-change-count': '0', 'data-working-dirty': '0', 'data-seq': '9' });
  await sleep(30);
  check('A8 an empty tray is never flushed', calls.length === n, String(calls.length));

  // 5. the swap channel that an event listener would miss: outerHTML replace
  replaceTrayOuterHTML({ 'data-change-count': '2' });
  await sleep(30);
  check('A9 an outerHTML tray replacement still triggers a flush',
        calls.length === n + 1, String(calls.length));
  settle();
  await sleep(20);

  // 6. a disarm signal stops scheduling even if the attribute lingers
  w.document.dispatchEvent(new w.CustomEvent('autoApplyDisarm',
                                             { detail: { reason: 'conflict' } }));
  const m = calls.length;
  setTray({ 'data-change-count': '7' });
  await sleep(30);
  check('A10 a disarm signal stops the flusher', calls.length === m,
        String(calls.length));
  check('A11 and it says why', /auto-apply is OFF/i.test(
        w.document.getElementById('status-bar').textContent));

  // 7. the log collapse toggle persists (re-mount it: the outerHTML replace
  //    above wiped the tray's children, exactly as a real swap would)
  tray().innerHTML = '<div id="applied-log"><button class="applied-log-toggle"></button></div>';
  const log = w.document.getElementById('applied-log');
  if (log) {
    w.AutoApply.toggleLog(w.document.querySelector('.applied-log-toggle'));
    check('A12 the log collapses', log.classList.contains('applied-log-collapsed'));
    w.AutoApply.toggleLog(w.document.querySelector('.applied-log-toggle'));
    check('A13 and reopens', !log.classList.contains('applied-log-collapsed'));
  }

  if (failures) { console.error(failures + ' check(s) failed'); process.exit(1); }
  console.log('all checks passed');
})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });
