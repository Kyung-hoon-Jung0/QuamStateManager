// Behavioral check for the hardened shared folder browser (app.js IIFE).
//
// Customer feedback: "sometimes hangs, sometimes loses the path or resets"
// + Linux compatibility. Pins: fetch timeout → error row with a working
// Retry; stale responses dropped (monotonic nav token); _currentPath only
// ever a successfully-listed folder (failed navigation reverts the selected
// path, so Select/mkdir can't act on a folder never reached); POSIX
// breadcrumbs carry real "/home/user" paths (the old builder joined with
// backslashes and dropped the leading slash); Windows drive + UNC crumbs;
// mkdir double-submit guard + failure re-sync; last-path restore per input.
//
// Run: node tests/folder_browser_selfcheck.cjs   (needs jsdom)
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

const ROOT = path.join(__dirname, '..');
const APP_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

// The dialog markup, verbatim shape from base.html (ids are the contract).
const DIALOG = `
  <input id="target-a"><input id="target-b">
  <dialog id="folder-browser">
    <input type="text" id="browser-selected-path" readonly>
    <div id="browser-newfolder-row" hidden>
      <input type="text" id="browser-newfolder-name">
      <span id="browser-newfolder-err"></span>
    </div>
    <details id="browser-recent" open><div id="browser-recent-list"></div></details>
    <div id="browser-breadcrumbs"></div>
    <div id="browser-list"></div>
  </dialog>`;

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

function makeWorld() {
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + DIALOG + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  win.HTMLDialogElement.prototype.close = function () { this.open = false; };
  // Programmable fetch: routes[url-substring] = fn(url) -> response | Promise.
  win._fetchLog = [];
  win._fetchImpl = function () { return Promise.reject(new Error('no impl')); };
  win.fetch = function (url, opts) {
    win._fetchLog.push(url);
    return win._fetchImpl(url, opts);
  };
  win.AbortController = function () {
    this.signal = { aborted: false };
    this.abort = function () { this.signal.aborted = true; };
  };
  // app.js is the whole bundle — evaluate it; only the browser IIFE matters.
  new win.Function(APP_JS).call(win);
  return win;
}

function jsonResponse(data) {
  return Promise.resolve({ ok: true, json: function () { return Promise.resolve(data); } });
}
function listText(win) { return win.document.getElementById('browser-list').textContent; }
function selectedPath(win) { return win.document.getElementById('browser-selected-path').value; }

(async function main() {

  // G1: happy path — a listing updates the selected path, remembers it per
  // target input, and re-opens there next time.
  {
    const win = makeWorld();
    win._fetchImpl = function (url) {
      if (url.indexOf('/browse') === 0) {
        return jsonResponse({ path: '/home/user/data', dirs: ['/home/user/data/run1'],
                              parent: '/home/user', has_quam_state: false });
      }
      return Promise.reject(new Error('unexpected ' + url));
    };
    win.openFolderBrowser('target-a');
    await tick();
    ok(selectedPath(win) === '/home/user/data', 'G1: selected path from server');
    ok(listText(win).indexOf('run1') >= 0, 'G1: listing rendered');
    ok(win.localStorage.getItem('quam_folder_last:target-a') === '/home/user/data',
      'G1: last path remembered per input');
    // Re-open with an empty input → starts at the remembered path.
    win._fetchLog.length = 0;
    win.openFolderBrowser('target-a');
    await tick();
    ok(win._fetchLog[0].indexOf(encodeURIComponent('/home/user/data')) >= 0,
      'G1: reopen starts at the remembered path (got ' + win._fetchLog[0] + ')');
    // A DIFFERENT input has its own memory (server default when empty).
    win._fetchLog.length = 0;
    win.openFolderBrowser('target-b');
    await tick();
    ok(win._fetchLog[0] === '/browse?path=', 'G1: other input starts fresh');
  }

  // G2: network failure → error row + Retry that actually retries; the
  // selected path reverts to the last GOOD folder.
  {
    const win = makeWorld();
    let failNext = false;
    win._fetchImpl = function (url) {
      if (failNext) return Promise.reject(new Error('boom'));
      return jsonResponse({ path: '/ok', dirs: [], parent: '', has_quam_state: false });
    };
    win.openFolderBrowser('target-a');
    await tick();
    ok(selectedPath(win) === '/ok', 'G2: landed on the good folder');
    failNext = true;
    win.navigateBrowser('/broken');
    await tick();
    ok(listText(win).indexOf('Could not reach the app') >= 0, 'G2: failure text rendered');
    const retry = win.document.querySelector('#browser-list button');
    ok(!!retry && retry.textContent === 'Retry', 'G2: Retry button present');
    ok(selectedPath(win) === '/ok', 'G2: selected path reverted to the last good folder');
    failNext = false;
    retry.onclick();
    await tick();
    ok(listText(win).indexOf('Could not reach') < 0, 'G2: retry recovered');
  }

  // G2b: server-side error field (permission denied) uses the same surface.
  {
    const win = makeWorld();
    win._fetchImpl = function () {
      return jsonResponse({ path: '/locked', dirs: [], parent: '/',
                            error: 'Permission denied' });
    };
    win.navigateBrowser('/locked');
    await tick();
    ok(listText(win).indexOf('Permission denied') >= 0, 'G2b: server error rendered');
  }

  // G3: stale responses drop — a slow first navigation must not overwrite a
  // fast second one.
  {
    const win = makeWorld();
    let releaseSlow;
    const slow = new Promise(function (r) { releaseSlow = r; });
    win._fetchImpl = function (url) {
      if (url.indexOf('slowdir') >= 0) {
        return slow.then(function () {
          return { ok: true, json: function () {
            return Promise.resolve({ path: '/slowdir', dirs: ['/slowdir/x'], parent: '/' });
          } };
        });
      }
      return jsonResponse({ path: '/fastdir', dirs: ['/fastdir/y'], parent: '/' });
    };
    win.navigateBrowser('/slowdir');
    win.navigateBrowser('/fastdir');
    await tick();
    releaseSlow();
    await tick();
    ok(selectedPath(win) === '/fastdir', 'G3: stale slow response did not win');
    ok(listText(win).indexOf('y') >= 0 && listText(win).indexOf('/slowdir') < 0,
      'G3: listing is the newest navigation');
  }

  // G4: breadcrumbs — POSIX paths get real slash-prefixed crumb targets
  // (the old builder emitted "home\\user"); drive + UNC forms still work.
  {
    const win = makeWorld();
    win._fetchImpl = function (url) {
      const p = decodeURIComponent(url.split('=')[1] || '');
      return jsonResponse({ path: p, dirs: [], parent: '' });
    };
    win.navigateBrowser('/home/user/data');
    await tick();
    let crumbs = Array.prototype.map.call(
      win.document.querySelectorAll('#browser-breadcrumbs [data-path]'),
      function (c) { return c.getAttribute('data-path'); });
    ok(JSON.stringify(crumbs) === JSON.stringify(['/', '/home', '/home/user', '/home/user/data']),
      'G4: POSIX crumbs (got ' + JSON.stringify(crumbs) + ')');

    win.navigateBrowser('C:\\Users\\lab');
    await tick();
    crumbs = Array.prototype.map.call(
      win.document.querySelectorAll('#browser-breadcrumbs [data-path]'),
      function (c) { return c.getAttribute('data-path'); });
    ok(JSON.stringify(crumbs) === JSON.stringify(['C:\\', 'C:\\Users', 'C:\\Users\\lab']),
      'G4: drive crumbs (got ' + JSON.stringify(crumbs) + ')');

    win.navigateBrowser('\\\\srv\\share\\proj');
    await tick();
    crumbs = Array.prototype.map.call(
      win.document.querySelectorAll('#browser-breadcrumbs [data-path]'),
      function (c) { return c.getAttribute('data-path'); });
    ok(JSON.stringify(crumbs) === JSON.stringify(['\\\\srv\\share', '\\\\srv\\share\\proj']),
      'G4: UNC crumbs (got ' + JSON.stringify(crumbs) + ')');
  }

  // G5: mkdir — double-submit guard + failure re-navigates the listing.
  {
    const win = makeWorld();
    let mkdirCalls = 0, browseCalls = 0;
    let releaseMkdir;
    win._fetchImpl = function (url, opts) {
      if (url === '/mkdir') {
        mkdirCalls++;
        return new Promise(function (r) {
          releaseMkdir = function (okBody) {
            r({ ok: true, json: function () { return Promise.resolve(okBody); } });
          };
        });
      }
      browseCalls++;
      return jsonResponse({ path: '/base', dirs: [], parent: '/' });
    };
    win.navigateBrowser('/base');
    await tick();
    win.document.getElementById('browser-newfolder-name').value = 'sub';
    win.createBrowserFolder();
    win.createBrowserFolder();          // double-click — must not double-POST
    ok(mkdirCalls === 1, 'G5: in-flight guard blocks the second submit (got ' + mkdirCalls + ')');
    const preBrowse = browseCalls;
    releaseMkdir({ ok: false, error: 'Parent folder does not exist' });
    await tick();
    ok(win.document.getElementById('browser-newfolder-err').textContent
        .indexOf('Parent folder does not exist') >= 0, 'G5: mkdir error surfaced');
    ok(browseCalls > preBrowse, 'G5: failed mkdir re-syncs the listing');
    // Guard released — a new attempt POSTs again.
    win.createBrowserFolder();
    ok(mkdirCalls === 2, 'G5: guard released after completion');
  }

  // G6: dead-path navigation (stale Recent entry) — the server's
  // ancestor-walk response renders truthful crumbs for the folder ACTUALLY
  // listed plus an explanatory note; never a silent root-jump.
  {
    const win = makeWorld();
    win._fetchImpl = function (url) {
      const p = decodeURIComponent(url.split('path=')[1] || '');
      if (p === '/data/old/exp1') {
        // ancestor-walk: /data/old + /data/old/exp1 are gone → /data listed
        return jsonResponse({ path: '/data', dirs: ['/data/current'],
                              parent: '/', missing: '/data/old/exp1' });
      }
      return jsonResponse({ path: p, dirs: [], parent: '/' });
    };
    win.navigateBrowser('/data/old/exp1');
    await tick();
    ok(listText(win).indexOf('was not') >= 0 && listText(win).indexOf('/data/old/exp1') >= 0,
      'G6: missing-note explains the landing');
    const crumbs = Array.prototype.map.call(
      win.document.querySelectorAll('#browser-breadcrumbs [data-path]'),
      function (c) { return c.getAttribute('data-path'); });
    ok(JSON.stringify(crumbs) === JSON.stringify(['/', '/data']),
      'G6: crumbs mirror the folder actually listed (got ' + JSON.stringify(crumbs) + ')');
    ok(selectedPath(win) === '/data', 'G6: selected path = listed folder');
  }

  // G7: POSIX paths carry an explicit "/" root crumb (Computer = server
  // default/$HOME; "/" = the real filesystem root) — both truthful.
  {
    const win = makeWorld();
    win._fetchImpl = function (url) {
      const p = decodeURIComponent(url.split('path=')[1] || '') || '/home/u';
      return jsonResponse({ path: p, dirs: [], parent: '' });
    };
    win.navigateBrowser('/home/u/work');
    await tick();
    const crumbs = Array.prototype.map.call(
      win.document.querySelectorAll('#browser-breadcrumbs [data-path]'),
      function (c) { return c.getAttribute('data-path'); });
    ok(crumbs[0] === '/', 'G7: "/" crumb present first (got ' + JSON.stringify(crumbs) + ')');
    ok(crumbs.indexOf('/home/u') >= 0, 'G7: mid crumb is the true absolute path');
    // Windows drive paths get NO "/" crumb.
    win.navigateBrowser('D:\\work\\chips');
    await tick();
    const wcrumbs = Array.prototype.map.call(
      win.document.querySelectorAll('#browser-breadcrumbs [data-path]'),
      function (c) { return c.getAttribute('data-path'); });
    ok(wcrumbs[0] === 'D:\\', 'G7: drive crumb is D:\\ (rooted), no "/" crumb');
  }

  // G8: a bare drive token normalizes to the drive ROOT before fetching
  // (bare "D:" is CWD-relative on Windows).
  {
    const win = makeWorld();
    win._fetchImpl = function (url) {
      const p = decodeURIComponent(url.split('path=')[1] || '');
      return jsonResponse({ path: p, dirs: [], parent: '' });
    };
    win._fetchLog.length = 0;
    win.navigateBrowser('D:');
    await tick();
    ok(win._fetchLog[0].indexOf(encodeURIComponent('D:\\')) >= 0,
      'G8: bare "D:" normalized to "D:\\" (got ' + win._fetchLog[0] + ')');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('folder_browser_selfcheck: all checks passed');
  // app.js starts background poll intervals at eval time — exit explicitly
  // or the event loop never drains and the runner hangs waiting for EOF.
  process.exit(0);
})().catch(function (e) { console.error(e); process.exit(1); });
