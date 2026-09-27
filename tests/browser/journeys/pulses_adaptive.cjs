/* docs/218 adaptive pulses -- a pulse class SM has never seen, in real Chrome.
 *
 * Rig: an SM serving a COPY of a customer chip whose selected env imports the
 * lab's own quam_config (SNZTwoFluxPulse & co.), plus a scratch package on
 * PYTHONPATH carrying a synthetic CZ class the chip declares (cz_wobble on
 * q1.z) and a second module the chip does NOT import (RampCZPulse).
 *
 * Journey: Pulses page -> open the synthetic pulse (drawn by its own class)
 * -> edit a field (the new curve, not the old one) -> open the lab's real SNZ
 * pulse -> open the create form, name the unimported module, pick the new
 * class, draw it -> reload -> page intact. Screenshots to $SHOT_DIR.
 *
 *   SM_CDP_PORT=9412 SM_PORT=5112 SHOT_DIR=... node pulses_adaptive.cjs
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const PORT = process.env.SM_PORT || 5112;
const DIR = process.env.SHOT_DIR || '.';
const BASE = `http://127.0.0.1:${PORT}`;
const out = [];
require('fs').mkdirSync(DIR, { recursive: true });
let bad = 0;
function check(c, m) { const l = (c ? 'ok   ' : 'FAIL ') + m; out.push(l); console.log(l); if (!c) bad++; }

async function waitFor(p, expr, ms = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await p.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(400);
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

const LABEL = `(document.querySelector('#pulse-detail-root .pulse-plot-label')||{}).textContent||''`;
const PLOT_N = `(function(){var el=document.getElementById('pulse-detail-plot'); return el&&el.data? el.data.map(function(t){return t.y.length}).join(','):''})()`;
const PLOT_MAX = `(function(){var el=document.getElementById('pulse-detail-plot'); if(!el||!el.data||!el.data[0]) return null; return Math.max.apply(null, el.data[0].y.filter(function(v){return v!=null}));})()`;

async function openDetail(p, path) {
  // the row carries its path; a REAL click on it opens the inspector
  const sel = `tr[data-pulse-path="${path}"] td:nth-child(4)`;
  let okc = await clickSel(p, sel);
  if (!okc) {
    await p.ev(`htmx.ajax('GET','/pulse/detail?path=${encodeURIComponent(path)}',{target:'#inspector-pane',swap:'innerHTML'})`);
  }
  return waitFor(p, `document.querySelector('#pulse-detail-root[data-pulse-path="${path}"]') ? 1 : 0`, 20000);
}

(async () => {
  const p = await open(`${BASE}/pulses?per_page=200`);
  const mark = p.events.length;
  await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 30000);
  const nRows = await p.ev(`document.querySelectorAll('tr[data-pulse-path]').length`);
  check(nRows > 10, `pulses table rendered (${nRows} rows)`);

  // 1. the synthetic class the chip declares: its own code draws it
  const WOB = 'qubits.q1.z.operations.cz_wobble';
  check(!!(await openDetail(p, WOB)), 'cz_wobble detail opened');
  // cold (no probe of the env for this module set yet): the fields are either
  // typed already or the detail SAYS it has no schema -- never silently untyped
  const cold = await p.ev(`(function(){var i=document.querySelector('#pulse-detail-root input[data-param="amplitude"]'); var k=i?i.getAttribute('data-kind'):''; var t=(document.getElementById('pulse-detail-root')||{}).textContent||''; return k==='float' ? 'typed' : (/No schema for this class yet/.test(t) ? 'honest' : 'SILENT kind=' + k);})()`);
  check(cold === 'typed' || cold === 'honest', `cold detail typed or honestly untyped -> ${cold}`);
  if (cold === 'honest') {
    await p.shot(`${DIR}/adaptive_0_cold.png`);
    // warm the probe through the create form's env strip, then reopen
    await p.ev(`htmx.ajax('GET','/pulse/new',{target:'#inspector-pane',swap:'innerHTML'})`);
    await waitFor(p, `document.querySelector('#pulse-env-strip .pulse-env-badge-ok') ? 1 : 0`, 180000);
    check(!!(await openDetail(p, WOB)), 'cz_wobble detail reopened after the probe');
  }
  const kind = await p.ev(`(document.querySelector('#pulse-detail-root input[data-param="amplitude"]')||{}).getAttribute ? document.querySelector('#pulse-detail-root input[data-param="amplitude"]').getAttribute('data-kind') : ''`);
  check(kind === 'float', `amplitude typed from the class schema (data-kind=${kind})`);
  let lbl = await waitFor(p, `/class's own code/.test(${LABEL}) && ${PLOT_N} ? ${LABEL} : ''`, 90000);
  check(!!lbl, 'cz_wobble drawn by its own class: ' + String(lbl).slice(0, 90));
  const n0 = await p.ev(PLOT_N);
  const max0 = await p.ev(PLOT_MAX);
  check(n0 === '56', `56 samples (2*8+40, rounded to 4) -> ${n0}`);
  const amp0 = +(await p.ev(`document.querySelector('#pulse-detail-root input[data-param="amplitude"]').getAttribute('data-committed')`));
  check(Math.abs(max0 - amp0 * 1.1) < 0.01, `peak = A*(1+0.1) with A=${amp0} -> ${max0}`);
  const amp1 = amp0 === 0.3 ? 0.25 : 0.3;
  await p.shot(`${DIR}/adaptive_1_wobble.png`);

  // 2. edit a field: the curve follows the NEW value, drawn by the class
  const inp = `#pulse-detail-root input[data-param="amplitude"]`;
  await clickSel(p, inp);
  await p.ev(`(function(){var i=document.querySelector('${inp}'); i.select(); return 1})()`);
  await p.send('Input.insertText', { text: String(amp1) });
  await sleep(300);
  // a real Enter (with its CR text) submits the row form like a keyboard does
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: String.fromCharCode(13) });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
  const want1 = amp1 * 1.1;
  const max1 = await waitFor(p, `(function(){var m=${PLOT_MAX}; return (m!=null && Math.abs(m-${want1})<0.005 && /class's own code/.test(${LABEL})) ? m : null})()`, 90000);
  check(!!max1, `after amplitude=${amp1} the curve peaks at ${want1.toFixed(3)} -> ${max1}`);
  await p.shot(`${DIR}/adaptive_2_wobble_edited.png`);

  // 3. the lab's REAL class (customer quam_config, KRISS_CZ env)
  const SNZ = 'qubits.q1.z.operations.cz_SNZ_flux_pulse_q1_q2';
  const snzOpen = await openDetail(p, SNZ);
  check(!!snzOpen, 'SNZ detail opened');
  lbl = await waitFor(p, `/own code|generated config/.test(${LABEL}) && ${PLOT_N} ? ${LABEL} : ''`, 120000);
  check(!!lbl, 'SNZ drawn: ' + String(lbl).slice(0, 90));
  check(/class's own code/.test(String(lbl)), 'SNZ drawn by its own class (not only the config)');
  const nS = await p.ev(PLOT_N);
  check(nS === '88', `SNZ has 88 samples (docs/189) -> ${nS}`);
  await p.shot(`${DIR}/adaptive_3_snz.png`);

  // 4. the create form: name a module the chip does not import, pick its class
  await p.ev(`htmx.ajax('GET','/pulse/new',{target:'#inspector-pane',swap:'innerHTML'})`);
  await waitFor(p, `document.getElementById('pulse-create-root') ? 1 : 0`, 20000);
  await waitFor(p, `document.querySelector('#pulse-env-strip .pulse-env-badge-ok') ? 1 : 0`, 120000);
  const hasRampBefore = await p.ev(`!!document.querySelector('#pulse-create-type option[value="RampCZPulse"]')`);
  check(hasRampBefore === false, 'RampCZPulse not offered before its module is named');
  await p.shot(`${DIR}/adaptive_4a_strip_before.png`);
  await p.ev(`(function(){var d=document.querySelector('.pulse-env-modules'); if(d) d.open=true; return 1})()`);
  await clickSel(p, '.pulse-env-module-form input[name="module"]');
  await p.send('Input.insertText', { text: 'smlab_scratch.more_pulses' });
  await clickSel(p, '.pulse-env-module-form button[type="submit"]');
  const ramp = await waitFor(p, `document.querySelector('#pulse-create-type option[value="RampCZPulse"]') ? 1 : 0`, 180000);
  check(!!ramp, 'after naming the module the form offers RampCZPulse (re-probed + form rebuilt)');
  const modOk = await waitFor(p, `(function(){var t=(document.querySelector('.pulse-env-module')||{}).textContent||''; return /imported/.test(t)? t : ''})()`, 30000) || await p.ev(`(document.getElementById('pulse-env-strip')||{}).textContent||''`);
  check(/imported/.test(modOk), 'the named module shows imported: ' + modOk.replace(/\s+/g, ' ').trim().slice(0, 80));
  await p.ev(`(function(){var s=document.getElementById('pulse-create-type'); s.value='RampCZPulse'; PulsesPage.createTypeChanged(s); return 1})()`);
  await p.ev(`(function(){var i=document.querySelector('#pulse-create-fields input[name="amplitude"]'); if(i) i.value='0.4'; return !!i})()`);
  await clickSel(p, '#pulse-create-labdraw');
  const cn = await waitFor(p, `(function(){var el=document.getElementById('pulse-create-plot'); return el&&el.data&&el.data[0]? el.data[0].y.length : 0})()`, 90000);
  check(cn === 16, `the new class drawn in the create form (16 samples) -> ${cn}`);
  const clabel = await p.ev(`(document.querySelector('#pulse-create-root .pulse-plot-label')||{}).textContent||''`);
  check(/class's own code/.test(clabel), 'create-form plot labelled as the class\'s own code');
  await p.shot(`${DIR}/adaptive_4_create.png`);
  // 4b. remove the module again: after the forced re-probe the class is gone
  // (a cached probe that imported it may not keep offering it) -- and the
  // rig is back to where it started, so the journey is re-runnable
  await p.ev(`(function(){var d=document.querySelector('.pulse-env-modules'); if(d) d.open=true; return 1})()`);
  await clickSel(p, '.pulse-env-module button[hx-post]');
  const gone = await waitFor(p, `(document.querySelector('#pulse-env-strip .pulse-env-badge-ok') && !document.querySelector('#pulse-create-type option[value="RampCZPulse"]')) ? 1 : 0`, 180000);
  check(!!gone, 'after removing the module RampCZPulse is no longer offered');
  await p.shot(`${DIR}/adaptive_4b_removed.png`);

  // 5. reload: the page comes back intact
  const pr = p.send('Page.reload');
  await sleep(800);
  await Promise.race([p.send('Page.handleJavaScriptDialog', { accept: true }), sleep(1500)]);
  await Promise.race([pr, sleep(3000)]);
  await sleep(1500);
  const again = await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 30000);
  check(again > 10, `after reload the table is back (${again} rows)`);
  const wobRow = await p.ev(`(function(){var r=document.querySelector('tr[data-pulse-path="${WOB}"] .pulse-spark-cell'); return r? r.innerHTML.length : -1})()`);
  check(wobRow > 0, 'cz_wobble row present after reload');
  const sparkTitle = await waitFor(p, `(function(){var r=document.querySelector('tr[data-pulse-path="${WOB}"] .pulse-spark-lab'); return r? r.getAttribute('title') : ''})()`, 5000);
  check(/current field values/.test(String(sparkTitle)), 'cz_wobble thumbnail drawn by its class at the current values: ' + String(sparkTitle).slice(0, 60));
  await p.shot(`${DIR}/adaptive_5_reload.png`);
  const errs = p.errors(mark);
  check(errs.length === 0, 'no JS errors (' + errs.join(' | ').slice(0, 300) + ')');
  await p.close();
  console.log(bad ? `${bad} FAILED` : 'ALL OK');
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
