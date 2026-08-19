/* docs/124 C-2 / M-8 / M-9 / M-10 — the undo repaint's contract, in the REAL
 * bulk-edit.js under jsdom.
 *
 * The /undo response names RESOLVED paths; pointer-alias cells (every x180/
 * x90 amp column on the real chip) carry the alias in data-dot-path and the
 * resolved leaf in data-resolved. Matching only the alias axis left those
 * cells permanently stale + clean-marked after Ctrl+Z (C-2, critical), the
 * pair grid's alias twins phantom-dirty (M-8), the repainted value truncated
 * to 7 sig figs AND that truncation installed as the clean baseline (M-9),
 * and a type-changing revert clean-looking with the docs/56 stored-as-text
 * decorations silently wrong (M-10). Each is pinned here at the observable
 * level against the real revertPaths.
 *
 * Run: node tests/undo_repaint_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}

const ROOT = path.join(__dirname, '..');
const BULK_JS = fs.readFileSync(
    path.join(ROOT, 'quam_state_manager', 'web', 'static', 'bulk-edit.js'), 'utf8');
const PAIR_JS = fs.readFileSync(
    path.join(ROOT, 'quam_state_manager', 'web', 'static', 'pair-edit.js'), 'utf8');

let fails = 0;
function ok(cond, msg) {
    if (cond) console.log('ok - ' + msg);
    else { console.error('not ok - ' + msg); fails++; }
}

// A small world — far below the virtualization gate, so every cell is hot.
// Row q1 carries the C-2 shape: an ALIAS cell (data-dot-path = the alias,
// data-resolved = the resolved leaf) plus a plain numeric cell, a
// stored-as-text cell (docs/56 decorations), and a readonly cell.
function world() {
    const CELL = function (attrs, extra) {
        return '<td class="bulk-td"><input type="text" class="bulk-cell' +
            (extra && extra.cls ? ' ' + extra.cls : '') + '" ' + attrs +
            (extra && extra.ro ? ' readonly' : '') + '></td>';
    };
    const row =
        '<tr data-qubit="q1"><th class="bulk-rowhead" data-col-key="__id__">q1</th>' +
        CELL('value="0.4061" data-orig="0.4061"' +
             ' data-dot-path="qubits.q1.xy.operations.x180.amplitude"' +
             ' data-resolved="qubits.q1.xy.operations.x180_DragCosine.amplitude"') +
        CELL('value="4,333,200,000" data-orig="4,333,200,000"' +
             ' data-dot-path="qubits.q1.f_01"') +
        CELL('value="0.000135" data-orig="0.000135" data-str-numeric="1"' +
             ' data-dot-path="qubits.q1.T1"', { cls: 'bulk-cell-str' }) +
        CELL('value="ro" data-orig="ro" data-dot-path="qubits.q1.locked"',
             { ro: true }) +
        '</tr>';
    const DOM = '<div id="table-pane">' +
        '<div class="bulk-toolbar"><details class="bulk-colvis"><summary>P</summary>' +
        '<div class="bulk-colvis-menu" id="bulk-colvis-menu"></div></details>' +
        '<span class="bulk-search-wrap"><input type="search" id="bulk-search">' +
        '<span id="bulk-search-count"></span><span id="bulk-search-hint"></span></span></div>' +
        '<div class="bulk-table-wrap"><table id="bulk-table"><thead><tr>' +
        '<th class="bulk-corner" data-col-key="__id__"></th>' +
        '<th class="bulk-col-head" data-col-key="amp" data-section="s">amp</th>' +
        '<th class="bulk-col-head" data-col-key="f01" data-section="s">f01</th>' +
        '<th class="bulk-col-head" data-col-key="t1" data-section="s">t1</th>' +
        '<th class="bulk-col-head" data-col-key="lk" data-section="s">lk</th>' +
        '</tr></thead><tbody>' + row + '</tbody></table></div></div>' +
        // the pair grid, with the M-8 alias-twin shape: a direct macro cell
        // and its operations twin whose data-resolved names the macro leaf
        '<div><table id="bulk-pair-table"><thead><tr>' +
        '<th class="bulk-corner" data-col-key="__id__"></th></tr></thead><tbody>' +
        '<tr data-pair="q1-2"><th class="bulk-rowhead">q1-2</th>' +
        '<td class="bulk-td"><input type="text" class="bulk-cell" value="0.0515" data-orig="0.0515"' +
        ' data-dot-path="qubit_pairs.q1-2.macros.cz.coupler_flux_pulse.amplitude"></td>' +
        '<td class="bulk-td"><input type="text" class="bulk-cell bulk-cell-linked" value="0.0515" data-orig="0.0515"' +
        ' data-dot-path="qubit_pairs.q1-2.coupler.operations.cz_pulse.amplitude"' +
        ' data-resolved="qubit_pairs.q1-2.macros.cz.coupler_flux_pulse.amplitude"></td>' +
        '</tr></tbody></table></div>';
    const dom = new JSDOM('<!DOCTYPE html><html><body>' + DOM + '</body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
    const win = dom.window;
    win.htmx = { ajax: function () {} };
    new win.Function(BULK_JS).call(win);
    new win.Function(PAIR_JS).call(win);
    return win;
}

function cell(win, sel) { return win.document.querySelector(sel); }

(function main() {

// ── C-2: an entry naming the RESOLVED path repaints the alias cell ────────
{
    const win = world();
    const r = win.BulkEdit.revertPaths([{
        dot_path: 'qubits.q1.xy.operations.x180_DragCosine.amplitude',
        old_value_str: '0.3981', old_value_disp: '0.3981', old_kind: 'num',
    }]);
    const c = cell(win, '[data-dot-path="qubits.q1.xy.operations.x180.amplitude"]');
    ok(c.value === '0.3981' && c.getAttribute('data-orig') === '0.3981',
       'C-2: the alias cell repaints (value+baseline) when the entry names the resolved path');
    ok((r.covered || []).indexOf('qubits.q1.xy.operations.x180_DragCosine.amplitude') >= 0,
       'C-2: and the entry counts covered (no needless rebuild)');
}

// ── M-9: the lossless display string wins over the 7-sig-fig one ──────────
{
    const win = world();
    win.BulkEdit.revertPaths([{
        dot_path: 'qubits.q1.f_01',
        old_value_str: '4.333001e+09', old_value_disp: '4,333,001,234.5678',
        old_kind: 'num',
    }]);
    const c = cell(win, '[data-dot-path="qubits.q1.f_01"]');
    ok(c.value === '4,333,001,234.5678',
       'M-9: the cell shows the grids\' own lossless format, not %.6e');
    ok(c.getAttribute('data-orig') === '4,333,001,234.5678',
       'M-9: and the clean BASELINE is the lossless string (a re-edit cannot commit the truncation)');
}

// ── M-10: a type-changing revert refuses coverage (rebuild repaints the
//    docs/56 decorations); the VALUE still updates immediately ─────────────
{
    const win = world();
    // the T1 cell is currently stored-as-text-decorated; the revert restores
    // a plain number — repaint cannot remove the server-rendered quote spans
    const r1 = win.BulkEdit.revertPaths([{
        dot_path: 'qubits.q1.T1', old_value_str: '3.3e-05',
        old_value_disp: '0.000033', old_kind: 'num',
    }]);
    const c = cell(win, '[data-dot-path="qubits.q1.T1"]');
    ok(c.value === '0.000033', 'M-10: the number on screen updates immediately');
    ok((r1.covered || []).indexOf('qubits.q1.T1') < 0,
       'M-10: but a num-over-str-decorated cell is NOT covered (rebuild will repaint honestly)');
    // the same cell reverting back to a string IS decoration-consistent
    const r2 = win.BulkEdit.revertPaths([{
        dot_path: 'qubits.q1.T1', old_value_str: '0.000135',
        old_value_disp: '0.000135', old_kind: 'str_numeric',
    }]);
    ok((r2.covered || []).indexOf('qubits.q1.T1') >= 0,
       'M-10: a str-numeric revert onto a str-decorated cell IS covered');
    // a pointer can never be expressed by a value repaint
    const r3 = win.BulkEdit.revertPaths([{
        dot_path: 'qubits.q1.f_01', old_value_str: '#/qubits/q2/f_01',
        old_value_disp: '#/qubits/q2/f_01', old_kind: 'pointer',
    }]);
    ok((r3.covered || []).indexOf('qubits.q1.f_01') < 0,
       'M-10: a pointer revert is never covered (the cell must re-render as a link)');
}

// ── readOnly-only matches are not coverage ────────────────────────────────
{
    const win = world();
    const r = win.BulkEdit.revertPaths([{
        dot_path: 'qubits.q1.locked', old_value_str: 'x', old_value_disp: 'x',
        old_kind: 'str',
    }]);
    const c = cell(win, '[data-dot-path="qubits.q1.locked"]');
    ok(c.value === 'ro',
       'readOnly: the cell is not written');
    ok((r.covered || []).indexOf('qubits.q1.locked') < 0,
       'readOnly: and the entry is NOT covered (nothing was repainted)');
}

// ── M-8: the pair grid's alias twin gets value AND baseline, no phantom dirty
{
    const win = world();
    win.BulkPairEdit.revertPaths([{
        dot_path: 'qubit_pairs.q1-2.macros.cz.coupler_flux_pulse.amplitude',
        old_value_str: '0.05', old_value_disp: '0.05', old_kind: 'num',
    }]);
    const direct = cell(win,
        '#bulk-pair-table [data-dot-path="qubit_pairs.q1-2.macros.cz.coupler_flux_pulse.amplitude"]');
    const twin = cell(win,
        '#bulk-pair-table [data-dot-path="qubit_pairs.q1-2.coupler.operations.cz_pulse.amplitude"]');
    ok(direct.value === '0.05' && direct.getAttribute('data-orig') === '0.05',
       'M-8: the direct macro cell heals fully');
    ok(twin.value === '0.05' && twin.getAttribute('data-orig') === '0.05',
       'M-8: the alias twin gets value AND data-orig (no stale baseline)');
    ok(!twin.classList.contains('dirty') && !direct.classList.contains('dirty'),
       'M-8: neither cell is phantom-dirty after the revert');
}

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('all checks passed');
process.exit(0);
})();
