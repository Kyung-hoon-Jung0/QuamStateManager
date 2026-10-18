/* P1-2 (UX round) -- the reproduced figures' click-to-apply refusal reads in
 * plain words and points to the figure that does take clicks. The REAL
 * app.js slice that renders interactive figures, under jsdom (the same slice
 * f5_plot_selfcheck evaluates):
 *   - two reproduced (lab-code) figures refuse with the learn_click phrase;
 *     the run note leads "Click-to-apply is off on these figures:" and never
 *     shows the internal phrase;
 *   - a recipe figure below renders clickable: its title gets the "click a
 *     point to apply" badge and the run note names it.
 *
 * Run: node tests/click_refusal_selfcheck.cjs
 * (driven by tests/test_v151_ux_wrote_state.py)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const root = path.join(__dirname, '..', 'quam_state_manager', 'web');
const dom = new JSDOM('<html><body><div id="run">' +
    '<div class="ds-replot-head"></div><div class="ds-interactive-list" id="list"></div>' +
    '<p class="muted ds-recipe-head">SM fit figures and saved images</p>' +
    '<div class="ds-interactive-list" id="legacy">' +
    '<div class="ds-interactive-fig"><div class="ds-interactive-fig-title">Amplitude + fit</div><div id="lp"></div></div>' +
    '</div></div></body></html>',
    { runScripts: 'outside-only', url: 'http://localhost/', pretendToBeVisual: true });
const w = dom.window;
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const pause = () => new Promise(r => setTimeout(r, 20));

const app = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
w.eval(app.slice(app.indexOf('function _fetchInteractiveFig('), app.indexOf('function _fetchAndRenderPlot(')));
w._plotlyRender = () => Promise.resolve();
w._pruneInteractiveTiles = () => {};
w._attachInteractivePlotClickHandler = () => {};
w.MapTools = null;

const REFUSE = 'this run wrote no state: click-to-apply off; qubits.{q}.f_01: no common verified marker transform';
const answers = [
    { data: [], layout: {}, display_only_reason: REFUSE, notices: [REFUSE] },
    { data: [], layout: {}, display_only_reason: REFUSE, notices: [REFUSE] },
    { data: [], layout: {}, clickable: { mode: 'x' }, notices: [] },
];
let call = 0;
w.fetch = () => { const a = answers[call++]; return Promise.resolve({ json: () => Promise.resolve(a) }); };

(async function main() {
    const container = w.document.getElementById('run'); container._gen = 1;
    const list = w.document.getElementById('list');
    for (let i = 0; i < 2; i++) {
        const div = w.document.createElement('div'); div.dataset.endpoint = 'replot/plot'; list.appendChild(div);
        w._fetchInteractiveFig(div, 'run:1', 'f' + i, 1, container); await pause();
    }
    const noteEl = container.querySelector('.ds-replot-run-note');
    ok(container.querySelectorAll('.ds-replot-run-note').length === 1, 'one run note');
    const text = noteEl.textContent;
    ok(/^Click-to-apply is off on these figures: /.test(text), 'the note leads with what it means: ' + text);
    ok(text.indexOf('no common verified marker transform') === -1 && text.indexOf('click-to-apply off') === -1,
       'no internal phrase is shown');
    ok(/wrote no values into the chip/.test(text) && /could not be matched to the values this run wrote/.test(text),
       'the reasons are in plain words');
    ok(!/Click a point on/.test(text), 'no pointer before a clickable figure is known');

    const lp = w.document.getElementById('lp');
    w._fetchInteractiveFig(lp, 'run:1', 'amp', 1, container); await pause();
    const badge = container.querySelector('#legacy .ds-fig-click-badge');
    ok(!!badge && badge.textContent === 'click a point to apply', 'the clickable figure carries the badge');
    ok(/Click a point on “Amplitude \+ fit” below instead\./.test(noteEl.textContent),
       'the note points to the clickable figure: ' + noteEl.textContent);

    console.log(fails ? ('FAILURES: ' + fails) : 'ALL OK');
    process.exit(fails ? 1 : 0);
})().catch(function (e) { console.error('HARNESS ERROR:', e && e.stack || e); process.exit(1); });
