/* docs/217 pulse locations -- pulses a user hand-added where SM never looked.
 *
 * Rig: an SM serving a COPY of lab-F whose LIVE state.json had three
 * pulses hand-added AFTER the chip was loaded (scratch handadd.py):
 *   qubits.q1.xy2.operations.x180_hand            -- a new channel
 *   qubit_pairs.q1-2.macros.cz_SNZ.flux_pulse_extra -- a new macro slot, the
 *                                                   lab's GaussianNZTwoFluxPulse
 *   twpas.twpa1.pump.operations.pump_hand         -- a TWPA pump op
 *
 * Journey: Take live -> the rows appear (found-at label, "Other places" tab)
 * -> open each by a real click -> type into a field + Enter (commit, the plot
 * follows) -> duplicate / rename / delete in the new operations dict ->
 * away + back, reload -> intact, no JS errors. Screenshots to $SHOT_DIR.
 *
 *   SM_CDP_PORT=9414 SM_PORT=5114 SHOT_DIR=... node pulses_locations.cjs
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const PORT = process.env.SM_PORT || 5114;
const DIR = process.env.SHOT_DIR || '.';
const BASE = `http://127.0.0.1:${PORT}`;
require('fs').mkdirSync(DIR, { recursive: true });
let bad = 0;
function check(c, m) { console.log((c ? 'ok   ' : 'FAIL ') + m); if (!c) bad++; }

const XY2 = 'qubits.q1.xy2.operations.x180_hand';
const SLOT = 'qubit_pairs.q1-2.macros.cz_SNZ.flux_pulse_extra';
const PUMP = 'twpas.twpa1.pump.operations.pump_hand';

async function waitFor(p, expr, ms = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await p.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(300);
  }
  return null;
}
async function clickSel(p, sel) {
  const r = await p.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return null; e.scrollIntoView({block:'center'}); var b=e.getBoundingClientRect(); return [b.left+b.width/2,b.top+b.height/2];})()`);
  if (!r) return false;
  await sleep(150);
  await p.click(r[0], r[1]);
  return true;
}
const rowSel = path => `tr[data-pulse-path="${path}"]`;
const PLOT_MAX = `(function(){var el=document.getElementById('pulse-detail-plot'); if(!el||!el.data||!el.data[0]) return null; return Math.max.apply(null, el.data[0].y.filter(function(v){return v!=null}));})()`;
// wait for THIS value: a restored / previous detail's plot can still be on
// screen for a beat after the new detail root lands
const PEAK_IS = v => `(function(){var m=${PLOT_MAX}; return (m!=null && Math.abs(m-${v})<1e-6) ? m : null})()`;
const LABEL = `((document.querySelector('#pulse-detail-root .pulse-plot-label')||{}).textContent||'').replace(/\\s+/g,' ').trim()`;

async function openDetail(p, path) {
  const okc = await clickSel(p, `${rowSel(path)} td:nth-child(4)`);
  if (!okc) return null;
  return waitFor(p, `document.querySelector('#pulse-detail-root[data-pulse-path="${path}"]') ? 1 : 0`, 20000);
}
async function typeEnter(p, sel, text) {
  await clickSel(p, sel);
  await p.ev(`(function(){var i=document.querySelector('${sel}'); i.select(); return 1})()`);
  await p.send('Input.insertText', { text });
  await sleep(200);
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
}
// leaving with unapplied edits raises SM's beforeunload prompt: accept it the
// way a user presses "Leave" (the edits stay in the working copy)
async function go(p, cmd, params) {
  const pr = p.send(cmd, params || {});
  await sleep(800);
  await Promise.race([p.send('Page.handleJavaScriptDialog', { accept: true }), sleep(1500)]);
  await Promise.race([pr, sleep(4000)]);
  await sleep(1200);
}
const reload = p => go(p, 'Page.reload');

(async () => {
  const p = await open(`${BASE}/pulses?per_page=0`);
  const mark = p.events.length;
  await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 30000);
  const before = await p.ev(`!!document.querySelector('${rowSel(XY2)}')`);
  // only meaningful on a fresh rig (a re-run's working copy already has them)
  console.log(`info before taking live, x180_hand row shown: ${before}`);

  // 0. the user's own path: take the live file they edited
  const st = await p.ev(`fetch('/state/sync',{method:'POST',headers:{'HX-Request':'true','Content-Type':'application/x-www-form-urlencoded'},body:'mode=discard'}).then(r=>r.json()).then(j=>j.status)`);
  check(st === 'ok', `take live -> ${st}`);
  await reload(p);
  const n = await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 30000);
  console.log('info after take live: rows=' + n + ' url=' + await p.ev('location.href') + ' tab=' + await p.ev(`((document.querySelector('nav a.active')||{}).textContent||'').trim()`) + ' q=' + await p.ev(`(document.querySelector('[name=q]')||{}).value`));
  for (const path of [XY2, SLOT, PUMP]) {
    const lab = await p.ev(`(function(){var r=document.querySelector('${rowSel(path)} .pulse-found-at'); return r? r.textContent.replace(/\\s+/g,' ').trim() : ''})()`);
    check(/^found at /.test(lab), `row ${path} present, labelled "${lab}"`);
  }
  check(await p.ev(`[...document.querySelectorAll('.pulses-tabs a, nav a')].some(a=>a.textContent.trim()==='Other places')`), `"Other places" tab present (${n} rows total)`);
  await clickSel(p, rowSel(XY2));
  await p.shot(`${DIR}/loc_1_list.png`);

  // 1. new channel, catalog class: detail, synth plot, commit a field
  check(!!(await openDetail(p, XY2)), 'x180_hand detail opened by a real click');
  check(/Found at/.test(await p.ev(`document.getElementById('pulse-detail-root').textContent`)), 'detail says where it was found');
  const m0 = await waitFor(p, PEAK_IS(0.1), 20000) || await p.ev(PLOT_MAX);
  check(Math.abs(m0 - 0.1) < 1e-6, `plot drawn, peak = stored amplitude 0.1 -> ${m0}`);
  await typeEnter(p, '#pulse-detail-root input[data-param="amplitude"]', '0.15');
  const m1 = await waitFor(p, `(function(){var m=${PLOT_MAX}; return (m!=null && Math.abs(m-0.15)<1e-6)? m : null})()`, 20000);
  check(!!m1, `after typing 0.15 + Enter the plot peaks at 0.15 -> ${m1}`);
  const committed = await waitFor(p, `(function(){var i=document.querySelector('#pulse-detail-root input[data-param="amplitude"]'); return i && i.getAttribute('data-committed')==='0.15' ? 1 : 0})()`, 10000);
  check(!!committed, 'field committed (data-committed=0.15)');
  await p.shot(`${DIR}/loc_2_xy2_edited.png`);

  // 2. new macro slot, the lab's own class: drawn by its code/config, or an honest note
  check(!!(await openDetail(p, SLOT)), 'flux_pulse_extra detail opened');
  const renameBtn = await p.ev(`[...document.querySelectorAll('#pulse-detail-root button')].some(b=>b.textContent.trim()==='Rename')`);
  check(renameBtn === false, 'a macro slot offers no Rename (schema field)');
  const drawn = await waitFor(p, `(function(){var l=${LABEL}; var t=document.getElementById('pulse-detail-root').textContent; if (/own code|generated config/.test(l) && ${PLOT_MAX}!=null) return 'drawn: '+l; if (/preview is unavailable|No schema|could not|not drawn|failed/i.test(t)) return 'honest'; return '';})()`, 120000);
  check(!!drawn, `lab class: ${String(drawn).slice(0, 110)}`);
  const ampSel = '#pulse-detail-root input[data-param="amplitude"]';
  const hasAmp = await p.ev(`!!document.querySelector('${ampSel}')`);
  if (hasAmp) {
    await typeEnter(p, ampSel, '0.21');
    const c2 = await waitFor(p, `(function(){var i=document.querySelector('${ampSel}'); return i && Math.abs(+i.getAttribute('data-committed')-0.21)<1e-9 ? 1 : 0})()`, 20000);
    check(!!c2, 'slot amplitude committed 0.21');
  } else check(false, 'slot amplitude input present');
  await sleep(1500);
  await p.shot(`${DIR}/loc_3_slot_labclass.png`);

  // 3. TWPA pump op
  check(!!(await openDetail(p, PUMP)), 'pump_hand detail opened');
  const m2 = await waitFor(p, PEAK_IS(0.25), 20000) || await p.ev(PLOT_MAX);
  check(Math.abs(m2 - 0.25) < 1e-6, `pump plot peak 0.25 -> ${m2}`);
  await p.shot(`${DIR}/loc_4_pump.png`);

  // 4. duplicate / rename / delete inside the new operations dict
  await openDetail(p, XY2);
  await clickSel(p, '#pulse-detail-root button[onclick*="startDuplicate"]');
  await p.ev(`(function(){var i=document.querySelector('.pulse-duplicate-form input[name=new_name]'); i.value='x180_hand_copy'; return 1})()`);
  await clickSel(p, '.pulse-duplicate-form button[type=submit]');
  const COPY = 'qubits.q1.xy2.operations.x180_hand_copy';
  check(!!(await waitFor(p, `document.querySelector('#pulse-detail-root[data-pulse-path="${COPY}"]') && document.querySelector('${rowSel(COPY)}') ? 1 : 0`, 20000)), 'duplicate: detail + table row for x180_hand_copy');
  await clickSel(p, '#pulse-detail-root button[onclick*="startRename"]');
  await p.ev(`(function(){var i=document.querySelector('.pulse-rename-form input[name=new_name]'); i.value='x180_hand_ren'; return 1})()`);
  await clickSel(p, '.pulse-rename-form button[type=submit]');
  const REN = 'qubits.q1.xy2.operations.x180_hand_ren';
  check(!!(await waitFor(p, `document.querySelector('${rowSel(REN)}') && !document.querySelector('${rowSel(COPY)}') ? 1 : 0`, 20000)), 'rename: row x180_hand_ren replaces x180_hand_copy');
  await p.shot(`${DIR}/loc_5_renamed.png`);
  await clickSel(p, '#pulse-detail-root .pulse-delete-btn');
  await clickSel(p, '.pulse-delete-confirm button[type=submit]');
  check(!!(await waitFor(p, `!document.querySelector('${rowSel(REN)}') ? 1 : 0`, 20000)), 'delete: x180_hand_ren row gone');

  // 5. the tab, then away + back, then reload
  await clickSel(p, 'a[href^="/pulses?channel=found"]');
  const onlyFound = await waitFor(p, `(function(){var rs=[...document.querySelectorAll('tr[data-pulse-path]')]; return rs.length && rs.every(r=>r.querySelector('.pulse-found-at')) ? rs.length : 0})()`, 20000);
  check(!!onlyFound, `"Other places" tab lists only found rows (${onlyFound})`);
  await p.shot(`${DIR}/loc_6_tab.png`);
  await go(p, 'Page.navigate', { url: `${BASE}/bulk` });
  check(await waitFor(p, `document.querySelectorAll('.bulk-cell').length`, 30000) > 0, 'navigated away to Live Edit');
  await go(p, 'Page.navigate', { url: `${BASE}/pulses?per_page=0` });
  await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 30000);
  check(await p.ev(`!!document.querySelector('${rowSel(XY2)}') && !!document.querySelector('${rowSel(PUMP)}')`), 'away (Live Edit) and back: rows intact');
  await reload(p);
  await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 30000);
  check(await p.ev(`!!document.querySelector('${rowSel(SLOT)}') && !document.querySelector('${rowSel(REN)}')`), 'after reload: rows intact, deleted row stays gone');
  check(!!(await openDetail(p, XY2)), 'after reload the found pulse still opens');
  const m3 = await waitFor(p, PEAK_IS(0.15), 20000) || await p.ev(PLOT_MAX);
  check(Math.abs(m3 - 0.15) < 1e-6, `the committed 0.15 survived reload -> ${m3}`);
  await p.shot(`${DIR}/loc_7_reload.png`);
  // Chrome's own note that it suppressed the beforeunload prompt of a frame
  // with no gesture is browser policy, not an SM error
  const errs = p.errors(mark).filter(e => !/Blocked attempt to show a 'beforeunload'/.test(e));
  check(errs.length === 0, 'no JS errors (' + errs.join(' | ').slice(0, 300) + ')');
  await p.close();
  console.log(bad ? `${bad} FAILED` : 'ALL OK');
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
