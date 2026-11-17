/* jsdom selfcheck under the REAL bundled htmx: every open lazy date group gets its runs.
 *
 *   Q  three lazy groups open before a sidebar tree refresh are restored open, their asks
 *      overlap (no answer until all are made) -> each group sends its own request and all three
 *      receive their rows. Without a source htmx queues on document.body with strategy "last"
 *      and drops every queued ask but the last: the middle group stayed "loading..." forever.
 *   F  a person's open whose request fails -> closing and re-opening the group asks again and
 *      fills it (the `lazy-open once` trigger cannot fire twice, so the re-ask is htmx.ajax).
 *   R  the same for a restore-path ask that fails.
 *   T  two quick toggles while the first request is in flight send one request.
 *   K  a group that was filled is not asked again by the next refresh; a closed one is not asked.
 *
 * Run: node tests/sidebar_lazy_queue_selfcheck.cjs   (driven by tests/test_sidebar_lazy_group.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const HTMX = fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8');
const APP_JS = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const SEARCH_JS = fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8');

let passes = 0, fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }

const XPATH_SHIM =
  'window.XPathEvaluator = function () {};' +
  'window.XPathEvaluator.prototype.createExpression = function () {' +
  '  return { evaluate: function () { return { iterateNext: function () { return null; } }; } };' +
  '};';

// every xhr waits until the test answers it; fetch never answers (the app's own polls)
const MOCK = `(function () {
  window.__reqs = [];
  function Target() { this._l = {}; }
  Target.prototype.addEventListener = function (t, f) { (this._l[t] = this._l[t] || []).push(f); };
  Target.prototype._fire = function (t) { (this._l[t] || []).forEach(function (f) { f({type: t}); }); };
  function X() { Target.call(this); this.upload = new Target(); this._h = {}; this.status = 0; this.readyState = 0; }
  X.prototype = Object.create(Target.prototype);
  X.prototype.open = function (m, u) { this.method = m; this.url = u; };
  X.prototype.overrideMimeType = function () {};
  X.prototype.setRequestHeader = function () {};
  X.prototype.send = function (b) { this.body = b; window.__reqs.push(this); };
  X.prototype.abort = function () {
    if (this.readyState === 4) return; if (this.onabort) this.onabort(); this._fire('loadend'); };
  X.prototype.getAllResponseHeaders = function () { return ''; };
  X.prototype.getResponseHeader = function () { return null; };
  X.prototype.respond = function (body, status) {
    this.status = status || 200; this.readyState = 4;
    this.response = this.responseText = body; this.responseURL = 'http://localhost' + this.url;
    this.onload(); this._fire('load'); this._fire('loadend');
  };
  X.prototype.fail = function () { this.readyState = 4; this.onerror(); this._fire('loadend'); };
  window.XMLHttpRequest = X;
  window.fetch = function () { return new Promise(function () {}); };
  window.IntersectionObserver = function () { this.observe = function () {}; this.disconnect = function () {}; this.unobserve = function () {}; };
  window.ResizeObserver = window.IntersectionObserver;
})();`;

const DATES = ['2026-08-17', '2026-08-18', '2026-08-19'];

function groupHtml(tpath, open, filled) {
  return '<details class="tree-dir" data-tpath="' + tpath + '" data-lazy-group="1"'
    + ' hx-get="/workspace/tree/group?capped=1"'
    + ' hx-vals=\'{"root": "D:\\\\archive", "tpath": "' + tpath + '"}\''
    + ' hx-trigger="lazy-open once" hx-target="find ul.tree-entries" hx-swap="innerHTML"'
    + (open ? ' open' : '') + '><summary>' + tpath + '</summary>'
    + '<ul class="tree-entries" data-lazy="1">'
    + (filled ? '<li class="tree-entry"><label class="tree-entry-label">run of ' + tpath + '</label></li>'
              : '<li class="tree-entry muted tree-lazy-hint"><small>loading...</small></li>')
    + '</ul></details>';
}
function treeHtml(states) {   // states: {tpath: {open, filled}}
  return '<details class="tree-root" open><summary class="tree-root-label"><span title="D:\\archive">archive</span></summary>'
    + DATES.map((t) => groupHtml(t, !!(states[t] && states[t].open), !!(states[t] && states[t].filled))).join('')
    + '</details>';
}

function makeWorld() {
  const dom = new JSDOM('<!doctype html><html><head></head><body>'
    + '<div id="sidebar"><div id="sidebar-tree"></div><textarea id="sidebar-filter-input"></textarea></div>'
    + '<div id="table-pane"></div></body></html>',
    { runScripts: 'outside-only', url: 'http://localhost/', pretendToBeVisual: true });
  const w = dom.window;
  w.eval(XPATH_SHIM);
  w.eval(MOCK);
  w.eval(HTMX);
  w.eval('htmx.config.defaultSettleDelay = 0;');
  w.eval(SEARCH_JS);
  w.eval(APP_JS);
  return w;
}

const tick = (w, ms) => new Promise((r) => w.setTimeout(r, ms || 0));
async function drain(w) { for (let i = 0; i < 10; i++) await tick(w, 0); await tick(w, 20); for (let i = 0; i < 10; i++) await tick(w, 0); }

const groupReqs = (w) => w.eval('__reqs').filter((r) => String(r.url).indexOf('/workspace/tree/group') === 0);
const openReqs = (w) => groupReqs(w).filter((r) => !r.__done);
const tpathOf = (r) => { const m = /tpath=([^&]+)/.exec(decodeURIComponent(String(r.url))); return m ? m[1] : '?'; };
const grp = (w, t) => w.document.querySelector('details[data-lazy-group][data-tpath="' + t + '"]');
const filled = (w, t) => { const g = grp(w, t); return !!g && !g.querySelector('.tree-lazy-hint')
  && g.textContent.indexOf('run of ' + t) >= 0; };

async function answer(w, r, status) {
  r.__done = true;
  if (status === 'fail') r.fail();
  else r.respond(status && status !== 200 ? 'boom'
    : '<li class="tree-entry"><label class="tree-entry-label">run of ' + tpathOf(r) + '</label></li>', status || 200);
  await drain(w);
}

// one sidebar tree refresh: capture (beforeSwap), rebuild every group CLOSED with its
// placeholder (what the server's tree markup is), restore (afterSwap)
async function refresh(w, before, after) {
  const d = w.document;
  const tree = d.getElementById('sidebar-tree');
  tree.innerHTML = treeHtml(before);
  w.eval('htmx.process(document.getElementById("sidebar-tree"))');
  await drain(w);
  d.dispatchEvent(new w.CustomEvent('htmx:beforeSwap', { detail: { target: tree } }));
  tree.innerHTML = treeHtml(after || {});
  w.eval('htmx.process(document.getElementById("sidebar-tree"))');
  d.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { detail: { target: tree } }));
  await drain(w);
}

(async () => {
  // Q: three restored groups with overlapping asks
  {
    const w = makeWorld();
    const all = {}; DATES.forEach((t) => { all[t] = { open: true, filled: true }; });
    await refresh(w, all);
    ok(DATES.every((t) => grp(w, t).open), 'Q: the restore re-opens all three groups');
    const sent = openReqs(w).map(tpathOf).sort();
    ok(sent.length === 3 && sent.join(',') === DATES.join(','),
       'Q: each open group sends its own request before any answer (sent: ' + sent.join(',') + ')');
    for (let i = 0; i < 6 && openReqs(w).length; i++) await answer(w, openReqs(w)[0]);
    ok(DATES.every((t) => filled(w, t)),
       'Q: all three groups receive their rows (' + DATES.map((t) => t + '=' + filled(w, t)).join(' ') + ')');
    ok(groupReqs(w).length === 3, 'Q: three requests in all (' + groupReqs(w).length + ')');

    // K: the next refresh brings them back filled -> no request storm
    const n0 = groupReqs(w).length;
    const kept = {}; DATES.forEach((t) => { kept[t] = { open: true, filled: true }; });
    const d = w.document, tree = d.getElementById('sidebar-tree');
    d.dispatchEvent(new w.CustomEvent('htmx:beforeSwap', { detail: { target: tree } }));
    tree.innerHTML = treeHtml(kept);
    w.eval('htmx.process(document.getElementById("sidebar-tree"))');
    d.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { detail: { target: tree } }));
    await drain(w);
    ok(groupReqs(w).length === n0, 'K: groups that came back with their rows are not re-fetched');
  }

  // K: a closed group is not fetched by the restore
  {
    const w = makeWorld();
    await refresh(w, { '2026-08-17': { open: true, filled: true }, '2026-08-18': { open: false, filled: true } });
    const sent = openReqs(w).map(tpathOf);
    ok(sent.length === 1 && sent[0] === '2026-08-17' && !grp(w, '2026-08-18').open,
       'K: only the group that was open is asked (sent: ' + sent.join(',') + ')');
  }

  // F: a person's open whose request fails, then close + re-open
  for (const how of [500, 'fail']) {
    const w = makeWorld();
    const tree = w.document.getElementById('sidebar-tree');
    tree.innerHTML = treeHtml({});
    w.eval('htmx.process(document.getElementById("sidebar-tree"))');
    const g = grp(w, '2026-08-18');
    g.open = true;
    await drain(w);
    ok(openReqs(w).length === 1, 'F(' + how + '): the open asks once');
    await answer(w, openReqs(w)[0], how);
    ok(!filled(w, '2026-08-18'), 'F(' + how + '): the failed answer leaves the placeholder');
    g.open = false; await drain(w); g.open = true; await drain(w);
    ok(openReqs(w).length === 1, 'F(' + how + '): closing and re-opening asks again (' + openReqs(w).length + ' open request(s))');
    if (openReqs(w).length) await answer(w, openReqs(w)[0]);
    ok(filled(w, '2026-08-18'), 'F(' + how + '): and the group fills');
    g.open = false; await drain(w); g.open = true; await drain(w);
    ok(openReqs(w).length === 0 && groupReqs(w).length === 2, 'F(' + how + '): a filled group is not asked again');
  }

  // R: a restore-path ask that fails, then the person re-opens it
  {
    const w = makeWorld();
    await refresh(w, { '2026-08-19': { open: true, filled: true } });
    ok(openReqs(w).length === 1, 'R: the restore asks the open group');
    await answer(w, openReqs(w)[0], 500);
    const g = grp(w, '2026-08-19');
    g.open = false; await drain(w); g.open = true; await drain(w);
    ok(openReqs(w).length === 1, 'R: after the failed restore ask, re-opening asks again');
    if (openReqs(w).length) await answer(w, openReqs(w)[0]);
    ok(filled(w, '2026-08-19'), 'R: and the group fills');
  }

  // T: two quick toggles while the first request is in flight
  {
    const w = makeWorld();
    const tree = w.document.getElementById('sidebar-tree');
    tree.innerHTML = treeHtml({});
    w.eval('htmx.process(document.getElementById("sidebar-tree"))');
    const g = grp(w, '2026-08-17');
    g.open = true; await drain(w);
    g.open = false; await drain(w);
    g.open = true; await drain(w);
    ok(groupReqs(w).length === 1, 'T: two opens while one request is in flight send one (' + groupReqs(w).length + ')');
    await answer(w, openReqs(w)[0]);
    ok(filled(w, '2026-08-17'), 'T: and that one answer fills the group');
  }

  console.log(fails ? ('FAILED: ' + fails) : ('ALL OK (' + passes + ' assertions)'));
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e && e.stack || e); process.exit(1); });
