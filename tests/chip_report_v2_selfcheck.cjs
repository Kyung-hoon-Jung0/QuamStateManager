/* docs/277 -- the shareable report's clone-and-filter serializer, under jsdom.
 *
 * Usage (driven by tests/test_chip_report_v2.py):
 *   node tests/chip_report_v2_selfcheck.cjs <fixture.json>
 * fixture.json = { page: <GET /chip-status/report html>, sections: {key: <fragment html>} }
 *
 * The REAL page markup and its REAL inline scripts run; only the network is a
 * stub: section fetches answer from the fixture, the stylesheet fetch answers
 * a marker, and /finalize echoes the posted document (and records the
 * request) so every claim below is about what the BROWSER would send.
 */
'use strict';
const fs = require('fs');
const { JSDOM } = require('jsdom');

let fails = 0;
function ok(cond, what) {
    if (cond) console.log('ok - ' + what);
    else { fails++; console.log('not ok - ' + what); }
}

const fx = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const posts = [];
let failKey = null;

function resp(status, body, headers) {
    return {
        ok: status >= 200 && status < 300, status: status,
        text: function () { return Promise.resolve(body); },
        headers: { get: function (k) { return (headers || {})[k] || null; } },
    };
}

const dom = new JSDOM(fx.page, {
    url: 'http://localhost/chip-status/report?sections=overview,chip_status,pulses,zline,raw&redact=1',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    beforeParse(window) {
        window.fetch = function (url, opts) {
            url = String(url);
            const m = /\/chip-status\/report\/section\/(\w+)/.exec(url);
            if (m) {
                if (m[1] === failKey) return Promise.resolve(resp(500, 'planted server error'));
                return Promise.resolve(resp(200, fx.sections[m[1]] || ''));
            }
            if (/\.css(\?|$)/.test(url)) return Promise.resolve(resp(200, 'body{--css-marker:1}'));
            if (url.indexOf('/chip-status/report/finalize') >= 0) {
                const body = JSON.parse(opts.body);
                posts.push(body);
                return Promise.resolve(resp(200, body.html, { 'X-Report-Filename': 'chip_report_x.html' }));
            }
            return Promise.resolve(resp(404, 'no stub for ' + url));
        };
    },
});
const w = dom.window, d = w.document;

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function box(key) { return d.querySelector('input[data-rep-box][value="' + key + '"]'); }
function toggle(key, on) {
    const b = box(key);
    b.checked = on;
    b.dispatchEvent(new w.Event('change', { bubbles: true }));
}

(async function () {
    await sleep(300);                                // the lazy section fetches land
    // a drawn map, as ComponentMap would leave it (it does not run under jsdom)
    const map = d.querySelector('#component-map');
    ok(!!map, 'the inline Chip Status section carries the map mount');
    const svg = d.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('data-drawn-map', '1');
    map.appendChild(svg);

    const html = await w.ChipReport.buildStandalone();
    const p = posts[posts.length - 1];
    ok(html === p.html, 'the build resolves to what the server sent back');
    ok(JSON.stringify(p.sections) === JSON.stringify(['overview', 'chip_status', 'pulses', 'zline', 'raw']),
       'the request declares exactly the checked sections: ' + JSON.stringify(p.sections));
    ok(p.redact === true, 'the redaction switch travels with the request');
    const secs = (p.html.match(/data-rep-sec="(\w+)"/g) || []).map(function (s) { return s.slice(14, -1); });
    ok(JSON.stringify(secs) === JSON.stringify(p.sections),
       'the document holds the checked sections and nothing else: ' + JSON.stringify(secs));
    ok(p.html.indexOf('id="rep-panel"') < 0 && p.html.indexOf('rep-download') < 0,
       'the panel and the download button are not in the file');
    ok((p.html.match(/<script/g) || []).length === 2 && p.html.indexOf('ChipReportRaw') >= 0,
       'only the raw tree (its data + its renderer) keeps a script');
    ok(!/<div class="rep-raw-tree" data-rep-raw-tree[^>]*data-rep-raw-ready/.test(p.html) && p.html.indexOf('Opening the tree') >= 0,
       'the preview-expanded tree is reset, the file builds its own');
    ok(!/<link[^>]+stylesheet/.test(p.html) && p.html.indexOf('--css-marker:1') >= 0,
       'the stylesheet is inlined, its link is gone');
    ok(p.html.indexOf('data-drawn-map="1"') >= 0, 'the drawn map is baked in');
    ok(p.html.indexOf('<symbol id="rsp0"') >= 0, 'the pulses thumbnails are in the file');

    // uncheck Pulses: its own content must leave the file entirely
    toggle('pulses', false);
    ok(/sections=overview%2Cchip_status%2Czline%2Craw|sections=overview,chip_status,zline,raw/.test(w.location.search),
       'the selection rides the URL: ' + w.location.search);
    ok(/Not in the file:.*Pulses/.test(d.getElementById('rep-will').textContent),
       'the panel says Pulses is not in the file');
    await w.ChipReport.buildStandalone();
    const p2 = posts[posts.length - 1];
    ok(p2.sections.indexOf('pulses') < 0 && p2.html.indexOf('data-rep-sec="pulses"') < 0
       && p2.html.indexOf('class="rep-wide rep-pulses"') < 0 && p2.html.indexOf('<symbol id="rsp') < 0,
       'an unchecked section is absent -- not hidden, not in a symbol, not anywhere');

    // uncheck Raw: no script survives at all
    toggle('raw', false);
    await w.ChipReport.buildStandalone();
    const p3 = posts[posts.length - 1];
    ok(p3.html.indexOf('<script') < 0 && p3.html.indexOf('rep-raw-data') < 0,
       'without the raw tree the file carries no script and no JSON blob');

    // a section whose fetch fails says so, in words, in its own place
    failKey = 'trends';
    toggle('trends', true);
    await sleep(200);
    const t = d.querySelector('section[data-rep-sec="trends"]');
    ok(t && /Could not be built: HTTP 500/.test(t.textContent) && !t.hidden,
       'a failed section fetch renders the honest line: ' + (t && t.textContent.trim().slice(0, 80)));
    await w.ChipReport.buildStandalone();
    const p4 = posts[posts.length - 1];
    ok(p4.html.indexOf('Could not be built: HTTP 500') >= 0,
       'the failure line, not a blank, is what the file carries');

    console.log(fails ? fails + ' FAILED' : 'all passed');
    process.exit(fails ? 1 : 0);
})().catch(function (e) { console.log('not ok - threw ' + (e && e.stack || e)); process.exit(1); });
