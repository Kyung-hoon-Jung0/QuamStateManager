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
scrollLeft = 600; box.dispatchEvent(new window.Event('scroll'));
ok(/A, B, C are out of view/.test(hint.textContent), 'a scroll event updates the hint');
ok(hint.querySelector('.dxh-right').disabled && !hint.querySelector('.dxh-left').disabled, 'the right arrow is disabled at the far right');
scrollLeft = 60; window.DiffPanes.xHint(box);
ok(/C, D are out of view/.test(hint.textContent) && !/A,/.test(hint.textContent),
   'a pane less than half hidden is not announced (A is 70% visible, D is 30%)');
scrollLeft = 0; scrollW = 1000;
box.querySelectorAll('[data-xcol]').forEach((el, i) => {
  el.getBoundingClientRect = () => ({ left: 200 + i * 90, right: 290 + i * 90, width: 90 });
});
window.DiffPanes.xHint(box);
ok(hint.hidden, 'no hint when the block overflows but no pane is hidden');
scrollW = 600; window.DiffPanes.xHint(box);
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
const calls = [];
window.htmx = { ajax: (verb, p, ctx) => { calls.push({ verb, p, ctx }); return Promise.resolve(); } };
const handled = window.diffSendFailed({ detail: { target: pane, elt: btn, error: 'htmx:sendError',
  requestConfig: { path: '/compare', verb: 'post' } } });
const msg = pane.querySelector('.diff-send-failed');
ok(handled && msg && /did not reach the app/.test(msg.textContent) && /Retry/.test(msg.textContent),
   'a failed compare request leaves a message and a Retry');
ok(!/htmx:sendError/.test(msg.textContent), 'the message carries no event name');
msg.querySelector('button').click();
ok(calls.length === 1 && calls[0].verb === 'POST' && calls[0].ctx.source === btn && !pane.querySelector('.diff-send-failed'),
   'Retry re-issues the POST through its button and clears the message');
// F1: the picker FORM (GET /diff, refs in its fields) must retry through the form
const root = window.document.getElementById('diff-root');
const form = window.document.createElement('form'); form.setAttribute('hx-get', '/diff'); form.setAttribute('hx-swap', 'outerHTML');
root.appendChild(form);
calls.length = 0;
window.diffSendFailed({ detail: { target: root, elt: form, requestConfig: { path: '/diff', verb: 'get',
  parameters: { a: 'x', b: 'y', c: 'z' } }, pathInfo: { finalRequestPath: '/diff?a=x&b=y&c=z' } } });
root.querySelector('.diff-send-failed button').click();
ok(calls.length === 1 && calls[0].ctx.source === form && calls[0].ctx.swap === 'outerHTML' && calls[0].ctx.target === root,
   'a picker GET retries through the form, so its sources ride along');
// a detached source falls back to the full request path, never the bare one
const gone = window.document.createElement('form');
calls.length = 0;
window.diffSendFailed({ detail: { target: root, elt: gone, requestConfig: { path: '/diff', verb: 'get' },
  pathInfo: { finalRequestPath: '/diff?a=x&b=y&c=z' } } });
root.querySelector('.diff-send-failed button').click();
ok(calls.length === 1 && calls[0].p === '/diff?a=x&b=y&c=z' && !calls[0].ctx.source, 'a detached source retries the full request path');
const other = window.document.createElement('div'); other.id = 'inspector-pane'; window.document.body.appendChild(other);
ok(!window.diffSendFailed({ detail: { target: other, requestConfig: { path: '/diff', verb: 'get' } } }),
   'a diff request into another target is left to the generic toast');
ok(!window.diffSendFailed({ detail: { target: pane, requestConfig: { path: '/state/drift', verb: 'get' } } }),
   'an unrelated request is left to the generic toast');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('all ok');
