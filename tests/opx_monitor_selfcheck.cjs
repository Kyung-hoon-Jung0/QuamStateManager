/* OPX monitor page JS (web/static/opx-monitor.js): what the page SAYS.
 * - a close asks first and the question names the QM, its FEMs and its jobs;
 * - declining sends nothing; accepting POSTs once and reports the answer;
 * - a refused close is shown as "Not closed: <reason>";
 * - a refresh SM does not answer turns the status line into an error sentence.
 * Run: node tests/opx_monitor_selfcheck.cjs */
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
let fails = 0, passes = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { passes++; console.log('ok - ' + m); } }
const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'opx-monitor.js'), 'utf8');

function world() {
  const dom = new JSDOM('<!doctype html><html><body>' +
    '<div id="opxmon-body" hx-get="/opx-monitor/body?tab=cleanup" data-state="ok">' +
    '<p class="opxmon-status opxmon-status--ok"><span class="opxmon-sentence">Updated 1 s ago.</span></p>' +
    '<button class="btn-sm opxmon-close" data-qm-id="QM-x" data-ports="con1/2, con1/3" data-jobs="Running">Close</button>' +
    '<button class="btn-sm opxmon-close-all" data-qm-ids="QM-x, QM-y">Close all</button>' +
    '<p class="opxmon-close-result" hidden></p></div></body></html>',
    { url: 'http://localhost/opx-monitor/cleanup', runScripts: 'outside-only' });
  const w = dom.window;
  w.__asked = []; w.__posts = []; w.__answer = true; w.__reply = { ok: true, closed: ['QM-x'] };
  w.confirm = (t) => { w.__asked.push(t); return w.__answer; };
  w.fetch = (url, opts) => { w.__posts.push([url, opts]); return Promise.resolve({ status: 200, json: () => Promise.resolve(w.__reply) }); };
  w.htmx = { ajax: () => { w.__refreshed = (w.__refreshed || 0) + 1; } };
  w.eval(SRC);
  return w;
}
const tick = () => new Promise(r => setTimeout(r, 20));

(async () => {
  let w = world();
  w.__answer = false;
  w.document.querySelector('.opxmon-close').click();
  await tick();
  ok(w.__asked.length === 1, 'a close asks first');
  ok(/QM-x/.test(w.__asked[0]) && /con1\/2, con1\/3/.test(w.__asked[0]) && /Running/.test(w.__asked[0]),
     'the question names the QM, its FEMs and its jobs');
  ok(w.__posts.length === 0, 'declining sends nothing');

  w.__answer = true;
  w.document.querySelector('.opxmon-close').click();
  await tick();
  ok(w.__posts.length === 1 && w.__posts[0][0] === '/opx-monitor/close' && w.__posts[0][1].method === 'POST', 'accepting POSTs once');
  ok(w.__posts[0][1].body.get('qm_id') === 'QM-x' && !w.__posts[0][1].body.get('all'), 'the POST names that QM only');
  const res = w.document.querySelector('.opxmon-close-result');
  ok(!res.hidden && /Closed: QM-x/.test(res.textContent), 'the result is shown');
  ok(w.__refreshed === 1, 'the body re-fetches after a close');

  w = world();
  w.__reply = { ok: false, error: 'The server refused: busy' };
  w.document.querySelector('.opxmon-close-all').click();
  await tick();
  ok(/ALL/.test(w.__asked[0]) && /QM-x\nQM-y/.test(w.__asked[0]), 'close-all names every QM');
  ok(w.__posts[0][1].body.get('all') === '1', 'close-all POSTs all=1');
  ok(w.__posts[0][1].body.get('qm_ids') === 'QM-x, QM-y', 'close-all POSTs exactly the confirmed ids');
  const r2 = w.document.querySelector('.opxmon-close-result');
  ok(/Not closed: The server refused: busy/.test(r2.textContent) && r2.classList.contains('ui-state--error'), 'a refusal is said as an error');

  w = world();
  const b = w.document.getElementById('opxmon-body');
  w.document.dispatchEvent(new w.CustomEvent('htmx:sendError', { detail: { elt: b } }));
  ok(/SM did not answer/.test(b.querySelector('.opxmon-sentence').textContent), 'an unanswered refresh is said');
  ok(b.querySelector('.opxmon-status').classList.contains('opxmon-status--error') && !/Updated/.test(b.textContent), 'and the green line is gone');

  console.log(`${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
