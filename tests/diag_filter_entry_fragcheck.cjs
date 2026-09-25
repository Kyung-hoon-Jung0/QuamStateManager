/* QA F-D -- a saved severity filter must never hide crash errors from the page
 * the crash banner sends you to.
 *
 * The Diagnostics pills persist in ONE localStorage key (`quam_diag_filter`),
 * across reloads and chips, by design. But the red banner's "Review
 * diagnostics" is ABOUT the crash errors, and it used to land on a page whose
 * saved filter hid exactly those rows -- with only a muted "2 of 4 shown" at
 * the far right (and the same button also hides the banner, so both crash
 * signals were gone at once). Plain navigation (sidebar, Back) had the same
 * trap, with nothing saying a filter was hiding the errors.
 *
 * Pins, against the REAL app.js and the REAL server-rendered banner +
 * /diagnostics fragment (written by the pytest driver, argv[2], argv[3]):
 *   E1  "Review diagnostics" turns the error bucket back on, and the rows show
 *   E2  plain navigation keeps the saved choice (persistence is intended)...
 *   E3  ...but says so next to the pills, naming the count, with a Show button
 *   E4  that button shows the errors; the note goes away
 *   E5  everything hidden (no errors) -> "Every finding is hidden" + Show all
 *   E6  the pinned ".diag-shown-count" text keeps its "N of M shown" form
 *
 * Run via tests/test_diagnostics_banner_routes.py (needs jsdom). Named
 * *_fragcheck, not *_selfcheck: it reads the fragments its pytest driver
 * writes, and `npm run selfcheck` runs every *_selfcheck.cjs with no
 * arguments (the review of this fix found that runner red on a clean tree).
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require('jsdom')); } catch (e) {
  console.log('SKIP: jsdom not installed');
  process.exit(2);
}

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
if (!process.argv[2] || !process.argv[3]) {
  console.error('FAIL  usage: node <this> <banner-fragment.html> <diagnostics-fragment.html>');
  process.exit(1);
}
const BANNER = fs.readFileSync(process.argv[2], 'utf8');
const DIAG = fs.readFileSync(process.argv[3], 'utf8');
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const SQ = fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m, detail) {
  asserts++;
  if (!c) { fails++; console.error('FAIL: ' + m + (detail ? ' -- ' + detail : '')); }
}

function boot(savedFilter) {
  const vc = new VirtualConsole();
  const dom = new JSDOM('<!doctype html><html><body>'
    + '<div id="diagnostics-banner-slot">' + BANNER + '</div>'
    + '<main><div id="table-pane"><p>Live State Edit</p></div></main>'
    + '<div id="status-bar"></div></body></html>',
    { url: 'http://localhost/bulk', runScripts: 'dangerously', pretendToBeVisual: true,
      virtualConsole: vc });
  const w = dom.window;
  if (savedFilter) w.localStorage.setItem('quam_diag_filter', JSON.stringify(savedFilter));
  w.fetch = function () { return new w.Promise(function () {}); };
  w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
  w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
  w.requestAnimationFrame = function (f) { return w.setTimeout(f, 0); };
  w.Element.prototype.scrollIntoView = function () {};
  w.htmx = { ajax: function () { return w.Promise.resolve(); }, trigger: function () {},
             process: function () {}, swap: function () {} };
  w.eval(SQ);
  w.eval(APP);
  return w;
}

// What htmx does for the banner button / a sidebar link: put /diagnostics into
// #table-pane, then fire htmx:afterSwap (the app's own re-apply hook).
function navigateToDiagnostics(w, html) {
  const pane = w.document.getElementById('table-pane');
  pane.innerHTML = html || DIAG;
  pane.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: pane } }));
}
function rows(w, bucket) {
  return Array.prototype.slice.call(
    w.document.querySelectorAll('#table-pane tr.diag-row' + (bucket ? '[data-bucket="' + bucket + '"]' : '')));
}
function visible(tr) { return tr.style.display !== 'none'; }
function note(w) { return w.document.querySelector('#diag-filter-bar .diag-filter-hidden-note'); }

ok(/diag-error-banner/.test(BANNER) && /Review diagnostics/.test(BANNER),
   'preflight: the banner fragment is the crash banner');
ok(/data-bucket="error"/.test(DIAG), 'preflight: the /diagnostics fragment has error rows');

// E1 -- the banner entry point
{
  const w = boot({ error: false });
  const btn = Array.prototype.slice.call(w.document.querySelectorAll('#diagnostics-banner button'))
    .filter(function (b) { return /Review diagnostics/.test(b.textContent); })[0];
  ok(!!btn, 'E1 preflight: the Review diagnostics button is there');
  btn.click();                                   // the inline onclick, as shipped
  navigateToDiagnostics(w);
  const errs = rows(w, 'error');
  ok(errs.length > 0 && errs.every(visible),
     'E1 after "Review diagnostics" every error row is visible',
     errs.map(function (t) { return t.style.display; }).join(','));
  const pill = w.document.querySelector('#diag-filter-bar .diag-pill[data-bucket="error"]');
  ok(pill && !pill.classList.contains('diag-pill-off'), 'E1 ...and the errors pill reads ON');
  ok(!note(w), 'E1 ...and there is nothing to warn about');
}

// E2-E4, E6 -- plain navigation with errors switched off
{
  const w = boot({ error: false });
  navigateToDiagnostics(w);
  const errs = rows(w, 'error');
  ok(errs.length > 0 && errs.every(function (t) { return !visible(t); }),
     'E2 plain navigation keeps the saved choice (errors stay filtered)');
  const n = note(w);
  const nErr = errs.filter(function (t) { return !t.classList.contains('diag-row-acknowledged'); }).length;
  ok(!!n && new RegExp('\\b' + nErr + ' errors? hidden by your filter').test(n.textContent),
     'E3 a visible note names the hidden errors', n ? n.textContent : '(no note)');
  const cnt = w.document.querySelector('#diag-filter-bar .diag-shown-count');
  ok(cnt && /^\d+ of \d+ shown$/.test(cnt.textContent) && !(n && cnt.contains(n)),
     'E6 the note is a sibling -- the shown-count text keeps its pinned form',
     cnt ? JSON.stringify(cnt.textContent) : '(no count)');
  const show = n && n.querySelector('button');
  ok(show && /Show errors/.test(show.textContent), 'E3 ...with a Show errors button');
  if (show) show.click();
  ok(rows(w, 'error').every(visible), 'E4 Show errors shows every error row');
  ok(!note(w), 'E4 ...and the note goes away');
  let saved = {};
  try { saved = JSON.parse(w.localStorage.getItem('quam_diag_filter') || '{}'); } catch (e) {}
  ok(saved.error === true, 'E4 ...and the choice is saved like a pill click');
}

// E5 -- every bucket off
{
  let w = boot({ error: false, warning: false, advisory: false, info: false });
  // the error rows are hidden too, so the ERROR wording wins when there are any
  navigateToDiagnostics(w);
  ok(rows(w).every(function (t) { return !visible(t); }), 'E5 preflight: everything filtered');
  const n = note(w);
  ok(!!n && /errors? hidden by your filter/.test(n.textContent),
     'E5 errors among the hidden rows: the error wording is used', n ? n.textContent : '');
  // the same page with its rows re-labelled warnings: nothing is an error,
  // nothing is shown
  const w2 = boot({ error: false, warning: false, advisory: false, info: false });
  navigateToDiagnostics(w2, DIAG.replace(/(<tr class="diag-row[^"]*" data-bucket=)"error"/g, '$1"warning"'));
  ok(rows(w2).length > 0 && rows(w2, 'error').length === 0, 'E5 preflight: a warnings-only page');
  w = w2;
  const n2 = note(w);
  ok(!!n2 && /Every finding is hidden by your filter/.test(n2.textContent),
     'E5 no errors, nothing shown: the page says every finding is hidden', n2 ? n2.textContent : '');
  const b2 = n2 && n2.querySelector('button');
  ok(b2 && /Show all/.test(b2.textContent), 'E5 ...with a Show all button');
  if (b2) b2.click();
  ok(rows(w).length > 0 && rows(w).every(visible), 'E5 Show all shows every row');
}

// E7 -- QA F-I x QA F6 (merged at integration): the crash banner hides while
// the LIVE pane holds the Diagnostics findings slot, through a class on <html>
// (never a body:has() rule, which F6 measured freezing Trends)
{
  const w = boot();
  const html = w.document.documentElement;
  ok(!html.classList.contains('diag-page-live'), 'E7 on /bulk the banner slot shows');
  navigateToDiagnostics(w);
  ok(html.classList.contains('diag-page-live'), 'E7 on /diagnostics (a swap) it hides');
  const pane = w.document.getElementById('table-pane');
  pane.innerHTML = '<p>Live State Edit</p>';
  pane.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: pane } }));
  ok(!html.classList.contains('diag-page-live'), 'E7 swapping away shows it again');
  pane.innerHTML = DIAG;                                  // a PaneState restore: no afterSwap
  w.document.dispatchEvent(new w.CustomEvent('paneRestored', { detail: { route: '/diagnostics' } }));
  ok(html.classList.contains('diag-page-live'), 'E7 a keep-alive restore of /diagnostics hides it');
  pane.innerHTML = '<p>Qubits</p>';                       // htmx history restore (Back)
  w.document.body.dispatchEvent(new w.CustomEvent('htmx:historyRestore', { bubbles: true }));
  ok(!html.classList.contains('diag-page-live'), 'E7 a history restore of another page shows it');
  // a drag-drop preview carries the findings LIST but not the page's slot
  pane.innerHTML = DIAG.replace(/id="diag-findings"/g, 'id="diag-preview-list"');
  pane.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: pane } }));
  ok(!html.classList.contains('diag-page-live'), 'E7 a pane without #diag-findings keeps the banner');
}

console.log(fails ? 'FAILED (' + fails + ')' : 'diag_filter_entry_fragcheck ok (' + asserts + ' assertions)');
process.exit(fails ? 1 : 0);
