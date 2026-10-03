/* project-time.js -- the project time zone and the run-clock question
 * (docs/263). Drives the SHIPPED script (with the shipped app.js for
 * SnapTime) under jsdom.
 * Pinned:
 *   P1  zone math: DST-aware offsets, half-hour zones, English offset text;
 *   P2  search: by city, by region words, by offset ("+9", "UTC-7");
 *   P3  the landing renders the saved zone BEFORE the clock status answers
 *       (the status is fetched after the render, once);
 *   P4  the picker follows the env picker's project (sm-landing-project);
 *   P5  Change... opens a searchable list (zone, offset, now);
 *   P6  a pick that differs from the PC's zone asks first; "view" saves with
 *       os_answer=view, then the watch check ("Now HH:MM in <zone>") with the
 *       time-sync line; the page's zone, the card and the old per-browser
 *       zone follow at once;
 *   P7  a pick in the PC's own offset asks nothing, then the watch check;
 *   P8  Cancel saves nothing;
 *   P9  "the PC's zone is wrong" saves pc_wrong and says how to fix it; the
 *       watch check's "No" is stored and says how to sync;
 *   P10 a pre-docs/263 per-browser zone is shown and offered first;
 *   P11 the run-clock question goes up ONCE with its evidence, is marked
 *       shown, and the answer is posted with the skew it was asked about;
 *   P12 not auto: no popup; Diagnostics' Answer... opens it;
 *   P13 a 409 (the skew moved) asks again;
 *   P14 no project open: no status fetch;
 *   P15 run times: the instant in the viewer's zone, recorded clock in the
 *       title; a correction is labelled and keeps the recorded time visible;
 *       a zone change re-renders;
 *   P16 SnapTime: the project zone on <html> beats the old per-browser one.
 * Exit 0 ok, 1 fail, 2 no jsdom.
 */
'use strict';
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const fs = require('fs');
const path = require('path');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
const PT = fs.readFileSync(path.join(STATIC, 'project-time.js'), 'utf8');
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
async function until(f, ms) { const t0 = Date.now(); while (!f() && Date.now() - t0 < (ms || 2000)) await tick(5); return f(); }

const JULY = Date.UTC(2026, 6, 15, 3, 0, 0);        // 2026-07-15 03:00 UTC: LA 20:00 (UTC-7), Seoul 12:00

const LANDING =
  '<div class="landing-card"><span class="landing-card-tz" data-tz-card="alpha">tz Seoul UTC+9</span>' +
  '<div class="landing-card-env" data-project="alpha"></div></div>' +
  '<div class="landing-card"><span class="landing-card-tz" data-tz-card="beta">tz Seoul UTC+9</span>' +
  '<div class="landing-card-env" data-project="beta"></div></div>' +
  '<section id="landing-tz" data-tz-default="alpha"><script type="application/json" data-tz-views>VIEWS</script>' +
  '<strong>Time zone for <span data-tz-project></span></strong><span data-tz-current></span>' +
  '<span data-tz-state></span><button type="button" data-tz-change>Change</button><span data-tz-clock></span>' +
  '<p data-tz-note hidden></p><div data-tz-search hidden><input type="search" data-tz-input>' +
  '<div data-tz-list></div></div></section>';

const VIEWS_PICKED = {
  alpha: { project: 'alpha', zone: 'Asia/Seoul', state: 'picked', offset: '+09:00', offset_text: 'UTC+9', note: null },
  beta: { project: 'beta', zone: 'Asia/Seoul', state: 'suggested', from_project: 'alpha', offset: '+09:00', offset_text: 'UTC+9', note: null },
};

function world(opts) {
  opts = opts || {};
  const body = opts.body != null ? opts.body
    : LANDING.replace('VIEWS', JSON.stringify(opts.views || VIEWS_PICKED));
  const html = '<!doctype html><html ' + (opts.htmlAttrs || '') + '><head></head><body>' + body + '</body></html>';
  const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1/' });
  const w = dom.window;
  const store = Object.assign({}, opts.storage || {});
  Object.defineProperty(w, 'localStorage', { configurable: true, value: {
    getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; } } });
  const log = [];
  const hold = {};
  const J = (o, status) => Promise.resolve({ ok: (status || 200) < 400, status: status || 200, json: () => Promise.resolve(o) });
  w.fetch = function (url, init) {
    const method = (init && init.method) || 'GET';
    const bodyStr = (init && init.body) || '';
    const form = {};
    String(bodyStr).split('&').filter(Boolean).forEach((kv) => {
      const i = kv.indexOf('='); form[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1));
    });
    log.push({ url, method, form });
    if (url.indexOf('/project-time/clock') === 0) {
      const ans = { os_zone: { utc_offset: opts.osOff || '+09:00', iana: opts.osIana || null },
                    ntp: { synced: true, source: 'time.windows.com' },
                    ntp_text: 'Time sync: on (time.windows.com)', now_ms: opts.nowMs || JULY };
      if (opts.slowClock) return new Promise((r) => { hold.clock = () => r({ ok: true, status: 200, json: () => Promise.resolve(ans) }); });
      return J(ans);
    }
    if (url === '/project-time/zone') {
      return J({ ok: true, view: { project: form.project, zone: form.zone, state: 'picked' },
                 display: { zone: form.zone, project: form.project, state: 'picked' } });
    }
    if (url === '/project-time/watch') return J({ ok: true });
    if (url === '/project-time/skew-shown') return J({ ok: true });
    if (url === '/project-time/skew-answer') {
      if (opts.answer409 && !hold.answered) { hold.answered = true; return J({ ok: false, changed: true }, 409); }
      return J({ ok: true, clock: {} });
    }
    if (url.indexOf('/project-time/status') === 0) {
      const n = log.filter((x) => x.url.indexOf('/project-time/status') === 0).length;
      return J(typeof opts.status === 'function' ? opts.status(n, log) : (opts.status || { project: null }));
    }
    return J({});
  };
  if (opts.app !== false) new w.Function(APP).call(w);
  new w.Function(PT).call(w);
  return { w, d: w.document, log, store, hold };
}
async function mk(opts) { const W = world(opts); await until(() => W.d.readyState === 'complete'); await tick(5); return W; }
const posts = (W, url) => W.log.filter((x) => x.url === url && x.method === 'POST');
const btn = (doc, sel) => doc.querySelector(sel);

(async function main() {
  // P1 -- zone math
  {
    const { w } = await mk({ body: '' });
    const P = w.ProjectTime;
    ok(P.zones().indexOf('UTC') >= 0 && P.zones().indexOf('Asia/Seoul') >= 0, 'P1: the list carries UTC and IANA zones');
    ok(P.offsetMinutes('America/New_York', new Date(Date.UTC(2026, 0, 15))) === -300, 'P1: New York is UTC-5 in January');
    ok(P.offsetMinutes('America/New_York', new Date(JULY)) === -240, 'P1: and UTC-4 in July (DST)');
    ok(P.offsetMinutes('Asia/Kolkata', new Date(JULY)) === 330, 'P1: half-hour zones');
    ok(P.offsetMinutes('Not/AZone', new Date(JULY)) === null, 'P1: a zone the browser rejects is null, never a guess');
    ok(P.offsetText(-420) === 'UTC-7' && P.offsetText(330) === 'UTC+5:30' && P.offsetText(0) === 'UTC', 'P1: offset text');
    ok(P.spanText(960) === '16 h' && P.spanText(570) === '9 h 30 min', 'P1: the gap reads 16 h / 9 h 30 min');
    ok(P.hhmm('America/Los_Angeles', new Date(JULY)) === '20:00', 'P1: a zone wall clock');
    ok(P.wallInOffset('2026-09-30T08:55:12Z', -420) === '2026-09-30 01:55:12', 'P1: a recorded clock in its own offset');
  }
  // P2 -- search
  {
    const { w } = await mk({ body: '' });
    const P = w.ProjectTime;
    const jul = new Date(JULY);
    ok(P.search('seoul', jul)[0].zone === 'Asia/Seoul', 'P2: a city finds its zone first');
    ok(P.search('new york', jul)[0].zone === 'America/New_York', 'P2: words match underscored names');
    const plus9 = P.search('+9', jul, 400);
    ok(plus9.some((h) => h.zone === 'Asia/Seoul') && plus9.some((h) => h.zone === 'Asia/Tokyo') &&
       plus9.every((h) => h.mins === 540), 'P2: an offset finds every zone at that offset now');
    ok(P.search('UTC-7', jul, 400).some((h) => h.zone === 'America/Los_Angeles'), 'P2: "UTC-7" finds Los Angeles in July');
    ok(P.search('zzzz', jul).length === 0 && P.search('', jul).length === 12, 'P2: no match is empty; the list is bounded');
  }
  // P3 -- the render does not wait for the clock
  {
    const W = await mk({ slowClock: true });
    const cur = W.d.querySelector('[data-tz-current]').textContent;
    ok(/^Asia\/Seoul · UTC\+9 · now \d\d:\d\d$/.test(cur), 'P3: the saved zone is shown at once, got "' + cur + '"');
    ok(W.d.querySelector('[data-tz-state]').textContent === 'saved', 'P3: and says it is saved');
    ok(W.log.filter((x) => x.url.indexOf('/project-time/clock') === 0).length === 1, 'P3: the clock is asked once, after the render');
    ok(W.d.querySelector('[data-tz-clock]').textContent === '', 'P3: nothing claims a clock status before it answers');
    W.hold.clock();
    await until(() => /This PC: UTC\+9/.test(W.d.querySelector('[data-tz-clock]').textContent));
    ok(/This PC: UTC\+9 · Time sync: on \(time\.windows\.com\)/.test(W.d.querySelector('[data-tz-clock]').textContent),
       'P3: then the PC zone and time sync, got "' + W.d.querySelector('[data-tz-clock]').textContent + '"');
  }
  // P4 -- follows the env picker's project
  {
    const W = await mk();
    W.d.dispatchEvent(new W.w.CustomEvent('sm-landing-project', { detail: { project: 'beta' } }));
    ok(W.d.querySelector('[data-tz-project]').textContent === 'beta', 'P4: the zone picker follows the env picker');
    ok(/default from alpha/.test(W.d.querySelector('[data-tz-state]').textContent), 'P4: a new project says whose zone it defaults to');
  }
  // P5 -- searchable list
  {
    const W = await mk();
    await until(() => W.w.ProjectTime._state().clock);
    btn(W.d, '[data-tz-change]').click();
    ok(!W.d.querySelector('[data-tz-search]').hidden, 'P5: Change... opens the search');
    const input = W.d.querySelector('[data-tz-input]');
    input.value = 'los an';
    input.dispatchEvent(new W.w.Event('input', { bubbles: true }));
    const row = W.d.querySelector('[data-tz-list] [data-zone]');
    ok(row && row.getAttribute('data-zone') === 'America/Los_Angeles', 'P5: typing filters the list');
    ok(/UTC-7/.test(row.textContent) && /20:00/.test(row.textContent), 'P5: each row shows its offset and its now, got "' + row.textContent + '"');
  }
  // P6 -- differs from the PC: ask, view, watch check
  {
    const W = await mk({ storage: { quam_tz: 'Europe/Paris' } });
    await until(() => W.w.ProjectTime._state().clock);
    const p = W.w.ProjectTime.pick('America/Los_Angeles');
    await until(() => W.d.getElementById('pt-zone-dialog'));
    const dlg = W.d.getElementById('pt-zone-dialog');
    ok(dlg && dlg.hasAttribute('open'), 'P6: a pick that differs from the PC asks first');
    ok(dlg.children.length === 1 && dlg.children[0].matches('article.pt-card') && /This PC is in another zone/.test(dlg.children[0].textContent),
       'P6: the content sits in ONE article box (Pico centres it; loose children lay out as a full-screen row)');
    ok(/This PC is UTC\+9/.test(dlg.textContent) && /America\/Los_Angeles, UTC-7/.test(dlg.textContent) &&
       /16 h apart/.test(dlg.textContent), 'P6: with both offsets and the gap, got "' + dlg.textContent + '"');
    ok(/view times in Los Angeles time\?/.test(dlg.textContent), 'P6: and the question names the city');
    ok(posts(W, '/project-time/zone').length === 0, 'P6: nothing is saved before the answer');
    btn(W.d, '[data-pt-answer="view"]').click();
    await until(() => posts(W, '/project-time/zone').length === 1);
    const z = posts(W, '/project-time/zone')[0].form;
    ok(z.project === 'alpha' && z.zone === 'America/Los_Angeles' && z.os_answer === 'view' && z.os_offset === '+09:00',
       'P6: "view" saves the zone with the answer');
    await until(() => /Now \d\d:\d\d in America\/Los_Angeles/.test(W.d.getElementById('pt-zone-dialog').textContent));
    const wd = W.d.getElementById('pt-zone-dialog');
    ok(/Now 20:0\d in America\/Los_Angeles \(UTC-7\)\./.test(wd.textContent), 'P6: the watch check shows now in the chosen zone, got "' + wd.textContent + '"');
    ok(/Does it match your watch\?/.test(wd.textContent) && /Time sync: on/.test(wd.textContent), 'P6: and the time-sync status');
    ok(W.d.documentElement.getAttribute('data-sm-zone') === 'America/Los_Angeles', 'P6: the page renders in the new zone at once');
    ok(!('quam_tz' in W.store), 'P6: the old per-browser zone is retired (one setting)');
    ok(/Los Angeles UTC-7/.test(W.d.querySelector('[data-tz-card="alpha"]').textContent), 'P6: the card follows');
    btn(W.d, '[data-pt-answer="matches"]').click();
    await until(() => posts(W, '/project-time/watch').length === 1);
    const wf = posts(W, '/project-time/watch')[0].form;
    ok(wf.answer === 'matches' && /^20:0\d$/.test(wf.shown) && wf.ntp_synced === 'true', 'P6: the watch answer is stored with what was shown');
    ok(!W.d.getElementById('pt-zone-dialog').hasAttribute('open'), 'P6: and the dialog closes');
    ok(await p === true, 'P6: the pick resolves saved');
  }
  // P7 -- same offset as the PC: no question
  {
    const W = await mk();
    await until(() => W.w.ProjectTime._state().clock);
    W.w.ProjectTime.pick('Asia/Tokyo');
    await until(() => posts(W, '/project-time/zone').length === 1);
    ok(!('os_answer' in posts(W, '/project-time/zone')[0].form), 'P7: the PC zone matches: nothing asked');
    await until(() => /Does it match your watch/.test((W.d.getElementById('pt-zone-dialog') || {}).textContent || ''));
    ok(/Now 12:0\d in Asia\/Tokyo/.test(W.d.getElementById('pt-zone-dialog').textContent), 'P7: straight to the watch check');
  }
  // P7b -- UTC reads once on the card
  {
    const W = await mk({ osOff: '+00:00' });
    await until(() => W.w.ProjectTime._state().clock);
    W.w.ProjectTime.pick('UTC');
    await until(() => posts(W, '/project-time/zone').length === 1);
    await tick(10);
    ok(W.d.querySelector('[data-tz-card="alpha"]').textContent === 'tz UTC', 'P7b: a UTC card reads "tz UTC", got "' + W.d.querySelector('[data-tz-card="alpha"]').textContent + '"');
  }
  // P8 -- cancel
  {
    const W = await mk();
    await until(() => W.w.ProjectTime._state().clock);
    const p = W.w.ProjectTime.pick('Europe/London');
    await until(() => W.d.getElementById('pt-zone-dialog'));
    btn(W.d, '[data-pt-answer="cancel"]').click();
    ok(await p === false && posts(W, '/project-time/zone').length === 0, 'P8: Cancel saves nothing');
    ok(W.d.querySelector('[data-tz-current]').textContent.indexOf('Asia/Seoul') === 0, 'P8: the shown zone is unchanged');
  }
  // P9 -- the PC zone is wrong; the watch says no
  {
    const W = await mk();
    await until(() => W.w.ProjectTime._state().clock);
    W.w.ProjectTime.pick('America/Los_Angeles');
    await until(() => W.d.getElementById('pt-zone-dialog'));
    btn(W.d, '[data-pt-answer="pc_wrong"]').click();
    await until(() => posts(W, '/project-time/zone').length === 1);
    ok(posts(W, '/project-time/zone')[0].form.os_answer === 'pc_wrong', 'P9: "the PC zone is wrong" is saved as such');
    await until(() => /Fix the PC/.test(W.d.getElementById('pt-zone-dialog').textContent));
    ok(/Time zone\. Runs already recorded keep their own offsets/.test(W.d.getElementById('pt-zone-dialog').textContent),
       'P9: and says how to fix it');
    btn(W.d, '[data-pt-answer="differs"]').click();
    await until(() => posts(W, '/project-time/watch').length === 1);
    ok(posts(W, '/project-time/watch')[0].form.answer === 'differs', 'P9: a watch that disagrees is stored');
    ok(/Set time automatically/.test(W.d.getElementById('pt-zone-dialog').textContent), 'P9: and SM says how to sync the clock');
  }
  // P10 -- the old per-browser zone
  {
    const views = { alpha: { project: 'alpha', zone: null, state: 'none' } };
    const W = await mk({ views, storage: { quam_tz: 'Europe/Paris' } });
    ok(/^Europe\/Paris · UTC\+\d · \(this browser\)$|^Europe\/Paris · UTC\+\d \(this browser\)$/.test(
       W.d.querySelector('[data-tz-current]').textContent), 'P10: an earlier per-browser zone is shown, got "' +
       W.d.querySelector('[data-tz-current]').textContent + '"');
    btn(W.d, '[data-tz-change]').click();
    const first = W.d.querySelector('[data-tz-list] [data-zone]');
    ok(first.getAttribute('data-zone') === 'Europe/Paris' && /your earlier choice/.test(first.textContent),
       'P10: and offered first');
  }
  // P10b -- nothing set anywhere: this PC's zone, and the state says so once
  {
    const W = await mk({ views: { alpha: { project: 'alpha', zone: null, state: 'none' } } });
    await until(() => W.w.ProjectTime._state().clock);
    await tick(10);
    ok(W.d.querySelector('[data-tz-current]').textContent === 'UTC+9 (this PC)', 'P10b: an unset zone shows the PC offset, got "' + W.d.querySelector('[data-tz-current]').textContent + '"');
    ok(W.d.querySelector('[data-tz-state]').textContent === 'not set — pick one', 'P10b: and says it is not set, once');
  }
  // P11 -- the run-clock question, once, with evidence
  const ASK = {
    project: 'alpha',
    clock: {
      auto_ask: true,
      ask: { skew_s: 3600, skew_text: '1 h 00 min', ahead: true, n: 3, src: 'live', whole_units: true,
             from_utc: '2026-09-30T07:00:00Z', from_key: 'D:/data::2026-09-30/#10_ramsey_005512',
             examples: [{ key: 'D:/data::2026-09-30/#12_ramsey_015512', run_utc: '2026-09-30T08:55:12Z',
                          run_off: '-07:00', seen_utc: '2026-09-30T07:55:13Z', skew_s: 3599 }] } },
    line: { text: 'Run clock: 1 h 00 min ahead of this PC (3 runs seen arriving live) -- waiting for your answer.', ask: true },
  };
  {
    const answered = { project: 'alpha', clock: { ask: null, auto_ask: false }, line: { text: 'answered', ask: false } };
    const W = await mk({ body: '', htmlAttrs: 'data-sm-project="alpha" data-sm-zone="Asia/Seoul"',
      status: (n, log) => (log.some((x) => x.url === '/project-time/skew-answer') ? answered : ASK) });
    await W.w.ProjectTime.checkStatus(false);
    const dlg = W.d.getElementById('pt-skew-dialog');
    ok(dlg && dlg.hasAttribute('open'), 'P11: an unanswered skew puts the question up');
    ok(/Two clocks disagree by 1 h 00 min/.test(dlg.textContent) && /SM saw 3 runs of alpha arrive/.test(dlg.textContent) &&
       /1 h 00 min later than this PC saw its folder appear/.test(dlg.textContent), 'P11: with the evidence');
    ok(/(^|[^/])#12_ramsey_015512: run clock 2026-09-30 01:55:12 \(UTC-7\) · seen 2026-09-30 16:55:13 \(UTC\+9\)/.test(dlg.querySelector('.pt-examples').textContent),
       'P11: an example run: its own clock and when SM saw it, got "' + (dlg.querySelector('.pt-examples') || {}).textContent + '"');
    ok(/wrong time zone/.test(dlg.textContent), 'P11: a whole-hour step is named as a zone mistake');
    ok(/corrected from run #10_ramsey_005512 on/.test(dlg.textContent), 'P11: the correction starts at a named run, not at its skewed clock');
    ok(/Only .The experiment PC.s clock. changes how run times are shown/.test(dlg.textContent), 'P11: it says what each answer does');
    ok(posts(W, '/project-time/skew-shown').length === 1 && posts(W, '/project-time/skew-shown')[0].form.skew_s === '3600',
       'P11: it is marked shown (asked once)');
    btn(W.d, '[data-pt-choice="experiment_pc"]').click();
    await until(() => posts(W, '/project-time/skew-answer').length === 1);
    const a = posts(W, '/project-time/skew-answer')[0].form;
    ok(a.choice === 'experiment_pc' && a.skew_s === '3600' && a.project === 'alpha', 'P11: the answer carries the skew it was asked about');
    await until(() => !W.d.getElementById('pt-skew-dialog').hasAttribute('open'));
    ok(!W.d.getElementById('pt-skew-dialog').hasAttribute('open'), 'P11: and the question closes');
  }
  // P12 -- shown before: no popup; Diagnostics' Answer... opens it
  {
    const shown = JSON.parse(JSON.stringify(ASK)); shown.clock.auto_ask = false;
    const W = await mk({ body: '<p data-clock-line><span class="diag-clock-text">x</span><button data-clock-ask-open>Answer</button></p>',
                      htmlAttrs: 'data-sm-project="alpha"', status: shown });
    await W.w.ProjectTime.checkStatus(false);
    ok(!W.d.getElementById('pt-skew-dialog'), 'P12: a question already shown does not pop up again');
    ok(/waiting for your answer/.test(W.d.querySelector('.diag-clock-text').textContent), 'P12: the Diagnostics line is refreshed');
    btn(W.d, '[data-clock-ask-open]').click();
    await until(() => W.d.getElementById('pt-skew-dialog'));
    ok(W.d.getElementById('pt-skew-dialog').hasAttribute('open'), 'P12: Answer... opens it on demand');
  }
  // P13 -- the skew moved meanwhile: ask again
  {
    const W = await mk({ body: '', htmlAttrs: 'data-sm-project="alpha"', status: ASK, answer409: true });
    await W.w.ProjectTime.checkStatus(false);
    btn(W.d, '[data-pt-choice="ignore"]').click();
    await until(() => W.log.filter((x) => x.url.indexOf('/project-time/status') === 0).length === 2);
    ok(W.log.filter((x) => x.url.indexOf('/project-time/status') === 0).length === 2, 'P13: a 409 fetches the new question');
  }
  // P14 -- no project open
  {
    const W = await mk({ body: '', status: ASK });
    await W.w.ProjectTime.checkStatus(false);
    ok(W.log.filter((x) => x.url.indexOf('/project-time/status') === 0).length === 0, 'P14: no project, no status fetch');
  }
  // P15 -- run times
  {
    const body = '<span id="a" class="ts-run" data-utc="2026-09-30T08:55:12Z" data-recorded="2026-09-30 01:55:12" ' +
      'data-recorded-off="UTC-7" data-quality="offset">x</span>' +
      '<span id="b" class="ts-run" data-utc="2026-09-30T08:55:12Z" data-recorded="2026-09-30 01:55:12" ' +
      'data-recorded-off="UTC-7" data-corrected-utc="2026-09-30T07:55:12Z" data-corrected-by="-1 h 00 min">y</span>' +
      '<span id="c" class="ts-run" data-utc="2026-09-30T08:55:12Z" data-recorded="2026-09-30 17:55:12" data-recorded-off="">z</span>';
    const W = await mk({ body, htmlAttrs: 'data-sm-zone="Asia/Seoul"' });
    const a = W.d.getElementById('a'), b = W.d.getElementById('b'), c = W.d.getElementById('c');
    ok(a.textContent === '2026-09-30 17:55:12 (UTC+9)', 'P15: a run reads in the viewer zone, got "' + a.textContent + '"');
    ok(/^recorded 2026-09-30 01:55:12 \(UTC-7\)/.test(a.title), 'P15: its recorded clock is in the tooltip, got "' + a.title + '"');
    ok(/^2026-09-30 16:55:12 \(UTC\+9\) corrected recorded 2026-09-30 01:55:12 \(UTC-7\)$/.test(b.textContent),
       'P15: a correction is labelled and keeps the recorded time visible, got "' + b.textContent + '"');
    ok(b.querySelector('.ts-run-corrected') && /corrected by -1 h 00 min/.test(b.title), 'P15: and says by how much');
    ok(/folder clock; zone not recorded/.test(c.title), 'P15: a clock with no offset says so');
    W.w.setDisplayZone('UTC');
    ok(a.textContent === '2026-09-30 08:55:12 (UTC)', 'P15: a zone change re-renders, got "' + a.textContent + '"');
  }
  // P16 -- one zone
  {
    const W = await mk({ body: '', htmlAttrs: 'data-sm-zone="Asia/Seoul"', storage: { quam_tz: 'America/New_York' } });
    ok(W.w.SnapTime.zone() === 'Asia/Seoul', 'P16: the project zone beats the old per-browser one');
    const W2 = await mk({ body: '', storage: { quam_tz: 'America/New_York' } });
    ok(W2.w.SnapTime.zone() === 'America/New_York', 'P16: with no project zone anywhere, the old choice still reads');
  }
  if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
  console.log('project_time_selfcheck: all checks passed');
  process.exit(0);
})().catch((e) => { console.error('FAIL: crashed: ' + (e && e.stack || e)); process.exit(1); });
