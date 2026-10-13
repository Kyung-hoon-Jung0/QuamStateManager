/* jsdom selfcheck for the Instrument Wiring DIG sub-column (docs/126
 * follow-up), running the REAL shipped app.js renderer:
 *
 *  1. A controller with >= 1 digital assignment grows a DIG sub-column per
 *     FEM: header text, 8 physical slots ([data-io="digital"] cells), slate
 *     'digital' role color on assigned circles.
 *  2. A shared trigger port (3 qubits, shareable=true — the customer's older
 *     snapshot shape) renders all three sub-circles on ONE digital cell.
 *  3. Digital port 1 and analog output 1 coexist: the z circle still sits on
 *     the analog OUT cell, the trigger on the digital cell.
 *  4. Hover popup on a digital circle shows the DIGITAL badge + the
 *     marker/line/delay fields (through the real _showPortPopup path).
 *  5. A payload with no digital anywhere STILL draws the DIG sub-column with
 *     its 8 physical slots, all empty (instrument-ports hotfix: old "no DIG
 *     column, byte-identical pre-digital layout" -> new "always drawn", because
 *     every FEM carries 8 digital outputs and an empty slot is where a trigger
 *     line can be wired); same width as the digital layout.
 *
 * Run: node tests/instrument_digital_selfcheck.cjs
 * (driven by tests/test_gen_ux_selfchecks.py).
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
    '<div id="iw-host"></div><div id="iw-host2"></div>' +
    '<div id="port-popup" class="port-popup hidden">' +
    '  <div class="port-popup-header">' +
    '    <span id="popup-label"></span><span id="popup-role-badge"></span>' +
    '  </div>' +
    '  <div id="popup-body"></div>' +
    '</div>' +
    '</body></html>',
    { url: 'http://localhost/instrument', pretendToBeVisual: true });
const { window } = dom;
global.window = window;
global.CSS = window.CSS;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
global.MouseEvent = window.MouseEvent;
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

/* One LF-FEM (slot 4): analog z on output 1 + digital trigger ports —
   port 1 shared by three qubits, port 4 a single trigger. */
const withDigital = {
    controllers: {
        con1: {
            max_output_port: 8,
            max_digital_port: 4,
            fems: {
                '4': {
                    type: 'lf-fem',
                    output_ports: {
                        '1': [{ label: 'q1.z', role: 'z', element: 'q1' }],
                    },
                    input_ports: {},
                    digital_ports: {
                        '1': [
                            { label: 'q1.trigger', role: 'digital', element: 'q1',
                              marker: 'trigger', source: 'z.opx_trigger_out',
                              delay: 0, buffer: 0, shareable: true, inverted: null },
                            { label: 'q9.trigger', role: 'digital', element: 'q9',
                              marker: 'trigger', source: 'z.opx_trigger_out',
                              delay: 0, buffer: 0, shareable: true, inverted: null },
                            { label: 'q17.trigger', role: 'digital', element: 'q17',
                              marker: 'trigger', source: 'z.opx_trigger_out',
                              delay: 0, buffer: 0, shareable: true, inverted: null },
                        ],
                        '4': [
                            { label: 'q7.trigger', role: 'digital', element: 'q7',
                              marker: 'trigger', source: 'z.opx_trigger_out',
                              delay: 0, buffer: 0, shareable: true, inverted: null },
                        ],
                    },
                },
            },
        },
    },
};

window.renderInstrumentWiring('iw-host', withDigital, {}, null);
const host = window.document.getElementById('iw-host');
const svg = host.querySelector('svg.instrument-svg');
ok(!!svg, 'digital payload rendered an svg');

/* 1. DIG sub-column exists: header + 8 physical slots */
const texts = Array.prototype.map.call(svg.querySelectorAll('text'), (t) => t.textContent);
ok(texts.indexOf('DIG') !== -1, 'DIG sub-column header rendered');
const digCells = svg.querySelectorAll('g.iw-port[data-io="digital"]');
ok(digCells.length === 8, 'eight digital port slots rendered (got ' + digCells.length + ')');

/* 2. Shared port 1: three sub-circles; single port 4: one circle */
function cellCircles(port) {
    let cell = null;
    digCells.forEach ? null : null;
    Array.prototype.forEach.call(digCells, function (c) {
        if (c.getAttribute('data-port') === String(port)) cell = c;
    });
    return cell ? cell.querySelectorAll('g.iw-port-circle') : [];
}
const p1 = cellCircles(1);
ok(p1.length === 3, 'shared digital port 1 renders 3 circles (got ' + p1.length + ')');
const p1els = Array.prototype.map.call(p1, (g) => g.getAttribute('data-element')).sort();
ok(p1els.join(',') === 'q1,q17,q9', 'shared port carries q1/q9/q17 (got ' + p1els.join(',') + ')');
const p4 = cellCircles(4);
ok(p4.length === 1 && p4[0].getAttribute('data-element') === 'q7',
   'single digital port 4 carries q7');

/* Assigned circles wear the digital slate color */
const digFill = p4[0].querySelector('circle').getAttribute('fill');
ok(digFill === '#54617a', 'digital circle uses the slate role color (got ' + digFill + ')');

/* 3. Analog output 1 unaffected by digital port 1 */
let analog1 = null;
Array.prototype.forEach.call(svg.querySelectorAll('g.iw-port[data-io="output"]'), function (c) {
    if (c.getAttribute('data-port') === '1') analog1 = c;
});
const a1circ = analog1 ? analog1.querySelectorAll('g.iw-port-circle') : [];
ok(a1circ.length === 1 && a1circ[0].getAttribute('data-role') === 'z',
   'analog output 1 still carries exactly the z line');

/* 4. Hover popup: DIGITAL badge + marker/line/delay fields */
const ev = new window.MouseEvent('mouseenter', { clientX: 60, clientY: 60, bubbles: true });
p4[0].dispatchEvent(ev);
const popup = window.document.getElementById('port-popup');
ok(!popup.classList.contains('hidden'), 'popup opens on digital circle hover');
ok(window.document.getElementById('popup-label').textContent === 'q7.trigger',
   'popup label is the trigger label');
ok(window.document.getElementById('popup-role-badge').textContent === 'DIGITAL',
   'popup badge reads DIGITAL');
const bodyText = window.document.getElementById('popup-body').textContent;
ok(bodyText.indexOf('marker') !== -1 && bodyText.indexOf('trigger') !== -1,
   'popup body carries the marker name');
ok(bodyText.indexOf('line') !== -1 && bodyText.indexOf('z.opx_trigger_out') !== -1,
   'popup body names the carrying channel');
ok(bodyText.indexOf('delay') !== -1 && bodyText.indexOf('0 ns') !== -1,
   'popup body shows the delay in ns');

/* 5. No digital anywhere -> the DIG column is still drawn, every slot empty */
const noDigital = {
    controllers: {
        con1: {
            max_output_port: 8,
            max_digital_port: 0,
            fems: {
                '4': {
                    type: 'lf-fem',
                    output_ports: { '1': [{ label: 'q1.z', role: 'z', element: 'q1' }] },
                    input_ports: {},
                    digital_ports: {},
                },
            },
        },
    },
};
window.renderInstrumentWiring('iw-host2', noDigital, {}, null);
const svg2 = window.document.getElementById('iw-host2').querySelector('svg.instrument-svg');
ok(!!svg2, 'no-digital payload rendered an svg');
const texts2 = Array.prototype.map.call(svg2.querySelectorAll('text'), (t) => t.textContent);
ok(texts2.indexOf('DIG') !== -1, 'DIG header drawn without digital wiring');
const digCells2 = svg2.querySelectorAll('g.iw-port[data-io="digital"]');
ok(digCells2.length === 8,
   'eight empty digital slots drawn without digital wiring (got ' + digCells2.length + ')');
ok(Array.prototype.every.call(digCells2, (c) => !c.querySelector('circle[fill="#54617a"]')),
   'no digital slot carries an invented assignment');
/* One FEM: marginLeft 40 + (82 + 66 + 66) + 20 = 274, the digital layout's width */
ok(svg2.getAttribute('width') === '274',
   'no-digital svg has the DIG sub-column width 274 (got ' + svg2.getAttribute('width') + ')');
ok(svg.getAttribute('width') === '274',
   'digital svg width 274 (got ' + svg.getAttribute('width') + ')');

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('ALL OK');
process.exit(0);
