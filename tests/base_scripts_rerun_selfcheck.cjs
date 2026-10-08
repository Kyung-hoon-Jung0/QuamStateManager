/* Run the actual body scripts with controlled events, timers, and responses. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '..');
const templates = path.join(root, 'quam_state_manager', 'web', 'templates');
const base = fs.readFileSync(path.join(templates, 'base.html'), 'utf8');
const body = base.slice(base.indexOf('<body'));
function scripts(text) {
    text = text.replace(/\{#[\s\S]*?#\}/g, '');
    return [...text.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
        .filter(match => !/\bsrc\s*=|application\/json/i.test(match[1]))
        .map(match => match[2]);
}
function expandIncludes(text, parents = []) {
    return text.replace(/\{%\s*include\s+['"]([^'"]+)['"][\s\S]*?%\}/g, (_, name) => {
        assert(!parents.includes(name), 'include cycle');
        return expandIncludes(fs.readFileSync(path.join(templates, name), 'utf8'), [...parents, name]);
    });
}
const baseScripts = scripts(body);
assert.equal(baseScripts.length, 6, 'extract every executable base body script');
const allScripts = scripts(expandIncludes(body));
assert(allScripts.length >= baseScripts.length, 'include scripts are exercised too');
allScripts.forEach(source => assert(!/\{[{%]/.test(source), 'body scripts require no unresolved template values'));
const app = fs.readFileSync(path.join(root, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const autocompleteStart = app.indexOf('window.initPathAutocomplete = function(');
assert(autocompleteStart >= 0, 'extract the autocomplete initializer');
const autocomplete = app.slice(autocompleteStart, app.indexOf('\n};', autocompleteStart) + 3);

const markup = `
<div id="exp-density-full"></div><div id="exp-density-compact"></div>
<div id="diagnostics-banner-slot"></div>
<div class="app-layout"><aside id="sidebar" style="width:260px">
<textarea id="sidebar-filter-input"></textarea><div id="sidebar-tree"><div data-ws-version="1"></div></div>
<form><div><input id="workspace-path-input"></div><button type="submit">Load</button></form>
<form><div><input id="load-path-input"></div><button type="submit">Load</button></form>
</aside><div id="sidebar-resizer"></div><main>
<div id="table-pane">Table</div><div id="inspector-pane">Details</div>
</main></div><div id="status-bar"></div>
<div id="scheduler-badge"><span class="scheduler-badge-label"></span><span class="scheduler-badge-count"></span></div>
<div id="autofit-nav-badge"></div><form id="compare-form"></form>`;

const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
async function scenario(mode) {
    const dom = new JSDOM(`<!doctype html><html><body class="exp-list-compact">${markup}</body></html>`, {
        url: 'https://example.invalid/view', runScripts: 'outside-only', pretendToBeVisual: true,
    });
    const w = dom.window;
    const d = w.document;
    // Finish jsdom's own initial events before instrumenting application activity.
    await new Promise(resolve => setImmediate(resolve));
    let readyState = mode === 'loading' ? 'loading' : 'complete';
    Object.defineProperty(d, 'readyState', { get: () => readyState, configurable: true });
    d.querySelectorAll('*');
    const records = [];
    const nativeAdd = w.EventTarget.prototype.addEventListener;
    const nativeRemove = w.EventTarget.prototype.removeEventListener;
    const capture = options => typeof options === 'boolean' ? options : !!(options && options.capture);
    w.EventTarget.prototype.addEventListener = function (type, callback, options) {
        if (!records.some(r => r.target === this && r.type === type && r.callback === callback && r.capture === capture(options))) {
            const record = { target: this, type, callback, capture: capture(options) };
            records.push(record);
            // Signal-based removal is native and bypasses the removeEventListener method.
            if (options && options.signal) nativeAdd.call(options.signal, 'abort', () => {
                const index = records.indexOf(record);
                if (index >= 0) records.splice(index, 1);
            }, { once: true });
        }
        return nativeAdd.call(this, type, callback, options);
    };
    w.EventTarget.prototype.removeEventListener = function (type, callback, options) {
        const index = records.findIndex(r => r.target === this && r.type === type && r.callback === callback && r.capture === capture(options));
        if (index >= 0) records.splice(index, 1);
        return nativeRemove.call(this, type, callback, options);
    };
    let nextTimer = 0;
    const intervals = new Map(), timeouts = new Map(), observers = new Set();
    w.setInterval = (callback, delay) => { const id = ++nextTimer; intervals.set(id, { callback, delay }); return id; };
    w.clearInterval = id => intervals.delete(id);
    w.setTimeout = (callback, delay) => { const id = ++nextTimer; timeouts.set(id, { callback, delay }); return id; };
    w.clearTimeout = id => timeouts.delete(id);
    function fireTimeouts(delay) {
        for (const [id, timer] of [...timeouts]) if (timer.delay === delay) {
            timeouts.delete(id);
            timer.callback();
        }
    }
    w.MutationObserver = class {
        constructor(callback) { this.callback = callback; }
        observe(target) { this.target = target; observers.add(this); }
        disconnect() { observers.delete(this); }
    };
    const requests = [], processed = [], ajaxCalls = [];
    w.fetch = (url, options) => new Promise(resolve => requests.push({ url, options, resolve }));
    const respond = (request, data) => request.resolve({ json: () => Promise.resolve(data) });
    const settlePolls = async () => {
        for (const request of requests.splice(0)) respond(request, request.url === '/workspace/tree/poll' ? { v: 1 } : {});
        await tick();
    };
    w.htmx = { process: element => processed.push(element), ajax: (...args) => { ajaxCalls.push(args); return Promise.resolve(); } };
    w.UI_CONFIG = { autoRefreshInterval: 5, split: {
        defaultSizes: [60, 40], expandedSizes: [20, 80], collapsedSizes: [80, 20], minSizes: [0, 0], gutterSize: 8,
    } };
    let splitCalls = 0, splitDestroyed = 0;
    const liveSplits = new Set();
    let bundledSplit;
    if (mode === 'drag') {
        w.eval(fs.readFileSync(path.join(root, 'quam_state_manager', 'web', 'static', 'split.min.js'), 'utf8'));
        bundledSplit = w.Split;
    }
    w.Split = (selectors, options) => {
        splitCalls++;
        if (bundledSplit) {
            const instance = bundledSplit(selectors, options);
            const destroy = instance.destroy;
            instance.destroy = (...args) => {
                assert(liveSplits.delete(instance), 'each bundled split is destroyed once');
                splitDestroyed++;
                destroy(...args);
            };
            liveSplits.add(instance);
            return instance;
        }
        const panes = selectors.map(selector => d.querySelector(selector));
        const gutter = d.createElement('div');
        gutter.className = 'gutter gutter-vertical';
        panes[0].after(gutter);
        const instance = { pairs: [{ gutter }], getSizes: () => options.sizes, destroy: () => {
            assert(liveSplits.delete(instance), 'each split is destroyed once');
            splitDestroyed++;
            gutter.remove();
        } };
        liveSplits.add(instance);
        return instance;
    };
    w.sessionStorage.setItem('quam_diag_banner_h', '18');
    w.localStorage.setItem('quam_exp_list_compact', '0');
    w.__activePath = '/state';
    w.__activeDataPath = '/data';
    w.eval(autocomplete);
    function run() { for (const source of allScripts) w.eval(source); }
    function finishLoading() {
        readyState = 'interactive';
        d.dispatchEvent(new w.Event('DOMContentLoaded'));
        readyState = 'complete';
    }
    function snapshot() {
        const listeners = {};
        for (const r of records) {
            const target = r.target === d ? 'document' : r.target === w ? 'window' : r.target === d.body ? 'body' :
                r.target.id || r.target.className || r.target.tagName;
            const key = `${target}:${r.type}`;
            listeners[key] = (listeners[key] || 0) + 1;
        }
        return { listeners, intervals: intervals.size, timeouts: timeouts.size, observers: observers.size };
    }
    function assertCurrent() {
        assert.equal(d.querySelectorAll('.gutter').length, 1, 'one gutter on the current pane pair');
        assert.equal(liveSplits.size, 1, 'one live Split instance');
        assert.equal(d.querySelectorAll('.path-suggestions').length, 2, 'one autocomplete per input');
        assert.equal(d.querySelectorAll('.diag-banner-placeholder').length, 1, 'one reserved placeholder');
        assert.equal(d.getElementById('exp-density-full').getAttribute('aria-pressed'), 'true', 'density follows the current body');
        assert.equal(d.getElementById('workspace-path-input').value, '/data', 'current input initialized');
    }
    function swap(target) { target.dispatchEvent(new w.CustomEvent('htmx:afterSwap', { bubbles: true, detail: { target } })); }
    function startDrag() { d.getElementById('sidebar-resizer').dispatchEvent(new w.MouseEvent('pointerdown', { bubbles: true, clientX: 10 })); }

    const densityButtons = ['exp-density-full', 'exp-density-compact'].map(id => d.getElementById(id));
    if (readyState === 'loading') densityButtons.forEach(button => button.remove());
    run();
    if (readyState === 'loading') {
        const pending = snapshot();
        run();
        run();
        assert.deepEqual(snapshot(), pending, 'reruns before DOMContentLoaded retain one pending initialization');
        assert.equal(observers.size, 1, 'one density observer while the toolbar is still parsing');
        densityButtons.forEach(button => d.body.appendChild(button));
        finishLoading();
    }
    await settlePolls();
    const first = snapshot();
    assert.equal(first.intervals, 2, 'both pollers start');
    assert.equal(first.listeners['body:schedulerLocked'], 1, 'one lock notice');
    assertCurrent();
    for (let runNumber = 2; runNumber <= 3; runNumber++) {
        const previousOwners = ['density', 'sidebar', 'paths', 'split', 'scheduler'].map(key => w.__smBase[key]);
        const oldInput = d.getElementById('workspace-path-input');
        const oldBody = d.body;
        const cached = d.body.innerHTML;
        startDrag();
        if (mode === 'drag') {
            d.querySelector('.gutter').dispatchEvent(new w.MouseEvent('mousedown', { bubbles: true, clientY: 20 }));
            assert(records.some(record => record.target === w && record.type === 'mousemove'), 'bundled Split has an active window drag');
        }
        d.getElementById('status-bar').innerHTML = '<span>Saved</span>';
        swap(d.getElementById('status-bar'));
        d.querySelector('.split-set-high-btn').click();
        oldInput.dispatchEvent(new w.Event('input'));
        oldInput.dispatchEvent(new w.Event('blur'));
        assert(timeouts.size >= 4, 'exercise transient timers');
        if (mode === 'cache') d.body.innerHTML = cached;
        if (mode === 'fetch') {
            const replacement = d.createElement('body');
            replacement.className = 'exp-list-compact';
            replacement.innerHTML = markup;
            oldBody.replaceWith(replacement);
        }
        run();
        await settlePolls();
        assert(previousOwners.every(owner => !owner.alive), 'previous owners are retired');
        assert.deepEqual(snapshot(), first, `run ${runNumber} keeps the first live listener/timer/observer counts`);
        assert(!d.body.classList.contains('sidebar-resizing'), 'a rerun ends an old drag');
        assert.equal(processed.at(-1), d.getElementById('sidebar-tree'), 'latest initialization uses the current tree');
        assertCurrent();
        if (mode === 'cache' || mode === 'fetch') {
            oldInput.value = '/retired';
            oldInput.closest('form').dispatchEvent(new w.Event('submit'));
            assert.notEqual(w.localStorage.getItem('quam_workspace_path'), '/retired', 'old form handlers are detached');
        }
        const beforeSwap = splitCalls;
        w.localStorage.setItem('quam_split_expanded', '[20,80]');
        swap(d.getElementById('inspector-pane'));
        assert.equal(splitCalls - beforeSwap, 1, 'one Split call per inspector swap');
        assert.deepEqual(snapshot(), first, 'swapping panes replaces gutter listeners');
        assertCurrent();
        const beforeNotice = splitCalls;
        d.body.dispatchEvent(new w.Event('schedulerLocked'));
        assert.match(d.getElementById('status-bar').textContent, /locked/i, 'lock notice works after a rerun');
        assert.equal(splitCalls, beforeNotice, 'lock notice does not rebuild the split');
        const compare = d.getElementById('compare-form');
        compare.dispatchEvent(new w.CustomEvent('htmx:beforeRequest', { bubbles: true, detail: { elt: compare } }));
        assert.equal(splitCalls - beforeNotice, 1, 'one compare handler after a rerun');
        assert.equal(w.localStorage.getItem('quam_split_sizes'), '[80,20]', 'compare still collapses the inspector');
    }
    const currentInput = d.getElementById('workspace-path-input');
    currentInput.dispatchEvent(new w.Event('input'));
    fireTimeouts(250);
    const browse = requests.splice(0);
    assert.equal(browse.length, 1, 'one autocomplete request after reruns');
    respond(browse[0], { dirs: ['/data/item'] });
    await tick();
    assert.equal(d.querySelectorAll('.path-suggestion').length, 1, 'current autocomplete renders a response');
    currentInput.closest('form').dispatchEvent(new w.Event('submit'));
    assert.equal(d.querySelectorAll('.path-suggestion').length, 0, 'current form hides autocomplete');
    // Hold old responses across a rerun; even a response that ignores abort cannot update the new DOM.
    for (const interval of intervals.values()) interval.callback();
    const stale = requests.splice(0);
    assert.equal(stale.length, 2, 'both old poll responses are pending');
    run();
    assert(stale.every(request => !request.options || request.options.signal.aborted), 'retired polls are aborted');
    const freshRequests = requests.splice(0);
    const ajaxBefore = ajaxCalls.length;
    for (const request of stale) respond(request, request.url === '/workspace/tree/poll' ? { v: 9, rescanned: true } : { run: { status: 'running' } });
    await tick();
    assert.equal(ajaxCalls.length, ajaxBefore, 'stale responses cannot trigger a tree swap');
    assert(!d.body.classList.contains('scheduler-active'), 'stale responses cannot lock the fresh DOM');
    for (const request of freshRequests) respond(request, request.url === '/workspace/tree/poll' ? { v: 1 } : {});
    await tick();
    assert.deepEqual(snapshot(), first, 'settled rerun keeps stable resources');
    const slot = d.getElementById('diagnostics-banner-slot');
    slot.innerHTML = '<div>Current notice</div>';
    run();
    await settlePolls();
    assert.equal(slot.textContent, 'Current notice', 'a rerun preserves a restored notice without adding a placeholder');
    assert.equal(slot.children.length, 1, 'no placeholder beside an existing notice');
    const baseListeners = records.filter(r => r.type === 'mouseover' || r.type === 'mouseout');
    for (const key of ['density', 'sidebar', 'paths', 'split', 'scheduler']) w.__smBase[key].teardown();
    assert.equal(intervals.size, 0, 'final teardown clears every interval');
    assert.equal(timeouts.size, 0, 'final teardown clears every timeout');
    assert.equal(observers.size, 0, 'final teardown disconnects every observer');
    assert.equal(liveSplits.size, 0, 'final teardown destroys Split');
    assert.equal(records.length, baseListeners.length, 'final teardown removes every application listener, including detached elements');
    assert(splitDestroyed > 0, 'split teardown exercised');
    dom.window.close();
    console.log(`ok - ${mode}: stable listeners, timers, observers, current DOM, and one gutter`);
}

(async () => {
    for (const mode of ['loading', 'same', 'cache', 'fetch', 'drag']) await scenario(mode);
    console.log('all ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
