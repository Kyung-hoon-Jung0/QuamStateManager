/* Journey (w9/pulsegate, user decision 2026-09-28): pulses are added, removed
 * and renamed on the Pulses page only. Real headless Chrome, every click a
 * real mouse event, every screenshot written for a person to look at.
 *
 *   T  the Json Tree: hover a pulse -> no ＋/✕, the note "Pulses are added,
 *      removed and renamed on the Pulses page" + its link; the same on the
 *      operations dict; a field inside the pulse and the channel keep their
 *      buttons
 *   L  the link: lands on the Pulses page ON that pulse (inspector open, the
 *      owner's rows, the channel tab) -> Back -> the tree is whole -> reload
 *      -> still whole; the operations dict's link lands on the channel's rows
 *   J  the tree's JSON editor: dropping an op is refused in place with the
 *      link; nothing written (server + tray)
 *   V  a value edit inside a pulse still commits from the tree (Enter), and
 *      Ctrl+Z takes it back
 *   G  a grid (Live Edit) value edit of a pulse field still commits
 *   O  (GATE=<gate path>) the tree's lab "Delete together" is a link to the
 *      same offer on the Pulses page -> the delete step opens with it -> press
 *      -> gone (server) -> Ctrl+Z -> back byte-equal
 *
 *   SM_CDP_PORT=<cdp> PORT=<sm> SHOT_DIR=<dir> [Q=q1] [CH=xy] [GATE=...] node pulse_gate.cjs
 */
'use strict';
const fs = require('fs');
const { open, sleep, base, baseFrom } = require('./cdp.cjs');

const PORT = +(process.env.PORT || 5363);
const BASE = base(PORT);   // docs/226: SM_BASE_URL (proxy + prefix) wins
const DIR = process.env.SHOT_DIR || '.';
const SLOW = +(process.env.SLOW || 1);
const Q = process.env.Q || 'q1', CH = process.env.CH || 'xy';
const GATE = process.env.GATE || '';
fs.mkdirSync(DIR, { recursive: true });
const J = JSON.stringify;
const out = []; let bad = 0; const timing = {};
function check(c, m) { const l = (c ? 'ok   ' : 'FAIL ') + m; out.push(l); console.log(l); if (!c) bad++; return c; }
async function waitFor(p, expr, ms = 60000) {
  ms *= SLOW;
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await p.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(120);
  }
  return null;
}
const NOTE = 'Pulses are added, removed and renamed on the Pulses page';
const PEEK = (paths) => `fetch('/field/peek?'+${J(paths)}.map(function(x){return 'dot_path='+encodeURIComponent(x)}).join('&')).then(function(r){return r.json()}).then(function(d){var o={};${J(paths)}.forEach(function(x){o[x]= d.errors&&d.errors[x] ? '<absent>' : JSON.stringify(d.values[x])}); return JSON.stringify(o)})`;
const TRAY = `parseInt((document.getElementById('pending-tray')||{getAttribute:function(){return '0'}}).getAttribute('data-change-count')||'0',10)`;

async function pressZ(p) {
  await p.ev(`(document.activeElement&&document.activeElement.blur&&document.activeElement.blur(), 1)`);
  await p.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'z', code: 'KeyZ', windowsVirtualKeyCode: 90, modifiers: 2 });
  await p.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'z', code: 'KeyZ', windowsVirtualKeyCode: 90, modifiers: 2 });
}
// reveal a tree node by its path (the search, like a user), then hover its row
async function reveal(p, path) {
  const segs = path.split('.');
  const term = segs.slice(-2).join(' ');
  await p.ev(`(function(){var s=document.getElementById('explorer-search'); s.focus(); s.value=${J(term)}; s.dispatchEvent(new Event('input',{bubbles:true})); return 1})()`);
  const got = await waitFor(p, `document.querySelector('.tree-node[data-path=${J(path)}]')?1:0`, 30000);
  await p.key('Escape', 'Escape', 27);
  await sleep(250);
  return !!got;
}
async function hoverRow(p, path) {
  const r = await p.ev(`(function(){var n=document.querySelector('.tree-node[data-path=${J(path)}]'); if(!n) return null; var r=n.querySelector(':scope > .tree-row'); r.scrollIntoView({block:'center'}); var b=r.getBoundingClientRect(); return [b.left+30,b.top+b.height/2]})()`);
  if (!r) return null;
  await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r[0] + 5, y: r[1] });
  await p.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: r[0], y: r[1] });
  await sleep(250);
  return p.ev(`(function(){var n=document.querySelector('.tree-node[data-path=${J(path)}]'); var s=n.querySelector(':scope > .tree-row > .tree-row-actions'); var g=s&&s.querySelector('.tree-pulse-gate'); var a=g&&g.querySelector('a');
    return {add:!!(s&&s.querySelector('.tree-act-add')), del:!!(s&&s.querySelector('.tree-act-del')), note:g?g.textContent:'',
            shown:g?getComputedStyle(g).opacity:'', href:a?a.getAttribute('href'):''}})()`);
}
async function clickIn(p, rootSel, sel) {
  // the FIRST line box of the element: a wrapped inline link's bounding box
  // has a gap a real mouse would not aim at
  const r = await p.ev(`(function(){var root=document.querySelector(${J(rootSel)}); var e=root&&root.querySelector(${J(sel)}); if(!e) return null; e.scrollIntoView({block:'center'}); var b=(e.getClientRects()[0])||e.getBoundingClientRect(); return [b.left+b.width/2,b.top+b.height/2]})()`);
  if (!r) return false;
  await p.click(r[0], r[1]);
  return true;
}

(async () => {
  const p = await open(`${BASE}/explorer`);
  await waitFor(p, `document.querySelector('#explorer-tree-state .tree-node')?1:0`, 120000);
  const mark = p.events.length;
  const ops = JSON.parse(await p.ev(PEEK([`qubits.${Q}.${CH}.operations`])))[`qubits.${Q}.${CH}.operations`];
  const OPD = JSON.parse(ops);
  // a pulse with a NUMERIC field (a pointer field would make the value edit
  // below a no-op that proves nothing)
  const num = k => ['amplitude', 'length'].find(f => typeof (OPD[k] || {})[f] === 'number');
  const opNames = Object.keys(OPD).filter(k => OPD[k] && typeof OPD[k] === 'object' && num(k));
  const OP = `qubits.${Q}.${CH}.operations.${opNames[0]}`;
  const OPS = `qubits.${Q}.${CH}.operations`;
  const FIELD = `${OP}.` + num(opNames[0]);
  console.log('pulse', OP, 'field', FIELD, 'payload rows_known', await p.ev('window._treePulseGate && window._treePulseGate.rows_known'));

  // T ---------------------------------------------------------------------
  if (check(await reveal(p, OP), `T: the tree shows ${OP}`)) {
    const a = await hoverRow(p, OP);
    check(a && !a.add && !a.del, 'T: no ＋/✕ on the pulse ' + J(a));
    check(a && a.note === NOTE && a.shown === '1', `T: the note shows on hover: "${a && a.note}"`);
    check(a && a.href === '/pulses/goto?path=' + encodeURIComponent(OP), 'T: its link names the pulse ' + (a && a.href));
    await p.shot(`${DIR}/T1_tree_pulse_hover.png`);
    const b = await hoverRow(p, OPS);
    check(b && !b.add && !b.del && b.note === NOTE, 'T: the operations dict: no ＋/✕, the note ' + J(b));
    await p.shot(`${DIR}/T2_tree_ops_hover.png`);
    const f = await hoverRow(p, FIELD);
    check(f && f.del && !f.note, 'T: a field inside the pulse keeps its ✕, no note ' + J(f));
    const c = await hoverRow(p, `qubits.${Q}.${CH}`);
    check(c && c.add && c.del && !c.note, 'T: the channel keeps ＋ and ✕ ' + J(c));
    await p.shot(`${DIR}/T3_tree_channel_hover.png`);
  }

  // L ---------------------------------------------------------------------
  {
    const a = await hoverRow(p, OP);
    const t0 = Date.now();
    const clicked = a && await clickIn(p, `.tree-node[data-path=${J(OP)}] > .tree-row`, '.tree-pulse-gate a');
    check(!!clicked, 'L: the link is clickable (hit-tested by the real mouse)');
    const landed = await waitFor(p, `((window.SM?window.SM.path(location.pathname):location.pathname)==='/pulses' && document.querySelector('#inspector-pane #pulse-detail-root[data-pulse-path=${J(OP)}]'))?location.search:0`, 90000);
    timing.link_to_pulse_ms = Date.now() - t0;
    check(!!landed, `L: the Pulses page opened ON the pulse (${timing.link_to_pulse_ms} ms) ${landed}`);
    const params = new URLSearchParams(landed || '');
    check(params.get('pulse') === OP && params.get('owner') === Q && params.get('channel') === CH,
      'L: the address names the pulse, its owner and its channel tab');
    const row = await waitFor(p, `document.querySelector('tr[data-pulse-path=${J(OP)}]')?1:0`, 30000);
    check(!!row, "L: the pulse's row is in the table beside it");
    const tab = await p.ev(`(document.querySelector('#pulse-channel-tabs a.active')||{}).textContent||''`);
    check(new RegExp(CH, 'i').test(tab), `L: the ${CH} tab is the active one ("${tab.trim()}")`);
    await p.shot(`${DIR}/L1_landed_on_pulse.png`);
    // Back: the tree, whole
    await p.ev('history.back()');
    const back = await waitFor(p, `((window.SM?window.SM.path(location.pathname):location.pathname)==='/explorer' && document.querySelector('#explorer-tree-state .tree-node'))?1:0`, 60000);
    check(!!back, 'L: Back returns to the Json Tree');
    const ok2 = await waitFor(p, `document.querySelector('.tree-node[data-path=${J(OP)}]')?1:0`, 20000) || await reveal(p, OP);
    const a2 = ok2 && await hoverRow(p, OP);
    check(a2 && a2.note === NOTE && !a2.del, 'L: ...whole: the pulse row still says where, still no ✕');
    await p.shot(`${DIR}/L2_back_in_tree.png`);
    // reload
    await p.send('Page.reload', {});
    await sleep(800);
    await waitFor(p, `document.readyState==='complete' && document.querySelector('#explorer-tree-state .tree-node')?1:0`, 120000);
    check(await reveal(p, OP), 'L: after reload the tree reveals the pulse');
    const a3 = await hoverRow(p, OP);
    check(a3 && a3.note === NOTE && !a3.del, 'L: after reload the note is there, no ✕');
    // the operations dict's link
    const b = await hoverRow(p, OPS);
    if (check(b && b.note === NOTE, 'L: the operations dict has its link')) {
      await clickIn(p, `.tree-node[data-path=${J(OPS)}] > .tree-row`, '.tree-pulse-gate a');
      const ch = await waitFor(p, `((window.SM?window.SM.path(location.pathname):location.pathname)==='/pulses' && document.querySelectorAll('tr[data-pulse-path]').length)?location.search:0`, 90000);
      const pp = new URLSearchParams(ch || '');
      check(pp.get('owner') === Q && pp.get('channel') === CH && !pp.get('pulse'),
        `L: the operations dict lands on ${Q}'s ${CH} rows (${ch})`);
      const rows = await p.ev(`[].slice.call(document.querySelectorAll('tr[data-pulse-path]')).map(function(r){return r.getAttribute('data-pulse-path')})`);
      check(rows.length > 0 && rows.every(x => x.indexOf(`qubits.${Q}.${CH}.operations.`) === 0),
        `L: ...and only those (${rows.length} rows)`);
      await p.shot(`${DIR}/L3_ops_dict_landed.png`);
      await p.ev('history.back()');
      await waitFor(p, `((window.SM?window.SM.path(location.pathname):location.pathname)==='/explorer' && document.querySelector('#explorer-tree-state .tree-node'))?1:0`, 60000);
    }
  }

  // J ---------------------------------------------------------------------
  if (await reveal(p, OPS)) {
    const tray0 = await p.ev(TRAY);
    const before = await p.ev(PEEK([OPS]));
    await hoverRow(p, OPS);
    await clickIn(p, `.tree-node[data-path=${J(OPS)}] > .tree-row`, '.tree-json-edit-btn');
    const ta = await waitFor(p, `document.querySelector('.tree-node[data-path=${J(OPS)}] .tree-json-textarea')?1:0`, 10000);
    if (check(!!ta, 'J: the JSON editor opens on the operations dict')) {
      await p.ev(`(function(){var t=document.querySelector('.tree-node[data-path=${J(OPS)}] .tree-json-textarea'); var o=JSON.parse(t.value); delete o[${J(opNames[0])}]; t.value=JSON.stringify(o,null,2); return 1})()`);
      await clickIn(p, `.tree-node[data-path=${J(OPS)}] .tree-json-editor-bar`, 'button');
      const err = await waitFor(p, `(function(){var e=document.querySelector('.tree-node[data-path=${J(OPS)}] .tree-json-err'); return (e&&!e.hidden&&e.textContent)?e.textContent:0})()`, 30000);
      check(!!err && /Pulses page/.test(err) && /remove/.test(err), `J: refused in place: "${(err || '').slice(0, 160)}"`);
      check(!!(await p.ev(`document.querySelector('.tree-node[data-path=${J(OPS)}] .tree-json-err a.tree-pulses-link')?1:0`)), 'J: ...with the link');
      await p.shot(`${DIR}/J1_json_edit_refused.png`);
      check((await p.ev(PEEK([OPS]))) === before && (await p.ev(TRAY)) === tray0, 'J: nothing written (server, tray)');
      await p.key('Escape', 'Escape', 27);
    }
  }

  // V ---------------------------------------------------------------------
  if (await reveal(p, FIELD)) {
    const before = await p.ev(PEEK([FIELD]));
    const v0 = JSON.parse(JSON.parse(before)[FIELD]);
    const v1 = Number.isInteger(v0) ? v0 + 4 : +(v0 * 0.9).toPrecision(6);
    check(typeof v0 === 'number' && v1 !== v0, `V: a numeric field to edit (${FIELD} = ${v0})`);
    await clickIn(p, `.tree-node[data-path=${J(FIELD)}] > .tree-row`, '.tree-val');
    await waitFor(p, `document.querySelector('.tree-node[data-path=${J(FIELD)}] input.tree-edit-input')?1:0`, 10000);
    await p.ev(`(function(){var i=document.querySelector('.tree-node[data-path=${J(FIELD)}] input.tree-edit-input'); i.focus(); i.select(); return 1})()`);
    await p.send('Input.insertText', { text: String(v1) });
    const t0 = Date.now();
    await p.key('Enter', 'Enter', 13);
    const got = await waitFor(p, `(function(){return ${PEEK([FIELD])}.then(function(s){return JSON.parse(JSON.parse(s)[${J(FIELD)}])===${J(v1)}?1:0})})()`, 60000);
    timing.tree_value_commit_ms = Date.now() - t0;
    check(!!got, `V: a value inside the pulse commits from the tree (${v0} -> ${v1}, ${timing.tree_value_commit_ms} ms)`);
    await p.shot(`${DIR}/V1_value_edit_in_pulse.png`);
    await sleep(500);
    await pressZ(p);
    const back = await waitFor(p, `(function(){return ${PEEK([FIELD])}.then(function(s){return s===${J(before)}?1:0})})()`, 60000);
    check(!!back, 'V: Ctrl+Z takes it back');
  }

  // O (a lab gate whose delete must take a pulse with it) ----------------
  if (GATE) {
    const offer = await p.ev(`fetch('/api/pulse/delete-together/offer?path='+encodeURIComponent(${J(GATE)})).then(function(r){return r.text()})`);
    const m = /data-together='([^']*)'/.exec(offer || '');
    const together = m ? JSON.parse(m[1].replace(/&#34;/g, '"').replace(/&quot;/g, '"')) : null;
    if (check(!!together && together.length > 1, `O: the lab refuses ${GATE} alone (set ${J(together)})`)) {
      const before = await p.ev(PEEK(together));
      if (check(await reveal(p, GATE), 'O: the tree shows the gate')) {
        const g = await hoverRow(p, GATE);
        check(g && g.del && !g.note, 'O: the gate (not a pulse) keeps its ✕');
        await clickIn(p, `.tree-node[data-path=${J(GATE)}] > .tree-row`, '.tree-act-del');
        await sleep(300);
        await clickIn(p, `.tree-node[data-path=${J(GATE)}] > .tree-row`, '.tree-row-actions .tree-act-btn');
        const link = await waitFor(p, `(function(){var a=document.querySelector('.tree-node[data-path=${J(GATE)}] > .tree-row > .tree-edit-err a.tree-pulses-link'); return a? a.textContent : 0})()`, 120000);
        check(!!link && /on the Pulses page$/.test(link) && /^Delete together/.test(link), `O: the offer is a link: "${link}"`);
        check(!(await p.ev(`document.querySelector('.tree-node[data-path=${J(GATE)}] .tree-cascade-btn')?1:0`)), 'O: ...and no batch button in the tree');
        await p.shot(`${DIR}/O1_tree_offer_link.png`);
        const t0 = Date.now();
        await clickIn(p, `.tree-node[data-path=${J(GATE)}] > .tree-row > .tree-edit-err`, 'a.tree-pulses-link');
        const opened = await waitFor(p, `(function(){var b=document.querySelector('#pulse-delete-result .pulse-delete-refused'); var c=document.querySelector('.pulse-delete-confirm'); return ((window.SM?window.SM.path(location.pathname):location.pathname)==='/pulses' && b && c && !c.hidden)? b.getAttribute('data-refused-path') : 0})()`, 120000);
        timing.link_to_offer_ms = Date.now() - t0;
        check(opened === GATE, `O: the Pulses page opens the delete step on the same offer (${timing.link_to_offer_ms} ms)`);
        const shown = await p.ev(`JSON.parse(document.querySelector('.pulse-delete-refused').getAttribute('data-together'))`);
        check(J(shown) === J(together), `O: ...the same set ${J(shown)}`);
        await p.shot(`${DIR}/O2_pulses_page_offer.png`);
        const t1 = Date.now();
        await clickIn(p, '#pulse-delete-result', '.pulse-delete-together');
        const gone = await waitFor(p, `(function(){return ${PEEK(together)}.then(function(s){return s.split('<absent>').length===${together.length + 1}?1:0})})()`, 120000);
        timing.delete_together_ms = Date.now() - t1;
        check(!!gone, `O: all ${together.length} went in one batch (${timing.delete_together_ms} ms)`);
        await p.shot(`${DIR}/O3_deleted_together.png`);
        await sleep(800);
        await pressZ(p);
        const back = await waitFor(p, `(function(){return ${PEEK(together)}.then(function(s){return s===${J(before)}?1:0})})()`, 90000);
        check(!!back, 'O: one Ctrl+Z restores every path byte-equal');
        await p.shot(`${DIR}/O4_undone.png`);
        await p.ev('history.back()');
        const tb = await waitFor(p, `((window.SM?window.SM.path(location.pathname):location.pathname)==='/explorer' && document.querySelector('#explorer-tree-state .tree-node'))?1:0`, 60000);
        check(!!tb, 'O: Back returns to the tree');
      }
    }
  }

  // G (a grid value edit of a pulse field) ---------------------------------
  {
    await p.send('Page.navigate', { url: `${BASE}/bulk` });
    await waitFor(p, `document.readyState==='complete' && document.querySelectorAll('input.bulk-cell').length`, 180000);
    const sel = await p.ev(`(function(){var c=[].slice.call(document.querySelectorAll('input.bulk-cell:not([readonly])')).filter(function(e){var d=e.getAttribute('data-dot-path')||''; return /\\.operations\\.[^.]+\\.(amplitude|length)$/.test(d) && !e.dataset.isPointer && /^-?[0-9.]+$/.test(e.value);}); if(!c.length) return null; c[0].scrollIntoView({block:'center',inline:'center'}); return c[0].getAttribute('data-dot-path');})()`);
    if (check(!!sel, `G: the grid has a pulse-field cell (${sel})`)) {
      const before = await p.ev(PEEK([sel]));
      const v0 = JSON.parse(JSON.parse(before)[sel]);
      const v1 = Number.isInteger(v0) ? v0 + 4 : +(v0 * 0.9).toPrecision(6);
      await clickIn(p, 'body', `input.bulk-cell[data-dot-path=${J(sel)}]`);
      await p.ev(`(function(){var e=document.querySelector('input.bulk-cell[data-dot-path=${J(sel)}]'); e.focus(); e.select(); return 1})()`);
      await p.send('Input.insertText', { text: String(v1) });
      const t0 = Date.now();
      await p.key('Enter', 'Enter', 13);
      const got = await waitFor(p, `(function(){return ${PEEK([sel])}.then(function(s){var v=JSON.parse(JSON.parse(s)[${J(sel)}]); return Math.abs(v-${v1})<1e-12?1:0})})()`, 90000);
      timing.grid_commit_ms = Date.now() - t0;
      check(!!got, `G: a grid edit of a pulse field commits (${v0} -> ${v1}, ${timing.grid_commit_ms} ms)`);
      await p.shot(`${DIR}/G1_grid_pulse_field.png`);
      await sleep(500);
      await pressZ(p);
      const back = await waitFor(p, `(function(){return ${PEEK([sel])}.then(function(s){return s===${J(before)}?1:0})})()`, 60000);
      check(!!back, 'G: Ctrl+Z takes it back');
    }
  }

  const all = p.errors(mark);
  const errs = all.filter(m => !/status of 40[09] \((BAD REQUEST|CONFLICT)\)/.test(m));
  console.log(`  (${all.length - errs.length} network-400/409 log lines: the refusals)`);
  check(errs.length === 0, 'no page errors: ' + J(errs.slice(0, 5)));
  fs.writeFileSync(`${DIR}/result.json`, J({ bad, timing, out }, null, 1));
  console.log(J(timing));
  console.log(bad ? `${bad} FAILED` : 'ALL OK');
  await p.close();
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
