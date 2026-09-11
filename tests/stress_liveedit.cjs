/* Live State Edit (/bulk) — a hostile-user stress round in real headless Chrome.
 *
 * The grid is the app's widest surface: on the real 20Q customer chip it ships
 * 224 qubit columns + 111 pair columns over 20 + 30 rows, of which 198 qubit
 * columns are SERVER-COLD (empty <td> + one value map, hydrated from
 * GET /bulk/cells on demand). Almost every mechanism here — whole-chip search,
 * sort, Column History, path-addressed undo repaint — has to reach cells that
 * are not in the DOM.
 *
 * So this driver is not a happy path. It types a value that exists ONLY in a
 * cold column, clicks a cold header three times in a row, scrolls 55,000 px
 * right and back, feeds a numeric cell an empty string / 1e999 / a quote /
 * Hangul / an emoji / 300 characters, hides a column that is dirty, and
 * presses Ctrl+Z more times than there are things to undo.
 *
 * EVERY Runtime.exceptionThrown and console.error for the whole session is
 * collected. XHRs are counted per gesture (Network.requestWillBeSent): a
 * gesture that fires hundreds is a finding, and this grid has produced exactly
 * that before (a sort re-entrancy loop, 401 requests in 4 s).
 *
 * DELIBERATELY NOT DRIVEN: Apply to live / ⚡ (it writes the chip files).
 * Staging into the Review tray is as far as this goes.
 *
 * argv[2] = json out path, argv[3] = CDP port, argv[4] = base url
 */
const fs = require('fs');
const OUT = process.argv[2] || 'liveedit.json';
const CDP = process.argv[3] || '9411';
const BASE = process.argv[4] || 'http://127.0.0.1:5411';
const SHOTDIR = OUT.replace(/[^\\/]*$/, '');

const errors = [];
const results = [];
const notes = {};
const shots = [];
function ok(name, cond, detail) {
  results.push({ name: name, pass: !!cond, detail: detail === undefined ? null : detail });
  return !!cond;
}

let reqs = [];          // every request of the session: {t, url, method}
function mark() { return reqs.length; }
function since(m) { return reqs.slice(m); }
// The app has background heartbeats (the scheduler poll, the drift poll, the
// run long-poll). They belong to no gesture, so a per-gesture count excludes
// them -- measured: without this, "three clicks fired one request" was one
// GET /scheduler/status that would have happened anyway.
const HEARTBEAT = /\/scheduler\/status|\/state\/drift|\/datasets\/wait|\/datasets\/changes-since|\/api\/progress/;
function countSince(m, re) {
  const s = since(m).filter(r => !HEARTBEAT.test(r.url));
  return re ? s.filter(r => re.test(r.url)).length : s.length;
}

async function main() {
  const targets = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(res => ws.onopen = res);
  let id = 0; const pending = new Map();
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      errors.push({ kind: 'exception', at: Date.now(),
                    text: (d.exception && (d.exception.description || d.exception.value)) || d.text });
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      errors.push({ kind: 'console.error', at: Date.now(),
                    text: (m.params.args || []).map(a => a.value || a.description || '').join(' ') });
    }
    if (m.method === 'Network.requestWillBeSent') {
      reqs.push({ t: Date.now(), url: m.params.request.url, method: m.params.request.method });
    }
  };
  const send = (method, params = {}) => new Promise(res => {
    const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params }));
  });
  const ev = async (expr) => {
    const rr = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) {
      const ex = rr.result.exceptionDetails.exception;
      throw new Error((ex && (ex.description || ex.value)) || 'eval failed: ' + expr.slice(0, 120));
    }
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(res => setTimeout(res, ms));
  const until = async (expr, ms) => {
    const t = Date.now();
    while (Date.now() - t < ms) { const v = await ev(expr); if (v) return v; await sleep(150); }
    return await ev(expr);
  };
  // A REAL keystroke: keyDown with `text` only (a `char` event double-types).
  const typeChar = async (ch) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  };
  const KEYS = { Enter: 13, Tab: 9, Escape: 27, ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37,
                 ArrowRight: 39, Backspace: 8, Delete: 46, z: 90, Z: 90, a: 65, End: 35, Home: 36 };
  const press = async (key, mods) => {
    const p = { type: 'rawKeyDown', key: key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key] };
    if (mods) p.modifiers = mods;             // 2 = Ctrl, 8 = Shift, 10 = Ctrl+Shift
    if (key === 'Enter' || key === 'Tab') { p.text = key === 'Enter' ? '\r' : '\t'; p.type = 'keyDown'; }
    await send('Input.dispatchKeyEvent', p);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: key, windowsVirtualKeyCode: KEYS[key],
                                           modifiers: mods || 0 });
  };
  // A real mouse click at the element's centre, scrolled into view first.
  const clickAt = async (sel, nth) => {
    const box = await ev(`(function(){
      var els=document.querySelectorAll(${JSON.stringify(sel)});
      var e=els[${nth || 0}]; if(!e) return null;
      e.scrollIntoView({block:'center', inline:'center'});
      var r=e.getBoundingClientRect();
      if (r.width<=0||r.height<=0) return {off:1};
      return {x:r.left+r.width/2, y:r.top+r.height/2};})()`);
    if (!box || box.off) return false;
    if (box.x < 0 || box.y < 0 || box.x > 1500 || box.y > 1000) return false;
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    return true;
  };
  const shot = async (tag) => {
    const s = await send('Page.captureScreenshot', { format: 'png' });
    const p = SHOTDIR + 'le_' + tag + '.png';
    fs.writeFileSync(p, Buffer.from(s.result.data, 'base64'));
    shots.push(p);
    return p;
  };
  const typeInto = async (sel, text, opts) => {
    await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return 0;
      e.focus(); e.value=''; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
    for (const ch of text) { await typeChar(ch); if (!(opts && opts.fast)) await sleep(10); }
    await sleep(opts && opts.settle != null ? opts.settle : 500);
  };
  const gridState = () => ev(`(function(){
    var t=document.getElementById('bulk-table'), pt=document.getElementById('bulk-pair-table');
    var vs = window.BulkEdit && BulkEdit._virtState ? BulkEdit._virtState() : null;
    return {
      heads: t? t.querySelectorAll('thead .bulk-col-head').length:0,
      rows: t? t.querySelectorAll('tbody tr').length:0,
      visRows: t? Array.prototype.filter.call(t.querySelectorAll('tbody tr'),
                function(r){return !r.classList.contains('bulk-row-hidden') && !r.classList.contains('bulk-qubit-off');}).length:0,
      cells: t? t.querySelectorAll('.bulk-cell').length:0,
      coldTd: document.querySelectorAll('#bulk-table td.bulk-td-cold').length,
      dirty: t? t.querySelectorAll('.bulk-cell-modified').length:0,
      cold: vs? vs.cold.length:-1, remote: vs? vs.remote.length:-1,
      inflight: vs? vs.inflight.length:-1, failed: vs? vs.failed:-1,
      pairHeads: pt? pt.querySelectorAll('thead .bulk-col-head').length:0,
      pairRows: pt? pt.querySelectorAll('tbody tr').length:0,
      count: (document.getElementById('bulk-search-count')||{}).textContent||'',
      dirtyCount: (document.getElementById('bulk-dirty-count')||{}).textContent||'',
      tray: (function(){var e=document.querySelector('.tray-indicator'); return e? e.textContent.trim():'';})(),
      sl: (function(){var p=document.getElementById('table-pane'); return p? p.scrollLeft:-1;})()
    };})()`);
  const trayN = async () => {
    const t = await ev(`(function(){var e=document.querySelector('.tray-indicator');
      if(!e) return null; var m=/(\\d+)\\s+unsaved/.exec(e.textContent); return m? +m[1] : 0;})()`);
    return t;
  };

  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  /* ═══ PHASE 0 · load ═══════════════════════════════════════════════════ */
  const m0 = mark();
  await send('Page.navigate', { url: BASE + '/bulk' });
  await ev(`1`).catch(() => {});
  const mounted = await until(`!!(document.getElementById('bulk-table')
      && window.BulkEdit && BulkEdit._virtState && BulkEdit._virtState())`, 30000);
  await sleep(2500);
  await ev(`window.confirm=function(){return true}; window.prompt=function(){return ''};
            window.alert=function(){}; 1`);
  let g = await gridState();
  notes.load_requests = countSince(m0);
  ok('the grid mounts on the 20Q chip', mounted && g.heads > 200 && g.rows === 20, g);
  ok('…and the pair grid mounts under it', g.pairHeads > 100 && g.pairRows === 30,
     { pairHeads: g.pairHeads, pairRows: g.pairRows });
  ok('…with server-cold columns to hydrate', g.cold > 100 && g.coldTd > 1000,
     { cold: g.cold, remote: g.remote, coldTd: g.coldTd });
  ok('…and nothing failed or stuck in flight at rest', g.inflight === 0 && g.failed === 0,
     { inflight: g.inflight, failed: g.failed });
  notes.baseline = g;
  await shot('00_loaded');

  // A value that exists ONLY in a cold column — read it OUT of the cold map, so
  // the test cannot accidentally pick something the DOM already holds.
  const coldProbe = await ev(`(function(){
    var s=document.getElementById('bulk-cold-map'); if(!s) return null;
    var d=JSON.parse(s.textContent);
    var keys=Object.keys(d.cols);
    for (var i=0;i<keys.length;i++){
      var col=d.cols[keys[i]];
      for (var j=0;j<col.length;j++){
        var v=String(col[j][0]);
        if (/^0\\.99[0-9]{6,}$/.test(v)) {
          // and prove it is in NO rendered cell
          var hot=Array.prototype.some.call(document.querySelectorAll('#bulk-table .bulk-cell'),
            function(c){return c.value===v;});
          if (!hot) return {key:keys[i], row:d.rows[j], value:v, frag:v.slice(0,12)};
        }
      }
    }
    return null;})()`);
  ok('a value exists that lives ONLY in a cold column (nothing in the DOM holds it)',
     !!coldProbe, coldProbe);
  notes.coldProbe = coldProbe;

  /* ═══ PHASE 1 · #bulk-search ═══════════════════════════════════════════ */
  let m = mark();
  await typeInto('#bulk-search', 'ampl', { settle: 700 });
  g = await gridState();
  const amplReq = countSince(m);
  ok('typing `ampl` filters the grid and says how much', /of 20/.test(g.count), g.count);
  ok('…and four keystrokes do not fire a storm of requests', amplReq <= 6,
     { requests: amplReq, urls: since(m).map(r => r.url.replace(BASE, '')).slice(0, 8) });
  notes.search_ampl_requests = amplReq;

  // the headline case: a term that lives ONLY in a cold column
  if (coldProbe) {
    m = mark();
    await typeInto('#bulk-search', coldProbe.frag, { settle: 1200 });
    g = await gridState();
    const which = await ev(`(function(){var t=document.getElementById('bulk-table');
      return Array.prototype.filter.call(t.querySelectorAll('tbody tr'), function(r){
        return !r.classList.contains('bulk-row-hidden');}).map(function(r){return r.getAttribute('data-qubit');});})()`);
    ok('a value that exists ONLY in a cold column is still found by the search',
       which.length >= 1 && which.indexOf(coldProbe.row) >= 0,
       { typed: coldProbe.frag, expectRow: coldProbe.row, visible: which, count: g.count });
    // docs/141 4d: a search hydrates the columns now in the viewport, so the
    // matching cold column is fetched -- ONE request, never one per column.
    ok('…and finding it costs at most one hydration request, not one per column',
       countSince(m, /\/bulk\/cells/) <= 1,
       { bulkCells: countSince(m, /\/bulk\/cells/),
         urls: since(m).map(r => r.url.replace(BASE, '')).slice(0, 6) });
    notes.coldsearch_rows = which;
  }

  // hostile input into the search box
  const hostile = [
    ['empty-after-text', ''],
    ['whitespace', '   '],
    ['zero', '0'],
    ['minus one', '-1'],
    ['1e999', '1e999'],
    ['NaN', 'NaN'],
    ['a quote', '"'],
    ['a backslash', '\\'],
    ['Hangul', '한글'],
    ['emoji', '😀'],
    ['a pipe alone', '|'],
    ['a lone colon', ':'],
    ['300 chars', 'x'.repeat(300)],
  ];
  let hostileErrs = errors.length;
  for (const [label, text] of hostile) {
    m = mark();
    await typeInto('#bulk-search', text, { fast: text.length > 20, settle: 420 });
    const st = await gridState();
    ok('search survives ' + label,
       typeof st.count === 'string' && st.rows === 20 && st.heads > 200,
       { typed: text.length > 40 ? text.slice(0, 20) + '…(' + text.length + ')' : text,
         count: st.count, visRows: st.visRows, requests: countSince(m) });
  }
  ok('…and no hostile search term threw', errors.length === hostileErrs,
     errors.slice(hostileErrs).map(e => e.text.slice(0, 160)));

  // Enter on an empty box, and Enter on a term
  await typeInto('#bulk-search', '', { settle: 200 });
  m = mark();
  await ev(`document.getElementById('bulk-search').focus(); 1`);
  await press('Enter'); await press('Enter'); await press('Enter');
  await sleep(600);
  g = await gridState();
  ok('Enter on an empty search box does nothing destructive',
     g.rows === 20 && g.heads > 200 && (await ev(`location.pathname`)) === '/bulk',
     { path: await ev(`location.pathname`), requests: countSince(m) });

  // 40 input events back to back with no settle
  m = mark();
  await ev(`(function(){var e=document.getElementById('bulk-search'); e.focus();
    for(var i=0;i<40;i++){ e.value='amplitude'.slice(0,(i%9)+1); e.dispatchEvent(new Event('input',{bubbles:true})); }
    return 1;})()`);
  await sleep(1200);
  g = await gridState();
  ok('40 input events back to back leave one consistent filter',
     /of 20/.test(g.count) && g.rows === 20, { count: g.count, requests: countSince(m) });

  await typeInto('#bulk-search', '', { settle: 700 });
  g = await gridState();
  ok('clearing the search restores every row and clears the counter',
     g.visRows === 20 && g.count === '', g);
  await shot('01_search_cleared');

  /* ═══ PHASE 2 · sorting a COLD column, repeatedly ══════════════════════ */
  const coldHead = await ev(`(function(){
    var vs=BulkEdit._virtState(); if(!vs) return null;
    var t=document.getElementById('bulk-table');
    var hs=t.querySelectorAll('thead th.bulk-col-head');
    for (var i=0;i<hs.length;i++){
      var k=hs[i].getAttribute('data-col-key');
      if (vs.remote.indexOf(k)>=0 && !hs[i].classList.contains('bulk-col-hidden')
          && k.indexOf('dyn__')!==0) return {key:k, idx:i};
    }
    for (var i=0;i<hs.length;i++){
      var k=hs[i].getAttribute('data-col-key');
      if (vs.remote.indexOf(k)>=0) return {key:k, idx:i, hidden:true};
    }
    return null;})()`);
  ok('a server-cold column header is on the page to sort', !!coldHead, coldHead);
  notes.coldHead = coldHead;

  if (coldHead) {
    // THREE clicks in four seconds, the docs/141 re-entrancy shape.
    m = mark();
    const t0 = Date.now();
    for (let i = 0; i < 3; i++) {
      await ev(`(function(){var t=document.getElementById('bulk-table');
        var th=t.querySelector('thead th[data-col-key="'+CSS.escape(${JSON.stringify(coldHead.key)})+'"]');
        if(th) th.click(); return 1;})()`);
      await sleep(400);
    }
    await sleep(4000);
    const burst = countSince(m);
    const cellsReq = countSince(m, /\/bulk\/cells/);
    notes.coldsort_requests = { total: burst, bulkCells: cellsReq, ms: Date.now() - t0 };
    ok('three clicks on a COLD header do not loop the hydration endpoint',
       cellsReq <= 6 && burst <= 20,
       { total: burst, bulkCells: cellsReq, urls: since(m).map(r => r.url.replace(BASE, '')).slice(0, 12) });
    const sorted = await ev(`(function(){
      var t=document.getElementById('bulk-table');
      var k=${JSON.stringify(coldHead.key)};
      var cs=Array.prototype.map.call(t.querySelectorAll('tbody tr'), function(r){
        var c=r.querySelector('[data-col-key="'+CSS.escape(k)+'"] .bulk-cell');
        return c? c.value : null;});
      var caret='';
      var th=t.querySelector('thead th[data-col-key="'+CSS.escape(k)+'"] .bulk-sort-caret');
      if(th) caret=th.textContent.trim();
      var nums=cs.filter(function(v){return v!=null && isFinite(parseFloat(v));}).map(parseFloat);
      var asc=true, desc=true;
      for(var i=1;i<nums.length;i++){ if(nums[i]<nums[i-1]) asc=false; if(nums[i]>nums[i-1]) desc=false; }
      return {hydrated: cs.filter(function(v){return v!=null;}).length, caret:caret, asc:asc, desc:desc, n:nums.length, head:nums.slice(0,3)};})()`);
    ok('…the column hydrates and the rows really end up sorted',
       sorted.hydrated > 0 && sorted.n > 1 && (sorted.asc || sorted.desc) && sorted.caret !== '',
       sorted);
    // now that it is HOT, three more clicks must cost nothing
    m = mark();
    for (let i = 0; i < 3; i++) {
      await ev(`(function(){var t=document.getElementById('bulk-table');
        var th=t.querySelector('thead th[data-col-key="'+CSS.escape(${JSON.stringify(coldHead.key)})+'"]');
        if(th) th.click(); return 1;})()`);
      await sleep(250);
    }
    await sleep(1200);
    ok('…and re-sorting the now-hot column costs zero requests', countSince(m) === 0,
       { requests: countSince(m), urls: since(m).map(r => r.url.replace(BASE, '')) });
  }

  // the id corner, three times
  m = mark();
  const idOrder = [];
  for (let i = 0; i < 3; i++) {
    await ev(`(function(){var c=document.querySelector('#bulk-table .bulk-corner'); if(c) c.click(); return 1;})()`);
    await sleep(400);
    idOrder.push(await ev(`Array.prototype.map.call(document.querySelectorAll('#bulk-table tbody tr'),
      function(r){return r.getAttribute('data-qubit');}).slice(0,3).join(',')`));
  }
  ok('clicking the id corner three times alternates the order, never scrambles it',
     idOrder[0] !== idOrder[1] && idOrder[0] === idOrder[2], idOrder);
  ok('…and sorting by id fires no request at all', countSince(m) === 0, countSince(m));

  /* ═══ PHASE 3 · scroll to the far right and back ═══════════════════════ */
  const geom = await ev(`(function(){var p=document.getElementById('table-pane');
    return p? {sw:p.scrollWidth, cw:p.clientWidth}:null;})()`);
  notes.pane = geom;
  m = mark();
  const tScroll = Date.now();
  await ev(`(function(){var p=document.getElementById('table-pane'); p.scrollLeft=p.scrollWidth; return p.scrollLeft;})()`);
  await sleep(600);
  await until(`(function(){var v=BulkEdit._virtState(); return v && v.inflight.length===0;})()`, 30000);
  await sleep(1500);
  const afterRight = await gridState();
  notes.scroll_right = { ms: Date.now() - tScroll, requests: countSince(m),
                         bulkCells: countSince(m, /\/bulk\/cells/), state: afterRight };
  ok('scrolling to the far right hydrates without failing',
     afterRight.failed === 0 && afterRight.inflight === 0,
     { failed: afterRight.failed, inflight: afterRight.inflight, cold: afterRight.cold,
       requests: countSince(m), bulkCells: countSince(m, /\/bulk\/cells/) });
  ok('…and the far-right hydration is a handful of batched requests, not one per column',
     countSince(m, /\/bulk\/cells/) <= 25,
     { bulkCells: countSince(m, /\/bulk\/cells/), total: countSince(m) });
  await shot('02_scrolled_right');

  m = mark();
  await ev(`(function(){var p=document.getElementById('table-pane'); p.scrollLeft=0; return 1;})()`);
  await sleep(1800);
  g = await gridState();
  ok('scrolling back left leaves the grid intact', g.rows === 20 && g.heads > 200 && g.failed === 0, g);

  // search again AFTER hydration — the value map and the live DOM must agree
  if (coldProbe) {
    await typeInto('#bulk-search', coldProbe.frag, { settle: 1200 });
    const which2 = await ev(`Array.prototype.filter.call(
      document.querySelectorAll('#bulk-table tbody tr'),
      function(r){return !r.classList.contains('bulk-row-hidden');}).map(function(r){return r.getAttribute('data-qubit');})`);
    ok('the same cold-only value still resolves to the same row after hydration',
       JSON.stringify(which2) === JSON.stringify(notes.coldsearch_rows || []),
       { before: notes.coldsearch_rows, after: which2 });
    await typeInto('#bulk-search', '', { settle: 600 });
  }

  /* ═══ PHASE 4 · editing a cell ═════════════════════════════════════════ */
  const target = await ev(`(function(){
    var t=document.getElementById('bulk-table');
    var cs=t.querySelectorAll('tbody tr[data-qubit="q1"] .bulk-cell');
    for (var i=0;i<cs.length;i++){
      var c=cs[i];
      if (c.readOnly || c.hasAttribute('data-is-pointer')) continue;
      if (c.closest('td').classList.contains('bulk-col-hidden')) continue;
      if (!isFinite(parseFloat(c.value))) continue;
      return {path:c.getAttribute('data-dot-path'), key:c.closest('td').getAttribute('data-col-key'),
              orig:c.value, idx:i};
    }
    return null;})()`);
  ok('an editable numeric cell is reachable on q1', !!target, target);
  notes.editTarget = target;

  const focusCell = async (path) => ev(`(function(){
    var c=document.querySelector('#bulk-table .bulk-cell[data-dot-path="'+CSS.escape(${JSON.stringify(path)})+'"]');
    if(!c) return 0; c.scrollIntoView({block:'center',inline:'center'}); c.focus(); c.select(); return 1;})()`);
  const cellState = (path) => ev(`(function(){
    var c=document.querySelector('#bulk-table .bulk-cell[data-dot-path="'+CSS.escape(${JSON.stringify(path)})+'"]');
    if(!c) return null;
    return {value:c.value, orig:c.getAttribute('data-orig'),
            dirty:c.classList.contains('dirty'), mod:c.classList.contains('bulk-cell-modified'),
            bad:c.classList.contains('bulk-cell-bad'), focused: document.activeElement===c,
            rowErr:(function(){var e=c.closest('tr').querySelector('.bulk-row-error');
              return e && !e.hidden? e.textContent.trim():'';})(),
            applyOn:(function(){var b=c.closest('tr').querySelector('.bulk-row-apply'); return b? !b.disabled:null;})()};})()`);

  // focusCell SELECTS the whole value; a real Backspace then clears it, so
  // every edit below is keystrokes only -- no scripted value assignment.
  const typeCell = async (path, text) => {
    await focusCell(path);
    await press('Backspace');
    for (const ch of text) { await typeChar(ch); await sleep(8); }
    await sleep(250);
  };

  if (target) {
    // type → dirty
    await typeCell(target.path, '1234');
    let cs = await cellState(target.path);
    g = await gridState();
    // Two markers, deliberately (docs/146): `dirty` = typed and not committed;
    // `bulk-cell-modified` = an unapplied CHANGE-LOG entry. Typing raises the
    // first and must NOT raise the second.
    ok('typing into a cell marks it dirty and arms its row Apply',
       cs && cs.dirty && !cs.mod && cs.applyOn === true, { cell: cs, dirtyCount: g.dirtyCount });

    // Escape reverts and keeps focus (docs/120 item 9)
    await press('Escape');
    await sleep(350);
    cs = await cellState(target.path);
    ok('Escape reverts the typed value and keeps the caret in the cell',
       cs && cs.value === target.orig && !cs.dirty && cs.focused, cs);

    // type again, Tab → moves to the next edit cell, value survives
    await typeCell(target.path, '0.777');
    const before = await ev(`document.activeElement.getAttribute('data-dot-path')`);
    await press('Tab');
    await sleep(500);
    const after = await ev(`document.activeElement && document.activeElement.getAttribute
      ? document.activeElement.getAttribute('data-dot-path') : null`);
    ok('Tab hops to the next edit cell in the row', after && after !== before, { before, after });

    // the Tab out of the row is what commits — come back and press Enter instead
    let tray0 = await trayN();
    await typeCell(target.path, '0.4242');
    m = mark();
    await press('Enter');
    await sleep(2500);
    let tray1 = await trayN();
    cs = await cellState(target.path);
    notes.enter_commit = { tray0, tray1, requests: countSince(m),
                           urls: since(m).map(r => r.method + ' ' + r.url.replace(BASE, '')).slice(0, 10) };
    ok('Enter stages the edit into the Review tray',
       tray1 !== null && tray1 > (tray0 || 0), { tray0, tray1, cell: cs });
    ok('…and one Enter is one round trip, not a burst', countSince(m) <= 8,
       notes.enter_commit);

    // the SAME cell edited a second time
    tray0 = await trayN();
    await typeCell(target.path, '0.5151');
    await press('Enter');
    await sleep(2500);
    tray1 = await trayN();
    const trayRows = await ev(`(function(){
      var b=document.querySelector('.tray-review-btn'); if(b) b.click();
      var d=document.getElementById('tray-drawer'); if(!d) return null;
      return Array.prototype.map.call(d.querySelectorAll('.tray-change-item'),function(it){
        return {path:(it.querySelector('.tray-change-path')||{}).textContent,
                vals:(it.querySelector('.tray-change-vals')||{}).textContent.replace(/\\s+/g,' ').trim().slice(0,80)};});})()`);
    // The tray is the change LOG, not a set of differing leaves: a second edit
    // of the same path is a second entry, and Ctrl+Z walks them one at a time.
    // What must hold is that the entries CHAIN -- the second starts where the
    // first ended -- so one history per path is still readable.
    const mine = (trayRows || []).filter(r => r.path === target.path);
    ok('a second edit of the same cell appends a CHAINED entry, never a contradictory one',
       tray1 > tray0 && mine.length >= 2
       && /0\.4242/.test(mine[mine.length - 1].vals) && /0\.5151/.test(mine[mine.length - 1].vals),
       { before: tray0, after: tray1, rowsForThisPath: mine });
    notes.second_edit = { before: tray0, after: tray1, rows: trayRows };

    // edge values, one at a time, each committed with Enter
    const edges = [
      ['an empty value', ''],
      ['whitespace only', '   '],
      ['1e999 (overflows to Infinity)', '1e999'],
      ['NaN', 'NaN'],
      ['a double quote', '"'],
      ['a backslash', '\\'],
      ['Hangul', '한글'],
      ['an emoji', '😀'],
      ['300 characters', '9'.repeat(300)],
      ['minus one', '-1'],
      ['zero', '0'],
    ];
    const edgeOut = [];
    const eErr0 = errors.length;
    for (const [label, text] of edges) {
      await typeCell(target.path, text);
      const mk = mark();
      await press('Enter');
      await sleep(1600);
      const st = await cellState(target.path);
      const tn = await trayN();
      edgeOut.push({ label, typed: text.length > 30 ? text.slice(0, 10) + '…(' + text.length + ')' : text,
                     value: st && (String(st.value).length > 30 ? String(st.value).slice(0, 20) + '…' : st.value),
                     orig: st && st.orig, bad: st && st.bad, rowErr: st && st.rowErr,
                     tray: tn, requests: countSince(mk) });
      ok('the cell survives ' + label + ' without throwing',
         st !== null, edgeOut[edgeOut.length - 1]);
    }
    notes.edge_values = edgeOut;
    ok('…and no edge value threw an exception', errors.length === eErr0,
       errors.slice(eErr0).map(e => e.text.slice(0, 200)));
    // an unparseable value must be REFUSED or reported, never silently stored
    const silent = edgeOut.filter(e => /quote|backslash|Hangul|emoji|NaN/.test(e.label)
                                    && !e.bad && !e.rowErr
                                    && String(e.value) === String(e.orig));
    notes.edge_silent = silent;
    ok('a non-numeric value in a numeric cell is either refused or reported, never silently swallowed',
       edgeOut.filter(e => /quote|backslash|Hangul|emoji/.test(e.label))
              .every(e => e.bad || e.rowErr || String(e.value) !== String(e.orig)),
       edgeOut.filter(e => /quote|backslash|Hangul|emoji/.test(e.label)));
    await shot('03_edge_values');

    // put it back to something sane
    await typeCell(target.path, String(target.orig));
    await press('Enter');
    await sleep(2000);
  }

  /* ═══ PHASE 5 · Ctrl+Z / Ctrl+Shift+Z ══════════════════════════════════ */
  const trayBefore = await trayN();
  notes.tray_before_undo = trayBefore;
  m = mark();
  await ev(`document.body.focus(); 1`);
  await press('z', 2);              // Ctrl+Z
  await sleep(2500);
  const trayU1 = await trayN();
  ok('Ctrl+Z removes one entry from the Review tray',
     trayU1 !== null && trayBefore !== null && trayU1 < trayBefore,
     { before: trayBefore, after: trayU1, requests: countSince(m),
       urls: since(m).map(r => r.method + ' ' + r.url.replace(BASE, '')).slice(0, 8) });
  notes.undo1 = { before: trayBefore, after: trayU1, requests: countSince(m) };

  // three Ctrl+Z in fast succession (the queue, docs/122 ③)
  m = mark();
  await press('z', 2); await sleep(120);
  await press('z', 2); await sleep(120);
  await press('z', 2);
  await sleep(4000);
  const trayU2 = await trayN();
  ok('three fast Ctrl+Z presses are all honoured and none is swallowed',
     trayU2 !== null && trayU2 <= Math.max(0, (trayU1 || 0) - 1),
     { after1: trayU1, after3: trayU2, requests: countSince(m) });
  notes.undo3 = { after1: trayU1, after3: trayU2, requests: countSince(m),
                  urls: since(m).map(r => r.method + ' ' + r.url.replace(BASE, '')).slice(0, 12) };

  // Ctrl+Shift+Z redo
  m = mark();
  await press('Z', 10);             // Ctrl+Shift+Z
  await sleep(2500);
  const trayR = await trayN();
  ok('Ctrl+Shift+Z redoes what Ctrl+Z undid',
     trayR !== null && trayR > (trayU2 || 0),
     { afterUndo: trayU2, afterRedo: trayR, requests: countSince(m),
       urls: since(m).map(r => r.method + ' ' + r.url.replace(BASE, '')).slice(0, 8) });
  notes.redo = { afterUndo: trayU2, afterRedo: trayR };

  // Ctrl+Z past the bottom of the stack
  const eU = errors.length;
  m = mark();
  for (let i = 0; i < 8; i++) { await press('z', 2); await sleep(200); }
  await sleep(4000);
  const trayEmpty = await trayN();
  g = await gridState();
  ok('Ctrl+Z past the end of the stack neither throws nor corrupts the grid',
     errors.length === eU && g.rows === 20 && g.heads > 200,
     { tray: trayEmpty, dirtyCells: g.dirty, requests: countSince(m),
       newErrors: errors.slice(eU).map(e => e.text.slice(0, 150)) });
  notes.undo_past_end = { tray: trayEmpty, requests: countSince(m), dirty: g.dirty };
  const redMarks = await ev(`document.querySelectorAll('#bulk-table .bulk-cell-modified').length`);
  ok('…and with an empty tray no red modified marker is left behind',
     (trayEmpty === 0 || trayEmpty === null) ? redMarks === 0 : true,
     { tray: trayEmpty, redMarkers: redMarks });
  await shot('04_after_undo');

  /* ═══ PHASE 6 · the pickers ════════════════════════════════════════════ */
  // Properties, clicked three times
  const popStates = [];
  for (let i = 0; i < 3; i++) {
    await clickAt('.bulk-toolbar-controls details.bulk-colvis:not(.bulk-help-pop) > summary', 0);
    await sleep(400);
    popStates.push(await ev(`(function(){
      var d=document.getElementById('bulk-colvis-menu');
      return {open: !!(d && d.closest('details').open), items: d? d.querySelectorAll('[data-col-toggle]').length:0,
              dets: document.querySelectorAll('#bulk-colvis-menu details').length};})()`));
  }
  ok('the Properties picker toggles cleanly three times and keeps one menu',
     popStates[0].open !== popStates[1].open && popStates[0].open === popStates[2].open
     && popStates[0].items > 10, popStates);
  notes.properties_menu = popStates[0];

  // toggle a curated column off and back on
  await ev(`(function(){var d=document.getElementById('bulk-colvis-menu').closest('details'); d.open=true; return 1;})()`);
  await sleep(300);
  const colTog = await ev(`(function(){
    var cb=document.querySelector('#bulk-colvis-menu [data-col-toggle]');
    var k=cb.getAttribute('data-col-toggle');
    var vis=function(){return !document.querySelector('#bulk-table thead th[data-col-key="'+CSS.escape(k)+'"]').classList.contains('bulk-col-hidden');};
    var was=vis(); cb.checked=!cb.checked; cb.dispatchEvent(new Event('change',{bubbles:true}));
    var now=vis(); cb.checked=!cb.checked; cb.dispatchEvent(new Event('change',{bubbles:true}));
    return {key:k, was:was, afterToggle:now, restored:vis()};})()`);
  ok('a Properties checkbox really hides and restores its column',
     colTog.was !== colTog.afterToggle && colTog.restored === colTog.was, colTog);

  // Show all
  m = mark();
  await ev(`BulkEdit.showAllColumns(); 1`);
  await sleep(1500);
  const showAll = await ev(`(function(){var t=document.getElementById('bulk-table');
    return {hiddenHeads: t.querySelectorAll('thead th.bulk-col-head.bulk-col-hidden').length,
            heads: t.querySelectorAll('thead th.bulk-col-head').length,
            vs: BulkEdit._virtState()};})()`);
  ok('Show all really unhides every column', showAll.hiddenHeads === 0,
     { hidden: showAll.hiddenHeads, heads: showAll.heads, requests: countSince(m) });
  notes.show_all = { hiddenHeads: showAll.hiddenHeads, heads: showAll.heads,
                     cold: showAll.vs && showAll.vs.cold.length, requests: countSince(m) };
  await shot('05_show_all');

  // hide a column that has an UNSAVED edit in it
  if (target) {
    await typeCell(target.path, '0.3131');
    const dirtyKey = target.key;
    const hid = await ev(`(function(){
      var k=${JSON.stringify(dirtyKey)};
      var cb=document.querySelector('#bulk-colvis-menu [data-col-toggle="'+CSS.escape(k)+'"]');
      var before={dirtyCells: document.querySelectorAll('#bulk-table .bulk-cell-modified').length,
                  dirtyLabel:(document.getElementById('bulk-dirty-count')||{}).textContent||'',
                  applyEnabled: !document.getElementById('bulk-apply-all').disabled};
      if(!cb) return {noCheckbox:true, key:k, before:before};
      cb.checked=false; cb.dispatchEvent(new Event('change',{bubbles:true}));
      var th=document.querySelector('#bulk-table thead th[data-col-key="'+CSS.escape(k)+'"]');
      return {key:k, before:before, hidden: th? th.classList.contains('bulk-col-hidden'):null,
              after:{dirtyCells: document.querySelectorAll('#bulk-table .bulk-cell-modified').length,
                     dirtyLabel:(document.getElementById('bulk-dirty-count')||{}).textContent||'',
                     applyEnabled: !document.getElementById('bulk-apply-all').disabled}};})()`);
    notes.hide_dirty_column = hid;
    ok('hiding a column that holds an unsaved edit still reports that edit in the dirty counter',
       !hid.noCheckbox && hid.hidden ? /\d/.test(hid.after.dirtyLabel) && hid.after.applyEnabled : true,
       hid);
    // restore
    await ev(`(function(){var cb=document.querySelector('#bulk-colvis-menu [data-col-toggle="'+CSS.escape(${JSON.stringify(dirtyKey)})+'"]');
      if(cb){cb.checked=true; cb.dispatchEvent(new Event('change',{bubbles:true}));} return 1;})()`);
    await sleep(400);
    await ev(`BulkEdit.resetDirty(); 1`);
    await sleep(600);
  }

  // ⚏ Qubits picker: hide a clean row, then try to hide a DIRTY one
  await ev(`(function(){var d=document.getElementById('bulk-colvis-menu').closest('details'); d.open=false;
    var q=document.getElementById('bulk-qubitvis-menu'); if(q) q.closest('details').open=true; return 1;})()`);
  await sleep(400);
  const qpick = await ev(`(function(){
    var menu=document.getElementById('bulk-qubitvis-menu');
    if(!menu) return {none:true};
    var boxes=menu.querySelectorAll('[data-qcb]');
    return {items:boxes.length, first: boxes.length? boxes[0].getAttribute('data-qcb'):null,
            attrs: boxes.length? null : menu.innerHTML.slice(0,300)};})()`);
  notes.qubit_picker = qpick;
  ok('the ⚏ Qubits picker lists the chip\'s rows', qpick.items > 0 || !qpick.none, qpick);
  // NOTE for anyone extending this file: every change on these menus calls
  // _buildQubitMenu()/_buildPairMenu(), which REPLACES the menu's innerHTML.
  // A checkbox reference taken before the change is detached afterwards and a
  // second dispatch on it reaches nothing -- measured here as a phantom
  // "unhiding does not work". Re-query between steps.
  const setQ = async (qid, checked) => ev(`(function(){
    var cb=document.querySelector('#bulk-qubitvis-menu input[data-qcb="'+CSS.escape(${JSON.stringify(qid)})+'"]');
    if(!cb) return {noBox:true};
    var was={disabled:cb.disabled, checked:cb.checked};
    cb.checked=${checked}; cb.dispatchEvent(new Event('change',{bubbles:true}));
    var r=document.querySelector('#bulk-table tbody tr[data-qubit="'+${JSON.stringify(qid)}+'"]');
    return {before:was, off: r? r.classList.contains('bulk-qubit-off'):null};})()`);
  if (qpick.items > 0) {
    const qh = await setQ('q7', false); await sleep(400);
    const qu = await setQ('q7', true); await sleep(400);
    ok('hiding a qubit row hides it and unhiding brings it back',
       qh.off === true && qu.off === false, { hide: qh, unhide: qu });
  }
  // dirty row cannot be hidden
  if (target) {
    await typeCell(target.path, '0.9191');
    await press('Tab');              // stay inside the row: no commit
    await sleep(700);
    const dirtyRowHide = await setQ('q1', false);
    const badge = await ev(`(function(){var cb=document.querySelector('#bulk-qubitvis-menu input[data-qcb="q1"]');
      var lab=cb?cb.closest('label'):null;
      return {disabled:cb?cb.disabled:null, text:lab?(lab.textContent||'').replace(/\\s+/g,' ').trim():null};})()`);
    notes.dirty_row_hide = { result: dirtyRowHide, box: badge };
    ok('a row with an unsaved edit cannot be hidden away',
       dirtyRowHide.off === false && badge.disabled === true && /unsaved/.test(badge.text || ''),
       notes.dirty_row_hide);

    // …and the SAME state on the column axis. The row picker enforces
    // "Apply all = apply what you see"; measure whether the column one does.
    const colKey = target.key;
    const colHide = await ev(`(function(){
      var cb=document.querySelector('#bulk-colvis-menu [data-col-toggle="'+CSS.escape(${JSON.stringify(colKey)})+'"]');
      if(!cb) return {noBox:true};
      var was=cb.disabled;
      cb.checked=false; cb.dispatchEvent(new Event('change',{bubbles:true}));
      var c=document.querySelector('#bulk-table .bulk-cell[data-dot-path="'+CSS.escape(${JSON.stringify(target.path)})+'"]');
      var th=document.querySelector('#bulk-table thead th[data-col-key="'+CSS.escape(${JSON.stringify(colKey)})+'"]');
      var r=c?c.getBoundingClientRect():null;
      return {checkboxWasDisabled:was, headHidden: th?th.classList.contains('bulk-col-hidden'):null,
              cellWidth: r?Math.round(r.width):null,
              stillDirty: c? c.value!==c.getAttribute('data-orig'):null,
              applyAll:!document.getElementById('bulk-apply-all').disabled};})()`);
    notes.hide_dirty_column = colHide;
    ok('a COLUMN holding an unsaved edit cannot be hidden either (mirror of the row rule)',
       colHide.noBox ? true
         : !(colHide.headHidden && colHide.cellWidth === 0 && colHide.stillDirty && colHide.applyAll),
       colHide);
    await ev(`(function(){var cb=document.querySelector('#bulk-colvis-menu [data-col-toggle="'+CSS.escape(${JSON.stringify(colKey)})+'"]');
      if(cb){cb.checked=true;cb.dispatchEvent(new Event('change',{bubbles:true}));} return 1;})()`);
    await sleep(400);
    await setQ('q1', true);
    await ev(`BulkEdit.resetDirty(); 1`);
    await sleep(600);
  }

  // ⚷ Pairs picker
  const ppick = await ev(`(function(){
    var m=document.getElementById('bulk-pairvis-menu');
    if(!m) return {none:true};
    m.closest('details').open=true;
    var b=m.querySelectorAll('[data-pcb]');
    return {items:b.length, first: b.length? b[0].getAttribute('data-pcb'):null};})()`);
  notes.pair_picker = ppick;
  ok('the ⚷ Pairs picker exists and lists pairs', !ppick.none && ppick.items > 0, ppick);
  if (ppick.items > 0) {
    const pid = ppick.first;
    const setP = async checked => ev(`(function(){
      var cb=document.querySelector('#bulk-pairvis-menu input[data-pcb="'+CSS.escape(${JSON.stringify(pid)})+'"]');
      if(!cb) return {noBox:true};
      cb.checked=${checked}; cb.dispatchEvent(new Event('change',{bubbles:true}));
      var r=document.querySelector('#bulk-pair-table tbody tr[data-pair="'+CSS.escape(${JSON.stringify(pid)})+'"]');
      return {off: r? (r.classList.contains('bulk-qubit-off')||r.classList.contains('bulk-row-hidden')):null};})()`);
    const ph = await setP(false); await sleep(400);
    const pu = await setP(true); await sleep(400);
    ok('hiding a pair hides its row in the pair grid and unhiding brings it back',
       ph.off === true && pu.off === false, { hide: ph, unhide: pu, pair: pid });
  }
  await ev(`document.querySelectorAll('.bulk-colvis').forEach(function(d){d.open=false;}); 1`);

  /* ═══ PHASE 7 · 🕘 Column History on a HOT and a COLD column ═══════════ */
  const histHot = await ev(`(function(){
    var t=document.getElementById('bulk-table');
    var vs=BulkEdit._virtState();
    var hs=t.querySelectorAll('thead th.bulk-col-head');
    for (var i=0;i<hs.length;i++){
      var k=hs[i].getAttribute('data-col-key');
      if (vs.cold.indexOf(k)<0 && hs[i].querySelector('.bulk-col-hist')) return k;
    }
    return null;})()`);
  m = mark();
  await ev(`(function(){var th=document.querySelector('#bulk-table thead th[data-col-key="'+CSS.escape(${JSON.stringify(histHot)})+'"]');
    var b=th && th.querySelector('.bulk-col-hist'); if(b) b.click(); return 1;})()`);
  await sleep(3000);
  const hotHist = await ev(`(function(){
    var o=document.querySelector('.colhist-overlay');
    return o? {shown:o.style.display!=='none', text:(o.querySelector('.ch-card')||{}).textContent.slice(0,200)}:null;})()`);
  ok('the 🕘 column history opens on a HOT column',
     hotHist && hotHist.shown && !/No history-capable/.test(hotHist.text || ''),
     { col: histHot, overlay: hotHist, requests: countSince(m) });
  notes.colhist_hot = { col: histHot, overlay: hotHist, requests: countSince(m) };
  await shot('06_colhist_hot');
  await press('Escape'); await sleep(400);
  await ev(`(function(){var o=document.querySelector('.colhist-overlay'); if(o) o.style.display='none'; return 1;})()`);

  const histCold = await ev(`(function(){
    var t=document.getElementById('bulk-table');
    var vs=BulkEdit._virtState();
    var hs=t.querySelectorAll('thead th.bulk-col-head');
    for (var i=0;i<hs.length;i++){
      var k=hs[i].getAttribute('data-col-key');
      if (vs.remote.indexOf(k)>=0 && hs[i].querySelector('.bulk-col-hist')) return k;
    }
    return null;})()`);
  if (histCold) {
    m = mark();
    let toasted = false;
    await ev(`(function(){window.__toasts=[]; var f=window.showToast;
      window.showToast=function(t){window.__toasts.push(String(t)); if(f) return f.apply(this,arguments);}; return 1;})()`);
    await ev(`(function(){var th=document.querySelector('#bulk-table thead th[data-col-key="'+CSS.escape(${JSON.stringify(histCold)})+'"]');
      var b=th && th.querySelector('.bulk-col-hist'); if(b) b.click(); return 1;})()`);
    await sleep(5000);
    const coldHist = await ev(`(function(){
      var o=document.querySelector('.colhist-overlay');
      return {overlay: o? {shown:o.style.display!=='none', text:(o.querySelector('.ch-card')||{}).textContent.slice(0,200)}:null,
              toasts: window.__toasts||[]};})()`);
    ok('the 🕘 column history hydrates a COLD column instead of claiming it has nothing',
       coldHist.overlay && coldHist.overlay.shown
       && !/No history-capable/.test(coldHist.overlay.text || '')
       && !(coldHist.toasts || []).some(t => /No history-capable/.test(t)),
       { col: histCold, result: coldHist, requests: countSince(m) });
    notes.colhist_cold = { col: histCold, result: coldHist, requests: countSince(m) };
    await shot('07_colhist_cold');
    await ev(`(function(){var o=document.querySelector('.colhist-overlay'); if(o) o.style.display='none'; return 1;})()`);
  }

  /* ═══ PHASE 8 · the table-size slider ══════════════════════════════════ */
  await clickAt('.sidebar-tool.settings-btn', 0);
  await sleep(700);
  if (!(await ev(`(function(){var d=document.getElementById('settings-dropdown');
      return !!(d && !d.classList.contains('settings-hidden'));})()`))) {
    await ev(`window.toggleSettings && window.toggleSettings(); 1`);
    await sleep(700);
  }
  const sliderThere = await ev(`!!document.getElementById('bulk-font-slider')`);
  ok('the Live Edit table-size slider is reachable from Settings', sliderThere);
  const eS = errors.length;
  m = mark();
  const sizes = [];
  for (const v of ['1.7', '0.75', '1.25', '1']) {
    await ev(`(function(){var s=document.getElementById('bulk-font-slider');
      s.value=${JSON.stringify(v)}; s.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
    await sleep(700);
    sizes.push(await ev(`(function(){
      var fs=document.documentElement.style.getPropertyValue('--bulk-fs');
      var t=document.getElementById('bulk-table');
      return {set:${JSON.stringify(v)}, fs:fs.trim(), heads:t.querySelectorAll('thead .bulk-col-head').length,
              cells:t.querySelectorAll('.bulk-cell').length,
              paneW:(document.getElementById('table-pane')||{}).scrollWidth};})()`));
  }
  notes.slider = { steps: sizes, requests: countSince(m) };
  ok('the table-size slider changes the cell scale and the grid keeps every column',
     sizes.every(s => s.heads > 200) && sizes[0].fs !== sizes[1].fs, sizes);
  ok('…and dragging it through its whole range throws nothing',
     errors.length === eS, errors.slice(eS).map(e => e.text.slice(0, 160)));
  await shot('08_slider');
  await ev(`(function(){var d=document.getElementById('settings-dropdown');
    if(d) d.classList.add('settings-hidden'); return 1;})()`);

  /* ═══ PHASE 9 · the pair grid ══════════════════════════════════════════ */
  const pv = await ev(`window.BulkPairEdit && BulkPairEdit._pairVirtState ? BulkPairEdit._pairVirtState() : null`);
  notes.pair_virt = pv && { cold: pv.cold.length, remote: pv.remote.length, failed: pv.failed };
  ok('the pair grid virtualizes too', pv && pv.cold.length > 0, notes.pair_virt);
  if (pv && pv.remote.length) {
    m = mark();
    const pk = pv.remote[0];
    for (let i = 0; i < 3; i++) {
      await ev(`(function(){var t=document.getElementById('bulk-pair-table');
        var th=t.querySelector('thead th[data-col-key="'+CSS.escape(${JSON.stringify(pk)})+'"]');
        if(th) th.click(); return 1;})()`);
      await sleep(400);
    }
    await sleep(4000);
    ok('three clicks on a COLD PAIR header do not loop either',
       countSince(m, /\/bulk\/cells/) <= 6,
       { col: pk, bulkCells: countSince(m, /\/bulk\/cells/), total: countSince(m) });
    notes.pair_coldsort = { col: pk, bulkCells: countSince(m, /\/bulk\/cells/), total: countSince(m) };
  }
  // one pair cell edited then escaped
  const pcell = await ev(`(function(){
    var cs=document.querySelectorAll('#bulk-pair-table tbody .bulk-cell');
    for (var i=0;i<cs.length;i++){ var c=cs[i];
      if (!c.readOnly && isFinite(parseFloat(c.value)) && !c.closest('td').classList.contains('bulk-col-hidden'))
        return c.getAttribute('data-dot-path');}
    return null;})()`);
  if (pcell) {
    await ev(`(function(){var c=document.querySelector('#bulk-pair-table .bulk-cell[data-dot-path="'+CSS.escape(${JSON.stringify(pcell)})+'"]');
      c.scrollIntoView({block:'center',inline:'center'}); c.focus(); c.select(); return 1;})()`);
    await press('Backspace');
    for (const ch of '0.123') { await typeChar(ch); await sleep(8); }
    await sleep(400);
    const pdirty = await ev(`(function(){var c=document.querySelector('#bulk-pair-table .bulk-cell[data-dot-path="'+CSS.escape(${JSON.stringify(pcell)})+'"]');
      return {dirty:c.classList.contains('dirty'), v:c.value};})()`);
    await press('Escape'); await sleep(400);
    const pclean = await ev(`(function(){var c=document.querySelector('#bulk-pair-table .bulk-cell[data-dot-path="'+CSS.escape(${JSON.stringify(pcell)})+'"]');
      return {dirty:c.classList.contains('dirty'), v:c.value, orig:c.getAttribute('data-orig')};})()`);
    ok('a pair-grid cell marks dirty on typing and Escape reverts it',
       pdirty.dirty === true && pclean.dirty === false && pclean.v === pclean.orig,
       { path: pcell, typed: pdirty, afterEscape: pclean });
  }

  /* ═══ PHASE 10 · resize, two pickers at once, scroll to the end ════════ */
  const eR = errors.length;
  m = mark();
  for (const [w, h] of [[900, 700], [1500, 1000], [640, 900], [1500, 1000]]) {
    await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false });
    await sleep(1200);
  }
  await sleep(1500);
  g = await gridState();
  ok('resizing the window four times leaves the grid whole',
     g.rows === 20 && g.heads > 200 && g.failed === 0 && errors.length === eR,
     { state: g, requests: countSince(m), newErrors: errors.slice(eR).map(e => e.text.slice(0, 150)) });
  notes.resize = { requests: countSince(m), bulkCells: countSince(m, /\/bulk\/cells/), state: g };

  // two pickers open at once
  const both = await ev(`(function(){
    var a=document.getElementById('bulk-colvis-menu'), b=document.getElementById('bulk-qubitvis-menu');
    if(a) a.closest('details').open=true; if(b) b.closest('details').open=true;
    return {colvis: !!(a&&a.closest('details').open), qubitvis: !!(b&&b.closest('details').open),
            overlap: (function(){ if(!a||!b) return null;
              var ra=a.getBoundingClientRect(), rb=b.getBoundingClientRect();
              return !(ra.right<rb.left||rb.right<ra.left||ra.bottom<rb.top||rb.bottom<ra.top);})()};})()`);
  ok('two column pickers can be open at once without one covering the other',
     both.colvis && both.qubitvis && both.overlap === false, both);
  notes.two_pickers = both;
  await ev(`document.querySelectorAll('.bulk-colvis').forEach(function(d){d.open=false;}); 1`);

  // far scroll both ways, twice, fast
  m = mark();
  const eF = errors.length;
  for (let i = 0; i < 2; i++) {
    await ev(`(function(){var p=document.getElementById('table-pane'); p.scrollLeft=p.scrollWidth; return 1;})()`);
    await sleep(900);
    await ev(`(function(){var p=document.getElementById('table-pane'); p.scrollLeft=0; return 1;})()`);
    await sleep(900);
  }
  await until(`(function(){var v=BulkEdit._virtState(); return v && v.inflight.length===0;})()`, 20000);
  await sleep(1500);
  g = await gridState();
  ok('scrubbing the scroller end to end twice keeps the grid consistent',
     g.rows === 20 && g.heads > 200 && g.failed === 0 && g.inflight === 0 && errors.length === eF,
     { state: g, requests: countSince(m), bulkCells: countSince(m, /\/bulk\/cells/),
       newErrors: errors.slice(eF).map(e => e.text.slice(0, 150)) });
  notes.scrub = { requests: countSince(m), bulkCells: countSince(m, /\/bulk\/cells/) };

  // and a search after ALL of that
  await typeInto('#bulk-search', 'amplitude', { settle: 1200 });
  g = await gridState();
  ok('a search after every gesture above still reports a sane count',
     /of 20/.test(g.count), g.count);
  await typeInto('#bulk-search', '', { settle: 800 });
  await shot('09_final');

  /* ─── wrap up ───────────────────────────────────────────────────────── */
  const known = errors.filter(e => /unsafe-eval/.test(e.text) && /htmx/.test(e.text));
  const other = errors.filter(e => !(/unsafe-eval/.test(e.text) && /htmx/.test(e.text)));
  notes.total_requests = reqs.length;
  notes.bulk_cells_requests = reqs.filter(r => /\/bulk\/cells/.test(r.url)).length;
  notes.final = await gridState();

  fs.writeFileSync(OUT, JSON.stringify({
    results, errors, notes, shots,
    knownCspErrors: known.length, otherErrors: other,
    requestSummary: (function () {
      const by = {};
      reqs.forEach(r => { const u = r.url.replace(BASE, '').split('?')[0]; by[u] = (by[u] || 0) + 1; });
      return by;
    })(),
  }, null, 1));
  const bad = results.filter(x => !x.pass);
  console.log('checks: ' + (results.length - bad.length) + '/' + results.length
              + '   errors: ' + errors.length + ' (known htmx CSP: ' + known.length + ')'
              + '   requests: ' + reqs.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 400)));
  other.slice(0, 20).forEach(e => console.log('  ERR  ' + e.kind + ': ' + String(e.text).slice(0, 260)));
  process.exit(0);
}
main().catch(e => {
  console.error('driver error: ' + (e && e.stack || e));
  fs.writeFileSync(OUT, JSON.stringify({ results, errors, notes, shots, driver: String(e && e.stack || e) }, null, 1));
  process.exit(1);
});
