/* docs/237 -- the top bar's Reload (window.SmReload), against the real app.js.
 * Pinned: the button opens a popup with "State only" and "State + env"; the
 * popup names unapplied edits that will be kept; a choice POSTs /app/reload
 * with its mode and then reloads the page, carrying a note across the reload
 * (shown as a toast on the next load); a failed answer reloads nothing and
 * says why; Esc and an outside click close the popup.
 * Run: node tests/sm_reload_selfcheck.cjs   (driven by tests/test_app_reload.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));

function world() {
    const dom = new JSDOM('<!doctype html><html><body><header class="topbar"><nav><ul><li>'
        + '<button type="button" class="topbar-reload" id="topbar-reload" aria-expanded="false">R</button></li></ul></nav></header>'
        + '<div id="pending-tray" data-change-count="2"></div><div id="elsewhere">x</div></body></html>',
        { url: 'http://localhost/qubits', pretendToBeVisual: true, runScripts: 'outside-only' });
    const w = dom.window;
    const ss = {};
    Object.defineProperty(w, 'sessionStorage', { configurable: true, value: {
        getItem: (k) => (k in ss ? ss[k] : null), setItem: (k, v) => { ss[k] = String(v); }, removeItem: (k) => { delete ss[k]; } } });
    Object.defineProperty(w, 'localStorage', { configurable: true, value: w.sessionStorage });
    w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
    const posts = [];
    let reply = { ok: true, mode: 'state', kept_edits: 0 };
    w.__setReply = (r) => { reply = r; };
    w.fetch = function (u, o) { return w.__fetch(u, o); };
    w.__fetch = function (u, o) {
        if (u === '/app/reload') {
            posts.push({ u: u, body: o && o.body });
            if (reply === 'network') return Promise.reject(new Error('down'));
            return Promise.resolve({ status: reply.ok ? 200 : 500, json: () => Promise.resolve(reply) });
        }
        return new Promise(function () {});
    };
    const toasts = [];
    w.showToast = (m) => toasts.push(m);
    const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
    const i = src.indexOf('window.SmReload = (function () {');
    const end = /\r?\n\}\)\(\);\r?\n?/.exec(src.slice(i));
    w.eval(src.slice(i, i + end.index + end[0].length));
    let reloads = 0;
    w.SmReload._reloadPage = () => { reloads++; };
    return { w, d: w.document, posts, toasts, ss, reloads: () => reloads };
}
function click(W, el) { el.dispatchEvent(new W.w.MouseEvent('click', { bubbles: true, cancelable: true })); }

(async () => {
    // 1. open, the two choices, the kept-edits note
    const W = world();
    click(W, W.d.getElementById('topbar-reload'));
    const pop = W.d.getElementById('sm-reload-pop');
    ok(pop && !pop.hidden, 'the button opens the popup');
    const opts = [...pop.querySelectorAll('.sm-reload-opt')].map((o) => o.getAttribute('data-mode') + ':' + o.querySelector('b').textContent);
    ok(opts.join() === 'state:State only,both:State + env', 'it offers State only and State + env (' + opts.join() + ')');
    ok(/2 unapplied edits will be kept/.test(pop.querySelector('.sm-reload-note').textContent), 'it names the unapplied edits that will be kept');
    ok(W.d.getElementById('topbar-reload').getAttribute('aria-expanded') === 'true', 'aria-expanded follows');
    // 2. State only -> POST mode=state -> page reload, note carried
    W.w.__setReply({ ok: true, mode: 'state', kept_edits: 2 });
    click(W, pop.querySelector('[data-mode="state"]'));
    await tick(10);
    ok(W.posts.length === 1 && /mode=state/.test(W.posts[0].body), 'State only posts mode=state');
    ok(W.reloads() === 1, 'then reloads the page');
    ok(/Reloaded the chip — 2 unapplied edits kept/.test(W.ss.sm_reload_note || ''), 'the note rides sessionStorage across the reload');
    // 4. State + env
    const W4 = world();
    click(W4, W4.d.getElementById('topbar-reload'));
    W4.w.__setReply({ ok: true, mode: 'both', env: null, kept_edits: 0 });
    click(W4, W4.d.querySelector('#sm-reload-pop [data-mode="both"]'));
    await tick(10);
    ok(/mode=both/.test(W4.posts[0].body) && W4.reloads() === 1, 'State + env posts mode=both and reloads');
    ok(/Reloaded the chip and the env \(no Python env selected\)/.test(W4.ss.sm_reload_note || ''), 'no selected env is said, not hidden');
    // 5. a refused reload reloads nothing and says why
    const W5 = world();
    click(W5, W5.d.getElementById('topbar-reload'));
    W5.w.__setReply({ ok: false, error: 'Could not re-read the chip: bad json' });
    click(W5, W5.d.querySelector('#sm-reload-pop [data-mode="state"]'));
    await tick(10);
    ok(W5.reloads() === 0 && /Nothing reloaded — Could not re-read the chip: bad json/.test(
        W5.d.querySelector('#sm-reload-pop .sm-reload-msg').textContent), 'a refused reload reloads nothing and says why');
    ok([...W5.d.querySelectorAll('#sm-reload-pop .sm-reload-opt')].every((o) => !o.disabled), 'and the choices work again');
    W5.w.__setReply('network');
    click(W5, W5.d.querySelector('#sm-reload-pop [data-mode="state"]'));
    await tick(10);
    ok(W5.reloads() === 0 && /could not reach SM/.test(W5.d.querySelector('#sm-reload-pop .sm-reload-msg').textContent),
       'an unreachable server reloads nothing');
    // 6. Esc and an outside click close it
    W5.d.dispatchEvent(new W5.w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    ok(W5.d.getElementById('sm-reload-pop').hidden, 'Esc closes the popup');
    click(W5, W5.d.getElementById('topbar-reload'));
    click(W5, W5.d.getElementById('elsewhere'));
    ok(W5.d.getElementById('sm-reload-pop').hidden, 'a click elsewhere closes it');
    // 7. the note is shown once on the next load
    const W6 = (() => {
        const dom = new JSDOM('<!doctype html><html><body><button id="topbar-reload"></button></body></html>',
            { url: 'http://localhost/qubits', pretendToBeVisual: true, runScripts: 'outside-only' });
        const w = dom.window;
        const ss = { sm_reload_note: 'Reloaded the chip' };
        Object.defineProperty(w, 'sessionStorage', { configurable: true, value: {
            getItem: (k) => (k in ss ? ss[k] : null), setItem: (k, v) => { ss[k] = String(v); }, removeItem: (k) => { delete ss[k]; } } });
        const toasts = []; w.showToast = (m) => toasts.push(m);
        const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
        const i = src.indexOf('window.SmReload = (function () {');
        const end = /\r?\n\}\)\(\);\r?\n?/.exec(src.slice(i));
        w.eval(src.slice(i, i + end.index + end[0].length));
        return { toasts, ss };
    })();
    await tick(30);   // a fresh page shows it on DOMContentLoaded
    ok(W6.toasts.join() === 'Reloaded the chip' && !('sm_reload_note' in W6.ss), 'the next page shows the note once and forgets it');
    if (fails) { console.error(fails + ' FAIL'); process.exit(1); }
    console.log('sm_reload_selfcheck: all ok');
    process.exit(0);
})();
