/* v1.5.1 P2 layout pins: the SHIPPED stylesheets (pico.min.css + style.css)
 * applied by jsdom's cascade to REAL markup the Flask test client rendered.
 *
 * jsdom does no layout, so a pin here reads the COMPUTED declarations that
 * decide a layout (white-space, max-width, min-width, display, overflow,
 * colours), never a measured box. Each case comes from a pytest driver
 * (tests/test_v151_p2_*.py) as {name, html, theme?, html_class?, checks}; a check is
 * {sel, prop, eq|neq|in|not_in|min_px|max_px, all?, why} (a --custom prop reads its
 * computed value), or {sel, matches: bool, why}: does the selector reach the page.
 *
 * Run: node tests/v151_css_selfcheck.cjs <fixture.json>
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
const CSS = ['pico.min.css', 'style.css'].map((f) => fs.readFileSync(path.join(STATIC, f), 'utf8'));

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function px(v) { const m = /^(-?[\d.]+)px$/.exec(String(v || '').trim()); return m ? parseFloat(m[1]) : NaN; }

const worlds = {};
function world(theme) {
    if (worlds[theme]) return worlds[theme];
    const dom = new JSDOM('<!doctype html><html data-theme="' + theme + '"><head>'
        + CSS.map((c) => '<style>' + c + '</style>').join('') + '</head><body></body></html>',
        { pretendToBeVisual: true });
    worlds[theme] = dom.window;
    return dom.window;
}

FX.cases.forEach(function (cs) {
    const w = world(cs.theme || 'dark');
    w.document.documentElement.className = cs.html_class || '';
    w.document.body.innerHTML = cs.html;
    cs.checks.forEach(function (ck) {
        if ('matches' in ck) {      // does the shipped selector reach this real page?
            const hit = !!w.document.querySelector(ck.sel);
            ok(hit === ck.matches, cs.name + ': ' + ck.sel + (ck.matches ? ' matches' : ' does not match') + ' -- ' + (ck.why || ''));
            return;
        }
        const els = Array.from(w.document.querySelectorAll(ck.sel));
        if (!els.length) { ok(false, cs.name + ': ' + ck.sel + ' is in the real markup'); return; }
        (ck.all ? els : [els[0]]).forEach(function (el) {
            const st = w.getComputedStyle(el);
            const v = ck.prop.indexOf('--') === 0 ? st.getPropertyValue(ck.prop).trim() : st[ck.prop];
            let good = true;
            if ('eq' in ck) good = v === ck.eq;
            if ('neq' in ck) good = good && v !== ck.neq;
            if ('in' in ck) good = good && ck.in.indexOf(v) >= 0;
            if ('not_in' in ck) good = good && ck.not_in.indexOf(v) < 0;
            if ('min_px' in ck) good = good && px(v) >= ck.min_px;
            if ('max_px' in ck) good = good && px(v) <= ck.max_px;
            ok(good, cs.name + ': ' + ck.sel + ' ' + ck.prop + ' = ' + JSON.stringify(v) + ' -- ' + (ck.why || ''));
        });
    });
});
console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
process.exit(fails ? 1 : 0);
