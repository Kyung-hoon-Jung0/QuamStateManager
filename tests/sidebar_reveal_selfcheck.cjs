'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/app.js'), 'utf8');
let failures = 0;
function ok(value, message) {
  console.log((value ? 'PASS ' : 'FAIL ') + message);
  if (!value) failures++;
}
async function run(mode) {
  const dom = new JSDOM('<!doctype html><body><header class="topbar"></header><div class="app-layout"><aside class="sidebar"><nav class="sidebar-nav"><a class="active" href="/journal">Journal</a></nav></aside></div></body>', {
    url: 'http://localhost/journal', runScripts: 'outside-only', pretendToBeVisual: true
  });
  const win = dom.window;
  const doc = win.document;
  const calls = [];
  const errors = [];
  win.addEventListener('error', event => { errors.push(String(event.error || event.message)); event.preventDefault(); });
  win.htmx = { ajax() {}, process() {}, on() {} };
  win.fetch = () => new win.Promise(() => {});
  win.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
  const sb = doc.querySelector('.sidebar');
  const link = doc.querySelector('a');
  let linkRect = { top: 300, bottom: 320 };
  sb.getBoundingClientRect = () => ({ top: 60, bottom: 260 });
  link.getBoundingClientRect = () => linkRect;
  doc.querySelector('.topbar').getBoundingClientRect = () => ({ top: 0, bottom: 60, height: 60, width: 1000 });
  win.Element.prototype.scrollIntoView = function(options) {
    calls.push({ element: this, block: options && options.block,
      published: doc.documentElement.style.getPropertyValue('--topbar-height') });
  };
  // The window realm also bridges every global read bare by the shipped script.
  new win.Function(source).call(win);
  await new Promise(resolve => win.setTimeout(resolve, 100));
  if (mode === 'decide') {
    const reveal = win.SidebarReveal;
    ok(!!reveal && typeof reveal.decide === 'function', 'decide API exists');
    if (reveal) {
      const box = { top: 60, bottom: 260 };
      for (const [name, rect, expected] of [
        ['inside', { top: 80, bottom: 100 }, null],
        ['tolerance', { top: 59.5, bottom: 260.5 }, null],
        ['below', { top: 300, bottom: 320 }, 'center'],
        ['above', { top: 10, bottom: 30 }, 'center'],
        ['clipped bottom', { top: 250, bottom: 270 }, 'center'],
        ['clipped top', { top: 50, bottom: 70 }, 'center']
      ]) ok(reveal.decide(box, rect) === expected, 'decide ' + name);
    }
  } else if (mode === 'load') {
    ok(calls.length === 1 && calls[0].element === link && calls[0].block === 'center',
      'load reveals off-screen active link once with center');
    ok(calls.length === 1 && calls[0].published === '60px', 'load reveal follows topbar height publish');
  } else if (mode === 'sync') {
    calls.length = 0;
    linkRect = { top: 80, bottom: 100 };
    win.syncSidebarNavActive();
    ok(calls.length === 0, 'sync visible link does not scroll');
    calls.length = 0;
    linkRect = { top: 300, bottom: 320 };
    win.syncSidebarNavActive();
    ok(calls.length === 1 && calls[0].element === link && calls[0].block === 'center',
      'sync off-screen link scrolls with center');
  }
  ok(errors.length === 0, 'no script errors: ' + errors.join('; '));
  dom.window.close();
}
(async () => {
  for (const mode of process.argv[2] ? [process.argv[2]] : ['decide', 'load', 'sync']) await run(mode);
  process.exitCode = failures ? 1 : 0;
})().catch(error => { console.error(error); process.exitCode = 1; });
