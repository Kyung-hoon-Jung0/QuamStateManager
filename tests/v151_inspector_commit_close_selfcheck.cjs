/* R3B M6 -- an inspector closed by a navigation does not come back when the
 * value typed into it commits on the way out.
 *
 * Type a value into the q2 inspector, then click a sidebar page without
 * pressing Enter: the click's blur commits the value (a POST that targets
 * #inspector-pane), then the click closes the inspector for a page that
 * cannot show a qubit. The commit's answer lands after the close; it used to
 * swap the q2 inspector back over the new page (the P1-1 symptom again).
 * Now the edit lands, its out-of-band tray swaps, and the inspector stays
 * closed. A commit on a page that shows the inspector still re-renders it.
 *
 * REAL htmx.min.js + REAL app.js + the REAL split script of base.html, on
 * the REAL /qubits sidebar link, inspector pane, review tray and q2
 * inspector, answered with the REAL /qubit/q2/edit response (a fake
 * XMLHttpRequest stands in for the server). The fixture is written by
 * tests/test_v151_r5b_inspector_commit_close.py.
 *
 * Run: node tests/v151_inspector_commit_close_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const ROOT = path.join(__dirname, '..');
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
const base = fs.readFileSync(path.join(ROOT, 'quam_state_manager', 'web', 'templates', 'base.html'), 'utf8');
const bodyScripts = [...base.slice(base.indexOf('<body')).matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
    .filter(m => !/\bsrc\s*=|application\/json/i.test(m[1])).map(m => m[2]);
const registryScript = bodyScripts.find(s => s.includes('registry.replace = function'));
const splitScript = bodyScripts.find(s => s.includes("__smBase.replace('split')"));

let fails = 0, n = 0;
function ok(c, m) { n++; if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));

function world(page) {
    const body = '<nav class="sidebar-nav">' + FX.link + '</nav>' + FX.tray
        + '<div id="table-pane">' + FX.table + '</div>' + FX.pane_open + FX.inspector + '</div>'
        + '<div id="status-bar"></div>';
    const dom = new JSDOM('<!doctype html><html><head></head><body>' + body + '</body></html>',
        { url: 'http://localhost' + page, runScripts: 'dangerously', pretendToBeVisual: true });
    const w = dom.window;
    w.Element.prototype.scrollIntoView = function () {};
    w.XPathEvaluator = function () {};
    w.XPathEvaluator.prototype.createExpression = function () {
        return { evaluate: function () { return { iterateNext: function () { return null; } }; } };
    };
    const xhrs = [];
    class FakeXHR {
        constructor() { this.readyState = 0; this.status = 0; this._h = {}; this.aborted = false; this.answered = false;
                        this.upload = { addEventListener() {} }; this.headers = {}; this.reqHeaders = {}; xhrs.push(this); }
        open(m, u) { this.method = m; this.url = u; }
        setRequestHeader(k, v) { this.reqHeaders[k] = v; }
        overrideMimeType() {}
        addEventListener(nm, f) { (this._h[nm] = this._h[nm] || []).push(f); }
        getAllResponseHeaders() { return Object.keys(this.headers).map((k) => k + ': ' + this.headers[k]).join('\r\n'); }
        getResponseHeader(k) {
            const hit = Object.keys(this.headers).find((h) => h.toLowerCase() === String(k).toLowerCase());
            return hit ? this.headers[hit] : null;
        }
        abort() { this.aborted = true; if (this.onabort) this.onabort(); (this._h.abort || []).forEach(f => f({})); (this._h.loadend || []).forEach(f => f({})); }
        send(b) { this.body = b; this.sent = true; }
        answer(status, text, headers) {
            if (this.aborted || this.answered) return;
            this.answered = true;
            this.headers = headers || {};
            this.status = status; this.responseText = text; this.response = text;
            this.responseURL = 'http://localhost' + this.url; this.readyState = 4;
            if (this.onload) this.onload();
            (this._h.load || []).forEach(f => f({}));
            (this._h.loadend || []).forEach(f => f({}));
        }
    }
    w.XMLHttpRequest = FakeXHR;
    w.fetch = function () { return new w.Promise(function () {}); };
    w.eval(fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8'));
    w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
    w.Split = (selectors, options) => {
        const panes = selectors.map(s => w.document.querySelector(s));
        const gutter = w.document.createElement('div');
        gutter.className = 'gutter gutter-vertical';
        panes[0].after(gutter);
        return { pairs: [{ gutter }], options, getSizes: () => options.sizes.slice(), destroy: () => gutter.remove() };
    };
    w.UI_CONFIG = Object.assign({}, w.UI_CONFIG || {}, { split: { defaultSizes: [55, 45], expandedSizes: [15, 85],
        collapsedSizes: [85, 15], datasetExpandedSizes: [25, 75], minSizes: [0, 60], gutterSize: 26 } });
    w.eval(registryScript);
    w.eval(splitScript);
    const d = w.document;
    return {
        w, d, xhrs,
        pane: () => d.getElementById('inspector-pane'),
        qubitShown: () => !!d.querySelector('#inspector-pane .inspector-header-qubit'),
        input: () => {
            const h = d.querySelector('#inspector-pane input[name="dot_path"][value="' + FX.dot_path + '"]');
            return h ? h.form.querySelector('input[name="value"]') : null;
        },
        post: () => xhrs.find((x) => x.method === 'POST' && x.url.indexOf(FX.edit_url) === 0),
        get: (u) => xhrs.find((x) => x.method === 'GET' && x.url.indexOf(u) === 0),
        tray: () => d.getElementById('pending-tray'),
    };
}

// the value typed, then focus leaves the field the way a click on a link
// takes it (a focusout that names where the focus went)
function typeAndLeave(T, to) {
    const input = T.input();
    input.focus();
    input.value = FX.value;
    if (to) to.focus();
    if (!T.post()) {
        input.dispatchEvent(new T.w.FocusEvent('focusout', { bubbles: true, relatedTarget: to || null }));
    }
}

(async function () {
    if (!registryScript || !splitScript) { console.error('FAIL: the split scripts are not found'); process.exit(1); }

    // 1. the reported case: Qubits, q2 inspector, type, click Datasets
    let T = world('/qubits');
    await tick();
    ok(T.qubitShown() && !!T.input(), 'the real q2 inspector is open with its ' + FX.dot_path + ' field');
    const link = T.d.querySelector('.sidebar-nav a[href="/datasets"]');
    typeAndLeave(T, link);
    await tick();
    const post = T.post();
    ok(!!post && post.sent, 'the blur commits the typed value (a POST to ' + FX.edit_url + ')');
    link.click();
    await tick();
    ok(!T.qubitShown() && T.pane().innerHTML.trim() === '', 'the click closes the inspector for Datasets');
    const nav = T.get('/datasets');
    ok(!!nav, 'and the page is asked for');
    if (nav) nav.answer(200, FX.datasets, { 'HX-Push-Url': '/datasets' });
    await tick();
    ok(post && !post.aborted, 'the commit is not aborted by the close');
    ok(post && /value=/.test(String(post.body || '')) && String(post.body).indexOf(encodeURIComponent(FX.value)) >= 0
       && String(post.body).indexOf(encodeURIComponent(FX.dot_path)) >= 0,
       'it carries the typed value for the field: ' + String(post && post.body));
    post.answer(200, FX.edit_response, FX.edit_headers || {});
    await tick(60);
    ok(!T.qubitShown(), 'the commit\'s answer does NOT bring the q2 inspector back over Datasets');
    ok(T.pane().innerHTML.trim() === '', 'the inspector pane stays empty');
    // htmx looks for the answer's out-of-band tray beside the form the close
    // removed, so it cannot land: the tray is asked for instead
    const trayGet = T.get('/state/tray');
    ok(!!trayGet, 'the tray is asked for (its out-of-band copy had nowhere to land)');
    if (trayGet) trayGet.answer(200, FX.tray_after);
    await tick(60);
    ok(T.tray() && T.tray().getAttribute('data-change-count') === '1',
       'and shows the edit staged: ' + (T.tray() && T.tray().getAttribute('data-change-count')));

    // 2. the answer arriving before the page: the same
    T = world('/qubits');
    await tick();
    const link2 = T.d.querySelector('.sidebar-nav a[href="/datasets"]');
    typeAndLeave(T, link2);
    await tick();
    link2.click();
    await tick();
    T.post().answer(200, FX.edit_response, FX.edit_headers || {});
    await tick(60);
    ok(!T.qubitShown(), 'an answer landing before the page does not bring it back either');
    const nav2 = T.get('/datasets');
    if (nav2) nav2.answer(200, FX.datasets, { 'HX-Push-Url': '/datasets' });
    await tick();
    ok(!T.qubitShown(), 'nor after the page lands');

    // 3. a commit on a page that shows the inspector re-renders it, as always
    T = world('/qubits');
    await tick();
    typeAndLeave(T, null);
    await tick();
    T.post().answer(200, FX.edit_response, FX.edit_headers || {});
    await tick(60);
    ok(T.qubitShown(), 'a commit on Qubits re-renders the q2 inspector');

    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
