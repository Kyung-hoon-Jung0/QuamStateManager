/* Phases for the apply-to-live lane. Each gets the ctx built by
 * stress_applylive.cjs. Every phase snaps the chip files around each gesture. */

const TARGET = 'qubits.q1.f_01';         // a plain numeric leaf on the qubit grid
const TARGET2 = 'qubits.q2.f_01';

function esc(s) { return s.replace(/"/g, '\\"'); }

async function openGrid(c) {
  await c.nav(c.BASE + '/bulk');
  await c.until('!!document.querySelector(\'.bulk-cell\')', 15000);
}

// Type a value into a grid cell the way a person does: focus, select-all,
// real keystrokes, then blur (change event).
async function typeCell(c, dp, value) {
  const sel = '.bulk-cell[data-dot-path="' + esc(dp) + '"]';
  const okf = await c.ev(`(function(){var e=document.querySelector("${esc(sel)}");if(!e)return 0;e.scrollIntoView({block:'center'});e.focus();e.setSelectionRange(0,e.value.length);return 1;})()`);
  if (!okf) throw new Error('cell not found: ' + dp);
  // clear via real Backspaces
  const len = await c.ev(`document.querySelector("${esc(sel)}").value.length`);
  await c.ev(`(function(){var e=document.querySelector("${esc(sel)}");e.value='';e.dispatchEvent(new Event('input',{bubbles:true}));return 1;})()`);
  for (const ch of value) await c.typeChar(ch);
  await c.sleep(150);
  const dirty = await c.ev(`document.querySelectorAll('.bulk-cell.dirty').length`);
  await c.ev(`(function(){var e=document.querySelector("${esc(sel)}");e.blur();return 1;})()`);
  await c.sleep(900);      // blur auto-stages into the tray
  return { len, dirtyWhileTyping: dirty };
}

async function cellValue(c, dp) {
  return await c.ev(`(function(){var e=document.querySelector('.bulk-cell[data-dot-path="${esc(dp)}"]');return e?e.value:null;})()`);
}
async function trayCount(c) {
  return await c.ev(`(function(){var t=document.getElementById("pending-tray");return t?parseInt(t.getAttribute("data-change-count")||"0",10):-1;})()`);
}
async function post(c, url, body, headers) {
  const h = Object.assign({ 'Content-Type': 'application/x-www-form-urlencoded', 'HX-Request': 'true' }, headers || {});
  return await c.ev(`(async function(){
    var r = await fetch(${JSON.stringify(url)}, {method:'POST', headers:${JSON.stringify(h)}, body:${JSON.stringify(body || '')}});
    var t = await r.text();
    return JSON.stringify({status:r.status, body:t.slice(0,2000)});
  })()`).then(s => JSON.parse(s));
}

module.exports = {

  // ── P1: the honest single edit ────────────────────────────────────────────
  P1_single_edit_then_apply: async (c) => {
    await openGrid(c);
    let e = c.snap('opened /bulk (no press)');
    c.ok('P1 opening the grid writes nothing', !e.changed, e.diff);

    const before = await cellValue(c, TARGET);
    const t = await typeCell(c, TARGET, '4312405999.5');
    e = c.snap('typed into a cell + blurred (auto-stages to the working state)');
    c.ok('P1 typing writes nothing live', !e.changed, e.diff);
    c.ok('P1 cell went dirty while typing', t.dirtyWhileTyping > 0, t);
    const tc = await trayCount(c);
    c.ok('P1 blur staged into the tray (visible to the person)', tc === 1, { tray: tc });

    // Now the one door.
    c.dlg.clear();
    await c.ev(`window.doStateSync('apply')`);
    await c.sleep(2500);
    e = c.snap('pressed Apply to live (doStateSync apply)');
    c.ok('P1 apply WROTE the file', e.changed, { diff: e.diff });
    c.ok('P1 apply wrote exactly the edited leaf', e.diff.length === 1 && e.diff[0].path === TARGET,
      e.diff.slice(0, 6));
    c.ok('P1 new live value is what was typed', e.diff.length && e.diff[0].to === 4312405999.5, e.diff[0]);
    c.note('P1 before value', before);
  },

  // ── P2: press Apply with an EMPTY tray ────────────────────────────────────
  P2_empty_tray_apply: async (c) => {
    await openGrid(c);
    const tc = await trayCount(c);
    c.note('P2 tray count before', tc);
    const r = await post(c, '/state/apply-to-live', 'seen_changes=' + tc, { Accept: 'application/json' });
    await c.sleep(1200);
    const e = c.snap('POST /state/apply-to-live with an EMPTY tray');
    c.ok('P2 empty apply status is 2xx or an honest refusal', r.status < 500, { status: r.status });
    c.ok('P2 empty apply writes NOTHING (or is a provable no-op)', !e.changed, e.diff);
    c.note('P2 response', { status: r.status, head: r.body.slice(0, 200) });
  },

  // ── P3: double-press, fast ────────────────────────────────────────────────
  P3_double_press: async (c) => {
    await openGrid(c);
    await typeCell(c, TARGET, '4312406111.25');
    let e = c.snap('staged one edit (P3)');
    c.ok('P3 staging writes nothing', !e.changed, e.diff);

    // two presses in the same tick
    await c.ev(`(function(){window.doStateSync('apply');window.doStateSync('apply');return 1;})()`);
    await c.sleep(3000);
    e = c.snap('pressed Apply to live TWICE in one tick');
    c.ok('P3 double press wrote the leaf once', e.changed && e.diff.length === 1 && e.diff[0].path === TARGET, e.diff.slice(0, 6));
    c.note('P3 netlog tail', c.netlog.slice(-6));

    // press again with a now-empty tray, twice
    await c.sleep(600);
    await c.ev(`(function(){window.doStateSync('apply');return 1;})()`);
    await c.sleep(1500);
    await c.ev(`(function(){window.doStateSync('apply');return 1;})()`);
    await c.sleep(1800);
    e = c.snap('pressed Apply to live twice more with an empty tray');
    c.ok('P3 empty re-press writes nothing', !e.changed, e.diff);
  },

  // ── P4: several edits, Apply all ──────────────────────────────────────────
  P4_multi_edit: async (c) => {
    await openGrid(c);
    await typeCell(c, TARGET, '4312407000.0');
    await typeCell(c, TARGET2, '4400000001.5');
    let e = c.snap('typed two cells (P4)');
    c.ok('P4 staging writes nothing', !e.changed, e.diff);
    const tc = await trayCount(c);
    c.note('P4 tray count', tc);
    await c.ev(`window.doStateSync('apply')`);
    await c.sleep(3000);
    e = c.snap('Apply to live with two staged edits');
    const paths = e.diff.map(d => d.path).sort();
    c.ok('P4 apply wrote exactly the two edited leaves', e.changed && paths.length === 2 &&
      paths.includes(TARGET) && paths.includes(TARGET2), e.diff.slice(0, 8));
  },

  // ── P5: the identical-content fast path (docs/116) ────────────────────────
  P5_identical_value: async (c) => {
    await openGrid(c);
    const cur = await c.ev(`(function(){var e=document.querySelector('.bulk-cell[data-dot-path="${esc(TARGET)}"]');return e?e.value:null;})()`);
    c.note('P5 current displayed', cur);
    // Re-type the SAME number (strip the thousands separators the grid shows)
    const plain = String(cur).replace(/,/g, '');
    await typeCell(c, TARGET, plain);
    await c.sleep(300);
    const dirty = await c.ev(`document.querySelectorAll('.bulk-cell.dirty').length`);
    c.note('P5 dirty cells after retyping the same value', dirty);
    // Force it through the server regardless: a staged no-op edit.
    const r = await post(c, '/field/edit', 'dot_path=' + encodeURIComponent(TARGET) + '&value=' + encodeURIComponent(plain));
    await c.sleep(400);
    let e = c.snap('POST /field/edit with the value already live');
    c.ok('P5 a no-op staged edit writes nothing live', !e.changed, e.diff);
    const tc2 = await post(c, '/state/apply-to-live', '', { Accept: 'application/json' });
    await c.sleep(1800);
    e = c.snap('Apply to live with an identical-content change set');
    c.ok('P5 identical-content apply does not raise', tc2.status < 400 || tc2.status === 409, { status: tc2.status, head: tc2.body.slice(0, 200) });
    c.ok('P5 identical-content apply writes no VALUE change', !e.changed || e.diff.length === 0, e.diff);
    c.note('P5 apply response', { status: tc2.status, head: tc2.body.slice(0, 300) });
  },

  // ── P6: the unseen-edit gate (docs/120) ───────────────────────────────────
  P6_unseen_gate: async (c) => {
    await openGrid(c);
    const seen = await trayCount(c);
    c.note('P6 tray count this screen shows', seen);
    // A SECOND context stages an edit the open screen never sees: a bare
    // fetch that the page's tray does not follow (exactly what a second SM
    // window does — one server, one change log, two trays).
    const st = await post(c, '/field/edit', 'dot_path=' + encodeURIComponent(TARGET2) + '&value=4400000777.5');
    c.note('P6 second-context stage status', st.status);
    await c.sleep(400);
    let e = c.snap('a SECOND context staged an edit (the open tray still shows ' + seen + ')');
    c.ok('P6 second-context staging writes nothing live', !e.changed, e.diff);
    const shownNow = await trayCount(c);
    c.ok('P6 the presser\'s screen is genuinely stale', shownNow === seen, { shows: shownNow, server_has: seen + 1 });

    // press Apply DECLARING what this screen showed
    const r = await post(c, '/state/apply-to-live', 'seen_changes=' + seen, { Accept: 'application/json' });
    await c.sleep(1500);
    e = c.snap('pressed Apply to live declaring seen_changes=' + seen);
    c.ok('P6 apply-to-live 409s on unseen edits', r.status === 409, { status: r.status, head: r.body.slice(0, 300) });
    let named = false;
    try { const j = JSON.parse(r.body); named = (j.paths || []).includes(TARGET2); c.note('P6 refusal body', j); } catch (_) { }
    c.ok('P6 the refusal NAMES the unseen path', named, r.body.slice(0, 300));
    c.ok('P6 nothing was written by the refused press', !e.changed, e.diff);

    // the same press through the real UI path (doStateSync), declining the confirm
    c.dlg.accept = false; c.dlg.clear();
    await c.ev(`(function(){var t=document.getElementById("pending-tray");t.setAttribute("data-change-count","${seen}");window.doStateSync('apply');return 1;})()`);
    await c.sleep(2500);
    e = c.snap('UI Apply to live with a stale tray, confirm DECLINED');
    c.note('P6 dialog text', c.dlg.last);
    c.ok('P6 the UI asks before applying unseen edits', !!c.dlg.last && /another State Manager window|not shown on this screen/i.test(c.dlg.last || ''), c.dlg.last);
    c.ok('P6 declining writes nothing', !e.changed, e.diff);
    c.dlg.accept = true;
  },

  // ── P6b: the OTHER apply button (working_dirty branch) ────────────────────
  // The tray's `hx-post="/state/apply-to-live"` button carries no
  // seen_changes at all. Does the gate still hold?
  P6b_unseen_gate_html_button: async (c) => {
    await openGrid(c);
    const seen = await trayCount(c);
    const st = await post(c, '/field/edit', 'dot_path=' + encodeURIComponent(TARGET2) + '&value=4400000888.5');
    c.note('P6b stage status', st.status);
    await c.sleep(400);
    let e = c.snap('second context staged (P6b)');
    c.ok('P6b staging writes nothing', !e.changed, e.diff);
    // exactly what the tray button sends: no seen_changes param at all
    const r = await post(c, '/state/apply-to-live', '');
    await c.sleep(1800);
    e = c.snap('POST /state/apply-to-live with NO seen_changes (the tray hx-post button)');
    c.note('P6b response', { status: r.status, head: r.body.slice(0, 200) });
    c.ok('P6b — an apply that declares nothing still wrote the unseen edit', e.changed, e.diff);
    c.note('P6b what moved', e.diff);
  },

  // ── P7: hostile values ────────────────────────────────────────────────────
  P7_hostile_values: async (c) => {
    await openGrid(c);
    const cases = [
      ['empty', ''],
      ['quote', '"'],
      ['1e999', '1e999'],
      ['NaN', 'NaN'],
      ['long300', 'x'.repeat(300)],
      ['hangul', '한글값입니다'],
      ['negzero', '-0'],
      ['bigint', '99999999999999999999999999'],
    ];
    for (const [name, val] of cases) {
      const r = await post(c, '/field/edit', 'dot_path=' + encodeURIComponent(TARGET) + '&value=' + encodeURIComponent(val));
      await c.sleep(250);
      let e = c.snap('staged hostile value ' + name + ' = ' + JSON.stringify(val).slice(0, 40));
      c.ok('P7 ' + name + ': staging writes nothing live', !e.changed, e.diff);
      const tc = await trayCount(c);
      const ap = await post(c, '/state/apply-to-live', '', { Accept: 'application/json' });
      await c.sleep(1500);
      e = c.snap('applied hostile value ' + name);
      c.note('P7 ' + name, { stage: r.status, stage_head: r.body.slice(0, 160), apply: ap.status, wrote: e.diff.slice(0, 4) });
      // the rule: whatever landed, it must be traceable to THIS press
      if (e.changed) {
        const bad = e.diff.filter(d => d.path !== TARGET);
        c.ok('P7 ' + name + ': only the pressed path moved', bad.length === 0, bad.slice(0, 5));
      }
      // reset for the next case
      await post(c, '/field/edit', 'dot_path=' + encodeURIComponent(TARGET) + '&value=4312405235.74519');
      await post(c, '/state/apply-to-live', '', { Accept: 'application/json' });
      await c.sleep(900);
      c.snap('reset ' + name);
    }
  },

  // ── P8: revert last apply, twice ──────────────────────────────────────────
  P8_revert_last_apply: async (c) => {
    await openGrid(c);
    await typeCell(c, TARGET, '4312409999.0');
    await c.ev(`window.doStateSync('apply')`);
    await c.sleep(2500);
    let e = c.snap('applied 4312409999.0 (P8 setup)');
    c.ok('P8 setup apply wrote', e.changed, e.diff.slice(0, 4));
    const applied = e.diff.length ? e.diff[0].to : null;

    for (const round of [1, 2]) {
      // the real button, after a tray refresh
      await c.ev(`window.htmx && window.htmx.ajax("GET","/state/tray",{target:"#pending-tray",swap:"outerHTML"})`);
      await c.sleep(1200);
      const btn = await c.ev(`(function(){var b=document.querySelector('.tray-revert-apply');return b?(b.getAttribute('hx-post')||'?'):null;})()`);
      c.note('P8 round' + round + ' revert button', btn);
      if (!btn) { c.ok('P8 round' + round + ' revert button present', false, 'no .tray-revert-apply on screen'); break; }
      c.dlg.clear(); c.dlg.accept = true;
      await c.clickSel('.tray-revert-apply');
      await c.sleep(2500);
      e = c.snap('pressed Revert last apply #' + round + ' (stages only)');
      c.ok('P8 round' + round + ' revert STAGES, writes nothing live', !e.changed, e.diff.slice(0, 5));
      c.note('P8 round' + round + ' confirm text', c.dlg.last);
      // now complete it through the one door
      await c.ev(`window.doStateSync('apply')`);
      await c.sleep(3000);
      e = c.snap('applied the reverted state #' + round);
      c.note('P8 round' + round + ' apply wrote', e.diff.slice(0, 6));
      if (round === 1) {
        c.ok('P8 revert#1 put the pre-apply value back',
          e.changed && e.diff.some(d => d.path === TARGET && d.to !== applied), e.diff.slice(0, 5));
      } else {
        c.note('P8 revert#2 wrote', e.diff.slice(0, 6));
      }
    }
  },
};
