/* D-1/D-2 (docs/305): the Interactive menu answers before the replay finishes and
 * app.js polls the background job. Pinned under jsdom with the REAL app.js:
 * the phase and the server's elapsed seconds are shown, never a percentage; the
 * saved "(static)" figures stay on screen during the wait; the poll stops on a newer
 * load of the container, on a detached strip, and when the job ends; on done the menu
 * is fetched again and the strip is gone.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js');
const HTML = `<!doctype html><html><body>
  <div id="inspector-pane"><div class="dataset-tab-content" id="ds-tab-interactive">
    <div id="ds-interactive-container" data-run-id="u1" data-loaded="0"></div>
  </div></div>
</body></html>`;

const STRIP = `<div class="ds-replot-head muted" role="status" data-replot-job="J" data-run-id="u1">
  <span aria-busy="true"></span>
  <span>Reproducing the figures from the experiment code: <span data-role="phase">starting</span>
  &middot; <span data-role="elapsed">0</span> s</span>
  <span data-role="slow" hidden>still working &mdash; the saved figures below are complete</span></div>
  <div class="ds-interactive-list"><div class="ds-interactive-fig" data-fig="fit">
  <div class="ds-interactive-fig-title">fit <small class="muted">(static)</small></div>
  <img src="/dataset/u1/fig/fit"></div></div>`;
const DONE = `<div class="ds-replot-head" data-replot-state="ok"><span class="ds-replot-title">Reproduced</span></div>`;

let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log('  ok  ' + name);
  else { failures++; console.error('FAIL  ' + name + (detail ? ' -- ' + detail : '')); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function world() {
  const dom = new JSDOM(HTML, { runScripts: 'outside-only', url: 'http://localhost/' });
  const w = dom.window;
  const log = { menu: 0, status: 0 };
  const st = { status: { state: 'replaying', phase: 'opening datasets', label: 'opening datasets', elapsed: 2 }, menu: [STRIP, DONE] };
  w.fetch = function (url) {
    const body = (text) => Promise.resolve({ text: () => Promise.resolve(text), json: () => Promise.resolve(JSON.parse(text)) });
    if (/\/interactive\/status/.test(url)) { log.status++; return body(JSON.stringify(st.status)); }
    if (/\/interactive$/.test(url)) { const html = st.menu[Math.min(log.menu, st.menu.length - 1)]; log.menu++; return body(html); }
    return new Promise(function () {});
  };
  w.matchMedia = w.matchMedia || function () { return { matches: false, addListener() {}, removeListener() {} }; };
  w.requestAnimationFrame = w.requestAnimationFrame || function (f) { return setTimeout(f, 0); };
  global.window = w; global.document = w.document;
  w.eval(fs.readFileSync(SRC, 'utf8'));
  w.eval('INTERACTIVE_POLL_MS = 15;');
  return { w, log, st, c: w.document.getElementById('ds-interactive-container') };
}

(async function main() {
  // ── A: phases, elapsed, the static figures, then done ──────────────────
  {
    const { w, log, st, c } = world();
    w.loadDatasetInteractive('u1', w.document.getElementById('inspector-pane'));
    await sleep(60);
    const strip = c.querySelector('[data-replot-job]');
    check('A1 the first answer paints the strip', !!strip);
    check('A2 the saved figures are on screen during the wait', c.textContent.indexOf('(static)') !== -1);
    check('A3 the phase is the server\'s', strip && strip.querySelector('[data-role="phase"]').textContent === 'opening datasets');
    check('A4 elapsed is the server\'s seconds', strip && strip.querySelector('[data-role="elapsed"]').textContent === '2');
    check('A5 no percentage anywhere in the strip', strip && strip.textContent.indexOf('%') === -1);
    check('A6 the slow note waits for 30 s', strip && strip.querySelector('[data-role="slow"]').hidden === true);
    st.status = { state: 'replaying', phase: 'drawing figure', label: 'drawing figure 2 of 3 (fit)', elapsed: 31.4 };
    await sleep(60);
    check('A7 the phase advances', strip.querySelector('[data-role="phase"]').textContent === 'drawing figure 2 of 3 (fit)');
    check('A8 past 30 s the slow note shows', strip.querySelector('[data-role="slow"]').hidden === false);
    check('A9 still no percentage', strip.textContent.indexOf('%') === -1);
    st.status = { state: 'done', phase: 'done', elapsed: 33 };
    await sleep(80);
    check('A10 on done the menu is fetched again', log.menu === 2, 'menu fetches ' + log.menu);
    check('A11 and the strip is gone', !c.querySelector('[data-replot-job]'));
    const n = log.status;
    await sleep(80);
    check('A12 the poll stopped', log.status === n, n + ' -> ' + log.status);
  }
  // ── B: a newer load of the container (gen bump) stops the poll ─────────
  {
    const { w, log, c } = world();
    w.loadDatasetInteractive('u1', w.document.getElementById('inspector-pane'));
    await sleep(50);
    check('B1 polling', log.status > 0);
    c._gen += 1;
    const n = log.status;
    await sleep(80);
    check('B2 a gen bump stops the poll', log.status <= n + 1, n + ' -> ' + log.status);
  }
  // ── C: a detached strip (navigated away) stops the poll ───────────────
  {
    const { w, log, c } = world();
    w.loadDatasetInteractive('u1', w.document.getElementById('inspector-pane'));
    await sleep(50);
    c.parentNode.removeChild(c);
    const n = log.status;
    await sleep(80);
    check('C1 a detached strip stops the poll', log.status <= n + 1, n + ' -> ' + log.status);
    check('C2 and nothing re-fetched the menu', log.menu === 1);
  }
  // ── D: a failed job also re-fetches (the failed header + Retry render there) ──
  {
    const { w, log, st, c } = world();
    st.status = { state: 'failed', phase: 'failed', elapsed: 4 };
    w.loadDatasetInteractive('u1', w.document.getElementById('inspector-pane'));
    await sleep(80);
    check('D1 on failed the menu is fetched again', log.menu === 2);
    check('D2 and the strip is gone', !c.querySelector('[data-replot-job]'));
  }
  if (failures) { console.error(failures + ' failure(s)'); process.exit(1); }
  console.log('interactive progress selfcheck: all ok');
  process.exit(0);
})();
