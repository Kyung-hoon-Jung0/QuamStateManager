// docs/141 §4u — FloatPanel, the one drag core the Calculator, Settings and
// the Config Manual float with, against the REAL float-panel.js under jsdom:
//  - a header press + a move under 4 px is a click: nothing floats
//  - a real drag commits the panel to fixed coordinates (the owner's float
//    class AND fp-floating; left/top/width taken from the rect), calls onFloat
//    once, then follows the mouse, clamped inside the viewport
//  - the header's own buttons (tools) are not a grab
//  - the handle is bound once; a lost mouseup (buttons === 0) or a window blur
//    ends the drag; unfloat() puts the panel back under its anchor
//
// Run: node tests/float_panel_selfcheck.cjs   (needs jsdom)
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
const JS = fs.readFileSync(path.join(STATIC, 'float-panel.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

function world() {
  const dom = new JSDOM('<!DOCTYPE html><html><body>'
    + '<div id="p" class="tool"><div id="h" class="head"><b>Title</b><span class="tools"><button id="x">×</button></span></div><div>body</div></div>'
    + '</body></html>', { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  global.window = win; global.document = win.document;
  Object.defineProperty(win, 'innerWidth', { value: 1000, configurable: true });
  Object.defineProperty(win, 'innerHeight', { value: 700, configurable: true });
  const p = win.document.getElementById('p');
  p.getBoundingClientRect = function () { return { left: 300, top: 100, width: 320, height: 200, right: 620, bottom: 300 }; };
  Object.defineProperty(p, 'offsetWidth', { value: 320 });
  Object.defineProperty(p, 'offsetHeight', { value: 200 });
  win.eval(JS);
  return win;
}
function mouse(win, el, type, x, y, extra) {
  const ev = new win.MouseEvent(type, Object.assign({ bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: 1 }, extra || {}));
  el.dispatchEvent(ev);
  return ev;
}

let win = world();
let doc = win.document;
const p = doc.getElementById('p'), h = doc.getElementById('h');
let floated = 0;
ok(win.FloatPanel.drag(p, { handle: h, tools: '.tools', floatClass: 'tool-floating', onFloat: function () { floated++; } }) === true, 'drag() binds the handle');
ok(win.FloatPanel.drag(p, { handle: h }) === false, 'a second drag() on the same handle is a no-op (bound once)');

// a click (move under the threshold) never floats
mouse(win, h, 'mousedown', 400, 110);
mouse(win, doc, 'mousemove', 402, 111);
mouse(win, doc, 'mouseup', 402, 111);
ok(!p.classList.contains('tool-floating') && !p.classList.contains('fp-floating') && floated === 0 && p.style.left === '',
   'a press with a 3 px move is a click: nothing floats');

// a real drag commits at the rect, follows the mouse, calls onFloat once
mouse(win, h, 'mousedown', 400, 110);
mouse(win, doc, 'mousemove', 430, 150);
ok(p.classList.contains('tool-floating') && p.classList.contains('fp-floating') && floated === 1,
   'a real drag floats the panel with the owner\'s class and fp-floating, onFloat once');
ok(p.style.width === '320px' && p.style.left === '330px' && p.style.top === '140px',
   'position = the rect + the mouse delta, width frozen (' + p.style.left + ',' + p.style.top + ',' + p.style.width + ')');
mouse(win, doc, 'mousemove', 2000, 2000);
ok(p.style.left === (1000 - 320 - 4) + 'px' && p.style.top === (700 - 200 - 4) + 'px', 'clamped inside the viewport (' + p.style.left + ',' + p.style.top + ')');
mouse(win, doc, 'mousemove', -500, -500);
ok(p.style.left === '4px' && p.style.top === '4px', 'and at the top-left edge');
mouse(win, doc, 'mouseup', 0, 0);
mouse(win, doc, 'mousemove', 600, 600);
ok(p.style.left === '4px', 'after mouseup the panel no longer follows the mouse');
ok(floated === 1, 'onFloat is not called again by later moves');

// a lost mouseup ends the drag; the tools are not a grab
mouse(win, h, 'mousedown', 100, 100);
mouse(win, doc, 'mousemove', 150, 150, { buttons: 0 });
mouse(win, doc, 'mousemove', 300, 300);
ok(p.style.left === '4px', 'a move with no button held ends the drag (a mouseup missed over browser chrome)');
const before = p.style.left;
mouse(win, doc.getElementById('x'), 'mousedown', 10, 10);
mouse(win, doc, 'mousemove', 200, 200);
ok(p.style.left === before, 'a press on the header\'s own button is not a grab');
mouse(win, doc, 'mouseup', 200, 200);

// window blur ends a drag in progress
mouse(win, h, 'mousedown', 100, 100);
win.dispatchEvent(new win.Event('blur'));
mouse(win, doc, 'mousemove', 400, 400);
ok(p.style.left === before, 'a window blur ends the drag');
mouse(win, doc, 'mouseup', 0, 0);

// unfloat: back under the anchor
ok(win.FloatPanel.isFloating(p), 'isFloating reads fp-floating');
win.FloatPanel.unfloat(p, 'tool-floating');
ok(!p.classList.contains('fp-floating') && !p.classList.contains('tool-floating') && p.style.left === '' && p.style.width === '', 'unfloat clears the classes and the inline position');

// a missing handle or panel binds nothing, never throws
ok(win.FloatPanel.drag(null, {}) === false && win.FloatPanel.drag(p, { handleSelector: '.nope' }) === false, 'no handle, no binding, no throw');

console.log(fails ? ('FAILED ' + fails) : 'float_panel_selfcheck: all ok');
process.exit(fails ? 1 : 0);
