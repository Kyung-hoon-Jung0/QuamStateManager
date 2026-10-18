/* v1.5.1 (T34-F2): searching exactly "UTC" lists UTC first.
 *
 * The query "UTC" was read only as "offset zero": every zone at UTC+0 matched,
 * sorted alphabetically and capped at 12, and the zone named UTC fell past the
 * cap. The driver (tests/test_v151_p2_zone_picker.py) hands this harness the
 * REAL /landing/projects fragment; the SHIPPED project-time.js renders the
 * list as a person types.
 *
 * Run: node tests/v151_zone_picker_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.error('jsdom not installed');
    process.exit(2);
}
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const PT = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'project-time.js'), 'utf8');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = () => new Promise((r) => setTimeout(r, 5));

(async function () {
    const dom = new JSDOM('<html><body>' + FX.fragment + '</body></html>',
        { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1/' });
    const w = dom.window, d = w.document;
    w.fetch = async (url) => ({ ok: true, status: 200, json: async () => (String(url).indexOf('/project-time/clock') === 0
        ? { os_zone: { utc_offset: '+09:00' }, ntp: { synced: true }, ntp_text: 'Time sync: on', now_ms: Date.now() } : {}) });
    w.eval(PT);
    for (let i = 0; i < 200 && !w.ProjectTime._state().clock; i++) await tick();
    d.querySelector('[data-tz-change]').click();
    const input = d.querySelector('[data-tz-input]');
    function type(text) {
        input.value = text;
        input.dispatchEvent(new w.Event('input', { bubbles: true }));
        return Array.from(d.querySelectorAll('[data-tz-list] [data-zone]'));
    }
    let rows = type('UTC');
    ok(rows.length > 0 && rows[0].getAttribute('data-zone') === 'UTC', 'exactly "UTC" lists the zone UTC first: '
       + rows.slice(0, 3).map((r) => r.getAttribute('data-zone')).join(', '));
    ok(rows.length <= 12, 'still capped (' + rows.length + ')');
    ok(rows.every((r) => r.tagName === 'BUTTON' && r.classList.contains('landing-tz-row')),
       'each zone is a button.landing-tz-row (the CSS pin reads that rule)');
    rows = type('utc');
    ok(rows[0] && rows[0].getAttribute('data-zone') === 'UTC', 'case does not matter');
    rows = type('asia/seoul');
    ok(rows[0] && rows[0].getAttribute('data-zone') === 'Asia/Seoul', 'an exact IANA name comes first too');
    rows = type('+9');
    ok(rows.length > 0 && rows.every((r) => w.ProjectTime.offsetMinutes(r.getAttribute('data-zone'), new Date()) === 540),
       'an offset query still lists that offset');
    console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('UNCAUGHT', e && e.stack || e); process.exit(1); });
