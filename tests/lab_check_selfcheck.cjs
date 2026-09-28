/* lab-check.js -- the "checking with your class's own code..." seam.
 *
 * Drives the SHIPPED web/static/lab-check.js inside a real jsdom window (its
 * own script realm, so window.fetch IS the bare fetch the surfaces call).
 * Pinned:
 *   1. a /field/edit whose path the server calls "lab" shows the badge beside
 *      the edited cell while the edit is unanswered, and removes it after;
 *   2. a lab refusal (400 lab_refused) turns the badge into the refusal text;
 *   3. a non-lab edit never shows a badge, and its response is not delayed:
 *      the edit request goes out FIRST, the watch probe is a side request;
 *   4. the caller receives the very Response object fetch gave (untouched);
 *   5. a GET, or another URL, is never probed;
 *   6. a batch's JSON body names its paths to the probe;
 *   7. fix3: a GATE refusal that names the coupled field (lab_follow) offers
 *      "Set ... too"; the press posts ONE /field/edit-batch with both
 *      updates (group "new"), repaints both cells as still pending, and the
 *      offer does not time out before it is pressed.
 *  10. w9/labwarm: while the lab worker is still STARTING the badge says
 *      "Preparing your lab code... (first check after start)", switches to
 *      the ordinary line the moment the status says ready, never polls once
 *      the edit is answered, and a late status answer never overwrites a
 *      refusal; a ready worker is never polled at all;
 *  11. the Pulses page's htmx lab indicators ([data-lab-indicator]: inside the
 *      requesting form, or its next sibling) get the same text while the
 *      worker starts, and their own text back after the request; the create
 *      form's only when PulsesPage.createNeedsLab() says the create asks.
 * Run: node tests/lab_check_selfcheck.cjs (driven by tests/test_lab_check.py).
 * Exit 0 ok, 1 fail, 2 no jsdom.
 */
'use strict';
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const fs = require('fs');
const path = require('path');
const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'lab-check.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
async function until(f, ms) { const t0 = Date.now(); while (!f() && Date.now() - t0 < (ms || 2000)) await tick(5); return f(); }

function world(labPaths, probeDelay, opts) {
  opts = opts || {};
  const dom = new JSDOM('<!doctype html><body><table><tr><td>' +
    '<input class="bulk-cell" data-dot-path="qubit_pairs.p.macros.cz.flux.flat_length" value="74">' +
    '</td><td><input class="bulk-cell" data-dot-path="qubits.q1.f_01" value="5e9"></td></tr></table></body>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1/' });
  const w = dom.window;
  // jsdom has no layout: give every element a visible rect
  w.Element.prototype.getClientRects = function () { return [{ width: 10, height: 10 }]; };
  w.Element.prototype.getBoundingClientRect = function () {
    return { left: 20, top: 30, bottom: 50, right: 90, width: 70, height: 20 };
  };
  const log = [];
  const pending = [];
  w.fetch = function (url, init) {
    log.push({ url, init });
    if (url === '/field/lab-watch') {
      const body = JSON.parse(init.body);
      const lab = body.paths.some((p) => labPaths.includes(p));
      const j = { lab };
      if (lab && opts.worker) j.worker = opts.worker;
      const ans = { status: 200, json: () => Promise.resolve(j) };
      return probeDelay ? new Promise((r) => setTimeout(() => r(ans), probeDelay)) : Promise.resolve(ans);
    }
    if (url === '/api/lab/worker-status') {
      const st = (opts.states && opts.states.length > 1) ? opts.states.shift()
        : (opts.states && opts.states[0]) || 'ready';
      const ans = { status: 200, json: () => Promise.resolve({ state: st }) };
      return opts.statusDelay ? new Promise((r) => setTimeout(() => r(ans), opts.statusDelay)) : Promise.resolve(ans);
    }
    return new Promise((resolve) => pending.push(resolve));
  };
  w.eval(SRC);
  return { w, log, pending };
}

function resp(status, body) {
  const r = { status, json: () => Promise.resolve(body) };
  r.clone = () => ({ json: () => Promise.resolve(body) });
  return r;
}

(async () => {
  const LAB = 'qubit_pairs.p.macros.cz.flux.flat_length';

  // 1 + 4: badge while waiting, gone after, same Response object
  {
    const W = world([LAB]);
    const body = new W.w.URLSearchParams(); body.append('dot_path', LAB); body.append('value', '4');
    const p = W.w.fetch('/field/edit', { method: 'POST', body: body.toString(),
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
    ok(W.log[0].url === '/field/edit' && W.log[1] && W.log[1].url === '/field/lab-watch',
       'the edit goes out first; the lab probe is a side request');
    await until(() => W.w.document.querySelector('.lab-check-badge'));
    const b = W.w.document.querySelector('.lab-check-badge');
    ok(b && /checking with your class's own code/.test(b.textContent), 'the badge says it is checking');
    ok(b && b.style.top === '52px' && b.style.left === '20px', 'the badge sits under the edited cell (' + (b && b.style.top) + ')');
    const r = resp(200, { ok: true });
    W.pending[0](r);
    const got = await p;
    ok(got === r, 'the caller gets the very Response fetch gave');
    await tick(5);
    ok(!W.w.document.querySelector('.lab-check-badge'), 'the badge is gone once the edit is answered');
  }

  // 2: a refusal is named in place
  {
    const W = world([LAB]);
    const p = W.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=' + encodeURIComponent(LAB) + '&value=4' });
    await until(() => W.w.document.querySelector('.lab-check-badge'));
    W.pending[0](resp(400, { ok: false, lab_refused: true,
      error: 'Your pulse class refused this value -- nothing was written: ValueError: too short' }));
    await p;
    await until(() => W.w.document.querySelector('.lab-check-refused'));
    const b = W.w.document.querySelector('.lab-check-badge');
    ok(b && b.classList.contains('lab-check-refused') && /ValueError: too short/.test(b.textContent),
       'a lab refusal turns the badge into the refusal (' + (b && b.textContent) + ')');
    b && b.click();
    ok(!W.w.document.querySelector('.lab-check-badge'), 'a click dismisses the refusal');
  }

  // 3: non-lab edit -> no badge ever
  {
    const W = world([LAB]);
    const p = W.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=qubits.q1.f_01&value=5.1e9' });
    await until(() => W.log.length >= 2); await tick(30);
    ok(!W.w.document.querySelector('.lab-check-badge'), 'a non-lab edit shows no badge');
    W.pending[0](resp(200, { ok: true })); await p;
  }

  // answered before the probe: no badge flashes afterwards
  {
    const W = world([LAB]);
    const p = W.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=' + encodeURIComponent(LAB) + '&value=40' });
    W.pending[0](resp(200, { ok: true }));
    await p; await tick(40);
    ok(!W.w.document.querySelector('.lab-check-badge'), 'an edit answered before the probe never shows a badge');
  }

  // 5: GET / other URLs never probed
  {
    const W = world([LAB]);
    W.w.fetch('/field/edit?x=1', { method: 'PUT', body: 'dot_path=' + encodeURIComponent(LAB) });
    W.w.fetch('/field/edit', { body: 'dot_path=' + encodeURIComponent(LAB) });
    W.w.fetch('/field/peek', { method: 'POST', body: 'dot_path=' + LAB });
    await tick(5);
    ok(!W.log.some((l) => l.url === '/field/lab-watch'), 'a non-POST or another URL is never probed');
  }

  // 6: batch JSON body
  {
    const W = world([LAB]);
    W.w.fetch('/field/edit-batch', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ updates: [{ dot_path: 'qubits.q1.f_01', value: 1 }, { dot_path: LAB, value: 4 }] }) });
    await until(() => W.w.document.querySelector('.lab-check-badge'));
    const probe = W.log.find((l) => l.url === '/field/lab-watch');
    ok(probe && JSON.parse(probe.init.body).paths.length === 2, 'a batch names all its paths to the probe');
    ok(!!W.w.document.querySelector('.lab-check-badge'), 'a batch touching a lab pulse shows the badge');
  }

  // 7: the set-both offer of a coupled gate
  {
    const W = world([LAB]);
    const T = 'qubit_pairs.p.macros.cz.flux_target.flat_length';
    const events = [];
    W.w.document.addEventListener('cellsReverted', (e) => events.push(e.detail));
    const p = W.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=' + encodeURIComponent(LAB) + '&value=80' });
    await until(() => W.w.document.querySelector('.lab-check-badge'));
    W.pending[0](resp(400, { ok: false, lab_refused: true,
      error: 'Your gate refused this value -- nothing was written: your gate cz refused it in its own apply(): control/target flat_length differ (80 vs 74)',
      lab_follow: [{ dot_path: LAB, value: 80 }, { dot_path: T, value: 80 }] }));
    await p;
    await until(() => W.w.document.querySelector('.lab-check-follow'));
    const btn = W.w.document.querySelector('.lab-check-follow');
    const gb = W.w.document.querySelector('.lab-check-badge.lab-check-refused');
    ok(gb && /refused by your gate/.test(gb.textContent), 'a gate refusal says the GATE refused (' + (gb && gb.textContent.slice(0, 40)) + ')');
    ok(btn && /flux_target\.flat_length too/.test(btn.textContent) && /2 fields, one batch/.test(btn.textContent),
       'a coupled gate refusal offers to set the other field too (' + (btn && btn.textContent) + ')');
    btn.click();
    await until(() => W.log.some((l) => l.url === '/field/edit-batch'));
    const post = W.log.find((l) => l.url === '/field/edit-batch');
    const body = post && JSON.parse(post.init.body);
    ok(body && body.group === 'new' && body.updates.length === 2 && body.updates[1].dot_path === T && body.updates[1].value === 80,
       'the press posts ONE batch with both updates');
    const bi = W.log.indexOf(post);
    W.pending[W.pending.length - 1](Object.assign(resp(200, { ok: true,
      modified: [{ resolved_path: LAB, old_display: '74' }, { resolved_path: T, old_display: '74' }], results: [
      { dot_path: LAB, resolved_path: LAB, applied: true, display: '80' },
      { dot_path: T, resolved_path: T, applied: true, display: '80' }] }), { ok: true }));
    await until(() => events.length);
    ok(events.length && events[0].entries.length === 2 && events[0].entries.every((e) => e.pending === true && e.old_value_disp === '80')
       && events[0].entries[1].pending_old_disp === '74',
       'both written cells are repainted, marked pending with their baseline');
    ok(bi > 0, 'the batch went through the wrapped fetch (it gets the badge too)');
  }

  // 8: a refusal that would run off the bottom of the window stays inside it
  {
    const W = world([LAB]);
    Object.defineProperty(W.w, 'innerHeight', { value: 150, configurable: true });
    Object.defineProperty(W.w.HTMLElement.prototype, 'offsetHeight', { get() { return 120; }, configurable: true });
    const p = W.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=' + encodeURIComponent(LAB) + '&value=4' });
    await until(() => W.w.document.querySelector('.lab-check-badge'));
    W.pending[0](resp(400, { ok: false, lab_refused: true, error: 'Your pulse class refused this value -- nothing was written: x' }));
    await p;
    await until(() => W.w.document.querySelector('.lab-check-refused'));
    await tick(60);
    const b = W.w.document.querySelector('.lab-check-badge');
    ok(b && b.style.top === '26px', 'a badge that would leave the window is pinned inside it (' + (b && b.style.top) + ')');
  }

  // 9 (docs/218): a lab edit written UNCHECKED says so at the cell -- also when
  //   the answer beat the probe (a missing env answers in ~0.1 s), and never
  //   for a non-lab edit or a lab edit with nothing to say
  {
    const NOTE = 'Your lab code could not be run (the selected Python environment no longer exists): ' +
      'this edit was NOT checked against your gate cz. It was written unchecked.';
    const W = world([LAB]);
    const p = W.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=' + encodeURIComponent(LAB) + '&value=82' });
    await until(() => W.w.document.querySelector('.lab-check-badge'));
    W.pending[0](resp(200, { ok: true, warning: NOTE }));
    await p;
    await until(() => W.w.document.querySelector('.lab-check-warned'));
    const b = W.w.document.querySelectorAll('.lab-check-badge');
    ok(b.length === 1 && b[0].classList.contains('lab-check-warned') && /NOT checked/.test(b[0].title)
       && /^⚠ written UNCHECKED — your lab code could not be run: the selected Python environment no longer exists$/.test(b[0].textContent),
       'a 200 carrying the lab note turns the badge into it (' + (b[0] && b[0].textContent) + ')');
    b[0] && b[0].click();
    ok(!W.w.document.querySelector('.lab-check-badge'), 'a click dismisses the note');

    const W2 = world([LAB]);
    const p2 = W2.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=' + encodeURIComponent(LAB) + '&value=83' });
    W2.pending[0](resp(200, { ok: true, warning: NOTE }));     // before the probe answers
    await p2;
    await until(() => W2.w.document.querySelector('.lab-check-warned'));
    ok(!!W2.w.document.querySelector('.lab-check-warned'), 'an answer that beat the probe still shows the note');

    // a probe that answers AFTER the caller has read the body (a real
    // Response cannot be cloned once used): the copy taken on arrival speaks
    const W5 = world([LAB], 40);
    const p5 = W5.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=' + encodeURIComponent(LAB) + '&value=84' });
    let used = false;
    const real = { status: 200, json: () => { used = true; return Promise.resolve({ ok: true, warning: NOTE }); } };
    real.clone = () => { if (used) throw new TypeError('body already used'); return { json: () => Promise.resolve({ ok: true, warning: NOTE }) }; };
    W5.pending[0](real);
    await (await p5).json();          // the surface reads the body at once
    await until(() => W5.w.document.querySelector('.lab-check-warned'), 500);
    ok(!!W5.w.document.querySelector('.lab-check-warned'), 'a late probe answer still shows the note (the body was copied on arrival)');

    const W3 = world([LAB]);
    const p3 = W3.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=qubits.q1.f_01&value=5.1e9' });
    W3.pending[0](resp(200, { ok: true, warning: 'a re-point target does not exist' }));
    await p3; await tick(40);
    ok(!W3.w.document.querySelector('.lab-check-badge'), 'a non-lab edit\'s warning is its surface\'s own business');

    const W4 = world([LAB]);
    const p4 = W4.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=' + encodeURIComponent(LAB) + '&value=76' });
    await until(() => W4.w.document.querySelector('.lab-check-badge'));
    W4.pending[0](resp(200, { ok: true }));
    await p4; await tick(40);
    ok(!W4.w.document.querySelector('.lab-check-badge'), 'a checked lab edit leaves no badge');
  }

  // 10 (w9/labwarm): "Preparing your lab code..." while the worker starts
  {
    const PREP = 'Preparing your lab code\u2026 (first check after start)';
    const states = ['starting', 'starting', 'ready'];
    const W = world([LAB], 0, { worker: 'cold', states });
    const p = W.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=' + encodeURIComponent(LAB) + '&value=86' });
    await until(() => W.w.document.querySelector('.lab-check-badge'));
    const b = W.w.document.querySelector('.lab-check-badge');
    ok(b && b.textContent === PREP && b.classList.contains('lab-check-preparing'),
       'a check waiting on a starting worker says Preparing (' + (b && b.textContent) + ')');
    await until(() => /checking with your class/.test(b.textContent), 4000);
    ok(/^checking with your class's own code/.test(b.textContent) && !b.classList.contains('lab-check-preparing'),
       'the badge says the ordinary line once the worker is ready (' + b.textContent + ')');
    const polls = () => W.log.filter((l) => l.url === '/api/lab/worker-status').length;
    const n = polls();
    ok(n >= 3, 'it asked the status while preparing (' + n + ' polls)');
    W.pending[0](resp(200, { ok: true }));
    await p; await tick(1400);
    ok(polls() === n, 'no status poll once the worker is ready / the edit is answered');
    ok(!W.w.document.querySelector('.lab-check-badge'), 'the badge is gone after the answer');

    // a ready worker: the ordinary line, never polled
    const R = world([LAB], 0, { worker: 'ready', states: ['ready'] });
    const pr = R.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=' + encodeURIComponent(LAB) + '&value=87' });
    await until(() => R.w.document.querySelector('.lab-check-badge'));
    const rb = R.w.document.querySelector('.lab-check-badge');
    ok(rb && /^checking with your class's own code/.test(rb.textContent), 'a ready worker: the ordinary line');
    await tick(700);
    ok(!R.log.some((l) => l.url === '/api/lab/worker-status'), 'a ready worker is never polled');
    R.pending[0](resp(200, { ok: true })); await pr;

    // a refusal arriving while preparing is never overwritten by a late status
    const F = world([LAB], 0, { worker: 'starting', states: ['starting'] });
    const pf = F.w.fetch('/field/edit', { method: 'POST', body: 'dot_path=' + encodeURIComponent(LAB) + '&value=88' });
    await until(() => F.w.document.querySelector('.lab-check-badge'));
    F.pending[0](resp(400, { ok: false, lab_refused: true,
      error: 'Your pulse class refused this value -- nothing was written: ValueError: odd' }));
    await pf;
    await until(() => F.w.document.querySelector('.lab-check-refused'));
    await tick(1400);
    const fb = F.w.document.querySelector('.lab-check-badge');
    ok(fb && /ValueError: odd/.test(fb.textContent) && !/Preparing/.test(fb.textContent),
       'a refusal stays the refusal (' + (fb && fb.textContent) + ')');
  }

  // 11 (w9/labwarm): the Pulses page's htmx lab indicators
  {
    const PREP = 'Preparing your lab code\u2026 (first check after start)';
    const W = world([LAB], 0, { states: ['cold', 'cold', 'ready'] });
    const d = W.w.document;
    d.body.insertAdjacentHTML('beforeend',
      '<form id="del"><button type="submit" disabled>Delete x</button>' +
      '<span class="htmx-indicator" data-lab-indicator>Checking with your lab code\u2026</span></form>' +
      '<form id="fld"><input name="value"></form><span id="nx" class="htmx-indicator" data-lab-indicator>checking with your class\u2019s own code\u2026</span>' +
      '<form id="plain"><span class="htmx-indicator">Saving\u2026</span></form>' +
      '<form id="cre"><span id="cb" class="htmx-indicator" data-lab-indicator="create">Creating\u2026</span></form>');
    const fire = (el, name) => el.dispatchEvent(new W.w.CustomEvent(name, { bubbles: true, detail: { elt: el } }));
    const del = d.getElementById('del');
    const ind = del.querySelector('[data-lab-indicator]');
    fire(del, 'htmx:beforeRequest');
    await until(() => ind.textContent === PREP, 2000);
    ok(ind.textContent === PREP, 'the delete step says Preparing while the worker starts (' + ind.textContent + ')');
    await until(() => /^Checking with your lab code/.test(ind.textContent), 4000);
    ok(/^Checking with your lab code/.test(ind.textContent), 'and "Checking with your lab code..." once it is ready');
    fire(del, 'htmx:afterRequest');
    ok(ind.textContent === 'Checking with your lab code\u2026' && !ind.classList.contains('lab-check-preparing'),
       'the indicator gets its own text back after the request');

    const W2 = world([LAB], 0, { states: ['starting'] });
    const d2 = W2.w.document;
    d2.body.insertAdjacentHTML('beforeend',
      '<form id="fld"><input name="value"></form><span id="nx" class="htmx-indicator" data-lab-indicator>checking with your class\u2019s own code\u2026</span>' +
      '<form id="plain"><span class="htmx-indicator">Saving\u2026</span></form>' +
      '<form id="cre"><span id="cb" class="htmx-indicator" data-lab-indicator="create">Creating\u2026</span></form>');
    const fire2 = (el, name) => el.dispatchEvent(new W2.w.CustomEvent(name, { bubbles: true, detail: { elt: el } }));
    const fld = d2.getElementById('fld');
    fire2(fld, 'htmx:beforeRequest');
    await until(() => d2.getElementById('nx').textContent === PREP, 2000);
    ok(d2.getElementById('nx').textContent === PREP, 'a field form\'s NEXT-sibling indicator says Preparing too');
    fire2(fld, 'htmx:afterRequest');
    const plain = d2.getElementById('plain');
    fire2(plain, 'htmx:beforeRequest');
    await tick(50);
    ok(plain.textContent === 'Saving\u2026', 'an indicator not marked data-lab-indicator is never touched');
    const cre = d2.getElementById('cre');
    W2.w.PulsesPage = { createNeedsLab: () => false };
    fire2(cre, 'htmx:beforeRequest');
    await tick(80);
    ok(d2.getElementById('cb').textContent === 'Creating\u2026', 'a create of a catalog class keeps its own busy line');
    fire2(cre, 'htmx:afterRequest');
    W2.w.PulsesPage = { createNeedsLab: () => true };
    fire2(cre, 'htmx:beforeRequest');
    await until(() => d2.getElementById('cb').textContent === PREP, 2000);
    ok(d2.getElementById('cb').textContent === PREP, 'a create that asks the lab says Preparing while the worker starts');
    fire2(cre, 'htmx:afterRequest');
    ok(d2.getElementById('cb').textContent === 'Creating\u2026', 'and its own line back after');

    // before its first status answer (a busy server), the indicator says what
    // the server RENDERED into it (data-lab-state), never the ordinary line first
    const W3 = world([LAB], 0, { states: ['starting'], statusDelay: 400 });
    const d3 = W3.w.document;
    d3.body.insertAdjacentHTML('beforeend',
      '<form id="d1"><span class="htmx-indicator" data-lab-indicator data-lab-state="starting">Checking with your lab code\u2026</span></form>' +
      '<form id="d2"><span class="htmx-indicator" data-lab-indicator data-lab-state="ready">Checking with your lab code\u2026</span></form>');
    const fire3 = (el, name) => el.dispatchEvent(new W3.w.CustomEvent(name, { bubbles: true, detail: { elt: el } }));
    const f1 = d3.getElementById('d1');
    fire3(f1, 'htmx:beforeRequest');
    ok(f1.querySelector('span').textContent === PREP,
       'a worker rendered as starting: Preparing at once, before the status answers');
    fire3(f1, 'htmx:afterRequest');
    const W4 = world([LAB], 0, { states: ['ready'], statusDelay: 400 });
    const d4 = W4.w.document;
    d4.body.insertAdjacentHTML('beforeend',
      '<form id="d2"><span class="htmx-indicator" data-lab-indicator data-lab-state="ready">Checking with your lab code\u2026</span></form>');
    const f2 = d4.getElementById('d2');
    f2.dispatchEvent(new W4.w.CustomEvent('htmx:beforeRequest', { bubbles: true, detail: { elt: f2 } }));
    ok(f2.querySelector('span').textContent === 'Checking with your lab code\u2026',
       'a worker rendered as ready: the ordinary line at once');
  }

  if (fails) { console.error(fails + ' FAIL'); process.exit(1); }
  console.log('all ok');
})();
