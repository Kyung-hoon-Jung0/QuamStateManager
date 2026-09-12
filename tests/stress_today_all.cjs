/* An honest user pass over everything shipped on 2026-09-12 (docs/176-186).
 *
 * Not a re-run of each round's own driver — those already passed and would
 * mostly re-assert the same DOM. This walks the app the way a person does, on
 * ONE server and ONE chip copy, in an order a person would actually take, and
 * asks of each change: is it there, does it work, and did it break the thing
 * next to it.
 *
 * Every failure is reported. Nothing is skipped silently: a check that cannot
 * run says so and counts as NOT PASSED, because a green line for a check that
 * never executed is the thing this whole session kept finding.
 *
 * argv[2]=out.json argv[3]=cdp argv[4]=base
 */
const fs = require('fs');
const OUT = process.argv[2], CDP = process.argv[3], BASE = process.argv[4];
const results = [], errors = [], notes = [];
function ok(area, n, c, d) {
  results.push({ area: area, name: n, pass: !!c,
                 detail: d === undefined ? null : d });
}

async function main() {
  const t = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = t.find(x => x.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  let id = 0; const pend = new Map();
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
    if (m.method === 'Page.javascriptDialogOpening') {
      notes.push('dialog: ' + String(m.params.type) + ' ' + String(m.params.message || '').slice(0, 90));
      ws.send(JSON.stringify({ id: ++id, method: 'Page.handleJavaScriptDialog',
                               params: { accept: m.params.type === 'beforeunload' } }));
      return;
    }
    if (m.method === 'Runtime.exceptionThrown') {
      const s = String((m.params.exceptionDetails.exception || {}).description
        || m.params.exceptionDetails.text);
      if (!/unsafe-eval/.test(s)) errors.push(s.slice(0, 180));
    }
  };
  const send = (mm, p = {}) => new Promise(r => {
    const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method: mm, params: p }));
  });
  const ev = async x => {
    const rr = await send('Runtime.evaluate',
      { expression: x, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) {
      return { __err: String((rr.result.exceptionDetails.exception || {}).description || '').slice(0, 200) };
    }
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const go = async (path, wait) => {
    await send('Page.navigate', { url: BASE + path });
    await sleep(wait || 5000);
  };
  await send('Runtime.enable'); await send('Page.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true }).catch(() => {});
  await send('Emulation.setDeviceMetricsOverride',
    { width: 1600, height: 1100, deviceScaleFactor: 1, mobile: false });

  // ═══ docs/183 — the inspector's inline edit actually sends ═══════════════
  await go('/qubit/q1');
  const insp = await ev(`(function () {
    var forms = document.querySelectorAll('form.inline-edit');
    var marked = document.querySelectorAll('form.inline-edit[data-inject~="chip-freq"]');
    var jsvals = document.querySelectorAll('form.inline-edit[hx-vals]');
    return { forms: forms.length, marked: marked.length, jsvals: jsvals.length };
  })()`);
  ok('183', 'every inspector form declares what to inject',
     insp.forms > 0 && insp.marked === insp.forms, insp);
  ok('183', '…and none carries the CSP-dead hx-vals', insp.jsvals === 0, insp);

  // type into it for real and watch for a request
  let posted = [];
  await send('Network.enable');
  const netOn = (m) => {};
  void netOn;
  const typeInto = async (dotPath, text) => {
    const found = await ev(`(function () {
      var h = document.querySelector('input[name="dot_path"][value=${JSON.stringify(dotPath)}]');
      if (!h) return false;
      var f = h.closest('form.inline-edit');
      var i = f && f.querySelector('input.edit-input');
      if (!i) return false;
      i.scrollIntoView({ block: 'center' }); i.focus(); i.select();
      return true;
    })()`);
    if (!found) return false;
    for (const ch of text) {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', key: ch });
      await send('Input.dispatchKeyEvent', { type: 'char', text: ch, key: ch });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
      await sleep(18);
    }
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter',
      text: '\r', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter',
      windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    await sleep(1800);
    return true;
  };
  const trayCount = () => ev(`(async function () {
    var r = await fetch('/state/tray', { headers: { 'HX-Request': 'true' } });
    var h = await r.text();
    var m = /data-change-count="(\\d+)"/.exec(h);
    return m ? Number(m[1]) : null;
  })()`);

  const t0 = await trayCount();
  const typed = await typeInto('qubits.q1.anharmonicity', '2.11e8');
  const t1 = await trayCount();
  ok('183', 'Enter in the inspector stages the edit',
     typed && t1 !== null && t1 > t0, { typed, t0, t1 });

  // ═══ docs/185 — the chip is not reformatted ══════════════════════════════
  // (the file check is done by the shell around this driver; here we only make
  //  sure the apply path still works from the UI)
  const applied = await ev(`(async function () {
    var r = await fetch('/state/apply-to-live', { method: 'POST',
      headers: { 'HX-Request': 'true' } });
    return r.status;
  })()`);
  ok('185', 'apply-to-live still succeeds from the page', applied === 200, applied);

  // ═══ docs/186 — the applied log is collapsed; Revert exists ══════════════
  await ev(`try { sessionStorage.removeItem('quam_applied_log_open'); } catch (e) {} 1`);
  await ev(`(async function () { await fetch('/auto-apply/arm', { method: 'POST',
    headers: { 'HX-Request': 'true' } }); return 1; })()`);
  await go('/qubits');
  const log = await ev(`(function () {
    var l = document.getElementById('applied-log');
    if (!l) return { present: false };
    var list = l.querySelector('.applied-log-list');
    return { present: true, collapsed: l.classList.contains('applied-log-collapsed'),
             listVisible: !!(list && list.offsetParent !== null) };
  })()`);
  ok('186', 'the applied-to-live log is collapsed on a first visit',
     log.present && log.collapsed && !log.listVisible, log);
  await ev(`(async function () { await fetch('/auto-apply/disarm', { method: 'POST',
    headers: { 'HX-Request': 'true' } }); return 1; })()`);

  await go('/qubit/q1');
  const revert = await ev(`(async function () {
    var h = await (await fetch('/field/history?path=qubits.q1.anharmonicity')).text();
    return { hasTable: h.indexOf('fh-table') >= 0,
             hasRevert: h.indexOf('fh-revert') >= 0,
             hasDelta: h.indexOf('fh-revert-delta') >= 0,
             emptyRevert: /class="fh-revert" data-value=""/.test(h) };
  })()`);
  ok('186', 'the value history offers Revert', revert.hasRevert, revert);
  ok('186', '…with the diff of what reverting would do', revert.hasDelta, revert);
  ok('186', '…and never on a point with no value', !revert.emptyRevert, revert);

  // ═══ docs/181 — readout / resonator / ro find the same columns ═══════════
  await go('/bulk', 9000);
  // Through the REAL search box, which is what the customer does — and a
  // stronger check than reading a model: it measures the columns actually LEFT
  // ON SCREEN.
  const syn = await ev(`(async function () {
    var box = document.getElementById('bulk-search');
    var t = document.getElementById('bulk-table');
    if (!box || !t) return { __no: 'no grid search box' };
    function shown() {
      return Array.prototype.filter.call(
        t.querySelectorAll('th.bulk-col-head'),
        function (th) {
          return !th.classList.contains('bulk-col-hidden')
              && !th.classList.contains('bulk-search-hidden');
        }).map(function (th) { return th.getAttribute('data-col-key'); }).sort();
    }
    async function search(q) {
      box.value = q;
      box.dispatchEvent(new Event('input', { bubbles: true }));
      await new Promise(function (r) { setTimeout(r, 700); });
      return shown();
    }
    var all = await search('');
    var ro = await search('readout');
    var res = await search('resonator');
    var drv = await search('drive');
    await search('');
    return { total: all.length, readout: ro.length, resonator: res.length,
             same: JSON.stringify(ro) === JSON.stringify(res),
             roOnly: ro.filter(function (k) { return res.indexOf(k) < 0; }).slice(0, 5),
             resOnly: res.filter(function (k) { return ro.indexOf(k) < 0; }).slice(0, 5),
             drive: drv.length,
             narrowed: ro.length > 0 && ro.length < all.length };
  })()`);
  if (syn && syn.__no) {
    ok('181', 'readout and resonator find the same columns', false,
       'could not read the column model: ' + syn.__no);
  } else {
    ok('181', 'readout and resonator find the same columns',
       syn.readout > 0 && syn.same, syn);
    ok('181', '…and the search still narrows (it did not just match everything)',
       syn.narrowed, syn);
    ok('181', 'drive finds the xy columns', syn.drive > 0, syn);
  }

  // ═══ docs/177 — an unapplied edit never vanishes ═════════════════════════
  const dirty = await ev(`(function () {
    var t = document.getElementById('bulk-table');
    if (!t) return { __no: 'no grid' };
    var cells = t.querySelectorAll('.bulk-cell');
    if (!cells.length) return { __no: 'no cells' };
    // find a cell in a column we can hide
    var cell = null, key = null;
    for (var i = 0; i < cells.length; i++) {
      var td = cells[i].closest('td[data-col-key]');
      var k = td && td.getAttribute('data-col-key');
      if (k && k !== '__id__') { cell = cells[i]; key = k; break; }
    }
    if (!cell) return { __no: 'no keyed cell' };
    cell.value = String(Number(String(cell.value).replace(/,/g, '') || 0) + 1);
    cell.dispatchEvent(new Event('input', { bubbles: true }));
    // now hide that column through the picker
    var cb = document.querySelector('#bulk-colvis-menu input[data-col-toggle="' + key + '"]');
    if (!cb) return { __no: 'no picker entry for ' + key };
    cb.checked = false;
    cb.dispatchEvent(new Event('change', { bubbles: true }));
    var th = t.querySelector('th.bulk-col-head[data-col-key="' + key + '"]');
    return { key: key,
             hidden: th.classList.contains('bulk-col-hidden')
                  || th.classList.contains('bulk-search-hidden'),
             stillDirty: cell.value !== cell.getAttribute('data-orig') };
  })()`);
  if (dirty && dirty.__no) {
    ok('177', 'a column holding an unapplied edit is not hidden', false, dirty.__no);
  } else {
    ok('177', 'a column holding an unapplied edit is not hidden',
       dirty.stillDirty && !dirty.hidden, dirty);
  }
  await ev(`window.BulkEdit && window.BulkEdit.resetDirty && window.BulkEdit.resetDirty(); 1`);

  // ═══ docs/182 — the Tags button, and tags/notes in the typeahead ═════════
  await go('/datasets', 8000);
  const tags = await ev(`(function () {
    var tb = document.getElementById('tag-filter-toggle');
    var ex = document.getElementById('exp-filter-toggle');
    var so = document.querySelector('#sort-filter-grid .exp-filter-toggle');
    function top(e) { return e ? Math.round(e.getBoundingClientRect().top) : null; }
    return { has: !!tb, label: tb ? tb.textContent.replace(/\\s+/g, ' ').trim() : null,
             order: [top(ex), top(tb), top(so)] };
  })()`);
  ok('182', 'the Datasets page has a Tags button between Experiments and Sort',
     tags.has && /Tags/.test(tags.label || '')
     && tags.order[0] < tags.order[1] && tags.order[1] < tags.order[2], tags);

  const tv = await ev(`(async function () {
    var r = await fetch('/workspace/tag-vocab');
    if (r.status !== 200) return { status: r.status };
    var j = await r.json();
    return { status: 200, tags: (j.tags || []).length, notes: (j.notes || []).length,
             hasV: !!j.v };
  })()`);
  ok('182', 'the tag/note vocabulary route answers', tv.status === 200 && tv.hasV, tv);

  // ═══ docs/184 — no negative fidelity on Chip Status ══════════════════════
  await go('/topology', 9000);
  const rb = await ev(`(function () {
    var cards = document.querySelectorAll('#topo-overview-tiles .topo-card');
    var neg = [];
    cards.forEach(function (c) {
      var v = c.querySelector('.topo-card-value');
      var ttl = c.querySelector('.topo-card-title');
      if (v && /^-\\d/.test(v.textContent.trim())) {
        neg.push((ttl ? ttl.textContent.trim() : '?') + ' = ' + v.textContent.trim());
      }
    });
    return { cards: cards.length, negative: neg };
  })()`);
  ok('184', 'no tile shows a negative fidelity',
     rb.cards > 0 && rb.negative.length === 0, rb);

  // ═══ docs/180 — a jump shows the field even under a search ═══════════════
  await go('/explorer', 9000);
  const jump = await ev(`(async function () {
    var box = document.getElementById('explorer-search');
    if (!box) return { __no: 'no explorer search box' };
    box.value = 'anharmonicity';
    box.dispatchEvent(new Event('input', { bubbles: true }));
    await new Promise(function (r) { setTimeout(r, 700); });
    var target = 'qubits.q1.T1';
    var before = window._treePathVisible
      ? window._treePathVisible('explorer-tree-state', target) : null;
    window._jumpToTreePath('explorer-tree-state', target);
    await new Promise(function (r) { setTimeout(r, 900); });
    return { before: before, box: box.value,
             after: window._treePathVisible
               ? window._treePathVisible('explorer-tree-state', target) : null };
  })()`);
  if (jump && jump.__no) {
    ok('180', 'a jump clears a search that would hide the field', false, jump.__no);
  } else {
    ok('180', 'a jump clears a search that would hide the field',
       jump.after === true && jump.box === '', jump);
  }

  // ═══ docs/179 — every apply door declares what it showed ═════════════════
  const doors = await ev(`(async function () {
    // Stage something first: with nothing pending the tray renders no apply
    // button at all, and "the button declares its screen" cannot be asked of a
    // button that is not there.
    await fetch('/field/edit', { method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'dot_path=qubits.q1.T1&value=1.234e-5&expect_chip='
            + encodeURIComponent(window.__chipToken || '') });
    // …and SAVE it. The markup apply button lives in the working_dirty branch;
    // with change-log entries pending the tray offers the JS one-click path
    // instead (doStateSync), which declares its signature in app.js rather than
    // in the markup. Both are checked — this is the markup one.
    await fetch('/save', { method: 'POST', headers: { 'HX-Request': 'true' } });
    var tray = await (await fetch('/state/tray', { headers: { 'HX-Request': 'true' } })).text();
    var sig = /data-change-sig="([^"]*)"/.exec(tray);
    return { traySig: sig && sig[1],
             trayBtnDeclares: /hx-post="\\/state\\/apply-to-live"[\\s\\S]{0,400}?seen_sig/.test(tray)
                           || /seen_sig[\\s\\S]{0,400}?hx-post="\\/state\\/apply-to-live"/.test(tray),
             hasApplyBtn: tray.indexOf('/state/apply-to-live') >= 0 };
  })()`);
  ok('179', 'the tray publishes a change signature',
     !!doors.traySig && doors.traySig.length >= 8, doors);
  ok('179', 'the tray offers an apply button once something is pending',
     doors.hasApplyBtn, doors);
  ok('179', 'and that button declares the screen it was pressed from',
     doors.hasApplyBtn && doors.trayBtnDeclares, doors);

  // The OTHER door: the ⚡ one-click path is JS, and declares the signature
  // from app.js rather than from markup.
  const jsDoor = await ev(`(async function () {
    var js = await (await fetch('/static/app.js')).text();
    var i = js.indexOf('body: "mode=" + encodeURIComponent(mode)');
    return { found: i >= 0, declares: i >= 0 && js.slice(i, i + 500).indexOf('seen_sig') >= 0 };
  })()`);
  ok('179', 'the one-click apply path declares it too',
     jsDoor.found && jsDoor.declares, jsDoor);

  // …and a press made against a STALE screen is refused, which is the whole
  // point of the signature.
  const stale = await ev(`(async function () {
    var r = await fetch('/state/apply-to-live', { method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'seen_changes=1&seen_sig=notarealsignature' });
    var body = null;
    try { body = await r.json(); } catch (e) { body = null; }
    return { status: r.status, why: body && body.status };
  })()`);
  ok('179', 'a press declaring a stale screen is refused',
     stale.status === 409 && stale.why === 'unseen_changes', stale);

  // ═══ docs/178 — a stage-only door says so while push is armed ═══════
  // The covenant is intact either way (the arming press licensed the session);
  // what docs/178 fixed is that the door claimed the live chip was untouched
  // while it was being written. So the check is what the door SAYS, in both
  // states, and it disarms in a finally -- an armed session left behind would
  // push every later staging in this run to the chip.
  const note = await ev(`(async function () {
    function armed(pull, push) {
      var b = 'pull=' + (pull ? '1' : '0') + '&push=' + (push ? '1' : '0');
      return fetch('/auto-sync/set', { method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded',
                   'HX-Request': 'true' }, body: b });
    }
    function door() {
      return fetch('/state-history', { headers: { 'HX-Request': 'true' } })
        .then(function (r) { return r.text(); });
    }
    var SAYS = 'Auto-Sync (push) is ARMED';
    var out = { armStatus: null, before: null, during: null, after: null,
                pullOnly: null, doorFound: null };
    try {
      var d0 = await door();
      out.doorFound = d0.indexOf('Load as working state') >= 0;
      out.before = d0.indexOf(SAYS) >= 0;
      var r = await armed(true, true);
      out.armStatus = r.status;
      out.during = (await door()).indexOf(SAYS) >= 0;
      // a PULL-only session must stay silent: warning about a write that
      // cannot happen teaches people to ignore the warning.
      await armed(true, false);
      out.pullOnly = (await door()).indexOf(SAYS) >= 0;
    } finally {
      await armed(false, false);
      out.after = (await door()).indexOf(SAYS) >= 0;
    }
    return out;
  })()`);
  if (!note.doorFound) {
    ok('178', 'the stage-only door renders at all', false, note);
  } else if (note.armStatus !== 200) {
    ok('178', 'Auto-Sync push could be armed for the check', false, note);
  } else {
    ok('178', 'a stage-only door warns while push is armed', note.during, note);
    ok('178', '\u2026and says nothing when it is not', !note.before && !note.after, note);
    ok('178', '\u2026and nothing on a pull-only session either', !note.pullOnly, note);
  }

  // ═══ docs/176 — the Generate review reports the root ═════════════════════
  const gen = await ev(`(async function () {
    var r = await fetch('/static/generate.js');
    var s = await r.text();
    return { stringBlocker: s.indexOf('typeof b === "string"') >= 0,
             rootPicker: s.indexOf('gen-quam-class') >= 0,
             verdict: s.indexOf('Nothing blocks the build') >= 0 };
  })()`);
  ok('176', 'the wizard ships the root picker and the string-blocker fix',
     gen.stringBlocker && gen.rootPicker && gen.verdict, gen);

  fs.writeFileSync(OUT, JSON.stringify({ results, errors, notes }, null, 1));
  const bad = results.filter(r => !r.pass);
  console.log('TODAY pass: ' + (results.length - bad.length) + '/' + results.length
              + '   console errors: ' + errors.length);
  bad.forEach(b => console.log('  FAIL [' + b.area + '] ' + b.name + '  '
                               + JSON.stringify(b.detail).slice(0, 260)));
  notes.forEach(n => console.log('  NOTE ' + n));
  errors.slice(0, 5).forEach(e => console.log('  ERR ' + e));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
