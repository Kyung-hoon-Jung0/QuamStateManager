/* Pulse create / edit QA journey (2026-09-27, "SM에서 pulse 생성/수정이 되는지").
 *
 * Every create path the Pulses page offers, driven like a user: real clicks on
 * the toolbar button, real typing (Input.insertText) into every field the form
 * shows, the real submit button. Then edit (type + Enter), duplicate, rename,
 * delete, Ctrl+Z, Apply to live -- and write what was typed to
 * $SHOT_DIR/expect.json so a Python checker can compare it against the LIVE
 * state.json and Quam.load() it.
 *
 *   SM_CDP_PORT=9416 SM_PORT=5116 SHOT_DIR=... [W=1600] node pulses_create_qa.cjs
 *
 * Selectors are the ONE-button flow's when it is present (a "What to create"
 * choice inside "+ New pulse"), else the legacy two-button toolbar -- so the
 * same journey measures before and after.
 */
'use strict';
const fs = require('fs');
const { open, sleep } = require('./cdp.cjs');
const PORT = process.env.SM_PORT || 5116;
const DIR = process.env.SHOT_DIR || '.';
const W = +(process.env.W || 1600);
const BASE = `http://127.0.0.1:${PORT}`;
fs.mkdirSync(DIR, { recursive: true });
const out = []; let bad = 0; const defects = [];
function check(c, m) { const l = (c ? 'ok   ' : 'FAIL ') + m; out.push(l); console.log(l); if (!c) bad++; return c; }
const J = JSON.stringify;

async function waitFor(p, expr, ms = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await p.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(300);
  }
  return null;
}
async function center(p, sel) {
  return p.ev(`(function(){var e=document.querySelector(${J(sel)}); if(!e||e.offsetParent===null) return null; e.scrollIntoView({block:'center'}); var b=e.getBoundingClientRect(); return [b.left+b.width/2,b.top+b.height/2];})()`);
}
async function clickSel(p, sel) {
  const r = await center(p, sel); if (!r) return false;
  await sleep(120);
  // hit-test: the element (or a child) must be what the mouse lands on
  const hit = await p.ev(`(function(){var e=document.querySelector(${J(sel)}); var h=document.elementFromPoint(${r[0]},${r[1]}); return !!(h&&(h===e||e.contains(h)||h.contains(e)));})()`);
  if (!hit) console.log('  (hit-test miss for ' + sel + ')');
  await p.click(r[0], r[1]);
  return hit;
}
async function typeInto(p, sel, text) {
  if (!(await center(p, sel))) return false;
  await clickSel(p, sel);
  await p.ev(`(function(){var i=document.querySelector(${J(sel)}); i.focus(); i.select(); return 1})()`);
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 });
  if (text !== '') await p.send('Input.insertText', { text: String(text) });
  await sleep(80);
  return true;
}
async function enter(p) {
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 });
}
async function choose(p, sel, value) {
  // a <select> popup is not drawable headless; set it and fire the same
  // change event a user's pick fires (the inline onchange handlers run)
  return p.ev(`(function(){var s=document.querySelector(${J(sel)}); if(!s) return 'NOSEL'; var o=[].slice.call(s.options).filter(function(o){return o.value===${J(value)}})[0]; if(!o) return 'NOOPT:'+[].slice.call(s.options).map(function(o){return o.value}).join(','); if(o.disabled) return 'DISABLED'; s.value=${J(value)}; s.dispatchEvent(new Event('change',{bubbles:true})); return 'ok'})()`);
}
const INSP = `(document.getElementById('inspector-pane')||{}).innerText||''`;
const STATUS = `(function(){var s=document.querySelector('#inspector-pane .status-message, #inspector-pane [role=alert], #inspector-pane .alert, #inspector-pane .status'); return s? s.innerText.replace(/\\s+/g,' ').trim().slice(0,300) : ''})()`;

async function openCreate(p, what) {
  // ONE-button flow: "+ New pulse" -> (optional) a "what to create" choice.
  await clickSel(p, '.pulse-new-btn');
  const got = await waitFor(p, `(document.getElementById('pulse-create-root')||document.getElementById('gcz-root')||document.getElementById('pulse-create-choice'))?1:0`, 20000);
  if (!got) return false;
  const hasChoice = await p.ev(`!!document.querySelector('#pulse-create-choice')`);
  if (what === 'gcz') {
    if (hasChoice) {
      await clickSel(p, '#pulse-create-choice [data-create-kind="gaussian_cz"]');
    } else {
      await clickSel(p, '.pulse-gcz-btn');   // legacy second button
    }
    return waitFor(p, `document.getElementById('gcz-root')?1:0`, 20000);
  }
  if (hasChoice && !(await p.ev(`!!document.getElementById('pulse-create-root')`))) {
    await clickSel(p, '#pulse-create-choice [data-create-kind="pulse"]');
  }
  await waitFor(p, `document.getElementById('pulse-create-root')?1:0`, 20000);
  // the class list is rebuilt once the env probe lands; wait for it
  await waitFor(p, `document.querySelector('#pulse-env-strip .pulse-env-badge-ok, #pulse-env-strip .pulse-env-badge-warn, #pulse-env-strip .pulse-env-badge-none')?1:0`, 180000);
  await sleep(400);
  return true;
}

async function fillFields(p, vals) {
  const shown = await p.ev(`JSON.stringify([].slice.call(document.querySelectorAll('#pulse-create-fields input')).map(function(i){return i.name}))`);
  const names = JSON.parse(shown || '[]');
  const typed = {};
  for (const n of names) {
    if (!(n in vals)) continue;
    await typeInto(p, `#pulse-create-fields input[name="${n}"]`, vals[n]);
    typed[n] = vals[n];
  }
  const missed = Object.keys(vals).filter(k => names.indexOf(k) < 0);
  return { names, typed, missed };
}

async function submitCreate(p) {
  const before = await p.ev(`(document.querySelector('#pulse-detail-root')||{}).getAttribute ? document.querySelector('#pulse-detail-root').getAttribute('data-pulse-path') : ''`);
  await clickSel(p, '#pulse-create-root .pulse-create-actions button[type=submit]');
  const r = await waitFor(p, `(function(){var d=document.querySelector('#pulse-detail-root'); if(d) return 'DETAIL '+d.getAttribute('data-pulse-path'); if(!document.getElementById('pulse-create-root')) return 'OTHER '+${INSP}.replace(/\\s+/g,' ').slice(0,200); var v=[].slice.call(document.querySelectorAll('.pulse-create-form input:invalid, .pulse-create-form select:invalid')).map(function(i){return i.name+':'+i.validationMessage}); var st=document.querySelector('#pulse-create-root .status-message, .status-message'); return v.length? 'INVALID '+v.join(';') : (st? 'STATUS '+st.innerText : '')})()`, 30000);
  return r || 'TIMEOUT ' + before;
}

(async () => {
  const p = await open(`${BASE}/pulses?per_page=200`, W, 950);
  // auto-accept every confirm()/alert() the app raises, and record them
  const dialogs = []; let cur = 0;
  const dlgTimer = setInterval(() => {
    for (; cur < p.events.length; cur++) {
      const e = p.events[cur];
      if (e.method === 'Page.javascriptDialogOpening') {
        dialogs.push(e.params.message.slice(0, 200));
        p.send('Page.handleJavaScriptDialog', { accept: true });
      }
    }
  }, 150);
  const allErr = () => p.errors(0);
  await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 60000);
  const toolbar = await p.ev(`[].slice.call(document.querySelectorAll('.table-header-row button')).map(function(b){return b.textContent.trim()}).join(' | ')`);
  console.log('toolbar: ' + toolbar);
  await p.shot(`${DIR}/00_page_${W}.png`);

  // what the chip has
  await openCreate(p, 'pulse');
  const chip = JSON.parse(await p.ev(`JSON.stringify({q:[].slice.call(document.querySelectorAll('#pulse-create-root select[name=qubit] option')).map(function(o){return o.value}), pairs:[].slice.call(document.querySelectorAll('#pulse-create-pair option')).map(function(o){return o.value}), pc:[].slice.call(document.querySelectorAll('#pulse-create-root select[name=pc_pair] option')).map(function(o){return o.value}), pcDisabled: document.querySelector('input[name=target_kind][value=pair_channel]').disabled, types:[].slice.call(document.querySelectorAll('#pulse-create-type option')).map(function(o){return o.value}), chans:[].slice.call(document.querySelectorAll('#pulse-create-root select[name=channel] option')).map(function(o){return o.value})})`));
  console.log('chip: ' + J({ q: chip.q.length, pairs: chip.pairs.length, pc: chip.pc.length, pcDisabled: chip.pcDisabled, types: chip.types.length }));
  await p.shot(`${DIR}/01_create_form_${W}.png`);
  const Q = i => chip.q[Math.min(i, chip.q.length - 1)];
  const tag = 'qa' + (process.env.TAG || '');
  const expect = [];

  // ---- qubit-channel cases -------------------------------------------------
  const qcases = [
    { q: Q(1), ch: 'xy', t: 'DragCosinePulse', n: tag + '_dragc', v: { length: '48', axis_angle: '0.25', amplitude: '0.123', alpha: '-0.5', anharmonicity: '-210000000', detuning: '1000000' } },
    { q: Q(2), ch: 'z', t: 'FlatTopTanhPulse', n: tag + '_fttanh', v: { length: '140', amplitude: '0.07', flat_length: '96' } },
    { q: Q(3), ch: 'resonator', t: 'SquareReadoutPulse', n: tag + '_ro', v: { length: '1200', amplitude: '0.02', integration_weights_angle: '0.3' } },
    { q: Q(4), ch: 'xy', t: 'WaveformPulse', n: tag + '_wf', v: { waveform_I: '0, 0.05, 0.1, 0.05', waveform_Q: '0, 0, 0, 0' } },
    { q: Q(1), ch: 'xy', t: 'GaussianPulse', n: tag + '_gauss', v: { length: '32', amplitude: '0.2', sigma: '6', axis_angle: '1.5' } },
    { q: Q(2), ch: 'z', t: 'ErfSquarePulse', n: tag + '_erf', v: { amplitude: '0.04', flat_length: '60', risetime_samples: '12', phase: '0', detuning: '0', post_zero_padding_length: '8' } },
  ];
  // the lab's own classes, when this chip has them
  if (chip.types.indexOf('SNZTwoFluxPulse') >= 0)
    qcases.push({ q: Q(0), ch: 'z', t: 'SNZTwoFluxPulse', n: tag + '_snz2', v: { amplitude: '0.21', flat_length: '24', t_phi: '2', b_over_a: '0.3', neg_offset_v: '0.01', padding: '4' } });
  if (chip.types.indexOf('MatchedFilterReadoutPulse') >= 0)
    qcases.push({ q: Q(0), ch: 'resonator', t: 'MatchedFilterReadoutPulse', n: tag + '_mf', v: { length: '800', amplitude: '0.015', integration_weights_angle: '0.1' } });

  for (const c of qcases) {
    if (!(await p.ev(`!!document.getElementById('pulse-create-root')`))) await openCreate(p, 'pulse');
    await clickSel(p, 'input[name=target_kind][value=qubit]');
    let r1 = await choose(p, '#pulse-create-root select[name=qubit]', c.q);
    let r2 = await choose(p, '#pulse-create-root select[name=channel]', c.ch);
    let r3 = await choose(p, '#pulse-create-type', c.t);
    if (r1 !== 'ok' || r2 !== 'ok' || r3 !== 'ok') { check(false, `${c.t} on ${c.q}.${c.ch}: pick failed ${r1}/${r2}/${r3}`); continue; }
    await typeInto(p, '#pulse-create-name', c.n);
    const f = await fillFields(p, c.v);
    if (f.missed.length) console.log(`  ${c.t}: fields not on the form: ${f.missed.join(',')}`);
    await sleep(700);
    await p.shot(`${DIR}/10_create_${c.n}_${W}.png`);
    const res = await submitCreate(p);
    const want = `qubits.${c.q}.${c.ch}.operations.${c.n}`;
    if (check(res === 'DETAIL ' + want, `create ${c.t} on ${c.q}.${c.ch} as ${c.n} -> ${res.slice(0, 160)}`)) {
      expect.push({ path: want, cls: c.t, fields: f.typed });
    } else {
      defects.push({ what: `create ${c.t} on ${c.q}.${c.ch}`, got: res });
      await p.shot(`${DIR}/10_FAIL_${c.n}_${W}.png`);
    }
    await openCreate(p, 'pulse');
  }

  // ---- pair gate slot cases -----------------------------------------------
  const pair0 = chip.pairs[0], pair1 = chip.pairs[1] || chip.pairs[0];
  if (pair0) {
    await clickSel(p, 'input[name=target_kind][value=pair]');
    await sleep(300);
    await choose(p, '#pulse-create-pair', pair0);
    const gates = JSON.parse(await p.ev(`JSON.stringify([].slice.call(document.querySelectorAll('#pulse-create-gate option')).map(function(o){return o.value}))`));
    // every existing gate: which slots does the form offer as creatable?
    const offered = [];
    for (const g of gates.filter(g => !/^__new__/.test(g))) {
      await choose(p, '#pulse-create-gate', g);
      const s = JSON.parse(await p.ev(`JSON.stringify([].slice.call(document.querySelectorAll('#pulse-create-slot option')).map(function(o){return o.value+(o.disabled?'(held)':'')}))`));
      offered.push(g + ':' + s.join('/'));
    }
    console.log('  slots offered on ' + pair0 + ': ' + offered.join('  '));
    // (a) an EMPTY slot of an existing gate
    const g0 = gates.find(g => !/^__new__/.test(g));
    await choose(p, '#pulse-create-gate', g0);
    const emptySlot = await p.ev(`(function(){var o=[].slice.call(document.querySelectorAll('#pulse-create-slot option')).filter(function(o){return !o.disabled}); return o.map(function(x){return x.value}).join(',')})()`);
    console.log(`  ${g0} creatable slots: ${emptySlot}`);
    // try every offered slot: the form must never offer one the server refuses
    for (const slot of emptySlot.split(',').filter(Boolean)) {
      if (!(await p.ev(`!!document.getElementById('pulse-create-root')`))) { await openCreate(p, 'pulse'); await clickSel(p, 'input[name=target_kind][value=pair]'); await choose(p, '#pulse-create-pair', pair0); }
      await choose(p, '#pulse-create-gate', g0);
      await choose(p, '#pulse-create-slot', slot);
      await choose(p, '#pulse-create-type', 'SquarePulse');
      const f = await fillFields(p, { length: '52', amplitude: '0.031' });
      await p.shot(`${DIR}/20_pair_${g0}_${slot}_${W}.png`);
      const res = await submitCreate(p);
      const want = `qubit_pairs.${pair0}.macros.${g0}.${slot}`;
      if (check(res === 'DETAIL ' + want, `pair slot ${want} (offered as creatable) -> ${res.slice(0, 200)}`)) {
        expect.push({ path: want, cls: 'SquarePulse', fields: f.typed });
      } else {
        defects.push({ what: `pair slot ${want} offered as creatable`, got: res });
        await p.shot(`${DIR}/20_FAIL_${g0}_${slot}_${W}.png`);
      }
    }
    // (b) a NEW gate + its slot pulse
    await openCreate(p, 'pulse');
    await clickSel(p, 'input[name=target_kind][value=pair]');
    await choose(p, '#pulse-create-pair', pair1);
    const newg = JSON.parse(await p.ev(`JSON.stringify([].slice.call(document.querySelectorAll('#pulse-create-gate option')).map(function(o){return o.value}).filter(function(v){return /^__new__/.test(v)}))`));
    console.log('  new-gate options: ' + newg.join(','));
    const ng = newg.indexOf('__new__:cz_flattop') >= 0 ? '__new__:cz_flattop' : newg[0];
    if (ng) {
      await choose(p, '#pulse-create-gate', ng);
      await typeInto(p, '#pulse-create-newgate-name', tag + '_czg');
      await choose(p, '#pulse-create-slot', 'flux_pulse_qubit');
      await choose(p, '#pulse-create-type', 'FlatTopGaussianPulse');
      const f = await fillFields(p, { length: '88', amplitude: '0.066', flat_length: '64' });
      await p.shot(`${DIR}/21_newgate_${W}.png`);
      const res = await submitCreate(p);
      const want = `qubit_pairs.${pair1}.macros.${tag}_czg.flux_pulse_qubit`;
      if (check(res === 'DETAIL ' + want, `new gate ${ng} as ${tag}_czg on ${pair1} -> ${res.slice(0, 200)}`)) {
        expect.push({ path: want, cls: 'FlatTopGaussianPulse', fields: f.typed, gate: `qubit_pairs.${pair1}.macros.${tag}_czg` });
      } else defects.push({ what: 'new gate', got: res });
    }
  }

  // ---- pair CR/ZZ channel ---------------------------------------------------
  if (!chip.pcDisabled && chip.pc.length) {
    await openCreate(p, 'pulse');
    await clickSel(p, 'input[name=target_kind][value=pair_channel]');
    await choose(p, '#pulse-create-root select[name=pc_pair]', chip.pc[0]);
    const ch = await p.ev(`document.querySelector('#pulse-create-root select[name=pc_channel]').value`);
    await choose(p, '#pulse-create-type', 'SquarePulse');
    await typeInto(p, '#pulse-create-name', tag + '_pc');
    const f = await fillFields(p, { length: '64', amplitude: '0.045', axis_angle: '0.5' });
    const res = await submitCreate(p);
    const want = `qubit_pairs.${chip.pc[0]}.${ch}.operations.${tag}_pc`;
    if (check(res === 'DETAIL ' + want, `pair channel ${want} -> ${res.slice(0, 200)}`)) expect.push({ path: want, cls: 'SquarePulse', fields: f.typed });
    else defects.push({ what: 'pair channel', got: res });
  } else console.log('  pair CR/ZZ channel: not offered on this chip (disabled radio)');

  // ---- Gaussian CZ builder ----------------------------------------------------
  const gok = await openCreate(p, 'gcz');
  if (check(!!gok, 'Gaussian CZ builder opened')) {
    const gpairs = JSON.parse(await p.ev(`JSON.stringify([].slice.call(document.querySelectorAll('#gcz-form select[name=pair_id] option')).map(function(o){return o.value}))`) || '[]');
    console.log('  gcz eligible pairs: ' + gpairs.join(','));
    if (gpairs.length) {
      const gp = gpairs[Math.min(2, gpairs.length - 1)];
      await choose(p, '#gcz-form select[name=pair_id]', gp);
      await typeInto(p, '#gcz-form input[name=padding_length]', '16');
      await typeInto(p, '#gcz-form input[name=qubit_filter_mhz]', '25');
      await typeInto(p, '#gcz-form input[name=coupler_filter_mhz]', '30');
      await p.shot(`${DIR}/30_gcz_${W}.png`);
      await clickSel(p, '#gcz-form button[type=submit]');
      const gr = await waitFor(p, `(function(){var r=document.getElementById('gcz-result'); var t=r? r.innerText.trim():''; return t? t.replace(/\\s+/g,' ').slice(0,300) : ''})()`, 30000);
      check(gr && !/error|fail|refus/i.test(gr), 'Gaussian CZ created: ' + gr);
      await p.shot(`${DIR}/31_gcz_done_${W}.png`);
      expect.push({ gcz: gp, padding_length: 16, qubit_filter_mhz: 25, coupler_filter_mhz: 30 });
    }
  }

  // ---- edit what was created --------------------------------------------------
  const E0 = expect.find(e => /_dragc$/.test(e.path || ''));
  if (E0) {
    await p.ev(`htmx.ajax('GET','/pulse/detail?path=${encodeURIComponent(E0.path)}',{target:'#inspector-pane',swap:'innerHTML'})`);
    await waitFor(p, `document.querySelector('#pulse-detail-root[data-pulse-path="${E0.path}"]')?1:0`, 20000);
    const edits = { amplitude: '0.111', alpha: '-0.42', length: '52', detuning: '2000000', axis_angle: '0.3', anharmonicity: '-205000000' };
    for (const [k, v] of Object.entries(edits)) {
      const sel = `#pulse-detail-root input[data-param="${k}"]`;
      if (!(await center(p, sel))) { check(false, `edit: no ${k} field in the detail`); continue; }
      await typeInto(p, sel, v);
      await enter(p);
      const cm = await waitFor(p, `(function(){var i=document.querySelector(${J(sel)}); return i && i.getAttribute('data-committed')===${J(v)} ? 1 : 0})()`, 20000);
      if (!check(!!cm, `edit ${k}=${v} committed (data-committed=${await p.ev(`(document.querySelector(${J(sel)})||{getAttribute:function(){return 'gone'}}).getAttribute('data-committed')`)})`)) defects.push({ what: 'edit ' + k, got: await p.ev(STATUS) });
      E0.fields[k] = v;
    }
    const plotN = await waitFor(p, `(function(){var el=document.getElementById('pulse-detail-plot'); return el&&el.data&&el.data[0]? el.data[0].y.length : 0})()`, 20000);
    check(plotN === 52, `preview redrawn at the edited length (52 samples) -> ${plotN}`);
    await p.shot(`${DIR}/40_edited_${W}.png`);

    // duplicate -> rename -> delete, then Ctrl+Z the delete
    await clickSel(p, '#pulse-detail-root .pulse-actions button[onclick*=startDuplicate]');
    await typeInto(p, '.pulse-duplicate-form input[name=new_name]', tag + '_dup');
    await clickSel(p, '.pulse-duplicate-form button[type=submit]');
    const dupPath = E0.path.replace(/[^.]+$/, tag + '_dup');
    check(!!(await waitFor(p, `document.querySelector('#pulse-detail-root[data-pulse-path="${dupPath}"]')?1:0`, 20000)), 'duplicate -> ' + dupPath);
    await clickSel(p, '#pulse-detail-root .pulse-actions button[onclick*=startRename]');
    await typeInto(p, '.pulse-rename-form input[name=new_name]', tag + '_ren');
    await clickSel(p, '.pulse-rename-form button[type=submit]');
    const renPath = E0.path.replace(/[^.]+$/, tag + '_ren');
    check(!!(await waitFor(p, `document.querySelector('#pulse-detail-root[data-pulse-path="${renPath}"]')?1:0`, 20000)), 'rename -> ' + renPath);
    await clickSel(p, '#pulse-detail-root .pulse-delete-btn');
    await clickSel(p, '.pulse-delete-confirm button[type=submit]');
    await sleep(1500);
    const rowGone = await waitFor(p, `document.querySelector('tr[data-pulse-path="${renPath}"]')?0:1`, 15000);
    check(!!rowGone, 'deleted row gone from the table');
    await p.shot(`${DIR}/41_deleted_${W}.png`);
    // Ctrl+Z (focus on the page body, as after a click on empty space)
    await p.ev(`(document.activeElement&&document.activeElement.blur&&document.activeElement.blur(), 1)`);
    await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'z', code: 'KeyZ', windowsVirtualKeyCode: 90, modifiers: 2 });
    await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'z', code: 'KeyZ', windowsVirtualKeyCode: 90, modifiers: 2 });
    const back = await waitFor(p, `document.querySelector('tr[data-pulse-path="${renPath}"]')?1:0`, 20000);
    check(!!back, 'Ctrl+Z brings the deleted pulse back (' + renPath + ')');
    await p.shot(`${DIR}/42_undo_${W}.png`);
    expect.push({ path: renPath, cls: E0.cls, fields: Object.assign({}, E0.fields) });
  }

  // ---- Apply to live --------------------------------------------------------
  if (!process.env.NO_APPLY) {
    const ap = await waitFor(p, `document.querySelector('.btn-apply-live')?1:0`, 15000);
    if (check(!!ap, 'Apply-to-live button present')) {
      const txt = await p.ev(`document.querySelector('.btn-apply-live').textContent.trim()`);
      await clickSel(p, '.btn-apply-live');
      if (await p.ev(`document.querySelector('.btn-apply-live.sync-arm')?1:0`)) { await sleep(400); await clickSel(p, '.btn-apply-live'); }
      // the one-click Apply must write; a panel opening instead is a detour
      const how = await waitFor(p, `document.querySelector('#state-review-host .sp-choice-primary') && document.querySelector('#state-review-host .sp-choice-primary').offsetParent ? 'panel' : (document.querySelector('.btn-apply-live') ? '' : 'applied')`, 60000);
      if (!check(how === 'applied', `one-click Apply writes to live without a detour (${txt}) -> ${how}`)) {
        const msg = await p.ev(`(document.querySelector('#state-review-host')||{}).innerText.replace(/\\s+/g,' ').slice(0,300)`);
        defects.push({ what: 'one-click Apply opened the sync panel instead of writing', got: msg });
        await p.shot(`${DIR}/50_apply_detour_${W}.png`);
        await clickSel(p, '#state-review-host .sp-choice-primary');
        if (await p.ev(`document.querySelector('#state-review-host .sp-choice-primary.sync-arm')?1:0`)) { await sleep(400); await clickSel(p, '#state-review-host .sp-choice-primary'); }
        const done2 = await waitFor(p, `document.querySelector('.btn-apply-live')?0:1`, 60000);
        check(!!done2, 'applied to live through the panel');
      }
      await sleep(1500);
      await p.shot(`${DIR}/50_applied_${W}.png`);
    }
  }

  // ---- reload: intact ---------------------------------------------------------
  const pr = p.send('Page.reload'); await Promise.race([pr, sleep(4000)]);
  const again = await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length`, 60000);
  check(again > 10, `after reload the table is back (${again} rows)`);
  const createdRows = await p.ev(`[].slice.call(document.querySelectorAll('tr[data-pulse-path]')).filter(function(r){return /${tag}_/.test(r.getAttribute('data-pulse-path'))}).length`);
  console.log('  created rows visible after reload: ' + createdRows);
  await p.shot(`${DIR}/60_reload_${W}.png`);
  clearInterval(dlgTimer);
  const errs = allErr();
  check(errs.length === 0, 'console clean (' + errs.join(' | ').slice(0, 400) + ')');
  fs.writeFileSync(`${DIR}/expect.json`, J({ expect, defects, dialogs, toolbar, out }, null, 1));
  await p.close();
  console.log(bad ? `${bad} FAILED` : 'ALL OK');
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
