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

/* docs/165 (user): "크기 조절을 할수있으면 좋겠다 -- 마우스로 edge에 가져갔을 때".
   CSS `resize: both` gives ONE grip in the bottom-right corner, and Settings
   -- the one window without the rule -- had not even that. Every edge and
   every corner resizes now, and grabbing an edge floats the panel first,
   because anchored it is pinned by `right`/`top:100%` and a north or west
   drag would have to move an origin it does not own. */
{
  const w2 = world();
  const d2 = w2.document;
  const q = d2.getElementById('p');
  let rect = { left: 300, top: 100, width: 320, height: 200, right: 620, bottom: 300 };
  q.getBoundingClientRect = function () { return rect; };
  // the panel declares its own floor; the core must read it, not carry one
  w2.getComputedStyle = function () { return { minWidth: '260px', minHeight: '150px' }; };
  ok(w2.FloatPanel.resize(q, { floatClass: 'tool-floating' }) === true, 'resize() binds the panel');
  ok(w2.FloatPanel.resize(q, {}) === false, 'a second resize() on the same panel is a no-op');

  // the border band names the side, the middle names none
  ok(w2.FloatPanel.edgeAt(q, { clientX: 618, clientY: 200 }) === 'e', 'the right band reads as east');
  ok(w2.FloatPanel.edgeAt(q, { clientX: 302, clientY: 200 }) === 'w', 'the left band reads as west');
  ok(w2.FloatPanel.edgeAt(q, { clientX: 460, clientY: 102 }) === 'n', 'the top band reads as north');
  ok(w2.FloatPanel.edgeAt(q, { clientX: 618, clientY: 298 }) === 'se', 'a corner reads as both');
  ok(w2.FloatPanel.edgeAt(q, { clientX: 460, clientY: 200 }) === '', 'the middle is not an edge');

  // hovering the edge says so, and the middle does not
  mouse(w2, q, 'mousemove', 618, 200);
  ok(q.style.cursor === 'ew-resize', 'hovering the east edge shows the resize cursor');
  mouse(w2, q, 'mousemove', 460, 200);
  ok(q.style.cursor === '', 'the middle keeps the ordinary cursor');

  // drag the east edge 60 px wider
  mouse(w2, q, 'mousedown', 618, 200);
  ok(q.classList.contains('fp-floating') && q.classList.contains('tool-floating'),
     'grabbing an edge floats the panel (it needs an origin of its own)');
  mouse(w2, d2, 'mousemove', 678, 200);
  ok(q.style.width === '380px', 'the east edge widens it (got ' + q.style.width + ')');
  ok(q.style.left === '300px', 'and leaves the left edge where it was');
  mouse(w2, d2, 'mouseup', 678, 200);
  ok(q.style.cursor === '', 'the cursor is released with the mouse');

  // the WEST edge moves the origin instead
  rect = { left: 300, top: 100, width: 380, height: 200, right: 680, bottom: 300 };
  mouse(w2, q, 'mousedown', 302, 200);
  mouse(w2, d2, 'mousemove', 262, 200);
  ok(q.style.width === '420px' && q.style.left === '260px',
     'the west edge grows leftwards (got ' + q.style.width + ' at ' + q.style.left + ')');
  mouse(w2, d2, 'mouseup', 262, 200);

  // the panel's OWN floor is honoured, and a north drag stops at it
  rect = { left: 260, top: 100, width: 420, height: 200, right: 680, bottom: 300 };
  mouse(w2, q, 'mousedown', 262, 200);
  mouse(w2, d2, 'mousemove', 900, 200);              // far past the minimum
  ok(q.style.width === '260px', "it never shrinks below the panel's own min-width");
  ok(q.style.left === '420px', 'and the moving edge stops at that floor, never crosses it');
  mouse(w2, d2, 'mouseup', 900, 200);

  // A press in the middle is not a resize -- and, the part that bites, it must
  // not be SWALLOWED either. Without the border-band guard the handler still
  // returns early on the empty side, so nothing resizes and the mutation looks
  // harmless; what it really does is preventDefault + stopPropagation on every
  // press inside the panel, which is every button the window contains.
  const before2 = q.style.width;
  const ev = mouse(w2, q, 'mousedown', 460, 200);
  ok(ev.defaultPrevented === false,
     'a press in the panel BODY is left alone (its buttons still work)');
  mouse(w2, d2, 'mousemove', 900, 900);
  ok(q.style.width === before2, 'a press in the middle resizes nothing');
  mouse(w2, d2, 'mouseup', 0, 0);

  // ...while a press on the band is claimed
  const ev2 = mouse(w2, q, 'mousedown', 262, 200);
  ok(ev2.defaultPrevented === true, 'a press on the border band IS claimed');
  mouse(w2, d2, 'mouseup', 0, 0);

  ok(w2.FloatPanel.resize(null, {}) === false, 'no panel, no binding, no throw');
}

console.log(fails ? ('FAILED ' + fails) : 'float_panel_selfcheck: all ok');
process.exit(fails ? 1 : 0);
