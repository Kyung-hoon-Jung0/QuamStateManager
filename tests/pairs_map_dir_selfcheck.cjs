/* jsdom selfcheck for the Pairs map direction drawing (D-94, D-98).
 *
 * Every pair edge carries one arrow at the target end, the moving qubit is a
 * ring beside it, and the pair id is written along the edge. declutterLabels()
 * places each label so that nothing a reader must tell apart overlaps. jsdom
 * has no layout, so getBBox / getComputedTextLength are given a geometric
 * stand-in (text width = 0.6 em per character, the monospace-ish upper bound
 * of the UI font) and the real placement code runs on top of it. The real
 * browser measurement is in the stage-5 report; this pins that the placement
 * reaches ZERO overlaps on a lattice fixture and that the font floor (12 px,
 * D-6) holds.
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0, checks = 0;
function ok(c, m) { checks++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div id="m"></div></body></html>', { runScripts: 'outside-only' });
const win = dom.window;

// ---- geometric stand-ins for the two layout calls the placement uses ------
function num(el, a) { return parseFloat(el.getAttribute(a)) || 0; }
function bboxOf(el) {
  const tag = el.tagName.toLowerCase();
  if (tag === 'circle') { const r = num(el, 'r'); return { x: num(el, 'cx') - r, y: num(el, 'cy') - r, width: 2 * r, height: 2 * r }; }
  if (tag === 'line') {
    const x1 = num(el, 'x1'), y1 = num(el, 'y1'), x2 = num(el, 'x2'), y2 = num(el, 'y2');
    return { x: Math.min(x1, x2), y: Math.min(y1, y2), width: Math.abs(x2 - x1), height: Math.abs(y2 - y1) };
  }
  if (tag === 'polygon' || tag === 'polyline') {
    const pts = (el.getAttribute('points') || '').trim().split(/\s+/).map((p) => p.split(',').map(Number));
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    return { x: Math.min(...xs), y: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
  }
  if (tag === 'text' || tag === 'tspan') {
    const f = num(el, 'font-size') || num(el.parentElement, 'font-size') || 12, w = (el.textContent || '').length * 0.6 * f;
    return { x: num(el, 'x') - w / 2, y: num(el, 'y') - f / 2, width: w, height: f };
  }
  let lo = [Infinity, Infinity], hi = [-Infinity, -Infinity];
  for (const c of el.children) {
    if (c.tagName.toLowerCase() === 'title') continue;
    const b = bboxOf(c);
    lo = [Math.min(lo[0], b.x), Math.min(lo[1], b.y)]; hi = [Math.max(hi[0], b.x + b.width), Math.max(hi[1], b.y + b.height)];
  }
  return isFinite(lo[0]) ? { x: lo[0], y: lo[1], width: hi[0] - lo[0], height: hi[1] - lo[1] } : { x: 0, y: 0, width: 0, height: 0 };
}
win.SVGElement.prototype.getBBox = function () { return bboxOf(this); };
win.SVGElement.prototype.getComputedTextLength = function () {
  return (this.textContent || '').length * 0.6 * (parseFloat(this.getAttribute('font-size')) || num(this.parentElement, 'font-size') || 12);
};
win.SVGElement.prototype.getBoundingClientRect = function () { return { width: 100, height: 100, top: 0, left: 0, right: 100, bottom: 100 }; };

win.eval(fs.readFileSync(path.join(STATIC, 'topo-graph.js'), 'utf8'));

// A 4x3 lattice: horizontal, vertical and anti-parallel neighbours, couplers,
// moving qubits on both ends and frequencies for the chevrons.
const nodes = [], edges = [];
for (let r = 0; r < 3; r++) {
  for (let c = 0; c < 4; c++) {
    nodes.push({ id: 'q' + r + c, grid_location: c + ',' + r, f_01: 5.0e9 + ((r * 4 + c) % 3) * 1.5e8 });
  }
}
function e(a, b, extra) { edges.push(Object.assign({ pair_id: a + '-' + b, source: a, target: b }, extra || {})); }
for (let r = 0; r < 3; r++) {
  for (let c = 0; c < 3; c++) e('q' + r + c, 'q' + r + (c + 1), { moving_qubit: 'control' });
}
for (let r = 0; r < 2; r++) {
  for (let c = 0; c < 4; c++) e('q' + (r + 1) + c, 'q' + r + c, { moving_qubit: c % 2 ? 'target' : 'control' });
}


const m = win.document.getElementById('m');
win.TopoGraph.renderLayout(m, { nodes: nodes, edges: edges, highlight: 'pairs', cell: 120 });
const svg = m.querySelector('svg.cm-svg');
ok(!!svg && svg.classList.contains('cm-dir'), 'the map carries the direction drawing (cm-dir)');
ok(m.querySelectorAll('.cm-dir-arrow').length === edges.length, 'one arrow per pair edge (' + edges.length + ')');
ok(m.querySelectorAll('.cm-dir-label').length === edges.length, 'one along-the-edge label per pair edge');
ok(m.querySelectorAll('.cm-dir-m').length === edges.filter((x) => x.moving_qubit).length, 'one moving ring per edge that declares a moving qubit');

const placed = Array.from(m.querySelectorAll('.cm-dir-label')).every((t) => t.hasAttribute('data-px') &&
  (t.hasAttribute('data-stack') || t.hasAttribute('transform') || +t.getAttribute('data-ang') === 0));
ok(placed, 'every label was placed by declutterLabels (along its edge, or stacked upright on a steep one)');
// FB-01: no pair label is ever rotated off level.
ok(Array.from(m.querySelectorAll('.cm-pair-label')).every(t => !/rotate/.test(t.getAttribute('transform') || '')),
   'no rotate transform on any pair label');
const fonts = Array.from(m.querySelectorAll('.cm-dir-label')).map((t) => parseFloat(t.getAttribute('font-size')));
ok(Math.min(...fonts) >= 12, 'no label is drawn under 12 px (D-6), min ' + Math.min(...fonts));
ok(m.querySelectorAll('.cm-dir-crowded').length === 0, 'no label had to be left crowded');
ok(parseFloat(m.querySelector('.cm-id').getAttribute('font-size')) >= 12, 'qubit ids stay at or above 12 px');

const ov = win.TopoGraph.labelOverlaps(svg);
ok(ov.items > edges.length * 3, 'the overlap count looked at stones, labels and glyphs (' + ov.items + ' items)');
ok(ov.count === 0, 'label overlaps == 0 on the pairs fixture (' + JSON.stringify(ov.pairs.slice(0, 4)) + ')');

const legend = m.querySelector('.cm-dir-legend');
ok(!!legend && /control → target/.test(legend.textContent) && /moving qubit/.test(legend.textContent)
   && /lower f/.test(legend.textContent), 'the one-line legend names arrow, ring and chevron');

// A coupler chip: every pair carries a coupler dot at its midpoint, beside
// the frequency chevron. The chevron must move clear of it.
{
  const cedges = edges.map((x) => Object.assign({}, x, { has_coupler: true }));
  const mc = win.document.createElement('div');
  win.document.body.appendChild(mc);
  win.TopoGraph.renderLayout(mc, { nodes: nodes, edges: cedges, highlight: 'pairs', cell: 120 });
  ok(mc.querySelectorAll('.cm-coupler').length === cedges.length && mc.querySelectorAll('.cm-freq').length > 0,
     'coupler fixture: one coupler dot per pair, chevrons drawn');
  const ovc = win.TopoGraph.labelOverlaps(mc.querySelector('svg.cm-svg'));
  ok(ovc.count === 0, 'coupler fixture: overlaps == 0 (' + JSON.stringify(ovc.pairs.slice(0, 4)) + ')');
}

// Non-level edges, abbreviated display ids and endpoint names with punctuation.
function fixture(source, target, pair, dx, dy, cell = 120) {
  const host = win.document.createElement('div'); win.document.body.appendChild(host);
  win.TopoGraph.renderLayout(host, {nodes:[{id:source, grid_location:'0,0'},
    {id:target, grid_location:dx+','+dy}], edges:[{source, target, pair_id:pair}], highlight:'pairs', cell});
  return host.querySelector('svg');
}
for (const [dx, dy] of [[7,4], [4,4]]) {
  const diagonal = fixture('lower', 'upper', 'lower-upper', dx, dy);
  const label = diagonal.querySelector('.cm-pair-label');
  ok(!/rotate/.test(label.getAttribute('transform') || ''), 'no rotate transform at ' + Math.round(Math.atan2(dy,dx)*180/Math.PI) + ' degrees');
  ok(label.hasAttribute('data-stack') && label.firstElementChild.textContent === 'upper', 'diagonal stack reads upper endpoint first');
  ok(win.TopoGraph.labelOverlaps(diagonal).count === 0, 'diagonal stack clears stones and arrow');
  const nx = -Math.sin(+label.dataset.ang*Math.PI/180), ny = Math.cos(+label.dataset.ang*Math.PI/180);
  const distance = Math.abs((+label.dataset.px - +label.dataset.cx)*nx + (+label.dataset.py - +label.dataset.cy)*ny);
  ok(distance > Math.abs(nx)*+label.dataset.w/2 + Math.abs(ny)*+label.dataset.h/2 + 5, 'diagonal upright box clears the line');
}
for (const [source, target, pair] of [['q1','q2','q1-2'], ['q-one','q-two','q-one-q-two']]) {
  const shape = fixture(source, target, pair, 0, 1), label = shape.querySelector('.cm-pair-label');
  ok(Array.from(label.children, t => t.textContent).join(',') === [target,'|',source].join(','), 'endpoint ids survive display shape ' + pair);
  ok(!/rotate/.test(label.getAttribute('transform') || ''), 'display shape stays upright ' + pair);
}
const short = fixture('q_long_underscore_endpoint', 'q_other_long_endpoint', 'short', 0, 1, 30);
const label = short.querySelector('.cm-pair-label');
label.setAttribute('font-size', '20');
// A narrow free corridor forces the real collision pass to shrink the stack.
const cy = +label.dataset.cy;
for (const side of [-1,1]) {
  const block = win.document.createElementNS('http://www.w3.org/2000/svg','polygon');
  block.setAttribute('class','cm-res');
  const y = cy + side*25;
  block.setAttribute('points', `-2000,${y} 2000,${y} 2000,${y+side*500} -2000,${y+side*500}`);
  short.appendChild(block);
}
win.TopoGraph.declutterLabels(short);
const f = +label.getAttribute('font-size');
ok(f < 20 && f >= 12, 'short vertical edge with long underscore ids exercises font shrink');
const width = Math.max(...Array.from(label.children, t => t.getComputedTextLength()));
ok(Math.abs(+label.dataset.w-width) < 0.11, 'stack width remeasured at final shrunken font');
const height = f + 2*f*1.1;
ok(Math.abs(+label.dataset.h-height) < 0.11, 'stack height remeasured at final shrunken font');
ok(!label.classList.contains('cm-dir-crowded'), 'shrunken box clears the narrow corridor');

const summary = fails ? fails + ' check(s) FAILED\n' : 'pairs_map_dir_selfcheck: all checks passed (' + checks + ' assertions)\n';
process.stdout.write(summary, function () { process.exit(fails ? 1 : 0); });
