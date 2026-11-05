/* The workbench's "Through SM" choice under jsdom (qualibrate_proxy D-20, D-21).
 * Driven by tests/test_qualibrate_proxy.py::test_the_workbench_selfcheck, which
 * renders /workbench to a file and passes its path.
 */
'use strict';
const fs = require('fs');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.log('jsdom not installed'); process.exit(0); }

const PAGE = fs.readFileSync(process.argv[2], 'utf8')
  .replace(/<script src="[^"]*"><\/script>/g, '');           // Split.js is stubbed below
let n = 0, fails = 0;
function ok(c, msg) { n++; if (!c) { fails++; console.log('FAIL ' + msg); } }
const tick = (ms) => new Promise(r => setTimeout(r, ms || 30));

async function world(status, stored, opts) {
  opts = opts || {};
  const posts = [];
  const dom = new JSDOM(PAGE, {
    url: 'http://127.0.0.1:5491/workbench', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(w) {
      w.Split = function () { return { destroy() {}, setSizes() {}, getSizes() { return [50, 50]; } }; };
      Object.keys(stored || {}).forEach(k => w.localStorage.setItem(k, stored[k]));
      w.fetch = function (url, init) {
        url = String(url);
        if (url === '/qualibrate-ui-target' && init && init.method === 'POST') {
          posts.push(JSON.parse(init.body));
          const ans = opts.postAnswer || Object.assign({}, status, { url: JSON.parse(init.body).url, source: 'saved' });
          return Promise.resolve({ json: () => Promise.resolve(ans) });
        }
        if (url === '/qualibrate-ui-target') return Promise.resolve({ json: () => Promise.resolve(status) });
        if (url.indexOf('/workbench/') === 0) return Promise.resolve({ json: () => Promise.resolve({}) });
        return opts.dead ? Promise.reject(new Error('down')) : Promise.resolve({});
      };
    }
  });
  await tick(60);
  const w = dom.window;
  const $ = (id) => w.document.getElementById(id);
  return { w, $, posts, src: () => $('wb-qb').getAttribute('src'), close: () => w.close() };
}
const OK = { url: 'http://127.0.0.1:8001', source: 'default', env: 'QUALIBRATE_URL', ok: true, version: '2.0.0', reason: null };
const DOWN = Object.assign({}, OK, { ok: false, version: null, reason: 'nothing answered at http://127.0.0.1:8001' });

(async () => {
  // A. untouched, SM's check passes: through SM
  {
    const t = await world(OK);
    ok(t.src() === '/qualibrate-ui/', 'default with a passing check is through SM: ' + t.src());
    ok(t.$('wb-viasm').checked, 'the box says so');
    ok(t.$('wb-qb-note').hidden, 'nothing to explain');
    ok(t.$('wb-url').value === 'http://127.0.0.1:8001', 'the address box shows the address, not the proxy path');
    t.close();
  }
  // B. untouched, check fails: direct, with one line saying why
  {
    const t = await world(DOWN);
    ok(t.src() === 'http://127.0.0.1:8001', 'a failing check falls back to direct: ' + t.src());
    ok(!t.$('wb-viasm').checked, 'the box is off');
    const note = t.$('wb-qb-note');
    ok(!note.hidden && /directly/.test(note.textContent) && /nothing answered/.test(note.textContent), 'one line says why: ' + note.textContent);
    t.close();
  }
  // C. an explicit "direct" wins over a passing check, and the toggle is remembered
  {
    const t = await world(OK, { quam_workbench_qb_mode: 'direct' });
    ok(t.src() === 'http://127.0.0.1:8001', 'explicit direct wins');
    t.$('wb-viasm').checked = true;
    t.$('wb-viasm').dispatchEvent(new t.w.Event('change'));
    ok(t.src() === '/qualibrate-ui/' && t.w.localStorage.getItem('quam_workbench_qb_mode') === 'sm', 'ticking it goes through SM and is remembered');
    t.close();
  }
  // D. an explicit "sm" with a failing check still goes through SM and says what is wrong
  {
    const t = await world(DOWN, { quam_workbench_qb_mode: 'sm' });
    ok(t.src() === '/qualibrate-ui/', 'explicit SM wins');
    ok(!t.$('wb-qb-note').hidden && /cannot reach/.test(t.$('wb-qb-note').textContent), 'and says SM cannot reach it');
    t.close();
  }
  // E. the old "Through SM" button stored the proxy path as the address: read as the choice
  {
    const t = await world(DOWN, { quam_workbench_qb_url: '/qualibrate-ui/' });
    ok(t.src() === '/qualibrate-ui/', 'old stored proxy path is the SM choice');
    ok(t.w.localStorage.getItem('quam_workbench_qb_url') === null, 'and is no longer an address');
    ok(t.$('wb-url').value === 'http://127.0.0.1:8001', 'the box shows a real address');
    t.close();
  }
  // F. a new address is offered to SM, so "Through SM" follows it
  {
    const t = await world(OK);
    t.$('wb-url').value = 'http://127.0.0.1:8002';
    t.$('wb-go').click();
    await tick();
    ok(t.posts.length === 1 && t.posts[0].url === 'http://127.0.0.1:8002', 'the address is offered to SM');
    ok(t.src() === '/qualibrate-ui/', 'and the pane reloads through SM');
    ok(t.w.localStorage.getItem('quam_workbench_qb_url') === 'http://127.0.0.1:8002', 'remembered here too');
    t.close();
  }
  // G. an address from SM's environment is never overwritten from here
  {
    const t = await world(Object.assign({}, OK, { source: 'environment', url: 'http://127.0.0.1:8005' }));
    ok(!t.$('wb-qb-note').hidden && /QUALIBRATE_URL=http:\/\/127\.0\.0\.1:8005/.test(t.$('wb-qb-note').textContent), 'the environment is named');
    ok(t.$('wb-url').value === 'http://127.0.0.1:8005', 'the box shows the address SM uses');
    t.$('wb-url').value = 'http://127.0.0.1:8002';
    t.$('wb-go').click();
    await tick();
    ok(t.posts.length === 0, 'nothing posted');
    t.close();
  }
  // H. a refused address: the reason shows, the choice stays
  {
    const t = await world(OK, null, { postAnswer: { ok: false, reason: 'Not saved: nothing answered.' } });
    t.$('wb-url').value = 'http://127.0.0.1:8009';
    t.$('wb-go').click();
    await tick();
    ok(/Not saved/.test(t.$('wb-qb-note').textContent), 'the refusal is said: ' + t.$('wb-qb-note').textContent);
    t.close();
  }
  // I. an address remembered in this browser is offered to SM on open, so both agree
  {
    const t = await world(DOWN, { quam_workbench_qb_url: 'http://127.0.0.1:8003' }, { postAnswer: Object.assign({}, OK, { url: 'http://127.0.0.1:8003', source: 'saved' }) });
    ok(t.posts.length === 1 && t.posts[0].url === 'http://127.0.0.1:8003', 'offered on open');
    ok(t.src() === '/qualibrate-ui/' && t.$('wb-qb-note').hidden, 'and SM serving it, the pane goes through SM');
    t.close();
  }
  // J. the same address SM already has is not posted again
  {
    const t = await world(Object.assign({}, OK, { source: 'saved' }), { quam_workbench_qb_url: 'http://127.0.0.1:8001' });
    ok(t.posts.length === 0, 'nothing posted when SM already has it');
    t.close();
  }
  // K. server-supplied addresses must be HTTP(S), even after an unsuccessful check.
  for (const url of ['javascript:alert(1)', 'data:text/html,unsafe', 'file:///unsafe']) {
    const t = await world(Object.assign({}, DOWN, { source: 'saved', url }));
    ok(t.src() === OK.url, 'unsafe server URL falls back to the default: ' + url);
    t.close();
  }
  // L. compare the same host/port forms the server saves (it discards path/query).
  for (const [server, browser] of [
    ['http://127.0.0.1:8001', 'http://127.0.0.1:8001/'],
    ['http://localhost:80', 'http://LOCALHOST/old?query=1#page'],
    ['http://[::1]:80', 'http://[::1]/'],
  ]) {
    const t = await world(Object.assign({}, OK, { source: 'saved', url: server }), { quam_workbench_qb_url: browser });
    ok(t.posts.length === 0, 'canonical match does not POST on boot: ' + browser);
    t.$('wb-go').click();
    await tick();
    ok(t.posts.length === 0, 'canonical match does not POST on reload: ' + browser);
    t.close();
  }
  console.log(fails ? `FAILED ${fails} of ${n} assertions` : `ok (${n} assertions)`);
  process.exit(fails ? 1 : 0);
})();
