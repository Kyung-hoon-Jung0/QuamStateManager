/* QA F6 -- Datasets > Trends froze the page for 4-6 s when an experiment was
 * chosen, with the old "Select an experiment" placeholder still on screen.
 * P3 (design ram_design.md §2b) then moved the drawing out of the fragment:
 * the shell's inline script mounts window.DatasetTrends (app.js), which
 * fetches /trends/series and draws from it.
 *
 * Drives the SHIPPED shell (templates/_trends_data.html) and app.js under
 * jsdom (tests/trends_view_harness.cjs). Pinned:
 *   1. one chart per TASK. The old loop drew all twelve eager charts in one
 *      task (every _plotlyRender promise resolves in the same microtask
 *      checkpoint) -- that was the multi-second long task. Now one chart is
 *      drawn in the task the data lands in and the rest arrive in later
 *      macrotasks.
 *   2. IntersectionObserver bursts go through the same queue.
 *   3. a queue left over from a swapped-out view never draws into the next
 *      view's same-id divs, and stops; an answer that lands after the user
 *      picked again draws nothing.
 *   4. loadTrendData makes #trends-content the request's SOURCE, so the real
 *      bundled htmx marks it .htmx-request (the "Loading trends..." state)
 *      for the request's whole life -- also behind a queued second pick --
 *      and clears it on success AND on failure.
 * Run: node tests/trends_render_queue_selfcheck.cjs (driven by
 * tests/test_trends_render_queue.py). Exit 0 ok, 1 fail, 2 no jsdom.
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const H = require('./trends_view_harness.cjs');

const STATIC = H.STATIC;
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = H.tick;
// A rejected request the page leaves unhandled is a console error in a
// browser, not a crash -- let the aria-busy assertion be what fails.
process.on('unhandledRejection', () => {});
const until = H.until;

(async () => {
  // 1. one chart per task
  {
    const W = H.world();
    W.answers.push({ body: H.payload(3, H.seriesOf('A', 12, 3)) });
    W.mount();
    await H.microtasks();
    ok(W.draws.length === 1, 'one chart is drawn synchronously, not all twelve in one task (drew ' + W.draws.length + ')');
    await until(() => W.draws.length >= 12);
    ok(W.draws.length === 12, 'the other eleven arrive in later tasks (' + W.draws.length + ')');
    const names = W.draws.map((c) => c.name.split(' / ')[1]);
    ok(names.join(',') === 'A0,A1,A2,A3,A4,A5,A6,A7,A8,A9,A10,A11', 'in order, each once (' + names.join(',') + ')');
  }

  // 2. the IntersectionObserver path is queued too
  {
    const W = H.world();
    W.answers.push({ body: H.payload(3, H.seriesOf('B', 20, 3)) });
    W.mount();
    await until(() => W.draws.length >= 12);
    ok(W.draws.length === 12 && W.ios.length === 1 && W.ios[0].targets.length === 8,
       'twelve eager charts, the other eight observed (' + W.draws.length + ', ' + (W.ios[0] && W.ios[0].targets.length) + ')');
    const io = W.ios[0];
    io.cb(io.targets.map((t) => ({ isIntersecting: true, target: t })));   // a fast scroll: all at once
    ok(W.draws.length === 13, 'an observer burst draws ONE chart in this task (' + (W.draws.length - 12) + ')');
    await until(() => W.draws.length >= 20);
    ok(W.draws.length === 20, 'and the rest one task at a time (' + W.draws.length + ')');
  }

  // 3. a swapped-out view's queue never reaches the next view
  {
    const W = H.world();
    W.answers.push({ body: H.payload(3, H.seriesOf('OLD', 12, 3)) });
    W.mount('old');
    await H.microtasks();                      // one OLD drawn, eleven queued
    W.answers.push({ body: H.payload(3, H.seriesOf('NEW', 12, 3)) });
    W.mount('new');                            // the user picks another experiment: same ids
    await until(() => W.draws.filter((c) => /NEW/.test(c.name)).length >= 12);
    await tick(60);
    const intoLive = W.draws.filter((c) => c.el.isConnected && /OLD/.test(c.name));
    ok(intoLive.length === 0, 'no OLD series is drawn into the NEW view (' + intoLive.length + ')');
    const newDrawn = W.draws.filter((c) => /NEW/.test(c.name)).length;
    ok(newDrawn === 12, 'every NEW chart is drawn (' + newDrawn + ')');
    const oldAfter = W.draws.filter((c) => /OLD/.test(c.name)).length;
    ok(oldAfter <= 1, 'the OLD queue stops once its boxes are gone (' + oldAfter + ' OLD draws)');
  }
  {
    // an answer that lands after the next pick draws nothing
    const W = H.world();
    let release;
    W.w.fetch = function () {
      return new Promise((r) => { release = () => r({ ok: true, status: 200,
        json: () => Promise.resolve(H.payload(3, H.seriesOf('LATE', 2, 3))) }); });
    };
    const left = W.mount('late');
    W.w.document.getElementById('trends-content').innerHTML = '<p>another pick</p>';
    release();
    await tick(60);
    ok(W.draws.length === 0, 'an answer for a view the user already left draws nothing (' + W.draws.length + ')');
    ok(left.querySelectorAll('.trend-chart-box').length === 0 && left.querySelector('[data-role="loading"]'),
       'and builds nothing into it (' + left.querySelectorAll('.trend-chart-box').length + ' boxes)');
  }

  // 4. loadTrendData says it is loading, for the request's whole life. The box
  //    it fills is the request's SOURCE, so the REAL bundled htmx marks it
  //    .htmx-request -- including while a second pick waits QUEUED behind the
  //    first (a promise-based flag, the first try, was cleared at once there:
  //    the queued call's promise resolves immediately; seen in real Chrome)
  //    -- and clears it on success and on a network error.
  {
    const HTMX_SRC = fs.readFileSync(path.join(STATIC, 'htmx.min.js'), 'utf8');
    // htmx scans hx-on: through XPathEvaluator, which jsdom cannot execute
    // (settle_config_selfcheck.cjs); this fixture has no hx-on attributes.
    const SHIMS =
      'window.XPathEvaluator = function () {};' +
      'window.XPathEvaluator.prototype.createExpression = function () {' +
      '  return { evaluate: function () { return { iterateNext: function () { return null; } }; } }; };' +
      // a controllable XHR: the test answers each request by hand
      'window.__xhrs = [];' +
      'window.XMLHttpRequest = function () { this.readyState = 0; this.status = 0; this.response = "";' +
      '  this.responseText = ""; this.responseURL = ""; this._l = {}; this.upload = { addEventListener: function () {} };' +
      '  window.__xhrs.push(this); };' +
      'var P = window.XMLHttpRequest.prototype;' +
      'P.open = function (m, u) { this.method = m; this.url = u; this.readyState = 1; };' +
      'P.setRequestHeader = function () {}; P.overrideMimeType = function () {};' +
      'P.addEventListener = function (t, f) { (this._l[t] = this._l[t] || []).push(f); };' +
      'P.getAllResponseHeaders = function () { return ""; }; P.getResponseHeader = function () { return null; };' +
      'P.send = function () { this.sent = true; };' +
      'P.abort = function () { this.aborted = true; if (this.onabort) this.onabort(); };' +
      'P._respond = function (body) { this.readyState = 4; this.status = 200; this.response = this.responseText = body;' +
      '  this.responseURL = "http://localhost" + this.url; if (this.onload) this.onload(); };' +
      'P._fail = function () { this.readyState = 4; if (this.onerror) this.onerror(); };' +
      'window.IntersectionObserver = window.ResizeObserver = function () {' +
      '  this.observe = this.unobserve = this.disconnect = function () {}; };' +
      'window.fetch = function () { return new Promise(function () {}); };';
    const dom = new JSDOM('<!doctype html><html><head>' +
      '<script>' + SHIMS + '</scr' + 'ipt><script>' + HTMX_SRC + '</scr' + 'ipt></head><body>' +
      '<select id="trend-exp-select"><option value="01_time_of_flight">01</option>' +
      '<option value="08_qubit_spectroscopy" selected>08</option></select>' +
      '<select id="trend-qubit-select"><option value="" selected>All</option></select>' +
      '<div id="trends-content"><p class="section-placeholder">Select an experiment type to view trends.</p></div>' +
      '</body></html>', { url: 'http://localhost/trends', runScripts: 'dangerously', pretendToBeVisual: true });
    const w = dom.window;
    await new Promise((resolve) => {
      if (w.document.readyState !== 'loading') resolve();
      else w.document.addEventListener('DOMContentLoaded', () => w.setTimeout(resolve, 0));
    });
    w.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
    w.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));
    const box = w.document.getElementById('trends-content');
    const busy = () => box.classList.contains('htmx-request');
    const trendReqs = () => w.__xhrs.filter((x) => /\/trends\/data/.test(x.url || ''));

    w.loadTrendData();
    await tick(5);
    ok(trendReqs().length === 1 && /experiment=08_qubit_spectroscopy/.test(trendReqs()[0].url),
       'fixture: the request went out (' + (trendReqs()[0] && trendReqs()[0].url) + ')');
    ok(busy(), 'while it runs #trends-content is marked .htmx-request (the loading state)');
    trendReqs()[0]._respond('<div class="trends-section">PAGE-08</div>');
    await tick(60);
    ok(!busy() && /PAGE-08/.test(box.textContent), 'the answer lands and the loading state ends');

    // a second pick while one is in flight: htmx QUEUES it
    const sel = w.document.getElementById('trend-exp-select');
    sel.value = '01_time_of_flight'; w.loadTrendData();
    sel.value = '08_qubit_spectroscopy'; w.loadTrendData();
    await tick(5);
    ok(busy(), 'two quick picks: loading');
    ok(trendReqs().length === 2, 'the second pick waits queued behind the first (' + trendReqs().length + ' sent)');
    trendReqs()[1]._respond('<div class="trends-section">PAGE-01</div>');
    await tick(60);
    ok(trendReqs().length === 3, 'the queued pick goes out once the first lands (' + trendReqs().length + ')');
    ok(busy(), 'and the box still says loading while it runs');
    trendReqs()[2]._respond('<div class="trends-section">PAGE-08b</div>');
    await tick(60);
    ok(!busy() && /PAGE-08b/.test(box.textContent), 'the last pick lands, loading ends');

    w.loadTrendData();
    await tick(5);
    ok(busy(), 'the next request marks it again');
    trendReqs()[3]._fail();
    await tick(60);
    ok(!busy(), 'a FAILED request clears it too (no forever-spinner)');
  }

  if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
  console.log('ALL OK');
  process.exit(0);
})().catch((e) => { console.error('selfcheck crashed: ' + (e && e.stack || e)); process.exit(1); });
