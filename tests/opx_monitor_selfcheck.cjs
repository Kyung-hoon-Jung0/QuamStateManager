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
  {
    // a refusal that already says "Nothing was closed." is shown as one sentence, never "Not closed: ... Nothing was closed."
    const s = world();
    const b2 = s.document.createElement('button');
    b2.className = 'ui-opxmon-close-idle';
    b2.setAttribute('data-qm-ids', 'QM-x');
    s.document.body.appendChild(b2);
    s.__reply = {ok: false, error: 'The status is not fresh; wait for the next refresh. Nothing was closed.'};
    b2.click();
    await tick();
    s.document.querySelector('.ui-opxmon-confirm-accept').click();
    await tick();
    const out = s.document.querySelector('.ui-opxmon-close-result');
    ok(out.textContent === 'The status is not fresh; wait for the next refresh. Nothing was closed.' && out.classList.contains('ui-state--error'), 'a stale close-idle refusal reads as one sentence: ' + JSON.stringify(out.textContent));
  }
  let healthWorld = new JSDOM('<div id="ui-opxmon-alarm-slot"><a class="ui-opxmon-health" data-health="green" data-valid-ms="6000">OPX healthy</a></div>', {runScripts: 'outside-only'}).window;
  const timers = [];
  healthWorld.setTimeout = (fn, ms) => { timers.push([fn, ms]); };
  healthWorld.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'opx-health.js'), 'utf8'));
  healthWorld.document.dispatchEvent(new healthWorld.CustomEvent('htmx:afterSwap'));
  const monitorBody = healthWorld.document.createElement('div'); monitorBody.id = 'ui-opxmon-body';
  healthWorld.document.body.append(monitorBody);
  const badgeRequest = new healthWorld.CustomEvent('htmx:beforeRequest', {cancelable: true,
    detail: {elt: healthWorld.document.getElementById('ui-opxmon-alarm-slot')}});
  healthWorld.document.dispatchEvent(badgeRequest);
  ok(badgeRequest.defaultPrevented, 'a monitor snapshot owns the badge, suppressing its independent poll');
  const lateBadge = new healthWorld.CustomEvent('htmx:beforeOnLoad', {cancelable: true,
    detail: {elt: healthWorld.document.getElementById('ui-opxmon-alarm-slot'), xhr: {responseText: 'OPX free'}}});
  healthWorld.document.dispatchEvent(lateBadge);
  ok(lateBadge.defaultPrevented, 'an older badge request already in flight cannot overwrite the monitor snapshot');
  monitorBody.remove();
  ok(timers.length === 1 && timers[0][1] === 6000, 'health expiry is tied to the snapshot and cannot be extended by another swap');
  timers[0][0]();
  const expired = healthWorld.document.querySelector('.ui-opxmon-health');
  ok(healthWorld.document.querySelector('[data-health="grey"]') && /freshness expired/.test(expired.title)
     && / OPX unknown$/.test(expired.textContent),
     'D-14 health expires to grey without a server response; the badge stays short, the reason is its tooltip');
  const shared = new JSDOM('<div id="ui-opxmon-alarm-slot">old badge</div>' +
    '<div id="ui-opxmon-body" data-tab="settings"><div id="ui-opxmon-band" ' +
    'hx-get="/opx-monitor/body?tab=settings" hx-select="#ui-opxmon-band">FREE</div>' +
    '<input id="draft" value="unsaved"></div>', {runScripts: 'outside-only'}).window;
  shared.htmx = {process() {}};
  shared.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager/web/static/opx-health.js'), 'utf8'));
  shared.OpxHealth.receive({id: '2', live: '<div id="ui-opxmon-body" data-tab="live">' +
    '<div id="ui-opxmon-band">BUSY</div><div class="cards">1 QM</div></div>', badge: 'OPX busy'});
  ok(shared.document.querySelector('#ui-opxmon-band').textContent === 'BUSY' &&
    shared.document.querySelector('#ui-opxmon-alarm-slot').textContent === 'OPX busy' &&
    shared.document.querySelector('#draft').value === 'unsaved',
    'a shared snapshot updates Settings and its badge without replacing the form');
  ok(shared.document.querySelector('#ui-opxmon-band').getAttribute('hx-select') === '#ui-opxmon-band',
    'shared Settings band keeps its poll selection');
  shared.OpxHealth.receive({id: '1', live: '<div id="ui-opxmon-body">FREE</div>', badge: 'OPX free'});
  ok(shared.document.querySelector('#ui-opxmon-band').textContent === 'BUSY',
    'a delayed snapshot cannot roll the page back');
  shared.document.querySelector('#ui-opxmon-body').dataset.tab = 'live';
  shared.OpxHealth.receive({id: '3', live: '<div id="ui-opxmon-body" data-tab="live">' +
    '<div id="ui-opxmon-band">FREE</div><div class="cards">0 QM</div></div>', badge: 'OPX free'});
  ok(shared.document.querySelector('.cards').textContent === '0 QM' &&
    shared.document.querySelector('#ui-opxmon-band').textContent === 'FREE' &&
    shared.document.querySelector('#ui-opxmon-alarm-slot').textContent === 'OPX free',
    'a shared snapshot updates Live cards, band and badge together');
  shared.close();
  const dot = healthWorld.document.querySelector('.ui-opxmon-health');
  dot.classList.add('ui-opxmon-verdict--red');
  dot.setAttribute('data-health', 'green');
  healthWorld.document.dispatchEvent(new healthWorld.CustomEvent('htmx:sendError', {detail: {elt: healthWorld.document.getElementById('ui-opxmon-alarm-slot')}}));
  ok(dot.getAttribute('data-health') === 'grey' && /SM did not answer/.test(dot.title)
     && / OPX unknown$/.test(dot.textContent), 'D-14 a failed top-bar refresh immediately becomes unknown');
  ok(!dot.classList.contains('ui-opxmon-verdict--red'), 'expired health clears the red verdict modifier');
  {
    const bw = new JSDOM('<div id="ui-opxmon-body" data-tab="settings"><div id="ui-opxmon-band" class="ui-verdict ui-opxmon-verdict"'
      + ' data-valid-ms="0"><strong class="ui-verdict-key">BUSY</strong><span class="ui-verdict-why">x</span></div></div>',
      {runScripts: 'outside-only'}).window;
    bw.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'opx-health.js'), 'utf8'));
    bw.document.dispatchEvent(new bw.CustomEvent('htmx:sendError', {detail: {elt: bw.document.getElementById('ui-opxmon-band')}}));
    ok(bw.document.querySelector('.ui-verdict-key').textContent === 'UNKNOWN'
       && /SM did not answer/.test(bw.document.querySelector('.ui-verdict-why').textContent),
       'a failed refresh of the settings tab band alone becomes unknown');
  }
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
    ok(changed && changed.querySelector('dt').textContent === 'FEMs' && /con1\/5/.test(changed.textContent) && /Updated just now/.test(changed.textContent) && c.querySelectorAll('[data-fact-updated]').length === 1, 'only the changed fact carries Updated just now');
    swap(rowBody('con1/5', true));
    ok(!r.document.querySelector('[data-fact-updated]') && !/Updated just now/.test(r.document.querySelector('.ui-opxmon-confirm').textContent), 'unchanged facts have no update cue');
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
  {
    // Exercise the PC names form with real htmx and the app's complete 4xx swap listener.
    const app = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager/web/static/app.js'), 'utf8');
    const start = app.indexOf("document.addEventListener('htmx:beforeSwap', function(evt) {");
    const end = app.indexOf('\n});', start) + 4;
    if (start < 0 || end < 4) throw new Error('beforeSwap listener missing');
    const v = new JSDOM('<!doctype html><html><body><div id="ui-opxmon-body">' +
      '<form hx-post="/opx-monitor/pc-names" hx-target="#ui-opxmon-body" hx-swap="outerHTML">' +
      '<input name="label__abc" value="Edited"><button type="submit">Save</button></form>' +
      '</div></body></html>', {url: 'http://localhost/opx-monitor/settings', runScripts: 'outside-only'}).window;
    // jsdom's XPath needs its optional result type supplied for htmx's startup scan.
    const evaluate = v.XPathExpression.prototype.evaluate;
    v.XPathExpression.prototype.evaluate = function (context, type, result) {
      return evaluate.call(this, context, type || v.XPathResult.ANY_TYPE, result || null);
    };
    const requests = [];
    class ReplyXHR extends v.EventTarget {
      constructor() { super(); this.upload = new v.EventTarget(); this.headers = {}; }
      open(method, url) { this.method = method; this.url = url; }
      overrideMimeType() {}
      setRequestHeader(key, value) { this.headers[key] = value; }
      getAllResponseHeaders() { return 'Content-Type: text/html\r\n'; }
      getResponseHeader() { return null; }
      send(body) {
        requests.push({method: this.method, url: this.url, body});
        this.status = 409;
        this.responseURL = 'http://localhost/opx-monitor/pc-names';
        this.response = this.responseText = '<div id="ui-opxmon-body"><p class="ui-state ui-state--error" role="status">' +
          'Another window changed the name of tag abc; the current names are shown below. Type your change again. Nothing was saved.</p></div>';
        setTimeout(() => this.onload(), 0);
      }
      abort() {}
    }
    v.XMLHttpRequest = ReplyXHR;
    v.eval(app.slice(start, end));
    v.eval(SRC);
    v.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager/web/static/htmx.min.js'), 'utf8'));
    await tick();
    v.htmx.process(v.document.body);
    v.document.querySelector('form').dispatchEvent(new v.Event('submit', {bubbles: true, cancelable: true}));
    await tick();
    await tick();
    const flash = v.document.querySelector('#ui-opxmon-body .ui-state--error');
    ok(requests.length === 1 && requests[0].method === 'POST' && /label__abc=Edited/.test(requests[0].body), 'PC names form posts its edited row');
    ok(flash && !flash.hidden && /the current names are shown below/.test(flash.textContent), 'a 409 PC names reply leaves its error flash visible');
    const monitorBody = '<div id="ui-opxmon-body"><p class="ui-state ui-state--error">Refused.</p></div>';
    for (const id of ['ui-opxmon-body', 'unrelated-target', 'table-pane']) {
      for (const status of [400, 401, 403, 404, 409, 422, 500]) {
        const target = v.document.createElement('div'); target.id = id;
        const detail = {target, xhr: {status, responseText: monitorBody}, shouldSwap: false, isError: true};
        v.document.dispatchEvent(new v.CustomEvent('htmx:beforeSwap', {detail}));
        const allowed = id === 'ui-opxmon-body' && [400, 409].includes(status);
        ok(detail.shouldSwap === allowed && detail.isError === !allowed, id + ': HTTP ' + status + ' swap stays scoped');
      }
    }
    for (const status of [400, 409]) {
      for (const body of ['Bad Request', '<html><body><h1>Bad Request</h1></body></html>', '']) {
        const target = v.document.createElement('div'); target.id = 'ui-opxmon-body';
        const detail = {target, xhr: {status, responseText: body}, shouldSwap: false, isError: true};
        v.document.dispatchEvent(new v.CustomEvent('htmx:beforeSwap', {detail}));
        ok(!detail.shouldSwap && detail.isError, 'ui-opxmon-body: an HTTP ' + status + ' body that is not the monitor body never swaps (' + JSON.stringify(body.slice(0, 12)) + ')');
      }
    }
    v.close();
  }
  if (process.argv[2]) {
    const pages = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    const doc = key => new JSDOM(pages[key]).window.document;
    if (pages.flash_names) {
      // a refusal or a result is drawn right after the Save button of the form it is about
      const after = (key, formCls, label) => {
        const d = doc(key);
        const flashes = d.querySelectorAll('#ui-opxmon-body .ui-state[role="status"]');
        const btn = [...d.querySelectorAll('form.' + formCls + ' button[type="submit"]')].find(x => x.textContent.trim() === label);
        return flashes.length === 1 && btn && btn.nextElementSibling === flashes[0] ? flashes[0] : null;
      };
      const refused = after('flash_names', 'ui-opxmon-pcnames', 'Save PC names');
      ok(refused && refused.classList.contains('ui-state--error') && /A tag uses only/.test(refused.textContent), 'the PC names refusal sits right after Save PC names');
      ok(after('flash_names_ok', 'ui-opxmon-pcnames', 'Save PC names'), 'the PC names result sits right after Save PC names');
      const set = after('flash_settings', 'ui-opxmon-settings', 'Save');
      ok(set && /Port must be a whole number/.test(set.textContent), 'the Settings refusal sits right after the Settings Save button');
      console.log(`${passes} passed, ${fails} failed`);
      process.exit(fails ? 1 : 0);
    }
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
