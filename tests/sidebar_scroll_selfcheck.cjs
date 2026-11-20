// The sidebar run tree keeps what the reader sees across a refetch
// (app.js window.SidebarScroll, docs/144).
//
// Scrolled into the runs: the anchor is the first row on screen, so a run
// inserted above it does not push it down. Tree start on screen (the reader
// is at the top): nothing above the first row moves, so the scroll position
// stays as it is and a new run shows at the top -- anchoring the first row
// there scrolled the sidebar by one row.
//
// Geometry is stubbed (jsdom has no layout): #sidebar's top is at 0, the tree
// starts `treeY` below the sidebar's content top, rows are 32 px.
//
// Run: node tests/sidebar_scroll_selfcheck.cjs   (needs jsdom)
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
const APP_JS = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const ROW = 32;

function world(uids, treeY) {
  const page = '<!DOCTYPE html><html><body><div id="sidebar"><div id="sidebar-tree"></div></div></body></html>';
  const dom = new JSDOM(page, { runScripts: 'outside-only', pretendToBeVisual: true,
                               url: 'http://localhost/datasets' });
  const win = dom.window;
  win.htmx = { ajax: function () {}, process: function () {} };
  win.fetch = function () { return new win.Promise(function () {}); };
  win.IntersectionObserver = function () { this.observe = function () {}; this.disconnect = function () {}; };
  new win.Function(APP_JS).call(win);
  const doc = win.document;
  const sc = doc.getElementById('sidebar'), tree = doc.getElementById('sidebar-tree');
  const g = { S: 0 };
  Object.defineProperty(sc, 'scrollTop', { configurable: true, get: () => g.S, set: (v) => { g.S = v; } });
  sc.getBoundingClientRect = () => ({ top: 0, bottom: 600, left: 0, right: 300, width: 300, height: 600 });
  tree.getBoundingClientRect = () => ({ top: treeY - g.S, bottom: treeY - g.S + 2000, left: 0, right: 300,
                                        width: 300, height: 2000 });
  g.fill = (list) => {
    tree.innerHTML = list.map((u) => '<span class="tree-entry-click" data-uid="' + u + '"></span>').join('');
    Array.prototype.forEach.call(tree.querySelectorAll('[data-uid]'), (el, i) => {
      el.getBoundingClientRect = () => {
        const top = treeY - g.S + i * ROW;
        return { top, bottom: top + ROW, left: 0, right: 300, width: 300, height: ROW };
      };
    });
  };
  g.y = (uid) => tree.querySelector('[data-uid="' + uid + '"]').getBoundingClientRect().top;
  g.fill(uids);
  g.win = win;
  return g;
}

const RUNS = [];
for (let i = 40; i >= 1; i--) RUNS.push('f:' + i);

// 1. Tree start on screen: the refetch that adds a run on top moves nothing.
{
  const g = world(RUNS, 120);
  const a = g.win.SidebarScroll.capture();
  g.fill(['f:41'].concat(RUNS));
  g.win.SidebarScroll.restore(a);
  ok(g.S === 0, 'at the top the sidebar does not scroll when a run is added (scrollTop ' + g.S + ')');
  ok(g.y('f:41') === 120, 'and the new run shows at the top of the tree');
}

// 2. Scrolled into the runs: the row being read keeps its place.
{
  const g = world(RUNS, 120);
  g.S = 500;
  const first = 'f:' + (40 - Math.ceil((500 - 120 - ROW + 1) / ROW));
  const y0 = g.y(first);
  const a = g.win.SidebarScroll.capture();
  ok(a && a.id === first, 'scrolled into the runs the anchor is the first row on screen (' + (a && a.id) + ')');
  g.fill(['f:41'].concat(RUNS));
  g.win.SidebarScroll.restore(a);
  ok(g.y(first) === y0 && g.S === 500 + ROW,
     'a run added above it does not push it down (y ' + y0 + ' -> ' + g.y(first) + ', scrollTop ' + g.S + ')');
}

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('all checks passed (' + asserts + ' assertions)');
