/* docs/218 adaptive pulses, verifier round -- the class schema must never be
 * older than what a cold probe would read, on any surface, in real Chrome.
 *
 * Rig as pulses_adaptive.cjs, plus smlab_scratch.third_pulses (TriCZPulse)
 * on the rig's PYTHONPATH and LAB_FILE = the rig's smlab_scratch/cz_pulses.py.
 *
 * Journey:
 *  1. race: name more_pulses, wait 2 s (its probe is running), name
 *     third_pulses -> once the strip settles BOTH are imported and the create
 *     form offers RampCZPulse AND TriCZPulse;
 *  2. schema after a lab edit: add `skew` to WobbleCZPulse, open cz_wobble --
 *     the detail says its schema predates the edit (never "typed from"), and
 *     re-renders itself with skew once the re-probe lands, with no create
 *     form opened;
 *  3. the create-draw route refuses a class nobody named;
 *  4. close (remove modules, restore the lab file) -> reload -> page intact.
 *
 *   SM_CDP_PORT=9412 SM_PORT=5112 SHOT_DIR=... LAB_FILE=... node pulses_adaptive_stale.cjs
 */
'use strict';
const fs = require('fs');
const { open, sleep } = require('./cdp.cjs');
const PORT = process.env.SM_PORT || 5112;
const DIR = process.env.SHOT_DIR || '.';
const LAB_FILE = process.env.LAB_FILE;
const BASE = `http://127.0.0.1:${PORT}`;
const WOB = 'qubits.q1.z.operations.cz_wobble';
fs.mkdirSync(DIR, { recursive: true });
let bad = 0;
function check(c, m) { console.log((c ? 'ok   ' : 'FAIL ') + m); if (!c) bad++; }

async function waitFor(p, expr, ms = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await p.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(400);
  }
  return null;
}
const post = (url, body) => `fetch(${JSON.stringify(url)},{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:${JSON.stringify(body)}}).then(function(r){return r.status})`;
const STRIP = `fetch('/pulse/new/env-strip').then(function(r){return r.text()})`;

(async () => {
  const original = fs.readFileSync(LAB_FILE, 'utf8');
  const p = await open(`${BASE}/pulses?per_page=200`);
  const mark = p.events.length;
  try {
    await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 30000);

    // 1. the race
    await p.ev(post('/pulse/class-modules', 'action=add&module=smlab_scratch.more_pulses'));
    await sleep(2000);
    const busy = await p.ev(`${STRIP}.then(function(t){return /probing/.test(t)})`);
    check(busy === true, 'the first probe is still running when the second module is named');
    await p.ev(post('/pulse/class-modules', 'action=add&module=smlab_scratch.third_pulses'));
    const t0 = Date.now();
    const settled = await waitFor(p, `${STRIP}.then(function(t){return /probing/.test(t)? '' : t})`, 240000);
    console.log(`     strip settled after ${((Date.now() - t0) / 1000).toFixed(1)} s`);
    await p.ev(`htmx.ajax('GET','/pulse/new',{target:'#inspector-pane',swap:'innerHTML'})`);
    await waitFor(p, `document.getElementById('pulse-create-root') ? 1 : 0`, 20000);
    await p.ev(`(function(){var d=document.querySelector('.pulse-env-modules'); if(d) d.open=true; return 1})()`);
    await sleep(500);
    const mods = await p.ev(`Array.from(document.querySelectorAll('.pulse-env-module')).map(function(e){return e.textContent.replace(/\\s+/g,' ').trim()}).join(' || ')`);
    check(/more_pulses.*imported/i.test(mods) && /third_pulses.*imported/i.test(mods), 'both named modules imported: ' + mods.slice(0, 160));
    const offered = await p.ev(`['RampCZPulse','TriCZPulse'].map(function(k){return !!document.querySelector('#pulse-create-type option[value="'+k+'"]')}).join(',')`);
    check(offered === 'true,true', 'the create form offers RampCZPulse and TriCZPulse -> ' + offered);
    check(!!settled, 'the strip settled');
    await p.shot(`${DIR}/stale_1_race_strip.png`);

    // 2. the schema after a lab edit -- detail only, no create form
    await p.ev(`htmx.ajax('GET','/pulses?per_page=200',{target:'#table-pane',swap:'innerHTML'})`);
    await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 30000);
    fs.writeFileSync(LAB_FILE, original.replace('    cycles: float = 2.0\n', '    cycles: float = 2.0\n    skew: float = 0.0\n'));
    check(fs.readFileSync(LAB_FILE, 'utf8').includes('skew: float'), 'lab class edited (skew added)');
    await p.ev(`htmx.ajax('GET','/pulse/detail?path=${WOB}',{target:'#inspector-pane',swap:'innerHTML'})`);
    await waitFor(p, `document.querySelector('#pulse-detail-root') ? 1 : 0`, 20000);
    const note = await p.ev(`(function(){var n=document.querySelector('#pulse-detail-root .pulse-schema-note'); return n? n.textContent.replace(/\\s+/g,' ').trim() : ''})()`);
    check(/changed since SM read this class/.test(note) && !/typed from/.test(note), 'the detail says its schema predates the edit: ' + note.slice(0, 120));
    await p.shot(`${DIR}/stale_2a_detail_stale.png`);
    const t1 = Date.now();
    const fresh = await waitFor(p, `(function(){var u=document.querySelector('#pulse-detail-root .pulse-schema-unset'); return u && /skew=0\\.0/.test(u.textContent) && !document.querySelector('[data-schema-stale]') ? u.textContent.replace(/\\s+/g,' ').trim() : ''})()`, 240000);
    console.log(`     detail refreshed after ${((Date.now() - t1) / 1000).toFixed(1)} s`);
    check(!!fresh, 'the detail re-rendered itself with the new field: ' + String(fresh).slice(0, 100));
    const typed = await p.ev(`/typed from the class/.test((document.querySelector('#pulse-detail-root .pulse-schema-note')||{}).textContent||'')`);
    check(typed === true, 'and now says it is typed from the class schema');
    await p.shot(`${DIR}/stale_2b_detail_fresh.png`);

    // 3. the create-draw route never imports a path nobody named
    const refused = await p.ev(`fetch('/api/pulse/lab-waveform',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({qclass:'this.s',params:{}})}).then(function(r){return r.json()}).then(function(j){return j.results[0].reason+' | '+j.results[0].error})`);
    check(/^unknown-class/.test(refused), 'an unnamed class is refused: ' + refused.slice(0, 120));
  } finally {
    // 4. close: restore the rig
    fs.writeFileSync(LAB_FILE, original);
    await p.ev(post('/pulse/class-modules', 'action=remove&module=smlab_scratch.third_pulses'));
    await sleep(300);
    await p.ev(post('/pulse/class-modules', 'action=remove&module=smlab_scratch.more_pulses'));
  }
  await waitFor(p, `${STRIP}.then(function(t){return /probing/.test(t)? '' : 1})`, 240000);
  const pr = p.send('Page.reload');
  await Promise.race([pr, sleep(3000)]);
  await sleep(1500);
  const again = await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 30000);
  check(again > 10, `after reload the table is back (${again} rows)`);
  await p.ev(`htmx.ajax('GET','/pulse/detail?path=${WOB}',{target:'#inspector-pane',swap:'innerHTML'})`);
  await waitFor(p, `document.querySelector('#pulse-detail-root') ? 1 : 0`, 20000);
  const back = await waitFor(p, `(function(){var u=document.querySelector('#pulse-detail-root .pulse-schema-unset'); return u && !/skew/.test(u.textContent) && !document.querySelector('[data-schema-stale]') ? u.textContent.replace(/\\s+/g,' ').trim() : ''})()`, 240000);
  check(!!back, 'after the restore the detail is back to the original fields: ' + String(back).slice(0, 80));
  await p.shot(`${DIR}/stale_3_reload.png`);
  const errs = p.errors(mark);
  check(errs.length === 0, 'no JS errors (' + errs.join(' | ').slice(0, 300) + ')');
  await p.close();
  console.log(bad ? `${bad} FAILED` : 'ALL OK pulses_adaptive_stale');
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
