/* landing-env.js -- the Projects landing's per-project environment picker
 * (w9/labwarm). Drives the SHIPPED script under jsdom.
 * Pinned:
 *   1. nothing is fetched until a card's Change... is pressed (a remembered
 *      env is never re-discovered or re-probed);
 *   2. the picker opens for THAT project with "Discovering environments...",
 *      then one row per env, each "checking..." with its Use button disabled
 *      until its probe says the QM stack is there; a missing stack keeps it
 *      disabled and says what is missing; the env in use is marked;
 *   3. Use posts /qualibrate/project-env through htmx (project, python,
 *      how=changed) into THAT card's env row, and closes the picker;
 *   4. a typed path is probed, and the RESOLVED interpreter is what is saved;
 *   5. a slow answer for a picker that was closed / re-opened never renders;
 *   6. Escape closes it;
 *   7. an env selected ELSEWHERE (POST /generate/select-env from Generate
 *      Config / the Runner) re-fetches the sidebar badge slot, so it never
 *      names the previous env; a refused selection does not; the caller gets
 *      the very Response fetch gave.
 * Exit 0 ok, 1 fail, 2 no jsdom.
 */
'use strict';
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const fs = require('fs');
const path = require('path');
const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'landing-env.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
async function until(f, ms) { const t0 = Date.now(); while (!f() && Date.now() - t0 < (ms || 2000)) await tick(5); return f(); }

const HTML = '<!doctype html><body>' +
  '<div id="sidebar-folder-badges-slot"><a class="project-env-badge">env A</a></div>' +
  '<div class="landing-card"><div class="landing-card-env" data-project="alpha">env <code>A</code>' +
  '<button type="button" data-env-change="alpha" data-env-current="C:\\envs\\A\\python.exe">Change…</button></div></div>' +
  '<div class="landing-card"><div class="landing-card-env" data-project="beta">env <em>none</em>' +
  '<button type="button" data-env-change="beta" data-env-current="">Choose…</button></div></div>' +
  '<section id="landing-env-picker" hidden><strong><span data-env-project></span></strong>' +
  '<button data-env-rescan>Rescan</button><button data-env-close>x</button>' +
  '<p data-env-loading>Discovering environments…</p><div data-env-list></div>' +
  '<input data-env-custom><button data-env-use>Use</button><span data-env-custom-status></span></section></body>';

function world(opts) {
  opts = opts || {};
  const dom = new JSDOM(HTML, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1/' });
  const w = dom.window;
  const log = [];
  const hold = {};
  w.fetch = function (url) {
    log.push(url);
    if (url.indexOf('/generate/envs') === 0) {
      const ans = { json: () => Promise.resolve({ envs: [
        { name: 'A', python: 'C:\\envs\\A\\python.exe', kind: 'conda' },
        { name: 'B', python: 'C:\\envs\\B\\python.exe', kind: 'conda' },
        { name: '.venv · lab', python: 'C:\\lab\\.venv\\Scripts\\python.exe', kind: 'uv-venv' }] }) };
      if (opts.slowEnvs) return new Promise((r) => { hold.envs = () => r(ans); });
      return Promise.resolve(ans);
    }
    if (url.indexOf('/generate/probe') === 0) {
      const py = decodeURIComponent(url.split('python=')[1]);
      let info;
      if (/\\B\\/.test(py)) info = { usable: false, missing: ['quam_builder'] };
      else if (/typed/.test(py)) info = { usable: true, resolved: 'C:\\typed\\Scripts\\python.exe', versions: {} };
      else info = { usable: true, versions: { quam: '0.6.0', quam_builder: '0.4.0' } };
      if (opts.slowProbe) return new Promise((r) => { (hold.probes = hold.probes || []).push(() => r({ json: () => Promise.resolve(info) })); });
      return Promise.resolve({ json: () => Promise.resolve(info) });
    }
    if (url === '/generate/select-env') {
      const r = { ok: !opts.refuse, status: opts.refuse ? 400 : 200, json: () => Promise.resolve({ ok: !opts.refuse }) };
      hold.selectResp = r;
      return Promise.resolve(r);
    }
    if (url === '/sidebar/folder-badges') {
      return Promise.resolve({ ok: true, text: () => Promise.resolve(
        '<div id="sidebar-folder-badges-slot"><a class="project-env-badge">env NEW</a></div>') });
    }
    return Promise.reject(new Error('unexpected ' + url));
  };
  const posts = [];
  w.htmx = { ajax: function (verb, url, o) { posts.push({ verb, url, o }); return Promise.resolve(); } };
  w.eval(SRC);
  return { w, d: w.document, log, posts, hold };
}
function click(W, sel) { W.d.querySelector(sel).dispatchEvent(new W.w.MouseEvent('click', { bubbles: true })); }

(async () => {
  // 1 + 2
  {
    const W = world({ slowProbe: true });
    await tick(20);
    ok(W.log.length === 0, 'nothing is fetched until Change is pressed');
    click(W, '[data-env-change="alpha"]');
    const pk = W.d.getElementById('landing-env-picker');
    ok(!pk.hidden && W.d.querySelector('[data-env-project]').textContent === 'alpha', 'the picker opens for that project');
    await until(() => W.d.querySelectorAll('.landing-env-row').length === 3);
    const rows = W.d.querySelectorAll('.landing-env-row');
    ok(rows.length === 3, 'one row per discovered env (' + rows.length + ')');
    ok([].every.call(rows, (r) => r.querySelector('.landing-env-status').textContent === 'checking…'
       && r.querySelector('button').disabled), 'every row is "checking…" with Use disabled until probed');
    ok(rows[0].classList.contains('selected') && rows[0].querySelector('button').textContent === 'In use',
       'the env in use is marked');
    ok(rows[2].querySelector('.landing-env-kind').textContent === 'uv venv', 'a uv venv carries its kind');
    W.hold.probes.forEach((f) => f());
    await until(() => !rows[2].querySelector('button').disabled);
    ok(!rows[0].querySelector('button').disabled && !rows[2].querySelector('button').disabled,
       'a probed env with the QM stack can be used');
    ok(rows[1].querySelector('button').disabled && /missing: quam_builder/.test(rows[1].querySelector('.landing-env-status').textContent),
       'an env without the stack stays disabled and says what is missing');
    // 3
    rows[2].querySelector('button').dispatchEvent(new W.w.MouseEvent('click', { bubbles: true }));
    await until(() => W.posts.length);
    const p = W.posts[0];
    ok(p && p.verb === 'POST' && p.url === '/qualibrate/project-env' && p.o.values.project === 'alpha'
       && p.o.values.python === 'C:\\lab\\.venv\\Scripts\\python.exe' && p.o.values.how === 'changed'
       && p.o.target === W.d.querySelector('.landing-card-env[data-project="alpha"]') && p.o.swap === 'innerHTML',
       'Use posts the pick into THAT card\'s env row through htmx');
    await tick(10);
    ok(pk.hidden, 'the picker closes after the pick');
  }
  // 4: a typed path
  {
    const W = world();
    click(W, '[data-env-change="beta"]');
    W.d.querySelector('[data-env-custom]').value = '"C:\\typed"';
    click(W, '[data-env-use]');
    await until(() => W.posts.length);
    ok(W.posts[0] && W.posts[0].o.values.python === 'C:\\typed\\Scripts\\python.exe' && W.posts[0].o.values.project === 'beta',
       'a typed folder is probed and its RESOLVED interpreter is saved');
  }
  // 5: a stale answer never renders
  {
    const W = world({ slowEnvs: true });
    click(W, '[data-env-change="alpha"]');
    await until(() => W.hold.envs);
    click(W, '[data-env-close]');
    W.hold.envs();
    await tick(30);
    ok(W.d.querySelectorAll('.landing-env-row').length === 0, 'an answer for a closed picker never renders');
  }
  // 6: Escape
  {
    const W = world();
    click(W, '[data-env-change="alpha"]');
    W.d.dispatchEvent(new W.w.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    ok(W.d.getElementById('landing-env-picker').hidden, 'Escape closes the picker');
  }
  // 7: an env selected elsewhere refreshes the badge
  {
    const W = world();
    const got = await W.w.fetch('/generate/select-env', { method: 'POST', body: '{}' });
    ok(got === W.hold.selectResp, 'the caller gets the very Response fetch gave');
    await until(() => /env NEW/.test(W.d.getElementById('sidebar-folder-badges-slot').textContent));
    ok(/env NEW/.test(W.d.getElementById('sidebar-folder-badges-slot').textContent)
       && W.log.indexOf('/sidebar/folder-badges') > W.log.indexOf('/generate/select-env'),
       'a selection elsewhere re-fetches the sidebar env badge');
    const R = world({ refuse: true });
    await R.w.fetch('/generate/select-env', { method: 'POST', body: '{}' });
    await tick(30);
    ok(R.log.indexOf('/sidebar/folder-badges') < 0 && /env A/.test(R.d.getElementById('sidebar-folder-badges-slot').textContent),
       'a refused selection leaves the badge alone');
    await W.w.fetch('/generate/envs');
    ok(W.log.filter((u) => u === '/sidebar/folder-badges').length === 1, 'a GET never refreshes it');
  }
  if (fails) { console.error(fails + ' FAIL'); process.exit(1); }
  console.log('all ok');
})();
