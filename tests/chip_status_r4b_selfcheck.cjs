/* R4b Chip Status behaviour changes (D-106 / UD-8), against the REAL shipped
 * chip-status.js and component-map.js under jsdom.
 *
 *  CS-10  a threshold field that holds no number says "not a number" in its
 *         row, and Update colour bands (button AND Enter) stays off until
 *         every field holds a number.
 *  CS-3   the thresholds editor opens with focus in its first field; the
 *         Overview Panels popover follows its button when the page scrolls.
 *  CS-4   the sub-nav gear remembers where it was pressed; closing the editor
 *         (or a successful Update) returns there with focus on the gear.
 *  CS-18  the "avoid" chips of the verdict line are ONE wrapping group.
 *  UD-3   the component map, while the user has not chosen open/closed, is
 *         scaled so the table head reaches the first screen -- never below an
 *         11px label, and not at all when the user has chosen.
 *
 * Run: node tests/chip_status_r4b_selfcheck.cjs  (driven by test_orphan_selfchecks.py)
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

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

function chipWorld() {
  const topo = {
    nodes: [
      { id: 'qA1', grid_location: '0,0', T1: 2.4e-5, gate_fidelity_avg: 0.9990,
        metrics: { T1: { value: 2.4e-5 } } },
      { id: 'qA2', grid_location: '1,0', T1: 4e-6, gate_fidelity_avg: 0.90,
        metrics: { T1: { value: 4e-6 } } },
      { id: 'qA3', grid_location: '2,0', T1: 3e-6, gate_fidelity_avg: 0.90,
        metrics: { T1: { value: 3e-6 } } },
    ],
    edges: [],
    summary: {},
  };
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="table-pane">'
    + '<div class="topo-subnav"><span class="topo-diag-end"><button id="gear" type="button">g</button></span></div>'
    + '<div class="topo-dashboard"><div id="topo-hero"></div>'
    + '<div class="topo-verdict-banner" id="topo-verdict-banner" hidden></div>'
    + '<button type="button" id="ov-settings-btn">Panels</button>'
    + '<div id="topo-thresh-editor" hidden></div>'
    + '<div id="topo-health-tiles"></div><div id="topo-health-worst"></div>'
    + '</div></div></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  win.htmx = { ajax: function () {}, process: function () {} };
  win.posts = [];
  win.fetch = function (url, opts) {
    win.posts.push({ url: String(url), method: opts && opts.method });
    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ ok: true }); } });
  };
  new win.Function(read('app.js') + '\n;\n' + read('topo-graph.js') + '\n;\n'
                   + read('chip-status.js')).call(win);
  win.ChipStatus.mount({
    topo: topo, rawWiring: {}, diagFindings: [], metricMeta: {},
    defaultThresholds: {
      gate_fidelity_avg: { direction: 'higher', warn: 0.99, fail: 0.95, label: '1Q gate fidelity' },
      T1: { direction: 'higher', warn: 3e-5, fail: 1e-5, label: 'T1' },
    },
  });
  return win;
}

(async function () {
  /* ── CS-10 ─────────────────────────────────────────────────────────── */
  {
    const win = chipWorld(), doc = win.document;
    win.toggleThresholdEditor();
    const host = doc.getElementById('topo-thresh-editor');
    const ins = host.querySelectorAll('.thresh-in');
    const upd = host.querySelector('.thresh-apply');
    ok(ins.length >= 2, 'CS10.0: the editor builds its fields — ' + ins.length);
    ok(upd && !upd.disabled, 'CS10.1: Update is on at rest');
    ok([...host.querySelectorAll('.thresh-nan')].every((m) => m.hidden),
      'CS10.2: no "not a number" note at rest');
    // CS-3: opening puts focus in the first field
    ok(doc.activeElement === ins[0], 'CS3.1: opening the editor focuses its first field');

    ins[0].value = '';
    ins[0].dispatchEvent(new win.Event('input', { bubbles: true }));
    const note = host.querySelector('.thresh-nan[data-metric="' + ins[0].getAttribute('data-metric') + '"]');
    ok(note && !note.hidden && /not a number/.test(note.textContent),
      'CS10.3: an empty field shows "not a number" in its own row');
    ok(upd.disabled, 'CS10.4: …and Update colour bands is off');
    ok(ins[0].getAttribute('aria-invalid') === 'true', 'CS10.5: …and the field is marked invalid');
    const others = [...host.querySelectorAll('.thresh-nan')].filter((m) => m !== note);
    ok(others.every((m) => m.hidden), 'CS10.6: only that row carries the note');
    ok(!/press "Update colour bands"/.test(doc.getElementById('thresh-status').textContent),
      'CS10.7: the status line does not tell the user to press a disabled button');

    // Enter in a field obeys the disabled button: nothing is sent
    ins[1].value = String(parseFloat(ins[1].value) + 1);
    ins[1].dispatchEvent(new win.Event('input', { bubbles: true }));
    ins[1].dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await tick(5);
    ok(!win.posts.some((p) => /chip-status\/spec/.test(p.url) && p.method === 'POST'),
      'CS10.8: Enter with a non-number field saves nothing');

    ins[0].value = '99';
    ins[0].dispatchEvent(new win.Event('input', { bubbles: true }));
    ok(!upd.disabled && note.hidden, 'CS10.9: a number again turns Update back on and clears the note');
  }

  /* ── CS-4 (gear return) ───────────────────────────────────────────── */
  {
    const win = chipWorld(), doc = win.document;
    const pane = doc.getElementById('table-pane');
    const gear = doc.getElementById('gear');
    pane.scrollTop = 0;
    let st = 777;
    Object.defineProperty(pane, 'scrollTop', { get: () => st, set: (v) => { st = v; }, configurable: true });
    win.chipOpenThresholds(gear);
    ok(!doc.getElementById('topo-thresh-editor').hidden, 'CS4.1: the gear opens the editor');
    ok(win._threshReturnForTest() && win._threshReturnForTest().gear === gear,
      'CS4.2: …remembering where it was pressed');
    st = 5;   // the editor's own reveal moved the page
    win.toggleThresholdEditor();   // close (Escape / the Health button)
    ok(st === 777, 'CS4.3: closing returns to the place the gear was pressed — scrollTop ' + st);
    ok(doc.activeElement === gear, 'CS4.4: …with focus back on the gear');
    ok(!win._threshReturnForTest(), 'CS4.5: …once');

    // a successful Update returns too
    win.chipOpenThresholds(gear);
    st = 5;
    const ins = doc.querySelectorAll('#topo-thresh-editor .thresh-in');
    ins[0].value = String(parseFloat(ins[0].value) - 0.5);
    ins[0].dispatchEvent(new win.Event('input', { bubbles: true }));
    win.applyThresholds();
    await tick(20);
    ok(st === 777, 'CS4.6: a saved Update returns to the gear\'s place — scrollTop ' + st);

    // the Health section's own button opens with no return
    win.toggleThresholdEditor(); // (already closed? reopen)
    if (doc.getElementById('topo-thresh-editor').hidden) win.toggleThresholdEditor();
    ok(!win._threshReturnForTest(), 'CS4.7: the Health button\'s open has no return to make');
  }

  /* ── CS-3 (Panels popover follows its button) ─────────────────────── */
  {
    const win = chipWorld(), doc = win.document;
    const btn = doc.getElementById('ov-settings-btn');
    let y = 300;
    btn.getBoundingClientRect = () => ({ top: y - 20, bottom: y, left: 100, right: 180, width: 80, height: 20 });
    win._ovOpenSettings(btn);
    const pop = doc.getElementById('ov-settings-pop');
    ok(!!pop, 'CS3.2: the Panels popover opens');
    const t0 = parseFloat(pop.style.top);
    ok(t0 === 306, 'CS3.3: …just below its button — top ' + t0);
    y = 200;
    doc.getElementById('table-pane').dispatchEvent(new win.Event('scroll'));
    ok(parseFloat(pop.style.top) === 206, 'CS3.4: …and follows the button when the page scrolls — top ' + pop.style.top);
    win._ovOpenSettings(btn);   // toggle closed
    y = 100;
    doc.getElementById('table-pane').dispatchEvent(new win.Event('scroll'));
    ok(!doc.getElementById('ov-settings-pop'), 'CS3.5: closing removes it (and its listener does no harm)');
  }

  /* ── CS-18 ─────────────────────────────────────────────────────────── */
  {
    const win = chipWorld(), doc = win.document;
    const chips = doc.querySelectorAll('#topo-verdict-banner .verdict-avoid-chip');
    ok(chips.length >= 2, 'CS18.0: the fixture renders avoid chips — ' + chips.length);
    const groups = new Set([...chips].map((c) => c.parentElement));
    ok(groups.size === 1 && [...groups][0].classList.contains('verdict-avoid-chips'),
      'CS18.1: every avoid chip sits in ONE .verdict-avoid-chips group (wraps together, with a gap)');
    const css = read('style.css');
    ok(/\.verdict-avoid-chips\s*\{[^}]*display:\s*inline-flex[^}]*flex-wrap:\s*wrap[^}]*gap:/.test(css),
      'CS18.2: …and the group is an inline flex row that wraps with a gap');
  }

  /* ── UD-3 component map fit ───────────────────────────────────────── */
  function mapWorld(saved, minFont, theadBottom) {
    const dom = new JSDOM(
      '<!DOCTYPE html><html><body><div id="table-pane">'
      + '<details class="cmap" id="component-map" open><summary>m</summary><div class="cmap-body">'
      + '<div class="cm-scroll"><svg class="cm-svg" width="900" height="900" viewBox="0 0 900 900">'
      + '<text style="font-size:' + minFont + 'px">qA1</text><text style="font-size:20px">qA2</text>'
      + '</svg></div></div></details>'
      + '<table><thead><tr><th>h</th></tr></thead></table></div></body></html>',
      { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
    const win = dom.window;
    if (saved !== null) win.localStorage.setItem('quam_component_map_open', saved);
    win.Element.prototype.getClientRects = function () { return [1]; };
    const doc = win.document;
    doc.getElementById('table-pane').getBoundingClientRect = () => ({ top: 0, bottom: 768 });
    doc.querySelector('thead').getBoundingClientRect = () => ({ top: theadBottom - 40, bottom: theadBottom, height: 40 });
    new win.Function(read('component-map.js')).call(win);
    const root = doc.getElementById('component-map');
    const s = win.ComponentMap.fitUnsized(root, root.querySelector('.cmap-body'));
    const svg = root.querySelector('svg');
    return { s, h: +svg.getAttribute('height'), w: +svg.getAttribute('width'), fit: root.getAttribute('data-cmap-fit'), win, root };
  }
  {
    // head bottom 1000, needs 1000+40-768 = 272 px back: s = 628/900 = .698
    const a = mapWorld(null, 17.3, 1000);
    ok(a.s && Math.abs(a.s - 628 / 900) < 1e-6, 'UD3.1: unsized, the map shrinks just enough — s ' + a.s);
    ok(a.h === 628 && a.w === 628, 'UD3.2: …width and height together (labels scale, nothing crops) — ' + a.w + 'x' + a.h);
    ok(17.3 * a.s >= 11, 'UD3.3: …and the smallest label stays at or above 11px — ' + (17.3 * a.s).toFixed(2));
    // a theme repaint rebuilds the svg at natural size: the fit is put back
    const body = a.root.querySelector('.cmap-body');
    body.innerHTML = '<div class="cm-scroll"><svg class="cm-svg" width="900" height="900" viewBox="0 0 900 900"></svg></div>';
    await tick(5);
    ok(+body.querySelector('svg').getAttribute('height') === 628, 'UD3.4: a repaint keeps the fitted size');

    const b = mapWorld(null, 17.3, 1300);   // needs 572 back: s .364 < 11/17.3
    ok(b.s === null && b.h === 900 && b.fit === 'floor',
      'UD3.5: when the 11px floor cannot bring the head in, the map keeps its natural size — ' + b.fit);
    const c = mapWorld('open', 17.3, 1000);
    ok(c.s === null && c.h === 900, 'UD3.6: a user who chose open keeps the natural size');
    const e = mapWorld('1', 17.3, 1000);
    ok(e.s && e.h === 628, 'UD3.6b: the "1" every page view used to write is no choice — still fitted');
    // the parsed <details open> fires its own toggle: that saves nothing
    {
      const w = e.win, root = e.root;
      w.localStorage.removeItem('quam_component_map_open');
      w.fetch = () => new Promise(() => {});
      w.TopoGraph = { closeDetails: function () {} };
      w.ComponentMap.mount(root);
      root.dispatchEvent(new w.Event('toggle'));
      ok(w.localStorage.getItem('quam_component_map_open') === null,
        'UD3.8: a toggle no person made is not saved as a choice');
      root.querySelector('summary').dispatchEvent(new w.Event('click'));
      root.removeAttribute('open');
      root.dispatchEvent(new w.Event('toggle'));
      ok(w.localStorage.getItem('quam_component_map_open') === '0', 'UD3.9: a collapse made by a person is saved');
    }
    const d = mapWorld(null, 17.3, 600);
    ok(d.s === null && d.h === 900 && d.fit === '1', 'UD3.7: a head already on screen changes nothing');
  }

  console.log(fails ? ('FAILED (' + fails + ')')
    : ('chip_status_r4b_selfcheck ok (' + asserts + ' assertions)'));
  process.exit(fails ? 1 : 0);
})();
