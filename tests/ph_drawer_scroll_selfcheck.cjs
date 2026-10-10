/* S10 walk r4 (P2-11) jsdom selfcheck: the Param History cell drawer is
 * brought into view once its LOADED content is in place.
 *
 * paramHistoryOpenDrawer (app.js) used to call scrollIntoView only on the
 * one-line "Loading..." placeholder; the drawer that replaced it (header plus
 * a 340px chart) then opened below a 21-row grid with only its title on
 * screen. The check records every scrollIntoView on the drawer and what the
 * drawer held at that moment.
 *
 * Run: node tests/ph_drawer_scroll_selfcheck.cjs  (driven by
 *      tests/test_s10_walk_r4_ui.py::test_the_ph_drawer_scrolls_its_loaded_content_into_view)
 */
'use strict';
const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.log('jsdom not installed');
  process.exit(2);
}

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let n = 0, fails = 0;
function ok(cond, msg) {
  n++;
  if (!cond) { fails++; console.log('FAIL: ' + msg); }
}
function until(test, label, ms) {
  const t0 = Date.now();
  return new Promise(function (resolve, reject) {
    (function poll() {
      let v = false;
      try { v = test(); } catch (e) { v = false; }
      if (v) return resolve(v);
      if (Date.now() - t0 > (ms || 5000)) return reject(new Error('timeout: ' + label));
      setTimeout(poll, 5);
    })();
  });
}

async function main() {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="param-history-root" data-active-chip-key="">'
    + '</div><div id="param-history-drawer" style="display:none"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  const scrolls = [];
  win.Element.prototype.scrollIntoView = function (opts) {
    scrolls.push({ id: this.id, html: this.innerHTML, opts: opts });
  };
  const LOADED = '<div class="param-history-drawer-inner"><div class="phd-header">'
    + '<h3>qA1 · T1</h3></div><div id="phd-chart" style="height:340px"></div></div>';
  let release;
  const gate = new win.Promise(function (r) { release = r; });
  win.fetch = function () {
    return gate.then(function () { return { text: function () { return win.Promise.resolve(LOADED); } }; });
  };
  win.htmx = { ajax() {}, trigger() {}, process() {} };
  win.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

  win.paramHistoryOpenDrawer('qA1', 'T1');
  const drawer = win.document.getElementById('param-history-drawer');
  ok(drawer.style.display === 'block', '1a the drawer is shown at once');
  release();
  await until(function () { return /phd-header/.test(drawer.innerHTML); }, 'drawer loaded');
  await new win.Promise(function (r) { setTimeout(r, 20); });
  const onDrawer = scrolls.filter(function (s) { return s.id === 'param-history-drawer'; });
  const loaded = onDrawer.filter(function (s) { return /phd-header/.test(s.html); });
  ok(loaded.length >= 1, '1b the drawer is scrolled into view once its loaded content is in it '
     + '(calls on the drawer: ' + onDrawer.map(function (s) { return /Loading/.test(s.html) ? 'placeholder' : 'loaded'; }).join(', ') + ')');
  ok(loaded.every(function (s) { return s.opts && s.opts.block === 'nearest'; }),
     '1c the loaded-drawer scroll is block:nearest (no jump when it is already on screen)');
  dom.window.close();
}

main().then(function () {
  if (fails) { console.log(fails + ' of ' + n + ' checks FAILED'); process.exit(1); }
  console.log('all ' + n + ' checks passed');
  process.exit(0);
}).catch(function (e) {
  console.log('FAIL: ' + (e && e.stack || e));
  process.exit(1);
});
