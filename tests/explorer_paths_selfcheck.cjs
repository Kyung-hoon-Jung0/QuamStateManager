// Behavioral check for the dot-form list-element path grammar (app.js):
//  - renderJsonTree materialises list elements with dot-form data-paths (a.b.0.1)
//  - tree search finds + materialises element rows via the same grammar
//  - _collectDiffPairs: equal-length arrays -> per-element dot entries;
//    length mismatch -> ONE whole-array entry
//  - _ancestorPaths: plain dot accumulation
//  - element click-to-edit POSTs the dot-form path to /field/edit
//  - a rejected edit renders the server's reason via _showEditError (the old
//    red-flash-only swallowed it)
//
// Run: node tests/explorer_paths_selfcheck.cjs   (needs jsdom)
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

const ROOT = path.join(__dirname, '..');
const APP_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }
function tick(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="tree"></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win._fetchCalls = [];
  win._fetchImpl = function () {
    return Promise.resolve({ ok: true, json: function () {
      return Promise.resolve({ ok: true });
    } });
  };
  win.fetch = function (url, opts) {
    win._fetchCalls.push({ url: url, opts: opts });
    return win._fetchImpl(url, opts);
  };
  new win.Function(APP_JS).call(win);
  return win;
}

const DATA = {
  qubits: {
    qA1: {
      resonator: {
        confusion_matrix: [[0.98, 0.02], [0.03, 0.97]],
        time_of_flight: 376
      }
    }
  },
  ports: { mw_outputs: { con1: { '1': { '2': { band: 2 } } } } }
};

function expandAll(win, container) {
  // click every collapsed toggle until none remain (materialises lazily)
  for (var round = 0; round < 12; round++) {
    var toggles = container.querySelectorAll('.tree-toggle.collapsed');
    if (!toggles.length) break;
    toggles.forEach(function (t) { t.click(); });
  }
}

(async function main() {

  // P1: materialised list elements carry dot-form data-paths.
  {
    const win = makeWorld();
    win.renderJsonTree('tree', DATA, { defaultDepth: 1 });
    const container = win.document.getElementById('tree');
    expandAll(win, container);
    ok(!!container.querySelector('.tree-node[data-path="qubits.qA1.resonator.confusion_matrix.0.1"]'),
      'P1: matrix element has dot-form data-path a.b.0.1');
    ok(!container.querySelector('.tree-node[data-path*="["]'),
      'P1: no bracket-form data-path anywhere in the tree');
    ok(!!container.querySelector('.tree-node[data-path="ports.mw_outputs.con1.1.2.band"]'),
      'P1: number-keyed dict path renders with the same dot grammar');
  }

  // P2: search materialises + keeps element rows (flat index uses dot form).
  {
    const win = makeWorld();
    win.renderJsonTree('tree', DATA, { defaultDepth: 1 });
    const container = win.document.getElementById('tree');
    win.jsonTreeSearch('tree', '0.97');
    await tick(300);   // past the 200 ms debounce
    const el = container.querySelector('.tree-node[data-path="qubits.qA1.resonator.confusion_matrix.1.1"]');
    ok(!!el, 'P2: searching a matrix value materialises its element row (dot grammar match)');
    if (el) ok(!el.classList.contains('tree-search-hidden'), 'P2: matched element row not hidden');
  }

  // P3: _collectDiffPairs — equal lengths → per-element; mismatch → whole array.
  {
    const win = makeWorld();
    var out = [];
    win._collectDiffPairs([[1, 2], [3, 4]], [[1, 9], [3, 4]], 'm', out);
    ok(out.length === 1 && out[0].dot_path === 'm.0.1' && out[0].value === 9,
      'P3: equal-length nested diff -> single element entry m.0.1');
    out = [];
    win._collectDiffPairs([1, 2], [1, 2, 3], 'arr', out);
    ok(out.length === 1 && out[0].dot_path === 'arr'
      && JSON.stringify(out[0].value) === '[1,2,3]',
      'P3: length mismatch -> ONE whole-array entry');
  }

  // P4: _ancestorPaths — plain dot accumulation.
  {
    const win = makeWorld();
    const anc = win._ancestorPaths('a.b.2.c');
    ok(JSON.stringify(anc) === JSON.stringify(['a', 'a.b', 'a.b.2', 'a.b.2.c']),
      'P4: ancestors of a.b.2.c');
  }

  // P5: element click-to-edit POSTs the dot-form path; rejection shows the reason.
  {
    const win = makeWorld();
    win.renderJsonTree('tree', DATA, { defaultDepth: 1 });
    const container = win.document.getElementById('tree');
    expandAll(win, container);
    const node = container.querySelector('.tree-node[data-path="qubits.qA1.resonator.confusion_matrix.0.0"]');
    ok(!!node, 'P5: element node exists');
    const valEl = node && node.querySelector('.tree-val');
    ok(!!valEl, 'P5: element value span exists');
    if (valEl) {
      win._fetchImpl = function () {
        return Promise.resolve({ ok: true, json: function () {
          return Promise.resolve({ ok: false, error: 'expected number, got str "abc"' });
        } });
      };
      valEl.click();
      const input = valEl.querySelector('input');
      ok(!!input, 'P5: click opens the inline editor');
      if (input) {
        input.value = 'abc';
        input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        await tick(20);
        const call = win._fetchCalls.find(function (c) { return c.url === '/field/edit'; });
        ok(!!call, 'P5: /field/edit POSTed');
        if (call) {
          ok(call.opts.body.indexOf('dot_path=qubits.qA1.resonator.confusion_matrix.0.0') >= 0,
            'P5: dot-form element path in the POST body');
        }
        const chip = node.querySelector('.tree-edit-err');
        ok(!!chip && /expected number/.test(chip.textContent),
          'P5: rejection reason rendered inline (swallowed-error regression pin)');
      }
    }
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('explorer_paths_selfcheck: all checks passed');
  process.exit(0);   // app.js poll intervals keep the loop alive otherwise
})().catch(function (e) { console.error(e); process.exit(1); });
