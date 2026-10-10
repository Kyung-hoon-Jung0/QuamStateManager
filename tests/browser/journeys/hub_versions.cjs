/* docs/284 journey: the Versions panel and State History read the chip's
 * change ledger, walked like a person with real mouse events.
 *
 * Open Versions -> Diff a version against now -> Compare three -> Stage a run
 * version -> the review tray -> Apply -> Revert last apply -> State History ->
 * Restore-live refused (unsaved edits; an archive opened read-only) ->
 * Back / Forward / reload: every page stays whole and the console clean.
 *
 * The rig: a live chip whose data folder holds runs (some of another chip
 * identity, which must not be listed). Setup steps a person does elsewhere
 * (opening a folder, typing a value in Live Edit) go through the same routes
 * with fetch and are labelled "setup".
 * Env: SM_URL, SM_CDP_PORT, SHOTS, CHIP (the live chip folder), ARCHIVE (a
 * run's quam_state folder), EDIT_PATH (a numeric leaf of that chip).
 * Run: node tests/browser/journeys/hub_versions.cjs
 */
'use strict';
const path = require('path');
const fs = require('fs');
const { open, sleep } = require('./cdp.cjs');

const URL0 = (process.env.SM_URL || 'http://127.0.0.1:5165').replace(/\/$/, '');
const SHOTS = process.env.SHOTS || path.join(process.cwd(), 'shots');
const CHIP = process.env.CHIP || '';
const ARCHIVE = process.env.ARCHIVE || '';
// a numeric leaf of the rig's chip, edited (unsaved) to meet the Restore gate
const EDIT_PATH = process.env.EDIT_PATH || 'qubits.q1.T1';
fs.mkdirSync(SHOTS, { recursive: true });

let fails = 0;
function ok(c, m) { if (!c) fails++; console.log((c ? 'ok - ' : 'FAIL: ') + m); }

async function waitFor(b, expr, ms = 30000) {
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
// a real mouse click on the element, refused when something else covers it
async function clickSel(b, sel) {
  const c = await center(b, sel);
  if (!c) return false;
  await sleep(150);
  const c2 = await center(b, sel);
  const hit = await b.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});
    var h=document.elementFromPoint(${c2[0]},${c2[1]});return !!h&&(h===e||e.contains(h));})()`);
  if (!hit) { console.log('  (covered: ' + sel + ')'); return false; }
  // a real press carries the pressed-button mask: a checkbox toggles only then
  for (const [type, buttons] of [['mouseMoved', 0], ['mousePressed', 1], ['mouseReleased', 0]]) {
    await b.send('Input.dispatchMouseEvent', { type, buttons, x: c2[0], y: c2[1], button: 'left', clickCount: 1 });
  }
  await sleep(250);
  return true;
}
async function setup(b, method, url, form) {
  return b.ev(`(async function(){var f=new FormData();var d=${JSON.stringify(form || {})};
    for (var k in d) f.append(k,d[k]);
    var r=await fetch(${JSON.stringify(url)},{method:${JSON.stringify(method)},body:${method === 'GET' ? 'undefined' : 'f'},
      headers:{'X-SM-Actor':'operator'}});
    return r.status+' '+(await r.text()).replace(/<[^>]+>/g,' ').replace(/\\s+/g,' ').trim().slice(0,240);})()`);
}
async function reload(b) {
  await b.send('Page.reload');
  await sleep(500);
  return waitFor(b, "document.readyState==='complete' && !!document.querySelector('.state-version-chip')", 60000);
}
// Open the panel; while the change history is still being built after the
// chip opened, the panel draws the older snapshot path and says so -- a
// person reopens it a moment later (the first such view is kept as a shot).
let sawBuilding = false;
async function openVersions(b, ledger = true) {
  for (let i = 0; i < 40; i++) {
    await b.ev("StateVersions.close&&StateVersions.close()");
    await sleep(300);
    if (!await clickSel(b, '.state-version-chip')) { await sleep(1000); continue; }
    const src = await waitFor(b, "!document.getElementById('state-version-panel').hidden && (document.querySelector('#state-version-panel .state-versions')||{dataset:{}}).dataset.source", 30000);
    if (!ledger || src === 'ledger') return src;
    const note = await b.ev("(document.querySelector('#state-version-panel .sv-ledger-note')||{}).innerText||''");
    if (!sawBuilding && /being built|Preparing/.test(note)) {
      sawBuilding = true;
      console.log('  (the change history was still being built: "' + note.slice(0, 90) + '")');
      await b.shot(path.join(SHOTS, '00_versions_while_building.png'));
    }
    await sleep(1500);
  }
  return false;
}
const rowSel = (n) => `#state-version-panel .state-version-row:nth-child(${n})`;

let dialogs = 0;
function answerDialogs(b) {
  let seen = 0;
  return setInterval(() => {
    const ds = b.events.filter(e => e.method === 'Page.javascriptDialogOpening');
    for (; seen < ds.length; seen++) {
      dialogs++;
      console.log('  (dialog answered OK: ' + ds[seen].params.type + ' "' + (ds[seen].params.message || '').slice(0, 80) + '")');
      b.send('Page.handleJavaScriptDialog', { accept: true });
    }
  }, 100);
}
async function fullText(b, url) {
  return b.ev(`(async function(){var r=await fetch(${JSON.stringify(url)});var t=await r.text();
    var d=document.createElement('div');d.innerHTML=t;return d.innerText.replace(/\\s+/g,' ');})()`);
}

(async () => {
  const b = await open(URL0 + '/');
  b.watch = answerDialogs(b);
  const raw = () => b.events.filter(e => (e.method === 'Log.entryAdded' && e.params.entry.level === 'error')
    || e.method === 'Runtime.exceptionThrown').map(e => (e.params.entry || {}).text
    || ((e.params.exceptionDetails || {}).exception || {}).description || '');
  try {
    // setup: open the live chip (a person picks it under State Load)
    if (CHIP) console.log('setup: open the chip ->', await setup(b, 'POST', '/load', { folder: CHIP }));
    ok(await reload(b), 'the app opens with the chip and its version chip in the top bar');
    await b.send('Page.navigate', { url: URL0 + '/state-history' });
    ok(await waitFor(b, "document.readyState==='complete' && !!document.querySelector('.state-version-chip')", 60000),
       'State History is the page the walk starts on');

    // 1. Versions: the ledger's rows
    ok(await openVersions(b), 'the Versions panel opens');
    const src = await b.ev("(document.querySelector('#state-version-panel .state-versions')||{}).dataset.source");
    ok(src === 'ledger', 'its rows come from the change ledger (data-source=' + src + ')');
    const words = await b.ev("document.getElementById('state-version-panel').innerText");
    ok(/run #\d+/.test(words), 'rows name runs (run #N + node)');
    // S10: old "N runs of an uncertain chip identity" -> the note names the runs whose saved chip identity differs
    ok(/run(s)? whose saved chip identity does not match this chip.s (is|are) not listed: #\d+/.test(words),
       'runs of another chip identity are not listed, and a note says so (naming them)');
    // S10: old "From the change history: N recorded states" -> the one count line, "N versions (k older snapshots)"
    ok(/From the change history: \d+ versions?\b/.test(words), 'the foot says where the list comes from');
    await b.shot(path.join(SHOTS, '01_versions.png'));

    // 2. Diff a version against now (the third row: an older run)
    const diffable = await b.ev(`(function(){var rows=[].slice.call(document.querySelectorAll('#state-version-panel .state-version-row'));
      for (var i=2;i<rows.length;i++){var d=rows[i].querySelector('.sv-diff');if(d&&!d.disabled)return i+1;}return 0;})()`);
    ok(diffable > 0, 'an older row offers Diff');
    ok(await clickSel(b, rowSel(diffable) + ' .sv-diff'), 'Diff pressed with the mouse');
    const diffText = await waitFor(b, "(function(){var h=document.querySelector('#version-diff-overlay .version-diff');return h&&h.innerText.length>40?h.innerText:'';})()", 30000);
    ok(/Modified: \d+|No differences/.test(diffText || ''), 'the diff answers from the ledger document: ' + (diffText || '').slice(0, 90).replace(/\s+/g, ' '));
    await b.shot(path.join(SHOTS, '02_diff_vs_now.png'));
    await clickSel(b, '#version-diff-overlay .state-review-close-btn');
    ok(await waitFor(b, "document.getElementById('version-diff-overlay').style.display==='none'"), 'the diff closes back onto the list');

    // 3. Compare three versions
    for (const n of [2, 3, 4]) await clickSel(b, rowSel(n) + ' .sv-check');
    ok(await waitFor(b, "document.querySelectorAll('#state-version-panel .sv-check:checked').length===3"), 'three versions ticked');
    ok(await clickSel(b, '#sv-compare'), 'Compare pressed');
    const table = await waitFor(b, "location.pathname==='/diff/versions' && !!document.querySelector('.vc-table') && document.querySelector('.vc-table').innerText", 30000);
    ok(!!table, 'the N-way table opens');
    ok(/run #\d+/.test(table || ''), 'its columns name the runs');
    const ordered = await b.ev(`(function(){var t=[].slice.call(document.querySelectorAll('.vc-col-time')).map(function(e){return e.getAttribute('data-ts')||e.textContent;});return JSON.stringify(t);})()`);
    console.log('  columns:', ordered);
    await b.shot(path.join(SHOTS, '03_compare_three.png'));
    await b.ev('history.back()');
    ok(await waitFor(b, "location.pathname==='/state-history' && !!document.querySelector('#table-pane .sh-timeline, #table-pane .sh-toolbar, #table-pane h1')"),
       'Back returns to State History');
    ok(await b.ev("document.querySelectorAll('.state-version-chip').length===1 && document.querySelectorAll('#table-pane .sidebar, #table-pane .state-version-chip').length===0"),
       'the page is whole: one top bar, nothing nested in the pane');

    // 4. Stage a run version -> the review tray
    ok(await openVersions(b), 'Versions opens again');
    const stageRow = await b.ev(`(function(){var rows=[].slice.call(document.querySelectorAll('#state-version-panel .state-version-row'));
      for (var i=1;i<rows.length;i++){if(rows[i].querySelector('.sv-stage')&&/run #/.test(rows[i].innerText))return i+1;}return 0;})()`);
    ok(stageRow > 0, 'a run row offers Stage');
    const staged = await b.ev(`(document.querySelector('${rowSel(stageRow)} .sv-check')||{}).value`);
    console.log('  staging', staged);
    ok(await clickSel(b, rowSel(stageRow) + ' .sv-stage'), 'Stage pressed');
    ok(await waitFor(b, "document.querySelector('#pending-tray').dataset.workingDirty==='1'", 30000), 'the working state is the staged version (tray: unsaved)');
    const status = await b.ev("(document.getElementById('status-bar')||{}).innerText||''");
    ok(/Version .* loaded as the working state/.test(status), 'the status line names a Version, not a snapshot: ' + status.slice(0, 80));
    await b.shot(path.join(SHOTS, '04_staged.png'));
    ok(await clickSel(b, '#pending-tray .sync-control-main'), 'the review tray opens');
    const applySel = '#state-review-overlay [hx-post="/state/apply-to-live"]';
    ok(await waitFor(b, `!!document.querySelector('${applySel}')`, 30000), 'the review shows Apply');
    await b.shot(path.join(SHOTS, '05_review_tray.png'));
    await clickSel(b, applySel);
    await sleep(300);
    await clickSel(b, applySel);
    ok(await waitFor(b, "document.querySelector('#pending-tray').dataset.workingDirty==='0'", 30000), 'Apply writes it to live (tray clean)');
    await b.shot(path.join(SHOTS, '06_applied.png'));

    // 5. Revert last apply
    await b.ev("document.querySelector('#state-review-overlay') && (document.querySelector('#state-review-overlay').style.display='none')");
    ok(await clickSel(b, '#pending-tray .sync-control-main'), 'the sync panel opens again');
    ok(await waitFor(b, "!!document.querySelector('.tray-revert-apply')", 30000), 'it offers Revert last apply');
    await clickSel(b, '.tray-revert-apply');
    await sleep(300);
    await clickSel(b, '.tray-revert-apply');
    ok(await waitFor(b, "document.querySelector('#pending-tray').dataset.workingDirty==='1'", 30000), 'the pre-apply state is staged for review');
    ok(await waitFor(b, `!!document.querySelector('${applySel}')`, 30000) || await clickSel(b, '#pending-tray .sync-control-main'), 'the review offers Apply');
    await waitFor(b, `!!document.querySelector('${applySel}')`, 30000);
    await clickSel(b, applySel);
    await sleep(300);
    await clickSel(b, applySel);
    ok(await waitFor(b, "document.querySelector('#pending-tray').dataset.workingDirty==='0'", 30000), 'reverted on live (tray clean)');
    await b.shot(path.join(SHOTS, '07_reverted.png'));
    const after = await fullText(b, '/state/versions');
    // S10 C6: the door is named in words (story._SM_DOOR), not by its raw key
    ok((after.match(/applied by /g) || []).length >= 2 && /Applied a staged version/.test(after)
       && /Reverted the last apply/.test(after),
       'the two SM writes are listed with their actor and door (no actor was given in this tab: "a person")');

    // 6. State History
    await b.ev("document.querySelector('#state-review-overlay') && (document.querySelector('#state-review-overlay').style.display='none')");
    ok(await openVersions(b), 'Versions opens once more');
    ok(await clickSel(b, '#state-version-panel .sv-kept-note a[href="/state-history"]'),
       'State History opened from the panel foot ("full list in State History")');
    const sh = await waitFor(b, "!!document.querySelector('.sh-timeline') && document.querySelector('.sh-timeline').innerText", 30000);
    ok(/run #\d+/.test(sh || '') && /applied by /.test(sh || ''), 'State History names runs and SM writes');
    console.log('  (a state SM saw, writer unknown, is ' + (/seen by SM/.test(sh || '') ? '' : 'not ') + 'on the first page)');
    await b.shot(path.join(SHOTS, '08_state_history.png'));

    // 7. the version a Restore-live press will be tried on
    const entry = await b.ev(`(function(){var rows=[].slice.call(document.querySelectorAll('.sh-entry[data-ts*="_event-"]'));
      for (var i=1;i<rows.length;i++){if(rows[i].querySelector('.sh-restore-live')&&/run #/.test(rows[i].innerText))return rows[i].getAttribute('data-ts');}return '';})()`);
    ok(!!entry, 'a run version offers Restore to live: ' + entry);
    return { b, entry };
  } catch (e) {
    console.error(e.stack);
    fails++;
    return { b };
  }
})().then(async ({ b, entry }) => {
  // continued in the second half (kept apart so a failure above still closes the tab)
  try {
    if (entry) await second(b, entry);
  } catch (e) {
    console.error(e.stack);
    fails++;
  } finally {
    // the refusal presses answer 409 (a confirm or a refusal is the designed
    // reply); the browser logs each as a failed resource -- counted apart
    const refusals = b.errors().filter(m => /status of 409 \(CONFLICT\)/.test(m));
    const errs = b.errors().filter(m => !/status of 409 \(CONFLICT\)/.test(m));
    console.log('  (409 refusal / confirm replies logged by the browser: ' + refusals.length + ')');
    const all = b.events.filter(e => (e.method === 'Log.entryAdded' && e.params.entry.level === 'error')
      || e.method === 'Runtime.exceptionThrown').length;
    ok(errs.length === 0, 'console errors: ' + errs.length + (errs.length ? ' ' + JSON.stringify(errs) : ''));
    const filtered = b.events.filter(e => e.method === 'Log.entryAdded' && e.params.entry.level === 'error'
      && /EvalError|unsafe-eval|Content Security Policy|favicon/i.test(e.params.entry.text || ''));
    console.log('  (CSP / favicon messages the harness filters: ' + filtered.length + ')');
    filtered.forEach(e => console.log('    ' + (e.params.entry.text || '').slice(0, 160) + ' @ ' + (e.params.entry.url || '').slice(-60)));
    clearInterval(b.watch);
    console.log('  (dialogs answered: ' + dialogs + ')');
    await Promise.race([b.close(), sleep(2000)]);
    console.log(fails ? `FAILED: ${fails}` : 'ALL OK');
    process.exit(fails ? 1 : 0);
  }
});

async function second(b, entry) {
  // setup: a pending edit, as a person types one value into Live Edit (the
  // grid posts to the same door)
  console.log('setup: unsaved edit on', EDIT_PATH, '->',
              await setup(b, 'POST', '/field/edit', { dot_path: EDIT_PATH, value: '1.234e-05' }));
  await b.send('Page.reload');
  await waitFor(b, "document.readyState==='complete' && !!document.querySelector('.sh-timeline')", 60000);
  ok(await waitFor(b, "(function(){var t=document.querySelector('#pending-tray');return t.dataset.workingDirty==='1'||(+t.dataset.changeCount||0)>0;})()", 10000), 'the edit is pending (tray: 1 unapplied edit)');
  const sel = `.sh-entry[data-ts="${entry}"] .sh-restore-live`;
  const d0 = dialogs;
  ok(await clickSel(b, sel), 'Restore to live pressed on a run version');
  await sleep(800);
  ok(dialogs > d0, 'its confirm asked first and was answered OK');
  const refused = await waitFor(b, "(document.getElementById('state-history-detail')||{}).innerText||''", 20000);
  ok(/unsaved edits/.test(refused || ''), 'refused first for the unsaved edits, with a separate confirm: ' + (refused || '').slice(0, 100).replace(/\s+/g, ' '));
  await b.shot(path.join(SHOTS, '09_restore_refused_unsaved.png'));
  // setup: take the edit back (only an edit: Ctrl+Z on a clean tray would
  // take back the last APPLY, which crosses the save boundary, docs/107)
  if (await b.ev("(function(){var t=document.querySelector('#pending-tray');return t.dataset.workingDirty==='1'||(+t.dataset.changeCount||0)>0;})()"))
    console.log('setup: undo the edit ->', await setup(b, 'POST', '/undo', {}));

  // an archive (a run's own quam_state, opened read-only)
  if (ARCHIVE) {
    console.log('setup: open a run archive ->', await setup(b, 'POST', '/load', { folder: ARCHIVE }));
    await b.send('Page.reload');
    await waitFor(b, "document.readyState==='complete' && !!document.querySelector('.state-version-chip')", 60000);
    ok(await openVersions(b, false), 'Versions opens on the archive');
    const arch = await b.ev("document.getElementById('state-version-panel').innerHTML");
    ok(!/sv-restore|sv-stage/.test(arch || ''), 'an archive offers no Stage and no Restore');
    await b.shot(path.join(SHOTS, '10_archive_versions.png'));
    const said = await setup(b, 'POST', '/state-history/' + entry + '/restore-live', {});
    ok(/^4\d\d .*read-only/.test(said), 'a direct Restore-live press on the archive is refused: ' + said.slice(0, 120));
    console.log('setup: back to the live chip ->', await setup(b, 'POST', '/load', { folder: CHIP }));
  }

  // Back / Forward / reload
  await b.send('Page.navigate', { url: URL0 + '/state-history' });
  await waitFor(b, "document.readyState==='complete' && !!document.querySelector('.sh-timeline')", 60000);
  ok(await openVersions(b), 'Versions over State History');
  for (const n of [1, 2, 3]) await clickSel(b, rowSel(n) + ' .sv-check');
  await clickSel(b, '#sv-compare');
  ok(await waitFor(b, "location.pathname==='/diff/versions' && !!document.querySelector('.vc-table')", 30000), 'Compare (3) from the panel');
  await b.ev('history.back()');
  ok(await waitFor(b, "location.pathname==='/state-history' && !!document.querySelector('.sh-timeline')", 30000), 'Back: State History is whole');
  await b.ev('history.forward()');
  ok(await waitFor(b, "location.pathname==='/diff/versions' && !!document.querySelector('.vc-table')", 30000), 'Forward: the table is back');
  await b.send('Page.reload');
  ok(await waitFor(b, "document.readyState==='complete' && !!document.querySelector('.vc-table')", 60000), 'reload of the table URL: whole page with chrome');
  await b.shot(path.join(SHOTS, '11_after_back_forward_reload.png'));
}
