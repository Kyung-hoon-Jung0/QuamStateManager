/* QA r2-06 — the full-page run view (/dataset/<uid>, or ⛶) holds the run in
 * #table-pane, and every control of the detail used to assume the inspector:
 * × emptied the (empty) inspector, ↓ and the parent link stacked the next run
 * UNDER the unchanged full page (two #ds-detail-root, the URL still naming the
 * first), and "Go to state" / "Edit qN" replaced the run with the Explorer
 * with nothing to close and no way back.
 *
 * The REAL app.js + the REAL htmx.min.js under jsdom, on the detail markup the
 * REAL template rendered (the pytest driver passes it as argv[2]); every
 * request htmx would send is recorded at htmx:configRequest (target resolved
 * by htmx itself) and answered by a synthetic swap:
 *   × on a full page -> back to the Datasets list (the sidebar link);
 *   × in the inspector -> closes the inspector, as before;
 *   ↓ on a full page -> the next run REPLACES it in #table-pane, one detail,
 *     and the URL names the run on screen;
 *   the parent link resolves to the pane that holds the run;
 *   _navigateToExplorerPath from a full page moves the run into the inspector
 *     BEFORE the Explorer takes #table-pane.
 *
 * Run: node tests/ds_fullpage_fragcheck.cjs <rendered-detail.html>
 * (driven by tests/test_ds_fullpage.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const DETAIL = fs.readFileSync(process.argv[2], 'utf8');
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(r => setTimeout(r, ms || 20)); }

const UID = (DETAIL.match(/id="ds-detail-root"[\s\S]*?data-uid="([^"]+)"/) || [])[1];
if (!UID) { console.error('HARNESS ERROR: no data-uid in the rendered detail'); process.exit(1); }
const KEY = UID.split(':')[0];
const RUN = +UID.split(':')[1];
function detailFor(uid) {   // the same rendered markup, as the server would render another run
  return DETAIL.split(UID).join(uid).split('#' + RUN).join('#' + uid.split(':')[1]);
}
function entry(n) {
  return '<li><span class="tree-entry-click" data-uid="' + KEY + ':' + n + '" data-run-id="' + n
       + '" tabindex="0">#' + n + '</span></li>';
}

// a real page navigation (location.assign) is what jsdom reports as not implemented
const navigations = [];
const vconsole = new VirtualConsole();
vconsole.on('jsdomError', function (e) {
  if (/navigation/i.test(e.message)) navigations.push(e.message); else console.error(e.message);
});
// runScripts 'dangerously': the template's inline onclick="..." handlers are
// what a press runs, so they must run here too
const dom = new JSDOM('<!doctype html><html><head></head><body>'
  + '<nav class="sidebar-nav"><ul><li><a href="/datasets" id="nav-ds">Datasets</a></li></ul></nav>'
  + '<div id="sidebar-tree"><ul class="tree-entries">' + entry(RUN + 1) + entry(RUN) + entry(RUN - 1) + '</ul></div>'
  + '<div id="table-pane"></div><div id="inspector-pane"></div></body></html>',
  { url: 'http://localhost/dataset/' + UID, runScripts: 'dangerously', pretendToBeVisual: true,
    virtualConsole: vconsole });
const w = dom.window;
const doc = w.document;
Object.defineProperty(w.HTMLElement.prototype, 'offsetParent', {
  get() { return (this.hidden || (this.closest && this.closest('[hidden]'))) ? null : this.parentElement; },
  configurable: true,
});
w.Element.prototype.scrollIntoView = function () {};
// jsdom's XPath cannot run htmx's hx-on: attribute query (it throws); nothing
// here uses hx-on:, so the empty answer is the true one
w.XPathEvaluator = function () {};
w.XPathEvaluator.prototype.createExpression = function () {
  return { evaluate: function () { return { iterateNext: function () { return null; } }; } };
};
w.fetch = function (url) {
  url = String(url);
  let body = {};
  if (url.indexOf('/chip/active-token') === 0) body = { loaded: true, token: 't', name: 'LabA', path: '/c' };
  else if (url.indexOf('/neighbor') !== -1) body = { uid: KEY + ':' + (RUN - 2), run_id: RUN - 2 };
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
};
const splitPresets = [];
w._applySplitPreset = function (which) { splitPresets.push(which); };
w.eval(fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8'));
w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
const htmx = w.htmx;
ok(!!(htmx && htmx.ajax && htmx.process), '(fixture) the REAL htmx loaded');

// the full page, as the server rendered it (innerHTML: its inline scripts are
// the page's business, not this check's)
doc.getElementById('table-pane').innerHTML = DETAIL;

// every request htmx would send: record (verb, path, resolved target), cancel
// the network, and swap what the server would render for it
const reqs = [];
doc.addEventListener('htmx:configRequest', function (e) {
  const d = e.detail, t = d.target;
  reqs.push({ path: d.path, target: t && t.id });
  e.preventDefault();
  let html = null;
  const m = /^\/dataset\/([^/?]+)$/.exec(d.path);
  if (m) html = detailFor(decodeURIComponent(m[1]));
  else if (d.path === '/explorer') html = '<div id="explorer-tree-state"><div>tree</div></div>';
  else if (d.path === '/datasets') html = '<div id="datasets-page"></div>';
  if (html != null && t) {
    t.innerHTML = html;
    htmx.process(t);
    t.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target: t, requestConfig: d } }));
  }
});
htmx.process(doc.body);
const navLink = doc.getElementById('nav-ds');
navLink.setAttribute('hx-get', '/datasets');
navLink.setAttribute('hx-target', '#table-pane');
htmx.process(navLink);

function roots() { return doc.querySelectorAll('[id="ds-detail-root"]'); }
function lastReq() { return reqs[reqs.length - 1] || {}; }
function btn(sel, pane) { return doc.querySelector('#' + pane + ' ' + sel); }

(async () => {
  // ── ↓ on the full page: the next run REPLACES it ────────────────────────
  const down = btn('.inspector-nav-btn[aria-label="Older run"]', 'table-pane');
  ok(!!down, '(fixture) the rendered full page has the ↓ button');
  down.click();
  await tick();
  ok(lastReq().path === '/dataset/' + KEY + ':' + (RUN - 1) && lastReq().target === 'table-pane',
     '↓ on a full page loads the next run INTO #table-pane: ' + JSON.stringify(lastReq()));
  ok(roots().length === 1 && roots()[0].getAttribute('data-uid') === KEY + ':' + (RUN - 1),
     'exactly one run detail on screen (never stacked under the full page)');
  ok(w.location.pathname === '/dataset/' + KEY + ':' + (RUN - 1),
     'the URL names the run on screen: ' + w.location.pathname);
  btn('.inspector-nav-btn[aria-label="Older run"]', 'table-pane').click();
  await tick(40);
  ok(lastReq().path === '/dataset/' + KEY + ':' + (RUN - 2) && lastReq().target === 'table-pane',
     'past the tree end the server neighbor also lands in #table-pane (navigation advances)');

  // ── the parent link resolves to the pane that holds the run ────────────
  const parent = btn('a[hx-get^="/dataset/"]', 'table-pane');
  ok(!!parent, '(fixture) the rendered detail has the parent link');
  parent.click();
  await tick();
  ok(lastReq().target === 'table-pane', 'the parent link on a full page targets #table-pane: '
     + JSON.stringify(lastReq()));
  ok(roots().length === 1, 'still exactly one run detail after the parent link');
  ok(w.location.pathname === '/dataset/' + KEY + ':' + (RUN - 10),
     'the URL follows the parent link too: ' + w.location.pathname);

  // ...but a swap that is NOT the run's own control (e.g. a journal/agent run
  // link that pushes its own entry) never renames the entry it leaves
  const before = w.location.pathname;
  const other = doc.createElement('a');
  other.setAttribute('hx-get', '/dataset/' + KEY + ':' + (RUN - 5));
  other.setAttribute('hx-target', '#table-pane');
  doc.body.appendChild(other); htmx.process(other);
  other.click();
  await tick();
  ok(w.location.pathname === before, 'a foreign swap into #table-pane leaves the URL to its own history handling');
  other.remove();

  // ── Go to state from the full page: the run moves to the inspector first ──
  const n0 = reqs.length;
  w._navigateToExplorerPath('qubits.q1.f_01');
  await tick(80);
  const seq = reqs.slice(n0).map(r => r.path + '>' + r.target);
  ok(seq.length >= 2 && /^\/dataset\/.+>inspector-pane$/.test(seq[0]) && seq[1] === '/explorer>table-pane',
     'Go to state moves the run into the inspector BEFORE the Explorer takes #table-pane: ' + JSON.stringify(seq));
  ok(splitPresets[splitPresets.length - 1] === 'collapsed', 'and collapses it to the user preset after that swap');
  ok(!!doc.querySelector('#inspector-pane #ds-detail-root') && !doc.querySelector('#table-pane #ds-detail-root'),
     'the run is beside the state (inspector), the Explorer on top');

  // ── ⛶ from the inspector is a REAL page (a history entry Back returns
  //    from, one code path with a pasted URL) -- not an htmx swap ──────────
  const n2 = reqs.length;
  btn('.inspector-expand', 'inspector-pane').click();
  await tick();
  ok(navigations.length === 1 && reqs.length === n2,
     '⛶ navigates to the full page instead of swapping it into #table-pane: '
     + JSON.stringify({ navigations, reqs: reqs.slice(n2) }));

  // ── × in the inspector closes the inspector, as before ─────────────────
  btn('.inspector-close', 'inspector-pane').click();
  await tick();
  ok(doc.getElementById('inspector-pane').innerHTML === '', '× on an inspector run closes the inspector');

  // ── × on a full page goes back to the Datasets list ─────────────────────
  const t = doc.getElementById('table-pane');
  t.innerHTML = DETAIL; htmx.process(t);
  const n1 = reqs.length;
  btn('.inspector-close', 'table-pane').click();
  await tick();
  ok(reqs.length > n1 && lastReq().path === '/datasets' && lastReq().target === 'table-pane',
     '× on a full page returns to the Datasets list: ' + JSON.stringify(lastReq()));
  ok(!doc.querySelector('#table-pane #ds-detail-root'), 'the full page is gone after ×');

  // ── a full page AND an inspector run on screen: each header drives its own ──
  t.innerHTML = DETAIL; htmx.process(t);
  const ins = doc.getElementById('inspector-pane');
  ins.innerHTML = detailFor(KEY + ':' + (RUN + 50)); htmx.process(ins);
  btn('.inspector-nav-btn[aria-label="Older run"]', 'table-pane').click();
  await tick(40);
  ok(lastReq().path === '/dataset/' + KEY + ':' + (RUN - 1) && lastReq().target === 'table-pane',
     'the full page\'s ↓ steps from ITS run, not the inspector\'s: ' + JSON.stringify(lastReq()));
  ok(ins.querySelector('#ds-detail-root').getAttribute('data-uid') === KEY + ':' + (RUN + 50),
     'the inspector run is left alone');

  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('HARNESS ERROR:', e && e.stack || e); process.exit(1); });
