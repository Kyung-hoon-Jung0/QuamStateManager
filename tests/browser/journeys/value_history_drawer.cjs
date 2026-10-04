/* docs/282 journey: the value drawer and Column History on the change ledger,
 * walked like a person: open Live State Edit, open the clock on a T1 cell and on
 * an alias cell, open a Column History, Use a value, close, Back, reload --
 * the page stays whole and the console stays clean.
 *
 * Env: SM_URL (the served SM), SM_CDP_PORT (headless Chrome), SHOTS (a folder),
 *      VH_T1 (a T1 dot-path), VH_ALIAS (an alias dot-path, e.g. through
 *      resonator.operations.readout).
 * Run: node tests/browser/journeys/value_history_drawer.cjs
 */
'use strict';
const path = require('path');
const fs = require('fs');
const { open, sleep } = require('./cdp.cjs');

const URL0 = process.env.SM_URL || 'http://127.0.0.1:5157/';
const SHOTS = process.env.SHOTS || path.join(process.cwd(), 'shots');
const T1 = process.env.VH_T1 || 'qubits.qA1.T1';
const ALIAS = process.env.VH_ALIAS || 'qubits.qA1.resonator.operations.readout.amplitude';
const T1_COL = process.env.VH_T1_COL || 'T1';
const ALIAS_COL = process.env.VH_ALIAS_COL || 'readout_amplitude';
fs.mkdirSync(SHOTS, { recursive: true });

let fails = 0;
const log = [];
function ok(c, m) { log.push((c ? 'ok - ' : 'FAIL: ') + m); if (!c) fails++; console.log((c ? 'ok - ' : 'FAIL: ') + m); }

async function waitFor(b, expr, ms = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await b.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(150);
  }
  return null;
}

async function center(b, sel) {
  return b.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;
    e.scrollIntoView({block:'center',inline:'center'});var r=e.getBoundingClientRect();
    return r.width&&r.height?[r.left+r.width/2,r.top+r.height/2]:null;})()`);
}

async function clickSel(b, sel) {
  const c = await center(b, sel);
  if (!c) return false;
  await sleep(120);
  const c2 = await center(b, sel);
  await b.click(c2[0], c2[1]);
  return true;
}

async function openCellHistory(b, dotPath, tag, colKey) {
  const cell = `.bulk-cell[data-dot-path="${dotPath}"]`;
  if (colKey && !(await b.ev(`!!document.querySelector(${JSON.stringify(cell)})`))) {
    // a cold column (the grid hydrates what is on screen): scroll its header in, as a person would
    await b.ev(`(function(){var th=document.querySelector('th[data-col-key="${colKey}"]');if(th)th.scrollIntoView({block:'nearest',inline:'center'});})()`);
    await sleep(800);
  }
  const has = await waitFor(b, `!!document.querySelector(${JSON.stringify(cell)})`, 20000);
  ok(!!has, `${tag}: the ${dotPath} cell is on the grid`);
  if (!has) return false;
  await clickSel(b, cell);
  const btn = await waitFor(b, `(function(){var b=document.getElementById('fh-cellbtn');return b&&b.offsetParent?1:0})()`, 5000);
  ok(!!btn, `${tag}: focusing the cell shows its clock button`);
  if (!btn) return false;
  await clickSel(b, '#fh-cellbtn');
  const done = await waitFor(b, `(function(){var p=document.getElementById('field-history-panel');
    if(!p||p.style.display==='none')return null;
    if(p.querySelector('.vh-table,.fh-table,.fh-empty:not(:first-child)'))return 'rows';
    if(p.querySelector('.vh-wait'))return null; return null;})()`, 30000);
  ok(done === 'rows', `${tag}: the drawer shows a history (not stuck on loading/building)`);
  await sleep(400);
  return true;
}

(async function main() {
  const b = await open(URL0);
  const mark0 = b.events.length;
  // 1. Live State Edit through the sidebar, as a person would
  const live = await waitFor(b, `(function(){var a=[...document.querySelectorAll('a[href]')].find(x=>/\\/bulk(\\?|$)/.test(x.getAttribute('href')));return a?a.getAttribute('href'):null})()`, 15000);
  ok(!!live, 'the sidebar has Live State Edit');
  await clickSel(b, `a[href="${live}"]`);
  await waitFor(b, `location.pathname.indexOf('/bulk')===0`, 15000);
  await waitFor(b, `!!document.querySelector('.bulk-cell')`, 30000);
  await b.shot(path.join(SHOTS, '1_live_edit.png'));

  // 2. the clock on a T1 cell
  await openCellHistory(b, T1, 'T1', T1_COL);
  const t1 = await b.ev(`(function(){var p=document.getElementById('field-history-panel');return {
      ledger: !!p.querySelector('.vh-table'), foot: (p.querySelector('.fh-foot')||{}).textContent||'',
      rows: [...p.querySelectorAll('tr.vh-row')].map(r=>[r.querySelector('.fh-val code').textContent, r.querySelector('.vh-by').textContent, r.getAttribute('data-provenance'), !!r.querySelector('.fh-data')]),
      notes: [...p.querySelectorAll('.vh-note')].map(n=>n.textContent)}})()`);
  console.log(JSON.stringify(t1));
  ok(t1 && t1.ledger && /change ledger/.test(t1.foot), 'T1: the drawer reads the change ledger');
  ok(t1 && t1.rows.every(r => !r[3] || r[2] === 'run_proven' || r[2] === 'sm'),
     'T1: only rows the ledger proves a run wrote carry a Data link');
  await b.shot(path.join(SHOTS, '2_t1_drawer.png'));

  // 3. Use a value: fills the cell, stages nothing
  const useOk = await b.ev(`!!document.querySelector('#field-history-panel .fh-use')`);
  if (useOk) {
    const want = await b.ev(`document.querySelector('#field-history-panel .fh-use').getAttribute('data-value')`);
    await clickSel(b, '#field-history-panel .fh-use');
    await sleep(300);
    const got = await b.ev(`document.querySelector('.bulk-cell[data-dot-path="${T1}"]').value`);
    ok(got === want, `Use fills the T1 cell with the drawer's value (${want} -> ${got})`);
    await b.key('Escape', 'Escape', 27);
    await sleep(200);
    await b.shot(path.join(SHOTS, '3_after_use.png'));
    // put the cell back (no staging happened: Use never stages)
    await b.ev(`(function(){var c=document.querySelector('.bulk-cell[data-dot-path="${T1}"]');c.value=c.defaultValue;c.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  } else {
    ok(true, 'Use: no older value to use on this cell (only the current one)');
  }

  // 4. the clock on an alias cell
  await openCellHistory(b, ALIAS, 'alias', ALIAS_COL);
  const al = await b.ev(`(function(){var p=document.getElementById('field-history-panel');return {
      path: (p.querySelector('.fh-path')||{}).textContent||'', via: (p.querySelector('.vh-via')||{}).textContent||'',
      rows: [...p.querySelectorAll('tr.vh-row')].map(r=>[r.querySelector('.fh-val code').textContent, r.querySelector('.vh-by').textContent, (r.querySelector('.vh-sub')||{}).textContent||''])}})()`);
  console.log(JSON.stringify(al));
  // the grid hands the drawer the cell's RESOLVED holder (data-resolved): no hop left to name
  ok(al && /readout_square\.amplitude$/.test(al.path.trim()) && al.rows.length > 0,
     'alias cell: the drawer reads the holder the alias names (' + (al && al.path.trim()) + ')');
  ok(al && al.rows.every(r => r[2].length > 0), 'alias cell: every row says what is known about its writer');
  await b.shot(path.join(SHOTS, '4_alias_drawer.png'));
  await b.key('Escape', 'Escape', 27);
  await sleep(250);
  ok(await b.ev(`document.getElementById('field-history-panel').style.display==='none'`), 'Escape closes the drawer');

  // the alias column's Column History: the same points as the drawer, and the hop named
  await clickSel(b, `th[data-col-key="${ALIAS_COL}"] .bulk-col-hist`);
  const ac = await waitFor(b, `(function(){var c=document.querySelector('.ch-overlay .ch-card');if(!c||c.closest('.ch-overlay').style.display==='none')return null;
     var tr=c.querySelector('.ch-chg-table tr[data-row="qA1"]');if(!tr)return null;
     return {via:(tr.querySelector('.vh-chip-via')||{}).textContent||'',
             vals:[...tr.querySelectorAll('.vh-chip code')].map(x=>x.textContent)}})()`, 30000);
  console.log(JSON.stringify(ac));
  ok(ac && /via readout\s*→\s*readout_square/.test(ac.via), 'alias column: Column History names the hop (via readout → readout_square)');
  ok(ac && al && JSON.stringify(ac.vals) === JSON.stringify(al.rows.map(r => r[0])),
     'alias column: Column History shows the same points as the drawer for qA1 (' + (ac && ac.vals.join(', ')) + ')');
  await b.shot(path.join(SHOTS, '4b_alias_column.png'));
  await b.key('Escape', 'Escape', 27);
  await sleep(300);

  // 5. Column History of the T1 column
  const colKey = await b.ev(`(function(){var c=document.querySelector('.bulk-cell[data-dot-path="${T1}"]');var td=c&&c.closest('td');return td?td.getAttribute('data-col-key'):null})()`);
  ok(!!colKey, 'the T1 column has a key');
  await clickSel(b, `th[data-col-key="${colKey}"] .bulk-col-hist`);
  const card = await waitFor(b, `(function(){var c=document.querySelector('.ch-overlay .ch-card');if(!c||c.closest('.ch-overlay').style.display==='none')return null;
     return c.querySelector('.ch-chg-table')?'ok':null})()`, 30000);
  ok(card === 'ok', 'Column History opens with its Changes table');
  const ch = await b.ev(`(function(){var c=document.querySelector('.ch-overlay .ch-card');return {foot:(c.querySelector('.ch-chg-foot')||{}).textContent||'',
     chips:c.querySelectorAll('.vh-chip').length, data:c.querySelectorAll('.ch-chip-data').length}})()`);
  console.log(JSON.stringify(ch));
  ok(ch && /change ledger/.test(ch.foot) && ch.chips > 0, 'Column History reads the change ledger');
  await b.shot(path.join(SHOTS, '5_column_changes.png'));
  await clickSel(b, '.ch-overlay .ch-tab[data-view="byrun"]');
  await sleep(300);
  await b.shot(path.join(SHOTS, '6_column_byrun.png'));
  await clickSel(b, '.ch-overlay .ch-tab[data-view="changes"]');
  await b.key('Escape', 'Escape', 27);
  await sleep(300);
  ok(await b.ev(`document.querySelector('.ch-overlay').style.display==='none'`), 'Escape closes Column History');

  // 6. Back, then reload: the page is whole
  await b.ev('history.back()');
  await sleep(1500);
  await b.shot(path.join(SHOTS, '7_after_back.png'));
  ok(await b.ev(`!!document.querySelector('nav, .sidebar, #sidebar')`), 'after Back the page still has its sidebar');
  await b.send('Page.reload', {});
  await sleep(2500);
  await waitFor(b, `document.readyState==='complete'`, 15000);
  await b.shot(path.join(SHOTS, '8_after_reload.png'));
  ok(await b.ev(`!!document.querySelector('nav, .sidebar, #sidebar')`), 'after reload the page is whole');
  const errs = b.errors(mark0);
  ok(errs.length === 0, 'console errors: ' + errs.length + (errs.length ? ' -- ' + errs.join(' | ') : ''));
  await b.close();
  fs.writeFileSync(path.join(SHOTS, 'journey.log'), log.join('\n'));
  console.log(fails ? `${fails} FAILED` : 'ALL OK');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error('FAIL: journey threw', e); process.exit(1); });
