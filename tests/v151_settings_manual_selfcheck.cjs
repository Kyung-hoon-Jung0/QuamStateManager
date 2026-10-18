/* v1.5.1 (U P2-27): Settings sliders say what they set; the Config Manual
 * closes on Escape.
 *
 * The sliders were labelled "Aa", a left-right arrow and an up-down arrow.
 * The Config Manual, opened by F1 on a value, kept focus on that value, so
 * its own Escape listener (on the window) never heard the key and it stayed
 * open. The driver (tests/test_v151_p2_settings_manual.py) hands this harness
 * a REAL full page rendered by the Flask test client; the SHIPPED manual.js
 * runs on it.
 *
 * Run: node tests/v151_settings_manual_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.error('jsdom not installed');
    process.exit(2);
}
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const MANUAL = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'manual.js'), 'utf8');
const body = FX.page.slice(FX.page.indexOf('<body'), FX.page.lastIndexOf('</body>') + 7)
    .replace(/<script\b[\s\S]*?<\/script>/gi, '');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const wait = (ms) => new Promise((r) => setTimeout(r, ms || 20));

(async function () {
    const dom = new JSDOM('<!doctype html><html>' + body + '</html>',
        { url: 'http://localhost/bulk', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document;

    /* 1. the sliders name what they set */
    const sliders = Array.from(d.querySelectorAll('.settings-slider'));
    ok(sliders.length >= 5, 'the real Settings popover has its sliders (' + sliders.length + ')');
    const names = sliders.map((s) => (s.querySelector('.settings-slider-name') || {}).textContent || '');
    ok(names.every((t) => /^(Size|Letter gap|Row gap)$/.test(t.trim())), 'each slider is named: ' + JSON.stringify(names));
    ok(!sliders.some((s) => /Aa|↔|↕/.test(s.textContent)), 'no glyph-only label is left');

    /* 2. Escape closes the manual opened by F1 on a value */
    w.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, entries: [], classes: [], categories: [] }) });
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w._anchorPopover = (p) => { p.style.top = '40px'; };
    w._toolTrigger = () => d.getElementById('manual-btn');
    let modal = false;
    w.smModalOpen = () => modal;
    w.eval(MANUAL);
    const pop = d.getElementById('manual-popover');
    ok(!!pop && pop.classList.contains('manual-hidden'), 'the real page holds the closed manual window');
    const cell = d.createElement('input');
    cell.setAttribute('data-dot-path', 'qubits.q1.z.joint_offset');
    (d.getElementById('table-pane') || d.body).appendChild(cell);
    cell.focus();
    const key = (k) => cell.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
    key('F1');
    await wait();
    ok(!pop.classList.contains('manual-hidden'), 'F1 on a value opens the manual');
    ok(d.activeElement === cell, 'and the value keeps the focus');
    modal = true;
    key('Escape');
    ok(!pop.classList.contains('manual-hidden'), 'with a modal up, Escape is the modal\'s');
    modal = false;
    key('Escape');
    ok(pop.classList.contains('manual-hidden'), 'Escape closes the manual');
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
