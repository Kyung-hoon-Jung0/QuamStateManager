/* jsdom selfcheck for the instrument-wiring port-label sizing, running the
 * REAL shipped app.js renderer (renderInstrumentWiring / _appendPortCircle):
 *
 *  1. Single-member circles (control/z/coupler, single readout, input single)
 *     get chord-fit adaptive type: short qubit names render BIG (>= 12px on
 *     an output circle, up from the old fixed 10px), longer names shrink
 *     instead of only truncating.
 *  2. Multi-member feedline sub-circles keep their 7px labels (unchanged).
 *  3. Containment: every label's estimated width (0.62em monospace) stays
 *     within its circle's diameter - the user rule "bigger text, but never
 *     past the circle".
 *  4. Drag-drop DOM contract survives: .iw-port groups carry data-con/slot/
 *     port/io, .iw-port-circle carries data-element/data-role (the dragghost
 *     selfcheck's hit-testing depends on these).
 *
 * Run: node tests/wiring_portlabel_selfcheck.cjs  (driven by tests/test_gen_ux_selfchecks.py).
 */
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

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM(
    '<!doctype html><html><body>' +
    '<div id="table-pane"></div><div id="inspector-pane"></div>' +
    '<div id="status-bar"></div><div id="pending-tray"></div>' +
    '<div id="iw-host"></div>' +
    '</body></html>',
    { url: 'http://localhost/instrument', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
// jsdom bridges only what we hand it, and `CSS` was never on the list: the
// window HAS a CSS object, but bare `CSS` is undefined here, so app.js's
// `(window.CSS && CSS.escape) ? CSS.escape(s) : s` THREW ReferenceError
// instead of taking either branch. Inside LiveEditUndo._input that throw was
// swallowed by a try/catch returning null, so every cell lookup silently
// missed and whole selfchecks failed for a reason no assertion could name.
// A browser has CSS as a global; the harness must too.
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator;
global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.sessionStorage = global.localStorage;
window.localStorage = global.localStorage;
window.sessionStorage = global.sessionStorage;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: function () { return Promise.resolve(); }, trigger: function () {}, process: function () {} };
global.htmx = window.htmx;
window.fetch = global.fetch = function () {
    return Promise.resolve({
        status: 200,
        json: function () { return Promise.resolve({}); },
        text: function () { return Promise.resolve(''); },
    });
};

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

/* One MW-FEM: port 1 = xy single (2-char name), port 2 = z single (4-char),
   port 3 = a 4-qubit multiplexed readout feedline, port 4 = z single with a
   long name (truncation + shrink path). Input port 1 = single rr_in. */
const data = {
    controllers: {
        con1: {
            max_output_port: 8,
            fems: {
                '3': {
                    type: 'mw-fem',
                    output_ports: {
                        '1': [{ label: 'q1.xy', role: 'xy', element: 'q1' }],
                        '2': [{ label: 'qA12.z', role: 'z', element: 'qA12' }],
                        '3': [
                            { label: 'qF1.rr', role: 'rr', element: 'qF1' },
                            { label: 'qF2.rr', role: 'rr', element: 'qF2' },
                            { label: 'qF3.rr', role: 'rr', element: 'qF3' },
                            { label: 'qF4.rr', role: 'rr', element: 'qF4' },
                        ],
                        '4': [{ label: 'qLong123.z', role: 'z', element: 'qLong123' }],
                    },
                    input_ports: {
                        '1': [{ label: 'q1.rr_in', role: 'rr_in', element: 'q1' }],
                    },
                },
            },
        },
    },
};

window.renderInstrumentWiring('iw-host', data, {}, null);

const host = window.document.getElementById('iw-host');
const svg = host.querySelector('svg.instrument-svg');
ok(!!svg, 'renderer produced an svg.instrument-svg');

/* Collect every port-assignment circle: its radius, label text, font size. */
const entries = [];
svg.querySelectorAll('g.iw-port-circle').forEach(function (g) {
    const c = g.querySelector('circle');
    const t = g.querySelector('text');
    if (!c || !t) return;
    entries.push({
        el: g.getAttribute('data-element'),
        role: g.getAttribute('data-role'),
        r: parseFloat(c.getAttribute('r')),
        label: t.textContent,
        fs: parseFloat(t.getAttribute('font-size')),
    });
});
ok(entries.length === 8, 'eight assignment circles rendered (got ' + entries.length + ')');

function one(el, role) {
    return entries.filter(function (e) { return e.el === el && (!role || e.role === role); })[0];
}

/* 1. Single-member circles: big type. */
const xy = one('q1', 'xy');
ok(xy && xy.fs >= 12, 'single xy label "q1" is >= 12px (got ' + (xy && xy.fs) + ')');
const z = one('qA12', 'z');
ok(z && z.fs >= 12, 'single z label "qA12" is >= 12px (got ' + (z && z.fs) + ')');
ok(z && z.label === 'qA12', 'z label shows the full name, role suffix stripped (got "' + (z && z.label) + '")');
const rin = one('q1', 'rr_in');
ok(rin && rin.fs >= 10, 'input single "q1" is >= 10px (got ' + (rin && rin.fs) + ')');
ok(rin && rin.r < 21, 'input circle is the smaller input radius (got r=' + (rin && rin.r) + ')');

/* Long name: shrinks (not just truncates), never below the 9px floor. */
const lz = one('qLong123', 'z');
ok(lz && lz.fs >= 9 && lz.fs <= 10,
   'long z name shrinks toward the floor, 9-10px (got ' + (lz && lz.fs) + ')');
ok(lz && lz.label.length <= 7, 'long z name truncated to <= 7 chars (got "' + (lz && lz.label) + '")');

/* 2. Feedline sub-circles: unchanged 7px. */
const feed = entries.filter(function (e) { return e.role === 'rr'; });
ok(feed.length === 4, 'four feedline sub-circles (got ' + feed.length + ')');
ok(feed.every(function (e) { return e.fs === 7; }),
   'feedline sub-labels stay at 7px (got ' + feed.map(function (e) { return e.fs; }).join(',') + ')');
ok(feed.every(function (e) { return e.r < 14; }),
   'feedline sub-circles keep their small radii (got ' + feed.map(function (e) { return e.r; }).join(',') + ')');

/* 3. Containment: estimated monospace width never exceeds the diameter. */
entries.forEach(function (e) {
    const w = 0.62 * e.label.length * e.fs;
    ok(w <= 2 * e.r + 0.1,
       'label "' + e.label + '" (' + e.fs + 'px) fits its circle: ' +
       w.toFixed(1) + ' <= ' + (2 * e.r));
});

/* 4. Drag-drop DOM contract (generate_dragghost_selfcheck depends on it). */
const port1 = svg.querySelector('g.iw-port[data-con="con1"][data-slot="3"][data-port="1"][data-io="output"]');
ok(!!port1, 'iw-port group carries data-con/slot/port/io');
ok(!!(port1 && port1.querySelector('.iw-port-circle[data-element="q1"]')),
   'iw-port-circle carries data-element');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('ALL OK');
process.exit(0);
