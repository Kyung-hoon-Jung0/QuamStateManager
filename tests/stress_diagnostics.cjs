/* Diagnostics (/diagnostics) — the hostile-user round, in real headless Chrome.
 *
 * Not a screenshot round. What is driven here is what a screenshot cannot show:
 * the same pill clicked three times, every severity turned off at once and the
 * page reloaded, a <dialog> asked to open while it is already open, a domain
 * folded and then a self-refresh landing on top of it, the findings list
 * re-fetching itself on diagnostics-changed while an acknowledgement is in
 * flight, the repair PLAN opened three times and closed without applying, the
 * page at 400px and back, and ~25 s of sitting still to see who keeps talking.
 *
 * Every uncaught exception and console error of the WHOLE session is collected
 * and reported; every network request is counted and bucketed by URL.
 *
 * argv[2] = json out, argv[3] = CDP port, argv[4] = base url, argv[5] = phase
 *   phase: "main" (default) | "archive" | "unloaded"
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9413';
const BASE = process.argv[4] || 'http://127.0.0.1:5413';
const PHASE = process.argv[5] || 'main';
const CHIP = process.argv[6] || '';

const errors = [];
const results = [];
const net = [];
const dialogs = [];       // every native confirm()/alert() the product raised
let dialogAnswer = false; // what we answer them with
function ok(name, cond, detail) {
  results.push({ name: name, pass: !!cond, detail: detail === undefined ? null : detail });
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
      net.push({ t: Date.now(), url: m.params.request.url, method: m.params.request.method });
    }
    // A NATIVE confirm()/alert() blocks the renderer, and every CDP evaluate
    // after it hangs for ever. Answer it the way the test wants, and record
    // that the product asked — the message is itself evidence.
    if (m.method === 'Page.javascriptDialogOpening') {
      // "leave the page" is always accepted — refusing it CANCELS the
      // navigation and every later check would read a stale page.
      const acc = m.params.type === 'beforeunload' ? true : dialogAnswer;
      dialogs.push({ type: m.params.type, message: m.params.message, accept: acc });
      ws.send(JSON.stringify({ id: ++id, method: 'Page.handleJavaScriptDialog',
                               params: { accept: acc } }));
    }
  };
  // No CDP call may hang the whole run: 60 s and it resolves as a timeout.
  const send = (method, params = {}) => new Promise(res => {
    const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params }));
    setTimeout(() => { if (pending.has(i)) { pending.delete(i); res({ id: i, __timeout: true, result: {} }); } }, 60000);
  });
  const ev = async (expr) => {
    const rr = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (rr.__timeout) throw new Error('CDP evaluate timed out (blocked renderer?): ' + expr.slice(0, 100));
    if (rr.result && rr.result.exceptionDetails) {
      const ex = rr.result.exceptionDetails.exception;
      throw new Error((ex && (ex.description || ex.value)) || 'eval failed: ' + expr.slice(0, 120));
    }
    return rr.result.result.value;
  };
  // Same as ev() but an in-page throw is RETURNED, not raised — used where the
  // question is "does the product throw here?".
  const evSafe = async (expr) => {
    try { return { ok: true, v: await ev('(function(){try{ return {e:0, v:(' + expr + ')}; }catch(err){ return {e:1, v:String(err)}; }})()') }; }
    catch (e) { return { ok: false, v: String(e) }; }
  };
  const sleep = ms => new Promise(res => setTimeout(res, ms));
  const until = async (expr, ms) => {
    const t = Date.now();
    while (Date.now() - t < ms) { const v = await ev(expr); if (v) return v; await sleep(150); }
    return await ev(expr);
  };
  const KEYS = { Enter: 13, Tab: 9, Escape: 27, ArrowDown: 40, ArrowUp: 38, Space: 32, End: 35, Home: 36 };
  const press = async (key) => {
    const p = { type: 'rawKeyDown', key: key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key] };
    if (key === 'Enter') { p.type = 'keyDown'; p.text = '\r'; }
    if (key === 'Space') { p.type = 'keyDown'; p.text = ' '; p.key = ' '; }
    await send('Input.dispatchKeyEvent', p);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: p.key, windowsVirtualKeyCode: KEYS[key] });
  };
  // A REAL click: hit-tested at the element's viewport centre, so anything
  // covering it (a <dialog> backdrop) intercepts exactly as it would for a person.
  const rectOf = (sel, idx) => ev(`(function(){var l=document.querySelectorAll(${JSON.stringify(sel)}); var e=l[${idx || 0}];
      if(!e) return null; e.scrollIntoView({block:'center'});
      var r=e.getBoundingClientRect();
      return {x:r.left+r.width/2, y:r.top+r.height/2, w:r.width, h:r.height};})()`);
  const clickAt = async (sel, idx) => {
    const r = await rectOf(sel, idx);
    if (!r || r.w === 0 || r.h === 0) return false;
    for (const type of ['mousePressed', 'mouseReleased']) {
      await send('Input.dispatchMouseEvent', { type, x: Math.round(r.x), y: Math.round(r.y),
                                               button: 'left', clickCount: 1 });
    }
    return true;
  };
  const shot = async (name) => {
    const s = await send('Page.captureScreenshot', { format: 'png' });
    if (s.__timeout || !s.result || !s.result.data) return '(screenshot timed out: ' + name + ')';
    const p = OUT.replace(/\.json$/, '_' + name + '.png');
    fs.writeFileSync(p, Buffer.from(s.result.data, 'base64'));
    return p;
  };
  const shots = [];
  const snap = async (n) => { shots.push(await shot(n)); };
  // docs/78: on chip open the type alert RAISES ITSELF over whatever page you
  // are on, as a modal overlay. A person clicks Cancel. So must the driver —
  // otherwise every click below lands on its backdrop and measures nothing.
  const alertState = () => ev(`(function(){var o=document.querySelector('.tfx-overlay');
      if(!o || getComputedStyle(o).display==='none') return null;
      return {txt:(o.innerText||'').replace(/\\s+/g,' ').slice(0,240),
              cancel:!!Array.prototype.find.call(o.querySelectorAll('button'),function(b){return /^Cancel$/.test(b.textContent.trim());})};})()`);
  const dismissAlert = async () => {
    const a = await alertState();
    if (!a) return null;
    await ev(`(function(){var o=document.querySelector('.tfx-overlay');
        var b=Array.prototype.find.call(o.querySelectorAll('button'),function(x){return /^Cancel$/.test(x.textContent.trim());});
        if(b) b.click(); return 1;})()`);
    await sleep(500);
    return a;
  };

  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  /* ================================================================= */
  if (PHASE === 'unloaded') {
    await send('Page.navigate', { url: BASE + '/diagnostics' });
    await sleep(3500);
    const st = await ev(`(function(){
        var b=document.body.innerText||'';
        return {h2:(document.querySelector('#table-pane h2')||{}).textContent||'',
                bar:!!document.getElementById('diag-filter-bar'),
                findings:!!document.getElementById('diag-findings'),
                types:!!document.getElementById('diag-types-card'),
                empty:!!document.querySelector('.empty-state, [class*="empty"]'),
                text:b.slice(0,400)};})()`);
    ok('with no chip loaded /diagnostics renders an empty state, not a crash',
       st && !st.bar && !st.findings, st);
    ok('…and it says what to do', st && /load|open|folder|chip|state/i.test(st.text), st && st.text.slice(0, 200));
    const codes = await ev(`(async function(){var o={};
        for (var u of ['/diagnostics','/diagnostics/summary','/diagnostics/banner','/diagnostics/env-card','/diagnostics/types-card','/diagnostics/findings.json','/type-fix/plan','/type-alert']) {
          try { var r = await fetch(u); o[u]=r.status; } catch(e){ o[u]='ERR '+e; } }
        return o;})()`);
    ok('every diagnostics sub-route answers without a 500 when nothing is loaded',
       Object.values(codes).every(c => c === 200 || c === 204), codes);
    await snap('unloaded');
    fs.writeFileSync(OUT, JSON.stringify({ phase: PHASE, results, errors, dialogs, net: net.length, shots }, null, 1));
    report(); return;
  }

  if (PHASE === 'plan') {
    // The repair offer, opened and closed — never applied. The overlay is a
    // plain div (.tfx-overlay), built lazily by app.js — not a <dialog>.
    await send('Page.navigate', { url: BASE + '/diagnostics' });
    await sleep(5000);
    await dismissAlert();
    const planState = () => ev(`(function(){
        var o=document.querySelectorAll('.tfx-overlay');
        var vis=0; o.forEach(function(x){ if (getComputedStyle(x).display !== 'none') vis++; });
        var card=document.querySelector('.tfx-overlay .tfx-card');
        return {n:o.length, vis:vis, picks:document.querySelectorAll('.tfx-overlay .tfx-pick').length,
                apply:!!document.getElementById('tfx-apply'),
                txt:card?card.innerText.replace(/\\s+/g,' ').slice(0,400):''};})()`);
    const p0 = net.filter(n => /type-fix\/plan/.test(n.url)).length;
    for (let i = 0; i < 3; i++) { await clickAt('#diag-types-card button.primary'); await sleep(1400); }
    const p1 = net.filter(n => /type-fix\/plan/.test(n.url)).length;
    const ps = await planState();
    ok('the Types card opens the repair PLAN (GET /type-fix/plan)', p1 > p0, { before: p0, after: p1 });
    ok('…three presses never stack a second overlay', ps.n === 1 && ps.vis === 1, ps);
    ok('…the plan lists one row per value it would convert', ps.picks > 0, { picks: ps.picks });
    ok('…and shows what each value becomes before anything is written',
       /→|->|becomes|would/.test(ps.txt) || ps.picks > 0, ps.txt.slice(0, 200));
    ok('…and the apply button is there but unpressed', ps.apply, ps.apply);
    // The markup says "Convert <span>N</span> field(s)" with real spaces, but
    // .btn-sync is display:inline-flex, which makes each child an anonymous
    // flex item and DROPS the whitespace text nodes between them.
    const label = await ev(`(function(){var b=document.getElementById('tfx-apply');
        if(!b) return null; var r=b.getBoundingClientRect();
        return {text:b.textContent, rendered:b.innerText, disp:getComputedStyle(b).display};})()`);
    ok('the repair button reads as a sentence, not "Convert8field(s)"',
       label && / \d+ /.test(label.rendered), label);
    ok('…and NOTHING was applied by opening it',
       net.filter(n => /type-fix\/apply/.test(n.url)).length === 0);
    await snap('plan_open');
    // the checks dialog on top of it
    await ev(`document.getElementById('diag-checks-dialog').showModal(); 1`);
    await sleep(500);
    const both = await ev(`(function(){return {dlg:document.querySelectorAll('dialog[open]').length,
        ov:document.querySelectorAll('.tfx-overlay').length,
        ovVis:getComputedStyle(document.querySelector('.tfx-overlay')).display};})()`);
    ok('the checks dialog and the plan can be open at once without either vanishing',
       both.dlg === 1 && both.ov === 1 && both.ovVis !== 'none', both);
    await snap('plan_two_modals');
    await press('Escape'); await sleep(500);
    const after1 = await ev(`(function(){return {dlg:document.querySelectorAll('dialog[open]').length,
        ovVis:getComputedStyle(document.querySelector('.tfx-overlay')).display};})()`);
    await press('Escape'); await sleep(600);
    const after2 = await ev(`(function(){return {dlg:document.querySelectorAll('dialog[open]').length,
        ovVis:getComputedStyle(document.querySelector('.tfx-overlay')).display,
        picks:document.querySelectorAll('.tfx-overlay .tfx-pick').length};})()`);
    ok('Escape closes the top layer first, then the plan',
       after1.dlg === 0 && after2.ovVis === 'none', { afterFirst: after1, afterSecond: after2 });
    ok('…and closing the plan applied nothing',
       net.filter(n => /type-fix\/apply/.test(n.url)).length === 0);
    // reopen and dismiss by the backdrop
    await clickAt('#diag-types-card button.primary'); await sleep(1400);
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: 6, y: 500, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: 6, y: 500, button: 'left', clickCount: 1 });
    await sleep(700);
    const ps2 = await planState();
    ok('a click on the backdrop closes the plan too', ps2.vis === 0, ps2);
    ok('…and still applied nothing', net.filter(n => /type-fix\/apply/.test(n.url)).length === 0);
    fs.writeFileSync(OUT, JSON.stringify({ phase: PHASE, results, errors, dialogs, net: net.length, shots }, null, 1));
    report(); return;
  }

  if (PHASE === 'archive') {
    // The honest route to a read-only context: the dataset run's own
    // "Open read-only" button (templates/_dataset_detail.html).
    await send('Page.navigate', { url: BASE + '/dataset/820bdf2f:1' });
    await sleep(4000);
    await ev(`(function(){var t=document.querySelector('[onclick*="state"], .ds-tab, [data-tab]'); return 1;})()`);
    // the State tab, then the button
    await ev(`(function(){var els=document.querySelectorAll('.ds-tab, .tree-file-tab, [onclick*="switchDatasetTab"]');
        for (var e of els) if (/state/i.test(e.textContent)) { e.click(); return e.textContent; } return null;})()`);
    await sleep(1500);
    const hasBtn = await ev(`!!document.querySelector('.ds-load-archive-btn')`);
    ok('the run detail offers "Open read-only"', hasBtn);
    if (hasBtn) {
      await clickAt('.ds-load-archive-btn');
      await sleep(4000);
    }
    await send('Page.navigate', { url: BASE + '/diagnostics' });
    await sleep(4000);
    await dismissAlert();
    const a = await ev(`(function(){
        var c=document.getElementById('diag-types-card');
        return {card:!!c, text:c?c.innerText:'',
                autocorrect: !!document.querySelector('#diag-types-card button.primary'),
                rows: document.querySelectorAll('tr.diag-row').length,
                fixBtns: document.querySelectorAll('.diag-fix').length};})()`);
    ok('an archive still renders the whole findings list', a && a.rows > 0, a && a.rows);
    ok('…and the Types card offers NO repair on a read-only archive',
       a && !a.autocorrect && /read-only archive/i.test(a.text), a);
    const plan = await ev(`(async function(){var r=await fetch('/type-fix/plan'); var t=await r.text();
        return {status:r.status, text:t.replace(/<[^>]+>/g,' ').replace(/\\s+/g,' ').trim().slice(0,220)};})()`);
    ok('…and GET /type-fix/plan refuses with 409, saying why', plan && plan.status === 409, plan);
    ok('…and an archive is never offered a one-click state fix',
       a && a.fixBtns === 0, a && a.fixBtns);
    await snap('archive');
    fs.writeFileSync(OUT, JSON.stringify({ phase: PHASE, results, errors, dialogs, net: net.length, shots }, null, 1));
    report(); return;
  }

  /* ===================== PHASE main ================================= */
  await send('Page.navigate', { url: BASE + '/diagnostics' });
  await sleep(5500);

  /* ── 0. the self-raising type alert (docs/78) ─────────────────────── */
  // Open a FRESH copy of the chip the way the app does, so the one-shot alert
  // flag is armed by THIS browser's own chip-open and not by something else.
  if (CHIP) {
    const loaded = await ev(`(async function(){
        var b=new URLSearchParams(); b.append('folder', ${JSON.stringify(CHIP)});
        var r=await fetch('/load',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
                                   body:b.toString(), redirect:'follow'});
        return r.status;})()`);
    ok('the chip loads from the page like a person picking a folder',
       loaded === 200 || loaded === 302, loaded);
    await send('Page.navigate', { url: BASE + '/diagnostics' });
    await sleep(7000);
  }
  const applyBefore0 = net.filter(n => /type-fix\/apply/.test(n.url)).length;
  await snap('00_on_open');
  const raised = await dismissAlert();
  ok('on chip open the type alert raises ITSELF over Diagnostics', !!raised,
     raised && raised.txt.slice(0, 160));
  ok('…and Cancel closes it having written nothing',
     (await alertState()) === null
     && net.filter(n => /type-fix\/apply/.test(n.url)).length === applyBefore0);

  /* ── 1. it rendered, and it rendered the real chip ─────────────────── */
  const base = await ev(`(function(){
      var rows=document.querySelectorAll('tr.diag-row');
      var buckets={};
      rows.forEach(function(r){var b=r.getAttribute('data-bucket'); buckets[b]=(buckets[b]||0)+1;});
      return {h2:(document.querySelector('#table-pane h2, h2')||{}).textContent.trim().slice(0,40),
              bar:!!document.getElementById('diag-filter-bar'),
              findings:!!document.getElementById('diag-findings'),
              types:!!document.getElementById('diag-types-card'),
              env:!!document.getElementById('diag-env-card'),
              whatis:!!document.querySelector('.diag-whatis-badge'),
              pills:Array.prototype.map.call(document.querySelectorAll('.diag-pill'),function(p){return p.getAttribute('data-bucket');}),
              domains:Array.prototype.map.call(document.querySelectorAll('details.diag-domain'),function(d){return d.getAttribute('data-domain');}),
              openDomains:Array.prototype.filter.call(document.querySelectorAll('details.diag-domain'),function(d){return d.open;}).map(function(d){return d.getAttribute('data-domain');}),
              rows:rows.length, buckets:buckets,
              goto:document.querySelectorAll('.diag-goto').length,
              fix:document.querySelectorAll('.diag-fix').length,
              ack:document.querySelectorAll('.diag-ack').length};})()`);
  ok('the Diagnostics page renders on a real 20-qubit chip',
     base && base.bar && base.findings && /Diagnostics/.test(base.h2), base);
  ok('…with every severity bucket present as a pill',
     base && ['error', 'warning', 'advisory', 'info'].every(b => base.pills.indexOf(b) >= 0), base.pills);
  ok('…and a domain section per finding family', base && base.domains.length >= 4, base.domains);
  ok('…error/warning domains start OPEN, advisory/info-only start collapsed',
     base && base.openDomains.indexOf('values') >= 0 && base.openDomains.indexOf('references') >= 0,
     { open: base.openDomains, all: base.domains });
  ok('…and the Types & values card and the Environment card are both on the page',
     base && base.types && base.env, { types: base.types, env: base.env });
  const serverRows = await ev(`(async function(){var t=await (await fetch('/diagnostics')).text();
      return (t.match(/class="diag-row/g)||[]).length;})()`);
  ok('the DOM row count equals what the server rendered',
     serverRows === base.rows, { dom: base.rows, server: serverRows });
  await snap('01_loaded');

  /* ── 2. who keeps talking? 25 s of sitting still ───────────────────── */
  const idleStart = Date.now();
  await sleep(25000);
  const idle = net.filter(n => n.t >= idleStart).map(n => n.url.replace(BASE, ''));
  const idleCount = {};
  idle.forEach(u => { const k = u.split('?')[0]; idleCount[k] = (idleCount[k] || 0) + 1; });
  const diagIdle = Object.keys(idleCount).filter(k => /^\/diagnostics|^\/type-/.test(k));
  ok('the Diagnostics page itself starts no poller — it sits still when nothing happens',
     diagIdle.every(k => idleCount[k] <= 1), { diagnostics: diagIdle.map(k => k + '=' + idleCount[k]),
                                               appWide: idleCount });
  ok('…the env card is NOT still self-polling after the probe finished',
     !(idleCount['/diagnostics/env-card'] > 1), idleCount['/diagnostics/env-card'] || 0);
  const totalReq = net.length;

  /* ── 3. "What is checked?" — clicked three times, Escape, again ────── */
  const dlg = () => ev(`(function(){var d=document.getElementById('diag-checks-dialog');
      return d? {open:d.open, items:d.querySelectorAll('.diag-checks-item').length,
                 groups:d.querySelectorAll('.diag-checks-group').length} : null;})()`);
  await clickAt('.diag-whatis-badge');
  await sleep(500);
  let d1 = await dlg();
  ok('the "What is checked?" dialog opens', d1 && d1.open, d1);
  ok('…and lists every check in the catalogue', d1 && d1.items >= 30, d1);
  // the raw call the button makes, with the dialog provably still open
  const reopen = await evSafe(`(function(){var d=document.getElementById('diag-checks-dialog');
      if(!d.open) return 'dialog was not open'; d.showModal(); return 'no-throw';})()`);
  ok('showModal on an already-open dialog is harmless (spec: a no-op; older engines threw)',
     reopen && reopen.v && (reopen.v.e === 1 || reopen.v.v === 'no-throw'), reopen && reopen.v);
  const errsBefore = errors.length;
  await ev(`(function(){var d=document.getElementById('diag-checks-dialog'); if(!d.open) d.showModal(); return 1;})()`);
  await clickAt('.diag-whatis-badge');      // covered by the backdrop — a person cannot reach it
  await sleep(250);
  const afterBackdropClick = await dlg();
  await clickAt('.diag-whatis-badge');
  await sleep(400);
  ok('clicking where the badge is while the modal is open throws nothing',
     errors.length === errsBefore, errors.slice(errsBefore));
  ok('…and never stacks a second dialog',
     (await ev(`document.querySelectorAll('#diag-checks-dialog').length`)) === 1,
     { afterFirstClick: afterBackdropClick });
  await snap('02_whatis_dialog');
  await press('Escape'); await sleep(400);
  ok('Escape closes the dialog', !(await dlg()).open);
  await clickAt('.diag-whatis-badge'); await sleep(400);
  ok('…and it reopens afterwards', (await dlg()).open);
  await ev(`document.getElementById('diag-checks-dialog').close(); 1`);
  await sleep(300);
  ok('closing twice in a row is harmless',
     (await evSafe(`(function(){document.getElementById('diag-checks-dialog').close(); return 'ok';})()`)).v.e === 0);

  /* ── 4. the severity pills, clicked to death ───────────────────────── */
  const filterState = () => ev(`(function(){
      var vis=0, hid=0, doms=0, hidDoms=0;
      document.querySelectorAll('tr.diag-row').forEach(function(r){
        if (r.style.display === 'none') hid++; else vis++;});
      document.querySelectorAll('details.diag-domain').forEach(function(s){
        doms++; if (s.style.display === 'none') hidDoms++;});
      var pills={};
      document.querySelectorAll('.diag-pill').forEach(function(p){
        pills[p.getAttribute('data-bucket')] = {off:p.classList.contains('diag-pill-off'),
                                                pressed:p.getAttribute('aria-pressed')};});
      var c=document.querySelector('.diag-shown-count');
      return {vis:vis, hid:hid, doms:doms, hidDoms:hidDoms, pills:pills, count:c?c.textContent:null,
              ls:localStorage.getItem('quam_diag_filter')};})()`);
  const f0 = await filterState();
  ok('with no filter every row is visible and the counter is silent',
     f0.hid === 0 && f0.count === '', f0);

  await clickAt('.diag-pill[data-bucket="warning"]');
  await sleep(300);
  const f1 = await filterState();
  ok('turning warnings off hides exactly the warning rows',
     f1.hid === base.buckets.warning && f1.pills.warning.off === true, { got: f1.hid, expected: base.buckets.warning, f1 });
  ok('…the pill says so to a screen reader too', f1.pills.warning.pressed === 'false', f1.pills);
  ok('…the counter names how many are shown',
     /^\d+ of \d+ shown$/.test(f1.count || ''), f1.count);
  ok('…and a domain left with nothing visible is hidden', f1.hidDoms > 0, f1);

  // the same pill three more times (four presses in all — an even number of
  // toggles must land back where it started, with nothing half-applied)
  for (let i = 0; i < 3; i++) { await clickAt('.diag-pill[data-bucket="warning"]'); await sleep(220); }
  const f2 = await filterState();
  ok('four presses on one pill land back ON with every row shown again',
     f2.pills.warning.off === false && f2.hid === 0 && f2.count === '', f2);

  // every pill off — press only the ones currently ON
  for (const b of ['error', 'warning', 'advisory', 'info']) {
    const isOn = await ev(`(function(){var p=document.querySelector('.diag-pill[data-bucket="${b}"]');
        return p && !p.classList.contains('diag-pill-off');})()`);
    if (isOn) { await clickAt(`.diag-pill[data-bucket="${b}"]`); await sleep(200); }
  }
  const f3 = await filterState();
  ok('every severity off hides every row and every domain',
     f3.vis === 0 && f3.hidDoms === f3.doms, f3);
  ok('…and says "0 of N shown" rather than pretending the chip is clean',
     /^0 of \d+ shown$/.test(f3.count || ''), f3.count);
  await snap('03_all_filtered_off');

  // survives a FULL page load (the _diagInitOnLoad path, not just the swap path)
  await send('Page.navigate', { url: BASE + '/diagnostics' });
  await sleep(4500);
  await dismissAlert();
  const f4 = await filterState();
  ok('the filter choice survives a full page reload',
     f4.vis === 0 && /^0 of \d+ shown$/.test(f4.count || ''), f4);

  // keyboard: focus a pill, Space and Enter
  await ev(`document.querySelector('.diag-pill[data-bucket="error"]').focus(); 1`);
  await press('Space'); await sleep(300);
  const f5 = await filterState();
  ok('Space on a focused pill toggles it (it is a real button)',
     f5.pills.error.off === false, f5.pills);
  await press('Enter'); await sleep(300);
  const f6 = await filterState();
  ok('Enter on a focused pill toggles it back', f6.pills.error.off === true, f6.pills);

  // corrupt the persisted value — a hostile localStorage
  await ev(`localStorage.setItem('quam_diag_filter','{not json'); 1`);
  await send('Page.navigate', { url: BASE + '/diagnostics' });
  await sleep(4500);
  await dismissAlert();
  const f7 = await filterState();
  ok('a corrupted saved filter degrades to "show everything", never a blank page',
     f7.vis === base.rows && f7.hid === 0, f7);

  await ev(`try{localStorage.removeItem('quam_diag_filter')}catch(e){}; 1`);
  await send('Page.navigate', { url: BASE + '/diagnostics' });
  await sleep(4500);
  await dismissAlert();
  const fReset = await filterState();
  ok('clearing the saved filter brings every row back',
     fReset.hid === 0 && fReset.vis > 0 && fReset.count === '', fReset);

  /* ── 5. domain sections ────────────────────────────────────────────── */
  const domState = () => ev(`(function(){var o={};
      document.querySelectorAll('details.diag-domain').forEach(function(d){o[d.getAttribute('data-domain')]=d.open;});
      return o;})()`);
  const dom0 = await domState();
  for (let i = 0; i < 3; i++) { await clickAt('details.diag-domain[data-domain="values"] > summary'); await sleep(250); }
  const dom1 = await domState();
  ok('three clicks on a domain header land on the opposite of where it started',
     dom1.values === !dom0.values, { before: dom0, after: dom1 });
  // open every domain, then fold one and force a self-refresh
  await ev(`document.querySelectorAll('details.diag-domain').forEach(function(d){d.open=true;}); 1`);
  await clickAt('details.diag-domain[data-domain="physics"] > summary');
  await sleep(300);
  const foldedBefore = await domState();
  ok('a domain the user folded is folded', foldedBefore.physics === false, foldedBefore);

  /* ── 6. the list must re-fetch itself (docs/141 §4f) ───────────────── */
  // (a) the GENUINE in-page action: acknowledging an environment finding.
  const ackBefore = await ev(`(function(){
      return {ack:document.querySelectorAll('.diag-ack').length,
              acked:document.querySelectorAll('tr.diag-row-acknowledged').length,
              rows:document.querySelectorAll('tr.diag-row').length,
              envBadge:(document.querySelector('details.diag-domain[data-domain="env"] .diag-domain-badges')||{}).textContent||''};})()`);
  let refetches = 0;
  const markBefore = net.length;
  if (ackBefore.ack > 0) {
    await clickAt('.diag-ack');
    const grew = await until(`document.querySelectorAll('tr.diag-row-acknowledged').length > ${ackBefore.acked}`, 9000);
    const ackAfter = await ev(`(function(){
        return {acked:document.querySelectorAll('tr.diag-row-acknowledged').length,
                rows:document.querySelectorAll('tr.diag-row').length,
                pillW:(document.querySelector('.diag-pill[data-bucket="warning"]')||{}).textContent||'',
                mini:!!document.querySelector('.diag-acknowledged'),
                domains:Array.prototype.map.call(document.querySelectorAll('details.diag-domain'),function(d){return d.getAttribute('data-domain');})};})()`);
    refetches = net.slice(markBefore).filter(n => /\/diagnostics(\?|$)/.test(n.url.replace(BASE, ''))).length;
    ok('acknowledging an env finding re-fetches the findings list by itself (no F5)',
       !!grew && refetches >= 1, { grew: !!grew, refetches: refetches, ackAfter: ackAfter });
    ok('…and the acknowledged row stays LISTED, just not counted',
       ackAfter.acked >= 1 && ackAfter.rows === ackBefore.rows, { before: ackBefore, after: ackAfter });
    const foldedAfter = await domState();
    ok('…and the fold the user chose survives the self-refresh (docs/141 §4l)',
       foldedAfter.physics === false, { before: foldedBefore, after: foldedAfter });
    const f8 = await filterState();
    ok('…and the persisted severity filter is re-applied to the fresh rows',
       f8.count !== null, f8);
    // an acknowledged row is COLLAPSED behind its own "N acknowledged — show"
    const collapsed = await ev(`(function(){var b=document.querySelector('.diag-acked-btn');
        var r=document.querySelector('tr.diag-row-acknowledged');
        return {btn:!!b, label:b?b.textContent.trim():'', hidden:!!(r&&r.classList.contains('diag-row-collapsed'))};})()`);
    ok('an acknowledged finding is listed but folded away behind its own toggle',
       collapsed.btn && collapsed.hidden && /acknowledged/.test(collapsed.label), collapsed);
    await ev(`(function(){var d=document.querySelector('details.diag-domain[data-domain="env"]'); if(d) d.open=true; return 1;})()`);
    await clickAt('.diag-acked-btn');
    await sleep(500);
    const shown = await ev(`!document.querySelector('tr.diag-row-acknowledged').classList.contains('diag-row-collapsed')`);
    ok('…and the toggle reveals it', shown === true, shown);
    // put it back
    const revokeN0 = net.filter(n => /env-ack/.test(n.url)).length;
    await clickAt('.diag-ack-revoke');
    await until(`document.querySelectorAll('tr.diag-row-acknowledged').length === 0`, 12000);
    ok('…and Un-acknowledge puts it back',
       (await ev(`document.querySelectorAll('tr.diag-row-acknowledged').length`)) === 0,
       { revokeRequests: net.filter(n => /env-ack/.test(n.url)).length - revokeN0 });
  } else {
    ok('an env finding with an acknowledge button exists to test the refresh', false, ackBefore);
  }

  // (b) a REAL state edit staged elsewhere, announced by the app's own
  //     announcer (window._diagChanged — every mutation path calls it).
  const beforeEdit = await ev(`(function(){var t=document.body.innerText;
      return {tof: /multiple of 4/.test(t), rows:document.querySelectorAll('tr.diag-row').length};})()`);
  const edited = await ev(`(async function(){
      var b=new URLSearchParams();
      b.append('dot_path','qubits.q4.resonator.time_of_flight'); b.append('value','252');
      var r=await fetch('/field/edit',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:b.toString()});
      var j=await r.json(); window._diagChanged && window._diagChanged(); return {status:r.status, ok:j.ok, err:j.error||null};})()`);
  ok('a real edit to an offending value is accepted by the server', edited && edited.ok, edited);
  const gone = await until(`!/multiple of 4/.test(document.body.innerText)`, 12000);
  ok('…and the finding leaves the list on its own, with no page reload (docs/141 §4f)',
     !!gone, { before: beforeEdit, afterText: await ev(`/multiple of 4/.test(document.body.innerText)`) });
  await snap('04_after_selfrefresh');

  // (c) stateRestored: the template listens `from:body`. The app fires this
  //     event from two places — an htmx HX-Trigger (dispatched on the
  //     requesting element, so it bubbles THROUGH body) and one bare
  //     document.dispatchEvent (app.js, the structural-undo path). Only one of
  //     those can reach a body listener; measure which.
  const diagGets = () => net.filter(n => /\/diagnostics(\?|$)/.test(n.url.replace(BASE, ''))).length;
  let g0 = diagGets();
  await ev(`document.body.dispatchEvent(new CustomEvent('stateRestored',{bubbles:true})); 1`);
  await sleep(2600);
  const gBody = diagGets() - g0;
  ok('stateRestored dispatched ON BODY re-fetches the findings list', gBody >= 1, { refetches: gBody });
  g0 = diagGets();
  await ev(`document.dispatchEvent(new CustomEvent('stateRestored',{bubbles:true})); 1`);
  await sleep(2600);
  const gDoc = diagGets() - g0;
  ok('stateRestored dispatched on DOCUMENT also reaches the list', gDoc >= 1, { refetches: gDoc });
  g0 = diagGets();
  await ev(`window.htmx && htmx.trigger(document.body,'diagnostics-changed'); 1`);
  await sleep(2600);
  ok('the app\'s own announcer (htmx.trigger on body) re-fetches the list',
     diagGets() - g0 >= 1, { refetches: diagGets() - g0 });

  /* ── 7. Types & values card + the repair PLAN (opened, never applied) ─ */
  const tcard = await ev(`(function(){var c=document.getElementById('diag-types-card');
      return c? {text:c.innerText.replace(/\\s+/g,' ').slice(0,300),
                 primary:!!c.querySelector('button.primary'),
                 label:(c.querySelector('button.primary')||{}).textContent||''} : null;})()`);
  ok('the Types & values card names the stored-as-text values', tcard && /stored as text/i.test(tcard.text), tcard);
  ok('…and offers the repair as an explicit, countable action',
     tcard && tcard.primary && /Auto-correct \d+ value/.test(tcard.label), tcard);

  const planState = () => ev(`(function(){
      var m=[]; document.querySelectorAll('.tfx-overlay').forEach(function(x){
        if (getComputedStyle(x).display !== 'none') m.push(x); });
      var rows=document.querySelectorAll('.tfx-overlay .tfx-pick');
      var txt=''; m.forEach(function(x){txt+=x.innerText||'';});
      return {n:m.length, rows:rows.length, txt:txt.replace(/\\s+/g,' ').slice(0,300)};})()`);
  const planReq0 = net.filter(n => /type-fix\/plan/.test(n.url)).length;
  for (let i = 0; i < 3; i++) { await clickAt('#diag-types-card button.primary'); await sleep(900); }
  const planReq1 = net.filter(n => /type-fix\/plan/.test(n.url)).length;
  const ps = await planState();
  ok('the repair PLAN opens (GET /type-fix/plan)', planReq1 > planReq0, { before: planReq0, after: planReq1 });
  ok('…opening it three times never stacks three overlays', ps.n <= 1, ps);
  ok('…and the plan shows what each value would become, before anything is written',
     /would|becomes|→|->|convert/i.test(ps.txt) || ps.rows > 0, ps);
  const applyCalls = net.filter(n => /type-fix\/apply/.test(n.url)).length;
  ok('…and nothing was applied by merely opening it', applyCalls === 0, applyCalls);
  await snap('05_type_fix_plan');
  await ev(`window.closeTypeFixPlan && window.closeTypeFixPlan(); 1`); await sleep(600);
  const psClosed = await planState();
  ok('the plan closes without applying it',
     psClosed.n === 0 && net.filter(n => /type-fix\/apply/.test(n.url)).length === 0, psClosed);

  /* ── 8. a finding's own action: follow it, and come back ───────────── */
  const jumpPath = await ev(`(document.querySelector('.diag-goto')||{getAttribute:function(){return null;}}).getAttribute('data-jump-path')`);
  await clickAt('.diag-goto');
  await sleep(4000);
  const landed = await ev(`(function(){return {url:location.pathname+location.search,
      explorer:!!document.getElementById('explorer-tree-state'),
      hi:document.querySelectorAll('.explorer-row.highlight, .tree-row-highlight, [data-path][class*="highlight"]').length};})()`);
  ok('"Go to field" follows the finding into the Json Tree View',
     landed && (landed.explorer || /explorer/.test(landed.url)), { jumpPath, landed });
  await send('Page.navigate', { url: BASE + '/diagnostics' });
  await sleep(4500);
  await dismissAlert();
  const back = await ev(`(function(){return {rows:document.querySelectorAll('tr.diag-row').length,
      bar:!!document.getElementById('diag-filter-bar'), count:(document.querySelector('.diag-shown-count')||{}).textContent};})()`);
  ok('…and coming back leaves Diagnostics whole', back && back.bar && back.rows > 0, back);

  // the one-click fix, with the confirm ANSWERED NO (answered over CDP — a
  // native dialog, exactly what a person sees)
  dialogAnswer = false;
  const dlg0 = dialogs.length;
  const fixCallsBefore = net.filter(n => /apply-fix/.test(n.url)).length;
  const hasWarnFix = await ev(`!!document.querySelector('.diag-fix[data-confirm="1"]')`);
  if (hasWarnFix) {
    await clickAt('.diag-fix[data-confirm="1"]');
    await sleep(1200);
    const cl = dialogs.slice(dlg0);
    ok('a value-CHANGING one-click fix asks first', cl.length === 1 && /will change/i.test(cl[0].message || ''), cl);
    ok('…and answering no sends nothing',
       net.filter(n => /apply-fix/.test(n.url)).length === fixCallsBefore,
       net.filter(n => /apply-fix/.test(n.url)).length);
    ok('…and the button is left usable, not stuck on "Applying…"',
       await ev(`(function(){var b=document.querySelector('.diag-fix[data-confirm="1"]'); return b && !b.disabled && !/Applying/.test(b.textContent);})()`),
       await ev(`(document.querySelector('.diag-fix[data-confirm="1"]')||{}).textContent`));
  } else {
    ok('a confirming one-click fix exists to test', false, null);
  }
  // the info-severity fix (no confirm) clicked three times fast — the guard is
  // the button disabling itself; a second POST would be a double write.
  const infoFix = await ev(`!!document.querySelector('.diag-fix:not([data-confirm])')`);
  if (infoFix) {
    const n0 = net.filter(n => /apply-fix/.test(n.url)).length;
    await clickAt('.diag-fix:not([data-confirm])');
    await clickAt('.diag-fix:not([data-confirm])');
    await clickAt('.diag-fix:not([data-confirm])');
    await sleep(3500);
    const n1 = net.filter(n => /apply-fix/.test(n.url)).length;
    ok('three fast clicks on a one-click fix send ONE write, not three', n1 - n0 === 1, { sent: n1 - n0 });
    await sleep(2500);
    ok('…and the page re-renders itself afterwards',
       await ev(`!!document.getElementById('diag-filter-bar')`));
  }
  await snap('06_after_fix');

  /* ── 9. query parameters / deep links ──────────────────────────────── */
  const qp = await ev(`(async function(){var o={};
      for (var q of ['?domain=values','?severity=error','?x=%27%22%3E','?bucket=../../etc','?domain='+encodeURIComponent('가나다😀'),'?'+('a'.repeat(300))+'=1']) {
        try { var r=await fetch('/diagnostics'+q); o[q]=r.status; } catch(e){ o[q]='ERR'; } }
      return o;})()`);
  ok('unknown / hostile query parameters are ignored, never 500',
     Object.values(qp).every(s => s === 200), qp);
  await send('Page.navigate', { url: BASE + '/diagnostics?domain=values&severity=error' });
  await sleep(4000);
  await dismissAlert();
  const deep = await ev(`(function(){return {rows:document.querySelectorAll('tr.diag-row').length,
      visible:Array.prototype.filter.call(document.querySelectorAll('tr.diag-row'),function(r){return r.style.display!=='none';}).length};})()`);
  ok('…and /diagnostics takes no deep-link parameters at all (documented, not a crash)',
     deep && deep.rows > 0, deep);

  /* ── 10. the window, made hostile ──────────────────────────────────── */
  await send('Emulation.setDeviceMetricsOverride', { width: 400, height: 700, deviceScaleFactor: 1, mobile: false });
  await sleep(1200);
  const narrow = await ev(`(function(){return {ow:document.documentElement.scrollWidth, cw:document.documentElement.clientWidth,
      rows:document.querySelectorAll('tr.diag-row').length};})()`);
  ok('at 400px wide the page does not scroll the BODY sideways',
     narrow.ow <= narrow.cw + 2, narrow);
  await snap('07_narrow');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await sleep(900);
  // scroll to the far end and back
  await ev(`(function(){var p=document.getElementById('table-pane')||document.scrollingElement;
      p.scrollTop = p.scrollHeight; return p.scrollTop;})()`);
  await sleep(600);
  await press('End'); await sleep(300);
  const far = await ev(`(function(){var p=document.getElementById('table-pane')||document.scrollingElement;
      return {top:p.scrollTop, h:p.scrollHeight, rows:document.querySelectorAll('tr.diag-row').length};})()`);
  await ev(`(function(){var p=document.getElementById('table-pane')||document.scrollingElement; p.scrollTop=0; return 1;})()`);
  await sleep(500);
  ok('scrolling to the far end and back leaves every row in place',
     far.rows === (await ev(`document.querySelectorAll('tr.diag-row').length`)), far);

  /* ── 11. two things at once ────────────────────────────────────────── */
  const race = await ev(`(async function(){
      var out=[];
      var ps=[];
      for (var i=0;i<6;i++) ps.push(fetch('/diagnostics').then(function(r){return r.status;}));
      ps.push(fetch('/diagnostics/types-card').then(function(r){return r.status;}));
      ps.push(fetch('/diagnostics/env-card').then(function(r){return r.status;}));
      ps.push(fetch('/diagnostics/findings.json').then(function(r){return r.status;}));
      ps.push(fetch('/diagnostics/summary').then(function(r){return r.status;}));
      ps.push(fetch('/diagnostics/banner').then(function(r){return r.status;}));
      out = await Promise.all(ps);
      return out;})()`);
  ok('ten diagnostics requests fired at once all answer 2xx',
     race.every(s => s >= 200 && s < 300), race);
  // the dialog open AND the plan open at the same time
  await clickAt('.diag-whatis-badge'); await sleep(500);
  await ev(`window.openTypeFixPlan && window.openTypeFixPlan(); 1`);
  await sleep(1500);
  const both = await ev(`(function(){return {dialogs:document.querySelectorAll('dialog[open]').length,
      overlays:document.querySelectorAll('.modal-overlay, #sm-modal, #type-fix-modal').length,
      bodyScroll:getComputedStyle(document.body).overflow};})()`);
  ok('the checks dialog and the repair plan open together without a stuck page',
     both && both.dialogs <= 2, both);
  await snap('08_two_modals');
  await press('Escape'); await sleep(400); await press('Escape'); await sleep(400);
  const cleared = await ev(`(function(){return {dialogs:document.querySelectorAll('dialog[open]').length,
      overlays:document.querySelectorAll('#type-fix-modal, .modal-overlay').length};})()`);
  ok('two Escapes close both', cleared.dialogs === 0 && cleared.overlays === 0, cleared);

  /* ── 12. the env card, probed repeatedly ───────────────────────────── */
  const probeN0 = net.filter(n => /env-probe/.test(n.url)).length;
  await send('Page.navigate', { url: BASE + '/diagnostics' });
  await sleep(4500);
  await dismissAlert();
  const probeBtn = await ev(`(function(){var b=Array.prototype.find.call(
      document.querySelectorAll('#diag-env-card button'), function(x){return /probe environment/i.test(x.textContent);});
      if(b) b.setAttribute('data-stress','probe'); return !!b;})()`);
  if (probeBtn) {
    await clickAt('#diag-env-card button[data-stress="probe"]');
    await sleep(250);
    const disabled = await ev(`(function(){var b=document.querySelector('#diag-env-card button[data-stress="probe"]');
        return b ? b.disabled : 'card already re-rendered';})()`);
    ok('the Probe button cannot be pressed twice while it is running',
       disabled === true || disabled === 'card already re-rendered', disabled);
    await clickAt('#diag-env-card button[data-stress="probe"]');
    await clickAt('#diag-env-card button[data-stress="probe"]');
    await sleep(1500);
    const probeN1 = net.filter(n => /env-probe/.test(n.url)).length;
    ok('three clicks on Probe send one probe, not three', probeN1 - probeN0 === 1, { sent: probeN1 - probeN0 });
    const settled = await until(`!/probing the environment/.test((document.getElementById('diag-env-card')||{innerText:''}).innerText)`, 60000);
    ok('…and the card settles out of "probing" rather than spinning for ever', !!settled,
       await ev(`(document.getElementById('diag-env-card')||{innerText:''}).innerText.replace(/\\s+/g,' ').slice(0,160)`));
    await sleep(6000);
    const pollAfter = net.filter(n => /env-card/.test(n.url) && n.t > Date.now() - 5500).length;
    ok('…and the 2 s self-poll stops when the probe is done', pollAfter <= 1, pollAfter);
  } else {
    ok('the env card offers a probe button', false, null);
  }
  // "Validate deeply (Quam.load)" — on a chip carrying a NaN and a dangling
  // pointer this MUST fail. The question is whether the user is told.
  await ev(`window.__toasts=[]; (function(){var o=window.showToast;
      window.showToast=function(m,l){window.__toasts.push(String(m)); if(o) return o.apply(this,arguments);};})(); 1`);
  const deepN0 = net.filter(n => /config\/regenerate/.test(n.url)).length;
  const hasDeep = await ev(`(function(){var b=Array.prototype.find.call(
      document.querySelectorAll('#diag-env-card button'), function(x){return /validate deeply/i.test(x.textContent);});
      if(b) b.setAttribute('data-stress','deep'); return !!b;})()`);
  if (hasDeep) {
    await clickAt('#diag-env-card button[data-stress="deep"]');
    await until(`(window.__toasts||[]).length > 0 || !!(document.getElementById('diag-env-deep')||{}).innerHTML`, 45000);
    const deep = await ev(`(function(){return {toasts:window.__toasts||[],
        host:(document.getElementById('diag-env-deep')||{}).innerHTML||'',
        busy:(document.getElementById('diag-env-deep-busy')||{}).className||''};})()`);
    ok('"Validate deeply" reaches the server',
       net.filter(n => /config\/regenerate/.test(n.url)).length > deepN0);
    ok('…and a FAILING deep validation tells the user something (not only a console error)',
       (deep.toasts.length > 0) || deep.host.trim().length > 0,
       { toasts: deep.toasts, hostLen: deep.host.length });
    ok('…and the busy indicator is not left spinning',
       !/htmx-request/.test(deep.busy), deep.busy);
  }
  await snap('09_env_card');

  /* ── 13. still consistent? ─────────────────────────────────────────── */
  await send('Page.navigate', { url: BASE + '/diagnostics' });
  await sleep(4500);
  await dismissAlert();
  const end = await ev(`(function(){
      var rows=document.querySelectorAll('tr.diag-row');
      var b={};   // docs/168: an acknowledged row is LISTED but never COUNTED
      rows.forEach(function(r){ if (r.classList.contains('diag-row-acknowledged')) return;
                                var k=r.getAttribute('data-bucket'); b[k]=(b[k]||0)+1;});
      var pillN={};
      document.querySelectorAll('.diag-pill').forEach(function(p){
        var m=(p.textContent||'').match(/(\\d+)/); pillN[p.getAttribute('data-bucket')]=m?+m[1]:null;});
      return {rows:rows.length, buckets:b, pillN:pillN,
              badge:(document.querySelector('.diag-header-badge')||{}).textContent||'',
              dupIds:(function(){var ids={},d=[];document.querySelectorAll('[id]').forEach(function(e){
                 if(ids[e.id]) d.push(e.id); ids[e.id]=1;}); return d;})()};})()`);
  ok('every pill count equals the number of rows in that bucket',
     Object.keys(end.pillN).every(k => end.pillN[k] === (end.buckets[k] || 0)),
     { pills: end.pillN, rows: end.buckets });
  ok('the page has no duplicate element ids after all of that', end.dupIds.length === 0, end.dupIds);
  const jsonFeed = await ev(`(async function(){var j=await (await fetch('/diagnostics/findings.json')).json();
      return {vs:j.value_spec.length, conn:j.connectivity.length, counts:j.counts};})()`);
  ok('the JSON feed agrees with the rendered page', jsonFeed && jsonFeed.counts
     && jsonFeed.counts.value_spec === jsonFeed.vs, jsonFeed);
  await snap('10_final');

  fs.writeFileSync(OUT, JSON.stringify({
    phase: PHASE, results, errors, shots, dialogs,
    net: { total: net.length, idle: idleCount, byUrl: (function () {
      const o = {}; net.forEach(n => { const k = n.url.replace(BASE, '').split('?')[0]; o[k] = (o[k] || 0) + 1; }); return o; })() },
  }, null, 1));
  report();

  function report() {
    const bad = results.filter(x => !x.pass);
    const csp = errors.filter(e => /unsafe-eval/.test(String(e.text)));
    console.log('phase ' + PHASE + '  checks: ' + (results.length - bad.length) + '/' + results.length
                + '   errors: ' + errors.length + ' (htmx CSP: ' + csp.length + ')  requests: ' + net.length
                + '  native dialogs: ' + dialogs.length);
    bad.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 400)));
    errors.filter(e => !/unsafe-eval/.test(String(e.text)))
          .slice(0, 20).forEach(e => console.log('  ERR  ' + e.kind + ': ' + String(e.text).slice(0, 300)));
    process.exit(0);
  }
}
main().catch(e => {
  console.error('driver error: ' + (e && e.stack || e));
  fs.writeFileSync(OUT, JSON.stringify({ results, errors, driver: String(e && e.stack || e) }, null, 1));
  process.exit(1);
});
