// Recommend core layout: the preview opens first, Stage stays disabled until
// the Auto-Sync note is read (and stays disabled with one honest sentence when
// it cannot be read), Cancel posts nothing, Stage hands the items to
// applyDiagFixAll and then re-reads the findings into every surface.
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
let asserts = 0;
function ok(c, m) { asserts++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function wait(ms) { return new Promise((r) => setTimeout(r, ms || 0)); }
// Wait until cond() holds, polling every 2 ms, capped at 2 s; false at the cap.
async function until(cond, capMs) {
  const end = Date.now() + (capMs || 2000);
  while (Date.now() < end) { if (cond()) return true; await wait(2); }
  return cond();
}

const dom = new JSDOM('<!doctype html><html><body><div id="host"></div><div id="tiles"></div></body></html>',
  { url: 'http://localhost/topology', pretendToBeVisual: true, runScripts: 'outside-only' });
const { window } = dom;

const FIXES = [{ action: 'set_value', dot_path: 'qubits.q1.xy.core', value: 'q1_core' },
  { action: 'set_value', dot_path: 'qubits.q1.xy.thread', value: 'null' }];
const PREVIEW = {
  controller: 'con1', fem: 1, button: true, panel_line: null,
  cores: { limit: 8, before: 10, after: 5, firm_after: 5, estimated: false },
  groups_after: [{ core: 'q1_core', elements: ['qubits.q1.xy', 'qubits.q1.resonator'], cost: 1 }],
  changes: [{ element: 'qubits.q1.xy', key: 'core', before: null, after: 'q1_core' }],
  kept: [{ element: 'qubits.q1.extra', note: "keeps 'x'; no longer shares with qubits.q1.xy" }],
  unresolved: [{ element: 'qubits.q7.xy', field: 'opx_output', reason: 'dangling reference' }],
};
const FINDING = { category: 'core_layout', message: '10 / 8 cores on con1 FEM 1', fixes: FIXES, preview: PREVIEW };

// In-realm mocks: the code calls bare fetch / applyDiagFixAll.
window.eval(`
  window.__posts = []; window.__applied = []; window.__noteMode = 'armed'; window.__findingsAfter = [];
  window.fetch = function (url, init) {
    if (init && init.method === 'POST') window.__posts.push(url);
    if (url.indexOf('/diagnostics/auto-push-note') === 0) {
      if (window.__noteMode === 'fail') return Promise.resolve({ ok: false, status: 500, json: function () { return Promise.resolve({ ok: false }); } });
      return Promise.resolve({ ok: true, status: 200, json: function () {
        return Promise.resolve({ ok: true, note: window.__noteMode === 'armed' ? 'Auto-Sync (push) is ARMED, so this writes the chip.' : '' }); } });
    }
    if (url.indexOf('/diagnostics/core-findings') === 0) {
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ ok: true, findings: window.__findingsAfter }); } });
    }
    return Promise.reject(new Error('unexpected ' + url));
  };
  window.applyDiagFixAll = function (btn) { window.__applied.push(JSON.parse(btn.getAttribute('data-items'))); return Promise.resolve({ ok: true }); };
`);
window.eval(fs.readFileSync(path.join(STATIC, 'load_check.js'), 'utf8'));
const CR = window.CoreRecommend;

(async () => {
  ok(CR && typeof CR.html === 'function', 'CoreRecommend is exported');
  ok(CR.html({ preview: { button: false, panel_line: 'No lower core layout to recommend on con1 FEM 1; move elements off that FEM.' } })
    .indexOf('No lower core layout to recommend on con1 FEM 1') >= 0 && CR.html({ preview: { button: false } }) === '',
  'no button: the panel line only');

  const tiles = window.document.getElementById('tiles');
  const slot = CR.mountAfter(tiles, [FINDING]);
  ok(slot && slot.previousSibling === tiles, 'slot mounted right after the anchor');
  const box = slot.querySelector('[data-core-rec]');
  const pv = box.querySelector('[data-core-rec-preview]');
  const apply = box.querySelector('[data-core-rec-apply]');
  const note = box.querySelector('[data-core-rec-note]');
  ok(pv.hidden && apply.disabled, 'preview hidden and Stage disabled before the press');
  ok(!/style=/.test(slot.innerHTML) && slot.innerHTML.indexOf(' -- ') < 0, 'no inline style, no double dash');
  ok(pv.querySelector('[data-core-rec-counts]').textContent === 'Cores on con1 FEM 1: 10 / 8 now, 5 / 8 after.', 'counts line');
  ok(pv.querySelector('[data-core-rec-unresolved]').textContent.indexOf('qubits.q7.xy.opx_output') === 0, 'unresolved port named');
  ok(pv.querySelector('[data-core-rec-kept]') !== null, 'kept element listed');

  box.querySelector('[data-core-rec-open]').click();
  ok(!pv.hidden && apply.disabled, 'open: preview shown, Stage still disabled while the note loads');
  await until(() => !apply.disabled);
  ok(!apply.disabled && !note.hidden && note.textContent.indexOf('ARMED') >= 0, 'armed note shown, Stage enabled');

  box.querySelector('[data-core-rec-cancel]').click();
  ok(pv.hidden && apply.disabled && window.__posts.length === 0 && window.__applied.length === 0, 'cancel posts nothing');

  window.__noteMode = 'fail';
  box.querySelector('[data-core-rec-open]').click();
  await until(() => note.textContent === CR.NOTE_FAILED);
  ok(apply.disabled && note.textContent === 'Could not read the Auto-Sync state; try again.', 'note failure keeps Stage disabled');
  apply.click();
  // Negative check: nothing may happen, so a bounded wait (not a settle) is the only option.
  await wait(50);
  ok(window.__applied.length === 0, 'a disabled Stage applies nothing');

  window.__noteMode = 'idle';
  box.querySelector('[data-core-rec-open]').click();
  await until(() => !apply.disabled);
  ok(!apply.disabled && note.hidden, 'no note when Auto-Sync is idle');
  let seen = null;
  CR.onApplied((list) => { seen = list; });
  apply.click();
  await until(() => seen !== null);
  ok(window.__applied.length === 1 && JSON.stringify(window.__applied[0]) === JSON.stringify(FIXES), 'Stage hands the exact items to applyDiagFixAll');
  ok(Array.isArray(seen) && seen.length === 0, 'findings re-read after the apply');
  ok(window.document.querySelector('[data-core-rec-slot]').innerHTML === '', 'slot re-rendered from the new findings');

  console.log(`(${asserts} assertions)`);
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
