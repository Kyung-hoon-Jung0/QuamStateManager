/* app.js under a URL prefix (docs/226, spec §4.3/§4.4 -- implementer C1).
 *
 * A reverse proxy may mount SM under a path prefix (e.g. /sm). sm-root.js
 * publishes window.SM {root, url, path} and wraps fetch / history / htmx
 * requests; app.js hand-edits every sink those wrappers cannot reach, through
 * its two helpers _smUrl (add the prefix, idempotently) and _smPath (strip it).
 * This harness runs the SHIPPED app.js in a real jsdom realm, with window.SM
 * booted from the shipped sm-root.js when it exists (else from the normative
 * spec §4.2 copy below), once at root (data-root="") and once under /sm, and
 * pins every edited category:
 *   L  location.pathname compares        -> the app route, not the prefixed path
 *   P  pathInfo / requestConfig.path     -> the app route
 *   N  location.href / assign navigations -> prefixed exactly once
 *   A  attribute sinks + selectors       -> prefixed exactly once
 *   B  Bundles.forPath                   -> a prefixed request finds its bundles
 *   I  identity: at root, and with window.SM absent, the helpers change nothing
 * Navigations are read off jsdom's own navigate() (patched below before jsdom
 * loads): jsdom does not implement document navigation, so the URL a real
 * browser would load is recorded instead of followed.
 *
 * Run: node tests/app_prefix_selfcheck.cjs   (driven by tests/test_app_prefix.py;
 * SM_ROOT_STUB=1 forces the spec copy; SM_TEST_URL_PREFIX adds one more prefix)
 */
'use strict';
const path = require('path');
const fs = require('fs');
const vm = require('vm');
let JSDOM, VirtualConsole, NAVS = [];
try {
  // Record every document navigation BEFORE jsdom's Location implementation
  // destructures navigate() out of this module.
  const NAVJS = require.resolve('jsdom/lib/jsdom/living/window/navigation.js');
  const navMod = require(NAVJS);
  const wu = require(require.resolve('whatwg-url', { paths: [path.dirname(NAVJS)] }));
  const orig = navMod.navigate;
  navMod.navigate = function (window, newURL, flags) {
    NAVS.push(wu.serializeURL(newURL));
    return orig.call(this, window, newURL, flags);
  };
  ({ JSDOM, VirtualConsole } = require('jsdom'));
} catch (e) {
  console.error('jsdom not installed: ' + e.message);
  process.exit(2);
}

const ROOT = path.join(__dirname, '..');
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
const APP_SRC = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');

/* spec §4.2, verbatim (the normative sm-root.js). Used only when the shipped
   file is absent (before C2's branch is merged) or SM_ROOT_STUB=1. */
const SPEC_SM_ROOT = `(function () {
  var doc = window.document;
  var root = '';
  try { root = doc.documentElement.getAttribute('data-root') || ''; } catch (e) {}
  if (root === '/') root = '';
  while (root.length && root.charAt(root.length - 1) === '/') root = root.slice(0, -1);
  function rooted(p) { return p === root || p.indexOf(root + '/') === 0 || p.indexOf(root + '?') === 0 || p.indexOf(root + '#') === 0; }
  function url(p) {
    if (!root || typeof p !== 'string' || p.charAt(0) !== '/' || p.charAt(1) === '/') return p;
    return rooted(p) ? p : root + p;
  }
  function path(p) {
    if (!root || typeof p !== 'string') return p;
    if (p === root) return '/';
    if (p.indexOf(root + '/') === 0) return p.slice(root.length);
    if (p.indexOf(root + '?') === 0 || p.indexOf(root + '#') === 0) return '/' + p.slice(root.length);
    return p;
  }
  window.SM = { root: root, url: url, path: path };
  if (!root) return;
  if (typeof window.fetch === 'function') {
    var _fetch = window.fetch;
    window.fetch = function (input, init) { if (typeof input === 'string') input = url(input); return _fetch.call(this, input, init); };
  }
  var H = window.history;
  ['pushState', 'replaceState'].forEach(function (k) {
    var o = H[k]; H[k] = function (state, title, u) { if (typeof u === 'string') u = url(u); return o.call(this, state, title, u); };
  });
  function defineExt() {
    var hx = window.htmx; if (!hx || !hx.defineExtension) return false;
    hx.defineExtension('sm-root', { onEvent: function (name, evt) {
      if (name === 'htmx:configRequest' && evt.detail && typeof evt.detail.path === 'string') evt.detail.path = url(evt.detail.path);
      return true; } });
    return true;
  }
  if (!defineExt()) doc.addEventListener('DOMContentLoaded', defineExt);
})();`;
const SHIPPED_SM = path.join(STATIC, 'sm-root.js');
const useShipped = fs.existsSync(SHIPPED_SM) && process.env.SM_ROOT_STUB !== '1';
const SM_SRC = useShipped ? fs.readFileSync(SHIPPED_SM, 'utf8') : SPEC_SM_ROOT;
console.log('# window.SM from ' + (useShipped ? 'the shipped sm-root.js' : 'the spec §4.2 copy (sm-root.js not in this tree)'));
const SM_SCRIPT = new vm.Script(SM_SRC, { filename: 'sm-root.js' });
const APP_SCRIPT = new vm.Script(APP_SRC, { filename: 'app.js' });

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (c) console.log('ok - ' + m); else { fails++; console.log('FAIL: ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
/* wait for a POSITIVE signal (never a bare sleep): a loaded machine delays
   timers, and a negative assertion read before the code ran passes vacuously */
async function until(fn, ms) { const t = Date.now(); while (!fn() && Date.now() - t < (ms || 8000)) await tick(10); return fn(); }
const ORIGIN = 'http://127.0.0.1:5335';

/* One page: the shipped sm-root.js then app.js, in the page's own realm. */
function env(P, pathQS, body, opts) {
  opts = opts || {};
  const vc = new VirtualConsole();
  const errs = [];
  vc.on('jsdomError', (e) => { if (e.type !== 'not-implemented') errs.push(e.message); });
  const dom = new JSDOM('<!doctype html><html data-root="' + P + '"><head></head><body>' + (body || '')
    + '</body></html>', { url: ORIGIN + P + pathQS, runScripts: 'outside-only', pretendToBeVisual: true,
    virtualConsole: vc });
  const w = dom.window;
  const fetches = [];
  w.fetch = function (u, init) {             // the INNERMOST fetch: what reaches the network
    fetches.push(String(u));
    const h = opts.fetch && opts.fetch(String(u), init);
    return h || new Promise(function () {});
  };
  w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
  const ajax = [];
  w.htmx = {
    ajax: function (verb, url, o) { ajax.push({ verb: verb, url: url, o: o || {} }); return Promise.resolve(); },
    trigger: function () {}, process: function () {}, on: function () {}, off: function () {},
    find: function () { return null; }, findAll: function () { return []; },
    closest: function () { return null; }, values: function () { return {}; }, config: {},
    defineExtension: function (n, e) { w.__smExt = e; },
  };
  const ctx = dom.getInternalVMContext();
  SM_SCRIPT.runInContext(ctx);
  APP_SCRIPT.runInContext(ctx);
  if (opts.htmx === false) w.htmx = undefined;
  const E = { w: w, doc: w.document, ajax: ajax, fetches: fetches, errs: errs,
              $: (s) => w.document.querySelector(s) };
  E.fire = function (el, name, detail) {
    const ev = new w.CustomEvent(name, { bubbles: true, cancelable: true, detail: detail || {} });
    el.dispatchEvent(ev);
    return ev;
  };
  E.close = function () {
    // an exception thrown inside any listener is a finding, never noise
    ok(errs.length === 0, 'no uncaught error on ' + P + pathQS + (errs.length ? ': ' + errs.slice(0, 3).join(' | ') : ''));
    try { w.close(); } catch (e) {}
  };
  return E;
}
const jsonRes = (obj) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(obj),
                                           text: () => Promise.resolve(JSON.stringify(obj)) });
const textRes = (s) => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve(s),
                                         json: () => Promise.resolve(JSON.parse(s)) });

const PREFIXES = [''];
PREFIXES.push('/sm');
const extra = process.env.SM_TEST_URL_PREFIX || '';
if (extra && PREFIXES.indexOf(extra) < 0) PREFIXES.push(extra);

async function each(fn) { for (const P of PREFIXES) await fn(P, P ? 'under ' + P : 'at root'); }
function navOnly(E) { return NAVS.slice(); }

(async () => {
  /* ---------------------------------------------------------------- I */
  await each(async (P, M) => {
    const E = env(P, '/qubits', '');
    ok(typeof E.w._smUrl === 'function' && typeof E.w._smPath === 'function', 'I ' + M + ': app.js defines _smUrl/_smPath');
    ok(E.w.SM && E.w.SM.root === P, 'I ' + M + ': window.SM.root === ' + JSON.stringify(P));
    ok(E.w._smUrl('/x?y=1') === P + '/x?y=1' && E.w._smUrl(P + '/x') === P + '/x',
       'I ' + M + ': _smUrl prefixes exactly once (' + E.w._smUrl(P + '/x') + ')');
    ok(E.w._smPath(P + '/x?y') === '/x?y' && E.w._smPath('/x') === '/x',
       'I ' + M + ': _smPath strips the prefix, leaves an app route alone');
    ok(E.w._smUrl('//cdn/x') === '//cdn/x' && E.w._smUrl('http://h/x') === 'http://h/x' && E.w._smUrl(undefined) === undefined,
       'I ' + M + ': protocol-relative, absolute and non-string inputs are untouched');
    const SMsave = E.w.SM;
    delete E.w.SM;
    let threw = null, a, b;
    try { a = E.w._smUrl('/sm/x'); b = E.w._smPath('/sm/x'); } catch (e) { threw = e.message; }
    ok(!threw && a === '/sm/x' && b === '/sm/x', 'I ' + M + ': with window.SM absent both helpers are the identity (' + (threw || a + ',' + b) + ')');
    E.w.SM = SMsave;
    E.close();
  });
  {
    const E = env('', '/qubits', '');
    ok(E.w._smPath('/sm/x') === '/sm/x' && E.w._smUrl('/sm/x') === '/sm/x',
       'I at root: nothing is ever stripped or added (a /sm/... route stays /sm/...)');
    E.close();
  }

  /* ---------------------------------------------------------------- L */
  await each(async (P, M) => {
    // _dsSyncFullPageUrl (app.js ~2730): the full page names the run swapped in
    let E = env(P, '/dataset/k:1', '<div id="table-pane"><div id="ds-detail-root" data-uid="k:2"><button id="b">x</button></div></div>');
    E.fire(E.$('#table-pane'), 'htmx:afterSwap', { target: E.$('#table-pane'), requestConfig: { elt: E.$('#b') } });
    ok(E.w.location.pathname === P + '/dataset/k:2', 'L ' + M + ': a run swapped into the full page renames the URL (' + E.w.location.pathname + ')');
    E.close();
    E = env(P, '/dataset/k:2', '<div id="table-pane"><div id="ds-detail-root" data-uid="k:2"><button id="b">x</button></div></div>');
    let rs = 0; const orig = E.w.history.replaceState;
    E.w.history.replaceState = function () { rs++; return orig.apply(this, arguments); };
    E.fire(E.$('#table-pane'), 'htmx:afterSwap', { target: E.$('#table-pane'), requestConfig: { elt: E.$('#b') } });
    ok(rs === 0, 'L ' + M + ': the SAME run already named by the URL rewrites nothing (' + rs + ' replaceState)');
    E.close();

    // _softRefreshLiveSurface: a state page re-fetches itself
    E = env(P, '/qubits', '<div id="table-pane"><p>t</p></div>');
    E.w._softRefreshLiveSurface();
    ok(E.ajax.length === 1 && E.ajax[0].url === P + '/qubits' && E.ajax[0].o.target === '#table-pane',
       'L ' + M + ': a state page (Qubits) re-fetches its own URL after a pull (' + JSON.stringify(E.ajax.map((a) => a.url)) + ')');
    E.close();

    // PaneState: every route key is the app route
    E = env(P, '/bulk', '<div id="table-pane"><p>grid</p></div>');
    ok(E.w.PaneState._cur() === '/bulk' && E.w.PaneState.isKeepRoute(),
       'L ' + M + ': PaneState starts on the app route /bulk, a KEEP route (' + E.w.PaneState._cur() + ')');
    E.close();

    // popstate onto the SAME route, the pane stamped with it (the JT-10 soft tier)
    const softBody = '<div id="pending-tray" data-seq="7"></div>'
      + '<div id="table-pane" data-pane-route="/qubits"><input type="search" id="s" value=""></div>';
    E = env(P, '/qubits', softBody);
    const PS = E.w.PaneState;
    PS.clear();
    PS._soft()['/qubits'] = { inputs: [{ key: '#s', value: 'abc' }] };
    PS.__softFor = 'keep-me';
    E.w.dispatchEvent(new E.w.PopStateEvent('popstate', { state: null }));
    ok(PS._cur() === '/qubits', 'L ' + M + ': after Back the route is the app route (' + PS._cur() + ')');
    ok(PS.__softFor === 'keep-me', 'L ' + M + ': a Back onto the SAME route does not reset the soft-reapply token');
    ok(PS._soft()['/qubits'] && PS._soft()['/qubits'].inputs[0].value === 'abc',
       'L ' + M + ': ...and does not re-capture (overwrite) the parked search');
    ok(await until(() => PS.__freshFor != null), 'L ' + M + ': (the Back check ran)');
    ok(!E.ajax.some((a) => a.o && a.o.source === '#table-pane'),
       'L ' + M + ': a pane stamped with the URL\'s own route is not a mismatch (no refetch; ' + JSON.stringify(E.ajax.map((a) => a.url)) + ')');
    ok(E.$('#s').value === 'abc' && PS.__softFor === '/qubits',
       'L ' + M + ': the soft tier re-applies the parked search for the route (' + E.$('#s').value + ', ' + PS.__softFor + ')');
    E.close();
    // ...and the once-per-change token really suppresses a second re-apply
    E = env(P, '/qubits', softBody);
    E.w.PaneState.clear();
    E.w.PaneState._soft()['/qubits'] = { inputs: [{ key: '#s', value: 'abc' }] };
    E.w.PaneState.__softFor = '/qubits';
    E.w.dispatchEvent(new E.w.PopStateEvent('popstate', { state: null }));
    ok(await until(() => E.w.PaneState.__freshFor != null), 'L ' + M + ': (the Back check ran)');
    ok(E.$('#s').value === '', 'L ' + M + ': a route already re-applied within the second is not re-applied again');
    E.close();

    // a pane whose stamp disagrees with the URL IS refetched, once per change
    const foreign = '<div id="pending-tray" data-seq="7"></div><div id="table-pane" data-pane-route="/bulk"><p>grid</p></div>';
    E = env(P, '/qubits', foreign);
    E.w.dispatchEvent(new E.w.PopStateEvent('popstate', { state: null }));
    await until(() => E.ajax.some((a) => a.o && a.o.source === '#table-pane'));
    await tick(30);
    const re = E.ajax.filter((a) => a.o && a.o.source === '#table-pane');
    ok(re.length === 1 && re[0].url === P + '/qubits' && E.w.PaneState.__refetchFor === '/qubits',
       'L ' + M + ': a foreign pane under the URL is refetched once, token = app route (' + JSON.stringify(re.map((a) => a.url)) + ', ' + E.w.PaneState.__refetchFor + ')');
    E.close();
    E = env(P, '/qubits', foreign);
    E.w.PaneState.__refetchFor = '/qubits';
    E.w.dispatchEvent(new E.w.PopStateEvent('popstate', { state: null }));
    ok(await until(() => E.w.PaneState.__freshFor != null), 'L ' + M + ': (the Back check ran)');
    ok(!E.ajax.some((a) => a.o && a.o.source === '#table-pane'),
       'L ' + M + ': ...and a refetch already under way for the route is not repeated');
    E.close();

    // _historyFreshness: the tray behind the server is refreshed (seq 7 -> 9)
    const freshBody = '<div id="pending-tray" data-seq="7"></div><div id="table-pane" data-pane-route="/qubits"><p>t</p></div>';
    const trayFetch = (u) => (/\/state\/tray$/.test(u) ? textRes('<div id="pending-tray" data-seq="9"></div>') : null);
    E = env(P, '/qubits', freshBody, { fetch: trayFetch });
    E.w.dispatchEvent(new E.w.PopStateEvent('popstate', { state: null }));
    let freshKey = null;   // read while it is live (the token resets itself after 1 s)
    await until(() => { freshKey = freshKey || E.w.PaneState.__freshFor; return E.ajax.some((a) => a.url === '/state/tray'); });
    ok(freshKey === '/qubits', 'L ' + M + ': the freshness probe is keyed by the app route (' + freshKey + ')');
    ok(E.fetches.filter((u) => /\/state\/tray$/.test(u)).length === 1 && E.fetches.some((u) => u === P + '/state/tray'),
       'L ' + M + ': ...probes the tray once (' + JSON.stringify(E.fetches) + ')');
    ok(E.ajax.some((a) => a.url === '/state/tray' && a.o.target === '#pending-tray'),
       'L ' + M + ': ...and a tray behind the server is refreshed (the route did not move under it)');
    E.close();
    E = env(P, '/qubits', freshBody, { fetch: trayFetch });
    E.w.PaneState.__freshFor = '/qubits';
    E.w.dispatchEvent(new E.w.PopStateEvent('popstate', { state: null }));
    ok(await until(() => E.w.PaneState.__softFor === '/qubits'), 'L ' + M + ': (the Back check ran)');
    ok(!E.fetches.some((u) => /\/state\/tray$/.test(u)), 'L ' + M + ': a probe already made for the route is not repeated');
    E.close();

    // the canonical sidebar-active sync (the /diff family is one destination)
    const nav = '<nav class="sidebar-nav"><a id="aq" href="' + P + '/qubits">Q</a><a id="ad" href="' + P + '/diff">C</a>'
      + '<a id="at" href="' + P + '/topology?view=trends">T</a></nav>';
    E = env(P, '/diff/versions', nav);
    E.w.syncSidebarNavActive();
    ok(E.$('#ad').classList.contains('active') && !E.$('#aq').classList.contains('active'),
       'L ' + M + ': /diff/versions lights Compare (' + E.doc.querySelector('.active') + ')');
    E.close();
    E = env(P, '/topology?view=trends', nav);
    E.w.syncSidebarNavActive();
    ok(E.$('#at').classList.contains('active') && !E.$('#aq').classList.contains('active'),
       'L ' + M + ': a view-scoped sidebar link matches its own view');
    E.close();

    // explorer jump: from another page it is sourced by the sidebar link; ON /explorer it is not
    const exNav = '<div id="table-pane"></div><nav class="sidebar-nav"><a id="ax" href="' + P + '/explorer" hx-push-url="true">J</a></nav>';
    const tok = (u) => (/\/chip\/active-token$/.test(u) ? jsonRes({ loaded: true }) : null);
    E = env(P, '/datasets', exNav, { fetch: tok });
    E.w._navigateToExplorerPath('qubits.q1.f_01');
    await until(() => E.ajax.length >= 1);
    ok(E.ajax.length >= 1 && E.ajax[0].url === '/explorer' && E.ajax[0].o.source === E.$('#ax'),
       'L ' + M + ': a jump from Datasets is sourced by the sidebar Json Tree link (' + JSON.stringify(E.ajax.map((a) => [a.url, a.o.source && a.o.source.id])) + ')');
    E.close();
    E = env(P, '/explorer', exNav, { fetch: tok });
    E.w._navigateToExplorerPath('qubits.q1.f_01');
    await until(() => E.ajax.length >= 1);
    ok(E.ajax.length >= 1 && E.ajax[0].url === '/explorer' && E.ajax[0].o.source === undefined,
       'L ' + M + ': a jump while ON /explorer pushes no second entry (no source; ' + JSON.stringify(E.ajax.map((a) => [a.url, a.o.source && a.o.source.id])) + ')');
    E.close();

    // _navigateTablePane: the history entry hangs off the swap of THIS url
    for (const u of ['/explorer', P + '/explorer']) {
      E = env(P, '/datasets', '<div id="table-pane"></div>');
      E.w._navigateTablePane(u);
      E.fire(E.$('#table-pane'), 'htmx:afterSwap', { target: E.$('#table-pane'), pathInfo: { finalRequestPath: P + '/explorer' } });
      ok(E.w.location.pathname === P + '/explorer',
         'L ' + M + ': _navigateTablePane(' + JSON.stringify(u) + ') pushes the entry when its swap lands (' + E.w.location.pathname + ')');
      E.close();
      E = env(P, '/explorer', '<div id="table-pane"></div>');
      const n0 = E.w.history.length;
      E.w._navigateTablePane(u);
      E.fire(E.$('#table-pane'), 'htmx:afterSwap', { target: E.$('#table-pane'), pathInfo: { finalRequestPath: P + '/explorer' } });
      ok(E.w.history.length === n0, 'L ' + M + ': _navigateTablePane(' + JSON.stringify(u) + ') on its own URL adds no entry (' + (E.w.history.length - n0) + ')');
      E.close();
    }

    // Pulses: URL sync / restore / re-open follow the app route
    const pulses = '<div id="table-pane"><div class="table-filter"><input name="q" value="abc"></div>'
      + '<div id="pulses-rows-wrap" hx-get="/pulses/rows"></div></div><div id="inspector-pane"></div>';
    E = env(P, '/pulses', pulses);
    E.w._pulsesSyncUrl();
    ok(E.w.location.pathname + E.w.location.search === P + '/pulses?q=abc',
       'L ' + M + ': the Pulses search is written to the URL (' + E.w.location.pathname + E.w.location.search + ')');
    const n1 = E.w.history.length;
    E.w._pulsesSyncUrl(true);
    ok(E.w.history.length === n1, 'L ' + M + ': a push for the URL already shown adds no entry (' + (E.w.history.length - n1) + ')');
    E.$('#inspector-pane').innerHTML = '<div id="pulse-detail-root" data-pulse-path="pulses.p1"></div>';
    E.fire(E.$('#inspector-pane'), 'htmx:afterSwap', { target: E.$('#inspector-pane') });
    ok(/[?&]pulse=pulses\.p1/.test(E.w.location.search), 'L ' + M + ': opening a pulse puts it in the URL (' + E.w.location.search + ')');
    E.$('.table-filter input').value = 'xyz';
    E.fire(E.$('.table-filter input'), 'input', {});
    ok(/[?&]q=xyz/.test(E.w.location.search), 'L ' + M + ': typing in the search keeps the URL current (' + E.w.location.search + ')');
    E.close();
    E = env(P, '/pulses?q=zz', pulses);
    E.w._pulsesRestoreFromUrl();
    ok(E.$('.table-filter input').value === 'zz', 'L ' + M + ': Back restores the search from the URL (' + E.$('.table-filter input').value + ')');
    E.close();
    E = env(P, '/qubits', pulses);
    E.w._pulsesSyncUrl();
    ok(E.w.location.pathname === P + '/qubits' && E.w.location.search === '', 'L ' + M + ': off the Pulses page the URL is left alone');
    E.close();
    E = env(P, '/pulses', '<div id="inspector-pane"><div class="toast">Deleted p9</div></div>');
    E.fire(E.doc, 'cellsReverted', { entries: [{ deleted: true, dot_path: 'pulses.p9' }] });
    ok(E.ajax.some((a) => a.url === '/pulse/detail?path=pulses.p9'),
       'L ' + M + ': undoing a pulse delete re-opens the pulse (' + JSON.stringify(E.ajax.map((a) => a.url)) + ')');
    E.close();

    // Disk guard: only the /disk page re-renders its pane
    E = env(P, '/disk', '<div id="table-pane"></div>');
    E.w.diskGuardRefresh();
    ok(E.ajax.some((a) => a.url === '/disk'), 'L ' + M + ': the /disk page re-renders after a reclaim');
    E.close();
  });
  {
    // a prefix whose LAST segment is "disk" is legal (only the first segment
    // may not collide); SM's own landing page there is not the /disk page
    const E = env('/lab/disk', '', '<div id="table-pane"></div>');
    E.w.diskGuardRefresh();
    ok(!E.ajax.some((a) => a.url === '/disk'), 'L under /lab/disk: the landing page is not mistaken for /disk (' + JSON.stringify(E.ajax.map((a) => a.url)) + ')');
    E.close();
  }

  /* ---------------------------------------------------------------- P */
  await each(async (P, M) => {
    let E = env(P, '/qubits', '<div id="table-pane"><p>x</p></div>');
    E.fire(E.$('#table-pane'), 'htmx:afterSwap', { target: E.$('#table-pane'), pathInfo: { finalRequestPath: P + '/bulk?x=1' },
                                                  requestConfig: { verb: 'get' } });
    ok(E.$('#table-pane').getAttribute('data-pane-route') === '/bulk' && E.w.PaneState._cur() === '/bulk',
       'P ' + M + ': a swap stamps the pane with the app route (' + E.$('#table-pane').getAttribute('data-pane-route') + ')');
    E.close();

    E = env(P, '/pulses', '<div id="inspector-pane"><div id="pulse-detail-root" data-pulse-path="pulses.b"></div></div>');
    E.w._pulseNavWanted = 'pulses.a';
    E.fire(E.$('#inspector-pane'), 'htmx:afterSwap', { target: E.$('#inspector-pane'), pathInfo: { requestPath: P + '/pulse/edit?x=1' } });
    ok(E.ajax.some((a) => a.url === '/pulse/detail?path=pulses.a'),
       'P ' + M + ': the blur-commit\'s own late response re-opens the pulse the user clicked (' + JSON.stringify(E.ajax.map((a) => a.url)) + ')');
    E.close();
    E = env(P, '/pulses', '<div id="inspector-pane"><div id="pulse-detail-root" data-pulse-path="pulses.a"></div></div>');
    E.w._pulseNavWanted = 'pulses.a';
    E.fire(E.$('#inspector-pane'), 'htmx:afterSwap', { target: E.$('#inspector-pane'), pathInfo: { requestPath: P + '/pulse/edit?x=1' } });
    ok(E.w._pulseNavWanted === null && E.ajax.length === 0,
       'P ' + M + ': a commit response is recognised as one (by its request path) and ends the intent (' + E.w._pulseNavWanted + ')');
    E.close();

    E = env(P, '/qubits', '<div id="pending-tray" data-seq="1"></div>');
    E.fire(E.doc, 'htmx:beforeRequest', { requestConfig: { verb: 'post', path: P + '/undo' }, xhr: {} });
    E.w.UndoQueue.push('/undo');
    ok(E.ajax.some((a) => a.verb === 'POST' && a.url === '/undo'),
       'P ' + M + ': an undo in flight is not a write that holds the next press (' + JSON.stringify(E.ajax.map((a) => a.verb + ' ' + a.url)) + ')');
    E.close();
    E = env(P, '/qubits', '<div id="pending-tray" data-seq="1"></div>');
    E.fire(E.doc, 'htmx:beforeRequest', { requestConfig: { verb: 'post', path: P + '/field/edit' }, xhr: {} });
    E.w.UndoQueue.push('/undo');
    ok(!E.ajax.some((a) => a.url === '/undo'), 'P ' + M + ': ...while a real write in flight does hold it (control)');
    E.close();

    E = env(P, '/qubits', '<div id="status-bar"></div>');
    let ev = E.fire(E.$('#status-bar'), 'htmx:beforeSwap', { target: E.$('#status-bar'), xhr: { status: 404, responseText: 'gone' },
      requestConfig: { path: P + '/state-history/20260901T000000Z/revert' }, shouldSwap: false });
    ok(ev.detail.shouldSwap === true && ev.detail.isError === false, 'P ' + M + ': a State-History 4xx reason reaches the status bar');
    E.close();
    E = env(P, '/qubits', '<div id="chip-name-banner"></div>');
    ev = E.fire(E.$('#chip-name-banner'), 'htmx:beforeSwap', { target: E.$('#chip-name-banner'), xhr: { status: 409, responseText: '' },
      requestConfig: { path: P + '/chip-data-folder/set' }, shouldSwap: false });
    ok(ev.detail.shouldSwap === true, 'P ' + M + ': the data-folder 409 confirm renders in its banner');
    E.close();
    E = env(P, '/qubits', '<div id="pending-tray" data-seq="1"></div>');
    E.w._keepMineInFlight = {};
    ev = E.fire(E.$('#pending-tray'), 'htmx:beforeSwap', { target: E.$('#pending-tray'),
      xhr: { status: 409, responseText: JSON.stringify({ status: 'unseen_changes', paths: [] }) },
      requestConfig: { path: P + '/state/apply-to-live?force=1' }, shouldSwap: true });
    ok(E.w._keepMineInFlight && E.w._keepMineInFlight._unseenRefused === true && ev.detail.shouldSwap === false,
       'P ' + M + ': Keep mine\'s unseen-edit refusal is recognised');
    E.close();

    E = env(P, '/qubits', '<div id="quam-loader"></div>');
    E.fire(E.doc, 'htmx:beforeRequest', { requestConfig: { verb: 'get', path: P + '/bulk' } });
    await until(() => E.$('#quam-loader').classList.contains('visible'));
    ok(E.$('#quam-loader').classList.contains('visible'), 'P ' + M + ': a slow page (/bulk) shows the loader');
    E.close();
  });

  /* ---------------------------------------------------------------- B */
  await each(async (P, M) => {
    const E = env(P, '/qubits', '');
    const a = E.w.Bundles.forPath(P + '/bulk?x=1'), b = E.w.Bundles.forPath(ORIGIN + P + '/pulses?q=1');
    ok(a.join() === 'grid' && b.join() === 'pulses', 'B ' + M + ': a request path finds its lazy bundles (' + a + ' / ' + b + ')');
    E.close();
  });

  /* ---------------------------------------------------------------- N */
  await each(async (P, M) => {
    const X = ORIGIN + P;
    const nav = async (label, body, fn, want, opts) => {
      const E = env(P, (opts && opts.at) || '/qubits', body, Object.assign({ htmx: false }, opts || {}));
      NAVS = [];
      await fn(E);
      await until(() => NAVS.length >= 1);
      await tick(20);
      ok(NAVS.length === 1 && NAVS[0] === want, 'N ' + M + ': ' + label + ' navigates to ' + want + ' (' + JSON.stringify(NAVS) + ')');
      E.close();
      return E;
    };
    await nav('chipNavView (no htmx)', '', (E) => E.w.chipNavView('trends'), X + '/topology?view=trends');
    await nav('dsCloseRun with no sidebar link', '<div id="table-pane"><button id="b">x</button></div>',
      (E) => E.w.dsCloseRun(E.$('#b')), X + '/datasets');
    await nav('dsOpenFullPage', '<div data-uid="k:5"><button id="b">x</button></div>',
      (E) => E.w.dsOpenFullPage(E.$('#b')), X + '/dataset/k:5');
    await nav('Compare selected snapshots (no htmx)',
      '<input type="checkbox" class="history-compare-cb" value="a" checked><input type="checkbox" class="history-compare-cb" value="b" checked>',
      (E) => E.w.compareSelectedSnapshots(), X + '/diff/snapshots?ts_a=a&ts_b=b');
    await nav('Diff selected runs (no htmx)', '',
      (E) => { E.w.DatasetVirtual = { getSelectedIds: () => ['k:1', 'k:2'] }; E.w.diffSelectedDatasets(); },
      X + '/diff/runs?uids=k%3A1,k%3A2');
    await nav('Versions panel Compare of two (no htmx)',
      '<div id="state-version-panel"><input type="checkbox" class="sv-check" value="t1" checked><input type="checkbox" class="sv-check" value="t2" checked></div>',
      (E) => E.w.StateVersions.compare('chipK'), X + '/diff/snapshots?ts_a=t1&ts_b=t2&chip_key=chipK');
    await nav('Versions panel Compare of three (no htmx)',
      '<div id="state-version-panel"><input type="checkbox" class="sv-check" value="t1" checked><input type="checkbox" class="sv-check" value="t2" checked><input type="checkbox" class="sv-check" value="t3" checked></div>',
      (E) => E.w.StateVersions.compare(''), X + '/diff/versions?ts=t1&ts=t2&ts=t3');
    await nav('UndoNav to the grid (no htmx)', '<div id="table-pane"></div>',
      (E) => E.w.UndoNav.handle([{ dot_path: 'qubits.q1.a' }, { dot_path: 'qubits.q2.b' }]), X + '/bulk');
    await nav('the Param History drawer chart click (no htmx)',
      '<div id="param-history-drawer"><div id="phd-chart"></div></div>',
      async (E) => {
        E.w.Plotly = { newPlot: function (id) {
          const el = E.w.document.getElementById(id); el.__h = {};
          el.on = function (ev, fn) { el.__h[ev] = fn; }; return Promise.resolve(el); } };
        E.w.paramHistoryRenderDrawerChart({ property: 'f', values: [{ value: 1, ts: '20260901T000000Z', trigger: 'save' }] }, 1);
        const ch = E.w.document.getElementById('phd-chart');
        await until(() => ch.__h && ch.__h.plotly_click);
        E.w.document.getElementById('phd-chart').__h.plotly_click({ points: [{ customdata: [0, 0, 0, 0, 'kk:9'] }] });
      }, X + '/dataset/kk:9');
    // the command palette's page pick (a URL the template rooted) goes through
    // _navigateTablePane: the history entry lands on the swap, prefixed once
    for (const url of [P + '/explorer', '/explorer']) {
      const E = env(P, '/datasets', '<div id="table-pane"></div><div id="cmd-palette" hidden><input id="cmd-palette-input">'
        + '<ul id="cmd-palette-results"></ul></div><script type="application/json" id="cmd-palette-data">'
        + JSON.stringify({ pages: [{ label: 'Zeta page', url: url }], qubits: [], pairs: [] }) + '</script>');
      E.w.openCmdPalette();
      const i = E.$('#cmd-palette-input'); i.value = 'Zeta';
      i.dispatchEvent(new E.w.Event('input', { bubbles: true }));
      E.doc.dispatchEvent(new E.w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      E.fire(E.$('#table-pane'), 'htmx:afterSwap', { target: E.$('#table-pane'), pathInfo: { finalRequestPath: P + '/explorer' } });
      ok(E.ajax.length === 1 && E.ajax[0].url === url && E.w.location.pathname === P + '/explorer',
         'N ' + M + ': a palette page pick ' + JSON.stringify(url) + ' swaps the pane and names it in the URL (' + E.w.location.pathname + ')');
      E.close();
    }
    // dsCloseRun WITH the sidebar link: it presses the link (its hx-get + push-url), no navigation
    {
      const E = env(P, '/datasets', '<nav class="sidebar-nav"><a id="al" href="' + P + '/datasets">D</a></nav><div id="table-pane"><button id="b">x</button></div>', { htmx: false });
      let clicked = 0;
      E.$('#al').addEventListener('click', (ev) => { clicked++; ev.preventDefault(); });
      NAVS = [];
      E.w.dsCloseRun(E.$('#b'));
      await tick(10);
      ok(clicked === 1 && NAVS.length === 0, 'N ' + M + ': dsCloseRun presses the sidebar Datasets link when it is there (' + clicked + ', ' + JSON.stringify(NAVS) + ')');
      E.close();
    }
  });

  /* ---------------------------------------------------------------- A */
  await each(async (P, M) => {
    let E = env(P, '/qubits', '');
    E.w.requirePlotly();
    const s = E.doc.querySelector('script[src]');
    ok(s && s.getAttribute('src') === P + '/static/plotly.min.js', 'A ' + M + ': the Plotly fallback src is ' + (s && s.getAttribute('src')));
    E.close();

    E = env(P, '/datasets', '<button id="rs" hx-post="' + P + '/datasets/rescan">R</button>');
    let pressed = 0; E.$('#rs').addEventListener('click', () => { pressed++; });
    const did = E.w.refreshRunLists();
    ok(did.datasets === true && pressed === 1, 'A ' + M + ': refreshRunLists finds and presses the Datasets Rescan button');
    let cleared = 0;
    E.w.SyncBadge = { clear: (k) => { if (k === 'new') cleared++; }, onAck: () => {} };
    E.$('#rs').click();
    ok(cleared === 1, 'A ' + M + ': pressing Rescan yourself acknowledges the new-run count (' + cleared + ')');
    E.close();

    E = env(P, '/qubits', '<div id="sidebar"><a id="ae" href="' + P + '/explorer">J</a><a id="ai" href="' + P + '/instrument">W</a></div>',
      { fetch: (u) => (/\/diagnostics\/findings\.json$/.test(u) ? jsonRes({ value_spec: [{ severity: 'error' }], connectivity: [{ severity: 'warning' }] }) : null) });
    E.w._refreshSidebarDiagDots();
    await until(() => E.$('#ae').classList.contains('nav-diag-dot'));
    ok(E.$('#ae').classList.contains('nav-diag-dot') && E.$('#ai').classList.contains('nav-diag-dot-warn'),
       'A ' + M + ': the sidebar diagnostics dots land on the prefixed links');
    E.close();
  });

  /* ------------------------------------- the Trends view (its own harness) */
  const TH = require('./trends_view_harness.cjs');
  await each(async (P, M) => {
    const W = TH.world();
    W.w.document.documentElement.setAttribute('data-root', P);
    SM_SCRIPT.runInContext(W.dom.getInternalVMContext());
    const n = 3;
    const p = TH.payload(n, [{ q: 'q1', m: 'm', v: [1, 2, 3] }]);
    p.fig_keys = ['figure']; p.fig_runs = [[0, 1, 2]];
    W.answers.push({ body: p });
    const root = W.mount();
    await TH.until(() => W.draws.length >= 1);
    const det = root.querySelector('[data-role="figtl"]');
    det.open = true; det.dispatchEvent(new W.w.Event('toggle'));
    const sec = root.querySelector('.trend-figure-strip-section');
    sec.open = true; sec.dispatchEvent(new W.w.Event('toggle'));
    const img = sec.querySelector('img');
    ok(img && img.getAttribute('src') === P + '/dataset/kk:1002/fig/figure', 'A ' + M + ': a Trends figure-strip image is ' + (img && img.getAttribute('src')));
    const el = W.draws[0].el;
    W.w.htmx = null;
    NAVS = [];
    el.__handlers.plotly_click[0]({ points: [{ customdata: 'kk:1001' }] });
    await tick(10);
    ok(NAVS.length === 1 && NAVS[0] === 'http://localhost' + P + '/dataset/kk:1001',
       'N ' + M + ': a Trends point click with no htmx navigates to its run (' + JSON.stringify(NAVS) + ')');
    try { W.w.close(); } catch (e) {}
  });

  console.log((fails ? 'FAIL' : 'ok') + ' app_prefix_selfcheck (' + asserts + ' assertions, ' + fails + ' failed)');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.log('FAIL: harness threw ' + (e && e.stack || e)); process.exit(1); });
