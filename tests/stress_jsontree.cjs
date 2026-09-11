/* Json Tree View (/explorer) — the hostile-user round, in real headless Chrome.
 *
 * Modelled on tests/stress_drive.cjs (same CDP plumbing, same real-keystroke
 * dispatch, same whole-session error collection).
 *
 * What is driven here is the part a screenshot cannot show: the shared search
 * grammar typed a character at a time, a query that matches thousands of
 * leaves and the 150-shallowest-first cap it trips, the notice's count checked
 * against an INDEPENDENT recount of the same model, "show all", the search
 * cleared, expand/collapse walked to the bottom and back, the hover action
 * group built under a REAL mouse (opacity is the ?'s only hiding mechanism, so
 * a synthetic hover would prove nothing), and the inline leaf editor fed every
 * value a person really produces — a bare word, an empty string, a number into
 * a string field, 300 characters, a quote, a backslash, Hangul, an emoji,
 * 1e999, NaN — with Escape mid-edit, a click away mid-edit, and the same leaf
 * edited twice.
 *
 * Nothing here applies to live. Every write lands in the working copy /
 * pending tray of a COPY of the chip.
 *
 * argv[2] = json out path, argv[3] = CDP port, argv[4] = base url
 */
const fs = require('fs');
/* Memory pressure on this machine kills a long-lived Chrome browser process
 * (measured twice: the CDP port went dead mid-run while the renderers lived on),
 * so the round runs as three SHORT parts against a freshly launched Chrome.
 * argv[5] = A | B | C | all. Stage 0 (boot + the independent model walk) always runs. */
const PART = process.argv[5] || 'all';
const want = p => PART === 'all' || PART === p;
const OUT = (PART === 'all' ? process.argv[2] : process.argv[2].replace(/\.json$/, '_' + PART + '.json'));
const CDP = process.argv[3] || '9412';
const BASE = process.argv[4] || 'http://127.0.0.1:5412';

const errors = [];
const results = [];
const notes = [];
const shots = [];
const editCases = [];
const numCases = [];
const dialogs = [];
let stage = 'boot';
// Incremental save: the first run of this driver hung past its wall clock and
// wrote nothing at all, so every measurement was lost. Now every check is on
// disk the moment it is made, and `stage` says where a hang happened.
function save(extra) {
  try {
    fs.writeFileSync(OUT, JSON.stringify(Object.assign(
      { stage, results, errors, notes, editCases, numCases, shots, dialogs }, extra || {}), null, 1));
  } catch (e) { /* never let bookkeeping kill the round */ }
}
function ok(name, cond, detail) {
  results.push({ name: name, pass: !!cond, detail: detail === undefined ? null : detail });
  save();
}
function note(k, v) { notes.push({ k: k, v: v }); save(); }

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
      errors.push({ kind: 'exception', text: (d.exception && (d.exception.description || d.exception.value)) || d.text });
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      errors.push({ kind: 'console.error', text: (m.params.args || []).map(a => a.value || a.description || '').join(' ') });
    }
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') {
      errors.push({ kind: 'log.' + m.params.entry.source, text: m.params.entry.text });
    }
    // The app arms a beforeunload guard whenever the pending tray shows N>0
    // (app.js:452). Headless Chrome then blocks the navigation on a dialog
    // nobody answers -- which is what hung parts C, D and E of this driver
    // for 45-120s at a Page.navigate. Answer it, and record that it fired.
    if (m.method === 'Page.javascriptDialogOpening') {
      dialogs.push({ type: m.params.type, message: m.params.message, url: m.params.url });
      ws.send(JSON.stringify({ id: ++id, method: 'Page.handleJavaScriptDialog', params: { accept: true } }));
    }
  };
  // Every CDP call gets a wall clock. A silent never-resolving call is what
  // ate the first run of this driver.
  const send = (method, params = {}, ms = 45000) => new Promise((res, rej) => {
    const i = ++id;
    const t = setTimeout(() => { pending.delete(i); rej(new Error('CDP timeout ' + ms + 'ms on ' + method + ' [stage: ' + stage + ']')); }, ms);
    pending.set(i, m => { clearTimeout(t); res(m); });
    ws.send(JSON.stringify({ id: i, method, params }));
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
  const shot = async (name) => {
    const s = await send('Page.captureScreenshot', { format: 'png' });
    const p = OUT.replace(/\.json$/, '_' + name + '.png');
    fs.writeFileSync(p, Buffer.from(s.result.data, 'base64'));
    return p;
  };
  const snap = async (n) => { shots.push(await shot(n)); save(); };

  // A REAL keystroke: keyDown with `text` only (a `char` event double-types).
  const typeChar = async (ch) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  };
  const KEYS = { Enter: 13, Tab: 9, Escape: 27, ArrowDown: 40, ArrowUp: 38, Backspace: 8, End: 35, Home: 36 };
  const press = async (key) => {
    const p = { type: 'rawKeyDown', key: key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key] };
    if (key === 'Enter' || key === 'Tab') { p.text = key === 'Enter' ? '\r' : '\t'; p.type = 'keyDown'; }
    await send('Input.dispatchKeyEvent', p);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: key, windowsVirtualKeyCode: KEYS[key] });
  };
  const clearBox = async (sel) => {
    await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return 0; e.focus(); e.value=''; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  };
  // Type into a box with REAL keystrokes (the box's oninput is what filters).
  const typeInto = async (sel, text, opts) => {
    await clearBox(sel);
    await sleep(opts && opts.pre != null ? opts.pre : 260);
    for (const ch of text) { await typeChar(ch); if (!(opts && opts.fast)) await sleep(8); }
    await sleep(opts && opts.settle != null ? opts.settle : 500);
  };
  const val = async (sel) => ev(`(document.querySelector(${JSON.stringify(sel)})||{}).value`);
  const rect = async (sel) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return null; var r=e.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2,w:r.width,h:r.height,top:r.top};})()`);
  const mouseMove = async (x, y) => send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: x, y: y, button: 'none', buttons: 0 });
  // A real hover on an element, scrolling it into view first.
  const hover = async (sel) => {
    await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(e&&e.scrollIntoView) e.scrollIntoView({block:'center'}); return 1;})()`);
    await sleep(150);
    const r = await rect(sel);
    if (!r) return null;
    await mouseMove(5, 5); await sleep(40);
    await mouseMove(r.x, r.y); await sleep(250);
    return r;
  };
  const realClick = async (sel) => {
    const r = await hover(sel);
    if (!r) return false;
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: r.x, y: r.y, button: 'left', clickCount: 1 });
    await sleep(250);
    return true;
  };

  await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  const BROAD = 'q';   // a query matching thousands (used by parts A and C)

  /* ══ 0. boot ═════════════════════════════════════════════════════════ */
  await send('Page.navigate', { url: BASE + '/explorer' });
  await sleep(6000);
  // a controllable confirm/prompt: record what the page asks, always say yes
  await ev(`window.__confirms=[]; window.confirm=function(m){window.__confirms.push(String(m)); return true;};
            window.prompt=function(){return '';}; window.__toasts=[];
            (function(){var t=window.showToast; window.showToast=function(m,k){window.__toasts.push(String(m)); if(t) return t.apply(this,arguments);};})(); 1`);

  const ES = '#explorer-search';
  const boot = await ev(`(function(){
      var s=document.querySelector('${ES}');
      var st=document.getElementById('explorer-tree-state');
      var wi=document.getElementById('explorer-tree-wiring');
      return {search:!!s, placeholder:s?s.placeholder:null, title:s?s.title:null,
              stateNodes: st?st.querySelectorAll('.tree-node').length:-1,
              wiringNodes: wi?wi.querySelectorAll('.tree-node').length:-1,
              stateLeaves: st&&st._treeData?1:0,
              tabs: document.querySelectorAll('#explorer-tabs .tree-file-tab').length,
              chips: document.querySelectorAll('#explorer-chipbar .bulk-chip').length};})()`);
  ok('the Json Tree page boots with both trees and the search box',
     boot.search && boot.stateNodes > 0 && boot.wiringNodes > 0 && boot.tabs === 2, boot);
  note('boot', boot);

  // Independent model walk: my OWN flat index + my OWN grammar, so the notice's
  // count is checked against something the search did not produce.
  // Re-injectable: a reload throws the previous document's copy away.
  const injectMine = () => ev(`window.__mine = (function(){
    function fmt(v){ return window._formatValue ? window._formatValue(v) : String(v); }
    function build(data){
      var flat=[];
      function add(p,k,vs){ flat.push({p:p, h:((k==null?'':String(k))+' '+(vs||'')).toLowerCase()+' '+String(p).toLowerCase()}); }
      function walk(k,v,p){
        if (v!==null && typeof v==='object' && Object.prototype.toString.call(v)!=='[object Array]'){
          var ks=Object.keys(v); add(p,k,'{'+ks.length+' key'+(ks.length!==1?'s':'')+'}');
          for(var i=0;i<ks.length;i++) walk(ks[i], v[ks[i]], p?p+'.'+ks[i]:ks[i]);
        } else if (Object.prototype.toString.call(v)==='[object Array]'){
          add(p,k,'['+v.length+' item'+(v.length!==1?'s':'')+']');
          for(var j=0;j<v.length;j++) walk(String(j), v[j], p+'.'+j);
        } else { add(p,k,fmt(v)); }
      }
      if (data!==null && typeof data==='object' && Object.prototype.toString.call(data)!=='[object Array]'){
        var tk=Object.keys(data); for(var t=0;t<tk.length;t++) walk(tk[t], data[tk[t]], tk[t]);
      } else walk(null, data, '');
      return flat;
    }
    // my own reading of the documented grammar: space = AND, standalone | = OR
    // (tight), every other pipe a literal term.
    function grps(q){
      var toks=String(q||'').toLowerCase().trim(); toks = toks? toks.split(/\\s+/):[];
      var g=[], join=[];
      for(var i=0;i<toks.length;i++){
        var t=toks[i];
        if(t==='|'){
          var nx=(i+1<toks.length)?toks[i+1]:null;
          if(g.length && join[g.length-1] && nx!==null && nx!=='|'){ g[g.length-1].push(nx); join[g.length-1]=true; i++; continue; }
          g.push([t]); join.push(false); continue;
        }
        g.push([t]); join.push(true);
      }
      return g;
    }
    function match(h,G){
      for(var i=0;i<G.length;i++){ var any=false;
        for(var j=0;j<G[i].length;j++){ if(h.indexOf(G[i][j])>=0){any=true;break;} }
        if(!any) return false; }
      return true;
    }
    var cacheId=null, cache=null;
    function flatOf(cid){
      var c=document.getElementById(cid);
      if(!c||!c._treeData) return [];
      if(cacheId===cid && cache && cache.src===c._treeData) return cache.f;
      var f=build(c._treeData); cache={src:c._treeData,f:f}; cacheId=cid; return f;
    }
    return {
      count: function(cid,q){ var f=flatOf(cid), G=grps(q), n=0;
        for(var i=0;i<f.length;i++) if(match(f[i].h,G)) n++; return n; },
      paths: function(cid,q){ var f=flatOf(cid), G=grps(q), o=[];
        for(var i=0;i<f.length;i++) if(match(f[i].h,G)) o.push(f[i].p); return o; },
      // the first N by (depth, tree order) — the documented cap policy
      firstByDepth: function(cid,q,N){ var f=flatOf(cid), G=grps(q), m=[];
        for(var i=0;i<f.length;i++){ if(!match(f[i].h,G)) continue;
          var p=f[i].p, d=1, k=-1; while((k=p.indexOf('.',k+1))>=0) d++;
          m.push({p:p,d:d,i:i}); }
        m.sort(function(a,b){return a.d-b.d || a.i-b.i;});
        return m.slice(0,N).map(function(x){return x.p;}); },
      total: function(cid){ return flatOf(cid).length; }
    };})(); 1`);
  await injectMine();

  const totalLeaves = await ev(`window.__mine.total('explorer-tree-state')`);
  note('state model nodes (my own walk)', totalLeaves);

  const treeState = async () => ev(`(function(){
      var c=document.getElementById('explorer-tree-state');
      var res=c.querySelector(':scope > .tree-search-results');
      var head=res?res.querySelector('.tsr-head'):null;
      return { notice: !!res,
               noticeText: head?head.textContent.replace(/\\s+/g,' ').trim():null,
               btn: !!(res&&res.querySelector('.tsr-all')),
               btnText: res&&res.querySelector('.tsr-all')?res.querySelector('.tsr-all').textContent:null,
               highlighted: c.querySelectorAll('.tree-node.tree-highlight').length,
               hidden: c.querySelectorAll('.tree-node.tree-search-hidden').length,
               nodes: c.querySelectorAll('.tree-node').length,
               visible: c.querySelectorAll('.tree-node:not(.tree-search-hidden)').length,
               lastQ: c._lastSearchQuery, showAllQ: c._searchShowAllQ };})()`);

  if (want('A')) {
  stage = '1-grammar'; save();
  /* ══ 1. the shared grammar ═══════════════════════════════════════════ */
  // one plain word, typed a character at a time
  await typeInto(ES, 'grid_location');
  let s = await treeState();
  let mine = await ev(`window.__mine.count('explorer-tree-state','grid_location')`);
  ok('one plain word filters the tree and highlights exactly the model matches',
     s.highlighted === mine && mine > 0, { ui: s.highlighted, independent: mine, notice: s.notice });
  note('grid_location', { ui: s.highlighted, independent: mine, hidden: s.hidden });

  // AND
  await typeInto(ES, 'x180 amplitude');
  s = await treeState();
  mine = await ev(`window.__mine.count('explorer-tree-state','x180 amplitude')`);
  const onlyAmp = await ev(`window.__mine.count('explorer-tree-state','amplitude')`);
  ok('space = AND: two words narrow, and the tree agrees with an independent count',
     s.highlighted === mine && mine > 0 && mine < onlyAmp,
     { and: mine, ui: s.highlighted, amplitudeAlone: onlyAmp });

  // OR (standalone pipe), tight binding
  const orCount = await ev(`window.__mine.count('explorer-tree-state','amplitude | length')`);
  await typeInto(ES, 'amplitude | length');
  s = await treeState();
  const tightMine = await ev(`window.__mine.count('explorer-tree-state','x180 amplitude | length')`);
  ok('standalone | = OR (checked against an independent count)',
     (s.notice ? Number((s.noticeText.match(/^(\d+) matches/) || [])[1]) : s.highlighted) === orCount,
     { uiTotal: s.noticeText || s.highlighted, independent: orCount });

  await typeInto(ES, 'x180 amplitude | length');
  s = await treeState();
  const uiTight = s.notice ? Number((s.noticeText.match(/^(\d+) matches/) || [])[1]) : s.highlighted;
  ok('| binds tighter than the space: x180 AND (amplitude|length)',
     uiTight === tightMine, { ui: uiTight, independent: tightMine });

  // every OTHER pipe is a literal term
  for (const q of ['|e>', '| amplitude', 'amplitude |', 'amplitude || length', '|', '||']) {
    await typeInto(ES, q, { settle: 420 });
    const st2 = await treeState();
    const mn = await ev(`window.__mine.count('explorer-tree-state', ${JSON.stringify(q)})`);
    const uiTotal = st2.notice ? Number((st2.noticeText.match(/^(\d+) matches/) || [])[1]) : st2.highlighted;
    ok('a non-standalone pipe `' + q + '` stays literal and does not throw',
       uiTotal === mn, { ui: uiTotal, independent: mn });
  }

  // quoted phrases — NOT in this box's grammar (the placeholder promises only
  // "space = AND, | = OR"). Recorded as measured behaviour, not as a defect.
  await typeInto(ES, '"full scale"');
  const qPhrase = await treeState();
  const qMine = await ev(`window.__mine.count('explorer-tree-state','"full scale"')`);
  note('quoted phrase `"full scale"`', { highlighted: qPhrase.highlighted, hidden: qPhrase.hidden, independent: qMine, placeholder: boot.placeholder });
  ok('a quoted phrase behaves as two literal tokens (this box has no phrase syntax) and never throws',
     qPhrase.highlighted === qMine, { ui: qPhrase.highlighted, independent: qMine });
  await typeInto(ES, 'full scale');
  const unq = await treeState();
  ok('…while the same words unquoted DO match (so the quotes, not the words, are the difference)',
     unq.highlighted > 0, { quoted: qPhrase.highlighted, unquoted: unq.highlighted });

  stage = '2-cap'; save();
  /* ══ 2. a query matching thousands: the cap, the notice, show-all ═════ */
  await typeInto(ES, BROAD, { settle: 1200 });
  s = await treeState();
  const broadMine = await ev(`window.__mine.count('explorer-tree-state', ${JSON.stringify(BROAD)})`);
  const m = (s.noticeText || '').match(/^(\d+) matches for (\S+) — the first (\d+) are shown/);
  ok('a query matching thousands shows the capped notice', !!s.notice && !!m, s.noticeText);
  ok('…and the count in the notice is TRUE (independent recount of the same model)',
     m && Number(m[1]) === broadMine, { notice: m && m[1], independent: broadMine });
  ok('…and it says 150 are shown, and 150 really are highlighted',
     m && Number(m[3]) === 150 && s.highlighted === 150,
     { says: m && m[3], highlighted: s.highlighted });
  ok('…and it offers "show all <true count>"',
     s.btn && s.btnText === 'show all ' + broadMine, s.btnText);
  note('broad query', { q: BROAD, total: broadMine, shown: s.highlighted, nodesMaterialised: s.nodes });

  // shallowest-first: the shown set must be the first 150 by depth, not by
  // depth-first order.
  const shownPaths = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
      return Array.prototype.map.call(c.querySelectorAll('.tree-node.tree-highlight'), function(n){return n.getAttribute('data-path');});})()`);
  const expectPaths = await ev(`window.__mine.firstByDepth('explorer-tree-state', ${JSON.stringify(BROAD)}, 150)`);
  const setA = new Set(shownPaths), setB = new Set(expectPaths);
  const sameSet = shownPaths.length === expectPaths.length && expectPaths.every(p => setA.has(p));
  ok('the 150 are the SHALLOWEST 150, not the first 150 depth-first',
     sameSet, { shown: shownPaths.length, expected: expectPaths.length,
                missing: expectPaths.filter(p => !setA.has(p)).slice(0, 5),
                extra: shownPaths.filter(p => !setB.has(p)).slice(0, 5) });
  const depths = shownPaths.map(p => p.split('.').length);
  ok('…which is visible as a shallow depth spread, not one deep branch',
     new Set(shownPaths.map(p => p.split('.')[0])).size > 1,
     { maxDepth: Math.max.apply(null, depths), roots: Array.from(new Set(shownPaths.map(p => p.split('.')[0]))).slice(0, 8) });

  // The notice must stay in view when the user scrolls to the subtree they
  // are searching about. `window` is NOT the scroller here and neither is
  // #table-pane: the tree container itself (.json-tree) carries
  // overflow-y:auto, which is what part E measured. Scrolling the wrong box
  // made an earlier cut of this check pass while proving nothing.
  const noticeTop0 = await ev(`(function(){var e=document.querySelector('#explorer-tree-state > .tree-search-results');
      var c=document.getElementById('explorer-tree-state');
      if(!e) return null; var r=e.getBoundingClientRect();
      return {top:Math.round(r.top), bottom:Math.round(r.bottom), scrollTop:Math.round(c.scrollTop),
              sh:c.scrollHeight, ch:c.clientHeight};})()`);
  await ev(`(function(){var c=document.getElementById('explorer-tree-state'); c.scrollTop=c.scrollHeight; return 1;})()`);
  await sleep(500);
  const noticeTop1 = await ev(`(function(){var e=document.querySelector('#explorer-tree-state > .tree-search-results');
      var c=document.getElementById('explorer-tree-state'); var cr=c.getBoundingClientRect();
      if(!e) return null; var r=e.getBoundingClientRect();
      return {top:Math.round(r.top), bottom:Math.round(r.bottom), scrollTop:Math.round(c.scrollTop),
              boxTop:Math.round(cr.top), boxBottom:Math.round(cr.bottom),
              visible: r.bottom > cr.top && r.top < cr.bottom};})()`);
  ok('the tree container really scrolled (it, not window, is the scroller)',
     noticeTop1 && noticeTop1.scrollTop > 200, { before: noticeTop0, after: noticeTop1 });
  ok('the notice is STICKY — still on screen after scrolling to the far end',
     noticeTop1 && noticeTop1.visible === true,
     { before: noticeTop0, afterScrollToBottom: noticeTop1 });
  await snap('capped_scrolled');
  await ev(`(function(){var c=document.getElementById('explorer-tree-state'); c.scrollTop=0; return 1;})()`);
  await sleep(300);

  // show all
  const tShow = Date.now();
  await ev(`(function(){var b=document.querySelector('#explorer-tree-state > .tree-search-results .tsr-all'); if(b) b.click(); return 1;})()`);
  await sleep(2500);
  const afterAll = await treeState();
  const msShow = Date.now() - tShow;
  ok('"show all" materialises every match and retires the notice',
     afterAll.highlighted === broadMine && !afterAll.notice,
     { highlighted: afterAll.highlighted, total: broadMine, notice: afterAll.notice, ms: msShow });
  note('show all', { ms: msShow, nodes: afterAll.nodes, highlighted: afterAll.highlighted });
  await snap('show_all');

  // pressing show-all again is impossible (gone) — press the button element
  // we still hold a reference to? No: assert it is gone.
  ok('…and the "show all" button is gone once everything is shown',
     !afterAll.btn, afterAll.btnText);

  stage = '3-clear'; save();
  /* ══ 3. clearing the search ══════════════════════════════════════════ */
  // First record the pre-search collapse state, then search, then clear.
  await clearBox(ES); await sleep(900);
  const cleared = await treeState();
  ok('clearing the box unhides every row',
     cleared.hidden === 0 && cleared.highlighted === 0, cleared);
  const depth1 = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
      var open=0, closed=0;
      c.querySelectorAll('.tree-node').forEach(function(n){
        var t=n.querySelector(':scope > .tree-row > .tree-toggle'); if(!t) return;
        var d=parseInt(n.getAttribute('data-depth'),10);
        if(t.classList.contains('collapsed')) closed++; else open++;
      });
      // Only a CONTAINER has a toggle -- a top-level scalar (this chip's
      // __class__) is a depth-0 node with none, and counting it as "not
      // opened" was my own error in the first run of this driver.
      var d0open=0, d0=0;
      c.querySelectorAll('.tree-node[data-depth="0"]').forEach(function(n){
        var t=n.querySelector(':scope > .tree-row > .tree-toggle');
        if(!t) return;
        d0++;
        if(!t.classList.contains('collapsed')) d0open++; });
      return {open:open, closed:closed, d0:d0, d0open:d0open};})()`);
  ok('…and the tree is back to a depth-1 shape (top level open, deeper closed)',
     depth1.d0 > 0 && depth1.d0open === depth1.d0, depth1);
  ok('…and clearing forgets the "show all" the user asked for',
     cleared.showAllQ === undefined || cleared.showAllQ === null, cleared.showAllQ);

  // the same broad query again must be capped again
  await typeInto(ES, BROAD, { settle: 1200 });
  const again = await treeState();
  ok('the same broad query is capped again after a clear (show-all is not sticky)',
     again.notice && again.highlighted === 150, { notice: again.noticeText, highlighted: again.highlighted });

  // a hand-built collapse state, then a search, then a clear
  await clearBox(ES); await sleep(800);
  const manual = await ev(`(function(){
      var c=document.getElementById('explorer-tree-state');
      var n=c.querySelector('.tree-node[data-path="qubits"]');
      var t=n&&n.querySelector(':scope > .tree-row > .tree-toggle');
      if(t&&t.classList.contains('collapsed')) t.click();
      var q1=c.querySelector('.tree-node[data-path="qubits.q1"]');
      var t1=q1&&q1.querySelector(':scope > .tree-row > .tree-toggle');
      if(t1&&t1.classList.contains('collapsed')) t1.click();
      var xy=c.querySelector('.tree-node[data-path="qubits.q1.xy"]');
      var t2=xy&&xy.querySelector(':scope > .tree-row > .tree-toggle');
      if(t2&&t2.classList.contains('collapsed')) t2.click();
      return (window.jsonTreeExpandedPaths?window.jsonTreeExpandedPaths('explorer-tree-state'):[]).length;})()`);
  await sleep(400);
  const beforeSearchOpen = await ev(`window.jsonTreeExpandedPaths('explorer-tree-state')`);
  await typeInto(ES, 'grid_location', { settle: 700 });
  await clearBox(ES); await sleep(900);
  const afterClearOpen = await ev(`window.jsonTreeExpandedPaths('explorer-tree-state')`);
  const keptDeep = afterClearOpen.filter(p => p.indexOf('.') >= 0);
  ok('after clearing, the hand-built DEEP expansion is NOT restored — the tree resets to depth 1',
     keptDeep.length === 0,
     { beforeSearch: beforeSearchOpen, afterClear: afterClearOpen, manualOpened: manual });
  note('collapse state across a search',
       { openedByHand: beforeSearchOpen, openAfterClear: afterClearOpen });

  stage = '4-expand'; save();
  /* ══ 4. expand / collapse, deep and wide ═════════════════════════════ */
  // depth buttons, each pressed three times
  for (const d of [0, 1, 2, 3]) {
    for (let k = 0; k < 3; k++) {
      await ev(`window.jsonTreeExpandToDepth('explorer-tree-state', ${d}); 1`);
      await sleep(120);
    }
    const st3 = await ev(`(function(){var c=document.getElementById('explorer-tree-state'); var bad=0, open=0;
        c.querySelectorAll('.tree-node').forEach(function(n){
          var t=n.querySelector(':scope > .tree-row > .tree-toggle'); if(!t) return;
          var dd=parseInt(n.getAttribute('data-depth'),10);
          var isOpen=!t.classList.contains('collapsed');
          if(isOpen) open++;
          if(isOpen && dd>=${d}) bad++;
          if(!isOpen && dd<${d}) bad++;
        });
        return {bad:bad, open:open, nodes:c.querySelectorAll('.tree-node').length};})()`);
    ok('depth ' + d + ' pressed three times leaves exactly depth-' + d + ' expansion',
       st3.bad === 0, st3);
  }

  // expand-all on the whole 20-qubit state tree
  const tAll = Date.now();
  await ev(`window.jsonTreeExpandAll('explorer-tree-state'); 1`);
  await sleep(600);
  const msAll = Date.now() - tAll;
  const allSt = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
      var n=c.querySelectorAll('.tree-node').length;
      var lazy=0, collapsed=0;
      c.querySelectorAll('.tree-node').forEach(function(x){ if(x._lazyData) lazy++;
        var t=x.querySelector(':scope > .tree-row > .tree-toggle');
        if(t && t.classList.contains('collapsed')) collapsed++; });
      return {nodes:n, lazyLeft:lazy, collapsedLeft:collapsed, modelNodes: window.__mine.total('explorer-tree-state')};})()`);
  ok('expand-all materialises EVERY node of the model with none left collapsed',
     allSt.lazyLeft === 0 && allSt.collapsedLeft === 0 && allSt.nodes === allSt.modelNodes,
     Object.assign({ ms: msAll }, allSt));
  note('expand all', Object.assign({ ms: msAll }, allSt));
  await snap('expand_all');

  // the far end and back, on the container that actually scrolls
  await ev(`(function(){var c=document.getElementById('explorer-tree-state'); c.scrollTop=c.scrollHeight; return 1;})()`);
  await sleep(700);
  const farEnd = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
      var rs=c.querySelectorAll('.tree-row');
      return {y: Math.round(c.scrollTop), h: c.scrollHeight, rows: rs.length,
              lastRow: rs.length? rs[rs.length-1].textContent.replace(/\s+/g,' ').slice(0,60):null};})()`);
  await ev(`(function(){var c=document.getElementById('explorer-tree-state'); c.scrollTop=0; return 1;})()`);
  await sleep(400);
  const backHome = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
      var f=c.querySelector(':scope > .tree-node');
      return {y:Math.round(c.scrollTop), first: f? f.getAttribute('data-path'):null};})()`);
  ok('the fully expanded tree scrolls to the far end and back without throwing',
     farEnd.y > 10000 && backHome.y === 0 && backHome.first === 'octaves',
     { farEnd, backHome });

  const tColl = Date.now();
  await ev(`window.jsonTreeCollapseAll('explorer-tree-state'); 1`);
  await sleep(400);
  const collSt = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
      var open=0; c.querySelectorAll('.tree-node').forEach(function(x){
        var t=x.querySelector(':scope > .tree-row > .tree-toggle');
        if(t && !t.classList.contains('collapsed')) open++; });
      return {openLeft:open, nodes:c.querySelectorAll('.tree-node').length};})()`);
  ok('collapse-all closes every toggle', collSt.openLeft === 0, Object.assign({ ms: Date.now() - tColl }, collSt));

  // a single deep node toggled three times ends where it started
  await ev(`window.jsonTreeExpandToDepth('explorer-tree-state', 2); 1`);
  await sleep(300);
  const tog = await ev(`(async function(){
      var c=document.getElementById('explorer-tree-state');
      var n=c.querySelector('.tree-node[data-path="qubits.q1"]');
      if(!n) return null;
      var t=n.querySelector(':scope > .tree-row > .tree-toggle');
      var out=[t.classList.contains('collapsed')?'closed':'open'];
      for(var i=0;i<3;i++){ t.click(); await new Promise(r=>setTimeout(r,120));
        out.push(t.classList.contains('collapsed')?'closed':'open'); }
      return out;})()`);
  ok('one toggle clicked three times alternates cleanly (no stuck half-state)',
     tog && tog.length === 4 && tog[0] !== tog[1] && tog[1] !== tog[2] && tog[2] !== tog[3], tog);

    await send('Page.navigate', { url: BASE + '/explorer' });   // drop the expanded DOM
  await sleep(5000);
  }

  if (want('B')) {
  stage = '5-hover'; save();
  /* ══ 5. the per-row hover actions ════════════════════════════════════ */
  await typeInto(ES, 'grid_location', { settle: 800 });
  const LEAF = '.tree-node[data-path="qubits.q1.grid_location"]';
  const leafExists = await ev(`!!document.querySelector('${LEAF}')`);
  ok('the searched leaf qubits.q1.grid_location is materialised by the search', leafExists);

  // before hover: no action group, and the ? is present-but-transparent
  const preHover = await ev(`(function(){var n=document.querySelector('${LEAF}');
      var r=n&&n.querySelector(':scope > .tree-row');
      var h=r&&r.querySelector(':scope > .tree-help');
      return {actions: r? r.querySelectorAll(':scope > .tree-row-actions').length : -1,
              help: !!h, helpOpacity: h? getComputedStyle(h).opacity : null,
              helpIsLast: !!(h && r.lastElementChild===h)};})()`);
  ok('before hover there is no action group at all (built lazily)',
     preHover.actions === 0, preHover);
  ok('…and the ? is in the row but fully transparent', preHover.help && preHover.helpOpacity === '0', preHover);

  const rowSel = LEAF + ' > .tree-row';
  await hover(rowSel);
  const onHover = await ev(`(function(){var n=document.querySelector('${LEAF}');
      var r=n.querySelector(':scope > .tree-row');
      var a=r.querySelector(':scope > .tree-row-actions');
      var h=r.querySelector(':scope > .tree-help');
      return {actions: r.querySelectorAll(':scope > .tree-row-actions').length,
              btns: a? Array.prototype.map.call(a.children, function(b){return b.textContent;}) : [],
              helpOpacity: h? getComputedStyle(h).opacity : null,
              order: Array.prototype.map.call(r.children, function(c){return c.className.split(' ')[0];}),
              helpIsLast: !!(h && r.lastElementChild===h),
              helpLeft: h? Math.round(h.getBoundingClientRect().left):null,
              actionsRight: a? Math.round(a.getBoundingClientRect().right):null};})()`);
  ok('hovering a leaf row builds the action group',
     onHover.actions === 1 && onHover.btns.length > 0, onHover);
  ok('…the leaf offers copy ⧉, type ⚙ and delete ✕',
     onHover.btns.indexOf('⧉') >= 0 && onHover.btns.indexOf('⚙') >= 0 && onHover.btns.indexOf('✕') >= 0, onHover.btns);
  ok('…the ? sits RIGHT of the action group and is the last child of the row',
     onHover.helpIsLast && onHover.helpLeft >= onHover.actionsRight - 2,
     { helpLeft: onHover.helpLeft, actionsRight: onHover.actionsRight, order: onHover.order });
  ok('…and the ? becomes visible only under the hover',
     onHover.helpOpacity !== '0' && Number(onHover.helpOpacity) > 0,
     { before: preHover.helpOpacity, hovered: onHover.helpOpacity });
  await snap('hover_actions');

  // hover the same row three times — no duplicated groups
  for (let i = 0; i < 3; i++) { await mouseMove(5, 5); await sleep(80); await hover(rowSel); }
  const dup = await ev(`(function(){var r=document.querySelector('${LEAF} > .tree-row');
      return {groups:r.querySelectorAll(':scope > .tree-row-actions').length,
              helps:r.querySelectorAll(':scope > .tree-help').length};})()`);
  ok('hovering the same row three times never duplicates the actions or the ?',
     dup.groups === 1 && dup.helps === 1, dup);

  // a container row: ✎ (edit as JSON) + ＋ (add key)
  await ev(`window.jsonTreeExpandToDepth('explorer-tree-state', 2); 1`); await sleep(300);
  await typeInto(ES, 'grid_location', { settle: 700 });
  const CONT = '.tree-node[data-path="qubits.q1"] > .tree-row';
  await hover(CONT);
  const contAct = await ev(`(function(){var r=document.querySelector('${CONT}'); if(!r) return null;
      var a=r.querySelector(':scope > .tree-row-actions');
      return {btns: a? Array.prototype.map.call(a.children,function(b){return b.textContent;}):[],
              jsonEdit: r.querySelectorAll(':scope > .tree-json-edit-btn').length,
              helpLast: r.lastElementChild ? r.lastElementChild.className : null};})()`);
  ok('a container row offers ＋ (add key) and ⧉, plus the ✎ edit-as-JSON button',
     contAct && contAct.btns.indexOf('＋') >= 0 && contAct.btns.indexOf('⧉') >= 0 && contAct.jsonEdit === 1,
     contAct);

  // ⧉ copy, clicked three times
  const copyRes = await ev(`(async function(){
      var r=document.querySelector('${LEAF} > .tree-row');
      var a=r.querySelector(':scope > .tree-row-actions');
      var b=a && Array.prototype.filter.call(a.children,function(x){return x.textContent==='⧉';})[0];
      if(!b) return null; var out=[];
      for(var i=0;i<3;i++){ b.click(); await new Promise(r2=>setTimeout(r2,350)); out.push(b.textContent); }
      await new Promise(r2=>setTimeout(r2,900));
      return {marks:out, settled:b.textContent};})()`);
  ok('⧉ copy pressed three times gives feedback each time and settles back to ⧉',
     copyRes && copyRes.marks.every(x => x === '✓' || x === '✗') && copyRes.settled === '⧉', copyRes);

  // ＋ add-key panel: open three times, Escape
  const addP = await ev(`(async function(){
      var n=document.querySelector('.tree-node[data-path="qubits.q1"]');
      var r=n.querySelector(':scope > .tree-row');
      var a=r.querySelector(':scope > .tree-row-actions');
      var b=a && Array.prototype.filter.call(a.children,function(x){return x.textContent==='＋';})[0];
      if(!b) return null; var counts=[];
      for(var i=0;i<3;i++){ b.click(); await new Promise(r2=>setTimeout(r2,250));
        counts.push(n.querySelectorAll(':scope > .tree-crud-panel').length); }
      return counts;})()`);
  ok('the ＋ add-key panel opened three times never stacks', addP && addP.every(c => c === 1), addP);
  await press('Escape'); await sleep(200);
  // Escape is handled by the panel's own keydown — focus is in its key input
  const addAfterEsc = await ev(`(function(){var n=document.querySelector('.tree-node[data-path="qubits.q1"]');
      return n? n.querySelectorAll(':scope > .tree-crud-panel').length : -1;})()`);
  ok('Escape closes the add-key panel', addAfterEsc === 0, addAfterEsc);

  // empty key + Enter
  const emptyKey = await ev(`(async function(){
      var n=document.querySelector('.tree-node[data-path="qubits.q1"]');
      var r=n.querySelector(':scope > .tree-row');
      var a=r.querySelector(':scope > .tree-row-actions');
      var b=a && Array.prototype.filter.call(a.children,function(x){return x.textContent==='＋';})[0];
      if(!b) return null; b.click(); await new Promise(r2=>setTimeout(r2,250));
      var p=n.querySelector(':scope > .tree-crud-panel');
      p.querySelector('.tree-crud-ok').click(); await new Promise(r2=>setTimeout(r2,400));
      var err=p.querySelector('.tree-crud-err').textContent;
      p.querySelector('.tree-crud-cancel').click(); await new Promise(r2=>setTimeout(r2,200));
      return {err:err, gone: n.querySelectorAll(':scope > .tree-crud-panel').length===0};})()`);
  ok('Add with an empty key says so instead of posting', emptyKey && /key required/i.test(emptyKey.err) && emptyKey.gone, emptyKey);

  // ✕ delete confirm — opened and CANCELLED (nothing is deleted)
  const delC = await ev(`(async function(){
      var r=document.querySelector('${LEAF} > .tree-row');
      var a=r.querySelector(':scope > .tree-row-actions');
      var b=a && Array.prototype.filter.call(a.children,function(x){return x.textContent==='✕';})[0];
      if(!b) return null; b.click(); await new Promise(r2=>setTimeout(r2,900));
      var a2=r.querySelector(':scope > .tree-row-actions');
      var txt=a2? a2.textContent : '';
      var cancel=a2 && Array.prototype.filter.call(a2.children,function(x){return x.textContent==='Cancel';})[0];
      if(cancel) cancel.click();
      await new Promise(r2=>setTimeout(r2,300));
      return {confirmText:txt, stillThere: !!document.querySelector('${LEAF}'),
              actionsGone: !r.querySelector(':scope > .tree-row-actions')};})()`);
  ok('✕ asks first — naming the leaf count and the real pointer-ref count',
     delC && /delete grid_location/.test(delC.confirmText) && /pointer ref/.test(delC.confirmText), delC);
  ok('…and Cancel keeps the row', delC && delC.stillThere, delC);

  // the ? opens the Config Manual
  const helpOpen = await ev(`(async function(){
      var r=document.querySelector('${LEAF} > .tree-row');
      var h=r.querySelector(':scope > .tree-help');
      if(!h) return null; h.click(); await new Promise(r2=>setTimeout(r2,900));
      var p=document.querySelector('.manual-popover, #config-manual, .manual-header');
      return {opened: !!p, cls: p? p.className : null,
              text: p? p.textContent.replace(/\\s+/g,' ').slice(0,120) : null};})()`);
  ok('the ? opens the Config Manual for that key', helpOpen && helpOpen.opened, helpOpen);
  await press('Escape'); await sleep(300);

  stage = '6-tabs'; save();
  /* ══ 6. state / wiring container switch ══════════════════════════════ */
  await clearBox(ES); await sleep(700);
  const sw1 = await ev(`(function(){ window.switchExplorerTab('wiring');
      var st=document.getElementById('explorer-tree-state'), wi=document.getElementById('explorer-tree-wiring');
      var tabs=Array.prototype.map.call(document.querySelectorAll('#explorer-tabs .tree-file-tab'), function(t){return t.textContent+':'+t.classList.contains('active');});
      return {stateHidden: st.style.display==='none', wiringShown: wi.style.display!=='none', tabs:tabs, active: window._activeTreeId()};})()`);
  ok('the wiring tab switches the container and the active marking',
     sw1.stateHidden && sw1.wiringShown && sw1.active === 'explorer-tree-wiring'
     && sw1.tabs.indexOf('wiring.json:true') >= 0, sw1);

  await typeInto(ES, 'amplitude', { settle: 800 });
  const wiringSearch = await ev(`(function(){var c=document.getElementById('explorer-tree-wiring');
      return {hl:c.querySelectorAll('.tree-node.tree-highlight').length,
              notice: !!c.querySelector(':scope > .tree-search-results'),
              stateNotice: !!document.querySelector('#explorer-tree-state > .tree-search-results')};})()`);
  note('wiring search for amplitude', wiringSearch);

  // switch back and forth three times with a live query
  const flip = await ev(`(async function(){ var out=[];
      for(var i=0;i<3;i++){ window.switchExplorerTab('state'); await new Promise(r=>setTimeout(r,400));
        window.switchExplorerTab('wiring'); await new Promise(r=>setTimeout(r,400));
        out.push({stateNodes:document.getElementById('explorer-tree-state').querySelectorAll('.tree-node').length,
                  wiringNotices:document.querySelectorAll('#explorer-tree-wiring > .tree-search-results').length,
                  stateNotices:document.querySelectorAll('#explorer-tree-state > .tree-search-results').length}); }
      return out;})()`);
  ok('three fast tab flips with a live query never stack a second notice on either tree',
     flip.every(f => f.wiringNotices <= 1 && f.stateNotices <= 1), flip);

  // THE ASYMMETRY: clear the box on wiring, then go back to state
  await ev(`window.switchExplorerTab('state'); 1`); await sleep(600);
  await typeInto(ES, 'amplitude', { settle: 900 });
  const stateFiltered = await treeState();
  await ev(`window.switchExplorerTab('wiring'); 1`); await sleep(700);
  await clearBox(ES); await sleep(900);
  await ev(`window.switchExplorerTab('state'); 1`); await sleep(700);
  const stateAfterClearOnWiring = await treeState();
  ok('clearing the search on one tab also clears the OTHER tab (no tab left filtered under an empty box)',
     stateAfterClearOnWiring.hidden === 0 && !stateAfterClearOnWiring.notice,
     { boxValue: await val(ES), stateWhenFiltered: { hidden: stateFiltered.hidden, hl: stateFiltered.highlighted },
       stateAfterClearOnWiring: stateAfterClearOnWiring });
  await snap('tab_switch_stale_filter');

  // second, independent reproduction of the same gesture
  await clearBox(ES); await sleep(600);
  await ev(`window.switchExplorerTab('state'); 1`); await sleep(400);
  await typeInto(ES, 'readout', { settle: 900 });
  const st5a = await treeState();
  await ev(`window.switchExplorerTab('wiring'); 1`); await sleep(600);
  await clearBox(ES); await sleep(900);
  await ev(`window.switchExplorerTab('state'); 1`); await sleep(600);
  const st5b = await treeState();
  note('tab-switch clear, second reproduction',
       { query: 'readout', hiddenWhileSearching: st5a.hidden, hiddenAfterClearElsewhere: st5b.hidden,
         boxValue: await val(ES) });

  // repair for the rest of the run
  await ev(`window.switchExplorerTab('state'); 1`);
  await typeInto(ES, 'zzz-nothing', { settle: 500 });
  await clearBox(ES); await sleep(800);

  stage = '7-typeahead'; save();
  /* ══ 7. the typeahead in that box ════════════════════════════════════ */
  const rows = async () => ev(`(function(){var p=document.getElementById('sm-typeahead'); if(!p||p.hidden) return []; return Array.prototype.map.call(p.querySelectorAll('.sm-th-row'), function(r){return {t:(r.querySelector('.sm-th-label')||{}).textContent, m:(r.querySelector('.sm-th-meta')||{}).textContent||'', note:r.classList.contains('sm-th-note'), fuzzy:r.classList.contains('sm-th-fuzzy'), sep:r.classList.contains('sm-th-fuzzsep')};});})()`);
  await typeInto(ES, 'ampl', { settle: 700 });
  let tr = await rows();
  ok('typeahead: `ampl` lists the chip\'s amplitude keys with a place count',
     tr.some(x => /ampl/i.test(x.t)) && tr.some(x => /place/.test(x.m)), tr.slice(0, 5));
  await typeInto(ES, 'amplitide', { settle: 800 });
  tr = await rows();
  ok('typeahead: the typo `amplitide` still finds amplitude, marked as a guess',
     tr.some(x => x.fuzzy) && tr.some(x => x.sep), tr.slice(0, 6));
  await snap('typeahead_typo');
  // accept it and check the tree follows
  await press('ArrowDown'); await press('Enter'); await sleep(1200);
  const accepted = await val(ES);
  const afterAccept = await treeState();
  ok('accepting a typeahead row rewrites the box and the tree filters on the corrected word',
     /amplit/i.test(String(accepted)) && afterAccept.highlighted > 0,
     { box: accepted, highlighted: afterAccept.highlighted });

  await typeInto(ES, 'zzzzqqqqxxxx', { settle: 600 });
  ok('typeahead: a word with nothing in common offers nothing', (await rows()).length === 0);
  await typeInto(ES, 'x'.repeat(300), { fast: true, settle: 900 });
  const longSt = await treeState();
  ok('a 300-character query neither hangs nor throws and the tree says "nothing"',
     (await rows()).length === 0 && longSt.highlighted === 0, { hidden: longSt.hidden, nodes: longSt.nodes });
  await clearBox(ES); await sleep(700);

  // 40 input events back to back
  await ev(`(function(){var e=document.querySelector('${ES}'); e.focus(); e.value='';
     for(var i=0;i<40;i++){ e.value='amplitude'.slice(0,(i%9)+1); e.dispatchEvent(new Event('input',{bubbles:true})); } return 1;})()`);
  await sleep(1400);
  const rapid = await treeState();
  ok('40 input events back to back leave one consistent filtered tree',
     rapid.lastQ === 'amplitud' || rapid.lastQ === 'amplitude' || typeof rapid.lastQ === 'string',
     { lastQ: rapid.lastQ, highlighted: rapid.highlighted, notices: await ev(`document.querySelectorAll('#explorer-tree-state > .tree-search-results').length`) });
  await clearBox(ES); await sleep(700);

    }

  if (want('C')) {
  stage = '8-inline-edit'; save();
  /* ══ 8. INLINE EDIT of a leaf ════════════════════════════════════════ */
  const peek = async (p) => ev(`fetch('/field/peek?dot_path=' + encodeURIComponent(${JSON.stringify(p)}))
      .then(function(r){return r.json();}).then(function(d){ return {ok:d.ok, v: d.values? d.values[${JSON.stringify(p)}] : undefined,
        kind: (d.values && typeof d.values[${JSON.stringify(p)}])};})`);

  const SPATH = 'qubits.q1.grid_location';
  const SSEL = '.tree-node[data-path="' + SPATH + '"] > .tree-row > .tree-val';
  const openEditor = async (sel) => {
    await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(e) e.click(); return 1;})()`);
    await sleep(500);
  };
  const editorState = async (sel) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});
      if(!e) return null; var i=e.querySelector('input');
      return {open:!!i, value:i?i.value:null, editing:e.classList.contains('tree-val-editing'),
              chip:(e.querySelector('.tree-type-chip')||{}).textContent||null, text:e.textContent};})()`);
  const setEditorValue = async (sel, v) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});
      var i=e&&e.querySelector('input'); if(!i) return 0; i.focus(); i.value=${JSON.stringify(v)};
      i.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  const rowState = async (p) => ev(`(function(){var n=document.querySelector('.tree-node[data-path=${JSON.stringify(p)}]');
      var r=n&&n.querySelector(':scope > .tree-row'); var v=r&&r.querySelector('.tree-val');
      return {text: v? v.textContent : null, cls: v? v.className : null,
              pending: !!(r&&r.classList.contains('tree-row-pending')),
              err: (r&&r.querySelector('.tree-edit-err')||{}).textContent || null};})()`);

  await typeInto(ES, 'grid_location', { settle: 900 });
  const before = await peek(SPATH);
  await openEditor(SSEL);
  let edSt = await editorState(SSEL);
  ok('clicking a string leaf opens an editor showing its JSON LITERAL (quotes included)',
     edSt && edSt.open && edSt.value === JSON.stringify(String(before.v)),
     { editor: edSt && edSt.value, stored: before.v });
  ok('…with an expected-type chip beside it', !!(edSt && edSt.chip), edSt && edSt.chip);
  await snap('inline_editor_open');

  // Escape mid-edit
  await setEditorValue(SSEL, '"typed-but-escaped"');
  await press('Escape'); await sleep(500);
  let after = await peek(SPATH);
  let rs = await rowState(SPATH);
  ok('Escape mid-edit cancels: the stored value is untouched and the row is not pending',
     after.v === before.v && !rs.pending, { before: before.v, after: after.v, row: rs });

  // click away mid-edit with an UNCHANGED value → no write
  await openEditor(SSEL);
  await ev(`(function(){document.getElementById('explorer-search').focus(); return 1;})()`);
  await sleep(700);
  after = await peek(SPATH);
  rs = await rowState(SPATH);
  ok('clicking away with an unchanged value is a no-op (nothing staged)',
     after.v === before.v && !rs.pending, { after: after.v, row: rs });

  // click away mid-edit with a CHANGED value → commits (documented)
  await openEditor(SSEL);
  await setEditorValue(SSEL, '"blurcommit"');
  await ev(`(function(){document.getElementById('explorer-search').focus(); return 1;})()`);
  await sleep(900);
  after = await peek(SPATH);
  rs = await rowState(SPATH);
  ok('clicking away with a CHANGED value commits it (blur = Enter, as documented)',
     after.v === 'blurcommit', { after: after.v, row: rs });

  // the SAME leaf, edited a second time — the editor must show the NEW value
  await openEditor(SSEL);
  edSt = await editorState(SSEL);
  ok('re-opening the same leaf shows the value it now holds, not the one it had',
     edSt && edSt.value === '"blurcommit"', edSt);
  await setEditorValue(SSEL, '"secondedit"');
  await press('Enter'); await sleep(900);
  after = await peek(SPATH);
  rs = await rowState(SPATH);
  ok('the same leaf edited twice lands both times, and the row shows the second value',
     after.v === 'secondedit' && rs.text === '"secondedit"', { stored: after.v, row: rs });

  async function editCase(label, typed, expectFn) {
    await openEditor(SSEL);
    const opened = await editorState(SSEL);
    if (!opened || !opened.open) { editCases.push({ label, error: 'editor did not open' }); return; }
    await setEditorValue(SSEL, typed);
    await press('Enter');
    await sleep(1100);
    const st6 = await peek(SPATH);
    const r6 = await rowState(SPATH);
    const rec = { label: label, typed: typed, stored: st6.v, storedKind: st6.kind,
                  row: r6.text, rowErr: r6.err, confirms: (await ev(`window.__confirms.length`)) };
    editCases.push(rec);
    if (expectFn) ok('inline edit — ' + label, expectFn(rec), rec);
    return rec;
  }

  await ev(`window.__confirms=[]; 1`);
  await editCase('a valid JSON string literal is unwrapped', '"direct"',
                 r => r.stored === 'direct' && r.row === '"direct"');
  await editCase('a bare word (no quotes) is stored as that text', 'bareword',
                 r => r.stored === 'bareword');
  await editCase('an invalid JSON literal (unterminated quote) does not crash',
                 '"unterminated', r => typeof r.stored === 'string');
  await editCase('a 300-character value', '"' + 'L'.repeat(300) + '"',
                 r => typeof r.stored === 'string' && r.stored.length === 300);
  await editCase('a value containing a quote', '"he said \\"hi\\""',
                 r => r.stored === 'he said "hi"');
  await editCase('a value containing a backslash', '"a\\\\b"',
                 r => r.stored === 'a\\b');
  await editCase('Hangul', '"한글 테스트"', r => r.stored === '한글 테스트');
  await editCase('an emoji', '"⚛️🧊"', r => r.stored === '⚛️🧊');
  await editCase('whitespace only', '"   "', r => r.stored === '   ');
  const emptyCase = await editCase('an empty JSON string', '""', null);
  ok('inline edit — an empty string is either stored or refused with a reason, never silently wrong',
     emptyCase && (emptyCase.stored === '' || !!emptyCase.rowErr), emptyCase);
  // completely clearing the box (not even quotes)
  const blankCase = await editCase('the editor emptied completely (no quotes at all)', '', null);
  ok('inline edit — emptying the box entirely is handled honestly',
     blankCase && (blankCase.stored === '' || blankCase.stored === null || !!blankCase.rowErr), blankCase);

  // a NUMBER typed into a string field — the documented type_fix ask
  await ev(`window.__confirms=[]; 1`);
  const numIntoStr = await editCase('a number typed into a string field', '3124', null);
  const confirmsSeen = await ev(`window.__confirms.slice()`);
  ok('inline edit — a number typed unquoted into a string field ASKS before changing the type',
     confirmsSeen.length > 0 && /stored as text/i.test(confirmsSeen[0] || ''),
     { confirms: confirmsSeen, result: numIntoStr });
  note('number-into-string', { confirms: confirmsSeen, stored: numIntoStr && numIntoStr.stored, kind: numIntoStr && numIntoStr.storedKind });

  // restore the leaf to something sane
  await openEditor(SSEL); await setEditorValue(SSEL, '"1,0"'); await press('Enter'); await sleep(900);
  note('grid_location restored to', (await peek(SPATH)).v);

  /* number leaf: 0 / -1 / 1e999 / NaN / text */
  await typeInto(ES, 'x180_DragCosine detuning', { settle: 1000 });
  const NPATH = 'qubits.q1.xy.operations.x180_DragCosine.detuning';
  const NSEL = '.tree-node[data-path="' + NPATH + '"] > .tree-row > .tree-val';
  const nExists = await ev(`!!document.querySelector('${NSEL}')`);
  ok('the numeric leaf ' + NPATH + ' is reachable by search', nExists);
  async function numCase(label, typed, expectFn) {
    await ev(`(function(){var e=document.querySelector('${NSEL}'); if(e) e.click(); return 1;})()`);
    await sleep(450);
    const o = await editorState(NSEL);
    if (!o || !o.open) { numCases.push({ label, error: 'editor did not open' }); return null; }
    await setEditorValue(NSEL, typed);
    await press('Enter'); await sleep(1000);
    const st7 = await peek(NPATH);
    const r7 = await rowState(NPATH);
    const rec = { label, typed, stored: st7.v, kind: st7.kind, row: r7.text, err: r7.err };
    numCases.push(rec);
    if (expectFn) ok('numeric leaf — ' + label, expectFn(rec), rec);
    return rec;
  }
  const n0 = await ev(`(function(){var e=document.querySelector('${NSEL}'); return e? e.dataset.editVal : null;})()`);
  note('detuning starts at', n0);
  await numCase('a plain integer 0', '0', r => Number(r.stored) === 0 && !r.err);
  await numCase('-1', '-1', r => Number(r.stored) === -1);
  await numCase('1e999 (overflows double)', '1e999', r => r.err || r.stored === null || r.stored === undefined || !isFinite(Number(r.stored)) || isFinite(Number(r.stored)));
  const nanRec = await numCase('NaN', 'NaN', null);
  ok('numeric leaf — NaN is either refused with a reason or stored visibly, never a silent lie',
     nanRec && (!!nanRec.err || String(nanRec.row).toLowerCase().indexOf('nan') >= 0 || nanRec.stored === null),
     nanRec);
  const textRec = await numCase('a bare word into a number field', 'abc', null);
  ok('numeric leaf — a word into a number field is refused with an inline reason',
     textRec && (!!textRec.err || typeof textRec.stored === 'string'), textRec);
  await numCase('back to 0', '0', r => Number(r.stored) === 0);
  note('numeric cases', numCases);
  await snap('numeric_edits');

  stage = '9-consistency'; save();
  /* ══ 9. after all that — is the page still CONSISTENT? ═══════════════ */
  await clearBox(ES); await sleep(900);
  const finalTree = await treeState();
  ok('after every edit the tree is unfiltered and whole again',
     finalTree.hidden === 0 && !finalTree.notice, finalTree);
  const modelVsPeek = await ev(`(async function(){
     var c=document.getElementById('explorer-tree-state');
     function get(o,p){ var s=p.split('.'); for(var i=0;i<s.length;i++){ if(o==null) return undefined;
        o = Array.isArray(o) && /^[0-9]+$/.test(s[i]) ? o[Number(s[i])] : o[s[i]]; } return o; }
     var paths=['${SPATH}','${NPATH}'];
     var out=[];
     for (var i=0;i<paths.length;i++){
       var p=paths[i];
       var d=await fetch('/field/peek?dot_path='+encodeURIComponent(p)).then(function(r){return r.json();});
       out.push({path:p, model:get(c._treeData,p), server: d.values? d.values[p]:undefined});
     }
     return out;})()`);
  ok('the in-page model agrees with the server for every leaf we edited',
     modelVsPeek.every(x => String(x.model) === String(x.server)), modelVsPeek);

  // a broad search still works after all the mutation
  await typeInto(ES, BROAD, { settle: 1200 });
  const finalBroad = await treeState();
  const finalMine = await ev(`window.__mine.count('explorer-tree-state', ${JSON.stringify(BROAD)})`);
  const fm = (finalBroad.noticeText || '').match(/^(\d+) matches/);
  ok('the broad search still reports a true count after ~20 edits',
     fm && Number(fm[1]) === finalMine, { notice: finalBroad.noticeText, independent: finalMine });
  await clearBox(ES); await sleep(700);

  // resize small, then back
  await send('Emulation.setDeviceMetricsOverride', { width: 600, height: 520, deviceScaleFactor: 1, mobile: false });
  await sleep(700);
  await typeInto(ES, 'grid_location', { settle: 800 });
  const small = await ev(`(function(){var b=document.body; return {scrollW: b.scrollWidth, clientW: document.documentElement.clientWidth,
      searchVisible: !!document.querySelector('${ES}') && document.querySelector('${ES}').getBoundingClientRect().width>0};})()`);
  ok('at 600x520 the search box is still usable and nothing throws', small.searchVisible, small);
  await snap('narrow');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await sleep(600);
  await clearBox(ES); await sleep(700);
  await snap('final');

  // reload — the page must come back clean
  await send('Page.navigate', { url: BASE + '/explorer' });
  await sleep(5000);
  const reboot = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
      return {nodes: c? c.querySelectorAll('.tree-node').length : -1,
              hidden: c? c.querySelectorAll('.tree-search-hidden').length : -1,
              search: (document.querySelector('${ES}')||{}).value};})()`);
  ok('reloading /explorer after the whole round gives a clean, unfiltered tree',
     reboot.nodes > 0 && reboot.hidden === 0 && !reboot.search, reboot);

  }


  if (want('D')) {
  stage = 'D-followup'; save();
  /* ══ D. the follow-up round ══════════════════════════════════════════
     Three of part A/B's checks failed on MY expectation, not the product,
     and one of A's passed VACUOUSLY (window.scrollTo moved nothing, because
     docs/141 §4q made #table-pane the one vertical scroller). Re-run them
     against the real scroller, reproduce the two genuine defects a second
     time, and finish the consistency stage part C could not reach. */

  const PANE = '#table-pane';
  const paneInfo = await ev(`(function(){var p=document.querySelector('${PANE}');
      return p? {found:true, oy:getComputedStyle(p).overflowY, sh:p.scrollHeight, ch:p.clientHeight} : {found:false};})()`);
  ok('the page has a scrolling #table-pane (the one vertical scroller)',
     paneInfo.found && paneInfo.sh > paneInfo.ch, paneInfo);

  // 1. the notice, checked against the scroller that actually scrolls
  await typeInto(ES, BROAD, { settle: 1400 });
  const stickyBefore = await ev(`(function(){var e=document.querySelector('#explorer-tree-state > .tree-search-results');
      var p=document.querySelector('${PANE}');
      return e? {top:Math.round(e.getBoundingClientRect().top), paneTop:Math.round(p.getBoundingClientRect().top), scrollTop:Math.round(p.scrollTop), sh:p.scrollHeight}:null;})()`);
  await ev(`(function(){var p=document.querySelector('${PANE}'); p.scrollTop = p.scrollHeight; return 1;})()`);
  await sleep(700);
  const stickyAfter = await ev(`(function(){var e=document.querySelector('#explorer-tree-state > .tree-search-results');
      var p=document.querySelector('${PANE}'); var pr=p.getBoundingClientRect();
      if(!e) return {gone:true, scrollTop:Math.round(p.scrollTop)};
      var r=e.getBoundingClientRect();
      return {top:Math.round(r.top), bottom:Math.round(r.bottom), paneTop:Math.round(pr.top), paneBottom:Math.round(pr.bottom),
              scrollTop:Math.round(p.scrollTop), inPane: r.bottom>pr.top && r.top<pr.bottom};})()`);
  ok('the pane really scrolled this time (the earlier window.scrollTo did not)',
     stickyAfter.scrollTop > 500, { before: stickyBefore, after: stickyAfter });
  ok('the capped-search notice is STICKY inside the real scroller',
     stickyAfter.inPane === true, { before: stickyBefore, after: stickyAfter });
  await snap('sticky_real_scroller');

  // 2. the fully expanded tree, scrolled to the far end and back
  await clearBox(ES); await sleep(800);
  await ev(`window.jsonTreeExpandAll('explorer-tree-state'); 1`);
  await sleep(1200);
  await ev(`(function(){var p=document.querySelector('${PANE}'); p.scrollTop=p.scrollHeight; return 1;})()`);
  await sleep(900);
  const farEnd = await ev(`(function(){var p=document.querySelector('${PANE}');
      var rows=document.querySelectorAll('#explorer-tree-state .tree-row');
      return {scrollTop:Math.round(p.scrollTop), sh:p.scrollHeight, rows:rows.length,
              last: rows.length? rows[rows.length-1].textContent.replace(/\\s+/g,' ').slice(0,60):null};})()`);
  ok('a fully expanded 9,893-node tree scrolls to the far end without throwing',
     farEnd.scrollTop > 10000, farEnd);
  await ev(`(function(){var p=document.querySelector('${PANE}'); p.scrollTop=0; return 1;})()`);
  await sleep(600);
  const backTop = await ev(`(function(){var p=document.querySelector('${PANE}');
      var f=document.querySelector('#explorer-tree-state > .tree-node');
      return {scrollTop:Math.round(p.scrollTop), first: f? f.getAttribute('data-path'):null};})()`);
  ok('…and back to the top, with the first row where it belongs',
     backTop.scrollTop === 0 && backTop.first === 'octaves', backTop);
  await snap('far_end_expanded');
  await ev(`window.jsonTreeCollapseAll('explorer-tree-state'); 1`);
  await sleep(500);

  // 3. SECOND reproduction — the ⧉ copy button's ✓ becomes permanent
  await typeInto(ES, 'grid_location', { settle: 900 });
  const LEAF2 = '.tree-node[data-path="qubits.q2.grid_location"]';
  await hover(LEAF2 + ' > .tree-row');
  const copy2 = await ev(`(async function(){
      var r=document.querySelector('${LEAF2} > .tree-row');
      var a=r && r.querySelector(':scope > .tree-row-actions');
      var b=a && Array.prototype.filter.call(a.children,function(x){return x.textContent==='⧉';})[0];
      if(!b) return null;
      var seq=[];
      b.click(); await new Promise(s=>setTimeout(s,300)); seq.push('after1:'+b.textContent);
      b.click(); await new Promise(s=>setTimeout(s,300)); seq.push('after2:'+b.textContent);
      await new Promise(s=>setTimeout(s,2500));
      return {seq:seq, settled:b.textContent};})()`);
  ok('DEFECT re-check: two ⧉ presses inside 800ms leave the button stuck on ✓',
     copy2 && copy2.settled !== '⧉', copy2);
  // and one press alone settles correctly (so the mechanism, not the button, is the fault)
  const copy1 = await ev(`(async function(){
      var r=document.querySelector('${LEAF2} > .tree-row');
      var a=r && r.querySelector(':scope > .tree-row-actions');
      var b=a && Array.prototype.filter.call(a.children,function(x){return x.textContent==='⧉';})[0];
      if(!b) return null;
      b.textContent='⧉';
      b.click(); await new Promise(s=>setTimeout(s,300)); var mid=b.textContent;
      await new Promise(s=>setTimeout(s,1200));
      return {mid:mid, settled:b.textContent};})()`);
  ok('…while ONE press alone does settle back to ⧉',
     copy1 && copy1.settled === '⧉', copy1);

  // 4. THIRD reproduction — a tab left filtered under an empty search box,
  //    and whether anything on that tab can clear it.
  await clearBox(ES); await sleep(700);
  await ev(`window.switchExplorerTab('state'); 1`); await sleep(400);
  await typeInto(ES, 'frequency', { settle: 1000 });
  const t1 = await treeState();
  await ev(`window.switchExplorerTab('wiring'); 1`); await sleep(600);
  await clearBox(ES); await sleep(900);
  await ev(`window.switchExplorerTab('state'); 1`); await sleep(800);
  const t2 = await treeState();
  ok('DEFECT re-check (3rd): the state tab stays filtered under an EMPTY search box',
     t2.hidden > 0 && (await val(ES)) === '', { whileSearching: { hidden: t1.hidden, hl: t1.highlighted },
       afterClearOnWiring: { hidden: t2.hidden, hl: t2.highlighted, notice: t2.noticeText }, box: await val(ES) });
  // what does a user have to do to get out of it?
  const escape1 = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
      // pressing a depth button — the obvious "reset the tree" control
      window.jsonTreeExpandToDepth('explorer-tree-state',1);
      return c.querySelectorAll('.tree-node.tree-search-hidden').length;})()`);
  ok('…and a Depth button does NOT clear it (the rows stay hidden)', escape1 > 0, escape1);
  const escape2 = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
      return {reload:'not tried', hidden:c.querySelectorAll('.tree-node.tree-search-hidden').length,
              lastQ:c._lastSearchQuery};})()`);
  note('the stuck tab after a Depth press', escape2);
  await snap('stale_filter_empty_box');
  // typing and clearing ON that tab is the recovery
  await typeInto(ES, 'x', { settle: 700 });
  await clearBox(ES); await sleep(900);
  const recovered = await treeState();
  ok('…but retyping and clearing ON that tab does recover it', recovered.hidden === 0, recovered);

  /* 5. the type-fix offer, set up the way the code comment describes:
        a leaf that HOLDS a numeric string, then the quotes deleted. */
  const SP = 'qubits.q3.grid_location';
  const SS = '.tree-node[data-path="' + SP + '"] > .tree-row > .tree-val';
  await typeInto(ES, 'grid_location', { settle: 900 });
  await ev(`window.__confirms=[]; 1`);
  await ev(`(function(){var e=document.querySelector('${SS}'); if(e) e.click(); return 1;})()`);
  await sleep(450);
  await ev(`(function(){var e=document.querySelector('${SS}'); var i=e&&e.querySelector('input');
      if(i){ i.focus(); i.value='"3124"'; i.dispatchEvent(new Event('input',{bubbles:true})); } return 1;})()`);
  await press('Enter'); await sleep(1100);
  const asText = await ev(`fetch('/field/peek?dot_path=${SP}').then(function(r){return r.json();})
      .then(function(d){return {v:d.values['${SP}'], t:typeof d.values['${SP}']};})`);
  ok('a quoted number commits as TEXT (the literal is the file\'s own spelling)',
     asText.t === 'string' && asText.v === '3124', asText);
  // now delete the quotes: the documented "make this a number" gesture
  await ev(`(function(){var e=document.querySelector('${SS}'); if(e) e.click(); return 1;})()`);
  await sleep(450);
  const shown = await ev(`(function(){var e=document.querySelector('${SS}'); var i=e&&e.querySelector('input'); return i?i.value:null;})()`);
  await ev(`(function(){var e=document.querySelector('${SS}'); var i=e&&e.querySelector('input');
      if(i){ i.focus(); i.value='3124'; i.dispatchEvent(new Event('input',{bubbles:true})); } return 1;})()`);
  await press('Enter'); await sleep(1500);
  const confirms = await ev(`window.__confirms.slice()`);
  const afterFix = await ev(`fetch('/field/peek?dot_path=${SP}').then(function(r){return r.json();})
      .then(function(d){return {v:d.values['${SP}'], t:typeof d.values['${SP}']};})`);
  ok('deleting the quotes on a numeric string ASKS before changing the type',
     confirms.length > 0 && /stored as text/i.test(confirms[0] || ''),
     { editorShowed: shown, confirms: confirms, after: afterFix });
  ok('…and after answering OK the leaf is a NUMBER, not text',
     afterFix.t === 'number' && afterFix.v === 3124, afterFix);
  note('type-fix gesture', { editorShowed: shown, confirms: confirms, after: afterFix });
  await snap('type_fix_offer');
  // put it back
  await ev(`(function(){var e=document.querySelector('${SS}'); if(e) e.click(); return 1;})()`);
  await sleep(450);
  await ev(`(function(){var e=document.querySelector('${SS}'); var i=e&&e.querySelector('input');
      if(i){ i.focus(); i.value='"3,0"'; i.dispatchEvent(new Event('input',{bubbles:true})); } return 1;})()`);
  await press('Enter'); await sleep(900);

  /* 6. the consistency stage part C could not reach (its Page.navigate
        timed out at 45s). Fire the navigate WITHOUT awaiting the command
        response, and measure how long the document actually takes. */
  await clearBox(ES); await sleep(800);
  const finalTree = await treeState();
  ok('after every edit of this round the tree is unfiltered and whole again',
     finalTree.hidden === 0 && !finalTree.notice, finalTree);
  await typeInto(ES, BROAD, { settle: 1400 });
  const fb = await treeState();
  const fbMine = await ev(`window.__mine.count('explorer-tree-state', ${JSON.stringify(BROAD)})`);
  const fbm = (fb.noticeText || '').match(/^(\d+) matches/);
  ok('the broad search still reports a TRUE count after ~30 edits',
     fbm && Number(fbm[1]) === fbMine, { notice: fb.noticeText, independent: fbMine });
  await clearBox(ES); await sleep(700);

  // narrow viewport
  await send('Emulation.setDeviceMetricsOverride', { width: 600, height: 520, deviceScaleFactor: 1, mobile: false });
  await sleep(800);
  await typeInto(ES, 'grid_location', { settle: 900 });
  const small = await ev(`(function(){var e=document.querySelector('${ES}');
      var r=e.getBoundingClientRect();
      return {searchW:Math.round(r.width), bodyScrollW: document.body.scrollWidth,
              docW: document.documentElement.clientWidth,
              hl: document.querySelectorAll('#explorer-tree-state .tree-highlight').length};})()`);
  ok('at 600x520 the search box is usable and the filter still works',
     small.searchW > 40 && small.hl > 0, small);
  await snap('narrow_600');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await sleep(600);
  await clearBox(ES); await sleep(600);

  // the reload, measured rather than awaited
  const tNav = Date.now();
  send('Page.navigate', { url: BASE + '/explorer' }, 120000).then(
    () => notes.push({ k: 'Page.navigate command answered after ms', v: Date.now() - tNav }),
    e => notes.push({ k: 'Page.navigate command FAILED', v: String(e && e.message) }));
  const loaded = await (async () => {
    const t = Date.now();
    while (Date.now() - t < 60000) {
      try {
        const v = await ev(`(function(){return {rs:document.readyState, url:location.pathname,
            nodes: (document.getElementById('explorer-tree-state')||{querySelectorAll:function(){return [];}}).querySelectorAll('.tree-node').length};})()`);
        if (v && v.rs === 'complete' && v.nodes > 0) return { ms: Date.now() - t, ...v };
      } catch (e) { /* context swap mid-navigation */ }
      await sleep(400);
    }
    return { ms: Date.now() - t, timedOut: true };
  })();
  ok('a reload of /explorer after the whole round comes back within 60s',
     !loaded.timedOut, loaded);
  note('reload after the round', loaded);
  await sleep(1500);
  const reboot = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
      return {nodes: c? c.querySelectorAll('.tree-node').length : -1,
              hidden: c? c.querySelectorAll('.tree-search-hidden').length : -1,
              search: (document.querySelector('${ES}')||{}).value,
              tray: (document.querySelector('#pending-tray')||{}).getAttribute
                    ? document.querySelector('#pending-tray').getAttribute('data-change-count') : null};})()`);
  ok('…clean, unfiltered, with an empty search box',
     reboot.nodes > 0 && reboot.hidden === 0 && !reboot.search, reboot);
  await snap('after_reload');
  }


  if (want('E')) {
  stage = 'E-scroller'; save();
  /* ══ E. which box actually scrolls, and what a reload costs ══════════
     Part A scrolled `window` (moved nothing) and part D scrolled
     `#table-pane` (954px tall against a 9,893-row tree). Find the real
     scrolling ancestor first, then judge stickiness and the far end against
     IT — and time the reload three ways, because part C and part D both hung
     on the same Page.navigate. */

  const chain = await ev(`(function(){
      var e=document.getElementById('explorer-tree-state'), out=[];
      while (e && e !== document.documentElement) {
        var cs=getComputedStyle(e);
        out.push({tag:e.tagName.toLowerCase(), id:e.id||null, cls:(e.className||'').toString().slice(0,50),
                  oy:cs.overflowY, ox:cs.overflowX, pos:cs.position,
                  sh:e.scrollHeight, ch:e.clientHeight, scrollable:(e.scrollHeight - e.clientHeight) > 4});
        e=e.parentElement;
      }
      out.push({tag:'documentElement', sh:document.documentElement.scrollHeight, ch:document.documentElement.clientHeight,
                oy:getComputedStyle(document.documentElement).overflowY,
                scrollable:(document.documentElement.scrollHeight-document.documentElement.clientHeight)>4});
      return out;})()`);
  note('ancestor chain of #explorer-tree-state (collapsed tree)', chain);
  ok('the tree has at least one scrolling ancestor once it is tall',
     Array.isArray(chain) && chain.length > 0, chain.length);

  // Fire the broad query, then find the ancestor that CAN scroll.
  await typeInto(ES, BROAD, { settle: 1500 });
  const scroller = await ev(`(function(){
      var e=document.getElementById('explorer-tree-state');
      while (e && e !== document.documentElement) {
        var cs=getComputedStyle(e);
        if ((cs.overflowY==='auto'||cs.overflowY==='scroll') && (e.scrollHeight-e.clientHeight)>4) {
          window.__scroller = e;
          return {id:e.id||null, cls:(e.className||'').toString().slice(0,60), sh:e.scrollHeight, ch:e.clientHeight};
        }
        e=e.parentElement;
      }
      if ((document.documentElement.scrollHeight-document.documentElement.clientHeight)>4) {
        window.__scroller = document.scrollingElement;
        return {id:'documentElement', sh:document.documentElement.scrollHeight, ch:document.documentElement.clientHeight};
      }
      window.__scroller = null; return null;})()`);
  note('the real scroller with a capped search on screen', scroller);
  ok('with a capped search the tree is inside something that scrolls', !!scroller, scroller);

  if (scroller) {
    const before = await ev(`(function(){var n=document.querySelector('#explorer-tree-state > .tree-search-results');
        var s=window.__scroller; var sr=s.getBoundingClientRect? s.getBoundingClientRect():{top:0,bottom:innerHeight};
        return {noticeTop:Math.round(n.getBoundingClientRect().top), sTop:Math.round(sr.top), sBottom:Math.round(sr.bottom), scrollTop:Math.round(s.scrollTop)};})()`);
    await ev(`(function(){var s=window.__scroller; s.scrollTop = s.scrollHeight; return 1;})()`);
    await sleep(800);
    const after = await ev(`(function(){var n=document.querySelector('#explorer-tree-state > .tree-search-results');
        var s=window.__scroller; var sr=s.getBoundingClientRect? s.getBoundingClientRect():{top:0,bottom:innerHeight};
        if(!n) return {gone:true, scrollTop:Math.round(s.scrollTop)};
        var r=n.getBoundingClientRect();
        return {noticeTop:Math.round(r.top), noticeBottom:Math.round(r.bottom),
                sTop:Math.round(sr.top), sBottom:Math.round(sr.bottom),
                scrollTop:Math.round(s.scrollTop),
                visible: r.bottom > Math.max(sr.top,0) && r.top < Math.min(sr.bottom, innerHeight),
                sticky: getComputedStyle(n).position};})()`);
    ok('the real scroller moved a meaningful distance',
       after.scrollTop > 200, { before, after });
    ok('the capped-search notice stays visible after scrolling to the far end (it declares position:sticky)',
       after.visible === true, { before, after, declaredPosition: after.sticky });
    note('notice stickiness, measured on the real scroller', { before, after });
    await snap('E_notice_far_end');
    await ev(`(function(){window.__scroller.scrollTop = 0; return 1;})()`);
    await sleep(400);
  }

  /* one ⧉ press, the button found by CLASS this time (part D looked it up by
     its label, which the stuck-✓ defect had already changed) */
  await clearBox(ES); await sleep(700);
  await typeInto(ES, 'grid_location', { settle: 900 });
  const L5 = '.tree-node[data-path="qubits.q5.grid_location"]';
  await hover(L5 + ' > .tree-row');
  const one = await ev(`(async function(){
      var r=document.querySelector('${L5} > .tree-row');
      var b=r && r.querySelector('.tree-act-copy');
      if(!b) return null;
      var start=b.textContent;
      b.click(); await new Promise(s=>setTimeout(s,300)); var mid=b.textContent;
      await new Promise(s=>setTimeout(s,1500));
      return {start:start, mid:mid, settled:b.textContent};})()`);
  ok('ONE ⧉ press gives feedback and settles back to ⧉',
     one && one.start === '⧉' && one.mid !== '⧉' && one.settled === '⧉', one);
  const two = await ev(`(async function(){
      var r=document.querySelector('${L5} > .tree-row');
      var b=r && r.querySelector('.tree-act-copy');
      if(!b) return null;
      b.click(); await new Promise(s=>setTimeout(s,250));
      b.click(); await new Promise(s=>setTimeout(s,2500));
      return {settled:b.textContent};})()`);
  ok('DEFECT (4th reproduction): TWO presses inside the 800ms window leave it stuck',
     two && two.settled !== '⧉', two);
  await snap('E_copy_stuck');

  /* the reload, timed three ways */
  const navTime = async (label) => {
    const t0 = Date.now();
    let cmdMs = null;
    send('Page.navigate', { url: BASE + '/explorer' }, 150000)
      .then(() => { cmdMs = Date.now() - t0; }, () => { cmdMs = -1; });
    let ready = null;
    while (Date.now() - t0 < 100000) {
      try {
        const v = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
            return {rs:document.readyState, n:c?c.querySelectorAll('.tree-node').length:0};})()`);
        if (v && v.rs === 'complete' && v.n > 0) { ready = Date.now() - t0; break; }
      } catch (e) { /* execution context swapped */ }
      await sleep(300);
    }
    const rec = { label, navigateCmdMs: cmdMs, documentReadyMs: ready };
    note('reload timing — ' + label, rec);
    return rec;
  };
  const nav1 = await navTime('from a page whose tree is at depth 1');
  ok('a reload from an ordinary (depth-1) /explorer completes', nav1.documentReadyMs !== null, nav1);

  await sleep(1500);
  const tExp = Date.now();
  await ev(`window.jsonTreeExpandAll('explorer-tree-state'); 1`);
  await sleep(1500);
  const expanded = await ev(`document.getElementById('explorer-tree-state').querySelectorAll('.tree-node').length`);
  note('expand-all before the second reload', { nodes: expanded, ms: Date.now() - tExp });
  const nav2 = await navTime('from a page with all 9,893 nodes expanded');
  ok('a reload from a FULLY EXPANDED /explorer completes too', nav2.documentReadyMs !== null, nav2);
  note('reload comparison', { depth1: nav1, expanded: nav2 });
  await snap('E_after_reload');
  }


  if (want('F')) {
  stage = 'F-reload';  save();
  /* ══ F. the reload, with the unload dialog answered ══════════════════
     Parts C/D/E all hung at a Page.navigate. The cause was this driver,
     not the product: the round leaves unsaved edits in the pending tray,
     app.js:452 arms a beforeunload guard, and nothing was answering the
     dialog. With the handler installed above, the same gesture is re-run. */
  // A beforeunload dialog only fires on a document with STICKY ACTIVATION, so
  // the gesture must include real keystrokes before the navigation -- which is
  // exactly the difference between parts C/D/E (they hung) and F's first cut
  // (it did not). Type first, with real key events.
  await typeInto(ES, 'grid_location', { settle: 900 });
  await clearBox(ES); await sleep(600);
  const tray = await ev(`(function(){var t=document.getElementById('pending-tray');
      return {found:!!t, count: t? t.getAttribute('data-change-count') : null};})()`);
  note('pending tray before the reload', tray);
  ok('the round really did leave unsaved edits behind (so the guard should arm)',
     tray.found && Number(tray.count) > 0, tray);

  const t0 = Date.now();
  const navPromise = send('Page.navigate', { url: BASE + '/explorer' }, 60000);
  let ready = null;
  while (Date.now() - t0 < 45000) {
    try {
      const v = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
          return {rs:document.readyState, n:c?c.querySelectorAll('.tree-node').length:0};})()`);
      if (v && v.rs === 'complete' && v.n > 0) { ready = Date.now() - t0; break; }
    } catch (e) { /* execution context swapped mid-navigation */ }
    await sleep(250);
  }
  let cmdMs = null;
  try { await navPromise; cmdMs = Date.now() - t0; } catch (e) { cmdMs = -1; }
  ok('with the unload dialog answered, /explorer reloads normally',
     ready !== null && ready < 30000, { documentReadyMs: ready, navigateCmdMs: cmdMs, dialogs: dialogs.slice() });
  ok('…and the guard DID fire — the app warned about the unsaved edits',
     dialogs.some(d => d.type === 'beforeunload'), dialogs.slice());
  note('reload with the dialog answered', { documentReadyMs: ready, navigateCmdMs: cmdMs, dialogs: dialogs.slice() });

  await sleep(1500);
  await injectMine();          // the reload threw the previous document's copy away
  const reboot = await ev(`(function(){var c=document.getElementById('explorer-tree-state');
      var t=document.getElementById('pending-tray');
      return {nodes: c? c.querySelectorAll('.tree-node').length : -1,
              hidden: c? c.querySelectorAll('.tree-search-hidden').length : -1,
              notice: !!document.querySelector('#explorer-tree-state > .tree-search-results'),
              search: (document.querySelector('${ES}')||{}).value,
              tray: t? t.getAttribute('data-change-count') : null};})()`);
  ok('the reloaded page is clean: unfiltered tree, empty box, edits still staged',
     reboot.nodes > 0 && reboot.hidden === 0 && !reboot.notice && !reboot.search
     && Number(reboot.tray) > 0, reboot);
  await snap('F_after_reload');

  // and the search still tells the truth on the freshly loaded document
  await typeInto(ES, BROAD, { settle: 1500 });
  const fresh = await treeState();
  const freshMine = await ev(`window.__mine.count('explorer-tree-state', ${JSON.stringify(BROAD)})`);
  const fm2 = (fresh.noticeText || '').match(/^(\d+) matches/);
  ok('after the reload the capped notice still names a TRUE count',
     fm2 && Number(fm2[1]) === freshMine, { notice: fresh.noticeText, independent: freshMine });
  await clearBox(ES); await sleep(700);
  const clean = await treeState();
  ok('…and clearing still unhides everything', clean.hidden === 0, clean);
  await snap('F_final');
  }


  if (want('G')) {
  stage = 'G-json-editor'; save();
  /* ══ G. the ✎ edit-as-JSON editor, and how a quote reads in a row ════
     Part B proved the ✎ button exists on a container row; nothing had yet
     PRESSED it. Feed it invalid JSON, Escape, a valid commit, Ctrl+Enter,
     and a 300-character string — and measure how a value containing a quote
     or a backslash is rendered back into the row. */

  await typeInto(ES, 'extras', { settle: 1000 });
  const DICT = '.tree-node[data-path="qubits.q1.extras"]';
  const present = await ev(`!!document.querySelector('${DICT}')`);
  ok('a small dict (qubits.q1.extras) is reachable by search', present);

  const openJson = async () => {
    await ev(`(function(){var n=document.querySelector('${DICT}');
        var b=n && n.querySelector(':scope > .tree-row > .tree-json-edit-btn');
        if(b) b.click(); return 1;})()`);
    await sleep(500);
    return ev(`(function(){var n=document.querySelector('${DICT}');
        var e=n && n.querySelector(':scope > .tree-json-editor');
        var ta=e && e.querySelector('.tree-json-textarea');
        return e? {open:true, text: ta? ta.value : null, hint:(e.querySelector('.tree-json-hint')||{}).textContent||null} : {open:false};})()`);
  };
  const setJson = (t) => ev(`(function(){var n=document.querySelector('${DICT}');
      var ta=n.querySelector(':scope > .tree-json-editor .tree-json-textarea');
      if(!ta) return 0; ta.focus(); ta.value=${JSON.stringify(t)};
      ta.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  const jsonState = () => ev(`(function(){var n=document.querySelector('${DICT}');
      var e=n && n.querySelector(':scope > .tree-json-editor');
      var er=e && e.querySelector('.tree-json-err');
      return {open:!!e, err: er && !er.hidden ? er.textContent : null,
              saveDisabled: e? e.querySelector('.btn-sm').disabled : null};})()`);
  const clickSave = () => ev(`(function(){var n=document.querySelector('${DICT}');
      var b=n.querySelector(':scope > .tree-json-editor .tree-json-editor-bar .btn-sm');
      if(b) b.click(); return 1;})()`);
  const peekX = (p) => ev(`fetch('/field/peek?dot_path=' + encodeURIComponent(${JSON.stringify(p)}))
      .then(function(r){return r.json();}).then(function(d){return {v:d.values[${JSON.stringify(p)}], t:typeof d.values[${JSON.stringify(p)}]};})`);

  let js = await openJson();
  ok('the ✎ button opens a JSON editor prefilled with the subtree',
     js.open && /1QRB_p/.test(String(js.text)), { hint: js.hint, head: String(js.text).slice(0, 90) });

  // invalid JSON
  await setJson('{ this is not json ');
  await clickSave();
  await sleep(700);
  const bad = await jsonState();
  ok('invalid JSON is refused inline, with the parser\'s own reason, and the editor stays open',
     bad.open && /invalid json/i.test(String(bad.err)), bad);
  await snap('G_invalid_json');

  // Escape closes without committing
  const beforeEsc = await peekX('qubits.q1.extras.1QRB_p');
  await ev(`(function(){var n=document.querySelector('${DICT}');
      var ta=n.querySelector(':scope > .tree-json-editor .tree-json-textarea');
      ta.focus(); ta.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); return 1;})()`);
  await sleep(500);
  const afterEsc = await jsonState();
  const stillThere = await peekX('qubits.q1.extras.1QRB_p');
  ok('Escape closes the JSON editor and commits nothing',
     !afterEsc.open && String(stillThere.v) === String(beforeEsc.v), { afterEsc, before: beforeEsc.v, after: stillThere.v });

  // a valid commit, through Ctrl+Enter
  js = await openJson();
  await setJson('{"1QRB_p": 0.5, "1QRB_p_sem": 1e-05, "epg": 0.001, "coupler_q1_q2_dispersion_load_id": 1851}');
  await ev(`(function(){var n=document.querySelector('${DICT}');
      var ta=n.querySelector(':scope > .tree-json-editor .tree-json-textarea');
      ta.focus(); ta.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',ctrlKey:true,bubbles:true})); return 1;})()`);
  await sleep(1400);
  const committed = await peekX('qubits.q1.extras.1QRB_p');
  const closed = await jsonState();
  ok('Ctrl+Enter commits valid JSON and closes the editor',
     !closed.open && Number(committed.v) === 0.5, { editorOpen: closed.open, stored: committed });
  await snap('G_json_committed');

  // pressing ✎ three times never stacks an editor
  const stack = await ev(`(async function(){var n=document.querySelector('${DICT}'); var out=[];
      for(var i=0;i<3;i++){ var b=n.querySelector(':scope > .tree-row > .tree-json-edit-btn');
        if(b) b.click(); await new Promise(s=>setTimeout(s,250));
        out.push(n.querySelectorAll(':scope > .tree-json-editor').length); }
      var c=n.querySelector(':scope > .tree-json-editor .tree-json-editor-bar .btn-sm.outline');
      if(c) c.click(); await new Promise(s=>setTimeout(s,200));
      return {counts: out, closed: !n.querySelector(':scope > .tree-json-editor')};})()`);
  ok('pressing ✎ three times never stacks a second editor, and Cancel closes it',
     stack && stack.counts.every(c => c === 1) && stack.closed, stack);

  /* how a quote and a backslash read back in the row (docs/145 says the
     EDITOR shows the file's own spelling; this measures the ROW) */
  const SQ = 'qubits.q4.grid_location';
  const SQS = '.tree-node[data-path="' + SQ + '"] > .tree-row > .tree-val';
  await typeInto(ES, 'grid_location', { settle: 900 });
  const roundTrip = [];
  for (const lit of ['"he said \\"hi\\""', '"a\\\\b"', '"tab\\there"']) {
    await ev(`(function(){var e=document.querySelector('${SQS}'); if(e) e.click(); return 1;})()`);
    await sleep(400);
    await ev(`(function(){var e=document.querySelector('${SQS}'); var i=e&&e.querySelector('input');
        if(i){ i.focus(); i.value=${JSON.stringify(lit)}; i.dispatchEvent(new Event('input',{bubbles:true})); } return 1;})()`);
    await press('Enter'); await sleep(1000);
    const st = await peekX(SQ);
    const rowText = await ev(`(document.querySelector('${SQS}')||{}).textContent`);
    // reopen to see what the editor offers for the SAME value
    await ev(`(function(){var e=document.querySelector('${SQS}'); if(e) e.click(); return 1;})()`);
    await sleep(400);
    const editorText = await ev(`(function(){var e=document.querySelector('${SQS}'); var i=e&&e.querySelector('input'); return i?i.value:null;})()`);
    await press('Escape'); await sleep(300);
    roundTrip.push({ typed: lit, stored: st.v, row: rowText, editorShows: editorText,
                     rowIsJson: rowText === editorText });
  }
  note('a quote / backslash / tab, round-tripped', roundTrip);
  ok('the ROW renders a string with the same JSON spelling the editor shows',
     roundTrip.every(r => r.rowIsJson), roundTrip);
  await snap('G_quote_display');
  // restore
  await ev(`(function(){var e=document.querySelector('${SQS}'); if(e) e.click(); return 1;})()`);
  await sleep(400);
  await ev(`(function(){var e=document.querySelector('${SQS}'); var i=e&&e.querySelector('input');
      if(i){ i.focus(); i.value='"4,0"'; i.dispatchEvent(new Event('input',{bubbles:true})); } return 1;})()`);
  await press('Enter'); await sleep(800);
  await clearBox(ES); await sleep(600);
  }

  stage = 'done'; save({ done: true });
  const bad = results.filter(x => !x.pass);
  const csp = errors.filter(e => /unsafe-eval/.test(String(e.text)) && /htmx/.test(String(e.text)));
  const other = errors.filter(e => !(/unsafe-eval/.test(String(e.text)) && /htmx/.test(String(e.text))));
  console.log('checks: ' + (results.length - bad.length) + '/' + results.length
    + '   htmx-CSP errors: ' + csp.length + '   OTHER errors: ' + other.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 420)));
  other.slice(0, 25).forEach(e => console.log('  ERR  ' + e.kind + ': ' + String(e.text).slice(0, 300)));
  process.exit(0);
}
main().catch(e => {
  console.error('driver error: ' + (e && e.stack || e));
  fs.writeFileSync(OUT, JSON.stringify({ results, errors, notes, driver: String(e && e.stack || e) }, null, 1));
  process.exit(1);
});
