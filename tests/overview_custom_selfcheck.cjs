/* Behavioral check for the docs/150 Overview per-panel customization (v1)
 * against the REAL app.js + topo-graph.js + chip-status.js under jsdom.
 *
 * Pins the display-preferences contract:
 *  - default render: every identified tile carries data-tile-id + a kebab,
 *    the ghost "+ Add panel" tile exists, the customized note stays hidden,
 *    localStorage stays EMPTY (no deviation -> nothing stored);
 *  - stat override: the big number switches to the chosen aggregate (same
 *    computeAggregates output — never new math), wears the stat tag, and the
 *    sub line states the complementary aggregates;
 *  - remove hides a tile; a composite tile's popover offers remove ONLY;
 *  - add: the metric list is the chip's REAL metric-record keys (+ cz), an
 *    added tile renders, and one whose values are all null renders the
 *    honest muted "no data" tile — never silently nothing;
 *  - reset restores the defaults and clears the stored key;
 *  - preferences survive a full re-mount (the localStorage round-trip).
 *
 * Run: node tests/overview_custom_selfcheck.cjs   (driven by tests/test_overview_custom.py)
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
const APP_JS = read('app.js');
const TOPO_JS = read('topo-graph.js');
const CS_JS = read('chip-status.js');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

function makeWorld() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body>'
    + '<div id="topo-hero"></div><div id="topo-html-wrap"></div>'
    + '<h3>Overview <button id="ov-settings-btn"></button>'
    + '<span id="ov-custom-note" hidden>customized · <a href="#">reset</a></span></h3>'
    + '<div class="topo-summary-cards" id="topo-overview-tiles"></div>'
    + '</body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.htmx = { ajax: function () {} };
  win.fetch = function () { return new win.Promise(function () {}); };
  new win.Function(APP_JS + '\n;\n' + TOPO_JS + '\n;\n' + CS_JS).call(win);
  return win;
}

// Asymmetric T1 so avg (30µs, the docs/150b default) and median (20µs)
// are distinct numbers.
function topoFixture() {
  function node(id, gl, t1) {
    return {
      id: id, grid_location: gl, T1: t1, gate_fidelity_avg: 0.999,
      metrics: {
        T1: { value: t1 },
        gate_fidelity_avg: { value: 0.999 },
        anharmonicity: { value: null },   // present key, NO values anywhere
      },
    };
  }
  return {
    nodes: [node('q1', '0,0', 1.0e-5), node('q2', '1,0', 2.0e-5), node('q3', '0,1', 6.0e-5)],
    edges: [{ pair_id: 'q1-q2', source: 'q1', target: 'q2', has_cz: true, cz_fidelity: 0.97,
              gate_kind: 'cz', directed: false, active: null, best_gate: 'cz' }],
  };
}

// docs/154: a chip whose 2Q fidelity rows include values the server marked
// unphysical. `physical:false` + `raw_value` is exactly what
// query._gate_fidelity_row emits -- `value` is GONE, which is the safety
// property (no reader can use the number by accident).
function badFixture() {
  function node(id, gl, t1, roGef) {
    return { id: id, grid_location: gl, T1: t1, gate_fidelity_avg: 0.999,
             metrics: { T1: { value: t1 }, gate_fidelity_avg: { value: 0.999 },
                        // `physical:false` with NO raw is what chip_health emits
                        // for a metric whose confusion matrix was refused: there
                        // is no number to show, and it is not "never measured".
                        assignment_fidelity_gef: roGef } };
  }
  const GEF_OK = { value: 0.90, physical: true };
  const GEF_REFUSED = { value: null, raw: null, physical: false, unresolved: false };
  const GEF_ABSENT = { value: null, raw: null, physical: true, unresolved: false };
  function gf(metric, value, extra) {
    return Object.assign({ gate: 'cz_unipolar', metric: metric, level: 'gate',
                           value: value }, extra || {});
  }
  return {
    nodes: [node('q1', '0,0', 1e-5, GEF_REFUSED), node('q2', '1,0', 2e-5, GEF_OK),
            node('q3', '0,1', 3e-5, GEF_OK), node('q4', '1,1', 4e-5, GEF_ABSENT)],
    edges: [
      // clean pair
      { pair_id: 'q1-q2', source: 'q1', target: 'q2', has_cz: true, gate_kind: 'cz',
        gate_fidelities: [gf('InterleavedRB', 0.99)] },
      // one macro dropped, another still usable -> the pair KEEPS its place
      { pair_id: 'q2-q3', source: 'q2', target: 'q3', has_cz: true, gate_kind: 'cz',
        gate_fidelities: [
          Object.assign(gf('InterleavedRB', undefined),
                        { physical: false, raw_value: 1.5345, value: undefined }),
          gf('InterleavedRB', 0.97, { gate: 'cz_flattop' })] },
      // every macro dropped -> the pair leaves the aggregate but stays listed
      { pair_id: 'q3-q4', source: 'q3', target: 'q4', has_cz: true, gate_kind: 'cz',
        gate_fidelities: [
          Object.assign(gf('InterleavedRB', undefined),
                        { physical: false, raw_value: 4.7, value: undefined })] },
    ],
  };
}
function mountBad(win) {
  win.ChipStatus.mount({ topo: badFixture(), rawWiring: {}, defaultThresholds: {},
                         diagFindings: [], metricMeta: {} });
}

function mount(win) {
  win.ChipStatus.mount({ topo: topoFixture(), rawWiring: {}, defaultThresholds: {},
                         diagFindings: [], metricMeta: {} });
}
function tiles(win) { return win.document.querySelectorAll('#topo-overview-tiles .topo-card:not(.ov-add-tile)'); }
function tileById(win, id) {
  return win.document.querySelector('#topo-overview-tiles .topo-card[data-tile-id="' + id + '"]');
}
function openMenu(win, id) {
  tileById(win, id).querySelector('.ov-tile-menu')
    .dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
  return win.document.getElementById('ov-tile-popover');
}
function stored(win) { return win.localStorage.getItem('quam_overview_tiles_v1'); }

(async function main() {

  // C1: defaults — ids + kebabs + ghost tile, nothing stored, note hidden.
  {
    const win = makeWorld();
    mount(win);
    ok(tiles(win).length >= 10, 'C1: default tiles render (got ' + tiles(win).length + ')');
    ok(!!tileById(win, 't1') && !!tileById(win, 'chip_size'),
      'C1: tiles carry stable data-tile-id');
    const kebabs = win.document.querySelectorAll('#topo-overview-tiles .ov-tile-menu');
    ok(kebabs.length === tiles(win).length, 'C1: every identified tile has a kebab');
    ok(!!win.document.getElementById('ov-add-tile'), 'C1: ghost add tile exists');
    ok(stored(win) === null, 'C1: nothing stored while at defaults');
    ok(win.document.getElementById('ov-custom-note').hidden === true, 'C1: note hidden');
    const t1tile = tileById(win, 't1');
    const t1v = t1tile.querySelector('.topo-card-value').textContent;
    ok(/30\.0/.test(t1v), 'C1: T1 big number is the AVG by default (got "' + t1v + '")');
    ok(!!t1tile.querySelector('.ov-stat-tag') && /avg/i.test(t1tile.querySelector('.ov-stat-tag').textContent),
      'C1: the default big number states its stat too (docs/150b)');
  }

  // C2: stat override — big number becomes the median, tagged 'med',
  // sub shows the avg (docs/150b: avg is the default).
  {
    const win = makeWorld();
    mount(win);
    const pop = openMenu(win, 't1');
    ok(!!pop, 'C2: kebab opens the popover');
    ok(!pop.querySelector('#ov-pop-key'), 'C2: a default tile offers NO metric select');
    const sel = pop.querySelector('#ov-pop-stat');
    ok(!!sel && sel.value === 'avg', 'C2: stat select present, avg selected by default');
    sel.value = 'median';
    sel.dispatchEvent(new win.Event('change', { bubbles: true }));
    const t1 = tileById(win, 't1');
    const v = t1.querySelector('.topo-card-value').textContent;
    ok(/20\.0/.test(v), 'C2: big number is now the median (got "' + v + '")');
    ok(!!t1.querySelector('.ov-stat-tag') && /med/i.test(t1.querySelector('.ov-stat-tag').textContent),
      'C2: the median stat is tagged MED (docs/150b)');
    ok(/avg 30\.0/.test(t1.querySelector('.topo-card-sub').textContent),
      'C2: the sub line states the avg instead');
    ok(/"t1":"median"/.test(stored(win) || ''), 'C2: override persisted');
    ok(win.document.getElementById('ov-custom-note').hidden === false, 'C2: note shown');
  }

  // C3: remove — composite tile offers remove only; the tile disappears.
  {
    const win = makeWorld();
    mount(win);
    const pop = openMenu(win, 'chip_size');
    ok(!pop.querySelector('#ov-pop-stat') && !pop.querySelector('#ov-pop-key'),
      'C3: composite tile offers neither key nor stat');
    ok(/Computed tile/.test(pop.textContent), 'C3: the composite note explains why');
    pop.querySelector('#ov-pop-remove').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    ok(!tileById(win, 'chip_size'), 'C3: removed tile gone');
    ok(/chip_size/.test(stored(win) || ''), 'C3: removal persisted');
  }

  // C4: add — real keys only; a value-less metric renders the honest
  // muted tile; a real one renders values.
  {
    const win = makeWorld();
    mount(win);
    win.document.getElementById('ov-add-tile')
      .dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    const pop = win.document.getElementById('ov-tile-popover');
    ok(!!pop, 'C4: ghost tile opens the popover');
    const keySel = pop.querySelector('#ov-pop-key');
    const keys = Array.prototype.map.call(keySel.options, function (o) { return o.value; });
    ok(JSON.stringify(keys) === JSON.stringify(['T1', 'anharmonicity', 'cz_fidelity', 'gate_fidelity_avg']),
      'C4: metric list = the real metric-record keys + cz (got ' + JSON.stringify(keys) + ')');
    keySel.value = 'anharmonicity';
    pop.querySelector('#ov-pop-add').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    const added = tileById(win, 'custom:0');
    ok(!!added, 'C4: added tile renders');
    ok(added.classList.contains('topo-card-empty')
       && added.querySelector('.topo-card-value').textContent.indexOf('—') >= 0,
      'C4: a value-less metric renders the honest muted no-data tile');
    // change the custom tile's key in place -> real values appear
    const pop2 = openMenu(win, 'custom:0');
    ok(!!pop2.querySelector('#ov-pop-key'), 'C4: a custom tile DOES offer the metric select');
    pop2.querySelector('#ov-pop-key').value = 'T1';
    pop2.querySelector('#ov-pop-key').dispatchEvent(new win.Event('change', { bubbles: true }));
    ok(/30\.0/.test(tileById(win, 'custom:0').querySelector('.topo-card-value').textContent),
      'C4: re-keyed custom tile shows the new metric avg (docs/150b default)');
  }

  // C5: reset restores defaults + clears storage; prefs survive a re-mount.
  {
    const win = makeWorld();
    mount(win);
    let pop = openMenu(win, 't1');
    pop.querySelector('#ov-pop-stat').value = 'max';
    pop.querySelector('#ov-pop-stat').dispatchEvent(new win.Event('change', { bubbles: true }));
    pop = openMenu(win, 'chip_size');
    pop.querySelector('#ov-pop-remove').dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    ok(stored(win) !== null, 'C5: customization stored');
    // a full re-mount (fresh /topology render) re-applies the preferences
    mount(win);
    ok(!tileById(win, 'chip_size'), 'C5: removal survives a re-mount');
    ok(/60\.0/.test(tileById(win, 't1').querySelector('.topo-card-value').textContent),
      'C5: stat override survives a re-mount (max = 60.0)');
    win._ovResetTiles();
    ok(!!tileById(win, 'chip_size'), 'C5: reset restores the removed tile');
    ok(/30\.0/.test(tileById(win, 't1').querySelector('.topo-card-value').textContent),
      'C5: reset restores the avg big number (docs/150b default)');
    ok(stored(win) === null, 'C5: reset clears the stored key');
    ok(win.document.getElementById('ov-custom-note').hidden === true, 'C5: note hidden again');
  }

  // C6 (docs/151): hovering a metric tile lists every entity with its value
  // (desc), a pair tile lists pairs, a composite tile shows nothing, and
  // leaving the tile strip hides the popup.
  {
    const win = makeWorld();
    mount(win);
    const cont = win.document.getElementById('topo-overview-tiles');
    tileById(win, 't1').dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
    const pop = win.document.getElementById('ov-hover-pop');
    ok(!!pop, 'C6: hover opens the per-entity popup');
    ok(/per qubit/.test(pop.textContent), 'C6: titled per qubit');
    const ids = Array.prototype.map.call(pop.querySelectorAll('.ov-hover-id'),
      function (x) { return x.textContent; });
    ok(JSON.stringify(ids) === JSON.stringify(['q3', 'q2', 'q1']),
      'C6: qubits sorted by value desc (got ' + JSON.stringify(ids) + ')');
    const vals = Array.prototype.map.call(pop.querySelectorAll('.ov-hover-val'),
      function (x) { return x.textContent; });
    ok(/60\.0/.test(vals[0]) && /10\.0/.test(vals[2]), 'C6: values listed beside ids');
    tileById(win, 'gate2q').dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
    const pop2 = win.document.getElementById('ov-hover-pop');
    ok(!!pop2 && /per pair/.test(pop2.textContent) && /q1-q2/.test(pop2.textContent)
       && /97\.00/.test(pop2.textContent),
      'C6: a pair tile lists pairs with values');
    tileById(win, 'chip_size').dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
    ok(!win.document.getElementById('ov-hover-pop'),
      'C6: a composite tile shows no popup (and hides the previous one)');
    tileById(win, 't1').dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
    cont.dispatchEvent(new win.MouseEvent('mouseleave'));
    ok(!win.document.getElementById('ov-hover-pop'), 'C6: leaving the strip hides the popup');
  }

  // C7 (docs/152): the VISIBLE settings door -- global apply + per-tile
  // selects + reset, panel stays open across applies.
  {
    const win = makeWorld();
    mount(win);
    win._ovOpenSettings(win.document.getElementById('ov-settings-btn'));
    const pop = win.document.getElementById('ov-settings-pop');
    ok(!!pop, 'C7: the settings button opens the panel');
    const rows = pop.querySelectorAll('.ov-set-row');
    ok(rows.length >= 3, 'C7: per-tile rows listed (got ' + rows.length + ')');
    ok(/Add panel/.test(pop.textContent), 'C7: the hint names the other doors');
    pop.querySelector('[data-global-stat="min"]')
      .dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    ok(/10\.0/.test(tileById(win, 't1').querySelector('.topo-card-value').textContent),
      'C7: global apply -> T1 shows the MIN');
    ok(/"t1":"min"/.test(stored(win) || '') && /"gate1q":"min"/.test(stored(win) || ''),
      'C7: the global stat persisted for every stat tile');
    ok(!!win.document.getElementById('ov-settings-pop'), 'C7: the panel stays open after apply');
    ok(!!pop.querySelector('[data-global-stat="min"].active'), 'C7: the uniform stat shows active');
    const sel = pop.querySelector('.ov-set-sel[data-tile-id="t1"]');
    sel.value = 'avg';
    sel.dispatchEvent(new win.Event('change', { bubbles: true }));
    ok(/30\.0/.test(tileById(win, 't1').querySelector('.topo-card-value').textContent),
      'C7: per-tile apply -> T1 back to AVG while others stay MIN');
    ok(!/"t1"/.test(stored(win) || '') && /"gate1q":"min"/.test(stored(win) || ''),
      'C7: the default stat is deleted from storage, the rest kept');
    win.document.getElementById('ov-set-reset')
      .dispatchEvent(new win.MouseEvent('click', { bubbles: true }));
    ok(stored(win) === null, 'C7: the panel reset clears storage');
    // toggle: a second press closes it
    win._ovOpenSettings(win.document.getElementById('ov-settings-btn'));
    ok(!win.document.getElementById('ov-settings-pop'), 'C7: the button toggles the panel closed');
  }

  // C8 (docs/153): drag-reorder -- live DOM move, order persisted on
  // dragend, default order never stored, survives a re-mount, reset clears.
  {
    const win = makeWorld();
    mount(win);
    const cont = win.document.getElementById('topo-overview-tiles');
    function firstId() { return cont.querySelector('.topo-card[data-tile-id]').getAttribute('data-tile-id'); }
    ok(firstId() === 'chip_size', 'C8: default first tile is chip_size');
    ok(tileById(win, 't1').getAttribute('draggable') === 'true', 'C8: tiles are draggable');
    // no-move drag: default order must NOT be stored
    tileById(win, 't1').dispatchEvent(new win.MouseEvent('dragstart', { bubbles: true }));
    tileById(win, 't1').dispatchEvent(new win.MouseEvent('dragend', { bubbles: true }));
    ok(stored(win) === null, 'C8: a drag landing where it began stores nothing');
    // real move: t1 to the front (zero-rects: clientX<0 means "before")
    const t1 = tileById(win, 't1');
    t1.dispatchEvent(new win.MouseEvent('dragstart', { bubbles: true }));
    ok(t1.classList.contains('ov-dragging'), 'C8: dragged tile ghosts');
    tileById(win, 'chip_size').dispatchEvent(
      new win.MouseEvent('dragover', { bubbles: true, clientX: -5 }));
    ok(firstId() === 't1', 'C8: dragover previews the move live');
    t1.dispatchEvent(new win.MouseEvent('dragend', { bubbles: true }));
    ok(!t1.classList.contains('ov-dragging'), 'C8: ghost class cleared');
    const st = JSON.parse(stored(win) || '{}');
    ok(Array.isArray(st.order) && st.order[0] === 't1',
      'C8: the order persisted with t1 first (got ' + JSON.stringify(st.order && st.order.slice(0, 3)) + ')');
    ok(win.document.getElementById('ov-custom-note').hidden === false, 'C8: note shows');
    mount(win);
    ok(firstId() === 't1', 'C8: the order survives a full re-mount');
    win._ovResetTiles();
    ok(firstId() === 'chip_size' && stored(win) === null,
      'C8: reset restores the default order and clears storage');
  }


  // C9 (docs/154): a value outside (0,1] is excluded from every aggregate and
  // every colour, counted where the user can see it, and still LISTED.
  {
    const win = makeWorld();
    mountBad(win);
    const t = tileById(win, 'irb');
    ok(!!t, 'C9: the IRB tile renders');
    const txt = t.textContent.replace(/\s+/g, ' ');

    // the aggregate is over the two usable values only (0.99, 0.97)
    ok(/98\.00%/.test(txt),
      'C9: the average excludes the unphysical rows (got "' + txt.slice(0, 90) + '")');
    ok(!/153|470|1\.53|4\.7/.test(txt),
      'C9: no unphysical number reaches the tile (got "' + txt.slice(0, 90) + '")');
    // N counts pairs that still have a usable value; the tail counts the
    // dropped ROWS, which is the truer number when a pair survives on
    // another macro (2 rows here, only 1 pair lost)
    ok(/\(2\)/.test(txt), 'C9: N is the pairs with a usable value (got "' + txt + '")');
    ok(/2 excluded/.test(txt),
      'C9: the tile SAYS how many rows it set aside (got "' + txt + '")');

    // the hover list still names the fully-excluded pair, uncoloured and last
    t.dispatchEvent(new win.MouseEvent('mouseenter', { bubbles: true }));
    t.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
    await new Promise(function (r) { setTimeout(r, 30); });
    const pop = win.document.getElementById('ov-hover-pop');
    ok(!!pop, 'C9: the hover popup opens');
    if (pop) {
      const items = Array.prototype.map.call(
        pop.querySelectorAll('.ov-hover-item'), function (e) {
          const v = e.querySelector('.ov-hover-val');
          return { id: e.querySelector('.ov-hover-id').textContent,
                   txt: v.textContent,
                   bad: v.classList.contains('ov-hover-val-bad'),
                   dot: !!e.querySelector('.ov-hover-dot-na') };
        });
      const bad = items.filter(function (x) { return x.bad; });
      ok(bad.length === 1 && bad[0].id === 'q3-q4',
        'C9: the excluded pair is still LISTED (got ' + JSON.stringify(items) + ')');
      ok(bad.length === 1 && /470/.test(bad[0].txt),
        'C9: it shows its REAL number, not a dash (got "' + (bad[0] || {}).txt + '")');
      ok(bad.length === 1 && bad[0].dot,
        'C9: it takes no heat colour — one 470% would wash out every other dot');
      ok(items.length && items[items.length - 1].bad,
        'C9: it sorts LAST, never to the top of a fidelity list');
    }
  }


  // C10 (docs/154): a metric whose SOURCE was refused is not a missing one.
  // Readout Fidelity (GEF) used to go 20 -> 19 in silence on a chip whose
  // confusion matrix is not row-stochastic.
  {
    const win = makeWorld();
    mountBad(win);
    const t = tileById(win, 'ro_gef');
    ok(!!t, 'C10: the GEF tile renders');
    const txt = t.textContent.replace(/\s+/g, ' ');
    // two usable (q2, q3); q1 refused; q4 genuinely absent
    ok(/\(2\)/.test(txt), 'C10: N counts only the usable ones (got "' + txt + '")');
    ok(/1 excluded/.test(txt),
      'C10: the refused one is COUNTED, the absent one is not (got "' + txt + '")');

    t.dispatchEvent(new win.MouseEvent('mouseenter', { bubbles: true }));
    t.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
    await new Promise(function (r) { setTimeout(r, 30); });
    const pop = win.document.getElementById('ov-hover-pop');
    ok(!!pop, 'C10: the hover popup opens');
    if (pop) {
      const items = Array.prototype.map.call(
        pop.querySelectorAll('.ov-hover-item'), function (e) {
          const v = e.querySelector('.ov-hover-val');
          return { id: e.querySelector('.ov-hover-id').textContent,
                   txt: v.textContent, bad: v.classList.contains('ov-hover-val-bad'),
                   title: v.getAttribute('title') || '' };
        });
      const bad = items.filter(function (x) { return x.bad; });
      ok(bad.length === 1 && bad[0].id === 'q1',
        'C10: only the REFUSED qubit is marked, never the absent one (got '
        + JSON.stringify(items.map(function (x) { return x.id + (x.bad ? '*' : ''); })) + ')');
      ok(bad.length === 1 && /source was refused/.test(bad[0].title),
        'C10: it says its source was refused, not "a failed fit" — there is no '
        + 'number here to be out of range (got "' + (bad[0] || {}).title + '")');
      const absent = items.filter(function (x) { return x.id === 'q4'; })[0];
      ok(absent && !absent.bad && absent.txt === '\u2014',
        'C10: a genuinely absent value still reads as a plain dash');
    }
  }

  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('overview_custom_selfcheck: all checks passed');
  process.exit(0);
})().catch(function (e) { console.error(e); process.exit(1); });
