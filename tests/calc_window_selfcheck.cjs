// docs/156 — the Calculator as its OWN browser window, driving the REAL
// calc.js under jsdom in its two worlds.
//
// World A, the standalone document (/calc-window, #calc-popover.calc-standalone):
//  - the fields are computed on load (no toggleCalc ever runs there)
//  - the first field takes focus
//  - an outside click / toggleCalc never hides it (the window is the frame)
//  - Escape closes the WINDOW
//  - size + screen position are remembered (quam_calc_win) on resize/pagehide
//
// World B, the in-page popover with its ↗:
//  - openCalcWindow opens ONE named popup window with a size, from the
//    trigger's data-calc-window-url, carrying the page's theme
//  - the in-page popover closes when the calculator moves out
//  - a second press, the Calculator button and Alt+C FOCUS the live window
//    instead of opening a second calculator; once it is closed the in-page
//    popover is back
//  - the remembered geometry becomes the next window's features
//  - a blocked popup (window.open → null) leaves the popover alone, no throw
//  - under pywebview: nothing opens, and the ↗ hides on pywebviewready
//
// Run: node tests/calc_window_selfcheck.cjs   (needs jsdom)
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
const CALC_JS = fs.readFileSync(path.join(STATIC, 'calc.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

// The fields calc.js reads by id (a subset of _calc_body.html; the Python
// test pins that every id calc.js names exists in the partial).
const FIELDS = `
  <input id="calc-s1-dp" value="-25"><input id="calc-s1-amp" value="0.5"><input id="calc-s1-from" value="10"><input id="calc-s1-to" value="-15">
  <input id="calc-s2-fsp" value="-11"><input id="calc-s2-amp" value="1.0"><input id="calc-s2-target" value="-15">
  <input id="calc-s3-dbm" value="0"><input id="calc-s3-r" value="50"><input id="calc-s3-vrms"><input id="calc-s3-vpk"><input id="calc-s3-vpp">
  <input id="calc-s4-rf"><input id="calc-s4-lo"><input id="calc-s4-if"><span id="calc-s4-note"></span>
  <input id="calc-expr" class="calc-expr" value="0.5*10^(-25/20)">
  <span id="calc-s1-k"></span><span id="calc-s1-anew"></span><span id="calc-s2-dbm"></span>
  <span id="calc-s2-anew"></span><span id="calc-s3-pmw"></span><span id="calc-expr-res"></span>`;

function world(bodyHtml, opts) {
  const dom = new JSDOM('<!doctype html><html data-theme="dark"><body>' + bodyHtml + '</body></html>',
    Object.assign({ url: 'http://localhost/', pretendToBeVisual: true, runScripts: 'outside-only' }, opts || {}));
  const w = dom.window;
  // `window.eval` runs in the jsdom realm (runScripts outside-only), so the
  // bare `document` / `navigator` / `setTimeout` calc.js makes resolve there.
  w.eval(CALC_JS);
  // The real page evaluates calc.js in <head> (readyState 'loading') and
  // wires on DOMContentLoaded; jsdom fires that asynchronously, so fire it
  // now — same order as the browser, and wire() is idempotent (_calcWired).
  w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
  return w;
}

/* ══ World A: the standalone document ══════════════════════════════════════ */
{
  const w = world('<div id="calc-popover" class="calc-popover calc-standalone">' + FIELDS + '</div><input id="outside">');
  const doc = w.document;
  const pop = doc.getElementById('calc-popover');

  ok(doc.getElementById('calc-s1-k').textContent === '0.0562341',
     'A1 standalone: computed on load without any toggle (10^(-25/20) = 0.0562341, got '
     + doc.getElementById('calc-s1-k').textContent + ')');
  ok(doc.getElementById('calc-expr-res').textContent === '0.0281171',
     'A1 standalone: the expression box is evaluated on load too');

  w.toggleCalc();
  ok(!pop.classList.contains('calc-hidden'), 'A3 toggleCalc is a no-op in the standalone window (no trigger to toggle from)');
  doc.getElementById('outside').dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
  ok(!pop.classList.contains('calc-hidden'), 'A3 an outside click never hides the standalone calculator');
  ok(!pop.classList.contains('calc-floating') && !pop.classList.contains('pop-anchored'),
     'A3 nothing anchors or floats it — the window is the frame');

  // Escape closes the WINDOW (jsdom's window.close is replaceable)
  let closed = 0;
  Object.defineProperty(w, 'close', { value: function () { closed++; }, configurable: true, writable: true });
  doc.getElementById('calc-s2-amp').dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  ok(closed === 1, 'A4 Escape in the standalone window closes the window (close() called ' + closed + 'x)');
  ok(!pop.classList.contains('calc-hidden'), 'A4 …and does not merely hide the popover');

  // geometry remembered on pagehide
  w.localStorage.removeItem('quam_calc_win');
  w.dispatchEvent(new w.Event('pagehide'));
  let g = null;
  try { g = JSON.parse(w.localStorage.getItem('quam_calc_win')); } catch (e) {}
  ok(g && g.w === w.innerWidth && g.h === w.innerHeight && typeof g.x === 'number' && typeof g.y === 'number',
     'A5 pagehide remembers {w, h, x, y} in quam_calc_win (got ' + JSON.stringify(g) + ')');

  // …and (debounced) on resize
  w.localStorage.removeItem('quam_calc_win');
  Object.defineProperty(w, 'innerWidth', { value: 517, configurable: true });
  Object.defineProperty(w, 'innerHeight', { value: 701, configurable: true });
  w.dispatchEvent(new w.Event('resize'));
  ok(w.localStorage.getItem('quam_calc_win') === null, 'A6 resize is debounced (nothing stored synchronously)');
  setTimeout(function () {
    let g2 = null;
    try { g2 = JSON.parse(w.localStorage.getItem('quam_calc_win')); } catch (e) {}
    ok(g2 && g2.w === 517 && g2.h === 701, 'A6 resize remembers the new size after the debounce (got ' + JSON.stringify(g2) + ')');
    ok(doc.activeElement && doc.activeElement.id === 'calc-s1-dp', 'A2 the first field takes focus in the standalone window');
    worldB();
  }, 320);
}

/* ══ World B: the in-page popover and its ↗ ═══════════════════════════════ */
function worldB() {
  const w = world(`
    <button class="sidebar-tool calc-btn" id="calc-btn" aria-expanded="false"></button>
    <div id="calc-popover" class="calc-popover calc-hidden">
      <div class="calc-header" id="calc-header"><span class="calc-header-tools">
        <button type="button" class="calc-close calc-popout" id="popout" data-calc-window-url="/calc-window"></button>
        <button type="button" class="calc-close" id="x"></button>
      </span></div>
      ${FIELDS}
    </div>
    <input id="outside">`);
  const doc = w.document;
  const pop = doc.getElementById('calc-popover');
  const btn = doc.getElementById('calc-btn');
  const popout = doc.getElementById('popout');

  const calls = [];
  let fake = null;
  w.open = function (url, name, features) { calls.push({ url, name, features }); return fake; };
  function newFake() { return { closed: false, focused: 0, focus() { this.focused++; } }; }

  // B1: opening
  fake = newFake();
  w.toggleCalc(btn);
  ok(!pop.classList.contains('calc-hidden'), 'B1 the in-page popover opens as before');
  const r = w.openCalcWindow(popout);
  ok(calls.length === 1, 'B1 ↗ opens exactly one window (window.open called ' + calls.length + 'x)');
  ok(calls[0] && calls[0].url.indexOf('/calc-window') === 0, 'B1 …at the route the button names (' + (calls[0] && calls[0].url) + ')');
  ok(calls[0] && /[?&]theme=dark\b/.test(calls[0].url), 'B1 …carrying the opening page\'s theme');
  ok(calls[0] && calls[0].name === 'quam-calc', 'B1 …under ONE window name, so a second open can never spawn a second window');
  ok(calls[0] && /popup=yes/.test(calls[0].features) && /width=400/.test(calls[0].features) && /height=680/.test(calls[0].features),
     'B1 …sized (a size is what makes the browser give a WINDOW, not a tab): ' + (calls[0] && calls[0].features));
  ok(calls[0] && !/left=/.test(calls[0].features), 'B1 no remembered position → none requested');
  ok(r === fake, 'B1 openCalcWindow returns the window');
  ok(fake.focused === 1, 'B1 the new window is focused');
  ok(pop.classList.contains('calc-hidden'), 'B1 the in-page popover CLOSES when the calculator moves out');

  // B2/B3: while it is alive, everything focuses it
  w.openCalcWindow(popout);
  ok(calls.length === 1 && fake.focused === 2, 'B2 a second ↗ focuses the live window, never opens another');
  w.toggleCalc(btn);
  ok(pop.classList.contains('calc-hidden') && fake.focused === 3, 'B3 the Calculator button focuses the live window instead of opening a second calculator');
  doc.body.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'c', altKey: true, bubbles: true, cancelable: true }));
  ok(pop.classList.contains('calc-hidden') && fake.focused === 4, 'B3 Alt+C does the same');

  // B4: once closed, the in-page popover is back
  fake.closed = true;
  w.toggleCalc(btn);
  ok(!pop.classList.contains('calc-hidden'), 'B4 after the window is closed the Calculator button opens the in-page popover again');
  w.toggleCalc(btn);

  // B5: remembered geometry → the next window's features
  w.localStorage.setItem('quam_calc_win', JSON.stringify({ w: 520, h: 700, x: 100, y: 50 }));
  fake = newFake();
  w.openCalcWindow(popout);
  ok(calls.length === 2 && /width=520/.test(calls[1].features) && /height=700/.test(calls[1].features)
     && /left=100/.test(calls[1].features) && /top=50/.test(calls[1].features),
     'B5 a remembered size + position becomes the next window\'s features (' + (calls[1] && calls[1].features) + ')');
  w.localStorage.removeItem('quam_calc_win');
  fake.closed = true;

  // B6: blocked popup
  fake = null;
  w.toggleCalc(btn);
  let threw = false, r6;
  try { r6 = w.openCalcWindow(popout); } catch (e) { threw = true; }
  ok(!threw && r6 === null, 'B6 a blocked popup returns null and does not throw');
  ok(!pop.classList.contains('calc-hidden'), 'B6 …and leaves the in-page popover open');
  ok(calls.length === 3, 'B6 (window.open was asked)');
  w.toggleCalc(btn);

  // B9: the URL comes from the trigger's own attribute, else the document's
  fake = newFake();
  popout.setAttribute('data-calc-window-url', '/prefix/calc-window');
  w.openCalcWindow(popout);
  ok(calls.length === 4 && calls[3].url.indexOf('/prefix/calc-window?') === 0, 'B9 the trigger\'s data-calc-window-url wins (' + calls[3].url + ')');
  fake.closed = true;
  fake = newFake();
  w.openCalcWindow();                       // no trigger (e.g. a future shortcut)
  ok(calls.length === 5 && calls[4].url.indexOf('/prefix/calc-window?') === 0, 'B9 …and is found in the document when no trigger is passed');
  fake.closed = true;

  // B10: the theme param follows the page
  doc.documentElement.setAttribute('data-theme', 'light');
  fake = newFake();
  w.openCalcWindow(popout);
  ok(calls.length === 6 && /[?&]theme=light\b/.test(calls[5].url), 'B10 a light page opens a light window');
  fake.closed = true;

  // B7/B8: the desktop shell
  w.pywebview = {};
  let r7;
  try { r7 = w.openCalcWindow(popout); } catch (e) { r7 = 'threw'; }
  ok(r7 === null && calls.length === 6, 'B7 under pywebview nothing opens (window.open would navigate the app away)');
  ok(popout.hidden !== true, 'B8 (the ↗ is still visible before the shell announces itself)');
  w.dispatchEvent(new w.Event('pywebviewready'));
  ok(popout.hidden === true, 'B8 pywebviewready hides the ↗');
  delete w.pywebview;

  process.exit(fails ? 1 : 0);
}
