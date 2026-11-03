'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
const staticDir = path.join(__dirname, '../quam_state_manager/web/static');
const source = fs.readFileSync(path.join(staticDir, 'port-identity.js'), 'utf8');
const tick = (ms = 20) => new Promise(resolve => setTimeout(resolve, ms));
const offer = {port_identity: {message: 'Identity differs'}};
function boot(html = '<div id="target"></div>') {
    const errors = [];
    const vc = new VirtualConsole();
    vc.on('jsdomError', error => errors.push(error));
    const dom = new JSDOM(html, {url: 'http://localhost/', runScripts: 'outside-only', virtualConsole: vc});
    const w = dom.window;
    w.Headers = Headers;
    w.Request = Request;
    w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    w.HTMLDialogElement.prototype.close = function () { this.open = false; };
    return {dom, w, errors};
}
function answer(w, text) {
    assert.equal(w.document.querySelectorAll('dialog').length, 1, 'one unanswered dialog');
    [...w.document.querySelectorAll('dialog button')].find(b => b.textContent === text).click();
}
async function fetchChecks() {
    const {dom, w, errors} = boot();
    const posts = [];
    const saved = [];
    w.FormData = FormData;
    w.fetch = async (input, init) => {
        const req = new Request(input.url || new URL(input, w.location.href), init || input);
        const body = await req.text();
        posts.push({body, token: req.headers.get('X-Token')});
        if (!body.includes('port_ack')) return new Response(JSON.stringify(offer), {
            status: 409, headers: {'Content-Type': 'text/plain'}});
        const response = new Response(JSON.stringify({ok: true, row: posts.length}));
        saved.push(response);
        return response;
    };
    w.eval(source);
    let repaints = 0;
    const promises = [1, 2, 3].map(row => w.fetch('/field/edit', {
        method: 'POST', body: 'value=' + row, headers: {'X-Token': 'retained'}
    }).then(response => { repaints++; return response; }));
    await tick();
    assert.equal(posts.length, 3);
    assert.equal(repaints, 0);
    const first = w.document.querySelector('dialog');
    await tick();
    assert.equal(w.document.querySelector('dialog'), first, 'pending requests never replace an unanswered dialog');
    answer(w, 'Fix other side too');
    await tick();
    answer(w, 'Keep as typed');
    await tick();
    answer(w, 'Cancel');
    const responses = await Promise.all(promises);
    assert.equal(responses[0], saved[0], 'original caller receives retry response');
    assert.equal(responses[1], saved[1]);
    assert.equal(responses[2].status, 409);
    assert.equal(posts.length, 5);
    assert(posts.every(post => post.token === 'retained'));
    const request = new Request('http://localhost/field/edit-batch', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Token': 'retained'},
        body: JSON.stringify({updates: [{dot_path: 'ports.analog_outputs.con1.2.1.port_id', value: 4}]})});
    const pending = w.fetch(request);
    await tick();
    answer(w, 'Fix other side too');
    assert.equal(await pending, saved[2]);
    assert.equal(JSON.parse(posts.at(-1).body).port_ack, 'fix');
    const form = new FormData();
    form.append('value', '4');
    form.append('tag', 'a');
    form.append('tag', 'b');
    const formPending = w.fetch(new Request('http://localhost/field/edit', {method: 'POST', body: form}));
    await tick();
    answer(w, 'Keep as typed');
    assert.equal(await formPending, saved[3]);
    assert(posts.at(-1).body.includes('name="port_ack"'));
    assert(posts.at(-1).body.includes('name="tag"'));
    await tick(550); // Detect the former delayed reload too.
    assert.equal(errors.length, 0, errors.map(String).join('\n'));
    assert(!source.includes('location.reload'), 'transport must let callers repaint');
    dom.window.close();
}
async function htmxChecks() {
    const {dom, w, errors} = boot('<form id="form" hx-post="/qubit/q1/edit" hx-target="#target">' +
        '<input name="value" value="4"><input name="tag" value="a"><input name="tag" value="b">' +
        '<button>Save</button></form><div id="target"></div>');
    const posts = [];
    class XHR {
        constructor() { this.upload = {addEventListener() {}}; this.headers = {}; }
        open(method, url) { this.method = method; this.url = url; }
        overrideMimeType() {}
        addEventListener() {}
        setRequestHeader(key, value) { this.headers[key] = value; }
        getAllResponseHeaders() { return 'Content-Type: text/plain\r\n'; }
        getResponseHeader() { return null; }
        send(body) {
            posts.push({body: new URLSearchParams(body), headers: this.headers});
            setTimeout(() => {
                this.status = posts.at(-1).body.has('port_ack') ? 200 : 409;
                this.response = this.responseText = this.status === 409 ? JSON.stringify(offer) : '<b>Repainted</b>';
                this.responseURL = 'http://localhost' + this.url;
                this.onload();
            }, 0);
        }
    }
    w.XMLHttpRequest = XHR;
    w.XPathEvaluator = function () {};
    w.XPathEvaluator.prototype.createExpression = () => ({evaluate: () => ({iterateNext: () => null})});
    w.fetch = () => Promise.reject(new Error('unexpected fetch'));
    w.eval(fs.readFileSync(path.join(staticDir, 'htmx.min.js'), 'utf8'));
    w.eval(source);
    await tick();
    w.htmx.process(w.document.body);
    let proxySeen = false;
    w.document.addEventListener('htmx:beforeSwap', event => {
        if (event.detail.xhr.status === 409) {
            proxySeen = event.detail.requestConfig.formData instanceof w.FormData;
            assert.deepEqual([...event.detail.requestConfig.parameters.getAll('tag')], ['a', 'b']);
        }
    });
    w.document.querySelector('form').dispatchEvent(new w.Event('submit', {bubbles: true, cancelable: true}));
    await tick();
    assert.equal(errors.length, 0, errors.map(String).join("\n"));
    assert.equal(posts.length, 1);
    // Changes made while the dialog is open must not replace the original request.
    w.document.querySelector('[name=value]').value = '99';
    w.document.querySelectorAll('[name=tag]')[0].value = 'changed';
    answer(w, 'Fix other side too');
    await tick(80);
    assert(proxySeen, 'real bundled HTMX provides FormData and its parameters proxy');
    assert.equal(posts.length, 2);
    assert.deepEqual(posts[1].body.getAll('tag'), ['a', 'b']);
    assert.equal(posts[1].body.get('port_ack'), 'fix');
    assert.equal(posts[1].body.get('value'), '4');
    assert.equal(w.document.querySelector('#target').textContent, 'Repainted');
    assert.equal(errors.length, 0, errors.map(String).join('\n'));
    dom.window.close();
}
(async () => {
    await fetchChecks();
    await htmxChecks();
    console.log('port_identity_selfcheck: all ok');
})().catch(error => { console.error(error); process.exit(1); });
