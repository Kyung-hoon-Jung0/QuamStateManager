/* R3B MINOR 6 -- Enter (or Down) from the Live State Edit grid search goes
 * INTO the first matching cell without selecting its text, and on a
 * virtualized grid waits for that cell to arrive.
 *
 * Enter jumped into the first cell with its whole text selected: pressing
 * Enter out of habit and typing on replaced a value. And on a grid large
 * enough to be virtualized, a matching cell whose column was still cold (its
 * contents not rendered yet) was skipped: focus landed on a later cell, or
 * stayed in the search when every match was cold.
 *
 * REAL /bulk fragment of a 12-qubit chip rendered with a narrow viewport hint
 * (the server ships the far columns cold), REAL app.js + grid-virt.js +
 * bulk-edit.js and the fragment's own mount script, under jsdom; the cold
 * column's cells come from the REAL /bulk/cells answer. The fixture is
 * written by tests/test_v151_r5b_grid_search_enter.py.
 *
 * Probes: (1) a hot first match, caret at the end, nothing selected; (2) a
 * server-cold first match while a later row is already filled: focus waits,
 * then lands on the FIRST row; (3) a client-detached first cell is restored
 * locally and focused; (4) an answer that leaves the first cell cold keeps
 * focus in the search (never a later row), and a newer query owns the focus.
 *
 * Run: node tests/v151_grid_search_enter_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

let fails = 0, n = 0;
function ok(c, m) { n++; if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 20));
async function settle(pred, maxMs) {
    for (let i = 0; i < (maxMs || 1500) / 10; i++) {
        let done = false;
        try { done = !!pred(); } catch (e) { done = false; }
        if (done) return true;
        await tick(10);
    }
    return false;
}

(async function () {
    const inline = [...FX.page.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
        .filter((m) => !/\bsrc\s*=|application\/json/i.test(m[1])).map((m) => m[2]);
    const markup = FX.page.replace(/<script\b(?![^>]*application\/json)[\s\S]*?<\/script>/gi, '');
    const dom = new JSDOM('<!doctype html><html><body><div id="table-pane">' + markup + '</div></body></html>',
        { url: 'http://localhost/bulk', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document;
    const asked = [];
    let missingRow = null;
    w.fetch = (url) => {
        if (String(url).indexOf('/bulk/cells') === 0) {
            asked.push(String(url));
            return new Promise((res) => setTimeout(() => res({ ok: true, status: 200,
                json: () => {
                    const cells = JSON.parse(JSON.stringify(FX.cells));
                    if (missingRow) delete cells.cells[FX.cold_key][missingRow];
                    return Promise.resolve(cells);
                } }), 60));
        }
        return new Promise(() => {});
    };
    w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
    // jsdom has no layout: give the grid the geometry of a narrow window (as
    // tests/bulk_virt_server_selfcheck.cjs does), so the far columns stay cold
    d.querySelectorAll('#bulk-table th.bulk-col-head').forEach((h, i) => {
        Object.defineProperty(h, 'offsetLeft', { get: () => 60 + i * 400 });
        Object.defineProperty(h, 'offsetWidth', { get: () => 130 });
    });
    Object.defineProperty(d.getElementById('table-pane'), 'clientWidth', { value: 400 });
    w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
    w.eval(fs.readFileSync(path.join(STATIC, 'grid-virt.js'), 'utf8'));
    let virt;
    const create = w.GridVirt.create;
    w.GridVirt.create = function (opts) {
        const gv = create(opts);
        virt = gv;
        return gv;
    };
    w.eval(fs.readFileSync(path.join(STATIC, 'bulk-edit.js'), 'utf8'));
    inline.forEach((s) => w.eval(s));
    await tick(60);
    const search = d.getElementById('bulk-search');
    const coldTd = () => d.querySelector('td.bulk-td[data-col-key="' + FX.cold_key + '"]');
    ok(!!search && coldTd() && coldTd().classList.contains('bulk-td-cold'),
       'the real grid opens with the ' + FX.cold_key + ' column cold (server-virtualized)');
    const press = (k) => {
        const ev = new w.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
        search.dispatchEvent(ev);
        return ev;
    };
    const type = (q) => { search.focus(); search.value = q; search.dispatchEvent(new w.Event('input', { bubbles: true })); };

    // 1. a hot cell: the caret goes in, nothing is selected
    type(FX.hot_query);
    const ev = press('Enter');
    ok(ev.defaultPrevented, 'Enter in the grid search is taken');
    const a = d.activeElement;
    ok(a && a.classList.contains('bulk-cell') && a.getAttribute('data-dot-path') === FX.hot_path,
       'Enter lands on the first matching cell: ' + (a && a.getAttribute('data-dot-path')));
    const len = a ? a.value.length : -1;
    ok(a && a.selectionStart === len && a.selectionEnd === len,
       'with the caret at the end and nothing selected: ' + (a && [a.selectionStart, a.selectionEnd, len]));
    const before = a ? a.value : null;
    // typing on (as a habit) does not replace the value
    if (a) { a.setRangeText('7', a.selectionStart, a.selectionEnd, 'end'); }
    ok(a && a.value === before + '7', 'a key typed after it does not overwrite the value: ' + (a && a.value));

    // Restore the typing probe before blur: a dirty column intentionally
    // survives search, so leaving f_01 dirty would change the first match.
    a.value = before;

    // 2. every match in a COLD column: the column is hydrated, then focused
    // A later row is already ready: it must never win over the first row.
    const later = d.querySelector('tr[data-qubit="q2"] td[data-col-key="' + FX.cold_key + '"]');
    later.innerHTML = FX.cells.cells[FX.cold_key].q2;
    later.classList.remove('bulk-td-cold');
    type(FX.cold_query);
    const ev2 = press('ArrowDown');
    ok(ev2.defaultPrevented, 'Down in the grid search is taken (a cold match is still a match)');
    ok(d.activeElement === search, 'focus waits in search while the first row is loading');
    const landed = await settle(() => d.activeElement && d.activeElement.getAttribute
                                && d.activeElement.getAttribute('data-dot-path') === FX.cold_path);
    ok(asked.some((u) => u.indexOf(encodeURIComponent(FX.cold_key)) >= 0),
       'the cold column is asked for');
    ok(landed, 'focus lands on the first row\'s cell of the cold column once it arrives: '
       + (d.activeElement && d.activeElement.getAttribute && d.activeElement.getAttribute('data-dot-path')));
    const b = d.activeElement;
    ok(b && b.selectionStart === b.value.length && b.selectionEnd === b.value.length,
       'again with nothing selected');

    // The remaining probes require the hydration just asserted above.
    if (!landed) { console.log('FAILED: ' + fails + ' of ' + n); process.exit(1); }

    // 3. Client-detached cells use their saved fragments, in document order.
    search.focus();
    const firstRow = d.querySelector('tr[data-qubit="q3"]');
    firstRow.parentNode.insertBefore(firstRow, firstRow.parentNode.firstChild);
    const detached = firstRow.querySelector('td[data-col-key="' + FX.cold_key + '"]');
    const savedInput = detached.querySelector('input.bulk-cell');
    const frag = d.createDocumentFragment();
    while (detached.firstChild) frag.appendChild(detached.firstChild);
    const st = virt.state();
    st.html.set(detached, frag);
    st.vals.set(detached, savedInput.value.toLowerCase());
    st.cold.add(FX.cold_key);
    detached.classList.add('bulk-td-cold');
    const requests = asked.length;
    press('Enter');
    ok(d.activeElement === savedInput, 'a detached first row is restored and focused before a later ready row');
    ok(asked.length === requests, 'local hydration needs no server request');
    ok(savedInput.selectionStart === savedInput.value.length && savedInput.selectionEnd === savedInput.value.length,
       'the detached cell also has no text selected');

    // 4. An incomplete answer must not jump past the still-cold first row.
    search.focus();
    detached.innerHTML = '';
    detached.classList.add('bulk-td-cold');
    st.cold.add(FX.cold_key);
    st.remote.add(FX.cold_key);
    missingRow = 'q3';
    press('Enter');
    await tick(150);
    ok(detached.classList.contains('bulk-td-cold'), 'an unanswered first cell stays cold');
    ok(d.activeElement === search, 'an unanswered first cell never falls back to a later ready row');

    // A changed query while loading owns the focus; the old request cannot.
    st.cold.add(FX.cold_key);
    st.remote.add(FX.cold_key);
    missingRow = null;
    press('Enter');
    type('q4');
    await tick(150);
    ok(d.activeElement === search, 'a late landing never steals focus from a newer query');

    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
