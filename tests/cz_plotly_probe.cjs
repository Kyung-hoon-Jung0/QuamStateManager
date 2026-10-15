'use strict';
// Execute the production queue and the shipped formatter/purge functions without a browser.
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const {EventEmitter} = require('events');
const root = path.join(__dirname, '..');

function emitter(el) {
    if (!el._ev) {
        el._ev = new EventEmitter();
        el.on = (event, fn) => { el._ev.on(event, fn); return el; };
    }
}
function queue(w, renders) {
    const app = fs.readFileSync(path.join(root, 'quam_state_manager/web/static/app.js'), 'utf8');
    const start = app.indexOf('window._plotlyRender = function(');
    const end = app.indexOf('\n};', start) + 3;
    assert(start > 0 && end > start);
    const draw = (el, traces, layout) => {
        emitter(el);
        renders.push({id: el.id, traces, layout});
        el.data = traces;
        el.classList.add('js-plotly-plot');
        el.textContent = 'numeric plot';
        return Promise.resolve(el);
    };
    w.Plotly = {newPlot: draw, react: draw, purge: el => { purge(el); delete el.data; }};
    w.eval(app.slice(start, end));
}
const vendor = fs.readFileSync(path.join(root, 'quam_state_manager/web/static/plotly.min.js'), 'utf8');
const purgeStart = vendor.indexOf('purge:function(t){return delete t._ev');
const purgeEnd = vendor.indexOf('}};t.exports=i}', purgeStart);
assert(purgeStart > 0 && purgeEnd > purgeStart);
const purge = new Function('return (' + vendor.slice(purgeStart + 6, purgeEnd + 1) + ');')();
const start = vendor.indexOf('function U(t,e,r){var n=this,a=arguments;e||(e={});');
const end = vendor.indexOf('h.subplotSort=', start);
assert(start > 0 && end > start);
const h = {TEMPLATE_STRING_REGEX: /%{([^\s%{}:]*)([:|\|][^}]*)?}/g,
    numberFormat: () => value => String(value), warn: () => {},
    nestedProperty: (obj, key) => ({get: () => obj[key]})};
const interpolate = new Function('h', 'var D=/^\\w*$/,j=/^[:|\\|]/,B=/^x$/; ' + vendor.slice(start, end) + ';return U;')(h);
function hover(template, values) {
    return interpolate.call({count: 0, max: 10, name: 'hovertemplate'}, template, {}, null, values);
}
function purgeWithin(w, el) {
    const app = fs.readFileSync(path.join(root, 'quam_state_manager/web/static/app.js'), 'utf8');
    function source(name) {
        const start = app.indexOf('    function ' + name + '(');
        const end = app.indexOf('\n    }', start) + 6;
        assert(start > 0 && end > start);
        return app.slice(start, end);
    }
    return new Function('document', 'Plotly', source('_graphDivs') + source('purgeWithin') + ';return purgeWithin;')(w.document, w.Plotly)(el);
}
module.exports = {emitter, queue, purge, hover, purgeWithin};
