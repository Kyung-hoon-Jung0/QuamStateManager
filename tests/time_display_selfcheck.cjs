/* docs/244 -- SnapTime.display / applyLocalTimes against the real app.js.
 * Pinned: one fixed English/digit form "YYYY-MM-DD HH:MM:SS (UTC+9)" in the
 * chosen zone (DST and half-hour zones included); an ISO offset is honoured;
 * a non-time is shown as written; applyLocalTimes renders that form (never
 * the browser locale's words) and names the full form in a chip's title.
 * Run: node tests/time_display_selfcheck.cjs (driven by tests/test_time_display.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
const i = SRC.indexOf("    var KEY = 'quam_tz';");
const SNAP = SRC.slice(SRC.lastIndexOf('(function () {', i), SRC.indexOf('})();', i) + 5);
const a = SRC.indexOf('function applyLocalTimes(root) {');
const APPLY = SRC.slice(a, SRC.indexOf('\n}', a) + 2);

function world(zone) {
    const dom = new JSDOM('<!doctype html><body>'
        + '<span id="l" class="ts-local" data-utc="2026-09-30T10:55:46Z">2026-09-30 10:55:46 UTC</span>'
        + '<span id="s" class="ts-local" data-utc="2026-09-30T10:55:46Z" data-fmt="short">09-30 10:55</span>'
        + '</body>', { runScripts: 'outside-only' });
    const w = dom.window;
    const store = { quam_tz: zone };
    Object.defineProperty(w, 'localStorage', { value: { getItem: (k) => store[k] || null, setItem() {}, removeItem() {} } });
    w.eval(SNAP);
    w.eval(APPLY + '\nwindow.applyLocalTimes = applyLocalTimes;');
    return w;
}

const W = world('Asia/Seoul');
const S = W.SnapTime;
ok(S.display('2026-09-30T19:55:46.424+09:00') === '2026-09-30 19:55:46 (UTC+9)', 'an ISO offset is honoured: ' + S.display('2026-09-30T19:55:46.424+09:00'));
ok(S.display('20260930_105546') === '2026-09-30 19:55:46 (UTC+9)', 'SM\'s UTC stamp in the chosen zone');
ok(S.display('20260930_105546', true) === '09-30 19:55', 'the short form');
ok(S.display('2026-09-30T10:55:46') === '2026-09-30 19:55:46 (UTC+9)', 'a bare ISO is SM\'s UTC');
ok(S.display('pending') === 'pending', 'a non-time is shown as written');
ok(world('America/New_York').SnapTime.display('2026-01-15T03:00:00Z') === '2026-01-14 22:00:00 (UTC-5)', 'winter offset');
ok(world('America/New_York').SnapTime.display('2026-07-15T03:00:00Z') === '2026-07-14 23:00:00 (UTC-4)', 'summer (DST) offset');
ok(world('Asia/Kolkata').SnapTime.display('2026-07-15T03:00:00Z') === '2026-07-15 08:30:00 (UTC+5:30)', 'a half-hour zone');
ok(world('UTC').SnapTime.display('2026-07-15T03:00:00Z') === '2026-07-15 03:00:00 (UTC)', 'UTC itself');
ok(S.axisValue('2026-09-30T19:55:46+09:00') === '2026-09-30T19:55:46', 'axisValue reads an ISO instant too (the field-history chart)');

W.applyLocalTimes(W.document);
const l = W.document.getElementById('l'), s = W.document.getElementById('s');
ok(l.textContent === '2026-09-30 19:55:46 (UTC+9)', 'applyLocalTimes renders the one form: ' + l.textContent);
ok(s.textContent === '09-30 19:55' && /2026-09-30 19:55:46 \(UTC\+9\)/.test(s.title), 'a chip is short, its title the full form: ' + s.title);
ok(!/[^\x00-\x7F]/.test(l.textContent + s.textContent + s.title.replace('·', '')), 'no non-ASCII words (no locale text)');

console.log(fails ? fails + ' FAILED' : 'all passed');
process.exit(fails ? 1 : 0);
