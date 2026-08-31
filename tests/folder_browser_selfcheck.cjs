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
    <button id="browser-select-btn">Select</button>
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

function makeWorld(opts) {
  // opts.lateDialog reproduces the REAL load order: app.js evaluates in
  // <head> before the dialog markup exists (docs/149 -- the default world
  // hid a load-time registration that silently bound nothing).
  const late = !!(opts && opts.lateDialog);
  const dom = new JSDOM('<!DOCTYPE html><html><body>' + (late ? '' : DIALOG) + '</body></html>',
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
  if (late) win.document.body.innerHTML = DIALOG;
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

  // G9: a POSIX path with a backslash INSIDE a folder name — "\" is a legal
  // filename char on POSIX; style classification is by the LEADING pattern
  // only (the old `indexOf("\\")` check flipped the whole path to Windows
  // splitting and every crumb click navigated to garbage).
  {
    const win = makeWorld();
    win._fetchImpl = function (url) {
      const p = decodeURIComponent(url.split('path=')[1] || '');
      if (p === '/data/back\\slash') {
        return jsonResponse({ path: '/data/back\\slash',
                              dirs: ['/data/back\\slash/child'], parent: '/data' });
      }
      return jsonResponse({ path: p, dirs: ['/data/back\\slash'], parent: '/' });
    };
    win.navigateBrowser('/data');
    await tick();
    const rows = win.document.querySelectorAll(
      '#browser-list .browser-folder:not(.browser-up)');
    ok(rows.length === 1 && rows[0].textContent === 'back\\slash',
      'G9: row label keeps the backslash name (got "' +
        (rows[0] && rows[0].textContent) + '")');
    win.navigateBrowser('/data/back\\slash');
    await tick();
    const crumbs = Array.prototype.map.call(
      win.document.querySelectorAll('#browser-breadcrumbs [data-path]'),
      function (c) { return c.getAttribute('data-path'); });
    ok(JSON.stringify(crumbs) === JSON.stringify(['/', '/data', '/data/back\\slash']),
      'G9: crumbs split on "/" only for POSIX paths (got ' + JSON.stringify(crumbs) + ')');
    const rows2 = win.document.querySelectorAll(
      '#browser-list .browser-folder:not(.browser-up)');
    ok(rows2.length === 1 && rows2[0].textContent === 'child',
      'G9: child row label (got "' + (rows2[0] && rows2[0].textContent) + '")');
  }

  // G10: a capped listing renders a muted "showing first N of M" note.
  {
    const win = makeWorld();
    win._fetchImpl = function () {
      return jsonResponse({ path: '/big', dirs: ['/big/a', '/big/b'], parent: '/',
                            truncated: true, total: 999 });
    };
    win.navigateBrowser('/big');
    await tick();
    ok(listText(win).indexOf('of 999') >= 0 && listText(win).indexOf('narrow') >= 0,
      'G10: truncated note rendered (got "' + listText(win) + '")');
    const note = win.document.querySelector('#browser-list .browser-truncated-note');
    ok(!!note, 'G10: note carries the muted class');
  }

  // G11: dead-end response (relative junk / no surviving ancestor — the
  // server echoes path === missing with nothing listed): the dead path must
  // NOT be remembered as last-good and Select must disable; a following
  // good navigation re-enables it.
  {
    const win = makeWorld();
    win._fetchImpl = function (url) {
      const p = decodeURIComponent(url.split('path=')[1] || '');
      if (p === 'Z:/junk') {
        return jsonResponse({ path: 'Z:/junk', dirs: [], parent: '',
                              missing: 'Z:/junk', has_quam_state: false });
      }
      return jsonResponse({ path: p || '/home/u', dirs: [], parent: '',
                            has_quam_state: false });
    };
    win.openFolderBrowser('target-a');
    await tick();
    ok(win.localStorage.getItem('quam_folder_last:target-a') === '/home/u',
      'G11: good path seeded');
    win.navigateBrowser('Z:/junk');
    await tick();
    const selBtn = win.document.getElementById('browser-select-btn');
    ok(selBtn.disabled === true, 'G11: Select disabled on a dead-end path');
    ok(win.localStorage.getItem('quam_folder_last:target-a') === '/home/u',
      'G11: dead path NOT remembered (got "' +
        win.localStorage.getItem('quam_folder_last:target-a') + '")');
    win.navigateBrowser('/home/u');
    await tick();
    ok(selBtn.disabled === false, 'G11: Select re-enabled after a good listing');
  }

  // G12 (docs/149): config mode -- *.toml files render as selectable rows
  // (click fills the selected path WITHOUT navigating), config-holding dirs
  // highlight, the current folder badges its own config.toml.
  {
    const win = makeWorld();
    win._fetchImpl = function (url) {
      const p = decodeURIComponent((url.split('path=')[1] || '').split('&')[0]);
      return jsonResponse({
        path: p || '/home/u', parent: '/',
        dirs: ['/home/u/.qualibrate', '/home/u/plain'],
        config_dirs: ['/home/u/.qualibrate'],
        files: ['/home/u/a.toml', '/home/u/config.toml'],
        has_config: true, has_quam_state: false,
      });
    };
    win.openFolderBrowser('target-a', 'config');
    await tick();
    ok(win._fetchLog[0] && win._fetchLog[0].indexOf('kind=config') >= 0,
      'G12: the request carries kind=config (got "' + win._fetchLog[0] + '")');
    const cfgDir = win.document.querySelector('#browser-list .browser-folder.is-config:not(.browser-file)');
    ok(!!cfgDir && cfgDir.textContent === '.qualibrate',
      'G12: the config-holding dir highlights');
    const fileRows = win.document.querySelectorAll('#browser-list .browser-file');
    ok(fileRows.length === 2 && fileRows[0].textContent === 'a.toml',
      'G12: toml files render as rows (got ' + fileRows.length + ')');
    ok(listText(win).indexOf('contains config.toml') >= 0, 'G12: badge rendered');
    const before = win._fetchLog.length;
    fileRows[1].click();
    await tick();
    ok(selectedPath(win) === '/home/u/config.toml',
      'G12: clicking a file fills the selected path (got "' + selectedPath(win) + '")');
    ok(win._fetchLog.length === before, 'G12: a file click never navigates');
    ok(fileRows[1].classList.contains('browser-file-selected'),
      'G12: the picked file row highlights');
    // Select confirms the FILE into the target input.
    win.selectBrowserFolder();
    ok(win.document.getElementById('target-a').value === '/home/u/config.toml',
      'G12: Select hands the file path to the target input');
  }

  // G13 (docs/149): keyboard navigation -- arrows move the highlight, Enter
  // descends into a folder / confirms a file, Backspace goes up, type-ahead
  // finds dot-dirs without the dot.
  {
    const win = makeWorld();
    win._fetchImpl = function (url) {
      const p = decodeURIComponent((url.split('path=')[1] || '').split('&')[0]);
      if (p === '/home/u/.qualibrate') {
        return jsonResponse({ path: p, parent: '/home/u', dirs: [],
                              files: [p + '/config.toml'], config_dirs: [],
                              has_config: true, has_quam_state: false });
      }
      return jsonResponse({
        path: p || '/home/u', parent: '/',
        dirs: ['/home/u/.qualibrate', '/home/u/plain'],
        config_dirs: ['/home/u/.qualibrate'], files: [],
        has_config: false, has_quam_state: false,
      });
    };
    win.openFolderBrowser('target-b', 'config');
    await tick();
    const list = win.document.getElementById('browser-list');
    function key(k) {
      list.dispatchEvent(new win.KeyboardEvent('keydown', { key: k, bubbles: true }));
    }
    key('ArrowDown');
    let act = win.document.querySelector('#browser-list .browser-active');
    ok(!!act && act.classList.contains('browser-up'),
      'G13: first ArrowDown lands on the up row');
    key('ArrowDown'); key('ArrowDown');
    act = win.document.querySelector('#browser-list .browser-active');
    ok(!!act && act.textContent === 'plain',
      'G13: repeated ArrowDown walks the rows (got "' + (act && act.textContent) + '")');
    key('ArrowUp');
    act = win.document.querySelector('#browser-list .browser-active');
    ok(!!act && act.textContent === '.qualibrate', 'G13: ArrowUp walks back');
    key('Enter');
    await tick();
    ok(selectedPath(win) === '/home/u/.qualibrate',
      'G13: Enter descends into the highlighted folder (got "' + selectedPath(win) + '")');
    // Inside .qualibrate: highlight the file, Enter must pick + confirm.
    key('ArrowDown'); key('ArrowDown');
    act = win.document.querySelector('#browser-list .browser-active');
    ok(!!act && act.hasAttribute('data-file'), 'G13: file row highlighted');
    key('Enter');
    await tick();
    ok(win.document.getElementById('target-b').value === '/home/u/.qualibrate/config.toml',
      'G13: Enter on a file confirms it into the target (got "'
        + win.document.getElementById('target-b').value + '")');
    ok(win.document.getElementById('folder-browser').open === false,
      'G13: the dialog closed on confirm');
    // Reopen: Backspace walks up; type-ahead finds ".qualibrate" from "qual".
    win.openFolderBrowser('target-b', 'config');
    await tick();
    win.navigateBrowser('/home/u/.qualibrate');
    await tick();
    key('Backspace');
    await tick();
    ok(selectedPath(win) === '/home/u',
      'G13: Backspace goes up one level (got "' + selectedPath(win) + '")');
    key('q'); key('u'); key('a'); key('l');
    act = win.document.querySelector('#browser-list .browser-active');
    ok(!!act && act.textContent === '.qualibrate',
      'G13: type-ahead "qual" finds .qualibrate without the dot');
  }

  // G14 (docs/149): the load-order trap. app.js is evaluated BEFORE the
  // dialog markup exists (exactly how base.html loads it); the keyboard
  // binding must still work on the first open.
  {
    const win = makeWorld({ lateDialog: true });
    win._fetchImpl = function (url) {
      const p = decodeURIComponent((url.split('path=')[1] || '').split('&')[0]);
      return jsonResponse({
        path: p || '/home/u', parent: '/',
        dirs: ['/home/u/.qualibrate', '/home/u/plain'],
        config_dirs: [], files: [], has_config: false, has_quam_state: false,
      });
    };
    win.openFolderBrowser('target-a', 'config');
    await tick();
    const list = win.document.getElementById('browser-list');
    list.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    const act = win.document.querySelector('#browser-list .browser-active');
    ok(!!act, 'G14: keyboard works when the dialog arrives AFTER app.js (late binding)');
    const before = win._fetchLog.length;
    list.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    list.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await tick();
    ok(win._fetchLog.length === before + 1,
      'G14: Enter navigates in the late-dialog world');
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('folder_browser_selfcheck: all checks passed');
  // app.js starts background poll intervals at eval time — exit explicitly
  // or the event loop never drains and the runner hangs waiting for EOF.
  process.exit(0);
})().catch(function (e) { console.error(e); process.exit(1); });
