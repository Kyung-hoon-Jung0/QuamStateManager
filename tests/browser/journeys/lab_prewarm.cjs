/* Journey (w9/labwarm): the project's Python environment on the landing, and
 * the lab-code worker pre-warmed so the first lab check does not pay its
 * start. Real headless Chrome, every click a real mouse event.
 *
 *   SM_CDP_PORT=<cdp> PORT=<sm> SHOT_DIR=<dir> PHASE=<phase> SRV_LOG=<file> node lab_prewarm.cjs
 *
 * PHASE (the caller restarts the server between phases where it matters):
 *   first    -- no project was ever synced: both cards say "suggested --
 *               confirm" (the env Generate Config already had); Confirm on
 *               PROJ remembers it (reload equals); Open -> the sidebar names
 *               the env; the worker becomes ready without any lab action.
 *   prep     -- (fresh server) open PROJ, go straight to the Pulses page and
 *               delete the gate's inline pulse: while the worker is still
 *               starting the step says "Preparing your lab code... (first
 *               check after start)" beside the disabled button, then
 *               "Checking with your lab code...", then the refusal lands;
 *               Cancel, reload -> the pulse is still there.
 *   grid     -- (fresh server) the Live Edit pair grid: a lab cell edited
 *               right after the open shows the Preparing badge, then the
 *               gate's refusal; nothing written.
 *   second   -- (fresh server) PROJ remembered: its card shows the env with
 *               no Confirm; Open -> NO /generate/* request in the browser or
 *               the server log (no re-discovery, no re-probe), and the
 *               worker becomes ready.
 *   other    -- the never-synced PROJ2 opens with the SUGGESTED env: the
 *               sidebar badge is amber "(suggested -- confirm)".
 *   change   -- PROJ open; Change... on its card: "Discovering environments",
 *               rows "checking..." then probed; Use ALT -> the card and the
 *               badge say ALT, the old env's worker is gone and ALT's starts;
 *               then back to the first env the same way.
 * Exit 0 = all ok.
 */
'use strict';
const fs = require('fs');
const { open, sleep } = require('./cdp.cjs');

const PORT = +(process.env.PORT || 5351);
const BASE = `http://127.0.0.1:${PORT}`;
const DIR = process.env.SHOT_DIR || '.';
const PHASE = process.env.PHASE || 'first';
const PROJ = process.env.PROJ || 'LW_krs5';
const PROJ2 = process.env.PROJ2 || 'LW_second';
const ENV_LABEL = process.env.ENV_LABEL || 'KRISS_CZ';
const ALT = process.env.ALT || 'cqt';
const SRV_LOG = process.env.SRV_LOG || '';
const PAIR = process.env.PAIR || 'q1-2', GATE = process.env.GATE || 'cz_SNZ';
const FQ = `qubit_pairs.${PAIR}.macros.${GATE}.flux_pulse_qubit`;
fs.mkdirSync(DIR, { recursive: true });
const J = JSON.stringify;
const out = []; let bad = 0; const timing = {};
function check(c, m) { const l = (c ? 'ok   ' : 'FAIL ') + m; out.push(l); console.log(l); if (!c) bad++; return c; }

async function waitFor(p, expr, ms = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await p.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(100);
  }
  return null;
}
async function center(p, sel) {
  return p.ev(`(function(){var e=document.querySelector(${J(sel)}); if(!e||e.offsetParent===null) return null; e.scrollIntoView({block:'center'}); var b=e.getBoundingClientRect(); return [b.left+b.width/2,b.top+b.height/2];})()`);
}
async function clickSel(p, sel) {
  let r = await center(p, sel); if (!r) return false;
  for (let i = 0; i < 10; i++) {
    await sleep(80);
    const r2 = await center(p, sel); if (!r2) return false;
    if (Math.abs(r2[0] - r[0]) < 1 && Math.abs(r2[1] - r[1]) < 1) { r = r2; break; }
    r = r2;
  }
  const hit = await p.ev(`(function(){var e=document.querySelector(${J(sel)}); var h=document.elementFromPoint(${r[0]},${r[1]}); return (h&&(h===e||e.contains(h)||h.contains(e))) ? true : (h? h.tagName+'.'+String(h.className).slice(0,60) : 'nothing');})()`);
  if (hit !== true) console.log('  (hit-test miss for ' + sel + ': ' + hit + ')');
  await p.click(r[0], r[1]);
  return hit === true;
}
async function nav(p, path) {
  await p.send('Page.navigate', { url: BASE + path });
  await waitFor(p, `document.readyState==='complete' && location.pathname===${J(path.split('?')[0])} ? 1 : 0`, 90000);
  await sleep(300);
}
const cardSel = (name) => `.landing-card-env[data-project=${J(name)}]`;
const cardText = (p, name) => p.ev(`(function(){var e=document.querySelector(${J(cardSel(name))}); return e? e.innerText.replace(/\\s+/g,' ').trim() : null})()`);
const badge = (p) => p.ev(`(function(){var b=document.querySelector('.project-env-badge'); return b? J2({text:b.innerText.replace(/\\s+/g,' ').trim(), cls:b.className, state:b.getAttribute('data-env-state')}) : null; function J2(x){return JSON.stringify(x)}})()`);
const status = (p) => p.ev(`fetch('/api/lab/worker-status',{cache:'no-store'}).then(r=>r.json()).then(j=>JSON.stringify(j))`);
function logLines() {
  if (!SRV_LOG) return [];
  try { return fs.readFileSync(SRV_LOG, 'utf8').split(/\r?\n/); } catch (e) { return []; }
}
async function landing(p) {
  await nav(p, '/?landing=1');
  return waitFor(p, `document.querySelector(${J(cardSel(PROJ))}) ? 1 : 0`, 60000);
}
async function openProject(p, name) {
  const sel = `.landing-card:has(${cardSel(name)}) form.landing-card-open button[type=submit]`;
  const t0 = Date.now();
  await clickSel(p, sel);
  await waitFor(p, `location.pathname==='/qubits' && document.readyState==='complete' ? 1 : 0`, 120000);
  timing[`open_${name}_ms`] = Date.now() - t0;
  return t0;
}
async function readyAfter(p, t0, ms = 240000) {
  const t = await waitFor(p, `fetch('/api/lab/worker-status',{cache:'no-store'}).then(r=>r.json()).then(j=>j.state==='ready'?1:0)`, ms);
  return t ? Date.now() - t0 : null;
}
async function openPulseByUrl(p) {
  await nav(p, '/pulses');
  await p.ev(`htmx.ajax('GET','/pulse/detail?path=${encodeURIComponent(FQ)}',{target:'#inspector-pane',swap:'innerHTML'})`);
  return waitFor(p, `document.querySelector('#pulse-detail-root') && document.querySelector('.pulse-delete-btn') ? 1 : 0`, 90000);
}

(async () => {
  const p = await open(BASE + '/?landing=1');
  const mark = p.events.length;
  if (PHASE === 'first') {
    check(!!(await landing(p)), 'the landing shows the project cards');
    const t1 = await cardText(p, PROJ), t2 = await cardText(p, PROJ2);
    check(/env\s+KRISS_CZ/.test(t1) && /suggested — confirm/.test(t1) && /Confirm/.test(t1), `${PROJ}: suggested env with Confirm -> "${t1}"`);
    check(/suggested — confirm/.test(t2), `${PROJ2}: suggested too -> "${t2}"`);
    await p.shot(`${DIR}/01_landing_suggested.png`);
    await clickSel(p, `${cardSel(PROJ)} .landing-env-confirm`);
    const conf = await waitFor(p, `(function(){var e=document.querySelector(${J(cardSel(PROJ))}); return e && /confirmed/.test(e.innerText) && !/suggested/.test(e.innerText) ? e.innerText : 0})()`, 15000);
    check(!!conf, `Confirm remembers it -> "${String(conf).replace(/\s+/g, ' ')}"`);
    await p.shot(`${DIR}/02_confirmed.png`);
    await landing(p);                                          // cold reload
    const t1b = await cardText(p, PROJ);
    check(/KRISS_CZ/.test(t1b) && !/suggested/.test(t1b) && !/Confirm\b/.test(t1b.replace('Confirmed', '')), `a cold reload shows it remembered -> "${t1b}"`);
    const t0 = await openProject(p, PROJ);
    const b = JSON.parse(await badge(p) || 'null');
    check(b && /env KRISS_CZ/.test(b.text) && b.state === 'remembered' && !/warn/.test(b.cls), `the sidebar names the env -> ${J(b)}`);
    await p.shot(`${DIR}/03_open_badge.png`);
    const r = await readyAfter(p, t0);
    timing.first_open_to_ready_ms = r;
    check(r !== null, `the lab worker is ready without any lab action (${r} ms after Open)`);
  } else if (PHASE === 'prep') {
    await landing(p);
    const t0 = await openProject(p, PROJ);
    check(!!(await openPulseByUrl(p)), 'the gate\'s inline pulse opens on the Pulses page');
    const st0 = JSON.parse(await status(p));
    timing.state_before_delete = st0.state;
    await clickSel(p, '#pulse-detail-root .pulse-delete-btn');
    await waitFor(p, `(document.querySelector('.pulse-delete-confirm')||{}).hidden===false?1:0`, 5000);
    const tc = Date.now();
    await clickSel(p, '.pulse-delete-confirm .pulse-confirm-delete');
    const seen = [];
    let shotPrep = false, shotCheck = false;
    for (let i = 0; i < 1500; i++) {
      const s = await p.ev(`(function(){var i=document.querySelector('.pulse-delete-checking'); var b=document.querySelector('.pulse-delete-confirm .pulse-confirm-delete'); var r=document.querySelector('#pulse-delete-result .pulse-delete-refused'); return JSON.stringify({vis: i? getComputedStyle(i).display!=='none' : null, text: i? i.textContent : null, dis: b? b.disabled : null, refused: !!r})})()`);
      const o = JSON.parse(s);
      if (o.vis && seen[seen.length - 1] !== o.text) seen.push(o.text);
      if (o.vis && /^Preparing your lab code/.test(o.text) && !shotPrep) { await p.shot(`${DIR}/04_delete_preparing.png`); shotPrep = { dis: o.dis, ms: Date.now() - tc }; }
      if (o.vis && /^Checking with your lab code/.test(o.text) && !shotCheck) { await p.shot(`${DIR}/05_delete_checking.png`); shotCheck = { dis: o.dis, ms: Date.now() - tc }; }
      if (o.refused) break;
      await sleep(60);
    }
    timing.delete_first_check_ms = Date.now() - tc;
    timing.delete_texts = seen;
    check(!!shotPrep && shotPrep.dis === true, `while the worker starts: "Preparing your lab code… (first check after start)" beside the DISABLED button (${J(shotPrep)}, worker was ${st0.state})`);
    check(seen.every((t) => /^(Preparing your lab code… \(first check after start\)|Checking with your lab code…)$/.test(t)), `the step only ever said the two lines -> ${J(seen)}`);
    check(!!shotCheck, `"Checking with your lab code…" once the worker is ready (${J(shotCheck)})`);
    const ref = await waitFor(p, `document.querySelector('#pulse-delete-result .pulse-delete-refused')?1:0`, 5000);
    check(!!ref, `the lab refusal lands in the delete step (${timing.delete_first_check_ms} ms after the press)`);
    await p.shot(`${DIR}/06_delete_refused.png`);
    const after = await p.ev(`(function(){var i=document.querySelector('.pulse-delete-checking'); return i? getComputedStyle(i).display : 'none'})()`);
    check(after === 'none', 'the indicator is hidden once answered');
    await clickSel(p, '.pulse-delete-confirm button[onclick*="cancelDelete"]');
    await nav(p, '/pulses');
    const still = await p.ev(`fetch('/field/peek?dot_path=${FQ}.flat_length').then(r=>r.json()).then(j=>JSON.stringify(j.values||j))`);
    check(/78/.test(still), `reload: the pulse is untouched -> ${still}`);
    timing.open_to_ready_ms = await readyAfter(p, t0, 5000);
  } else if (PHASE === 'grid') {
    await landing(p);
    await openProject(p, PROJ);
    await nav(p, '/bulk');
    await p.ev(`(function(){var t=document.querySelector('[data-grid-tab="pairs"],#bulk-tab-pairs,button[data-kind="pair"]'); if(t) t.click(); return 1})()`);
    const sel = `input.bulk-cell[data-dot-path="${FQ}.flat_length"]`;
    await p.ev(`(function(){var s=document.getElementById('bulk-search'); if(!s) return 0; s.focus(); s.value=${J(PAIR + ' ' + GATE)}; s.dispatchEvent(new Event('input',{bubbles:true})); return 1})()`);
    const found = await waitFor(p, `(function(){var c=document.querySelector(${J(sel)}); if(!c||!c.getClientRects().length||c.value==='') return 0; c.scrollIntoView({block:'center',inline:'center'}); return c.value;})()`, 60000);
    check(!!found, `the pair grid shows ${FQ}.flat_length = ${found}`);
    if (found) {
      await clickSel(p, sel);
      await p.ev(`(function(){var c=document.querySelector(${J(sel)}); c.focus(); c.select(); return 1})()`);
      await p.send('Input.insertText', { text: '82' });
      const t0 = Date.now();
      await p.key('Enter', 'Enter', 13);
      const prep = await waitFor(p, `(function(){var b=document.querySelector('.lab-check-badge'); return b && /^Preparing your lab code/.test(b.textContent) ? b.textContent : 0})()`, 8000);
      if (prep) await p.shot(`${DIR}/07_grid_preparing.png`);
      check(!!prep, `a lab cell edited right after the open: "${prep}"`);
      const refused = await waitFor(p, `(function(){var b=document.querySelector('.lab-check-refused'); return b ? b.textContent : 0})()`, 240000);
      timing.grid_first_check_ms = Date.now() - t0;
      check(!!refused, `then the gate's own refusal (${timing.grid_first_check_ms} ms): ${String(refused).slice(0, 90)}`);
      await p.shot(`${DIR}/08_grid_refused.png`);
      const v = await p.ev(`fetch('/field/peek?dot_path=${FQ}.flat_length').then(r=>r.json()).then(j=>JSON.stringify(j.values||j))`);
      check(/78/.test(v), `nothing written -> ${v}`);
    }
  } else if (PHASE === 'second') {
    const L0 = logLines().length;
    await landing(p);
    const t1 = await cardText(p, PROJ);
    check(/KRISS_CZ/.test(t1) && !/suggested/.test(t1) && /✓/.test(t1), `${PROJ}: the remembered env, pre-selected -> "${t1}"`);
    await p.shot(`${DIR}/09_second_landing.png`);
    const t0 = await openProject(p, PROJ);
    const r = await readyAfter(p, t0);
    timing.second_open_to_ready_ms = r;
    check(r !== null, `the worker is ready ${r} ms after Open`);
    const res = await p.ev(`JSON.stringify(performance.getEntriesByType('resource').map(e=>e.name).filter(n=>/\\/generate\\//.test(n)))`);
    const log = logLines().slice(L0).filter((l) => /"(GET|POST) \/generate\//.test(l));
    timing.generate_requests_browser = JSON.parse(res).length;
    timing.generate_requests_server = log.length;
    check(JSON.parse(res).length === 0 && log.length === 0, `no /generate/* discovery or probe on the second open (browser ${JSON.parse(res).length}, server log ${log.length} of ${logLines().length - L0} lines)`);
    const b = JSON.parse(await badge(p) || 'null');
    check(b && b.state === 'remembered', `the badge: ${J(b)}`);
    await p.shot(`${DIR}/10_second_open.png`);
  } else if (PHASE === 'other') {
    await landing(p);
    const t2 = await cardText(p, PROJ2);
    check(/suggested — confirm/.test(t2) && /KRISS_CZ/.test(t2), `${PROJ2} (never synced): suggested -> "${t2}"`);
    await openProject(p, PROJ2);
    const b = JSON.parse(await badge(p) || 'null');
    check(b && b.state === 'suggested' && /suggested — confirm/.test(b.text) && /warn/.test(b.cls), `the badge says it is only suggested -> ${J(b)}`);
    await p.shot(`${DIR}/11_other_suggested.png`);
  } else if (PHASE === 'change') {
    await landing(p);
    await openProject(p, PROJ);
    const before = JSON.parse(await status(p));
    await landing(p);
    await clickSel(p, `${cardSel(PROJ)} .landing-env-change`);
    const disc = await waitFor(p, `(function(){var l=document.querySelector('#landing-env-picker [data-env-loading]'); var pk=document.getElementById('landing-env-picker'); return pk && !pk.hidden && l && !l.hidden && /Discovering/.test(l.textContent) ? 1 : 0})()`, 5000);
    if (disc) await p.shot(`${DIR}/12_picker_discovering.png`);
    check(!!disc || !!(await p.ev(`document.querySelectorAll('.landing-env-row').length`)), 'the picker opens (loading state while envs are discovered)');
    await waitFor(p, `document.querySelectorAll('.landing-env-row').length>0?1:0`, 60000);
    const checking = await p.ev(`document.querySelectorAll('.landing-env-status[data-state="checking"]').length`);
    await p.shot(`${DIR}/13_picker_checking.png`);
    const altRow = `.landing-env-row[data-python*=${J('\\envs\\' + ALT + '\\')}]`;
    const ok2 = await waitFor(p, `(function(){var r=document.querySelector(${J(altRow)}); return r && !r.querySelector('button').disabled ? 1 : 0})()`, 180000);
    const rows = await p.ev(`JSON.stringify([].slice.call(document.querySelectorAll('.landing-env-row')).map(function(r){return r.querySelector('.landing-env-name').textContent+': '+r.querySelector('.landing-env-status').textContent}))`);
    timing.picker_rows = JSON.parse(rows);
    await p.shot(`${DIR}/14_picker_probed.png`);
    check(!!ok2, `${ALT} probes usable (${checking} rows were "checking…" first) -> ${rows.slice(0, 300)}`);
    const tch = Date.now();
    await clickSel(p, `${altRow} button`);
    const row = await waitFor(p, `(function(){var e=document.querySelector(${J(cardSel(PROJ))}); return e && e.innerText.indexOf(${J(ALT)})>=0 && /saved/.test(e.innerText) ? e.innerText.replace(/\\s+/g,' ') : 0})()`, 20000);
    check(!!row, `the card now says ${ALT} -> "${row}"`);
    const b = JSON.parse(await badge(p) || 'null');
    check(b && b.text.indexOf(ALT) >= 0, `the sidebar badge (out of band) says ${ALT} -> ${J(b)}`);
    await p.shot(`${DIR}/15_changed.png`);
    const st = await waitFor(p, `fetch('/api/lab/worker-status',{cache:'no-store'}).then(r=>r.json()).then(j=>j.env && j.env.indexOf(${J('\\' + ALT + '\\')})>=0 && j.state==='ready' ? JSON.stringify(j) : 0)`, 240000);
    timing.change_to_ready_ms = st ? Date.now() - tch : null;
    check(!!st, `the new env's worker is started and ready (${timing.change_to_ready_ms} ms) -> ${st}; before: ${J(before)}`);
    // and back
    await clickSel(p, `${cardSel(PROJ)} .landing-env-change`);
    const envRow = `.landing-env-row[data-python*=${J('\\envs\\' + ENV_LABEL + '\\')}]`;
    await waitFor(p, `(function(){var r=document.querySelector(${J(envRow)}); return r && !r.querySelector('button').disabled ? 1 : 0})()`, 180000);
    await clickSel(p, `${envRow} button`);
    const back = await waitFor(p, `(function(){var e=document.querySelector(${J(cardSel(PROJ))}); return e && e.innerText.indexOf(${J(ENV_LABEL)})>=0 ? e.innerText.replace(/\\s+/g,' ') : 0})()`, 20000);
    check(!!back, `changed back -> "${back}"`);
    await landing(p);
    const cold = await cardText(p, PROJ);
    check(/KRISS_CZ/.test(cold) && !/suggested/.test(cold), `a cold reload shows the same -> "${cold}"`);
  }
  // a lab REFUSAL answers 400 by design (prep, grid): that one line is the
  // expected answer, not a page error
  const errs = p.errors(mark).filter((e) => !(['prep', 'grid'].includes(PHASE) && /status of 400/.test(e)));
  check(errs.length === 0, 'no console errors' + (errs.length ? ': ' + J(errs.slice(0, 3)) : ''));
  fs.writeFileSync(`${DIR}/lab_prewarm_${PHASE}.json`, J({ phase: PHASE, ok: bad === 0, out, timing }, null, 1));
  console.log(J(timing));
  await p.close();
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
