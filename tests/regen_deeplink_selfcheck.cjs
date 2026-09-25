/* jsdom behavioral check for the Re-generate page's deep-link bootstrap
 * (_regenerate.html inline script, QA F19): /regenerate?step=5 -- the
 * /instrument "Modify wiring..." button -- must bring the Wiring step's rack
 * into view when the page is short, and leave the page alone otherwise.
 *
 * Two ways to run:
 *   node tests/regen_deeplink_selfcheck.cjs <rendered.html> <case>
 *     -> prints RESULT {...}; driven by test_regen_deeplink.py, which renders
 *        the fragment through Flask so the route's own Jinja step literal runs.
 *   node tests/regen_deeplink_selfcheck.cjs
 *     -> standalone (npm run selfcheck): substitutes the step literal into the
 *        raw template itself and asserts every case.
 * case = scroll | notes | tall   (the rendered page decides step 5 vs none)
 */
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('SKIP: jsdom not installed'); process.exit(2); }

process.on('uncaughtException', function (e) { console.error('UNCAUGHT:', (e && e.stack) || e); process.exit(1); });

function run(html, mode) {
  return new Promise(function (resolve) {
    const scrolled = [];
    let hydrated = null;
    const dom = new JSDOM('<!DOCTYPE html><body><div id="table-pane">' + html + '</div></body>', {
      runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/regenerate',
      beforeParse(win) {
        // 1366x768 geometry measured in real Chrome: the pane starts at 232 and
        // is 536 tall; the rack host starts at 732 (below the fold). 'tall' = a
        // window where the rack already starts in the upper half of the pane.
        win.Element.prototype.getBoundingClientRect = function () {
          const top = this.id === 'table-pane' ? 232
            : this.id === 'gen-wiring-diagram' ? (mode === 'tall' ? 420 : 732) : 0;
          const h = this.id === 'table-pane' ? 536 : 30;
          return { top: top, bottom: top + h, left: 0, right: 100, width: 100, height: h, x: 0, y: top };
        };
        win.Element.prototype.scrollIntoView = function (opt) {
          scrolled.push({ cls: String(this.className), id: this.id, block: opt && opt.block });
        };
        win.QuamGen = {
          init: function () {},
          hydrateFromSpec: function (spec, opts) { hydrated = { step: opts.step, mode: opts.mode }; },
        };
        const res = { ok: true, spec: { qubits: ['q1', 'q2'], qubit_pairs: [] }, source_name: 'chip',
                      source_folder: 'X', notes: mode === 'notes' ? ['a carried pin could bite'] : [],
                      info_notes: [] };
        win.fetch = function () {
          return win.Promise.resolve({ json: function () { return win.Promise.resolve(res); } });
        };
      },
    });
    setTimeout(function () {
      resolve({ scrolled: scrolled, hydrated: hydrated,
                status: (dom.window.document.getElementById('regen-status') || {}).textContent });
    }, 300);
  });
}

if (process.argv[2]) {
  run(fs.readFileSync(process.argv[2], 'utf8'), process.argv[3] || 'scroll').then(function (r) {
    console.log('RESULT ' + JSON.stringify(r));
    process.exit(0);
  });
} else {
  // Standalone: the raw template with the route's step literal substituted.
  const TPL = path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates');
  const raw = fs.readFileSync(path.join(TPL, '_regenerate.html'), 'utf8')
    .replace("{% include '_generate.html' %}", fs.readFileSync(path.join(TPL, '_generate.html'), 'utf8'));
  const STEP = /\{\{\s*\(regen_step if regen_step is defined else None\)\s*\|\s*tojson\s*\}\}/g;
  let fails = 0, asserts = 0;
  const ok = function (c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } };
  (async function () {
    ok(STEP.test(raw), 'the template carries the route step literal');
    const at5 = raw.replace(STEP, '5'), none = raw.replace(STEP, 'null');
    let r = await run(at5, 'scroll');
    ok(r.hydrated && r.hydrated.step === 5, 'step 5 reaches hydrateFromSpec');
    ok(r.scrolled.length === 1 && /gen-allocate-row/.test(r.scrolled[0].cls) && r.scrolled[0].block === 'start',
      'short window: the Auto-allocate row is scrolled to the pane top (' + JSON.stringify(r.scrolled) + ')');
    r = await run(at5, 'notes');
    ok(r.scrolled.length === 0 && /could bite/.test(r.status), 'a warning note is never scrolled out of sight');
    r = await run(at5, 'tall');
    ok(r.scrolled.length === 0, 'a tall window is left alone');
    r = await run(none, 'scroll');
    ok(r.scrolled.length === 0 && r.hydrated && r.hydrated.step === null, 'plain /regenerate does not scroll');
    if (fails) { console.error(fails + ' check(s) FAILED'); process.exit(1); }
    console.log('regen_deeplink_selfcheck: all checks passed (' + asserts + ' assertions)');
    process.exit(0);
  })();
}
