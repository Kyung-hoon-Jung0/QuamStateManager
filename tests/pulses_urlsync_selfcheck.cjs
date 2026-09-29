/* jsdom selfcheck: two Pulses-page layout/URL defects (w7/adaptive verifier).
 *
 * 1. per_page survives a URL sync. The page-size <select> in _pagination.html
 *    has no name attribute, so _pulsesSyncUrl's select[name='per_page'] never
 *    matched: choose "All", open a pulse, reload -> back to 50 rows.
 * 2. The late crash banner never pushes the page down. An unreserved banner
 *    becomes an overlay; a reserved one (placeholder from base.html) stays in
 *    the flow; a dismissed or empty response clears the remembered size.
 *
 * Run: node tests/pulses_urlsync_selfcheck.cjs (driven by tests/test_pulses_url_banner.py)
 */
require('./_sm_root_boot.cjs').install();
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<!doctype html><html><head></head><body></body></html>', {
    url: 'http://localhost/pulses?per_page=0', pretendToBeVisual: true,
});
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator;
global.location = window.location;
global.history = window.history;
const store = {};
const ss = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
};
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = ss;
window.localStorage = global.localStorage;
Object.defineProperty(window, 'sessionStorage', { value: ss, configurable: true });
global.fetch = () => new Promise(() => {});
window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

const src = fs.readFileSync(
    path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
try {
    window.eval(src);
} catch (e) {
    console.error('FAIL: app.js did not evaluate under jsdom: ' + e.message);
    process.exit(1);
}

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const doc = window.document;

// ---- 1. per_page in the URL -------------------------------------------
// the real partial's picker markup (no name attribute)
const pag = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web',
    'templates', '_pagination.html'), 'utf8');
ok(!/<select[^>]*name=/.test(pag), 'premise: the page-size select still has no name attribute');

function pulsesPage(sel) {
    doc.body.innerHTML =
        '<div id="table-pane">' +
        ' <div class="table-filter"><input name="q" value=""></div>' +
        ' <div id="pulses-rows-wrap" hx-get="/pulses?rows=1&channel=&per_page=0">' +
        '  <table><tbody><tr><td>x180</td></tr></tbody></table>' +
        '  <nav class="pagination"><ul>' +
        '   <li><span class="page-info" data-current-page="1">1 / 1</span></li>' +
        '   <li class="page-size-picker"><label>Show <select onchange="">' +
        ['25', '50', '100', '0'].map((v) => '<option value="' + v + '"' +
            (sel === v ? ' selected' : '') + '>' + (v === '0' ? 'All' : v) + '</option>').join('') +
        '   </select></label></li></ul></nav>' +
        ' </div>' +
        '</div>' +
        '<div id="inspector-pane"><div id="pulse-detail-root" data-pulse-path="qubits.q1.xy.operations.x180"></div></div>';
}
pulsesPage('0');
window._pulsesSyncUrl();
ok(/[?&]per_page=0(&|$)/.test(location.search), 'All + an open pulse keeps per_page=0 in the URL (got ' + location.search + ')');
ok(/pulse=qubits\.q1\.xy\.operations\.x180/.test(location.search), 'the open pulse is still in the URL');

pulsesPage('50');
window.history.replaceState({}, '', '/pulses?per_page=0');
window._pulsesSyncUrl();
ok(!/per_page/.test(location.search), 'the default 50 is left out of the URL');

// no picker rendered -> the rows wrap's own per_page decides
pulsesPage('0');
doc.querySelector('.page-size-picker').remove();
window._pulsesSyncUrl();
ok(/[?&]per_page=0(&|$)/.test(location.search), 'with no picker the rows wrap per_page is kept');

// ---- 2. the late crash banner -----------------------------------------
const BANNER = (sig) => '<div class="topo-change-banner diag-error-banner" id="diagnostics-banner" ' +
    'data-diag-sig="' + sig + '">1 error</div>';
function swapSlot(html, reserved) {
    doc.body.innerHTML = '<div id="diagnostics-banner-slot"></div><div id="table-pane"></div>';
    const slot = doc.getElementById('diagnostics-banner-slot');
    if (reserved) slot.setAttribute('data-reserved', '1');
    slot.innerHTML = html;
    slot.dispatchEvent(new window.CustomEvent('htmx:afterSwap', {
        bubbles: true, detail: { target: slot },
    }));
    return slot;
}
delete store.quam_diag_banner_h;
let slot = swapSlot(BANNER('1:c:a'), false);
ok(slot.classList.contains('diag-banner-overlay'), 'an unreserved late banner overlays (does not push the page)');
ok(slot.getAttribute('data-mode') === 'overlay', 'mode recorded as overlay');

slot = swapSlot(BANNER('1:c:a'), true);
ok(!slot.classList.contains('diag-banner-overlay') && slot.getAttribute('data-mode') === 'flow',
    'a banner the slot reserved space for renders in the flow');
ok(slot.getAttribute('data-reserved') === null, 'the reservation marker is consumed');

store.quam_diag_banner_h = '76';
slot = swapSlot('', true);
ok(!slot.classList.contains('diag-banner-overlay'), 'an empty response leaves no overlay');
ok(!('quam_diag_banner_h' in store), 'an empty response forgets the remembered size');

store.quam_diag_banner_h = '76';
store.quam_diag_banner_dismissed = '1:c:a';
slot = swapSlot(BANNER('1:c:a'), false);
ok(!slot.classList.contains('diag-banner-overlay') && !('quam_diag_banner_h' in store),
    'a dismissed banner neither overlays nor reserves');
delete store.quam_diag_banner_dismissed;

// the stylesheet must actually take the overlay out of the flow
const css = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web',
    'static', 'style.css'), 'utf8');
ok(/#diagnostics-banner-slot\.diag-banner-overlay\s*\{[^}]*height:\s*0/.test(css),
    'the overlay slot takes no layout height');
ok(/#diagnostics-banner-slot\.diag-banner-overlay > \.diag-error-banner\s*\{[^}]*position:\s*fixed/.test(css),
    'the overlaid banner is out of the flow (fixed)');

// docking: the overlay moves into the flow only when nothing can shift under the pointer
function overlaySlot() {
    doc.body.innerHTML = '<div class="shell-head"><header class="topbar"><a id="tb">x</a></header>' +
        '<div id="diagnostics-banner-slot"></div></div><div id="table-pane"><a id="row">r</a></div>';
    const s = doc.getElementById('diagnostics-banner-slot');
    s.innerHTML = BANNER('2:c:b');
    s.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: s } }));
    return s;
}
slot = overlaySlot();
doc.getElementById('row').dispatchEvent(new window.Event('pointerover', { bubbles: true }));
ok(slot.getAttribute('data-mode') === 'overlay', 'pointer over the page content does NOT dock (it would shift under it)');
const pane = doc.getElementById('table-pane');
pane.dispatchEvent(new window.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: pane } }));
ok(slot.getAttribute('data-mode') === 'flow' && !slot.classList.contains('diag-banner-overlay'),
    'a main-pane swap docks the overlay into the flow');
slot = overlaySlot();
doc.getElementById('tb').dispatchEvent(new window.Event('pointerover', { bubbles: true }));
ok(slot.getAttribute('data-mode') === 'flow', 'pointer over the head block docks it');
const base = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web',
    'templates', 'base.html'), 'utf8');
ok(base.indexOf("sessionStorage.getItem('quam_diag_banner_h')") > base.indexOf('id="diagnostics-banner-slot"'),
    'base.html reserves the remembered space right after the slot');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('all ok');
