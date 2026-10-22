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
// Mirrors base.html: the sidebar is <aside id="sidebar"> inside .app-layout.
const FIXTURE = '<!doctype html><body class="app-shell"><header class="topbar"></header>' +
  '<div class="app-layout"><aside id="sidebar" style="overflow-y:auto">' +
  '<nav class="sidebar-nav"><details id="grp" open><summary>G</summary>' +
  '<a class="active" href="/journal">Journal</a></details></nav></aside>' +
  '<main id="main"><div id="table-pane"></div></main></div></body>';

async function run(mode) {
  const dom = new JSDOM(FIXTURE, {
    url: 'http://localhost/journal', runScripts: 'outside-only', pretendToBeVisual: true
  });
  const win = dom.window;
  const doc = win.document;
  const errors = [];
  const foreign = [];   // every scroll that is not the sidebar's own scrollTop
  win.addEventListener('error', event => { errors.push(String(event.error || event.message)); event.preventDefault(); });
  win.htmx = { ajax() {}, process() {}, on() {} };
  win.fetch = () => new win.Promise(() => {});
  win.matchMedia = () => ({ matches: false, addListener() {}, addEventListener() {} });
  const sb = doc.getElementById('sidebar');
  const layout = doc.querySelector('.app-layout');
  const link = doc.querySelector('a.active');
  let linkRect = { top: 300, bottom: 320 };
  let sbWidth = 240;
  let scrollHeight = 1000;
  let sbTop = 0;
  const writes = [];
  sb.getBoundingClientRect = () => ({ top: 60, bottom: 260, width: sbWidth, height: 200 });
  link.getBoundingClientRect = () => linkRect;
  Object.defineProperty(sb, 'scrollHeight', { get: () => scrollHeight });
  Object.defineProperty(sb, 'clientHeight', { get: () => 200 });
  Object.defineProperty(sb, 'scrollTop', { get: () => sbTop, set: v => { sbTop = v; writes.push(v); } });
  for (const el of [doc.getElementById('main'), doc.getElementById('table-pane'), layout,
    doc.body, doc.documentElement, doc.querySelector('nav'), doc.getElementById('grp')]) {
    Object.defineProperty(el, 'scrollTop', { get: () => 0, set: v => foreign.push((el.id || el.tagName) + '.scrollTop=' + v) });
  }
  win.scrollTo = () => foreign.push('window.scrollTo');
  win.scrollBy = () => foreign.push('window.scrollBy');
  win.Element.prototype.scrollIntoView = function() { foreign.push('scrollIntoView'); };
  doc.querySelector('.topbar').getBoundingClientRect = () => ({ top: 0, bottom: 60, height: 60, width: 1000 });
  new win.Function(source).call(win);
  await new Promise(resolve => win.setTimeout(resolve, 100));
  const R = win.SidebarReveal;
  const again = () => { writes.length = 0; sbTop = 0; win.syncSidebarNavActive(); };
  if (mode === 'decide') {
    ok(!!R && typeof R.decide === 'function', 'decide API exists');
    const box = { top: 60, bottom: 260 };
    for (const [name, rect, expected] of [
      ['inside', { top: 80, bottom: 100 }, null],
      ['tolerance', { top: 59.5, bottom: 260.5 }, null],
      ['below', { top: 300, bottom: 320 }, 'center'],
      ['above', { top: 10, bottom: 30 }, 'center'],
      ['clipped bottom', { top: 250, bottom: 270 }, 'center'],
      ['clipped top', { top: 50, bottom: 70 }, 'center']
    ]) ok(R.decide(box, rect) === expected, 'decide ' + name);
    ok(R.target(0, 800, box, { top: 300, bottom: 320 }) === 150, 'target centres link');
    ok(R.target(700, 800, box, { top: 900, bottom: 920 }) === 800, 'target clamps to max');
    ok(R.target(10, 800, box, { top: -500, bottom: -480 }) === 0, 'target clamps to 0');
  } else if (mode === 'load') {
    ok(writes.length === 1 && writes[0] === 150, 'load centres off-screen link via sidebar scrollTop: ' + writes);
    ok(foreign.length === 0, 'load scrolls nothing but the sidebar: ' + foreign);
  } else if (mode === 'sync') {
    linkRect = { top: 80, bottom: 100 }; again();
    ok(writes.length === 0, 'sync visible link does not scroll');
    linkRect = { top: 300, bottom: 320 }; again();
    ok(writes.length === 1 && writes[0] === 150, 'sync off-screen link centres');
    linkRect = { top: 2000, bottom: 2020 }; again();
    ok(writes.length === 1 && writes[0] === 800, 'sync clamps to the scroll range');
    ok(foreign.length === 0, 'sync scrolls nothing but the sidebar: ' + foreign);
  } else if (mode === 'collapsed') {
    layout.classList.add('sidebar-collapsed'); again();
    ok(writes.length === 0, 'collapsed sidebar is not scrolled');
    layout.classList.remove('sidebar-collapsed');
    sbWidth = 0; again();
    ok(writes.length === 0, 'zero-width sidebar is not scrolled');
    sbWidth = 240;
    layout.classList.add('sidebar-collapsed');
    writes.length = 0; sbTop = 0;
    win.toggleSidebar();   // re-expand
    await new Promise(resolve => win.setTimeout(resolve, 100));
    ok(writes.length === 1 && writes[0] === 150, 're-expand reveals once: ' + writes);
    ok(foreign.length === 0, 'collapsed paths scroll nothing else: ' + foreign);
  } else if (mode === 'details') {
    linkRect = { top: 0, bottom: 0 }; again();
    ok(writes.length === 0, 'link in a closed details (zero rect) is not scrolled');
  } else if (mode === 'noscroll') {
    scrollHeight = 200; again();
    ok(writes.length === 0, 'sidebar without overflow is not scrolled');
    scrollHeight = 1000; sb.style.overflowY = 'visible'; again();
    ok(writes.length === 0, 'non-scroller sidebar is not scrolled');
  }
  ok(errors.length === 0, 'no script errors: ' + errors.join('; '));
  dom.window.close();
}
(async () => {
  for (const mode of process.argv[2] ? [process.argv[2]] : ['decide', 'load', 'sync', 'collapsed', 'details', 'noscroll']) await run(mode);
  process.exitCode = failures ? 1 : 0;
})().catch(error => { console.error(error); process.exitCode = 1; });
