/* v1.5.1 (r6 P2-16): Instrument Wiring's "Readout out" and "Readout in" are
 * told apart at a glance, in both themes.
 *
 * They were two near-identical oranges (#e67e22 and #f0a030: a luminance
 * contrast of 1.33 between them). Readout in keeps the orange family but is
 * a dark burnt orange now, so the pair differs in lightness -- which also
 * survives colour-vision deficiencies -- and the white port label reads on it.
 * The driver (tests/test_v151_r6_wiring_readout_colours.py) renders the REAL
 * /instrument fragment; the SHIPPED app.js draws the rack and its legend here,
 * and the SHIPPED stylesheets give the Wiring table's role badges.
 *
 * Contrast is the WCAG 2.x relative-luminance ratio (sRGB linearisation,
 * 0.2126 R + 0.7152 G + 0.0722 B, (L1 + 0.05) / (L2 + 0.05)).
 *
 * Run: node tests/v151_r6_wiring_readout_colours_selfcheck.cjs <fixture.json>
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
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const CSS = ['pico.min.css', 'style.css'].map((f) => fs.readFileSync(path.join(STATIC, f), 'utf8'));
const BG = { light: [255, 255, 255], dark: [19, 22.5, 30.5] };   // Pico's --pico-background-color per theme

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 30));

function rgb(s) {
    s = String(s || '').trim();
    let m = /^#([0-9a-f]{6})$/i.exec(s);
    if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
    m = /^rgba?\(([^)]+)\)$/i.exec(s);
    if (m) return m[1].split(',').slice(0, 3).map((x) => parseFloat(x));
    return null;
}
function lum(c) {
    const lin = c.map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}
function contrast(a, b) {
    const la = lum(a), lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
const r2 = (x) => Math.round(x * 100) / 100;

(async function () {
    const scripts = [...FX.page.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)]
        .filter((m) => !/\bsrc\s*=|application\/json/i.test(m[1])).map((m) => m[2]);
    const markup = FX.page.replace(/<script\b[\s\S]*?<\/script>/gi, '');
    const dom = new JSDOM('<!doctype html><html><body><div id="table-pane">' + markup + '</div></body></html>',
        { url: 'http://localhost/instrument', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, d = w.document;
    w.fetch = () => new Promise(() => {});
    w.htmx = { ajax: () => Promise.resolve(), process: () => {}, trigger: () => {}, on: () => {}, config: {} };
    w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
    w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
    w.console.log = () => {};
    w.eval(APP);
    scripts.forEach((s) => { try { w.eval(s); } catch (e) { /* a page script needing more of the app */ } });
    await tick();

    const item = (word) => Array.from(d.querySelectorAll('#instrument-diagram .iw-legend-item'))
        .find((i) => i.textContent.trim() === word);
    const sw = (word) => { const i = item(word); return i ? rgb(i.querySelector('.iw-legend-swatch').style.background) : null; };
    const out = sw('Readout out'), inn = sw('Readout in');
    ok(!!out && !!inn, 'the legend shows both readout colours: ' + JSON.stringify([out, inn]));
    if (out && inn) {
        const c = contrast(out, inn);
        ok(c >= 1.8, 'Readout in and Readout out differ in lightness (contrast ' + r2(c) + ' >= 1.8)');
        ['light', 'dark'].forEach((t) => {
            const cb = contrast(inn, BG[t]);
            ok(cb >= 2.5, 'Readout in stands out on the ' + t + ' page (contrast ' + r2(cb) + ' >= 2.5)');
        });
        const cl = contrast(inn, [255, 255, 255]);
        ok(cl >= 4.5, 'the white port label reads on a Readout in port (contrast ' + r2(cl) + ' >= 4.5)');
        const port = Array.from(d.querySelectorAll('#instrument-diagram .iw-port-circle[data-role="rr_in"] circle'))[0];
        ok(!!port && JSON.stringify(rgb(port.getAttribute('fill'))) === JSON.stringify(inn),
           'the drawn input ports wear the legend\'s colour: ' + (port && port.getAttribute('fill')));
    }

    // the Wiring table's role badge: same colour (one palette), readable text, both themes
    ['light', 'dark'].forEach((t) => {
        const tdom = new JSDOM('<!doctype html><html data-theme="' + t + '"><head>' + CSS.map((c) => '<style>' + c + '</style>').join('')
            + '</head><body><span class="role-badge rr_in">rr_in</span></body></html>', { pretendToBeVisual: true });
        const tw = tdom.window;
        const root = tw.getComputedStyle(tw.document.documentElement).getPropertyValue('--role-rr-in-color').trim();
        ok(!!inn && JSON.stringify(rgb(root)) === JSON.stringify(inn),
           t + ': the table badge colour is the diagram\'s (' + root + ')');
        const badge = tw.document.querySelector('.role-badge.rr_in');
        const fg = rgb(tw.getComputedStyle(badge).color);
        const bg = rgb(root);
        ok(!!fg && !!bg && contrast(fg, bg) >= 4.5,
           t + ': the badge text reads on it (contrast ' + (fg && bg ? r2(contrast(fg, bg)) : '?') + ' >= 4.5)');
    });
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
