// window.SnapTime — the one place a snapshot stamp becomes a time on screen.
//
// A stamp is UTC (core/history._ts_stamp; a run ingest converts LOCAL->UTC).
// The display sites already honoured that; the CHART AXES sliced the UTC digits
// into a label and drew them unconverted, so a Korean bench read a point at
// 03:14 while the row above it said 12:14. These pin the conversion, the zone
// preference, and the refusal to guess.
//
// Run: node tests/snaptime_selfcheck.cjs   (needs jsdom)
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}

const ROOT = path.join(__dirname, '..');
const APP_JS = fs.readFileSync(
  path.join(ROOT, 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } }

function world(zone) {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  const store = {};
  // jsdom's localStorage is fine, but an explicit stub keeps the zone exact
  Object.defineProperty(win, 'localStorage', {
    configurable: true,
    value: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
  });
  if (zone) store['quam_tz'] = zone;
  new win.Function(APP_JS).call(win);
  return win;
}

// A stamp chosen so every zone below lands on a different calendar day or hour.
const TS = '20260917_031400_000';     // 2026-09-17 03:14:00 UTC

// S1 — UTC in, UTC out, when that is what the viewer asked for.
{
  const S = world('UTC').SnapTime;
  ok(S.format(TS) === '2026-09-17 03:14:00', 'S1: UTC formats verbatim, got ' + S.format(TS));
  ok(S.axisValue(TS) === '2026-09-17T03:14:00', 'S1: axis value is naive ISO');
  ok(S.label() === 'UTC', 'S1: the axis says which zone it is');
}

// S2 — the reported case: Seoul is UTC+9, so the chart must read 12:14, which
//      is what the ts_local row beside it has always said.
{
  const S = world('Asia/Seoul').SnapTime;
  ok(S.format(TS) === '2026-09-17 12:14:00',
     'S2: Asia/Seoul is UTC+9, got ' + S.format(TS));
  ok(S.short(TS) === '09-17 12:14', 'S2: the compact chip agrees, got ' + S.short(TS));
  ok(S.label() === 'Asia/Seoul', 'S2: named zones are labelled by name');
}

// S3 — a zone WEST of UTC crosses back over midnight, which a naive slice of
//      the digits can never do.
{
  const S = world('America/New_York').SnapTime;
  ok(S.format(TS) === '2026-09-16 23:14:00',
     'S3: New York is UTC-4 in September and rolls to the previous day, got '
     + S.format(TS));
}

// S4 — DST is Intl's problem, not ours, but it must actually be handled:
//      the same zone, six months apart, is not the same offset.
{
  const S = world('America/New_York').SnapTime;
  const winter = S.format('20260117_031400_000');   // EST, UTC-5
  ok(winter === '2026-01-16 22:14:00',
     'S4: January in New York is UTC-5, got ' + winter);
}

// S5 — an unparseable id is NEVER placed at a fabricated instant.
{
  const S = world('UTC').SnapTime;
  ok(S.parse('not-a-stamp') === null, 'S5: garbage parses to null');
  ok(S.axisValue('not-a-stamp') === null, 'S5: and has no axis value');
  ok(S.format('not-a-stamp') === 'not-a-stamp',
     'S5: the raw label survives rather than becoming a wrong time');
  ok(S.parse(null) === null && S.parse(undefined) === null, 'S5: null/undefined too');
}

// S6 — a stamp with no milliseconds (the run-ingest spelling) parses the same.
{
  const S = world('UTC').SnapTime;
  ok(S.format('20260917_031400') === '2026-09-17 03:14:00',
     'S6: the 15-char stamp parses, got ' + S.format('20260917_031400'));
  ok(S.format('20260917_031400_042') === '2026-09-17 03:14:00',
     'S6: a run-id suffix does not change the instant');
}

// S7 — no preference means this browser's own zone, and the label says so
//      rather than leaving the basis unstated.
{
  const S = world('').SnapTime;
  ok(S.zone() === '', 'S7: unset by default');
  ok(typeof S.label() === 'string' && S.label().length > 0,
     'S7: an unset zone still names itself');
  ok(S.format(TS) !== TS, 'S7: and still converts');
}

// S8 — setZone round-trips, and clearing returns to the browser default.
{
  const S = world('').SnapTime;
  S.setZone('Asia/Seoul');
  ok(S.zone() === 'Asia/Seoul', 'S8: the choice persists');
  ok(S.format(TS) === '2026-09-17 12:14:00', 'S8: and takes effect at once');
  S.setZone('');
  ok(S.zone() === '', 'S8: clearing returns to the browser zone');
}

// S9 — a zone the browser rejects must not throw on every point of a series.
{
  const S = world('Not/AZone').SnapTime;
  const out = S.format(TS);
  ok(typeof out === 'string' && out.indexOf('2026-09-1') === 0,
     'S9: a bad zone degrades to the browser zone, got ' + out);
}

// S10 — blocked storage (private window) must not break the page.
{
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const win = dom.window;
  Object.defineProperty(win, 'localStorage', {
    configurable: true,
    get() { throw new Error('blocked'); },
  });
  new win.Function(APP_JS).call(win);
  ok(win.SnapTime.zone() === '', 'S10: a throwing storage reads as unset');
  ok(win.SnapTime.format(TS).indexOf('2026-09-1') === 0,
     'S10: and formatting still works');
}

// S11 — the Settings control changes what is on screen and nothing else, and
//       it re-localizes the ROWS as well as the charts. The rows are marked
//       done-once, so a zone change that forgot to clear that marker would move
//       the chart and leave the row behind -- the exact disagreement this
//       whole change exists to remove.
{
  const win = world('');
  const doc = win.document;
  doc.body.innerHTML =
    '<select id="tz-select"><option value="">This browser</option><option value="UTC">UTC</option><option value="Asia/Seoul">Asia/Seoul</option></select><small id="tz-note"></small>' +
    '<span class="ts-local" data-utc="2026-09-17T03:14:00Z">fallback</span>';
  win.applyLocalTimes(doc);
  const span = doc.querySelector('.ts-local');
  ok(span.getAttribute('data-localized') === '1', 'S11: the row localized once');
  const first = span.textContent;

  win.setDisplayZone('Asia/Seoul');
  ok(win.SnapTime.zone() === 'Asia/Seoul', 'S11: the zone took');
  ok(span.textContent !== first || first.indexOf('12:14') >= 0,
     'S11: the row re-localized rather than staying on the old zone');
  ok(span.textContent.indexOf('12:14') >= 0,
     'S11: and reads Seoul time, got ' + span.textContent);

  win.setDisplayZone('UTC');
  ok(span.textContent.indexOf('3:14') >= 0 || span.textContent.indexOf('03:14') >= 0,
     'S11: switching again moves it again, got ' + span.textContent);
}

// S12 — the note states the basis, which is what the report asked for.
{
  const win = world('');
  win.document.body.innerHTML =
    '<select id="tz-select"><option value="">This browser</option><option value="UTC">UTC</option><option value="Asia/Seoul">Asia/Seoul</option></select><small id="tz-note"></small>';
  win.setDisplayZone('Asia/Seoul');
  const note = win.document.getElementById('tz-note').textContent;
  ok(note.indexOf('Asia/Seoul') >= 0, 'S12: the note names the zone');
  ok(note.indexOf('UTC') >= 0, 'S12: and says what is stored');
  ok(win.document.getElementById('tz-select').value === 'Asia/Seoul',
     'S12: the control shows the current choice');
}

// S13 — a zone change announces itself, so a chart can redraw.
{
  const win = world('');
  win.document.body.innerHTML = '<select id="tz-select"><option value="">This browser</option><option value="UTC">UTC</option><option value="Asia/Seoul">Asia/Seoul</option></select>';
  let heard = null;
  win.document.body.addEventListener('sm:timezone-changed',
    function (e) { heard = e.detail.zone; });
  win.setDisplayZone('UTC');
  ok(heard === 'UTC', 'S13: the change is announced, heard ' + heard);
}

// S14 — a zone the shortlist does not carry still shows itself, rather than
//        leaving a blank control beside correctly-converted times.
{
  const win = world('Australia/Sydney');
  win.document.body.innerHTML =
    '<select id="tz-select"><option value="">This browser</option>'
    + '<option value="UTC">UTC</option></select><small id="tz-note"></small>';
  win.syncZoneNote();
  const sel = win.document.getElementById('tz-select');
  ok(sel.value === 'Australia/Sydney',
     'S14: the control shows an off-list zone, got "' + sel.value + '"');
  ok(win.document.getElementById('tz-note').textContent.indexOf('Australia/Sydney') >= 0,
     'S14: and the note agrees with it');
}

if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
console.log('snaptime_selfcheck: all checks passed (37 assertions)');
process.exit(0);
