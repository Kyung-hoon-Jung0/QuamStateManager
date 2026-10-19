/* v1.5.1 R4 A2: holding Enter on an armed two-press button never confirms.
 *
 * A held key repeats: the OS sends keydown (repeat) about every 33 ms after
 * about 500 ms, and on a focused <button> each Enter keydown clicks it. The
 * R3 M6 floor (a confirm counts 400 ms after arming) let the first repeat
 * confirm. Now the arming press must be let go first (keyup / pointerup),
 * and a key repeat on an armed button is swallowed (preventDefault).
 *
 * Every button runs its REAL onclick (from the rendered panel / tray):
 * Keep mine, Discard my edits, the merge with picks, the top bar's Apply.
 * The browser's activation is emulated: an Enter keydown that was not
 * prevented clicks the button; a second pass clicks even a prevented repeat
 * (the release rule must hold on its own). Plus: a mouse double click, an
 * assistive tool's bare clicks (no key or pointer events: no release is
 * needed, the floor still holds) and the Space key.
 *
 * Run: node tests/v151_r4_held_enter_selfcheck.cjs <fixture.json>
 *      (fixture written by tests/test_v151_r4_held_enter.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

function world(panelHtml, trayHtml) {
    const dom = new JSDOM('<!doctype html><html><body><div id="status-bar"></div>'
        + '<div id="tray-host">' + (trayHtml || '') + '</div>'
        + '<div id="state-review-overlay" class="sync-panel-overlay" style="display:block">'
        + '<div id="state-review-host">' + (panelHtml || '') + '</div></div></body></html>',
        { url: 'http://localhost/bulk', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    const runs = [];
    w.htmx = { ajax: () => Promise.resolve(), trigger: (el, name) => { if (name === 'armed') runs.push('armed'); },
               process: () => {}, on: () => {}, config: {} };
    w.fetch = (url) => {
        const u = String(url);
        if (u.indexOf('/state/overwrite-live/preflight') === 0) {
            return Promise.resolve({ json: () => Promise.resolve(
                { ok: true, live_changes: 1, live_paths: ['qubits.q1.T1'], live_hash: 'h' }) });
        }
        if (u.indexOf('/state/sync') === 0) runs.push('merge');
        return new Promise(() => {});
    };
    w.eval(APP);
    w.eval('_keepMinePush = function (d) { window.__pushes = (window.__pushes || 0) + 1; };');
    w.showToast = () => {};
    let now = 1.7e12;
    w.Date.now = () => now;
    w.setInterval = () => 1;
    w.clearInterval = () => {};
    const ctl = {
        w, d: w.document, runs,
        at(ms) { now = 1.7e12 + ms; },
        pushes() { return w.__pushes || 0; },
        // the button's own inline onclick, as the browser runs it
        wire(btn) {
            const code = btn.getAttribute('onclick');
            const fn = new w.Function('event', code);
            btn.addEventListener('click', function (ev) { fn.call(btn, ev); });
            return btn;
        },
        keydown(btn, key, repeat) {
            const e = new w.KeyboardEvent('keydown', { key, repeat: !!repeat, bubbles: true, cancelable: true });
            btn.dispatchEvent(e);
            return e;
        },
        keyup(btn, key) { btn.dispatchEvent(new w.KeyboardEvent('keyup', { key, bubbles: true, cancelable: true })); },
        click(btn) { btn.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true, detail: 0 })); },
        pointer(btn) {
            const P = w.PointerEvent || w.MouseEvent;
            btn.dispatchEvent(new P('pointerdown', { bubbles: true, cancelable: true }));
            btn.dispatchEvent(new P('pointerup', { bubbles: true, cancelable: true }));
            btn.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
        },
        // Enter on a focused button clicks it on keydown, unless prevented
        // (`always`: a browser that clicks even a prevented repeat)
        enter(btn, repeat, always) {
            const e = ctl.keydown(btn, 'Enter', repeat);
            if (always || !e.defaultPrevented) ctl.click(btn);
            return e;
        },
    };
    return ctl;
}

async function heldEnter(label, make, done, always) {
    const t = world(make.panel, make.tray);
    const btn = t.wire(make.pick(t));
    if (make.before) make.before(t, btn);
    t.at(0);
    t.enter(btn, false, always);                  // the arming press, key still down
    await settle();
    ok(btn.getAttribute('data-armed') === '1', label + ': the first press arms it');
    let prevented = 0;
    for (let ms = 500; ms <= 900; ms += 33) {     // the OS repeats the held key
        t.at(ms);
        if (t.enter(btn, true, always).defaultPrevented) prevented++;
        await settle();
    }
    ok(done(t) === 0, label + (always ? ' (repeat clicks forced)' : '')
       + ': holding Enter for 0.9 s confirms nothing (ran ' + done(t) + ')');
    if (!always) ok(prevented > 0, label + ': the repeats on the armed button are swallowed');
    t.at(950);
    t.keyup(btn, 'Enter');                        // let go
    t.at(1500);
    t.enter(btn, false, always);                  // a new press
    t.keyup(btn, 'Enter');
    await settle();
    ok(done(t) === 1, label + ': a new Enter press after letting go confirms once (ran ' + done(t) + ')');
}

(async function () {
    const keep = { panel: FX.mine, pick: (t) => t.d.querySelector('.sp-keep') };
    const disc = { panel: FX.mine, pick: (t) => t.d.querySelector('.sp-discard-all') };
    const merge = {
        panel: FX.collide, pick: (t) => t.d.querySelector('#sp-merge'),
        before(t) {
            t.d.querySelectorAll('input[data-pick-path][value="mine"]').forEach((i) => {
                i.checked = true;
                t.w.SyncPanel.picked(i);
            });
        },
    };
    const apply = { tray: FX.tray, pick: (t) => t.d.querySelector('.btn-apply-live.sync-arm') };
    const pushes = (t) => t.pushes();
    const armed = (t) => t.runs.filter((r) => r === 'armed').length;
    const merged = (t) => t.runs.filter((r) => r === 'merge').length;
    for (const always of [false, true]) {
        await heldEnter('Keep mine', keep, pushes, always);
        await heldEnter('Discard my edits', disc, armed, always);
        await heldEnter('the merge with picks', merge, merged, always);
        await heldEnter('the top bar Apply', apply, armed, always);
    }

    // a mouse: the 400 ms floor still stops a double click; a later click confirms
    {
        const t = world(FX.mine);
        const btn = t.wire(t.d.querySelector('.sp-discard-all'));
        t.at(0); t.pointer(btn);
        t.at(150); t.pointer(btn);
        ok(armed(t) === 0, 'a mouse double click does not confirm');
        t.at(700); t.pointer(btn);
        ok(armed(t) === 1, 'a mouse click after the floor confirms');
    }
    // an assistive tool clicks with no key or pointer events: no release needed
    {
        const t = world(FX.mine);
        const btn = t.wire(t.d.querySelector('.sp-discard-all'));
        t.at(0); t.click(btn);
        t.at(100); t.click(btn);
        ok(armed(t) === 0, 'a bare click right after arming does not confirm');
        t.at(700); t.click(btn);
        ok(armed(t) === 1, 'a bare click after the floor confirms (screen readers, voice control)');
    }
    // Space clicks on keyup: its press is already let go when it arms
    {
        const t = world(FX.mine);
        const btn = t.wire(t.d.querySelector('.sp-discard-all'));
        t.at(0); t.keydown(btn, ' ', false); t.keyup(btn, ' '); t.click(btn);
        ok(btn.getAttribute('data-armed') === '1' && armed(t) === 0, 'Space arms');
        t.at(700); t.keydown(btn, ' ', false); t.keyup(btn, ' '); t.click(btn);
        ok(armed(t) === 1, 'a second Space press confirms');
    }

    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
