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
const GRID_VIRT_JS = fs.readFileSync(
    path.join(ROOT, 'quam_state_manager', 'web', 'static', 'grid-virt.js'), 'utf8');
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
    new win.Function(GRID_VIRT_JS).call(win);
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
    // Review of 4ffee11: a FOUND-but-unwritable cell is uncovered (the
    // caller's rebuild clears a stale badge); a path with NO cell is merely
    // missing -- that one used to cost the 2.4 s whole-grid re-GET on every
    // inspector / tree Ctrl+Z.
    ok((r.uncovered || []).indexOf('qubits.q1.locked') >= 0,
       'readOnly: a found-but-unwritable cell IS uncovered (docs/124 M-10 kept)');
    const rm = win.BulkEdit.revertPaths([{
        dot_path: 'qubits.q1.xy.operations.saturation.length', old_value_str: '1000', old_value_disp: '1,000',
        old_kind: 'int',
    }]);
    ok(rm.missing === 1 && (rm.uncovered || []).length === 0,
       'a path with no cell on the grid is missing, NOT uncovered (no rebuild for an off-grid undo)');
    // the qubit grid's list column: a preview span (+ ✎), no input at all.
    // docs/159 (customer: Ctrl+Z on exponential_filter "did nothing"): the
    // span carries its ALIAS in data-path and the resolved leaf in
    // data-resolved -- /undo names the resolved one -- and a LIST revert is
    // repainted with the very preview the page renders (old_value_disp from
    // _list_preview), so it is COVERED, no rebuild needed.
    const tr = win.document.querySelector('#bulk-table tbody tr');
    const td = win.document.createElement('td'); td.className = 'bulk-td';
    td.innerHTML = '<span class="bulk-cell-list bulk-cell-modified" data-path="qubits.q1.z.opx_output.exponential_filter"'
                 + ' data-resolved="ports.analog_outputs.con1.4.1.exponential_filter">[[0.25,99.0],[0.1,5.0]]</span>'
                 + '<button type="button" class="bulk-list-edit">✎</button>';
    tr.appendChild(td);
    const rl = win.BulkEdit.revertPaths([{
        dot_path: 'ports.analog_outputs.con1.4.1.exponential_filter',
        old_value_str: '[[0.5, 123.0]]', old_value_disp: '[[0.5,123.0]]', old_value_badge: '▦ 1×2', old_kind: 'list',
    }]);
    const span = win.document.querySelector('.bulk-cell-list');
    ok(rl.missing === 0 && (rl.covered || []).indexOf('ports.analog_outputs.con1.4.1.exponential_filter') >= 0,
       'listedit: an undo naming the RESOLVED leaf finds the alias span and covers it');
    ok(span.textContent === '[[0.5,123.0]]',
       'listedit: the span shows the reverted list exactly as the page renders it (got ' + span.textContent + ')');
    // code-review round 2, F4: the value is COMMITTED, so the cell is clean --
    // the red "unapplied edit" box must go with it. Left on, it sat over a
    // reverted value forever: covered ⇒ no rebuild, and the tray was not empty
    // either, so nothing else would have cleared it.
    ok(!span.classList.contains('bulk-cell-modified'),
       'listedit: the reverted span is no longer marked as an unapplied edit');
    // …but a revert that changes the cell's SHAPE (back to null) is not a
    // string write: found, uncovered, the rebuild repaints it honestly
    const rn = win.BulkEdit.revertPaths([{
        dot_path: 'qubits.q1.z.opx_output.exponential_filter', old_value_str: '', old_value_disp: '', old_kind: 'null',
    }]);
    ok(rn.missing === 0 && (rn.uncovered || []).indexOf('qubits.q1.z.opx_output.exponential_filter') >= 0
       && span.textContent === '[[0.5,123.0]]',
       'listedit: a revert to null is found but uncovered (the shape changes; the rebuild owns it)');
    // the pair grid's list column: a readonly ▦ badge input (data-list) --
    // repainted from old_value_badge (the badge _list_pair_cell renders)
    const ptr = win.document.querySelector('#bulk-pair-table tbody tr');
    const ptd = win.document.createElement('td'); ptd.className = 'bulk-td';
    ptd.innerHTML = '<input type="text" class="bulk-cell bulk-cell-ro" readonly value="▦ 2×2" data-orig="▦ 2×2" data-list="1"'
                  + ' data-dot-path="qubit_pairs.p1.macros.cz.filters" data-resolved="qubit_pairs.p1.macros.cz.filters">'
                  + '<input type="text" class="bulk-cell bulk-cell-ro bulk-cell-runtime" readonly value="rt" data-orig="rt"'
                  + ' data-dot-path="qubit_pairs.p1.macros.cz.runtime_thing" data-resolved="qubit_pairs.p1.macros.cz.runtime_thing">';
    ptr.appendChild(ptd);
    const rp = win.BulkPairEdit.revertPaths([{
        dot_path: 'qubit_pairs.p1.macros.cz.filters', old_value_str: '[[1, 2]]', old_value_disp: '[[1,2]]', old_value_badge: '▦ 1×2', old_kind: 'list',
    }]);
    const badge = win.document.querySelector('#bulk-pair-table [data-list]');
    ok((rp.covered || []).indexOf('qubit_pairs.p1.macros.cz.filters') >= 0 && rp.missing === 0
       && badge.value === '▦ 1×2' && badge.getAttribute('data-orig') === '▦ 1×2',
       'pair grid: a list cell is repainted with the reverted badge and covered');
    const rr = win.BulkPairEdit.revertPaths([{
        dot_path: 'qubit_pairs.p1.macros.cz.runtime_thing', old_value_str: 'x', old_value_disp: 'x', old_kind: 'str',
    }]);
    ok((rr.uncovered || []).indexOf('qubit_pairs.p1.macros.cz.runtime_thing') >= 0 && rr.missing === 0,
       'pair grid: a readonly RUNTIME cell is still FOUND and uncovered (docs/124 M-10 kept)');
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
