/* jsdom selfcheck for the diff workbench's stress-walk fixes (2026-11-03):
 *  DS-2: a sideways-scrolling block ([data-xscroll]) gets a hint naming the
 *        sources out of view, hidden when everything fits (diff-panes.js xHint)
 *  DS-5: a /diff or /compare request that never reached the app leaves a
 *        message and a Retry in the pane (app.js window.diffSendFailed)
 * Run: node tests/diff_xscroll_selfcheck.cjs (driven by tests/test_diff_stress_fixes.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const HEAD = ['A', 'B', 'C', 'D'].map((s, i) => '<th class="dp-pane-head" data-i="' + i + '" data-xcol="' + s + '">' + s + '</th>').join('');
const DOM = '<div id="table-pane"><div id="diff-root"><div class="diff-xscroll-hint" hidden></div>' +
  '<div class="diff-panes-scroll" data-xscroll><table><thead><tr><th class="dp-key-col">Keys</th>' + HEAD + '</tr></thead></table></div></div></div>';
const dom = new JSDOM('<!doctype html><html><body>' + DOM + '</body></html>', { url: 'http://localhost/diff', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;

// geometry: scroller 0..600, keys column 200 wide, panes 200 wide each from x=200
const box = window.document.querySelector('[data-xscroll]');
let scrollLeft = 0, scrollW = 1000;
Object.defineProperty(box, 'clientWidth', { get: () => 600 });
Object.defineProperty(box, 'scrollWidth', { get: () => scrollW });
Object.defineProperty(box, 'scrollLeft', { get: () => scrollLeft, set: v => { scrollLeft = v; } });
box.getBoundingClientRect = () => ({ left: 0, right: 600, width: 600 });
box.querySelector('thead .dp-key-col').getBoundingClientRect = () => ({ left: 0, right: 200, width: 200 });
box.querySelectorAll('[data-xcol]').forEach((el, i) => {
  el.getBoundingClientRect = () => ({ left: 200 + i * 200 - scrollLeft, right: 400 + i * 200 - scrollLeft, width: 200 });
});
box.scrollBy = (o) => { scrollLeft += o.left; };

window.eval(fs.readFileSync(path.join(STATIC, 'diff-panes.js'), 'utf8'));
const hint = window.document.querySelector('.diff-xscroll-hint');
window.DiffPanes.armAll();
ok(!hint.hidden, 'the hint shows when panes overflow');
ok(/C, D are out of view/.test(hint.textContent), 'the hint names the panes out of view');
ok(hint.querySelector('.dxh-left').disabled && !hint.querySelector('.dxh-right').disabled, 'only the right arrow is live at the left edge');
scrollLeft = 400; window.DiffPanes.xHint(box);
ok(/A, B are out of view/.test(hint.textContent), 'after scrolling, the panes under the key column count as hidden');
scrollLeft = 0; scrollW = 600; window.DiffPanes.xHint(box);
ok(hint.hidden, 'the hint hides when everything fits');

// DS-5: extract window.diffSendFailed from app.js and run it in the window realm
const app = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const start = app.indexOf('window.diffSendFailed = function');
const end = app.indexOf('\n};', start);
ok(start > 0 && end > start, 'app.js defines window.diffSendFailed');
window.eval(app.slice(start, end + 3));
const pane = window.document.getElementById('table-pane');
const btn = window.document.createElement('button'); btn.id = 'cmpbtn'; window.document.body.appendChild(btn);
let clicked = 0; btn.addEventListener('click', () => clicked++);
const handled = window.diffSendFailed({ detail: { target: pane, elt: btn, error: 'net::ERR_NO_BUFFER_SPACE',
  requestConfig: { path: '/compare', verb: 'post' } } });
const msg = pane.querySelector('.diff-send-failed');
ok(handled && msg && /did not reach the app/.test(msg.textContent) && /Retry/.test(msg.textContent),
   'a failed compare request leaves a message and a Retry');
msg.querySelector('button').click();
ok(clicked === 1 && !pane.querySelector('.diff-send-failed'), 'Retry re-presses the same control and clears the message');
ok(!window.diffSendFailed({ detail: { target: pane, requestConfig: { path: '/state/drift', verb: 'get' } } }),
   'an unrelated request is left to the generic toast');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('all ok');
