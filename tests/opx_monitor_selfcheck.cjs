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
  ok(w.document.querySelectorAll('.ui-opxmon-confirm .ui-facts').length === 1 && w.document.querySelectorAll('.ui-opxmon-confirm .ui-facts-row').length === 3 && (w.document.querySelector('.ui-opxmon-confirm').textContent.match(/con1\/2/g) || []).length === 1, 'round 4 confirmation has one resource summary');
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
  {
    // the periodic refresh re-draws the body: an open close question and an opened fold come back,
    // built from the fresh rows; a QM that left the list withdraws the question and says so
    const rowBody = (ports, withRow) => '<div id="ui-opxmon-body" hx-get="/opx-monitor/body?tab=cleanup" data-state="ok">' +
      '<details class="ui-zone ui-zone--fold ui-opxmon-danger" data-fold="danger"><summary class="ui-zone-head">Danger</summary><div class="ui-zone-body"></div></details>' +
      '<table class="ui-table"><tbody>' + (withRow ? '<tr><td class="ui-table-cell">QM-r</td><td class="ui-table-cell"><button class="btn-sm ui-opxmon-close" data-qm-id="QM-r" data-basis="idle for 3 min" data-ports="' + ports + '" data-last-job="j1 Completed">Close</button></td></tr>' : '') +
      '</tbody></table><p class="ui-opxmon-close-result" hidden></p></div>';
    const r = new JSDOM('<!doctype html><html><body>' + rowBody('con1/4', true) + '</body></html>', { url: 'http://localhost/opx-monitor/cleanup', runScripts: 'outside-only' }).window;
    r.fetch = () => Promise.resolve({ status: 200, json: () => Promise.resolve({ ok: true, closed: [] }) });
    r.htmx = { ajax: () => {} };
    r.eval(SRC);
    const swap = (html) => {
      const old = r.document.getElementById('ui-opxmon-body');
      r.document.dispatchEvent(new r.CustomEvent('htmx:beforeSwap', { detail: { target: old } }));
      old.outerHTML = html;
      r.document.dispatchEvent(new r.CustomEvent('htmx:afterSwap', { detail: { target: r.document.getElementById('ui-opxmon-body') } }));
    };
    const danger = r.document.querySelector('.ui-opxmon-danger');
    danger.open = true; danger.dispatchEvent(new r.Event('toggle'));
    r.document.querySelector('.ui-opxmon-close').click();
    ok(r.document.querySelector('tr.ui-table-sub .ui-opxmon-confirm.ui-callout--warn'), 'the close question sits under its row as a warning callout');
    const labels = [...r.document.querySelectorAll('.ui-opxmon-confirm .ui-facts-label')].map(x => x.textContent).join('|');
    ok(labels === 'State|FEMs|Last job' && /idle for 3 min/.test(r.document.querySelector('.ui-opxmon-confirm').textContent), 'the question lists state, FEMs and last job as label / value facts');
    swap(rowBody('con1/5', true));
    const c = r.document.querySelector('.ui-opxmon-confirm');
    ok(c && /con1\/5/.test(c.textContent) && !/con1\/4/.test(c.textContent), 'the close question survives a refresh, rebuilt from the fresh row');
    const changed = c.querySelector('[data-fact-updated="1"]');
    ok(changed && changed.querySelector('dt').textContent === 'FEMs' && /con1\/5/.test(changed.textContent) && /Updated just now/.test(changed.textContent) && c.querySelectorAll('[data-fact-updated]').length === 1, 'r2: only the changed fact carries Updated just now');
    swap(rowBody('con1/5', true));
    ok(!r.document.querySelector('[data-fact-updated]') && !/Updated just now/.test(r.document.querySelector('.ui-opxmon-confirm').textContent), 'r2: unchanged facts have no update cue');
    ok(r.document.querySelector('.ui-opxmon-danger').open, 'a fold the user opened stays open across a refresh');
    swap(rowBody('con1/5', false));
    ok(!r.document.querySelector('.ui-opxmon-confirm') && /withdrawn/.test(r.document.querySelector('.ui-opxmon-close-result').textContent), 'a QM gone from the list withdraws the question and says so');
  }
  {
    // consent for close-all / close-idle is bound to the id set the user saw: a refresh with a
    // different set drops the question and the typed count and says why; the same set keeps both;
    // a fresh button that is disabled never re-opens the question
    const setBody = (cls, ids, disabled) => '<div id="ui-opxmon-body" hx-get="/opx-monitor/body?tab=cleanup" data-state="ok">' +
      '<details class="ui-zone ui-zone--fold" data-fold="danger"><summary class="ui-zone-head">Danger</summary><div class="ui-zone-body">' +
      '<p><button class="btn-sm ' + cls + '" data-qm-ids="' + ids + '"' + (disabled ? ' disabled' : '') + '>Go</button></p></div></details>' +
      '<p class="ui-opxmon-close-result" hidden></p></div>';
    const mk = (cls, ids) => {
      const v = new JSDOM('<!doctype html><html><body>' + setBody(cls, ids, false) + '</body></html>', { url: 'http://localhost/opx-monitor/cleanup', runScripts: 'outside-only' }).window;
      v.__posts = [];
      v.fetch = (u, o) => { v.__posts.push(o); return Promise.resolve({ status: 200, json: () => Promise.resolve({ ok: true, closed: [] }) }); };
      v.htmx = { ajax: () => {} };
      v.eval(SRC);
      v.swap = (html) => {
        const old = v.document.getElementById('ui-opxmon-body');
        v.document.dispatchEvent(new v.CustomEvent('htmx:beforeSwap', { detail: { target: old } }));
        old.outerHTML = html;
        v.document.dispatchEvent(new v.CustomEvent('htmx:afterSwap', { detail: { target: v.document.getElementById('ui-opxmon-body') } }));
      };
      return v;
    };
    for (const cls of ['ui-opxmon-close-all', 'ui-opxmon-close-idle']) {
      const tag = cls.endsWith('all') ? 'close-all' : 'close-idle';
      let v = mk(cls, 'QM-a, QM-b');
      v.document.querySelector('.' + cls).click();
      const t0 = v.document.querySelector('.ui-opxmon-confirm-count');
      if (t0) { t0.value = '2'; t0.dispatchEvent(new v.Event('input')); }
      v.swap(setBody(cls, 'QM-b, QM-a', false));
      const kept = v.document.querySelector('.ui-opxmon-confirm');
      ok(kept && (!t0 || v.document.querySelector('.ui-opxmon-confirm-count').value === '2'), tag + ': a refresh with the same id set keeps the question and the typed count');
      v.swap(setBody(cls, 'QM-a, QM-c', false));
      ok(!v.document.querySelector('.ui-opxmon-confirm') && /The list changed; check it again/.test(v.document.querySelector('.ui-opxmon-close-result').textContent), tag + ': a refresh that swaps an id drops the question and says the list changed');
      v.document.querySelector('.' + cls).click();
      const t1 = v.document.querySelector('.ui-opxmon-confirm-count');
      ok(!t1 || t1.value === '', tag + ': the typed count does not carry over to the new set');
      v.swap(setBody(cls, 'QM-a, QM-c, QM-d', false));
      ok(!v.document.querySelector('.ui-opxmon-confirm'), tag + ': a refresh that adds an id drops the question');
      v = mk(cls, 'QM-a, QM-b');
      v.document.querySelector('.' + cls).click();
      v.swap(setBody(cls, 'QM-a, QM-b', true));
      ok(!v.document.querySelector('.ui-opxmon-confirm') && /withdrawn/.test(v.document.querySelector('.ui-opxmon-close-result').textContent), tag + ': a disabled fresh button never re-opens the question');
      v = mk(cls, 'QM-a, QM-b');
      v.document.querySelector('.' + cls).click();
      v.swap(setBody(cls, 'QM-a, QM-b', false));
      const t2 = v.document.querySelector('.ui-opxmon-confirm-count');
      if (t2) t2.value = '2';
      v.document.querySelector('.ui-opxmon-confirm-accept').click();
      ok(v.__posts.length === 1 && v.__posts[0].body.get('qm_ids') === 'QM-a, QM-b', tag + ': after a same-set refresh the POST carries the ids the user saw');
    }
  }
  if (process.argv[2]) {
    const pages = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    const doc = key => new JSDOM(pages[key]).window.document;
    if (pages.owner_cleanup) {
      // owner cells and the PC names table, from markup the server rendered
      const jobs = doc('owner_jobs');
      const kind = id => { const row = jobs.querySelector('tr[data-qm-id="' + id + '"]'); const o = row && row.querySelector('.ui-opxmon-owner'); return o ? o.getAttribute('data-owner-kind') : null; };
      ok(kind('QM-none') === 'none' && kind('QM-one') === 'one' && kind('QM-v2') === 'unreadable' && kind('QM-two') === 'several' && kind('QM-unread') === 'unread', 'owner cell kind per QM');
      const one = jobs.querySelector('tr[data-qm-id="QM-one"] .ui-opxmon-owner');
      ok(/ab12cd34/.test(one.textContent) && one.querySelector('.ui-opxmon-this-pc') && /qubit_spec/.test(one.textContent), 'one marker shows tag, this PC chip and node');
      ok(jobs.querySelectorAll('tr[data-qm-id="QM-two"] .ui-opxmon-owner-marker').length === 2 && jobs.querySelector('tr[data-qm-id="QM-two"] .ui-opxmon-several'), 'several markers: every one shown and flagged');
      ok((jobs.body.textContent.match(/the server does not check it/g) || []).length === 1, 'the claim note is said once per table');
      const set = doc('owner_settings');
      ok(set.querySelector('table[aria-label="PC names"] tr[data-tag="ab12cd34"] input[name="label__ab12cd34"]') && set.querySelector('input[name="new_tag"]'), 'settings table offers seen tags and a new row');
      // the confirm panel carries the owner of each listed QM
      const cw = new JSDOM(pages.owner_cleanup, { url: 'http://localhost/opx-monitor/cleanup', runScripts: 'outside-only' }).window;
      cw.fetch = () => Promise.resolve({ status: 200, json: () => Promise.resolve({ ok: true, closed: [] }) });
      cw.htmx = { ajax: () => {} };
      cw.eval(SRC);
      cw.document.querySelector('.ui-opxmon-close[data-qm-id="QM-two"]').click();
      const panel = cw.document.querySelector('.ui-opxmon-confirm');
      ok(panel && /several markers/.test(panel.textContent) && /QM-two/.test(panel.textContent) && cw.document.querySelector('.ui-opxmon-confirm-accept'), 'confirm names the QM and its owner markers and still asks');
      console.log(`${passes} passed, ${fails} failed`);
      process.exit(fails ? 1 : 0);
    }
    ok(/Close idle QMs \(1\)/.test(doc('idle').body.textContent) && /while SM watched continuously/.test(doc('idle').body.textContent), 'D-13 rendered idle count states its basis');
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
