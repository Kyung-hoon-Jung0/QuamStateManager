// Search overview rail for the whole-file JSON editor (tree-json-edit.js).
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { JSDOM } = require('jsdom');

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'tree-json-edit.js');
let src = fs.readFileSync(SRC, 'utf8').replace(/\r\n/g, '\n');
const MUT = {
    count: ['+ " of " + matches.length', '+ " / " + matches.length'],
    nested: ['var cp = path ? path + "." + k : String(k);', 'var cp = "wrong";'],
    container: ['mark(i, i + 1, path);', 'mark(i, i + 1, "");'],
    array: ['var ip = path ? path + "." + idx : String(idx);', 'var ip = "wrong";'],
    braces: [`if (c === '"') { str(); mark(start, i, path); }`, `if (c === '"') { str(); }`],
    escapes: ['if (text[i] === "\\\\") i++;', 'if (false) i++;'],
    invalid: ['JSON.parse(text);\n            val("");', 'val("");'],
    matches: ['from = at + (q.length || 1);', 'break;'],
    positions: ['line: line});', 'line: 1});'],
    marks: ['frag.appendChild(b);', '/* missing mark */'],
    current: ['if (marks.children[cur])', 'if (false)'],
    hover: ['b.title = m.path;', 'b.title = "";'],
    tooltip: ['tooltip.textContent = mark.title;', 'tooltip.textContent = "";'],
    jump: ['gotoOffset(+e.currentTarget.getAttribute("data-from"));', '/* missing jump */'],
    viewport: ['sc.scrollTop / sc.scrollHeight * 100', '0'],
    grouping: ['g + " (" + group.length + ")"', '"wrong"'],
    list_jump: ['function () { gotoOffset(m.from); refreshRail(); }', 'function () {}'],
    cap: ['var RAIL_LIST_CAP = 500;', 'var RAIL_LIST_CAP = 2;'],
    more: ['(matches.length - shown) + " more not shown"', '"wrong"'],
    debounce: ['var RAIL_DEBOUNCE_LARGE_MS = 400;', 'var RAIL_DEBOUNCE_LARGE_MS = 0;'],
    batch: ['next + 300', 'next + 1000'],
    cache: ['S.railText !== text || S.railKey !== key', 'S.railText !== text || S.railKey !== key || S.railListOn'],
    word: ['if (spec.word &&', 'if (false &&'],
};
const mutation = process.env.RAIL_MUTATION;
if (mutation) {
    const pair = MUT[mutation];
    assert(pair && src.includes(pair[0]), 'mutation target exists: ' + mutation);
    src = src.replaceAll(pair[0], pair[1]);
}

function world() {
    const dom = new JSDOM('<!doctype html><html data-theme="light"><body>'
        + '<div id="explorer-tree-state"></div></body></html>', { runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    const eds = [];
    const delays = [];
    const realTimeout = w.setTimeout.bind(w);
    w.setTimeout = (fn, delay) => { delays.push(delay); return realTimeout(fn, 0); };
    w.fetch = () => Promise.resolve({ json: () => Promise.resolve({ ok: false }) });
    w.SmCodeMirror = {
        EditorView: { updateListener: { of: fn => ({ run: fn }) } },
        create(host, o) {
            let doc = o.doc || '';
            const lines = () => doc.split('\n').length;
            const lineAt = (n) => {
                let o = 0, i = 0, ls = doc.split('\n');
                for (i = 0; i < n - 1 && i < ls.length; i++) o += ls[i].length + 1;
                return { from: o, number: n };
            };
            const ed = {
                host, dark: o.dark, getDoc: () => doc,
                set(t) { doc = t; o.onChange && o.onChange(t);  },
                focus() {},
                destroy() {},
                view: {
                    state: {
                        doc: { lines: lines(), line: (n) => lineAt(n),
                            toString: () => doc },
                        selection: { main: { head: 0 } },
                    },
                    dispatch(t) {
                        if (t && t.selection) ed.view.state.selection.main.head = t.selection.anchor;
                        ed.jumped = t && t.selection ? t.selection.anchor : null;
                    },
                },
            };
            const search = w.document.createElement('div');
            search.className = 'cm-search';
            search.innerHTML = '<input class="cm-textfield" value="thread"><input class="cm-textfield">'
                + '<input type="checkbox" name="case"><input type="checkbox" name="re">';
            const scroller = w.document.createElement('div');
            scroller.className = 'cm-scroller';
            Object.defineProperty(scroller, 'scrollHeight', { get: () => 400, configurable: true });
            Object.defineProperty(scroller, 'clientHeight', { get: () => 100, configurable: true });
            scroller.scrollTop = 50;
            host.appendChild(search);
            host.appendChild(scroller);
            if (o.extensions) o.extensions.forEach(() => {});
            eds.push(ed);
            return ed;
        },
    };
    w.__chipToken = 't';
    w.eval(src);
    return { w, eds, delays };
}

const SAMPLE = JSON.stringify({
    qubits: { q5: { xy: { thread: 1, note: 'brace { } [ ] in value' } } },
    items: [{ thread: true }, 'escaped "quote" with \\ slash', 'prethreadpost'],
}, null, 2);

(async () => {
    let n = 0;
    const ok = (name, condition) => { assert.ok(condition, name); n++; };
    const { w, delays } = world();
    const api = w.TreeJsonEdit, pathAt = api._pathAtOffset, find = api._findMatches;
    const spec = { query: 'thread', case: false, re: false };
    ok('nested', pathAt(SAMPLE, SAMPLE.indexOf('"thread"')) === 'qubits.q5.xy.thread');
    ok('container', pathAt(SAMPLE, SAMPLE.indexOf('{', SAMPLE.indexOf('qubits'))) === 'qubits');
    ok('array', pathAt(SAMPLE, SAMPLE.indexOf('true')) === 'items.0.thread');
    ok('braces', pathAt(SAMPLE, SAMPLE.indexOf('brace {')) === 'qubits.q5.xy.note');
    ok('escapes', pathAt(SAMPLE, SAMPLE.indexOf('slash')) === 'items.1');
    ok('invalid', pathAt('{"thread": 1, bad}', 3) === 'line 1');
    const hits = find(SAMPLE, spec);
    ok('matches', hits.length === 3);
    ok('positions', hits[2].line === SAMPLE.slice(0, hits[2].from).split('\n').length);
    ok('word', find(SAMPLE, { ...spec, word: true }).length === 2);
    async function open(text) {
        api.close(true);
        w.fetch = () => Promise.resolve({ json: () => Promise.resolve({
            ok: true, tab: 'state', path: '', text, mutation_seq: 1,
        }) });
        await api.openWhole();
        api._refreshRail();
        return api._state();
    }
    const st = await open(SAMPLE);
    const q = selector => w.document.querySelector(selector);
    const qa = selector => [...w.document.querySelectorAll(selector)];
    ok('count', q('.ui-jedit-rail-count').textContent === '1 of 3');
    let marks = qa('.ui-jedit-rail-mark');
    ok('marks', marks.length === 3 && Math.abs(parseFloat(marks[2].style.top) - (hits[2].line - 0.5) / SAMPLE.split('\n').length * 100) < 0.01);
    ok('hover', marks[0].title === 'qubits.q5.xy.thread');
    marks[0].dispatchEvent(new w.Event('pointerover', { bubbles: true }));
    ok('tooltip', q('.ui-jedit-rail-tooltip-on').textContent === 'qubits.q5.xy.thread');
    marks[2].click();
    ok('jump', st.ed.jumped === hits[2].from);
    ok('current', q('.ui-jedit-rail-count').textContent === '3 of 3' && marks[2].classList.contains('ui-jedit-rail-mark-cur') && !marks[0].classList.contains('ui-jedit-rail-mark-cur'));
    ok('viewport', q('.ui-jedit-rail-viewport').style.top === '12.5%' && q('.ui-jedit-rail-viewport').style.height === '25%');
    q('.ui-jedit-rail-listbtn').click();
    ok('grouping', q('.ui-jedit-rail-group').textContent === 'qubits.q5 (1)');
    q('.ui-jedit-rail-list-row').click();
    ok('list_jump', st.ed.jumped === hits[0].from);
    st.ed.view.state.selection.main.head = SAMPLE.length;
    api._refreshRail(); // Must also tolerate a caret past the final hit.
    const cached = st.railMatches;
    q('.cm-scroller').dispatchEvent(new w.Event('scroll'));
    api._refreshRail();
    ok('cache', cached === st.railMatches);
    const large = JSON.stringify({ qubits: { q5: Array.from({length: 506}, () => ({ thread: 1 })) }, padding: 'x'.repeat(2 * 1048576) }, null, 2);
    const started = Date.now();
    const big = await open(large);
    const firstBatch = big.railMarks.children.length;
    await new Promise(resolve => w.requestAnimationFrame(resolve));
    ok('batch', firstBatch === 300 && big.railMarks.children.length === 506 && Date.now() - started < 4000);
    q('.ui-jedit-rail-listbtn').click();
    ok('cap', qa('.ui-jedit-rail-list-row').length === 500);
    ok('more', q('.ui-jedit-rail-list-more').textContent === '6 more not shown');
    ok('debounce', delays.includes(400));
    api.close(true);
    w.close();
    console.log('ALL OK (' + n + ' pins)');
})().catch(e => { console.error(e.stack || e); process.exit(1); });

