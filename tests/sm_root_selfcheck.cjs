/* docs/226 -- sm-root.js, the one place the URL prefix is known on the client.
 *
 * The REAL shipped file, evaluated the way the jsdom harnesses boot it
 * (`new Function('window', src)(window)`, the Node realm -- so a bare
 * `document` / `history` / `fetch` in it would throw here, not pass).
 *
 *  R*   data-root="" (and "/", and absent): window.SM exists with root '' and
 *       identity url/path; window.fetch, history.pushState/replaceState are
 *       the ORIGINAL functions (identity, not a wrapper); no htmx extension is
 *       defined and no document listener is added
 *  U*   SM.url under '/sm': the prefix is added exactly once, on a segment
 *       boundary ('/smx' and '/sm-x/y' are app paths); protocol-relative,
 *       absolute, relative, query/hash-only, empty and non-string inputs are
 *       returned untouched; url(url(p)) === url(p)
 *  P*   SM.path strips the prefix on the same boundary; path(url(p)) === p
 *  N*   data-root normalisation ('/sm/' '/sm//' -> '/sm'; multi-segment roots)
 *  F*   fetch: string inputs prefixed once; URL and Request objects that are
 *       same-origin and not yet prefixed are re-targeted (a Request keeps
 *       method, headers, options, body and abort signal); cross-origin and
 *       already-prefixed objects pass through as the very same object; init,
 *       arity, `this` and the return value are the caller's
 *  H*   history.pushState/replaceState: the URL argument only, same rules;
 *       state object and arity untouched; the location lands under /sm once
 *  X*   the htmx extension: defined at load when htmx is there, else on
 *       DOMContentLoaded or at the first htmx:configRequest dispatch (then
 *       its listeners are gone); onEvent prefixes configRequest's path once,
 *       touches nothing else, never cancels an event
 *  L*   lab-check.js loaded AFTER sm-root.js (base.html order) is the OUTER
 *       fetch wrapper: it still recognises the caller's '/field/edit' -- and a
 *       caller that already carries the prefix -- and both the edit and its
 *       '/field/lab-watch' probe reach the network under /sm, exactly once
 *  D*   a second load of sm-root.js does not wrap twice
 *
 * Run: node tests/sm_root_selfcheck.cjs   (driven by tests/test_js_root_lint.py)
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const SRC = fs.readFileSync(path.join(STATIC, 'sm-root.js'), 'utf8');
const LAB_SRC = fs.readFileSync(path.join(STATIC, 'lab-check.js'), 'utf8');
const ORIGIN = 'http://127.0.0.1:5050';

let fails = 0, passes = 0;
function ok(c, m) { if (c) { passes++; console.log('ok - ' + m); } else { fails++; console.error('FAIL: ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
const show = (v) => (typeof v === 'string' ? JSON.stringify(v) : String(v));

/* A window with data-root, a spy under window.fetch and under both history
   methods (installed BEFORE sm-root.js, as the page's natives are), and a
   count of document listeners added while sm-root.js runs. */
function world(root, opts) {
  opts = opts || {};
  const attr = root == null ? '' : ' data-root="' + root + '"';
  const dom = new JSDOM('<!doctype html><html' + attr + '><head></head><body><table><tr><td>'
    + '<input class="bulk-cell" data-dot-path="qubits.q1.f_01" value="5e9"></td></tr></table></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: ORIGIN + (opts.at || '/sm/qubits') });
  const w = dom.window;
  const T = { w: w, fetches: [], pushes: [], replaces: [], defined: [], docListeners: [] };
  T.fetchSpy = function (input, init) {
    T.fetches.push({ input: input, init: init, argc: arguments.length, self: this });
    if (opts.fetchAnswer) return opts.fetchAnswer(input, init);
    return T.fetchAnswer = Promise.resolve({ status: 200, json: () => Promise.resolve({}) });
  };
  w.fetch = T.fetchSpy;
  w.Request = Request;                                  // jsdom has none; Node's fetch Request stands in
  const H = w.history, pushN = H.pushState, replN = H.replaceState;
  T.pushSpy = function (s, t, u) { T.pushes.push({ state: s, url: u, argc: arguments.length }); return pushN.apply(this, arguments); };
  T.replSpy = function (s, t, u) { T.replaces.push({ state: s, url: u, argc: arguments.length }); return replN.apply(this, arguments); };
  H.pushState = T.pushSpy;
  H.replaceState = T.replSpy;
  if (opts.htmx) {
    w.htmx = { defineExtension: function (name, ext) { T.defined.push({ name: name, ext: ext }); } };
  }
  const add = w.document.addEventListener;
  w.document.addEventListener = function (type) { T.docListeners.push(type); return add.apply(this, arguments); };
  T.boot = function () { new Function('window', SRC)(w); return w.SM; };
  T.boot();
  w.document.addEventListener = add;
  T.loc = () => w.location.pathname + w.location.search;
  return T;
}

(async function main() {
  // ── R: root is a pure no-op ─────────────────────────────────────────────
  const ROOT_INPUTS = ['/x', '/', '/sm', '/sm/x', '/smx', '//cdn/x', 'http://h/x', 'x/y', '?q=1', '#h', '', '/a?b=/c'];
  for (const r of ['', '/', null]) {
    const T = world(r, { htmx: true, at: '/qubits' });
    const tag = 'data-root=' + (r == null ? '(absent)' : show(r));
    ok(T.w.SM && T.w.SM.root === '', 'R1 ' + tag + ': SM.root is ""');
    ok(ROOT_INPUTS.every((p) => T.w.SM.url(p) === p && T.w.SM.path(p) === p)
       && T.w.SM.url(null) === null && T.w.SM.path(undefined) === undefined,
       'R2 ' + tag + ': url(p) === p and path(p) === p for ' + ROOT_INPUTS.length + ' inputs + non-strings');
    ok(T.w.fetch === T.fetchSpy, 'R3 ' + tag + ': window.fetch IS the original function (no wrapper)');
    ok(T.w.history.pushState === T.pushSpy && T.w.history.replaceState === T.replSpy,
       'R4 ' + tag + ': history.pushState/replaceState ARE the originals');
    ok(T.defined.length === 0 && T.docListeners.length === 0,
       'R5 ' + tag + ': no htmx extension defined, no document listener added (' + T.docListeners.join(',') + ')');
  }
  {
    const T = world('', { at: '/qubits' });
    T.w.fetch('/field/peek');
    T.w.history.pushState(null, '', '/pairs');
    ok(T.fetches[0].input === '/field/peek' && T.loc() === '/pairs',
       'R6 root: fetch("/field/peek") and pushState("/pairs") reach the originals unchanged');
  }

  // ── U / P: the url / path tables under '/sm' ─────────────────────────────
  const T = world('/sm', { htmx: true });
  const SM = T.w.SM;
  ok(SM.root === '/sm', 'U0 SM.root is "/sm"');
  const URL_TABLE = [
    ['/x', '/sm/x'], ['/', '/sm/'], ['/sm', '/sm'], ['/sm/', '/sm/'], ['/sm/x', '/sm/x'],
    ['/sm?x=1', '/sm?x=1'], ['/sm#h', '/sm#h'], ['/sm-x/y', '/sm/sm-x/y'], ['/smx', '/sm/smx'],
    ['/qubits/sm', '/sm/qubits/sm'], ['/x?next=/sm/y', '/sm/x?next=/sm/y'], ['/field/edit?a=1#b', '/sm/field/edit?a=1#b'],
    ['//cdn/x', '//cdn/x'], ['/\\cdn/x', '/\\cdn/x'], ['http://h/x', 'http://h/x'], ['https://h/sm/x', 'https://h/sm/x'],
    ['x/y', 'x/y'], ['?q=1', '?q=1'], ['#h', '#h'], ['', ''],
  ];
  URL_TABLE.forEach(function (row) {
    ok(SM.url(row[0]) === row[1], 'U url(' + show(row[0]) + ') === ' + show(row[1]) + ' (got ' + show(SM.url(row[0])) + ')');
  });
  const obj = {};
  ok(SM.url(null) === null && SM.url(undefined) === undefined && SM.url(42) === 42 && SM.url(obj) === obj,
     'U non-string inputs are returned untouched');
  ok(URL_TABLE.every((row) => SM.url(SM.url(row[0])) === SM.url(row[0])), 'U idempotent: url(url(p)) === url(p) over the table');
  const PATH_TABLE = [
    ['/sm', '/'], ['/sm/', '/'], ['/sm/x', '/x'], ['/sm/x?q=1', '/x?q=1'], ['/sm?q', '/?q'], ['/sm#h', '/#h'],
    ['/sm-x/y', '/sm-x/y'], ['/smx', '/smx'], ['/x', '/x'], ['/', '/'], ['http://h/sm/x', 'http://h/sm/x'], ['', ''],
  ];
  PATH_TABLE.forEach(function (row) {
    ok(SM.path(row[0]) === row[1], 'P path(' + show(row[0]) + ') === ' + show(row[1]) + ' (got ' + show(SM.path(row[0])) + ')');
  });
  ok(SM.path(null) === null && SM.path(obj) === obj, 'P non-string inputs are returned untouched');
  ok(['/x', '/', '/smx', '/sm-x/y', '/x?q=1', '/diff?a=1#b'].every((p) => SM.path(SM.url(p)) === p),
     'P round trip: path(url(p)) === p for app paths');

  // ── N: normalisation of data-root ─────────────────────────────────────
  ok(world('/sm/').w.SM.root === '/sm' && world('/sm//').w.SM.root === '/sm', 'N1 "/sm/" and "/sm//" normalise to "/sm"');
  {
    const S2 = world('/a/b', { at: '/a/b/qubits' }).w.SM;
    ok(S2.url('/x') === '/a/b/x' && S2.url('/a/b/x') === '/a/b/x' && S2.url('/a/x') === '/a/b/a/x'
       && S2.path('/a/b/x') === '/x' && S2.path('/a/x') === '/a/x',
       'N2 a two-segment root: prefixed once, "/a/x" is not under "/a/b"');
  }

  // ── F: fetch ───────────────────────────────────────────────────────────
  {
    const init = { method: 'POST', body: 'x=1' };
    const ret = T.w.fetch('/field/peek', init);
    const f = T.fetches[T.fetches.length - 1];
    ok(f.input === '/sm/field/peek' && f.init === init && f.argc === 2 && f.self === T.w && ret === T.fetchAnswer,
       'F1 fetch("/field/peek", init): prefixed once; the same init object, arity, `this` and return value');
    T.w.fetch('/sm/field/peek');
    ok(T.fetches[T.fetches.length - 1].input === '/sm/field/peek' && T.fetches[T.fetches.length - 1].argc === 1,
       'F2 an already-prefixed string is not prefixed again; a one-argument call stays one argument');
    const passthrough = ['//cdn/x', 'http://127.0.0.1:5050/x', 'https://other/x', 'x/y', '?q=1', ''];
    const got = passthrough.map((p) => { T.w.fetch(p); return T.fetches[T.fetches.length - 1].input; });
    ok(got.every((g, i) => g === passthrough[i]), 'F3 protocol-relative / absolute / relative / query / empty strings pass untouched: ' + got.join(' | '));

    const u = new T.w.URL(ORIGIN + '/api/x?y=1#f');
    T.w.fetch(u);
    const fu = T.fetches[T.fetches.length - 1].input;
    ok(fu instanceof T.w.URL && fu.href === ORIGIN + '/sm/api/x?y=1#f' && u.href === ORIGIN + '/api/x?y=1#f',
       'F4 a same-origin URL object is re-targeted to /sm (a new URL; the caller\'s is not mutated): ' + fu);
    const up = new T.w.URL(ORIGIN + '/sm/api/x'), ux = new T.w.URL('http://127.0.0.1:5051/api/x');
    T.w.fetch(up); const a1 = T.fetches[T.fetches.length - 1].input;
    T.w.fetch(ux); const a2 = T.fetches[T.fetches.length - 1].input;
    ok(a1 === up && a2 === ux, 'F5 an already-prefixed URL and a cross-origin URL are passed as the SAME object');

    const ctl = new AbortController();
    const rq = new Request(ORIGIN + '/api/x?y=1', { headers: { 'X-T': '1' }, credentials: 'include',
                                                    cache: 'no-store', redirect: 'manual', signal: ctl.signal });
    const rinit = { priority: 'low' };
    T.w.fetch(rq, rinit);
    const fr = T.fetches[T.fetches.length - 1];
    ok(fr.input instanceof Request && fr.input !== rq && fr.input.url === ORIGIN + '/sm/api/x?y=1'
       && fr.input.method === 'GET' && fr.input.headers.get('X-T') === '1' && fr.input.credentials === 'include'
       && fr.input.cache === 'no-store' && fr.input.redirect === 'manual' && fr.init === rinit,
       'F6 a same-origin GET Request is re-targeted to /sm with method, headers, credentials, cache, redirect kept; init untouched');
    ctl.abort();
    ok(fr.input.signal.aborted === true, 'F7 ...and the caller\'s abort signal still aborts it');

    const n0 = T.fetches.length;
    const post = new Request(ORIGIN + '/field/edit', { method: 'POST', body: 'dot_path=q&value=4',
                                                       headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
    const pret = T.w.fetch(post);
    await pret;
    const fp = T.fetches[n0] && T.fetches[n0].input;
    ok(fp instanceof Request && fp.url === ORIGIN + '/sm/field/edit' && fp.method === 'POST'
       && fp.headers.get('content-type') === 'application/x-www-form-urlencoded' && (await fp.text()) === 'dot_path=q&value=4',
       'F8 a same-origin POST Request is re-targeted with its body and content-type (buffered, then sent once)');
    ok(T.fetches.length === n0 + 1, 'F9 ...one network call, not two');

    const rp = new Request(ORIGIN + '/sm/api/x'), rx = new Request('http://other.example/api/x');
    T.w.fetch(rp); const b1 = T.fetches[T.fetches.length - 1].input;
    T.w.fetch(rx); const b2 = T.fetches[T.fetches.length - 1].input;
    ok(b1 === rp && b2 === rx, 'F10 an already-prefixed Request and a cross-origin Request are passed as the SAME object');
  }

  // ── H: history ─────────────────────────────────────────────────────────
  {
    const st = { htmx: true };
    T.w.history.pushState(st, '', '/pairs');
    const p1 = T.pushes[T.pushes.length - 1];
    ok(T.loc() === '/sm/pairs' && p1.url === '/sm/pairs' && p1.state === st && T.w.history.state.htmx === true,
       'H1 pushState(state, "", "/pairs") lands on /sm/pairs; the state object is the caller\'s');
    T.w.history.pushState(null, '', '/sm/diff?x=1');
    ok(T.loc() === '/sm/diff?x=1', 'H2 an already-prefixed URL is pushed as is (no /sm/sm)');
    T.w.history.replaceState(null, '', '/topology?view=coherence');
    ok(T.loc() === '/sm/topology?view=coherence' && T.replaces[T.replaces.length - 1].url === '/sm/topology?view=coherence',
       'H3 replaceState gets the same treatment');
    T.w.history.replaceState({ k: 1 }, '');
    ok(T.replaces[T.replaces.length - 1].argc === 2 && T.loc() === '/sm/topology?view=coherence',
       'H4 a two-argument replaceState stays two arguments and keeps the URL');
    T.w.history.pushState(null, '', '?view=trends');
    ok(T.loc() === '/sm/topology?view=trends', 'H5 a query-only URL is relative to the (prefixed) page and passes untouched');
    T.w.history.pushState(null, '', new T.w.URL(ORIGIN + '/zline?line=q1'));
    ok(T.loc() === '/sm/zline?line=q1', 'H6 a same-origin URL object is pushed under /sm');
    T.w.history.pushState(null, '', ORIGIN + '/sm/zz');
    ok(T.pushes[T.pushes.length - 1].url === ORIGIN + '/sm/zz' && T.loc() === '/sm/zz', 'H7 an absolute string passes untouched');
  }

  // ── X: the htmx extension ──────────────────────────────────────────────
  {
    ok(T.defined.length === 1 && T.defined[0].name === 'sm-root', 'X1 htmx present at load: extension "sm-root" defined once, at load');
    const ext = T.defined[0].ext;
    const ev = { detail: { path: '/qubits?x=1' } };
    const r1 = ext.onEvent('htmx:configRequest', ev);
    const ev2 = { detail: { path: '/sm/qubits' } };
    ext.onEvent('htmx:configRequest', ev2);
    ok(ev.detail.path === '/sm/qubits?x=1' && ev2.detail.path === '/sm/qubits' && r1 === true,
       'X2 configRequest: "/qubits?x=1" -> "/sm/qubits?x=1", an already-prefixed path is kept, and it returns true');
    const ev3 = { detail: { path: '/qubits' } };
    const r3 = ext.onEvent('htmx:beforeRequest', ev3);
    const ev4 = { detail: { path: ORIGIN + '/qubits' } };
    ext.onEvent('htmx:configRequest', ev4);
    ok(ev3.detail.path === '/qubits' && r3 === true && ev4.detail.path === ORIGIN + '/qubits'
       && ext.onEvent('htmx:configRequest', { detail: {} }) === true && ext.onEvent('htmx:configRequest', {}) === true,
       'X3 other events, absolute paths and a missing detail/path are left alone; never returns false');
  }
  {
    const L = world('/sm');
    ok(L.defined.length === 0 && L.docListeners.indexOf('DOMContentLoaded') >= 0 && L.docListeners.indexOf('htmx:configRequest') >= 0,
       'X4 no htmx at load: it waits for DOMContentLoaded and for the first htmx:configRequest');
    L.w.htmx = { defineExtension: function (name, ext) { L.defined.push({ name: name, ext: ext }); } };
    L.w.document.dispatchEvent(new L.w.Event('DOMContentLoaded'));
    L.w.document.dispatchEvent(new L.w.Event('DOMContentLoaded'));
    L.w.document.body.dispatchEvent(new L.w.CustomEvent('htmx:configRequest', { bubbles: true, detail: {} }));
    ok(L.defined.length === 1 && L.defined[0].name === 'sm-root', 'X5 ...defined on DOMContentLoaded, exactly once (the listeners are gone)');
    const L2 = world('/sm');
    L2.w.htmx = { defineExtension: function (name, ext) { L2.defined.push({ name: name, ext: ext }); } };
    L2.w.document.body.dispatchEvent(new L2.w.CustomEvent('htmx:configRequest', { bubbles: true, detail: {} }));
    L2.w.document.dispatchEvent(new L2.w.Event('DOMContentLoaded'));
    ok(L2.defined.length === 1, 'X6 an htmx arriving late is caught at its first configRequest dispatch, once');
  }

  // ── L: lab-check.js composes as the OUTER wrapper ──────────────────────
  {
    const LAB = 'qubit_pairs.p.macros.cz.flux.flat_length';
    const W = world('/sm', {
      fetchAnswer: function (input, init) {
        if (input === '/sm/field/lab-watch') return Promise.resolve({ status: 200, json: () => Promise.resolve({ lab: false }) });
        return new Promise(function () {});
      },
    });
    W.w.eval(LAB_SRC);
    ok(W.w.LabCheck._isEditPost('/field/edit', { method: 'POST' }) === true
       && W.w.LabCheck._isEditPost('/sm/field/edit-batch', { method: 'POST' }) === true
       && W.w.LabCheck._isEditPost('/sm/field/peek', { method: 'POST' }) === false
       && W.w.LabCheck._isEditPost('/smfield/edit', { method: 'POST' }) === false,
       'L1 _isEditPost: "/field/edit" and a prefixed "/sm/field/edit-batch" are edits; "/sm/field/peek" and "/smfield/edit" are not');
    const body = new W.w.URLSearchParams(); body.append('dot_path', LAB); body.append('value', '4');
    W.w.fetch('/field/edit', { method: 'POST', body: body.toString() });
    await tick(5);
    const urls = W.fetches.map((f) => f.input);
    ok(urls[0] === '/sm/field/edit' && urls[1] === '/sm/field/lab-watch' && urls.length === 2,
       'L2 the edit and its lab probe both reach the network under /sm, once: ' + urls.join(' , '));
    W.w.fetch('/sm/field/edit-batch', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ updates: [{ dot_path: LAB, value: 4 }] }) });
    await tick(5);
    const urls2 = W.fetches.map((f) => f.input).slice(2);
    ok(urls2[0] === '/sm/field/edit-batch' && urls2[1] === '/sm/field/lab-watch',
       'L3 a caller that already carries the prefix still gets the lab probe: ' + urls2.join(' , '));
  }

  // ── D: loading sm-root.js twice wraps once ─────────────────────────────
  {
    const W = world('/sm');
    const f1 = W.w.fetch, p1 = W.w.history.pushState;
    W.boot();
    W.w.fetch('/x');
    ok(W.w.fetch === f1 && W.w.history.pushState === p1 && W.fetches[0].input === '/sm/x',
       'D1 a second load keeps the first wrappers (no second layer) and still prefixes once');
  }

  console.log(passes + ' passed, ' + fails + ' failed');
  process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('FAIL: selfcheck threw: ' + (e && e.stack || e)); process.exit(1); });
