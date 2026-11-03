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
    '<div id="ui-opxmon-body" hx-get="/opx-monitor/body?tab=cleanup" data-state="ok">' +
    '<p class="ui-opxmon-status ui-opxmon-status--ok"><span class="ui-opxmon-sentence">Updated 1 s ago.</span></p>' +
    '<button class="btn-sm ui-opxmon-close" data-qm-id="QM-x" data-ports="con1/2, con1/3" data-jobs="Running">Close</button>' +
    '<button class="btn-sm ui-opxmon-close-all" data-qm-ids="QM-x, QM-y">Close all</button>' +
    '<p class="ui-opxmon-close-result" hidden></p></div></body></html>',
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
  w.document.querySelector('.ui-opxmon-close').click();
  await tick();
  ok(w.document.querySelector('.ui-opxmon-confirm') && w.__asked.length === 0, 'D-25 inline confirmation replaces native confirm');
  ok(/QM-x/.test(w.document.querySelector('.ui-opxmon-confirm').textContent) && /con1\/2, con1\/3/.test(w.document.querySelector('.ui-opxmon-confirm').textContent) && /Running/.test(w.document.querySelector('.ui-opxmon-confirm').textContent),
     'the question names the QM, its FEMs and its jobs');
  ok(w.document.querySelectorAll('.ui-opxmon-confirm p').length === 2 && (w.document.querySelector('.ui-opxmon-confirm').textContent.match(/con1\/2/g) || []).length === 1, 'round 4 confirmation has one resource summary');
  ok(w.__posts.length === 0, 'declining sends nothing');

  w.document.querySelector('.ui-opxmon-confirm-cancel').click();
  w.__answer = true;
  w.document.querySelector('.ui-opxmon-close').click();
  w.document.querySelector('.ui-opxmon-confirm-accept').click();
  await tick();
  ok(w.__posts.length === 1 && w.__posts[0][0] === '/opx-monitor/close' && w.__posts[0][1].method === 'POST', 'accepting POSTs once');
  ok(w.__posts[0][1].body.get('qm_id') === 'QM-x' && !w.__posts[0][1].body.get('all'), 'the POST names that QM only');
  const res = w.document.querySelector('.ui-opxmon-close-result');
  ok(!res.hidden && /Closed: QM-x/.test(res.textContent), 'the result is shown');
  ok(!res.hidden, 'the action result stays visible');

  w = world();
  w.__reply = { ok: false, error: 'The server refused: busy' };
  w.document.querySelector('.ui-opxmon-close-all').click();
  await tick();
  ok(/ALL/.test(w.document.querySelector('.ui-opxmon-confirm').textContent) && /QM-x/.test(w.document.querySelector('.ui-opxmon-confirm').textContent), 'close-all names every QM');
  w.document.querySelector('.ui-opxmon-confirm-accept').click();
  ok(w.__posts.length === 0, 'D-25 wrong count sends nothing');
  w.document.querySelector('.ui-opxmon-confirm-count').value = '2';
  w.document.querySelector('.ui-opxmon-confirm-accept').click();
  await tick();
  ok(w.__posts[0][1].body.get('all') === '1', 'close-all POSTs all=1');
  ok(w.__posts[0][1].body.get('qm_ids') === 'QM-x, QM-y', 'close-all POSTs exactly the confirmed ids');
  const r2 = w.document.querySelector('.ui-opxmon-close-result');
  ok(/Not closed: The server refused: busy/.test(r2.textContent) && r2.classList.contains('ui-state--error'), 'a refusal is said as an error');

  w = world();
  const b = w.document.getElementById('ui-opxmon-body');
  w.document.dispatchEvent(new w.CustomEvent('htmx:sendError', { detail: { elt: b } }));
  ok(/SM did not answer/.test(b.querySelector('.ui-opxmon-sentence').textContent), 'an unanswered refresh is said');
  ok(b.querySelector('.ui-opxmon-status').classList.contains('ui-opxmon-status--error') && !/Updated/.test(b.textContent), 'and the green line is gone');

  w = world();
  const idle = w.document.createElement('button');
  idle.className = 'ui-opxmon-close-idle';
  idle.setAttribute('data-qm-ids', 'QM-x, QM-y');
  w.document.body.appendChild(idle);
  w.__reply = {ok: true, closed: ['QM-x'], skipped: ['QM-y'], skip_reasons: {'QM-y': 'active or unknown job status'}};
  idle.click();
  await tick();
  ok(/re-checked/.test(w.document.querySelector('.ui-opxmon-confirm').textContent) && /QM-x/.test(w.document.querySelector('.ui-opxmon-confirm').textContent) && /QM-y/.test(w.document.querySelector('.ui-opxmon-confirm').textContent), 'idle confirm names every candidate and the recheck');
  w.document.querySelector('.ui-opxmon-confirm-accept').click();
  await tick();
  ok(w.__posts[0][1].body.get('idle') === '1' && w.__posts[0][1].body.get('qm_ids') === 'QM-x, QM-y', 'idle POST names only confirmed ids');
  ok(/QM-y: active/.test(w.document.querySelector('.ui-opxmon-close-result').textContent), 'a newly active QM is reported as skipped');
  let healthWorld = new JSDOM('<div id="ui-opxmon-alarm-slot"><a class="ui-opxmon-health" data-health="green" data-valid-ms="6000">OPX healthy</a></div>', {runScripts: 'outside-only'}).window;
  const timers = [];
  healthWorld.setTimeout = (fn, ms) => { timers.push([fn, ms]); };
  healthWorld.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'opx-health.js'), 'utf8'));
  healthWorld.document.dispatchEvent(new healthWorld.CustomEvent('htmx:afterSwap'));
  ok(timers.length === 1 && timers[0][1] === 6000, 'health expiry is tied to the snapshot and cannot be extended by another swap');
  timers[0][0]();
  ok(healthWorld.document.querySelector('[data-health="grey"]') && /freshness expired/.test(healthWorld.document.body.textContent), 'D-14 health expires to grey without a server response');
  const dot = healthWorld.document.querySelector('.ui-opxmon-health');
  dot.classList.add('ui-opxmon-verdict--red');
  dot.setAttribute('data-health', 'green');
  healthWorld.document.dispatchEvent(new healthWorld.CustomEvent('htmx:sendError', {detail: {elt: healthWorld.document.getElementById('ui-opxmon-alarm-slot')}}));
  ok(dot.getAttribute('data-health') === 'grey' && /SM did not answer/.test(dot.textContent), 'D-14 a failed top-bar refresh immediately becomes unknown');
  ok(!dot.classList.contains('ui-opxmon-verdict--red'), 'expired health clears the red verdict modifier');
  const tile = w.document.createElement('details');
  tile.className = 'ui-opxmon-tile';
  w.document.body.appendChild(tile);
  tile.open = true;
  tile.dispatchEvent(new w.Event('toggle'));
  ok(tile.classList.contains('ui-opxmon-tile--open'), 'opening a tile applies the kit expansion modifier');
  tile.open = false;
  tile.dispatchEvent(new w.Event('toggle'));
  ok(!tile.classList.contains('ui-opxmon-tile--open'), 'closing a tile clears the kit expansion modifier');
  const savedResult = w.document.querySelector('.ui-opxmon-close-result');
  savedResult.textContent = '';
  w.document.dispatchEvent(new w.CustomEvent('htmx:afterSwap'));
  ok(/QM-y: active/.test(savedResult.textContent), 'D-13 skipped action result survives the cleanup refresh');
  if (process.argv[2]) {
    const pages = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    const doc = key => new JSDOM(pages[key]).window.document;
    ok(/Close idle QMs \(1\)/.test(doc('idle').body.textContent) && /since SM first saw it/.test(doc('idle').body.textContent), 'D-13 rendered idle count states its basis');
    ok(doc('unknown').querySelector('[data-health="grey"]') && /OPEN 1/.test(doc('unknown').body.textContent), 'round 4 unassessed health is muted while occupancy remains OPEN');
    ok(doc('stale').querySelector('[data-health="grey"]') && /fresh/.test(doc('stale').body.textContent), 'D-14 stale data is grey');
    ok(doc('trend').querySelectorAll('svg polyline').length === 2 && /actual span 100 s/.test(doc('trend').body.textContent), 'D-15 real markup has separate gap segments and actual span');
    ok(/2 runs at that time/.test(doc('ambiguous').body.textContent) && !Array.from(doc('ambiguous').querySelectorAll('a')).some(a => /probably/.test(a.textContent)), 'D-16 ambiguous candidates have no run link');
    ok(/probably #7/.test(doc('unique').body.textContent) && /estimated from times/.test(doc('unique').body.textContent), 'D-16 unique match is explicitly estimated');
    ok(/alarm_raise/.test(doc('alarm').body.textContent) && /threshold 50/.test(doc('alarm').body.textContent), 'D-17 history includes alarm value and threshold');
    ok(/physical slot has not been verified/.test(doc('mapping').body.textContent) && Array.from(doc('mapping').querySelectorAll('.ui-opxmon-tile')).some(td => td.textContent.includes('FEM 0') && td.title.includes('FEM 0')), 'D-18 mismatch renders raw sensor key and tooltip');
    ok(/not reported/.test(doc('mapping').body.textContent), 'D-18 zero reading is no sensor');
  }
  console.log(`${passes} passed, ${fails} failed`);
  process.exit(fails ? 1 : 0);
})();
