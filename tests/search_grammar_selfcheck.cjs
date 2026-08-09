/* The shared query grammar, pinned against the REAL shipped JS.
 *
 * space = AND, standalone | = OR (tight binding), every other pipe literal.
 * Five surfaces are exercised through their real code, not reimplementations:
 *
 *   1. SearchQuery itself (grouping table incl. every degenerate case)
 *   2. the Json Tree View DATA path  (app.js _searchTreeData via jsonTreeSearch)
 *   3. the tree DOM path             (app.js _searchTreeDom — the unified
 *      compare tree's path; pinned EQUAL to the data path on one fixture,
 *      because the first audit of this change found the DOM path unmeasured)
 *   4. the Live State Edit qubit grid (bulk-edit.js applySearch)
 *   5. the Datasets table            (dataset-virtual.js parseQuery/applyFilters)
 *      + the pair grid's hidden-column hint (pair-edit.js hiddenMatching)
 *
 * Additivity is asserted per surface: a query with no standalone pipe must
 * produce exactly the result the surface produced before the grammar landed
 * (AND surfaces), or a superset that contains every old match (the tree,
 * whose whole-substring matcher was the reported defect).
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { JSDOM = require('jsdom').JSDOM; } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
// fs.writeSync: unbuffered — console.log to a pipe is flushed only at exit,
// and a hang would otherwise look like silence.
function say(m) { try { fs.writeSync(1, m + '\n'); } catch (e) { console.log(m); } }
function ok(c, m) { if (!c) { say('FAIL: ' + m); fails++; } else { say('ok - ' + m); } }
const wait = ms => new Promise(r => setTimeout(r, ms));

/* ── 1. SearchQuery unit table ─────────────────────────────────────────── */
{
    global.window = {};
    eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
    const SQ = global.window.SearchQuery;
    const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    ok(eq(SQ.groups('a b'), [['a'], ['b']]), 'unit: plain words are singleton groups');
    ok(eq(SQ.groups('a | b'), [['a', 'b']]), 'unit: pipe ORs its neighbours');
    ok(eq(SQ.groups('x a | b'), [['x'], ['a', 'b']]), 'unit: OR binds tighter than AND');
    ok(eq(SQ.groups('a | b | c'), [['a', 'b', 'c']]), 'unit: chains extend one group');
    ok(eq(SQ.groups('|e>'), [['|e>']]), 'unit: embedded pipe (ket notation) is literal');
    ok(eq(SQ.groups('|'), [['|']]), 'unit: lone pipe is literal');
    ok(eq(SQ.groups('a |'), [['a'], ['|']]), 'unit: trailing pipe is literal');
    ok(eq(SQ.groups('a | | b'), [['a'], ['|'], ['|'], ['b']]), 'unit: doubled pipe stays literal');
    ok(SQ.matchesHay('x180 length 48', SQ.groups('x180 amplitude | length')),
       'unit: AND-of-OR evaluates');
    ok(!SQ.matchesHay('x90 amplitude', SQ.groups('x180 amplitude | length')),
       'unit: the AND term still filters');
}

/* ── 2 + 3. the tree, both paths, one fixture ──────────────────────────── */
async function treeChecks() {
    const dom = new JSDOM(
        '<!doctype html><html><body><div id="t1"></div><div id="t2"></div></body></html>',
        { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    global.window = w; global.document = w.document;
    w.matchMedia = w.matchMedia || (() => ({ matches: false, addEventListener() {}, addListener() {} }));
    w.htmx = { ajax: () => Promise.resolve(), on() {}, trigger() {}, process() {} };
    // app.js starts pollers on DOMContentLoaded; give them an inert fetch so
    // their rejections don't spray the log (they don't touch the tree search).
    w.fetch = () => Promise.resolve({ ok: true, status: 200,
        json: () => Promise.resolve({}), text: () => Promise.resolve('') });
    w.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
    w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

    const state = {
        qubits: {
            qA1: { f_01: 6.25e9, T1: 2.4e-5,
                   xy: { operations: { x180: { amplitude: 0.11, length: 48 } } } },
            qA2: { f_01: 5.8e9, T1: 3.1e-5,
                   xy: { operations: { x180: { amplitude: 0.09, length: 52 } } } },
        },
        // ket notation, as node.json descriptions really carry it
        description: 'measures the |e> state population',
    };

    function highlighted(cid) {
        return Array.from(w.document.getElementById(cid).querySelectorAll('.tree-highlight'))
            .map(n => n.getAttribute('data-path'));
    }
    async function search(cid, q) {
        w.jsonTreeSearch(cid, q);
        await wait(300);                       // 200 ms debounce in app.js
        return highlighted(cid);
    }

    // DATA path
    w.renderJsonTree('t1', state);
    let hits = await search('t1', 'qa1 f_01');
    ok(hits.length === 1 && hits[0] === 'qubits.qA1.f_01',
       'tree/data: two words AND to the one node carrying both (was 0 before)');
    hits = await search('t1', 'qa1 | qa2');
    ok(hits.some(p => p.startsWith('qubits.qA1')) && hits.some(p => p.startsWith('qubits.qA2')),
       'tree/data: | finds both qubits');
    hits = await search('t1', 'f_01');
    ok(hits.length === 2, 'tree/data: single word unchanged (both f_01 nodes)');
    hits = await search('t1', '|e>');
    ok(hits.length === 1 && hits[0] === 'description',
       'tree/data: |e> stays a literal (ket notation searchable)');
    hits = await search('t1', 'x180 amplitude | length');
    ok(hits.length === 4 &&
       hits.every(p => /x180\.(amplitude|length)$/.test(p)),
       'tree/data: tight binding — x180 AND (amplitude|length)');

    // additivity on this fixture: whole-substring matches survive AND
    const c1 = w.document.getElementById('t1');
    const flat = c1._flatIndex.flat;
    const SQ = w.SearchQuery;
    let lost = 0, tested = 0;
    for (const e of flat) {
        // every "<key> <value>"-shaped phrase that matched the OLD matcher...
        const phrase = e.hayLower;
        if (!phrase.trim() || phrase.length > 60) continue;
        tested++;
        const oldHit = flat.filter(x => x.hayLower.indexOf(phrase) >= 0 || x.pathLower.indexOf(phrase) >= 0);
        const grps = SQ.groups(phrase);
        const newHit = flat.filter(x => SQ.matchesHay(x.hayLower + ' ' + x.pathLower, grps));
        for (const o of oldHit) if (!newHit.includes(o)) lost++;
    }
    ok(tested > 10 && lost === 0,
       'tree/data: additivity — 0 old matches lost over ' + tested + ' real phrases');

    // DOM path: same fixture rendered, then forced down the DOM branch
    w.renderJsonTree('t2', state);
    const c2 = w.document.getElementById('t2');
    // materialise everything first (the DOM path's own loop does it too, but
    // the flat-index cache must not exist for the dispatch to take this arm)
    c2._treeData = null;
    for (const q of ['qa1 f_01', 'qa1 | qa2', 'f_01', '|e>']) {
        const a = await search('t1', q);
        const b = await search('t2', q);
        ok(JSON.stringify(a.slice().sort()) === JSON.stringify(b.slice().sort()),
           'tree/dom: identical answer to the data path for "' + q + '"');
    }
}

/* ── 4. Live State Edit qubit grid ─────────────────────────────────────── */
async function bulkChecks() {
    const COLS = [
        { key: 'f_01', label: 'f01', section: 'Q', unit: 'Hz', default_on: true },
        { key: 'T1', label: 'T1', section: 'Q', unit: 's', default_on: true },
        { key: 'T2', label: 'T2', section: 'Q', unit: 's', default_on: true },
    ];
    function cellTd(colKey, qid, val) {
        return '<td class="bulk-td" data-col-key="' + colKey + '">' +
            '<input type="text" class="bulk-cell" value="' + val + '" data-orig="' + val + '"' +
            ' data-dot-path="qubits.' + qid + '.' + colKey + '" data-resolved="qubits.' + qid + '.' + colKey + '"></td>';
    }
    function rowHtml(qid, f01) {
        return '<tr data-qubit="' + qid + '"><th class="bulk-rowhead" data-col-key="__id__">' + qid + '</th>' +
            cellTd('f_01', qid, f01) + cellTd('T1', qid, '2e-5') + cellTd('T2', qid, '3e-5') +
            '<td class="bulk-apply-col"><button class="btn-xs bulk-row-apply" disabled>Apply</button>' +
            '<span class="bulk-row-error" hidden></span></td></tr>';
    }
    const HTML = '<!doctype html><html><body><div id="bulk-panel">' +
        '<div id="bulk-colvis-menu"></div><div id="bulk-qubitvis-menu"></div>' +
        '<button id="bulk-qubit-pill" hidden></button>' +
        '<input id="bulk-search"><span id="bulk-search-count"></span>' +
        '<button id="bulk-dyncol-hint" hidden></button>' +
        '<span id="bulk-dirty-count"></span>' +
        '<button id="bulk-apply-all"></button><button id="bulk-reset"></button>' +
        '<div class="bulk-table-wrap"><table id="bulk-table"><thead>' +
        '<tr class="bulk-group-row"><th class="bulk-corner" data-col-key="__id__">qubit<span class="bulk-sort-caret"></span></th></tr>' +
        '<tr class="bulk-head-row">' +
        COLS.map(c => '<th class="bulk-col-head" data-col-key="' + c.key + '"><span class="bulk-col-label">' +
            c.label + '</span><span class="bulk-sort-caret"></span><span class="bulk-col-stats" data-col-stats="' + c.key + '"></span></th>').join('') +
        '</tr></thead><tbody>' +
        rowHtml('q1', '5e9') + rowHtml('q2', '6e9') + rowHtml('q3', '7e9') +
        '</tbody></table></div></div></body></html>';
    const dom = new JSDOM(HTML, { runScripts: 'outside-only', url: 'http://localhost/' });
    const w = dom.window;
    global.window = w; global.document = w.document;
    w.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
    w.eval(fs.readFileSync(path.join(STATIC, 'bulk-edit.js'), 'utf8'));
    w.BulkEdit.mount(COLS, { bands: {} }, [], {
        chip: 'testchip',
        qubits: [{ id: 'q1', grid: null }, { id: 'q2', grid: null }, { id: 'q3', grid: null }],
    });
    async function search(q) {
        const inp = w.document.getElementById('bulk-search');
        inp.value = q;
        inp.dispatchEvent(new w.Event('input', { bubbles: true }));
        await wait(250);                       // 120 ms debounce
    }
    const rowHidden = q => w.document.querySelector('tr[data-qubit="' + q + '"]')
        .classList.contains('bulk-row-hidden');
    const colHidden = k => w.document.querySelector('th[data-col-key="' + k + '"]')
        .classList.contains('bulk-search-hidden');

    await search('q1 | q2');
    ok(!rowHidden('q1') && !rowHidden('q2') && rowHidden('q3'),
       'grid: "q1 | q2" shows both rows, hides q3');
    ok(!colHidden('f_01') && !colHidden('T1'),
       'grid: an id-only OR leaves every column visible');

    await search('t1 | t2');
    ok(!colHidden('T1') && !colHidden('T2') && colHidden('f_01'),
       'grid: "t1 | t2" shows both columns, hides f01');
    ok(!rowHidden('q1') && !rowHidden('q3'),
       'grid: a column-only OR leaves every row visible');

    await search('q1 5e9');
    ok(!rowHidden('q1') && rowHidden('q2') && rowHidden('q3'),
       'grid: plain two-word AND unchanged');

    await search('');
    ok(!rowHidden('q1') && !rowHidden('q2') && !colHidden('f_01'),
       'grid: empty query shows everything');
}

/* ── 5. pair grid hidden-column hint ───────────────────────────────────── */
async function pairChecks() {
    const dom = new JSDOM('<!doctype html><html><body>' +
        '<table id="bulk-pair-table"><tbody></tbody></table>' +
        '<div id="bulk-pair-colvis-menu"></div>' +
        '</body></html>', { runScripts: 'outside-only', url: 'http://localhost/' });
    const w = dom.window;
    global.window = w; global.document = w.document;
    w.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
    w.eval(fs.readFileSync(path.join(STATIC, 'pair-edit.js'), 'utf8'));
    w.localStorage.setItem('quam_bulk_hidden_cols_pair_v2', JSON.stringify(['cz_amp', 'cz_len']));
    w.BulkPairEdit.mount([
        { key: 'cz_amp', label: 'cz · amplitude', section: 'Gate', default_on: true },
        { key: 'cz_len', label: 'cz · length', section: 'Gate', default_on: true },
        { key: 'det', label: 'detuning', section: 'Pair', default_on: true },
    ]);
    const hm = toks => w.BulkPairEdit.hiddenMatching(toks).slice().sort();
    ok(JSON.stringify(hm(['amplitude', '|', 'length'])) === JSON.stringify(['cz_amp', 'cz_len']),
       'pair hint: "amplitude | length" surfaces both hidden columns');
    ok(JSON.stringify(hm(['cz', 'amplitude'])) === JSON.stringify(['cz_amp']),
       'pair hint: plain AND unchanged');
}

/* ── 6. Datasets table ─────────────────────────────────────────────────── */
async function datasetChecks() {
    const rows = [
        { id: 1, f: 'f1', exp: 'power_rabi', q: ['q0'], p: [], tags: ['good'], date: '2026-08-09', status: 'finished' },
        { id: 2, f: 'f1', exp: 'ramsey', q: ['q1'], p: [], tags: ['wip'], date: '2026-08-09', status: 'finished' },
        { id: 3, f: 'f1', exp: 'iq_blobs', q: ['q2'], p: [], tags: [], date: '2026-08-09', status: 'finished' },
    ];
    const dom = new JSDOM('<!doctype html><html><body>' +
        '<script type="application/json" id="ds-rows-data" data-now="1000">' +
        JSON.stringify(rows) + '</script>' +
        '<input id="dataset-search"><span id="dataset-filter-count"></span>' +
        '<div id="datasets-scroll" style="height:400px"><table><tbody id="datasets-tbody"></tbody></table></div>' +
        '</body></html>', { url: 'http://localhost/datasets', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    global.window = w; global.document = w.document;
    global.localStorage = w.localStorage;
    w.requestAnimationFrame = w.requestAnimationFrame || (cb => setTimeout(cb, 0));
    global.requestAnimationFrame = w.requestAnimationFrame;
    w.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
    w.eval(fs.readFileSync(path.join(STATIC, 'dataset-virtual.js'), 'utf8'));
    w.DatasetVirtual.init();

    async function count(q) {
        const inp = w.document.getElementById('dataset-search');
        inp.value = q;
        inp.dispatchEvent(new w.Event('input', { bubbles: true }));
        await wait(30);
        const m = (w.document.getElementById('dataset-filter-count').textContent || '')
            .match(/Showing (\d+) of (\d+)/);
        return m ? Number(m[1]) : rows.length;   // no banner = no filter active
    }
    ok(await count('q0 | q1') === 2, 'datasets: "q0 | q1" = runs on either qubit');
    ok(await count('rabi | ramsey') === 2, 'datasets: free-text OR over experiment names');
    ok(await count('tag:good | tag:wip') === 2, 'datasets: scoped OR');
    ok(await count('-tag:wip') === 2, 'datasets: negation unchanged (singleton group)');
    ok(await count('rabi q0') === 1, 'datasets: plain AND unchanged');
    ok(await count('rabi | ramsey q1') === 1,
       'datasets: tight binding — (rabi|ramsey) AND q1');
}

(async function main() {
    say('-- tree');
    await treeChecks();
    say('-- bulk');
    await bulkChecks();
    say('-- pair');
    await pairChecks();
    say('-- datasets');
    await datasetChecks();
    if (fails) { say(fails + ' check(s) failed'); process.exit(1); }
    say('all checks passed');
    process.exit(0);
})().catch(e => { say('ERROR: ' + (e && e.stack || e)); process.exit(1); });
