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

function world(add, highlight) {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="table-pane">' +
    '<details class="cmap" id="component-map" data-highlight="' + (highlight || 'qubits') + '" data-cell="120"' +
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
      '<div data-cm-box><form id="cm-add-form"><input name="feedline" value="con1/1/1">' +
      '<input name="drive_rf" data-cm-add-rf value="5"><input type="hidden" name="drive" data-cm-port-value="drive" value="">' +
      '<div role="listbox" data-cm-port-list="drive" data-io="out" data-role="xy" data-cm-add-drive></div></form></div>';
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
    const list = w.document.querySelector('[data-cm-add-drive]');
    const answer = (port) => ({ ok: true, json: () => w.Promise.resolve({ drive: [{ con: 'con1', fem: 3, port: port, label: 'p' + port, why: 'x' }], flux: [] }) });
    // the newer request answers first, the older one late: the late one must not land
    w._fetchAnswers[1](answer(7));
    await tick(); await tick();
    w._fetchAnswers[0](answer(2));
    await tick(); await tick();
    const opts = list.querySelectorAll('[role="option"]');
    ok(opts.length === 1 && opts[0].getAttribute('data-value') === 'con1/3/7', 'only the newest answer lands: ' + (opts[0] && opts[0].getAttribute('data-value')));
    ok(w.document.querySelector('input[name="drive"]').value === 'con1/3/7', 'the re-proposed list chooses its first port');
  }

  // ── "+ pair" stays pressed and says what to do, in words ─────────────────
  {
    const w = world(true);
    await tick(); await tick();
    const g = w.document.querySelector('[data-cm="add:pair"]');
    click(w, g);
    ok(g.getAttribute('aria-pressed') === 'true' && g.classList.contains('cm-dir-ghost--armed'), '"+ pair" shows it is armed');
    const h = w.document.querySelector('[data-cm-add-hint]');
    ok(h && /click the control qubit/.test(h.textContent), 'the pick says what to do: ' + (h && h.textContent));
    click(w, w.document.querySelector('[data-cm="add:pair"]'));
    ok(g.getAttribute('aria-pressed') === 'false' && !w.document.querySelector('[data-cm-add-hint]'), 'a second press un-arms it');
  }

  // ── a port list lights its port on the rack: chosen, hovered, arrowed ────
  {
    const w = world(true);
    await tick(); await tick();
    const cell = (con, slot, port, io) => '<g class="iw-port" data-con="' + con + '" data-slot="' + slot +
      '" data-port="' + port + '" data-io="' + io + '"><circle cx="10" cy="10" r="5"></circle></g>';
    w.document.getElementById('cm-add-panel').innerHTML =
      '<div data-cm-box><form id="cm-add-form"><input name="kind" value="pair"><input name="dry" value="1">' +
      '<input type="hidden" name="flux" data-cm-port-value="flux" value="con1/5/7">' +
      '<div role="listbox" tabindex="0" data-cm-port-list="flux" data-io="out" data-role="coupler">' +
      '<div role="option" id="o1" data-value="con1/5/7" aria-selected="true">a</div>' +
      '<div role="option" id="o2" data-value="con1/5/8" aria-selected="false">b</div>' +
      '<div role="option" id="o3" data-value="con1/6/1" aria-selected="false">c</div></div></form>' +
      '<div data-cm-rack><svg>' + cell('con1', 5, 7, 'out') + cell('con1', 5, 8, 'out') + cell('con1', 5, 8, 'in') +
      cell('con1', 6, 1, 'out') + '</svg></div><span data-cm-rack-note></span>' +
      '<div id="cm-add-review" data-cm-review><button data-cm-add-stage>Stage</button></div></div>';
    const DA = w.DiagramAdd;
    const rack = w.document.querySelector('[data-cm-rack]');
    const id = (el) => el ? el.getAttribute('data-con') + '/' + el.getAttribute('data-slot') + '/' + el.getAttribute('data-port') + ':' + el.getAttribute('data-io') : null;
    ok(id(DA.portCell(rack, 'con1/5/8', 'out')) === 'con1/5/8:out' && id(DA.portCell(rack, 'con1/5/8', 'in')) === 'con1/5/8:in',
       'a port value maps to its own rack cell, output vs input kept apart');
    DA.relight(rack, null);
    const lit = (cls) => Array.prototype.map.call(rack.querySelectorAll('.' + cls), id).join(',');
    ok(lit('cm-lit-sel') === 'con1/5/7:out', 'the chosen port is lit: ' + lit('cm-lit-sel'));
    w.document.getElementById('o2').dispatchEvent(new w.MouseEvent('mouseover', { bubbles: true }));
    ok(lit('cm-lit-hover') === 'con1/5/8:out' && lit('cm-lit-sel') === 'con1/5/7:out', 'the hovered option lights too: ' + lit('cm-lit-hover'));
    ok(rack.querySelector('.iw-port.cm-lit-hover').getAttribute('data-cm-lit-role') === 'coupler', 'the light carries the field role');
    ok(rack.querySelectorAll('.cm-lit-ring--hover').length === 1, 'one ring marks the hovered port');
    w.document.querySelector('[data-cm-port-list]').dispatchEvent(new w.MouseEvent('mouseout', { bubbles: true, relatedTarget: w.document.body }));
    ok(lit('cm-lit-hover') === '' && rack.querySelectorAll('.cm-lit-ring--hover').length === 0, 'leaving the list returns to the chosen one');
    const lb = w.document.querySelector('[data-cm-port-list]');
    lb.focus();
    const key = (k) => lb.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true }));
    key('ArrowDown'); key('ArrowDown');
    ok(lit('cm-lit-hover') === 'con1/6/1:out', 'arrow focus counts as hover: ' + lit('cm-lit-hover'));
    ok(!!w.document.querySelector('[data-cm-add-stage]'), 'arrowing does not choose');
    key('Enter');
    ok(w.document.querySelector('input[name="flux"]').value === 'con1/6/1' && lit('cm-lit-sel') === 'con1/6/1:out',
       'Enter chooses, and the chosen light follows');
    ok(!w.document.querySelector('[data-cm-add-stage]'), 'a new port requires a fresh review');
    w.htmx.values = (form) => Object.fromEntries(new w.FormData(form));
    w.document.getElementById('cm-add-review').innerHTML = '<button data-cm-add-stage>Stage</button>';
    click(w, w.document.querySelector('[data-cm-add-stage]'));
    ok(w._ajax[w._ajax.length - 1][2].values.override === undefined, 'a plain stage never asks to override the env check');
    w.document.getElementById('cm-add-review').innerHTML = '<button data-cm-add-stage data-override="1">Stage</button>';
    click(w, w.document.querySelector('[data-cm-add-stage]'));
    const call = w._ajax[w._ajax.length - 1];
    ok(call[0] === 'POST' && call[1] === '/diagram-add/stage' &&
       call[2].values.flux === 'con1/6/1' && call[2].values.dry === '0' && call[2].values.override === '1',
       'staging submits the chosen port, dry=0, and the override only from its own button');
    ok(w.document.querySelector('input[name="dry"]').value === '1', 'the form itself stays in review mode');
  }

  // ── a removal lights the ports it frees ──────────────────────────────────
  {
    const w = world(true);
    await tick(); await tick();
    w.document.body.insertAdjacentHTML('beforeend', '<div id="cm-rm-slot"><div data-cm-box>' +
      '<div id="cm-rm-rack" data-cm-rack data-light=\'[{"con":"con1","fem":3,"port":7,"io":"out"}]\'></div></div></div>');
    w.renderInstrumentWiring = function (cid) {
      w.document.getElementById(cid).innerHTML = '<svg><g class="iw-port" data-con="con1" data-slot="3" data-port="7" data-io="out"><circle cx="1" cy="1" r="2"></circle></g>' +
        '<g class="iw-port" data-con="con1" data-slot="3" data-port="6" data-io="out"><circle cx="1" cy="1" r="2"></circle></g></svg>';
    };
    w._fetch.length = 0;
    w._fetchAnswers.length = 0;
    w.document.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { detail: { target: w.document.getElementById('cm-rm-slot') } }));
    await tick();
    const i = w._fetch.indexOf('/api/instrument/data');
    ok(i >= 0, 'the rack asks for the instrument data');
    w._fetchAnswers[i]({ ok: true, json: () => w.Promise.resolve({ instrument: {}, wiring: {} }) });
    await tick(); await tick();
    const f = w.document.querySelectorAll('#cm-rm-rack .cm-lit-freed');
    ok(f.length === 1 && f[0].getAttribute('data-port') === '7', 'exactly the freed port is lit');
  }

  // ── structural undo/redo refreshes map pages; value undo does not ───────
  {
    const w = world(true, 'qubits');
    await tick(); await tick();
    w._ajax.length = 0;
    w.document.dispatchEvent(new w.CustomEvent('cellsReverted', { detail: {
      message: 'Undone',
      entries: [{ dot_path: 'qubits.q101', created: true }],
    } }));
    await tick();
    ok(urls(w).includes('/qubits'),
      'structural undo on qubits refreshes /qubits: ' + JSON.stringify(urls(w)));
  }
  {
    const w = world(true, 'pairs');
    await tick(); await tick();
    w._ajax.length = 0;
    w.document.dispatchEvent(new w.CustomEvent('cellsReverted', { detail: {
      message: 'Redone',
      entries: [{ dot_path: 'qubit_pairs.q1-q3', deleted: true }],
    } }));
    await tick();
    ok(urls(w).includes('/pairs'),
      'structural redo on pairs refreshes /pairs: ' + JSON.stringify(urls(w)));
  }
  {
    const w = world(true, 'qubits');
    await tick(); await tick();
    w._ajax.length = 0;
    w.document.dispatchEvent(new w.CustomEvent('cellsReverted', { detail: {
      message: 'Undone',
      entries: [{ dot_path: 'qubits.q1.T1', old_value_str: '12e-6' }],
    } }));
    await tick();
    ok(urls(w).every((u) => u !== '/qubits'),
      'non-structural undo does not refresh the map page: ' + JSON.stringify(urls(w)));
  }

  if (fails) { console.error(fails + ' of ' + n + ' checks failed'); process.exit(1); }
  console.log('all checks passed (' + n + ' assertions)');
})();
