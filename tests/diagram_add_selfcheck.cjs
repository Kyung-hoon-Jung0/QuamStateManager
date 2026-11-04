/* The chip-diagram add door against the REAL topo-graph.js + component-map.js
 * + diagram-add.js under jsdom.
 *
 * Pins:
 *  - renderLayout draws the two ghost stones ONLY when asked (opts.ghost), in
 *    an extra column, and they never displace a qubit stone;
 *  - a click on "+" opens the qubit panel (no inspector request); while it is
 *    open a click on a stone re-opens it on THAT stone's feedline;
 *  - "+ pair" arms a two-stone pick: the first click marks one stone, the
 *    second opens the pair panel with control = first, target = second, and
 *    neither click opens the inspector; a second click on a picked stone
 *    un-picks it;
 *  - Cancel disarms (a stone click opens the inspector again);
 *  - typing a drive frequency re-proposes ports once per pause (debounced),
 *    and only the newest answer lands.
 *
 * Run: node tests/diagram_add_selfcheck.cjs   (driven by tests/test_diagram_add_ui.py)
 */
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
const read = (f) => fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'static', f), 'utf8');
const SRC = read('topo-graph.js') + '\n;\n' + read('component-map.js') + '\n;\n' + read('diagram-add.js');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } }

const TOPO = {
  nodes: [
    { id: 'q1', grid_location: '0,0', rr_port: 'con1/1/1' },
    { id: 'q2', grid_location: '1,0', rr_port: 'con1/1/1' },
    { id: 'q3', grid_location: '0,1', rr_port: 'con1/2/1' },
  ],
  edges: [{ pair_id: 'q1-q2', source: 'q1', target: 'q2', directed: false, has_coupler: false }],
};

function world(add) {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane">' +
    '<details class="cmap" id="component-map" data-highlight="qubits" data-cell="120"' +
    (add ? ' data-cm-add="1"' : '') + ' open><summary>m</summary><div class="cmap-body"></div></details>' +
    (add ? '<div id="cm-add-panel"></div>' : '') + '<div id="inspector-pane"></div></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win._ajax = [];
  win.htmx = { ajax: function () { win._ajax.push(Array.prototype.slice.call(arguments)); return win.Promise.resolve(); } };
  win._fetch = [];
  win._fetchAnswers = [];
  win.fetch = function (url) {
    url = String(url);
    win._fetch.push(url);
    if (url.indexOf('/api/topology') === 0) {
      return win.Promise.resolve({ ok: true, json: () => win.Promise.resolve(JSON.parse(JSON.stringify(TOPO))) });
    }
    return new win.Promise(function (res) { win._fetchAnswers.push(res); });
  };
  new win.Function(SRC).call(win);
  win.ComponentMap.mount(win.document.getElementById('component-map'));
  return win;
}

const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
function click(win, el) { el.dispatchEvent(new win.MouseEvent('click', { bubbles: true })); }
const stone = (win, id) => win.document.querySelector('[data-cm="q:' + id + '"] .cm-stone');
const urls = (win) => win._ajax.map((a) => a[1]);

(async function () {
  // ── the drawing ───────────────────────────────────────────────────────────
  {
    const w0 = world(false);
    await tick(); await tick();
    ok(!w0.document.querySelector('.cm-dir-ghost'), 'no ghost without data-cm-add');
    const w = world(true);
    await tick(); await tick();
    const ghosts = w.document.querySelectorAll('.cm-dir-ghost');
    ok(ghosts.length === 2, 'two ghost stones: ' + ghosts.length);
    ok(ghosts[0].getAttribute('data-cm') === 'add:qubit' && ghosts[1].getAttribute('data-cm') === 'add:pair',
       'the ghosts are add:qubit and add:pair');
    const svg = w.document.querySelector('svg.cm-svg');
    const gx = +ghosts[0].querySelector('circle').getAttribute('cx');
    let maxStone = 0;
    w.document.querySelectorAll('.cm-stone').forEach((c) => { maxStone = Math.max(maxStone, +c.getAttribute('cx')); });
    ok(gx > maxStone, 'the ghost column sits right of every stone');
    ok(+svg.getAttribute('width') > gx, 'the svg grew to hold the ghost column');
    ok(w.document.querySelectorAll('.cm-stone').length === 3, 'the qubit stones are unchanged');
  }

  // ── "+": open the qubit panel; a stone picks its feedline ───────────────
  {
    const w = world(true);
    await tick(); await tick();
    click(w, stone(w, 'q1'));
    ok(urls(w)[0] === '/qubit/q1', 'disarmed: a stone opens the inspector (' + urls(w)[0] + ')');
    w._ajax.length = 0;
    click(w, w.document.querySelector('[data-cm="add:qubit"] circle'));
    ok(urls(w).length === 1 && urls(w)[0] === '/diagram-add/panel?kind=qubit', 'the + opens the qubit panel');
    click(w, stone(w, 'q3'));
    ok(urls(w)[1] === '/diagram-add/panel?kind=qubit&feedline_of=q3', 'a stone moves the add to its feedline: ' + urls(w)[1]);
    ok(urls(w).every((u) => u.indexOf('/qubit/') !== 0), 'no inspector request while adding');
    // Cancel disarms
    w.document.getElementById('cm-add-panel').innerHTML = '<button data-cm-add-cancel>Cancel</button>';
    click(w, w.document.querySelector('[data-cm-add-cancel]'));
    w._ajax.length = 0;
    click(w, stone(w, 'q2'));
    ok(urls(w)[0] === '/qubit/q2', 'after Cancel a stone opens the inspector again');
  }

  // ── "+ pair": two stones ────────────────────────────────────────────────
  {
    const w = world(true);
    await tick(); await tick();
    click(w, w.document.querySelector('[data-cm="add:pair"] circle'));
    ok(urls(w).length === 0, 'arming the pair pick sends nothing');
    ok(/control qubit/.test(w.document.getElementById('cm-add-panel').textContent), 'the panel says what to click');
    click(w, stone(w, 'q3'));
    ok(w.document.querySelector('[data-cm="q:q3"]').classList.contains('cm-dir-addsel'), 'the first stone is marked');
    click(w, stone(w, 'q3'));
    ok(!w.document.querySelector('.cm-dir-addsel'), 'a second click un-picks it');
    click(w, stone(w, 'q3'));
    click(w, stone(w, 'q1'));
    ok(urls(w).length === 1 && urls(w)[0] === '/diagram-add/panel?kind=pair&a=q3&b=q1',
       'the second stone opens the pair panel, control first: ' + urls(w)[0]);
    ok(w.document.querySelectorAll('.cm-dir-addsel').length === 2, 'both picked stones are marked');
  }

  // ── typing a frequency: debounced, newest answer wins ────────────────────
  {
    const w = world(true);
    await tick(); await tick();
    w.document.getElementById('cm-add-panel').innerHTML =
      '<form id="cm-add-form"><input name="feedline" value="con1/1/1">' +
      '<input name="drive_rf" data-cm-add-rf value="5"><select name="drive" data-cm-add-drive></select></form>';
    const rf = w.document.querySelector('[data-cm-add-rf]');
    w._fetch.length = 0;
    for (const v of ['5.1', '5.12', '5.123e9']) {
      rf.value = v;
      rf.dispatchEvent(new w.Event('input', { bubbles: true }));
      await tick(50);
    }
    await tick(300);
    const ports = w._fetch.filter((u) => u.indexOf('/api/diagram-add/ports') === 0);
    ok(ports.length === 1 && /drive_rf=5\.123e9/.test(ports[0]), 'one request per pause, with the last value: ' + JSON.stringify(ports));
    rf.value = '6e9';
    rf.dispatchEvent(new w.Event('input', { bubbles: true }));
    await tick(300);
    const sel = w.document.querySelector('[data-cm-add-drive]');
    const answer = (port) => ({ ok: true, json: () => w.Promise.resolve({ drive: [{ con: 'con1', fem: 3, port: port, label: 'p' + port, why: 'x' }], flux: [] }) });
    // the newer request answers first, the older one late: the late one must not land
    w._fetchAnswers[1](answer(7));
    await tick(); await tick();
    w._fetchAnswers[0](answer(2));
    await tick(); await tick();
    ok(sel.options.length === 1 && sel.options[0].value === 'con1/3/7', 'only the newest answer lands: ' + (sel.options[0] && sel.options[0].value));
  }

  if (fails) { console.error(fails + ' of ' + n + ' checks failed'); process.exit(1); }
  console.log('all checks passed (' + n + ' assertions)');
})();
