// QA chipstatus-r2-17 (review) -- ONE row, two renderers. The Trends
// typeahead builds its `.topo-trend-sug` rows in chip-status.js (suggest());
// the unmatched slot in _topo_trends.html builds the same rows in Jinja so a
// typed fragment can be charted with one press. This driver renders the JS
// half through the REAL suggest() (its fetch answered with the rows the
// server's /topology/trends/paths returned) and parses the server half the
// pytest driver hands it, and prints both as normalised rows -- what a user
// can see or press: class, data-path, title, onclick, the text, the count.
//
// Run by tests/test_trends_all_entities.py::test_the_two_row_renderers_agree
//   node tests/trends_sug_parity.cjs <input.json>   (needs jsdom; exit 2 without)
// input.json: {"rows": [...], "server_html": "<the unmatched slot>"}
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
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');
const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

function norm(btn) {
  const n = btn.querySelector('.topo-trend-sug-n');
  return {
    cls: btn.className,
    path: btn.getAttribute('data-path'),
    title: btn.getAttribute('title'),
    onclick: btn.getAttribute('onclick'),
    type: btn.getAttribute('type'),
    text: btn.textContent.replace(/\s+/g, ' ').trim(),
    count: n ? n.textContent.replace(/\s+/g, ' ') : null,
    kids: Array.prototype.map.call(btn.children, (c) => c.tagName + '.' + c.className).join(','),
  };
}

(async function main() {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane">'
    + '<div id="topo-trends"><input id="topo-trend-path"><div id="topo-trend-suggest" hidden></div></div>'
    + '</div><div id="server"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  // suggest() calls a BARE fetch: the mock lives in the realm the code runs in
  win.__rows = input.rows;
  win.eval('window.fetch = function () { return Promise.resolve({ json: function () {'
         + ' return Promise.resolve(window.__rows); } }); };');
  new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                   + read('chip-status.js')).call(win);
  win.ChipTrends.suggest('zz');
  await new Promise((r) => setTimeout(r, 400));          // its 220 ms debounce + the fetch
  const js = Array.prototype.map.call(
    win.document.querySelectorAll('#topo-trend-suggest .topo-trend-sug'), norm);
  const host = win.document.getElementById('server');
  host.innerHTML = input.server_html;
  const server = Array.prototype.map.call(host.querySelectorAll('.topo-trend-sug'), norm);
  process.stdout.write(JSON.stringify({ js: js, server: server }));
  process.exit(0);
})().catch(function (e) { console.error('threw ' + (e && e.stack || e)); process.exit(1); });
