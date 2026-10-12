'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({JSDOM} = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }
const dom = new JSDOM('<!doctype html><html><body>' +
    '<div id="plot-apply-popup"><div id="plot-apply-context"></div>' +
    '<div id="plot-apply-extra"></div><div id="plot-apply-verdict"></div>' +
    '<div id="plot-apply-rows"></div><button id="plot-apply-all">Apply All</button></div>' +
    '</body></html>', {url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window;
const d = w.document;
const p1 = 'qubits.q1.T1', p2 = 'qubits.q2.T1';
const posts = [];
let replies = [];
let checks = 0;
function check(condition, message) { checks++; assert.ok(condition, message); }
const delay = () => new Promise(resolve => setTimeout(resolve, 30));
w.fetch = (url, opts) => {
    let reply = {ok: true};
    let status = 200;
    if (opts && opts.method === 'POST') {
        posts.push({url, opts});
        if (replies.length) { const r = replies.shift(); reply = r.body; status = r.status; }
    } else if (url.startsWith('/chip/active-token')) {
        reply = {loaded: true, token: 'chip-token', path: '/scratch/live'};
    } else if (url.startsWith('/field/peek')) {
        reply = {ok: true, values: {[p1]: 0.00002, [p2]: 0.00003}, resolved: {}, errors: {}};
    }
    return Promise.resolve({ok: status === 200, status, json: () => Promise.resolve(reply)});
};
w.htmx = {ajax: () => Promise.resolve(), on: () => {}, trigger: () => {}, process: () => {}};
w.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8'));
w._confirmTypeFix = () => true;
w._openFspPopup = (plan, cb) => cb('comp', plan);
w._fspCompUpdates = () => [{dot_path: 'qubits.q1.xy.operations.x180.amplitude', value: '0.2'}];
function row() { return d.querySelector('.plot-apply-row'); }
async function open(updates) {
    w.closePlotApplyPopup();
    posts.length = 0;
    replies = [];
    w._openPlotApplyPopup(updates, 'synthetic_node', 'q1', [], null, 'root:7');
    await delay();
}
function form(post) { return new URLSearchParams(post.opts.body); }
function json(post) { return JSON.parse(post.opts.body); }
const learned = {formula: 'clicked * 2 + 1', evidence_tier: 'proposed',
                 inputs: [{label: 'frozen old', frozen_value: 2}]};
const map = {request_hash: 'abc123', request: {
    fit: {model: 'extremum'}, smooth: {kernel: 'gaussian', sigma_x: 2, sigma_y: 0},
    contour: {on: true, level: 180}}};
(async () => {
    const root = d.createElement('div');
    root.id = 'ds-detail-root';
    root.setAttribute('data-uid', 'root:7');
    const plot = d.createElement('div');
    root.appendChild(plot); d.body.appendChild(root);
    let click;
    plot.on = (_, fn) => { click = fn; };
    w._attachInteractivePlotClickHandler(plot, {qubit: 'q1', axis: 'x', targets: [
        {path: p1, axis: 'x', evidence_tier: 'applied', provenance: {formula: 'clicked * 2 + 1'}}]}, '7');
    click({points: [{x: 3, y: 0}]});
    await delay();
    w.applyPlotRow(row()); await delay();
    check(form(posts[0]).has('provenance'), 'real click posts provenance');
    check(JSON.parse(form(posts[0]).get('provenance')).text.includes('patch (applied)'), 'real click handler carries evidence tier to the write');
    root.remove();

    await open([{dot_path: p1, value: 3, provenance: learned}]);
    check(row().textContent.includes(learned.formula), 'learned formula remains displayed');
    row().querySelector('input').value = '4';
    w.applyPlotRow(row());
    await delay();
    const first = form(posts[0]);
    check(first.has('provenance'), 'single form includes origin');
    const origin = JSON.parse(first.get('provenance'));
    check(first.get('value') === '4', 'editable row still sends the edited value');
    check(origin.src === 'plot_click' && origin.run_uid === 'root:7', 'click origin and run ride the form');
    check(origin.text.includes('proposal (proposed, not applied)') && origin.text.includes(learned.formula), 'tier and formula ride the form');
    check(origin.text.includes('frozen old=2'), 'frozen inputs are recorded');
    check(Object.keys(origin).sort().join(',') === 'run_uid,src,text', 'only bounded wire keys are sent');

    await open([{dot_path: p1, value: 3, provenance: map}, {dot_path: p2, value: 4, provenance: learned}]);
    check(row().textContent.includes('sigma x=2 pt, y=0 pt') && row().textContent.includes('contour level 180'), 'map origin is displayed');
    w.applyAllPlotRows();
    await delay();
    const mixed = json(posts[0]);
    check(!!mixed.updates[0].provenance && !!mixed.updates[1].provenance, 'both batch rows include origins');
    check(mixed.updates[0].provenance.src === 'map_fit', 'request provenance names the map fit');
    check(mixed.updates[0].provenance.request_hash === 'abc123', 'map request hash rides Apply All');
    check(mixed.updates[0].provenance.text.includes('extremum') && mixed.updates[0].provenance.text.includes('contour level 180'), 'map model and contour ride Apply All');
    check(mixed.updates[1].provenance.src === 'plot_click' && !mixed.provenance, 'mixed rows retain distinct origins');

    await open([{dot_path: p1, value: 3, provenance: map}, {dot_path: p2, value: 4, provenance: map}]);
    replies = [{status: 409, body: {ok: false, type_fix: {}}}, {status: 200, body: {ok: true}}];
    w.applyAllPlotRows();
    await delay(); await delay();
    check(posts.length === 2 && json(posts[1]).type_fix === 'convert', 'batch type-fix retry uses existing ack');
    check(!!json(posts[1]).provenance && !!json(posts[0]).provenance, 'homogeneous batch carries top-level origin on both attempts');
    check(JSON.stringify(json(posts[1]).provenance) === JSON.stringify(json(posts[0]).provenance), 'batch retry preserves top-level origin');
    check(json(posts[1]).provenance.request_hash === 'abc123', 'homogeneous batch sends top-level provenance');

    await open([{dot_path: p1, value: 3, provenance: map}]);
    replies = [{status: 409, body: {ok: false, type_fix: {}}}, {status: 200, body: {ok: true}}];
    w.applyPlotRow(row()); await delay(); await delay();
    check(posts.length === 2 && form(posts[1]).get('type_fix') === 'convert', 'single type-fix retry uses existing ack');
    check(form(posts[1]).get('provenance') === form(posts[0]).get('provenance'), 'single type-fix retry preserves origin');

    await open([{dot_path: p1, value: 3, provenance: map}]);
    replies = [{status: 409, body: {ok: false, fsp_compensation: {amps: [{}]}}},
               {status: 409, body: {ok: false, type_fix: {}}}, {status: 200, body: {ok: true}}];
    w.applyPlotRow(row());
    await delay(); await delay();
    check(posts.length === 3 && posts[1].url === '/field/edit-batch', 'FSP retry uses the existing batch door');
    check(json(posts[2]).fsp_ack === 'comp' && json(posts[2]).type_fix === 'convert', 'FSP and type-fix acks accumulate');
    check(JSON.stringify(json(posts[2]).provenance) === JSON.stringify(JSON.parse(form(posts[0]).get('provenance'))), 'origin survives FSP and type-fix retries');

    await open([{dot_path: p1, value: 3, provenance: {src: 'map_fit', text: '<script>x</script>' + 'x'.repeat(400)}}]);
    check(!row().querySelector('script'), 'provenance is escaped in the popup');
    w.applyPlotRow(row()); await delay();
    check(JSON.parse(form(posts[0]).get('provenance')).text.length === 300, 'wire text is bounded');

    await open([{dot_path: p1, value: 3}]);
    w.applyPlotRow(row()); await delay();
    check(!form(posts[0]).has('provenance'), 'ordinary row keeps its existing form shape');
    await open([{dot_path: p1, value: 3}, {dot_path: p2, value: 4}]);
    w.applyAllPlotRows(); await delay();
    check(!json(posts[0]).provenance && json(posts[0]).updates.every(u => !u.provenance), 'ordinary batch keeps its existing JSON shape');
    console.log(checks + ' assertions passed');
    dom.window.close();
})().catch(e => { console.error(e); dom.window.close(); process.exit(1); });
