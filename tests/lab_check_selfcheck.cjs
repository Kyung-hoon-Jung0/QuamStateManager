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

function world(labPaths) {
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
      return Promise.resolve({ status: 200, json: () => Promise.resolve({ lab }) });
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

  if (fails) { console.error(fails + ' FAIL'); process.exit(1); }
  console.log('all ok');
})();
