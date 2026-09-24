/* QA F-S + diagnostics-r2-20 — what a one-click Diagnostics fix leaves behind.
 *
 *   F-S   "/diagnostics: collapse 'Config'. Click 'Update f_01 → …', accept.
 *          Config is open again after the fix."  applyDiagFix re-rendered the
 *          WHOLE page (GET /diagnostics into #table-pane), which re-drew every
 *          domain with the server's default open state; the folds are only
 *          carried across the #diag-findings self-refresh (docs/141 4l-review).
 *   r2-20 "Tab to 'Update f_01 →', Enter, Enter on the confirm —
 *          document.activeElement is BODY." The refresh replaces the fixed row
 *          and the focused button with it.
 *
 * Pins, against the real shipped app.js (the htmx stub performs the swaps the
 * real one would, so the OLD whole-page GET really does re-open the fold):
 *   S1  the fix never re-renders #table-pane; it announces diagnostics-changed
 *   S2  a domain the user folded is still folded once the list has refreshed
 *   F1  focus lands on the finding that took the fixed one's place
 *   F2  focus the user moved meanwhile is never stolen (the docs/75 rule)
 *   F3  a mouse press cancels the pending restore
 *   F4  the fixed row was the LAST domain's last: the domain above takes focus
 *       on its nearest (last) finding -- not the page title (review)
 *   F5  a MIDDLE domain's last row: the next domain down, its first finding
 *   F6  ...and when that next domain is folded, its summary
 *   F7  the very last finding on the page: focus falls back to the page title
 *
 * Run: node tests/diag_fix_focus_selfcheck.cjs   (needs jsdom; driven by
 *      tests/test_diag_fix_focus.py)
 */
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

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const dom = new JSDOM('<!doctype html><html><body></body></html>',
  { url: 'http://localhost/diagnostics', pretendToBeVisual: true });
const { window } = dom;
const d = window.document;
global.window = window; global.document = d; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.location = window.location;
global.localStorage = window.localStorage;
global.sessionStorage = window.sessionStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.confirm = global.confirm = () => true;          // "Enter on the confirm"
window.alert = global.alert = () => {};
window.Element.prototype.scrollIntoView = function () {};

// ── the server: which findings are left, rendered with ITS default folds ──
// (every domain holding a warning renders `open`, as _diagnostics_list.html
// does -- which is exactly what re-opened the user's fold)
let findings;
function reset(over) {
  findings = over || {
    config: ['config.a', 'config.b'],
    values: ['qubits.q1.f_01', 'qubits.q2.f_01', 'qubits.q3.f_01'],
    wiring: ['wiring.only'],
  };
}
function rowHtml(loc) {
  return '<tr class="diag-row diag-row-warning" data-bucket="warning">'
    + '<td>warning</td><td><code class="diag-loc">' + loc + '</code></td><td>msg</td>'
    + '<td class="diag-action">'
    + '<button type="button" class="btn-sm outline diag-goto" data-jump-path="' + loc + '">Go to field</button>'
    + '<button type="button" class="btn-sm diag-fix" data-action="set_value" data-dot-path="' + loc
    + '" data-value="1" data-confirm="1" data-confirm-text="sure?">Update ' + loc + '</button>'
    + '</td></tr>';
}
function findingsHtml() {
  let h = '<div id="diag-findings" hx-get="/diagnostics" hx-select="#diag-findings" hx-swap="outerHTML">'
    + '<div id="diag-filter-bar"><span class="diag-shown-count"></span></div><div class="diag-results">';
  Object.keys(findings).forEach(function (k) {
    if (!findings[k].length) return;
    h += '<details class="detail-section diag-domain" data-domain="' + k + '" open>'
      + '<summary class="section-header diag-domain-summary">' + k + '</summary>'
      + '<table class="diag-table"><tbody>' + findings[k].map(rowHtml).join('') + '</tbody></table></details>';
  });
  return h + '</div></div>';
}
function pageHtml() {
  return '<div class="table-header-row diag-header-row"><h2>Diagnostics</h2></div>' + findingsHtml();
}

// ── htmx: records every call and performs the swap the real one would ────
const ajaxCalls = [];
const triggers = [];
function fire(el, name, detail) {
  el.dispatchEvent(new window.CustomEvent(name, { bubbles: true, cancelable: true, detail: detail }));
}
function swapPane() {                         // GET /diagnostics -> #table-pane, innerHTML
  const pane = d.getElementById('table-pane');
  fire(pane, 'htmx:beforeSwap', { target: pane, shouldSwap: true,
    requestConfig: { verb: 'get' }, pathInfo: { finalRequestPath: '/diagnostics' } });
  pane.innerHTML = pageHtml();
  fire(pane, 'htmx:afterSwap', { target: pane,
    requestConfig: { verb: 'get' }, pathInfo: { finalRequestPath: '/diagnostics' } });
}
function swapFindings() {                     // the slot's self-refresh, outerHTML
  const old = d.getElementById('diag-findings');
  if (!old) return;
  fire(old, 'htmx:beforeSwap', { target: old, shouldSwap: true,
    requestConfig: { verb: 'get' }, pathInfo: { finalRequestPath: '/diagnostics' } });
  const holder = d.createElement('div');
  holder.innerHTML = findingsHtml();
  const fresh = holder.firstElementChild;
  old.parentNode.replaceChild(fresh, old);
  fire(fresh, 'htmx:afterSwap', { target: old,
    requestConfig: { verb: 'get' }, pathInfo: { finalRequestPath: '/diagnostics' } });
}
window.htmx = {
  ajax: function (verb, url, opts) {
    ajaxCalls.push({ verb: verb, url: url, target: opts && opts.target });
    if (opts && opts.target === '#table-pane' && String(url).indexOf('/diagnostics') === 0) swapPane();
    return Promise.resolve();
  },
  trigger: function (el, name) {
    triggers.push(name);
    // hx-trigger="diagnostics-changed from:body delay:600ms" (shortened)
    if (name === 'diagnostics-changed') setTimeout(swapFindings, 60);
  },
  process: function () {},
};
global.htmx = window.htmx;

// ── the fix endpoint: the finding is resolved on the server ──────────────
global.fetch = window.fetch = function (url, opts) {
  if (String(url).indexOf('/diagnostics/apply-fix') === 0) {
    const body = new URLSearchParams(opts && opts.body || '');
    const loc = body.get('dot_path');
    Object.keys(findings).forEach(function (k) {
      findings[k] = findings[k].filter(function (x) { return x !== loc; });
    });
    return Promise.resolve({ json: () => Promise.resolve({
      ok: true, tray_html: '<div id="pending-tray" data-seq="2"></div>' }) });
  }
  return new Promise(function () {});
};

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
// app.js runs in the Node realm here: a bare top-level function lands on
// global, while applyDiagFix asks for window._swapPendingTray -- bridge it
// the way a browser's classic script would have.
if (!window._swapPendingTray && typeof global._swapPendingTray === 'function') {
  window._swapPendingTray = global._swapPendingTray;
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function build(over) {
  reset(over);
  ajaxCalls.length = 0; triggers.length = 0;
  d.body.innerHTML = '<div id="pending-tray" data-seq="1"></div>'
    + '<input id="elsewhere"><div id="table-pane">' + pageHtml() + '</div>';
}
function dom_(k) { return d.querySelector('#diag-findings details.diag-domain[data-domain="' + k + '"]'); }
function fixBtn(loc) { return d.querySelector('button.diag-fix[data-dot-path="' + loc + '"]'); }
function desc(el) {
  if (!el) return 'null';
  return el.tagName + (el.className ? '.' + String(el.className).split(' ').join('.') : '')
    + (el.getAttribute && el.getAttribute('data-jump-path') ? '[' + el.getAttribute('data-jump-path') + ']' : '');
}
// the whole round: POST -> tray -> 350 ms announcer -> slot refresh
const SETTLE = 900;

async function main() {
  // ── S1/S2/F1: fold Config, fix a values row by keyboard ──────────────────
  build();
  dom_('config').open = false;                     // the user folded Config
  let b = fixBtn('qubits.q2.f_01');
  b.focus();
  ok(d.activeElement === b, 'fixture: the fix button has keyboard focus');
  window.applyDiagFix(b);
  await sleep(SETTLE);

  const paneGets = ajaxCalls.filter((c) => c.target === '#table-pane');
  ok(paneGets.length === 0,
    'S1: the fix does not re-render #table-pane (got ' + JSON.stringify(ajaxCalls) + ')');
  ok(triggers.indexOf('diagnostics-changed') >= 0,
    'S1: it announces diagnostics-changed, which refreshes the findings slot');
  ok(!fixBtn('qubits.q2.f_01'), 'fixture: the resolved row is gone after the refresh');
  ok(dom_('config') && dom_('config').open === false,
    'S2: the folded Config domain is still folded after the fix');
  ok(dom_('values') && dom_('values').open === true, 'S2: an open domain stays open');

  const next = d.querySelector('button.diag-goto[data-jump-path="qubits.q3.f_01"]');
  ok(!!next && d.activeElement === next,
    'F1: focus lands on the finding that took the fixed one\'s place (got '
    + desc(d.activeElement) + ')');

  // ── F2: focus the user placed meanwhile is theirs ────────────────────────
  build();
  b = fixBtn('qubits.q1.f_01');
  b.focus();
  window.applyDiagFix(b);
  await sleep(50);                                  // POST answered, refresh pending
  const other = d.getElementById('elsewhere');
  other.focus();
  await sleep(SETTLE);
  ok(d.activeElement === other,
    'F2: an element the user focused before the refresh keeps focus (got '
    + desc(d.activeElement) + ')');

  // ── F3: a mouse press cancels the pending restore ────────────────────────
  build();
  b = fixBtn('qubits.q1.f_01');
  b.focus();
  window.applyDiagFix(b);
  await sleep(50);
  d.body.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true }));
  await sleep(SETTLE);
  ok(d.activeElement === d.body,
    'F3: after a mouse press the refresh moves nothing (got ' + desc(d.activeElement) + ')');

  // ── F4: the LAST domain's last finding: the domain above, nearest row ────
  // (review: this used to land on the page title while findings remained)
  build();
  b = fixBtn('wiring.only');
  b.focus();
  window.applyDiagFix(b);
  await sleep(SETTLE);
  ok(!dom_('wiring'), 'fixture: fixing the only wiring finding removes the domain');
  const above = d.querySelector('button.diag-goto[data-jump-path="qubits.q3.f_01"]');
  ok(!!above && d.activeElement === above,
    'F4: with the last domain gone, focus lands on the nearest finding above it (got '
    + desc(d.activeElement) + ')');

  // ── F5: a MIDDLE domain's only finding: the next domain down ─────────────
  const mid = { values: ['qubits.q4.f_01'], config: ['pulses.const_pulse', 'config.b'] };
  build({ values: mid.values.slice(), config: mid.config.slice() });
  b = fixBtn('qubits.q4.f_01');
  b.focus();
  window.applyDiagFix(b);
  await sleep(SETTLE);
  ok(!dom_('values'), 'fixture: fixing the only values finding removes the domain');
  const below = d.querySelector('button.diag-goto[data-jump-path="pulses.const_pulse"]');
  ok(!!below && d.activeElement === below,
    'F5: focus lands on the first finding of the next domain, not the page title (got '
    + desc(d.activeElement) + ')');

  // ── F6: ...and a folded next domain gives its summary ────────────────────
  build({ values: mid.values.slice(), config: mid.config.slice() });
  dom_('config').open = false;
  b = fixBtn('qubits.q4.f_01');
  b.focus();
  window.applyDiagFix(b);
  await sleep(SETTLE);
  const sum = dom_('config') && dom_('config').querySelector(':scope > summary');
  ok(dom_('config') && dom_('config').open === false, 'fixture: the folded domain stays folded');
  ok(!!sum && d.activeElement === sum,
    'F6: a folded next domain takes focus on its summary (got ' + desc(d.activeElement) + ')');

  // ── F7: the very last finding: the page title takes focus ────────────────
  build({ wiring: ['wiring.only'] });
  b = fixBtn('wiring.only');
  b.focus();
  window.applyDiagFix(b);
  await sleep(SETTLE);
  const h2 = d.querySelector('.diag-header-row h2');
  ok(!d.querySelector('#diag-findings details.diag-domain'), 'fixture: no domain is left');
  ok(d.activeElement === h2 && h2.getAttribute('tabindex') === '-1',
    'F7: with no finding left, focus falls back to the page title (got '
    + desc(d.activeElement) + ')');

  console.log(fails ? 'FAILED (' + fails + ')'
    : 'diag_fix_focus_selfcheck ok (' + asserts + ' assertions)');
  process.exit(fails ? 1 : 0);
}

main().catch(function (e) { console.error(String(e && e.stack || e)); process.exit(1); });
