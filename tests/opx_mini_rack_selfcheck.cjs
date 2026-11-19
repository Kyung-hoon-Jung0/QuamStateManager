'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const dom = new JSDOM('<!doctype html><html><body><div id="ui-opxmon-body">' +
    '<div id="port-popup" class="ui-panel ui-opxmon-port-popup hidden"><span id="popup-label"></span>' +
    '<span id="popup-role-badge"></span><div id="popup-body"></div></div></div></body></html>',
    {url: 'http://localhost/opx-monitor/live', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document;
w.htmx = {ajax() { throw Error('read-only rack navigated'); }, trigger() {}, process() {}, on() {}};
w.fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve({}), text: () => Promise.resolve('')});
const staticDir = path.join(__dirname, '../quam_state_manager/web/static');
w.eval(fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8'));
const spec = {
    data: {controllers: {con1: {max_output_port: 8, fems: {
        1: {type: 'lf-fem', output_ports: {
            1: [{element: 'flux_a', label: 'flux_a', role: 'z'}],
            2: [{element: 'foreign', label: 'foreign', role: 'xy'}]
        }, input_ports: {1: [{element: 'read', label: 'read', role: 'rr_in'}]},
        digital_ports: {3: [{element: 'switch', label: 'switch', role: 'digital'}]},
        digital_input_ports: {2: [{element: 'capture', label: 'capture', role: 'digital'}]}},
        2: {type: 'mw-fem', output_ports: {}},
        7: {type: 'type unknown', output_ports: {}}
    }}}},
    highlight: [['con1', '1', '1', 'output'], ['con1', '1', '1', 'input'],
        ['con1', '1', '3', 'digital'], ['con1', '1', '2', 'digital-input'], ['con1', '7', '2', 'output']]
};
function markup(second = false) {
    const host = d.createElement('div');
    host.id = second ? 'opxmon-rack-QM-b' : 'opxmon-rack-QM-a';
    host.className = 'ui-opxmon-rack';
    const data = JSON.parse(JSON.stringify(spec));
    if (second) data.highlight = [['con1', '1', '2', 'output']];
    host.setAttribute('data-opxmon-rack', JSON.stringify(data));
    return host;
}
let draws = 0;
const renderer = w.renderInstrumentWiring;
w.renderInstrumentWiring = function(...args) { draws++; return renderer(...args); };
let popupBindings = 0;
const popup = d.getElementById('port-popup');
const bind = popup.addEventListener.bind(popup);
popup.addEventListener = function(...args) { popupBindings++; return bind(...args); };
const body = d.getElementById('ui-opxmon-body');
body.append(markup(), markup(true));
w.eval(fs.readFileSync(path.join(staticDir, 'opx-monitor.js'), 'utf8'));
let checks = 0;
function check(value, message) { checks++; assert.ok(value, message); }
function keys(host) {
    return Array.from(host.querySelectorAll('.ui-opxmon-port-active'), cell =>
        [cell.dataset.con, cell.dataset.slot, cell.dataset.port, cell.dataset.io].join('/')).sort();
}
function poll(...hosts) { body.replaceChildren(...hosts, popup.cloneNode(true)); w.OpxMonitor.afterSwap(); }
try {
    const a = d.getElementById('opxmon-rack-QM-a');
    const b = d.getElementById('opxmon-rack-QM-b');
    check(draws === 2, 'each open QM uses the shared renderer once');
    const block = d.createElement('div'); block.className = 'ui-opxmon-rack-block';
    a.replaceWith(block); block.append(a);
    const details = d.createElement('details'); details.id = 'opxmon-ports-QM-a'; block.after(details);
    let rackScrolls = 0, detailScrolls = 0;
    block.scrollIntoView = () => rackScrolls++;
    details.scrollIntoView = () => detailScrolls++;
    w.history.replaceState({}, '', '#opxmon-ports-QM-a');
    w.dispatchEvent(new w.HashChangeEvent('hashchange'));
    check(details.open && rackScrolls === 1 && detailScrolls === 0,
        'a Jobs ports link opens its QM details and scrolls to the mini rack');
    w.history.replaceState({}, '', '/opx-monitor/live');
    check(JSON.stringify(keys(a)) === JSON.stringify(spec.highlight.map(p => p.join('/')).sort()),
        'exact highlight set includes analog input and both digital directions');
    check(keys(b).join() === 'con1/1/2/output', 'second QM has independent highlights');
    check(a.querySelectorAll('.ui-opxmon-port-dim').length > 40, 'every other chassis port is dim');
    check(!a.querySelector('[data-element="foreign"]'), 'foreign assignments are removed');
    check(a.querySelector('.ui-opxmon-port-active circle').getAttribute('fill') === w.UI_CONFIG.roleColors.z,
        'a highlighted assignment retains its role color');
    check(a.querySelector('[data-slot="7"][data-port="2"][data-io="output"] .iw-port-circle'),
        'a declared port with no elements is filled');
    check(a.textContent.includes('type unknown'), 'unknown type stays honest');
    check(!a.querySelector('button, .iw-port-grip, [data-play], .iw-fitbar, .iw-sizebar'),
        'mini rack offers no editing, playback or size controls');
    const svg = a.querySelector('svg');
    check(Number(svg.getAttribute('width')) === Number(svg.dataset.natW) * 0.55, 'mini scale is 0.55');
    const circle = a.querySelector('[data-element="switch"]');
    circle.dispatchEvent(new w.MouseEvent('mouseenter', {bubbles: true}));
    check(d.getElementById('popup-label').textContent === 'switch' && !popup.classList.contains('hidden'),
        'digitalInputs-only element is named by the existing hover popup');
    a.querySelector('[data-element="flux_a"]').dispatchEvent(new w.MouseEvent('mouseenter', {bubbles: true}));
    check(d.getElementById('popup-role-badge').className === 'ui-badge' && popup.querySelector('.ui-facts-label'),
        'monitor hover uses the UI kit badge and facts');
    let inspectorClicks = 0;
    w._openInspectorForElement = () => inspectorClicks++;
    w._showInstrumentJsonPanel = () => inspectorClicks++;
    // Intercept the inspector delay, so an accidental click handler cannot hide behind the timer.
    const timer = w.setTimeout;
    w.setTimeout = fn => { fn(); return 1; };
    circle.dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
    circle.dispatchEvent(new w.MouseEvent('dblclick', {bubbles: true}));
    w.setTimeout = timer;
    check(inspectorClicks === 0, 'read-only cells do not navigate or edit');
    const oldSvg = a.querySelector('svg');
    poll(markup(), markup(true));
    check(draws === 2 && d.getElementById(a.id) === a && a.querySelector('svg') === oldSvg,
        'unchanged polling reuses each host and SVG');
    check(d.getElementById('port-popup') === popup && body.contains(popup),
        'polling keeps exactly one popup within the swapped body');
    const beforeTheme = draws;
    d.dispatchEvent(new w.CustomEvent('sm:themechange'));
    check(draws === beforeTheme + 2 && a.querySelector('svg') !== oldSvg,
        'theme path redraws both registered racks');
    check(popupBindings === 2, 'the shared popup gets one listener pair across racks and themes');
    const changed = markup();
    const changedSpec = JSON.parse(changed.getAttribute('data-opxmon-rack'));
    changedSpec.highlight = [['con1', '2', '1', 'output']];
    changed.setAttribute('data-opxmon-rack', JSON.stringify(changedSpec));
    poll(changed, markup(true));
    check(keys(changed).join() === 'con1/2/1/output', 'changed configuration replaces the highlight');
    check(w._iwDrawn.length === 2 && d.getElementById(b.id) === b,
        'changing one QM keeps the other registered and reuses its host');
    const themeDraws = draws;
    d.dispatchEvent(new w.CustomEvent('sm:themechange'));
    check(draws === themeDraws + 2, 'both changed and reused racks still follow the theme');
    b.remove();
    w.OpxMonitor.afterSwap();
    check(w._iwDrawn.length === 1, 'closed QMs leave no stale registry entries');
    body.replaceChildren();
    w.OpxMonitor.afterSwap();
    check(w._iwDrawn.length === 0, 'leaving Live releases registered rack nodes');
    for (const stats of [{refs_seen: 1, refs_placed: 0}, {octave_detected: true}]) {
        const host = d.createElement('div'); host.id = 'unplaceable'; body.replaceChildren(host);
        renderer(host.id, {controllers: {}, stats}, {}, {readOnly: true, highlight: [], scale: 0.55});
        check(!host.querySelector('svg') && /layout.*render yet/.test(host.textContent),
            'OPX+ and Octave display the renderer unplaceable message');
        w.releaseInstrumentWiring(host);
    }
    const locked = d.createElement('div'); locked.id = 'locked'; body.replaceChildren(locked);
    renderer(locked.id, spec.data, {}, {
        readOnly: true, editable: true, onPortPlay() { throw Error('read-only port played'); }, scale: 0.55
    });
    check(!locked.querySelector('button, .iw-port-grip, [data-play]'),
        'read-only overrides requested editing and playback');
    check(locked.querySelector('[data-port="8"][data-io="output"] circle').getAttribute('r') === '8',
        'read-only keeps unused ports as dim dots even when editable was requested');
    w.releaseInstrumentWiring(locked);
    const css = fs.readFileSync(path.join(staticDir, 'ui-kit.css'), 'utf8');
    check(/\.ui-opxmon-rack svg\.instrument-svg\s*\{[^}]*max-width:\s*100%;[^}]*height:\s*auto/.test(css),
        'mini SVG fits the pane without a crop or inline styles');
    if (process.argv.includes('--page')) {
        const page = new JSDOM(fs.readFileSync(0, 'utf8'));
        const pd = page.window.document;
        check(pd.querySelectorAll('#ui-opxmon-body #port-popup').length === 1,
            'server popup is inside the poll replacement boundary');
        check(pd.querySelectorAll('[data-opxmon-rack]').length === 2,
            'server emits one rack for each open QM');
        page.window.close();
    }
    console.log(checks + ' all checks passed');
} finally {
    dom.window.close();
}
