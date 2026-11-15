'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const source = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
const fixtures = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
function block(start, end) {
    const a = source.indexOf(start), b = source.indexOf(end, a + start.length);
    assert(a >= 0 && b > a, 'production block exists');
    return source.slice(a, b);
}
const dom = new JSDOM('<div id="inspector-pane"></div>', {url: 'http://localhost', runScripts: 'outside-only', pretendToBeVisual: true});
const w = dom.window, d = w.document, pane = d.getElementById('inspector-pane');
// jsdom has no layout: model the browser refusing focus inside a closed fold.
const focus = w.HTMLElement.prototype.focus;
w.HTMLElement.prototype.focus = function (...args) {
    if (this.closest('details:not([open])')) return;
    focus.apply(this, args);
};
w.eval(block('window.InlineCommit = (function () {', '/* v1.5.1 (U P2-28)'));
w.eval('var _inspectorSticky = {type:null, sections:{}, scrollTop:0}; var _dsSticky = {}; var _dsScroll = {};');
w.eval(block('// Capture inspector state just before swap', '/* v1.5.1 (r6 P2-9)'));
w.eval(block('// Restore qubit/pair inspector state after HTMX swap', '// --- Sidebar tree highlight:'));
w.eval(block('var _debounceTimers = {};', '\n}') + '\n}');
w.eval(block('function _detailPanelArticle()', '/* ------------------------------------------------------------------ */\n/* JSON Tree Viewer'));
const zone = () => pane.querySelector('[data-detail-zone]');
const field = () => zone().querySelector('input[name="value"]');
function swap(html) {
    pane.dispatchEvent(new w.CustomEvent('htmx:beforeSwap', {bubbles:true, detail:{target:pane}}));
    pane.innerHTML = html;
    pane.scrollTop = 0; // a re-rendered pane starts at the top, as in a browser
    pane.dispatchEvent(new w.CustomEvent('htmx:afterSwap', {bubbles:true, detail:{target:pane}}));
}
async function until(condition) {
    const deadline = Date.now() + 3000;
    while (!condition()) {
        assert(Date.now() < deadline, 'condition reached before deadline');
        await new Promise(resolve => setTimeout(resolve, 10));
    }
}

const qubitPane = () => '<div class="inspector-header inspector-header-qubit"><span class="inspector-badge inspector-badge-qubit">Qubit</span></div>'
    + '<div class="detail-search"><input class="detail-search-input"><span class="detail-search-count"></span><button class="detail-search-clear" hidden></button></div>'
    + '<article class="qubit-detail">' + [['Frequencies', 'f01', 'drive'], ['Other', 'gain', 'bias']].map(([title, a, b]) =>
        '<details open class="detail-section"><summary class="section-header">' + title + '</summary><table class="prop-table"><tbody>'
        + [a, b].map(k => '<tr><td>' + k + '</td><td><form class="inline-edit"><input type="hidden" name="dot_path" value="qubits.' + k + '">'
            + '<input type="text" name="value" value="5"></form></td></tr>').join('')
        + '</tbody></table></details>').join('') + '</article>';
(async () => {
    try {
        pane.innerHTML = fixtures.initial;
        assert(!zone().open);
        zone().open = true;
        field().focus();
        field().setSelectionRange(2, 2);
        pane.scrollTop = 120;
        swap(fixtures.changed);
        await until(() => pane.scrollTop === 120);
        assert(zone().open, 'opened zone survives an edit with changed summary');
        assert.equal(d.activeElement, field(), 'same edit field focused in swap tick');
        assert.equal(field().selectionStart, 2, 'caret restored');
        // The swap tick must not spend the memory: a later settle pass still brings the focus back.
        d.activeElement.blur();
        assert.notEqual(d.activeElement, field(), 'focus was taken away');
        w.InlineCommit.restore(false);
        assert.equal(d.activeElement, field(), 'a later pass still returns the focus');
        w.InlineCommit.restore(true);
        // The existing inspector policy shares fold choices across pairs of the same type.
        const other = fixtures.initial.replaceAll('q1-q2', 'q3-q4');
        d.activeElement.blur();
        swap(other);
        assert(zone().open, 'cross-pair navigation keeps existing shared-fold policy');
        zone().open = false;
        swap(fixtures.warning);
        assert(zone().open, 'fresh warning overrides a saved closed fold');
        zone().open = false;
        swap(fixtures.changed);
        assert(!zone().open, 'closed non-warning fold survives refresh');
        zone().open = true;
        field().focus();
        swap(fixtures.changed);
        assert(zone().open && d.activeElement === field(), 'refresh preserves open zone and field focus');
        const search = pane.querySelector('.detail-search-input');
        const rows = [...pane.querySelectorAll('.ui-zone--fold .prop-table tbody > tr')];
        for (const [query, expected] of [['amplitude', 3], ['length', 3], ['ns', 3], ['0.37', 3]]) {
            pane.querySelector('.detail-search-count').textContent = '';
            search.value = query;
            w.filterDetailPanel(search);
            await until(() => pane.querySelector('.detail-search-count').textContent === expected + ' of 6 shown');
            const shown = rows.filter(r => r.style.display !== 'none');
            assert.equal(shown.length, expected, 'only matching cells: ' + query);
            assert(shown.every(r => r.textContent.includes(query) || [...r.querySelectorAll('input')].some(i => i.value.includes(query))));
        }

        // A press on a button of the gate zone leaves no focus memory behind that would block the next edit.
        w.InlineCommit.restore(true);
        d.activeElement.blur();
        pane.querySelector('.gate-status-delete').focus();
        assert.equal(d.activeElement.className.includes('gate-status-delete'), true, 'the button holds the focus');
        assert(pane.querySelector('.gate-status-delete'), 'the fixture has a gate zone button');
        swap(fixtures.changed);
        field().focus();
        swap(fixtures.changed);
        assert.equal(d.activeElement, field(), 'an edit after a button press is still remembered');
        w.InlineCommit.restore(true);

        // The pair-only focus memory must not touch a qubit inspector (focus stays where the swap leaves it).
        field().focus();
        w.InlineCommit.restore(true);
        pane.innerHTML = qubitPane();
        swap(qubitPane());
        let input = pane.querySelector('input[name="value"]');
        input.focus();
        assert.equal(d.activeElement, input);
        swap(qubitPane());
        assert.notEqual(d.activeElement, pane.querySelector('input[name="value"]'), 'qubit inspector focus is not restored');
        // Folds of non-gate sections keep the reader's choice, keyed by their title.
        pane.querySelector('details.detail-section').open = false;
        swap(qubitPane());
        const folds = [...pane.querySelectorAll('details.detail-section')].map(x => x.open);
        assert.deepEqual(folds, [false, true], 'non-gate fold choice survives a refresh');
        // A section title still matches rows whose own cells lack the word.
        const qsearch = pane.querySelector('.detail-search-input');
        qsearch.value = 'frequencies';
        w.filterDetailPanel(qsearch);
        await until(() => pane.querySelector('.detail-search-count').textContent === '2 of 4 shown');
        // A Tab commit's memory is not overwritten by the swap handler.
        pane.innerHTML = fixtures.initial;
        swap(fixtures.initial);
        zone().open = true;
        const both = zone().querySelectorAll('input[name="value"]');
        both[0].focus();
        w.InlineCommit.remember(both[0], both[1]);
        swap(fixtures.initial);
        assert.equal(d.activeElement, zone().querySelectorAll('input[name="value"]')[1], 'pending Tab memory wins');
        w.InlineCommit.restore(true);
        assert(!pane.querySelector('.ui-panel-sum[aria-hidden]'), 'summary remains accessible');
        console.log('ALL OK');
    } finally { w.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
