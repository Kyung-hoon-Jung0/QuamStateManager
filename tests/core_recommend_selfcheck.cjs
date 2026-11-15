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
      window.__reads = (window.__reads || 0) + 1;
      if (window.__coreMode === 'reject') return Promise.reject(new Error('network'));
      if (window.__coreMode === '500') return Promise.resolve({ ok: false, status: 500, json: function () { return Promise.resolve({ ok: true, findings: [] }); } });
      if (window.__coreMode === 'nobody') return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ ok: false }); } });
      if (window.__coreMode === 'badjson') return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.reject(new SyntaxError('bad json')); } });
      if (window.__defer) return new Promise(function (res) { window.__pending.push(function (f) {
        if (f === 'FAIL') return res({ ok: false, status: 500, json: function () { return Promise.resolve({ ok: false }); } });
        res({ ok: true, status: 200, json: function () { return Promise.resolve({ ok: true, findings: f }); } }); }); });
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ ok: true, findings: window.__findingsAfter }); } });
    }
    return Promise.reject(new Error('unexpected ' + url));
  };
  window.applyDiagFixAll = function (btn) { if (window.__fireOnApply) document.dispatchEvent(new CustomEvent('diagnostics-changed')); window.__applied.push(JSON.parse(btn.getAttribute('data-items'))); return Promise.resolve({ ok: true }); };
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
  window.__fireOnApply = true;
  apply.click();
  await until(() => seen !== null);
  ok(window.__applied.length === 1 && JSON.stringify(window.__applied[0]) === JSON.stringify(FIXES), 'Stage hands the exact items to applyDiagFixAll');
  ok(Array.isArray(seen) && seen.length === 0, 'findings re-read after the apply');
  ok(window.document.querySelector('[data-core-rec-slot]').innerHTML === '', 'slot re-rendered from the new findings');
  window.__fireOnApply = false;
  await wait(20);
  ok(window.document.querySelector('[data-core-rec-reread]') === null, 'own Stage press: its event shows no re-read line');

  // Events: two in quick succession, answers held back. One request at a time;
  // the second read follows the first, so the newest answer lands last.
  CR.mountAfter(tiles, [FINDING]);
  window.__pending = []; window.__defer = true; window.__reads = 0;
  const fire = () => window.document.dispatchEvent(new window.CustomEvent('diagnostics-changed'));
  const slotNow = () => window.document.querySelector('[data-core-rec-slot]');
  fire(); fire();
  await until(() => window.__pending.length === 1);
  ok(window.__reads === 1, 'two quick events: one request in flight, not two');
  window.__pending.shift()([]);            // the older answer: no finding
  await until(() => window.__pending.length === 1);
  ok(window.__reads === 2, 'the second event reads again after the first answer');
  window.__pending.shift()([FINDING]);     // the newer answer: the finding is back
  await until(() => slotNow().querySelector('[data-core-rec-open]') !== null);
  ok(slotNow().querySelector('[data-core-rec-open]') !== null, 'the newest answer is the one shown');
  await wait(20);
  ok(window.__reads === 2 && slotNow().querySelector('[data-core-rec-open]') !== null, 'no extra read, the older answer does not win');

  // Preview open when the event fires: closed by the re-read, with one honest line.
  window.__defer = false; window.__noteMode = 'idle';
  const box2 = slotNow().querySelector('[data-core-rec]');
  box2.querySelector('[data-core-rec-open]').click();
  await until(() => !box2.querySelector('[data-core-rec-apply]').disabled);
  const CHANGED = JSON.parse(JSON.stringify(FINDING));
  CHANGED.preview.cores.after = 6;
  window.__findingsAfter = [CHANGED];
  fire();
  await until(() => slotNow().querySelector('[data-core-rec-reread]') !== null);
  const rr = slotNow().querySelector('[data-core-rec-reread]');
  ok(rr && rr.textContent === CR.REREAD_LINE, 'open preview: the honest re-read line is shown');
  const pv2 = slotNow().querySelector('[data-core-rec-preview]');
  ok(pv2.hidden && slotNow().querySelector('[data-core-rec-apply]').disabled, 'open preview: closed, Stage disabled, never swapped silently');
  ok(pv2.querySelector('[data-core-rec-counts]').textContent.indexOf('6 / 8 after') >= 0, 'the re-read numbers are the new ones');
  fire();
  await until(() => slotNow().querySelector('[data-core-rec-reread]') === null);
  ok(slotNow().querySelector('[data-core-rec-reread]') === null, 'no open preview: no re-read line');

  // Per-slot line: two slots, only the one with an open preview gets the line.
  const LC = Object.assign({ line: 'Cores on con1 FEM 1: 10 / 8' }, FINDING);
  const host = window.document.getElementById('host');
  const lcHtml = () => '<div data-loadcheck-core><div class="ui-callout">' + CR.html(LC) + '</div></div>';
  const lcSlot = () => host.querySelector('[data-loadcheck-core]');
  const allRR = () => window.document.querySelectorAll('[data-core-rec-reread]');
  const openIn = async (slotEl) => {
    const b = slotEl.querySelector('[data-core-rec]');
    b.querySelector('[data-core-rec-open]').click();
    await until(() => !b.querySelector('[data-core-rec-apply]').disabled);
    return b;
  };
  host.innerHTML = lcHtml();
  window.__findingsAfter = [LC];
  await openIn(slotNow());
  fire();
  await until(() => allRR().length > 0);
  await wait(20);
  ok(allRR().length === 1 && slotNow().querySelector('[data-core-rec-reread]') !== null, 'two slots, one open: exactly one line, in the open slot');
  ok(lcSlot().querySelector('[data-core-rec-reread]') === null, 'the slot that held no preview gets no line');

  // Open in the Load check slot, the last finding removed: one plain line.
  CR.mountAfter(tiles, [LC]); host.innerHTML = lcHtml();
  await openIn(lcSlot());
  window.__findingsAfter = [];
  fire();
  await until(() => allRR().length > 0);
  await wait(20);
  ok(allRR().length === 1 && lcSlot().querySelector('[data-core-rec-reread]') !== null, 'last finding removed: one line, in the open slot only');
  ok(allRR()[0].textContent === CR.REREAD_NONE && CR.REREAD_NONE.indexOf('no core layout finding remains') >= 0, 'last finding removed: the line says no finding remains');
  ok(lcSlot().querySelector('[data-core-rec]') === null, 'last finding removed: no recommendation left to point at');

  // Both slots open at once (a harness case; the two slots live on different pages): one line each.
  CR.mountAfter(tiles, [LC]); host.innerHTML = lcHtml();
  window.__findingsAfter = [LC];
  await openIn(slotNow()); await openIn(lcSlot());
  fire();
  await until(() => allRR().length === 2);
  await wait(20);
  ok(allRR().length === 2 && slotNow().querySelectorAll('[data-core-rec-reread]').length === 1
    && lcSlot().querySelectorAll('[data-core-rec-reread]').length === 1, 'both open: one line per open slot');

  // Failed re-reads: no line, the open preview untouched; the next good read shows it once.
  host.innerHTML = '';
  for (const mode of ['reject', '500', 'badjson', 'nobody']) {
    CR.mountAfter(tiles, [FINDING]);
    const b = await openIn(slotNow());
    window.__coreMode = mode;
    const r1 = window.__reads;
    fire();
    await until(() => window.__reads > r1);
    await wait(30);
    ok(allRR().length === 0, mode + ': a failed re-read shows no line');
    ok(b.isConnected && !b.querySelector('[data-core-rec-preview]').hidden && !b.querySelector('[data-core-rec-apply]').disabled,
      mode + ': the open preview is left untouched');
  }
  window.__coreMode = null;
  window.__findingsAfter = [FINDING];
  fire();
  await until(() => allRR().length > 0);
  await wait(20);
  ok(allRR().length === 1 && allRR()[0].textContent === CR.REREAD_LINE, 'fail then success: the line appears then, once');

  // Two events in a row with the preview open: still one line.
  CR.mountAfter(tiles, [FINDING]);
  await openIn(slotNow());
  window.__pending = []; window.__defer = true;
  fire(); fire();
  await until(() => window.__pending.length === 1);
  window.__pending.shift()([FINDING]);
  await until(() => window.__pending.length === 1);
  window.__pending.shift()([FINDING]);
  await until(() => allRR().length > 0);
  await wait(30);
  ok(allRR().length === 1, 'two events in a row: one line, not two');
  window.__defer = false;

  // The event x read-outcome table (one row per case; the slot is re-rendered
  // only by a read that succeeds, and every read settles before the next row).
  window.__pending = []; window.__defer = true;
  const pvOpen = () => { const b = slotNow().querySelector('[data-core-rec]'); return !!b && !b.querySelector('[data-core-rec-preview]').hidden; };
  const settle = async (n) => { await until(() => window.__pending.length === n); };
  const rows = [
    { name: 'two events (ok, fail)', seq: [[FINDING], 'FAIL'], lines: 1, open: false },
    { name: 'two events (fail, ok)', seq: ['FAIL', [FINDING]], lines: 1, open: false },
    { name: 'two events (fail, fail)', seq: ['FAIL', 'FAIL'], lines: 0, open: true },
  ];
  for (const row of rows) {
    CR.mountAfter(tiles, [FINDING]);
    await openIn(slotNow());
    const r1 = window.__reads;
    fire(); fire();
    await settle(1);
    window.__pending.shift()(row.seq[0]);
    await settle(1);
    window.__pending.shift()(row.seq[1]);
    await until(() => window.__reads === r1 + 2 && window.__pending.length === 0
      && (row.lines ? allRR().length === row.lines : true));
    await wait(30);
    ok(allRR().length === row.lines && pvOpen() === row.open, row.name + ': ' + row.lines + ' line(s), preview ' + (row.open ? 'open' : 'closed'));
  }
  // Three events, the failure in the middle: (ok, fail) chained after a first fail.
  CR.mountAfter(tiles, [FINDING]);
  await openIn(slotNow());
  fire(); fire();
  await settle(1); window.__pending.shift()('FAIL');
  await settle(1); fire(); window.__pending.shift()([FINDING]);
  await settle(1); window.__pending.shift()('FAIL');
  await until(() => allRR().length === 1);
  await wait(30);
  ok(allRR().length === 1 && !pvOpen(), 'three events (fail, ok, fail): one line, preview closed');
  // Preview reopened after the first re-render, before a failing second read
  // (a third event lands while it is open): one line, the reopened preview stays.
  CR.mountAfter(tiles, [FINDING]);
  await openIn(slotNow());
  fire(); fire();
  await settle(1); window.__pending.shift()([FINDING]);
  await settle(1);
  await openIn(slotNow());
  fire();
  window.__pending.shift()('FAIL');
  await settle(1); window.__pending.shift()('FAIL');
  await until(() => allRR().length === 1);
  await wait(30);
  ok(allRR().length === 1 && pvOpen(), 'reopened before a failing read: one line, the reopened preview stays open');
  // Slot swapped off the page between the event and the read: no throw, no line on the new page.
  CR.mountAfter(tiles, [FINDING]);
  await openIn(slotNow());
  fire();
  await settle(1);
  const gone = slotNow(); gone.remove();
  CR.mountAfter(tiles, [FINDING]);
  window.__pending.shift()([FINDING]);
  await until(() => gone.querySelector('[data-core-rec-reread]') !== null);
  ok(allRR().length === 0 && slotNow() !== gone, 'slot swapped before the read: no line on the new page');
  window.__defer = false;

  // Slot gone: the event sends nothing.
  slotNow().remove();
  const r0 = window.__reads;
  fire();
  await wait(50);
  ok(window.__reads === r0, 'no slot: the event sends no request');

  console.log(`(${asserts} assertions)`);
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
