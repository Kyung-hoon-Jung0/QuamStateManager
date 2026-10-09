/* w8 pulsehint -- "Don't see your pulse class?" under the create form's class
 * list, in real headless Chrome over CDP, on a rig serving a COPY of the lab-F
 * 5Q chip whose server has a scratch lab package on PYTHONPATH (the env probe
 * subprocess inherits it; never a customer folder).
 *
 * Journey: Pulses -> + New pulse -> the line under the class list -> click it
 * (the env strip's module box opens, caret in its input) -> type a real
 * importable module -> Add module -> the probe lands and the class list shows
 * the new class WITHOUT reopening the form -> create a pulse of it -> reload
 * -> the pulse is there and the form still offers the class. Then a module
 * that does not import: the line says "✗ <module>: <error>". Cleanup removes
 * the named modules so the journey is re-runnable (A/B interleaving).
 *
 * MODE=a runs the same press on the base build (no line: the box is opened by
 * its own summary) and only measures; MODE=b checks everything.
 *
 *   SM_CDP_PORT=9626 SM_PORT=5326 SHOT_DIR=... MODE=b node pulses_classfind.cjs
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const PORT = process.env.SM_PORT || 5326;
const DIR = process.env.SHOT_DIR || '.';
const MODE = process.env.MODE || 'b';
const TAG = process.env.TAG || MODE;
const W = +(process.env.W || 1600);
const MOD = process.env.LAB_MODULE || 'smlab_hint.ramp_pulses';
const CLS = process.env.LAB_CLASS || 'HintRampPulse';
const BAD = MOD.split('.')[0] + '.nope_' + Date.now().toString(36);
const MEASURE_ONLY = process.env.MEASURE_ONLY === '1';   // A/B: stop after the class shows
const THEME = process.env.THEME || '';
const ONLY_FAILED = process.env.ONLY_FAILED === '1';     // screenshots of the failed state only
const TOUCHED = process.env.TOUCHED === '1';             // type an op name BEFORE naming the module                    // light | dark (quam_theme)
const BASE = `http://127.0.0.1:${PORT}`;
require('fs').mkdirSync(DIR, { recursive: true });
let bad = 0;
const meas = {};
function check(c, m) { console.log((c ? 'ok   ' : 'FAIL ') + m); if (!c) bad++; }

async function waitFor(p, expr, ms = 60000, every = 200) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await p.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(every);
  }
  return null;
}
async function rect(p, sel) {
  return p.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return null; var b=e.getBoundingClientRect(); return [b.left+b.width/2,b.top+b.height/2,b.width,b.height,b.top,b.bottom];})()`);
}
async function clickSel(p, sel, scroll = true) {
  if (scroll) await p.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(e) e.scrollIntoView({block:'center'}); return 1})()`);
  await sleep(120);
  const r = await rect(p, sel);
  if (!r || !r[2]) return false;
  // hit-test: the element (or a child) must be what a real click lands on
  const hit = await p.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); var h=document.elementFromPoint(${r[0]},${r[1]}); return !!(h && (h===e || e.contains(h)));})()`);
  if (!hit) { console.log('  (not hit-testable: ' + sel + ')'); return false; }
  await p.click(r[0], r[1]);
  return true;
}
async function type(p, text) { await p.send('Input.insertText', { text }); }
const STRIP_BUSY = `!!document.querySelector('#pulse-env-strip progress')`;
const HAS_CLASS = `!!document.querySelector('#pulse-create-type option[value="${CLS}"]')`;
const REFRESH = `!!document.querySelector('#pulse-env-strip .pulse-env-refresh')`;
const FIND = `document.getElementById('pulse-create-classfind')`;
const FIND_TXT = `((${FIND}||{}).textContent||'').replace(/\\s+/g,' ').trim()`;

async function reload(p) {
  // an unapplied edit arms a beforeunload prompt -- accept it like a user would
  const pr = p.send('Page.reload');
  await sleep(800);
  await Promise.race([p.send('Page.handleJavaScriptDialog', { accept: true }), sleep(1500)]);
  await Promise.race([pr, sleep(3000)]);
  await sleep(1200);
}

async function openCreate(p) {
  check(await clickSel(p, 'button.pulse-new-btn'), '+ New pulse pressed (real click)');
  await waitFor(p, `document.getElementById('pulse-create-root') ? 1 : 0`, 20000);
  // the strip settles (warm env) before anything is measured
  return waitFor(p, `document.querySelector('#pulse-env-strip .pulse-env-badge-ok') && !(${STRIP_BUSY}) ? 1 : 0`, 240000, 400);
}

async function addModule(p, name, viaHint) {
  if (viaHint) {
    check(await clickSel(p, '#pulse-create-classfind [data-pulse-module-open]'), 'the line\'s link pressed (real click)');
    await sleep(700);                       // smooth scroll
    const st = await p.ev(`(function(){var d=document.querySelector('#pulse-env-strip details.pulse-env-modules'); var i=d&&d.querySelector('input[name=module]'); var r=d&&d.getBoundingClientRect(); return {open:!!(d&&d.open), focused: document.activeElement===i, inView: !!(r && r.top>=0 && r.bottom<=innerHeight), url: location.pathname+location.search+location.hash};})()`);
    check(st && st.open, 'the module box is open');
    check(st && st.focused, 'the caret is in the module input');
    check(st && st.inView, 'the module box is in view');
    check(st && !/#/.test(st.url), 'no navigation (' + (st && st.url) + ')');
    await p.shot(`${DIR}/${TAG}_2_box_open.png`);
  } else {
    await p.ev(`(function(){var d=document.querySelector('#pulse-env-strip details.pulse-env-modules'); if(d) d.open=true; return 1})()`);
    check(await clickSel(p, '#pulse-env-strip input[name="module"]'), 'module input clicked');
  }
  await type(p, name);
  await sleep(150);
  const t0 = Date.now();
  check(await clickSel(p, '#pulse-env-strip .pulse-env-module-form button[type="submit"]'), 'Add module pressed (real click) -- ' + name);
  return t0;
}

(async () => {
  const p = await open(`${BASE}/pulses?per_page=200`, W, 950);
  if (THEME) {
    await p.ev(`localStorage.setItem('quam_theme', ${JSON.stringify(THEME)})`);
    await reload(p);
  }
  const mark = p.events.length;
  const n0 = await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 30000);
  check(n0 > 5, `pulses table rendered (${n0} rows)`);
  check(!!(await openCreate(p)), 'create form open, env strip settled');
  if (!ONLY_FAILED) check((await p.ev(HAS_CLASS)) === false, `${CLS} not offered before its module is named`);

  const hasLine = await p.ev(`!!${FIND}`);
  if (MODE === 'b') {
    check(hasLine, 'the line is there');
    const pos = await p.ev(`(function(){var f=${FIND}; var s=document.getElementById('pulse-create-type'); var a=f.getBoundingClientRect(), b=s.getBoundingClientRect(); return {under: f.previousElementSibling===s, gap: Math.round(a.top-b.bottom), lines: Math.round(a.height/parseFloat(getComputedStyle(f).lineHeight)), state: f.getAttribute('data-classfind-state'), color: getComputedStyle(f.querySelector('a')).color, fw: getComputedStyle(f.querySelector('a')).fontWeight};})()`);
    check(pos && pos.under && pos.gap >= 0 && pos.gap < 12, 'right under the class list (gap ' + (pos && pos.gap) + 'px)');
    check(pos && pos.lines === 1, 'one line at ' + W + 'px (' + (pos && pos.lines) + ')');
    check(pos && pos.state === 'ok', 'state ok');
    check(/Don.t see your pulse class\? Name the module it lives in \(e\.g\. my_lab\.cz_pulses\)/.test(await p.ev(FIND_TXT)), 'wording: ' + await p.ev(FIND_TXT));
    meas.link = pos;
    await p.ev(`(function(){var s=document.getElementById('pulse-create-type'); s.scrollIntoView({block:'center'}); return 1})()`);
    await p.shot(`${DIR}/${TAG}_1_line.png`);
  }

  if (!ONLY_FAILED) {
  if (TOUCHED) {
    // a touched form: the probe must grow the class list IN PLACE, keeping this
    check(await clickSel(p, '#pulse-create-name'), 'op name clicked before naming the module');
    await type(p, 'keepme');
  }
  // the press: name a real importable module, Add, wait for the class
  const t0 = await addModule(p, MOD, MODE === 'b');
  let sawBusy = false, tBusyEnd = null, tClass = null, refresh = false;
  const deadline = Date.now() + 300000;
  while (Date.now() < deadline) {
    const s = await p.ev(`[${STRIP_BUSY}, ${HAS_CLASS}, ${REFRESH}]`);
    if (s[0]) sawBusy = true;
    if (sawBusy && !s[0] && tBusyEnd == null) tBusyEnd = Date.now() - t0;
    if (s[1]) { tClass = Date.now() - t0; break; }
    if (s[2]) { refresh = true; break; }
    if (MODE === 'b' && sawBusy && s[0]) {
      const busyTxt = await p.ev(FIND_TXT);
      if (!meas.busyTxt && /reading/.test(busyTxt)) { meas.busyTxt = busyTxt; await p.shot(`${DIR}/${TAG}_3_reading.png`); }
    }
    await sleep(250);
  }
  meas.ms_add_to_class_auto = tClass;
  meas.ms_add_to_probe_end = tBusyEnd;
  meas.refresh_offered = refresh;
  if (refresh) {
    await p.shot(`${DIR}/${TAG}_3_refresh_offered.png`);
    const t1 = Date.now();
    await clickSel(p, '#pulse-env-strip .pulse-env-refresh');
    await waitFor(p, HAS_CLASS, 60000);
    meas.ms_add_to_class_with_extra_click = Date.now() - t0;
    meas.ms_refresh_click_to_class = Date.now() - t1;
  }
  console.log('MEAS ' + JSON.stringify(meas));
  if (MODE === 'b') {
    check(tClass != null && !refresh, `after Add module the class list shows ${CLS} by itself (${tClass} ms, refresh offered: ${refresh})`);
    const ln = await p.ev(FIND_TXT);
    check(!/✗/.test(ln) && !/reading/.test(ln), 'the line settled (no failure, no reading): ' + ln);
    if (TOUCHED) {
      const kept = await p.ev(`(document.getElementById('pulse-create-name')||{}).value`);
      check(kept === 'keepme', 'the typed op name survived the class-list update (' + kept + ')');
      await p.shot(`${DIR}/${TAG}_3b_touched_merged.png`);
      // the create step below types its own name
      await p.ev(`(function(){var i=document.getElementById('pulse-create-name'); i.value=''; return 1})()`);
    }
    const mod = await p.ev(`((document.querySelector('#pulse-env-strip .pulse-env-module')||{}).textContent||'').replace(/\\s+/g,' ')`);
    check(/imported/.test(mod), 'the strip says imported: ' + mod.trim());
  }

  // create a pulse of the new class (qubit channel, real typing + real click)
  const op = 'hint_' + TAG.replace(/\W/g, '') + '_' + Date.now().toString(36).slice(-4);
  let created = false;
  if (!MEASURE_ONLY && await p.ev(HAS_CLASS)) {
    await p.ev(`(function(){var s=document.getElementById('pulse-create-type'); s.value=${JSON.stringify(CLS)}; s.dispatchEvent(new Event('change',{bubbles:true})); return 1})()`);
    await sleep(300);
    check(await clickSel(p, '#pulse-create-name'), 'op name field clicked');
    await type(p, op);
    const amp = '#pulse-create-fields input[name="amplitude"]';
    if (await rect(p, amp)) {
      await clickSel(p, amp);
      await p.ev(`document.querySelector('${amp}').select()`);
      await type(p, '0.2');
    }
    await p.shot(`${DIR}/${TAG}_4_filled.png`);
    check(await clickSel(p, '.pulse-create-actions button[type="submit"]'), 'Create pulse pressed (real click)');
    created = !!(await waitFor(p, `(function(){var r=document.getElementById('pulse-detail-root'); return r && /${op}$/.test(r.getAttribute('data-pulse-path')||'') ? r.getAttribute('data-pulse-path') : ''})()`, 120000, 400));
    check(created, `the pulse ${op} was created and its detail opened`);
    await p.shot(`${DIR}/${TAG}_5_created.png`);
  }

  // reload: intact, the pulse is in the table, the form still offers the class
  if (!MEASURE_ONLY) {
  await reload(p);
  const n1 = await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 30000);
  check(n1 > 5, `after reload the table is back (${n1} rows)`);
  if (created) {
    const row = await waitFor(p, `!!document.querySelector('tr[data-pulse-path$=".${op}"]')`, 10000);
    check(!!row, `the created pulse's row is there after reload`);
  }
  check(!!(await openCreate(p)), 'create form reopened after reload');
  check(!!(await p.ev(HAS_CLASS)), `after reload the form still offers ${CLS}`);
  const lnR = await p.ev(FIND_TXT);
  check(MODE !== 'b' || /Name the module it lives in/.test(lnR), 'after reload the line is back: ' + lnR);
  }
  }

  if (MODE === 'b' && !MEASURE_ONLY) {
    // a module that does not import: the line names it with its error
    const t2 = await addModule(p, BAD, true);
    const failed = await waitFor(p, `(function(){var f=${FIND}; return f && f.getAttribute('data-classfind-state')==='failed' && !(${STRIP_BUSY}) ? 1 : 0})()`, 300000, 400);
    meas.ms_bad_module_to_failed_line = failed ? Date.now() - t2 : null;
    const ln = await p.ev(FIND_TXT);
    check(!!failed && new RegExp('✗ ' + BAD.replace('.', '\\.') + ': ModuleNotFoundError').test(ln), 'failed module named under the class list: ' + ln);
    const errFit = await p.ev(`(function(){var e=document.querySelector('#pulse-create-classfind .pulse-class-find-err'); if(!e) return null; var f=${FIND}; var q=f.querySelector('.pulse-class-find-q'); return {clip: e.scrollWidth>e.clientWidth, fits: e.getBoundingClientRect().right <= f.getBoundingClientRect().right+1, sameLine: Math.abs(e.getBoundingClientRect().top - q.getBoundingClientRect().top) < 3, lines: Math.round(f.getBoundingClientRect().height/parseFloat(getComputedStyle(f).lineHeight)), title: e.title};})()`);
    meas.failed_line = errFit;
    check(errFit && errFit.fits && /ModuleNotFoundError/.test(errFit.title), 'the error stays inside the line, full text in its title');
    check(errFit && errFit.sameLine && errFit.lines === 1, 'the failed state is still ONE line at ' + W + 'px (' + JSON.stringify(errFit) + ')');
    await p.ev(`(function(){var s=document.getElementById('pulse-create-type'); s.scrollIntoView({block:'center'}); return 1})()`);
    await p.shot(`${DIR}/${TAG}_6_failed.png`);
  }

  // cleanup: remove every named module (re-runnable), wait for the re-probe
  for (let i = 0; i < 4; i++) {
    const n = await p.ev(`document.querySelectorAll('#pulse-env-strip .pulse-env-module button[hx-post]').length`);
    if (!n) break;
    await p.ev(`(function(){var d=document.querySelector('#pulse-env-strip details.pulse-env-modules'); if(d) d.open=true; return 1})()`);
    await clickSel(p, '#pulse-env-strip .pulse-env-module button[hx-post]');
    await sleep(1200);
    await waitFor(p, `!(${STRIP_BUSY}) ? 1 : 0`, 240000, 500);
  }
  const left = await p.ev(`document.querySelectorAll('#pulse-env-strip .pulse-env-module').length`);
  check(left === 0, 'cleanup: no named modules left');
  const errs = p.errors(mark);
  check(errs.length === 0, 'no JS errors (' + errs.join(' | ').slice(0, 300) + ')');
  console.log('MEAS ' + JSON.stringify(meas));
  await p.close();
  console.log(bad ? `${bad} FAILED` : 'ALL OK');
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
