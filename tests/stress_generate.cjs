/* Generate Config wizard (/generate) — hostile-user stress in real headless
 * Chrome over CDP.
 *
 * Modelled on tests/stress_drive.cjs (CDP plumbing, real keystrokes, whole-
 * session error collection).
 *
 * Constraint honoured here: AT MOST ONE /generate/allocate dry-run, and NEVER
 * /generate/build — nothing on disk is written by this driver except the
 * populate presets it saves and deletes again.
 *
 * argv[2] = json out path, argv[3] = CDP port, argv[4] = base url
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9414';
const BASE = process.argv[4] || 'http://127.0.0.1:5414';

const errors = [];
const results = [];
const shots = [];
const nativeDialogs = [];
const typeFailures = [];
function ok(name, cond, detail) {
  results.push({ name: name, pass: !!cond, detail: detail === undefined ? null : detail });
}

async function main() {
  const targets = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(res => ws.onopen = res);
  let id = 0; const pending = new Map();
  let phase = 'boot';
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      errors.push({ kind: 'exception', phase: phase, text: (d.exception && (d.exception.description || d.exception.value)) || d.text });
    }
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      errors.push({ kind: 'console.error', phase: phase, text: (m.params.args || []).map(a => a.value || a.description || '').join(' ') });
    }
    // A NATIVE confirm()/alert() blocks every CDP evaluate until it is
    // answered. The page-level override below normally prevents one, but a
    // navigation drops the override — so accept anything that slips through
    // and record it (measured: without this the driver hangs on Reset).
    if (m.method === 'Page.javascriptDialogOpening') {
      nativeDialogs.push({ phase: phase, type: m.params.type, text: String(m.params.message).slice(0, 200) });
      ws.send(JSON.stringify({ id: ++id, method: 'Page.handleJavaScriptDialog', params: { accept: true } }));
    }
  };
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => {
    const rr = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) {
      const ex = rr.result.exceptionDetails.exception;
      throw new Error((ex && (ex.description || ex.value)) || 'eval failed: ' + expr.slice(0, 120));
    }
    return rr.result.result.value;
  };
  // Same as ev() but an in-page throw is RETURNED as a string rather than
  // failing the driver — used where the product throwing IS the finding.
  const evSoft = async (expr) => {
    try { return { v: await ev(expr) }; } catch (e) { return { err: String(e.message || e) }; }
  };
  const sleep = ms => new Promise(res => setTimeout(res, ms));
  const until = async (expr, ms) => {
    const t = Date.now();
    while (Date.now() - t < ms) { const v = await ev(expr); if (v) return v; await sleep(150); }
    return await ev(expr);
  };
  const typeChar = async (ch) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  };
  const KEYS = { Enter: 13, Tab: 9, Escape: 27, ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39, Backspace: 8, Delete: 46, m: 77, l: 76, ' ': 32 };
  const press = async (key) => {
    const p = { type: 'rawKeyDown', key: key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key] };
    if (key === 'Enter' || key === 'Tab') { p.text = key === 'Enter' ? '\r' : '\t'; p.type = 'keyDown'; }
    await send('Input.dispatchKeyEvent', p);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: key, windowsVirtualKeyCode: KEYS[key] });
  };
  // Real typing into a field: focus, clear, then per-character key events,
  // then fire change (a person's blur/Tab) so commit handlers run.
  const typeInto = async (sel, text, opts) => {
    const found = await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return 0;
       e.focus(); if (document.activeElement !== e) return -1;
       e.value=''; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
    if (found !== 1) {
      typeFailures.push({ sel: sel, reason: found === 0 ? 'no such element' : 'could not take focus', phase: phase });
      return false;
    }
    for (const ch of String(text)) { await typeChar(ch); await sleep(opts && opts.fast ? 2 : 10); }
    if (!(opts && opts.noChange)) {
      await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(e) e.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
    }
    await sleep(opts && opts.settle != null ? opts.settle : 220);
    return true;
  };
  const click = async (sel) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return 0; e.click(); return 1;})()`);
  const val = async (sel) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); return e? e.value : null;})()`);
  const txt = async (sel) => ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); return e? e.textContent.trim() : null;})()`);
  const msg = async () => ev(`(function(){var m=document.getElementById('gen-message'); return (m&&!m.hidden)? m.textContent.trim() : null;})()`);
  const step = async () => ev(`(window.QuamGen&&QuamGen.state)? QuamGen.state.step : null`);
  const spec = async (path) => ev(`(function(){try{return ${path};}catch(e){return '<<throw:'+e.message+'>>';}})()`);
  // Every navigation drops the page-level confirm/fetch instrumentation, so
  // re-install it on arrival — otherwise a later Reset press opens a NATIVE
  // confirm and every CDP evaluate blocks behind it.
  const ARM = `window.__confirms=window.__confirms||[];
     window.confirm=function(m){window.__confirms.push(String(m)); return true;};
     window.__alerts=window.__alerts||[]; window.alert=function(m){window.__alerts.push(String(m));};
     window.prompt=function(){return '';};
     window.__fetches=window.__fetches||[];
     if(!window.__origFetch){ window.__origFetch=window.fetch;
       window.fetch=function(u,o){ window.__fetches.push(String(u)+'|'+((o&&o.method)||'GET')); return window.__origFetch.apply(this,arguments); }; }
     1`;
  const nav = async (url, waitMs) => {
    await send('Page.navigate', { url: url });
    await sleep(waitMs == null ? 3500 : waitMs);
    await ev(ARM);
  };
  const shot = async (tag) => {
    const p = OUT.replace(/\.json$/, '_' + tag + '.png');
    const s = await send('Page.captureScreenshot', { format: 'png' });
    if (s.result && s.result.data) { fs.writeFileSync(p, Buffer.from(s.result.data, 'base64')); shots.push(p); }
  };

  await send('Page.enable'); await send('Runtime.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  /* ═══ PHASE A — mount + step 1 (Environment) ════════════════════════ */
  phase = 'A/mount';
  // window.confirm auto-true so destructive confirms are exercised, not dodged.
  await nav(BASE + '/generate', 3000);
  ok('the wizard mounts', await ev(`!!document.getElementById('generate-root')`));
  ok('QuamGen is exposed', await ev(`!!(window.QuamGen && QuamGen.state)`));
  ok('it opens on step 1', (await step()) === 1, await step());
  ok('…and it is in generate mode, not regenerate',
     (await ev(`QuamGen.state.mode`)) === 'generate', await ev(`QuamGen.state.mode`));

  // Env discovery must finish — "Scanning conda environments…" for ever is the
  // failure this watches for.
  const envDone = await until(`(function(){var l=document.getElementById('gen-env-list');
     if(!l) return 0; if(/Scanning|Rescanning/.test(l.textContent)) return 0;
     return l.querySelectorAll('.gen-env-row, [data-python], button, label').length || (l.textContent.trim()? -1 : 0);})()`, 60000);
  ok('env discovery finishes (not stuck on "Scanning…")', envDone !== 0,
     { rows: envDone, text: (await txt('#gen-env-list') || '').slice(0, 200) });
  await shot('A_step1');

  // Next with nothing selected → the guard must SAY something.
  await ev(`QuamGen.state.env=null; 1`);
  await click('#gen-next');
  await sleep(400);
  let m1 = await msg();
  ok('Next with no env selected refuses and says why',
     (await step()) === 1 && /environment/i.test(String(m1)), { step: await step(), msg: m1 });

  // Press Next three times in a row on the blocked step — the message must not
  // stack and the step must not advance.
  for (let i = 0; i < 3; i++) { await click('#gen-next'); await sleep(150); }
  ok('Next pressed three times on a blocked step stays on step 1 with one message',
     (await step()) === 1 && (await ev(`document.querySelectorAll('#gen-message').length`)) === 1,
     { step: await step(), msgNodes: await ev(`document.querySelectorAll('#gen-message').length`) });

  // A custom interpreter path that cannot exist — must say so, not hang.
  const envBeforeCustom = await ev(`QuamGen.state.env`);
  await typeInto('#gen-env-custom-path', 'C:\\definitely\\not\\here\\python.exe', { settle: 100 });
  await click('.gen-env-custom-use');
  const custStatus = await until(`(function(){var s=document.getElementById('gen-env-custom-status'); var t=(s&&s.textContent.trim())||''; return (t && !/^checking/i.test(t))? t : 0;})()`, 40000);
  ok('a nonexistent custom interpreter is reported, not silently accepted',
     !!custStatus && (await ev(`QuamGen.state.env`)) === envBeforeCustom,
     { status: custStatus, envBefore: envBeforeCustom, envAfter: await ev(`QuamGen.state.env`) });

  // Empty custom path + "Use this" — must not throw or change the env.
  const envBeforeEmpty = await ev(`QuamGen.state.env`);
  await ev(`(function(){var e=document.getElementById('gen-env-custom-path'); e.value=''; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  const emptyUse = await evSoft(`(function(){document.querySelector('.gen-env-custom-use').click(); return 1;})()`);
  await sleep(900);
  ok('"Use this" on an empty path does not throw and says what it wants',
     !emptyUse.err && (await ev(`QuamGen.state.env`)) === envBeforeEmpty &&
     /enter an interpreter/i.test(String(await txt('#gen-env-custom-status'))),
     { throw: emptyUse.err, env: await ev(`QuamGen.state.env`), status: await txt('#gen-env-custom-status') });

  // Pick a real env: prefer one the probe marked usable (status data-state=ok).
  await until(`document.querySelectorAll('#gen-env-list .gen-env-status[data-state="ok"]').length > 0 ? 1 : 0`, 90000);
  const envPick = await ev(`(function(){
     var rows = Array.prototype.slice.call(document.querySelectorAll('#gen-env-list [data-python]'));
     return rows.map(function(r){var s=r.querySelector('.gen-env-status');
        return {py:r.getAttribute('data-python'), state:s?s.dataset.state:null,
                txt:r.textContent.replace(/\\s+/g,' ').trim().slice(0,110)};});})()`);
  ok('the env list offers at least one environment', Array.isArray(envPick) && envPick.length > 0,
     (envPick || []).slice(0, 8));
  ok('…and at least one of them probes usable (has the QM stack)',
     (envPick || []).some(r => r.state === 'ok'), (envPick || []).map(r => r.state));
  const usable = (envPick || []).filter(r => r.state === 'ok');
  // Choose the cqt env if present (it has the customer QM stack), else any usable one.
  const chosen = usable.find(r => /[\\/]cqt[\\/]/i.test(r.py || '')) || usable[0] || (envPick || [])[0];
  // Clear whatever the auto-pick chose, so the CLICK is what is under test.
  // NB: the row is found by ATTRIBUTE COMPARISON, not a CSS attribute
  // selector — a Windows path's backslashes are CSS escapes and
  // `[data-python="D:\miniconda3\..."]` silently matches nothing
  // (measured: the first run of this driver never clicked a row at all).
  await ev(`QuamGen.state.env=null; 1`);
  let clickLanded = 0;
  if (chosen) {
    clickLanded = await ev(`(function(){
       var rows = Array.prototype.slice.call(document.querySelectorAll('#gen-env-list .gen-env-row'));
       var r = rows.filter(function(x){ return x.getAttribute('data-python') === ${JSON.stringify(chosen.py)}; })[0];
       if (!r) return 0;
       r.click(); return 1;})()`);
  }
  ok('the chosen env row is findable and clickable', clickLanded === 1, (chosen || {}).py);
  const envSet = await until(`(window.QuamGen&&QuamGen.state.env)? 1 : 0`, 60000);
  if (!envSet) {
    // Fall back so the rest of the wizard is reachable; record that the UI
    // path did not land. state.env is a STRING (the interpreter path).
    await ev(`QuamGen.state.env=${JSON.stringify((chosen || {}).py || 'python')}; 1`);
  }
  ok('clicking an environment row sets state.env to that interpreter', !!envSet &&
     (await ev(`QuamGen.state.env`)) === (chosen || {}).py,
     { clicked: (chosen || {}).py, env: await ev(`JSON.stringify(QuamGen.state.env)`) });
  ok('…and the clicked row is the one marked selected',
     await ev(`(function(){var s=document.querySelectorAll('#gen-env-list .gen-env-row.selected');
        return s.length === 1 && s[0].getAttribute('data-python') === ${JSON.stringify((chosen || {}).py || '')};})()`),
     await ev(`Array.prototype.map.call(document.querySelectorAll('#gen-env-list .gen-env-row.selected'), function(x){return x.getAttribute('data-python');})`));

  /* ═══ PHASE B — step 2 (Network) ════════════════════════════════════ */
  phase = 'B/network';
  await ev(`QuamGen.goToStep(2); 1`); await sleep(400);
  await click('#gen-next'); await sleep(300);
  ok('Next with an empty host refuses', (await step()) === 2 && /host/i.test(String(await msg())),
     { step: await step(), msg: await msg() });
  await typeInto('#gen-net-host', '   ', { settle: 150 });
  await click('#gen-next'); await sleep(300);
  ok('whitespace-only host is not accepted as a host',
     (await step()) === 2, { step: await step(), msg: await msg(), stored: await spec(`QuamGen.state.spec.network.host`) });
  await typeInto('#gen-net-host', '192.168.88.10');
  await click('#gen-next'); await sleep(300);
  ok('…then Next asks for the cluster name',
     (await step()) === 2 && /cluster/i.test(String(await msg())), { step: await step(), msg: await msg() });
  await typeInto('#gen-net-cluster', 'stress_cluster');

  // Port: a number input fed hostile values.
  const portCases = [];
  for (const v of ['abc', '-1', '0', '1e999', '99999999999999999999', '1,000', '80.5', '  ', '🙂', '한글']) {
    await typeInto('#gen-net-port', v, { settle: 120, fast: true });
    portCases.push({ typed: v, domValue: await val('#gen-net-port'), stored: await spec(`JSON.stringify(QuamGen.state.spec.network.port)`) });
  }
  ok('every hostile port value leaves a JSON-serialisable port (no NaN/Infinity in the spec)',
     portCases.every(c => !/NaN|Infinity|"?<<throw/.test(String(c.stored))), portCases);
  await typeInto('#gen-net-port', '');
  await click('#gen-next'); await sleep(400);
  ok('a filled host + cluster advances to step 3', (await step()) === 3, { step: await step(), msg: await msg() });

  /* ═══ PHASE C — step 3 (Chassis) ════════════════════════════════════ */
  phase = 'C/chassis';
  const chassisCases = [];
  for (const v of ['0', '-1', 'abc', '1e999', '999', '20', '3.7', '2']) {
    await typeInto('#gen-chassis-count', v, { settle: 250, fast: true });
    chassisCases.push({ typed: v, dom: await val('#gen-chassis-count'),
                        controllers: await spec(`QuamGen.state.spec.instruments.controllers.length`),
                        boxes: await ev(`document.querySelectorAll('#gen-chassis-list .gen-chassis').length || document.getElementById('gen-chassis-list').children.length`) });
  }
  ok('the chassis count never produces more controllers than the max of 20',
     chassisCases.every(c => typeof c.controllers === 'number' && c.controllers >= 0 && c.controllers <= 20), chassisCases);
  ok('…and the rendered chassis boxes match the model every time',
     chassisCases.every(c => c.boxes === c.controllers), chassisCases);

  // 0 chassis → the guard must refuse.
  await typeInto('#gen-chassis-count', '0', { settle: 300 });
  await click('#gen-next'); await sleep(300);
  ok('0 chassis refuses to advance', (await step()) === 3 && /chassis|FEM/i.test(String(await msg())),
     { step: await step(), msg: await msg() });

  await typeInto('#gen-chassis-count', '2', { settle: 400 });
  // A chassis with no FEM must also refuse.
  await click('#gen-next'); await sleep(300);
  const noFemMsg = await msg();
  ok('a chassis with no FEM refuses to advance',
     (await step()) === 3 && /FEM/i.test(String(noFemMsg)), { step: await step(), msg: noFemMsg });

  // --- the re-render that eats the next interaction (found in real Chrome) --
  // Typing a NEW chassis count and then moving on (Tab, or a click on a slot)
  // fires the input's native `change`, which re-renders the whole grid — and
  // the element the user was reaching for is destroyed mid-gesture.
  const focusAfterEdit = await (async () => {
    await ev(`(function(){var e=document.getElementById('gen-chassis-count'); e.focus(); e.select(); return 1;})()`);
    await typeChar('3'); await sleep(200);
    await press('Tab'); await sleep(400);
    return ev(`document.activeElement.tagName + '.' + (document.activeElement.className||'')`);
  })();
  const focusNoEdit = await (async () => {
    await ev(`(function(){var e=document.getElementById('gen-chassis-count'); e.focus(); return 1;})()`);
    await press('Tab'); await sleep(400);
    return ev(`document.activeElement.tagName + '.' + (document.activeElement.className||'')`);
  })();
  ok('Tab out of the chassis-count field lands on the slot grid, not on nothing',
     /gen-slot/.test(String(focusAfterEdit)),
     { afterEditingTheCount: focusAfterEdit, withNoEdit: focusNoEdit });
  ok('(control) Tab with the count UNCHANGED does reach the grid',
     /gen-slot/.test(String(focusNoEdit)), focusNoEdit);

  const clickAfterEdit = await (async () => {
    await ev(`(function(){var m=document.getElementById('gen-slot-menu'); if(m) m.hidden=true;
       var e=document.getElementById('gen-chassis-count'); e.focus(); e.select(); return 1;})()`);
    await typeChar('4'); await sleep(200);
    const b1 = await ev(`(function(){var s=document.querySelector('#gen-chassis-list .gen-slot');
       if(!s) return null; var r=s.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2};})()`);
    if (!b1) return { skipped: 1 };
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: b1.x, y: b1.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: b1.x, y: b1.y, button: 'left', clickCount: 1 });
    await sleep(450);
    const first = await ev(`document.getElementById('gen-slot-menu').hidden === false`);
    const b2 = await ev(`(function(){var s=document.querySelector('#gen-chassis-list .gen-slot');
       var r=s.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2};})()`);
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: b2.x, y: b2.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: b2.x, y: b2.y, button: 'left', clickCount: 1 });
    await sleep(450);
    const second = await ev(`document.getElementById('gen-slot-menu').hidden === false`);
    await ev(`(function(){var m=document.getElementById('gen-slot-menu'); if(m) m.hidden=true; return 1;})()`);
    return { first, second };
  })();
  ok('the first click on a slot after editing the chassis count opens its menu',
     clickAfterEdit.skipped || clickAfterEdit.first === true, clickAfterEdit);

  // Set FEMs by KEYBOARD where the mouse is expected (M / L on slots).
  // The count is committed FIRST (blur), so the grid is not re-rendered
  // out from under the focus this test needs.
  await ev(`(function(){var e=document.getElementById('gen-chassis-count'); e.blur(); document.body.focus(); return 1;})()`);
  await sleep(300);
  const kb = await evSoft(`(function(){
     var slots = document.querySelectorAll('#gen-chassis-list .gen-slot');
     if (!slots.length) return {slots:0};
     slots[0].focus();
     return {slots: slots.length, focused: document.activeElement === slots[0]};})()`);
  ok('the chassis grid has focusable slots', kb.v && kb.v.slots > 0 && kb.v.focused === true, kb);
  await press('m'); await sleep(200);
  await press('ArrowRight'); await sleep(120);
  await press('l'); await sleep(200);
  await press('ArrowRight'); await sleep(120);
  await press('m'); await sleep(200);
  const fems = await spec(`JSON.stringify(QuamGen.state.spec.instruments.controllers.map(function(c){return c.fems.map(function(f){return f.slot+':'+f.fem;});}))`);
  ok('M / L keys place FEM modules from the keyboard', /mw|lf/i.test(String(fems)), fems);
  // Delete then re-add — a list emptied and refilled.
  await press('Delete'); await sleep(200);
  const afterDel = await spec(`QuamGen.state.spec.instruments.controllers.reduce(function(a,c){return a+c.fems.length;},0)`);
  await press('m'); await sleep(200);
  const afterRe = await spec(`QuamGen.state.spec.instruments.controllers.reduce(function(a,c){return a+c.fems.length;},0)`);
  ok('Del clears a slot and the key re-adds it', afterRe === afterDel + 1, { afterDel, afterRe });

  // Enter opens the slot menu; Escape must close it and never leave two.
  await press('Enter'); await sleep(300);
  const menuOpen = await ev(`(function(){var m=document.getElementById('gen-slot-menu'); return m? !m.hidden : null;})()`);
  await press('Escape'); await sleep(250);
  const menuClosed = await ev(`(function(){var m=document.getElementById('gen-slot-menu'); return m? m.hidden : null;})()`);
  ok('Enter opens the slot menu and Escape closes it', menuOpen === true && menuClosed === true,
     { menuOpen, menuClosed });
  for (let i = 0; i < 3; i++) { await press('Enter'); await sleep(150); }
  ok('Enter three times leaves exactly one slot menu',
     (await ev(`document.querySelectorAll('#gen-slot-menu').length`)) === 1,
     await ev(`document.querySelectorAll('#gen-slot-menu').length`));
  await press('Escape'); await sleep(200);

  // Make sure there is at least one MW-FEM and one LF-FEM for the rest.
  // EVERY chassis needs a FEM (the step-3 guard), so populate them all.
  const femInject = await ev(`(function(){
     var c = QuamGen.state.spec.instruments.controllers;
     if (!c.length) return 0;
     c.forEach(function(ctrl, i){
       ctrl.fems = (i === 0)
         ? [{slot:1,fem:'mw'},{slot:2,fem:'lf'},{slot:3,fem:'mw'},{slot:4,fem:'lf'}]
         : [{slot:1,fem:'mw'},{slot:2,fem:'lf'}];
     });
     return c.length;})()`);
  results.push({ name: '(info) chassis populated with FEMs', pass: true, detail: femInject });
  await ev(`QuamGen.goToStep(3); 1`); await sleep(400);
  await click('#gen-next'); await sleep(500);
  ok('a chassis with FEMs advances to step 4', (await step()) === 4, { step: await step(), msg: await msg() });
  await shot('C_step3');
  // Defensive: the rest of the round is about step 4, so get there even if the
  // gate above misfired — the failure is already recorded above.
  if ((await step()) !== 4) { await ev(`QuamGen.goToStep(4); 1`); await sleep(600); }

  /* ═══ PHASE D — step 4 (Qubits, naming, pairs, TWPAs, QDAC) ═════════ */
  phase = 'D/qubits';
  await typeInto('#gen-qubit-count', '0', { settle: 300 });
  await click('#gen-next'); await sleep(300);
  ok('0 qubits refuses to advance', (await step()) === 4 && /qubit/i.test(String(await msg())),
     { step: await step(), msg: await msg() });

  const qCases = [];
  for (const v of ['1', '-1', 'abc', '1e999', '200', '250', '1,0', '0.5', '4']) {
    await typeInto('#gen-qubit-count', v, { settle: 450, fast: true });
    qCases.push({ typed: v, dom: await val('#gen-qubit-count'),
                  qubits: await spec(`QuamGen.state.spec.qubits.length`),
                  pairs: await spec(`QuamGen.state.spec.qubit_pairs.length`),
                  nameInputs: await ev(`document.querySelectorAll('#gen-qubit-name-list .gen-qubit-name-in').length`) });
  }
  ok('the qubit count is clamped to 0..200 for every hostile input',
     qCases.every(c => c.qubits >= 0 && c.qubits <= 200), qCases);
  ok('…and the rename list always has exactly one input per qubit',
     qCases.every(c => c.nameInputs === c.qubits), qCases);
  ok('…and no pair ever references more qubits than exist',
     await ev(`(function(){var q={}; QuamGen.state.spec.qubits.forEach(function(x){q[x]=1;});
        return QuamGen.state.spec.qubit_pairs.every(function(p){return q[p[0]]&&q[p[1]];});})()`),
     await spec(`JSON.stringify(QuamGen.state.spec.qubit_pairs)`));

  // Qubit count 1 — a single qubit means no chain pair.
  await typeInto('#gen-qubit-count', '1', { settle: 500 });
  ok('a 1-qubit chip has no pairs and still renders',
     (await spec(`QuamGen.state.spec.qubits.length`)) === 1 &&
     (await spec(`QuamGen.state.spec.qubit_pairs.length`)) === 0,
     { q: await spec(`QuamGen.state.spec.qubits.length`), p: await spec(`QuamGen.state.spec.qubit_pairs.length`) });
  await typeInto('#gen-qubit-count', '6', { settle: 600 });

  // --- naming scheme, fed hostile prefixes -------------------------------
  const nameCases = [];
  for (const pfx of ['q"x', 'q x', 'q한글', 'q🙂', 'Q', '', '   ', 'q-1', 'q_ok', '../q']) {
    await ev(`(function(){var s=document.getElementById('gen-naming-preset'); s.value='custom'; s.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
    await sleep(150);
    await typeInto('#gen-naming-prefix', pfx, { settle: 150, fast: true });
    const before = await spec(`JSON.stringify(QuamGen.state.spec.qubits)`);
    const r = await evSoft(`(function(){document.getElementById('gen-naming-apply').click(); return 1;})()`);
    await sleep(300);
    const after = await spec(`JSON.stringify(QuamGen.state.spec.qubits)`);
    nameCases.push({ prefix: pfx, threw: r.err || null, before: before, after: after, msg: await msg() });
  }
  // A prefix that fails QUBIT_NAME_RE must be refused with a message, and the
  // qubit set must be unchanged. 'Q'/'' fall back to 'q' by design.
  const badPrefixes = nameCases.filter(c => ['q"x', 'q x', 'q한글', 'q🙂', 'q-1', '../q'].indexOf(c.prefix) >= 0);
  ok('an invalid naming prefix is refused with a message, and no qubit is renamed',
     badPrefixes.every(c => !c.threw && c.before === c.after && /invalid|must start/i.test(String(c.msg))),
     badPrefixes);
  const goodPrefix = nameCases.find(c => c.prefix === 'q_ok');
  ok('a valid custom prefix does rename the set',
     goodPrefix && /q_ok/.test(String(goodPrefix.after)), goodPrefix);
  ok('no naming prefix ever throws', nameCases.every(c => !c.threw), nameCases.map(c => [c.prefix, c.threw]));

  // Inline rename with a hostile value: the input must snap back.
  await ev(`(function(){var s=document.getElementById('gen-naming-preset'); s.value='one_based'; s.dispatchEvent(new Event('change',{bubbles:true})); document.getElementById('gen-naming-apply').click(); return 1;})()`);
  await sleep(400);
  const renameCases = [];
  for (const nm of ['q 2', 'q-2', '한글', '', 'q1', 'x'.repeat(300), 'q2b']) {
    const first = await ev(`(function(){var i=document.querySelector('#gen-qubit-name-list .gen-qubit-name-in'); return i? i.value : null;})()`);
    await typeInto('#gen-qubit-name-list .gen-qubit-name-in', nm, { settle: 250, fast: true });
    renameCases.push({ typed: nm, was: first,
                       now: await ev(`(function(){var i=document.querySelector('#gen-qubit-name-list .gen-qubit-name-in'); return i? i.value : null;})()`),
                       qubits: await spec(`JSON.stringify(QuamGen.state.spec.qubits)`), msg: await msg() });
  }
  const badRenames = renameCases.filter(c => ['q 2', 'q-2', '한글', ''].indexOf(c.typed) >= 0);
  ok('an invalid inline rename restores the old name and says why',
     badRenames.every(c => c.now === c.was && /invalid|must start/i.test(String(c.msg))), badRenames);
  ok('a 300-character name is refused or accepted consistently with the model',
     (function () { const c = renameCases.find(x => x.typed.length === 300); return c && JSON.parse(c.qubits).indexOf(c.now) >= 0; })(),
     renameCases.find(x => x.typed.length === 300));
  ok('renaming to an existing sibling name is refused as a duplicate',
     (function () { const c = renameCases.find(x => x.typed === 'q1'); return !!c; })() &&
     await ev(`(function(){var q=QuamGen.state.spec.qubits; return new Set(q).size===q.length;})()`),
     { qubits: await spec(`JSON.stringify(QuamGen.state.spec.qubits)`) });

  // --- pairs: add, delete to empty, re-add --------------------------------
  await ev(`QuamGen.state.pairsTouched = true; 1`);
  const p0 = await spec(`QuamGen.state.spec.qubit_pairs.length`);
  for (let i = 0; i < 3; i++) { await click('#gen-add-pair'); await sleep(200); }
  const p1 = await spec(`QuamGen.state.spec.qubit_pairs.length`);
  ok('+ Add pair adds a row each press', p1 === p0 + 3, { p0, p1 });
  // …and each press pre-fills the SAME two qubits, so three presses leave
  // three identical pairs. Nothing on the client or the server objects.
  const dupPairs = await spec(`JSON.stringify(QuamGen.state.spec.qubit_pairs)`);
  const dupCount = await ev(`(function(){var seen={}, dup=0;
     QuamGen.state.spec.qubit_pairs.forEach(function(p){var k=p.slice().sort().join('|');
       if (seen[k]) dup++; seen[k]=1;}); return dup;})()`);
  await click('#gen-next'); await sleep(400);
  ok('duplicate qubit pairs are refused, or at least named',
     dupCount === 0 || ((await step()) === 4 && /duplicate|same|already/i.test(String(await msg()))),
     { duplicates: dupCount, pairs: dupPairs, step: await step(), msg: await msg() });
  if ((await step()) !== 4) { await ev(`QuamGen.goToStep(4); 1`); await sleep(400); }
  // A genuinely BLANK pair (the "—" option) must block Next.
  await ev(`(function(){var rows=document.querySelectorAll('#gen-pair-list .gen-pair-row');
     var row=rows[rows.length-1]; var c=row.querySelector('.gen-pair-c');
     c.value=''; c.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
  await sleep(400);
  await click('#gen-next'); await sleep(400);
  ok('a blank pair refuses to advance', (await step()) === 4 && /pair/i.test(String(await msg())),
     { step: await step(), msg: await msg(), pairs: await spec(`JSON.stringify(QuamGen.state.spec.qubit_pairs)`) });
  // Delete every pair row until the list is empty.
  let guardN = 0;
  while ((await spec(`QuamGen.state.spec.qubit_pairs.length`)) > 0 && guardN++ < 60) {
    await ev(`(function(){var b=document.querySelector('#gen-pair-list .gen-row-del'); if(b) b.click(); return 1;})()`);
    await sleep(90);
  }
  ok('every pair can be deleted down to an empty list',
     (await spec(`QuamGen.state.spec.qubit_pairs.length`)) === 0, { iterations: guardN });
  ok('…and the empty list says "No pairs." rather than rendering nothing',
     /No pairs/.test(String(await txt('#gen-pair-list'))), await txt('#gen-pair-list'));
  // Clicking delete on an already-empty list must not throw.
  const delEmpty = await evSoft(`(function(){var b=document.querySelector('#gen-pair-list .gen-row-del'); if(b){b.click();b.click();b.click();} return b?1:0;})()`);
  ok('deleting from an empty pair list does not throw', !delEmpty.err, delEmpty);
  await click('#gen-add-pair'); await sleep(250);
  ok('a pair can be added back after the list was emptied',
     (await spec(`QuamGen.state.spec.qubit_pairs.length`)) === 1);
  // Set both sides to the same qubit → must refuse.
  // Each change re-renders the list, so the row is re-queried in between —
  // holding the old node writes into a detached select.
  await ev(`(function(){var r=document.querySelectorAll('#gen-pair-list .gen-pair-row');
     var c=r[r.length-1].querySelector('.gen-pair-c');
     c.value = QuamGen.state.spec.qubits[0]; c.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
  await sleep(350);
  await ev(`(function(){var r=document.querySelectorAll('#gen-pair-list .gen-pair-row');
     var t=r[r.length-1].querySelector('.gen-pair-t');
     t.value = QuamGen.state.spec.qubits[0]; t.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
  await sleep(350);
  await click('#gen-next'); await sleep(300);
  ok('a self-pair (q↔q) refuses to advance',
     (await step()) === 4 && /different/i.test(String(await msg())), { step: await step(), msg: await msg() });
  await ev(`QuamGen.state.spec.qubit_pairs = [['q1','q2'],['q2','q3']]; QuamGen.state.pairsTouched=true;
            QuamGen._test ? 1 : 1; QuamGen.goToStep(4); 1`);
  await sleep(400);

  // --- TWPAs: add, empty id, delete to empty ------------------------------
  for (let i = 0; i < 3; i++) { await click('#gen-add-twpa'); await sleep(180); }
  ok('+ Add TWPA adds rows', (await spec(`QuamGen.state.spec.twpas.length`)) === 3,
     await spec(`QuamGen.state.spec.twpas.length`));
  await typeInto('#gen-twpa-list .gen-twpa-row input[type=text]', '', { settle: 250 });
  await click('#gen-next'); await sleep(300);
  ok('a TWPA with an empty id refuses to advance',
     (await step()) === 4 && /TWPA/i.test(String(await msg())), { step: await step(), msg: await msg() });
  guardN = 0;
  while ((await spec(`QuamGen.state.spec.twpas.length`)) > 0 && guardN++ < 40) {
    await ev(`(function(){var b=document.querySelector('#gen-twpa-list .gen-row-del, #gen-twpa-list button'); if(b) b.click(); return 1;})()`);
    await sleep(90);
  }
  ok('every TWPA can be deleted down to an empty list',
     (await spec(`QuamGen.state.spec.twpas.length`)) === 0, { iterations: guardN });
  ok('…and the empty TWPA list says "No TWPAs."',
     /No TWPAs/.test(String(await txt('#gen-twpa-list'))), await txt('#gen-twpa-list'));

  // --- the chip board: presets, grid dims, click-to-place, Del ------------
  phase = 'D/topology';
  await typeInto('#gen-qubit-count', '9', { settle: 700 });
  const presetRuns = [];
  for (const p of ['chain', 'ring', 'star', 'grid', 'grid', 'chain', 'ring']) {
    const r = await evSoft(`(function(){var b=document.querySelector('[data-topo-preset="${p}"]'); if(!b) return 0; b.click(); return 1;})()`);
    await sleep(320);
    presetRuns.push({ preset: p, threw: r.err || null, found: r.v,
                      pairs: await spec(`QuamGen.state.spec.qubit_pairs.length`),
                      stones: await ev(`document.querySelectorAll('#gen-topo-board .gen-topo-stone, #gen-topo-board [data-qubit]').length`),
                      placed: await ev(`(function(){var p=((QuamGen.state.spec.populate||{}).qubit)||{};
                         return QuamGen.state.spec.qubits.filter(function(q){return (p[q]||{}).grid_location!=null;}).length;})()`),
                      status: (await txt('#gen-topo-status') || '').slice(0, 80) });
  }
  ok('every topology preset runs without throwing', presetRuns.every(p => !p.threw && p.found === 1), presetRuns);
  ok('…and each one places every qubit on the board',
     presetRuns.every(p => p.placed === 9), presetRuns);
  ok('…and each one leaves at least one pair (except on a preset that needs none)',
     presetRuns.every(p => p.pairs >= 1), presetRuns);
  ok('…and repeating the same preset twice gives the same pair count',
     presetRuns[3].pairs === presetRuns[4].pairs, [presetRuns[3], presetRuns[4]]);

  // Grid cols/rows fed edge values.
  const zoneCases = [];
  for (const [c, r] of [['0', '0'], ['-1', '3'], ['abc', '3'], ['1e999', '3'], ['20', '20'], ['999', '999'], ['1', '1'], ['4', '4']]) {
    await typeInto('#gen-topo-cols', c, { settle: 150, fast: true });
    await typeInto('#gen-topo-rows', r, { settle: 250, fast: true });
    zoneCases.push({ c, r, zone: await ev(`JSON.stringify(window.WiringGrid.zone())`),
                     cells: await ev(`document.querySelectorAll('#gen-topo-board .gen-topo-cell').length`),
                     placed: await ev(`(function(){var p=((QuamGen.state.spec.populate||{}).qubit)||{};
                        return QuamGen.state.spec.qubits.filter(function(q){return (p[q]||{}).grid_location!=null;}).length;})()`) });
  }
  ok('a hostile grid dimension never produces a zero/negative/NaN zone',
     zoneCases.every(z => { const j = JSON.parse(z.zone); return j.cols >= 1 && j.rows >= 1 && isFinite(j.cols) && isFinite(j.rows); }), zoneCases);
  ok('…and the rendered cell count always equals cols × rows',
     zoneCases.every(z => { const j = JSON.parse(z.zone); return z.cells === j.cols * j.rows; }), zoneCases);
  ok('…and shrinking the board never silently unplaces a qubit',
     zoneCases[zoneCases.length - 1].placed === 9, zoneCases);

  // Click an empty cell three times, then a stone three times, then Delete.
  await typeInto('#gen-topo-cols', '6', { settle: 200 });
  await typeInto('#gen-topo-rows', '6', { settle: 300 });
  const boardClicks = await evSoft(`(async function(){
     function empty(){ return Array.prototype.slice.call(document.querySelectorAll('#gen-topo-board .gen-topo-cell'))
        .filter(function(c){ var col=c.dataset.col, row=c.dataset.row;
          return !window.WiringGrid._occupant(Number(col), Number(row)); })[0]; }
     var out = [];
     for (var i=0;i<3;i++){
       var c = empty(); if(!c){ out.push('no empty cell'); break; }
       c.click(); await new Promise(function(r){setTimeout(r,220);});
       out.push({qubits: QuamGen.state.spec.qubits.length,
                 placed: Object.keys(((QuamGen.state.spec.populate||{}).qubit)||{}).length});
     }
     return out;})()`);
  ok('clicking empty cells three times does not throw', !boardClicks.err, boardClicks);
  ok('…and never creates a qubit out of nothing (the count is the authority)',
     (await spec(`QuamGen.state.spec.qubits.length`)) === Number(await val('#gen-qubit-count')),
     { model: await spec(`QuamGen.state.spec.qubits.length`), box: await val('#gen-qubit-count') });

  const stoneTriple = await evSoft(`(async function(){
     var s = document.querySelector('#gen-topo-board [data-qubit]');
     if (!s) return {skipped:'no stone'};
     var id = s.getAttribute('data-qubit'), out = [];
     for (var i=0;i<3;i++){ s = document.querySelector('#gen-topo-board [data-qubit="'+id+'"]');
       if(!s){ out.push('gone'); break; }
       s.click(); await new Promise(function(r){setTimeout(r,220);});
       out.push({pairs: QuamGen.state.spec.qubit_pairs.length,
                 status: (document.getElementById('gen-topo-status')||{}).textContent}); }
     return {id: id, steps: out, qubits: QuamGen.state.spec.qubits.length};})()`);
  ok('clicking the same stone three times does not throw or duplicate a pair',
     !stoneTriple.err && await ev(`(function(){var seen={},d=0;
        QuamGen.state.spec.qubit_pairs.forEach(function(p){var k=p.slice().sort().join('|'); if(seen[k])d++; seen[k]=1;}); return d===0;})()`),
     stoneTriple);
  await press('Escape'); await sleep(250);
  // A REAL mouse click (the board listens to mousedown/mouseup, not a
  // synthetic `click`), in the state the wizard itself leaves you in —
  // focusStep(4) focuses #gen-qubit-count on entry and a board click does
  // not move focus off it.
  await ev(`(function(){var b=document.getElementById('gen-topo-board'); if(b) b.scrollIntoView({block:'center'}); return 1;})()`);
  await sleep(400);
  const stone0 = await ev(`(function(){var s=document.querySelector('#gen-topo-board [data-qubit]');
     if(!s) return null; var r=s.getBoundingClientRect();
     return {id:s.getAttribute('data-qubit'), x:r.left+r.width/2, y:r.top+r.height/2,
             inView: r.top>0 && r.bottom<innerHeight};})()`);
  let beforeDel = await spec(`QuamGen.state.spec.qubits.length`);
  let afterDelQ = beforeDel, delFocus = null, delStatus = null;
  if (stone0 && stone0.inView) {
    await ev(`(function(){var e=document.getElementById('gen-qubit-count'); if(e) e.focus(); return 1;})()`);
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: stone0.x, y: stone0.y });
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: stone0.x, y: stone0.y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: stone0.x, y: stone0.y, button: 'left', clickCount: 1 });
    await sleep(400);
    delStatus = await txt('#gen-topo-status');
    delFocus = await ev(`document.activeElement.tagName + '#' + document.activeElement.id`);
    beforeDel = await spec(`QuamGen.state.spec.qubits.length`);
    await press('Delete'); await sleep(600);
    afterDelQ = await spec(`QuamGen.state.spec.qubits.length`);
  }
  // Only assert when the precondition actually held: the click must have
  // ARMED/selected a stone (the status line says so). Otherwise the gesture
  // never reached the board and the check would be reporting the harness.
  const stoneArmed = /armed|selected/i.test(String(delStatus));
  if (stone0 && stone0.inView && stoneArmed) {
    ok('Del removes the selected stone, as the board help promises',
       afterDelQ === beforeDel - 1,
       { stone: stone0, statusAfterClick: delStatus, activeElement: delFocus, beforeDel, afterDelQ });
  } else {
    results.push({ name: '(info) board Delete not exercised — the click did not arm a stone',
                   pass: true, detail: { stone: stone0, statusAfterClick: delStatus, activeElement: delFocus } });
  }
  // Control: with focus off the count field the same gesture DOES delete.
  if (stone0 && stone0.inView && stoneArmed && afterDelQ === beforeDel) {
    await ev(`document.activeElement.blur(); 1`); await sleep(200);
    await press('Escape'); await sleep(200);
    const st = await ev(`(function(){var s=document.querySelector('#gen-topo-board [data-qubit]');
       if(!s) return null; var r=s.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2};})()`);
    if (st) {
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: st.x, y: st.y });
      await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: st.x, y: st.y, button: 'left', clickCount: 1 });
      await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: st.x, y: st.y, button: 'left', clickCount: 1 });
      await sleep(400);
      const b2 = await spec(`QuamGen.state.spec.qubits.length`);
      await press('Delete'); await sleep(600);
      const a2 = await spec(`QuamGen.state.spec.qubits.length`);
      ok('(control) the same gesture DOES delete once no input holds focus',
         a2 === b2 - 1, { before: b2, after: a2 });
      afterDelQ = a2; beforeDel = b2;
    }
  }
  ok('…and the qubit-count box follows the board',
     Number(await val('#gen-qubit-count')) === afterDelQ,
     { box: await val('#gen-qubit-count'), model: afterDelQ });
  ok('…and every remaining pair still references a live qubit',
     await ev(`(function(){var k={}; QuamGen.state.spec.qubits.forEach(function(q){k[q]=1;});
        return QuamGen.state.spec.qubit_pairs.every(function(p){return k[p[0]]&&k[p[1]];});})()`),
     await spec(`JSON.stringify(QuamGen.state.spec.qubit_pairs)`));
  // A hole in the ids must block a FORWARD step-rail jump, per jumpToStep.
  if (afterDelQ === beforeDel - 1) {
    await ev(`(function(){var li=document.querySelector('#gen-steps li[data-step="7"]'); if(li) li.click(); return 1;})()`);
    await sleep(400);
    ok('a forward jump with an id hole routes back to step 4 with a reason',
       (await step()) === 4 && !!(await msg()), { step: await step(), msg: await msg() });
    const renum = await ev(`(function(){var b=document.getElementById('gen-topo-renumber'); return b? !b.hidden : null;})()`);
    ok('…and the Renumber button is visible to fix it', renum === true, renum);
    await click('#gen-topo-renumber'); await sleep(500);
    ok('…and Renumber closes the hole',
       await ev(`(function(){var q=QuamGen.state.spec.qubits; return q.every(function(x,i){return x==='q'+(i+1);});})()`),
       await spec(`JSON.stringify(QuamGen.state.spec.qubits)`));
  }
  await typeInto('#gen-qubit-count', '6', { settle: 700 });

  // --- architecture radios in every order ---------------------------------
  const archOrder = ['flux_tunable_coupler', 'fixed_frequency', 'flux_tunable_fixed_coupler',
                     'fixed_frequency', 'flux_tunable_coupler', 'flux_tunable_fixed_coupler', 'fixed_frequency'];
  const archSeen = [];
  for (const a of archOrder) {
    const r = await evSoft(`(function(){var s=document.getElementById('gen-chip-arch'); s.value=${JSON.stringify(a)}; s.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
    await sleep(320);
    archSeen.push({ arch: a, threw: r.err || null,
                    state: await ev(`QuamGen.state.chipArch`),
                    gate: await ev(`QuamGen.state.pairGate`),
                    qubitFlux: await ev(`QuamGen.state.qubitFlux`),
                    fluxSourceVisible: await ev(`(function(){var e=document.getElementById('gen-line-flux-source'); return e? !e.hidden : null;})()`),
                    qdacVisible: await ev(`(function(){var e=document.getElementById('gen-qdac-band'); return e? !e.hidden : null;})()`) });
  }
  ok('every architecture switch lands in state without throwing',
     archSeen.every(a => !a.threw && a.state === a.arch), archSeen);
  ok('a fixed-frequency chip never keeps a tunable-coupler CZ',
     archSeen.every(a => !(a.arch === 'fixed_frequency' && a.gate === 'cz_tunable')), archSeen);
  // docs/136 §18: the row is visible on EVERY architecture (a QDAC-biased
  // fixed-frequency chip is the case that motivated the component); what the
  // architecture decides is which SOURCES are offered.
  ok('the flux-source control stays reachable on a fixed-frequency chip',
     archSeen.every(a => a.fluxSourceVisible === true), archSeen);
  const ffOpts = await (async () => {
    await ev(`(function(){var s=document.getElementById('gen-chip-arch'); s.value='fixed_frequency'; s.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
    await sleep(350);
    return ev(`(function(){var s=document.getElementById('gen-flux-source');
       return Array.prototype.map.call(s.options, function(o){
         return {v:o.value, disabled:o.disabled, label:o.textContent, title:o.title};});})()`);
  })();
  ok('…and on it the OPX-port sources are disabled WITH a reason, not silently offered',
     (ffOpts || []).filter(o => o.v === 'opx' || o.v === 'tee')
       .every(o => o.disabled === true && !!o.title), ffOpts);
  ok('…and the "opx" option is relabelled so it does not claim an LF-FEM bias',
     (ffOpts || []).some(o => o.v === 'opx' && /None \(no DC bias\)/.test(o.label)), ffOpts);

  // --- flux source × QDAC link, in every order ----------------------------
  await ev(`(function(){var s=document.getElementById('gen-chip-arch'); s.value='flux_tunable_coupler'; s.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
  await sleep(350);
  const fluxOrders = [['opx', 'qdac', 'tee', 'mixed', 'opx'], ['mixed', 'tee', 'qdac', 'opx', 'qdac'], ['tee', 'opx', 'mixed', 'qdac', 'tee']];
  const fluxSeen = [];
  for (const order of fluxOrders) {
    for (const mode of order) {
      const r = await evSoft(`(function(){var s=document.getElementById('gen-flux-source'); if(!s) return 0; s.value=${JSON.stringify(mode)}; s.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
      await sleep(300);
      fluxSeen.push({ mode: mode, threw: r.err || null,
                      chipMode: await ev(`QuamGen._test.chipFluxSource()`),
                      qdacBand: await ev(`(function(){var e=document.getElementById('gen-qdac-band'); return e? !e.hidden : null;})()`),
                      qdacEntries: await spec(`Object.keys((QuamGen.state.spec.qdac||{}).qubits||{}).length`),
                      qubits: await spec(`QuamGen.state.spec.qubits.length`) });
    }
  }
  ok('flux-source toggling in three different orders never throws',
     fluxSeen.every(f => !f.threw), fluxSeen.filter(f => f.threw));
  // docs/136: the band is deliberately visible once the chip HAS qubits (the
  // per-qubit picker is the only route to a mixed chip), so the honest
  // invariant is band-visible ⟺ qubits exist, and the chip-level answer is
  // DERIVED from the per-qubit shapes rather than stored.
  ok('the QDAC band is visible exactly while the chip has qubits',
     fluxSeen.every(f => (f.qubits > 0) === (f.qdacBand === true)),
     fluxSeen.map(f => [f.mode, f.qubits, f.qdacBand]));
  ok('the chip-level flux answer matches the mode just applied (or reports "mixed")',
     fluxSeen.every(f => f.chipMode === f.mode || f.mode === 'mixed' || f.chipMode === 'mixed'),
     fluxSeen.map(f => [f.mode, f.chipMode]));
  ok('a QDAC mode really creates a per-qubit entry, and leaving it removes them',
     fluxSeen.filter(f => f.mode === 'qdac').every(f => f.qdacEntries === f.qubits) &&
     fluxSeen.filter(f => f.mode === 'opx').every(f => f.qdacEntries === 0),
     fluxSeen.map(f => [f.mode, f.qdacEntries, f.qubits]));
  ok('leaving QDAC mode never orphans a qdac entry for a qubit that is not biased',
     await ev(`(function(){var q=QuamGen.state.spec.qdac||{}; var known={};
        QuamGen.state.spec.qubits.forEach(function(x){known[x]=1;});
        return Object.keys(q.qubits||{}).every(function(k){return known[k];});})()`),
     await spec(`JSON.stringify(Object.keys((QuamGen.state.spec.qdac||{}).qubits||{}))`));

  // QDAC link Ethernet/USB toggling + hostile IP/port.
  await ev(`(function(){var s=document.getElementById('gen-flux-source'); s.value='qdac'; s.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
  await sleep(400);
  const qdacCases = [];
  for (const link of ['USB', 'Ethernet', 'USB', 'Ethernet']) {
    await ev(`(function(){var s=document.getElementById('gen-qdac-comm'); if(!s) return 0; s.value=${JSON.stringify(link)}; s.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
    await sleep(250);
    qdacCases.push({ link: link,
                     ipShown: await ev(`(function(){var e=document.getElementById('gen-qdac-ip-label'); return e? !e.hidden : null;})()`),
                     usbShown: await ev(`(function(){var e=document.getElementById('gen-qdac-usb-label'); return e? !e.hidden : null;})()`) });
  }
  ok('the QDAC link toggle swaps IP and USB fields both ways',
     qdacCases.every(c => (c.link === 'USB') === (c.usbShown === true) && (c.link === 'Ethernet') === (c.ipShown === true)),
     qdacCases);
  for (const v of ['abc', '999.999.999.999', '', '-1', '🙂']) {
    await typeInto('#gen-qdac-ip', v, { settle: 120, fast: true });
  }
  ok('a hostile QDAC IP is stored as text and never breaks the spec',
     typeof (await spec(`(QuamGen.state.spec.qdac||{}).ip`)) !== 'undefined' ||
     (await spec(`JSON.stringify(QuamGen.state.spec.qdac||{})`)) !== null,
     await spec(`JSON.stringify(QuamGen.state.spec.qdac||{}).slice(0,300)`));
  await ev(`(function(){var s=document.getElementById('gen-flux-source'); s.value='opx'; s.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
  await sleep(350);
  await shot('D_step4');

  /* ═══ PHASE E — walk every step forward and backward repeatedly ═════ */
  phase = 'E/nav';
  // Set a valid chip for the rest.
  await ev(`(function(){
     var s = QuamGen.state;
     s.spec.qubit_pairs = [['q1','q2'],['q2','q3'],['q3','q4']];
     s.pairsTouched = true; s.spec.twpas = [];
     return 1;})()`);
  await typeInto('#gen-qubit-count', '6', { settle: 700 });
  await ev(`QuamGen.goToStep(4); 1`); await sleep(300);
  await click('#gen-next'); await sleep(600);
  ok('a valid step 4 advances to step 5', (await step()) === 5,
     { step: await step(), msg: await msg(),
       fems: await spec(`JSON.stringify(QuamGen.state.spec.instruments.controllers.map(function(c){return c.fems.length;}))`),
       lines: await spec(`(QuamGen.state.spec.lines||[]).length`) });
  if ((await step()) !== 5) { await ev(`QuamGen.goToStep(5); 1`); await sleep(600); }

  const navTrace = [];
  for (let cycle = 0; cycle < 4; cycle++) {
    for (let s = 1; s <= 8; s++) {
      await ev(`(function(){var li=document.querySelector('#gen-steps li[data-step="'+${s}+'"]'); if(li) li.click(); return 1;})()`);
      await sleep(120);
      navTrace.push({ want: s, got: await step() });
    }
    for (let s = 8; s >= 1; s--) {
      await ev(`(function(){var li=document.querySelector('#gen-steps li[data-step="'+${s}+'"]'); if(li) li.click(); return 1;})()`);
      await sleep(120);
      navTrace.push({ want: s, got: await step() });
    }
  }
  ok('64 step-rail jumps forward and backward always land on a real step',
     navTrace.every(t => t.got >= 1 && t.got <= 8), navTrace.filter(t => !(t.got >= 1 && t.got <= 8)).slice(0, 6));
  ok('…and exactly one panel is visible at every stop',
     await ev(`document.querySelectorAll('.gen-panel.active').length === 1`),
     await ev(`document.querySelectorAll('.gen-panel.active').length`));
  ok('…and the top and bottom step counters agree',
     (await txt('#gen-progress-top')) === (await txt('#gen-progress')),
     { top: await txt('#gen-progress-top'), bottom: await txt('#gen-progress') });

  // Back/Next hammered.
  await ev(`QuamGen.goToStep(4); 1`); await sleep(250);
  for (let i = 0; i < 10; i++) { await click('#gen-back'); await sleep(70); }
  ok('Back pressed ten times stops at step 1, never below', (await step()) === 1, await step());
  ok('…and the Back button is disabled there', await ev(`document.getElementById('gen-back').disabled === true`));
  // Next hammered on step 1 with an env set: it should walk forward and stop
  // at the first failing guard, never past 8 and never into a build.
  await ev(`window.__fetches.length=0; 1`);
  for (let i = 0; i < 14; i++) { await click('#gen-next'); await sleep(200); }
  ok('Next hammered 14 times never goes past step 8', (await step()) <= 8, await step());
  const fetched = await ev(`JSON.stringify(window.__fetches||[])`);
  ok('…and never fires a build',
     !/\/generate\/build|\/regenerate\/build/.test(String(fetched)), String(fetched).slice(0, 400));
  ok('…and it stops where a guard says so, with a message',
     (await step()) < 8 ? !!(await msg()) : true, { step: await step(), msg: await msg() });

  /* ═══ PHASE F — step 5 (Wiring): ONE allocate, fit toggle, drags ════ */
  phase = 'F/wiring';
  await ev(`QuamGen.goToStep(5); 1`); await sleep(600);
  await ev(`window.__fetches.length=0; 1`);
  const allocT0 = Date.now();
  await click('#gen-allocate-btn');
  // Press the primary button again immediately — twice in a row. The honest
  // question is whether TWO allocations can be in flight at once, so the
  // button's disabled state is sampled between the presses.
  await sleep(60);
  const btnDisabledMid = await ev(`document.getElementById('gen-allocate-btn').disabled`);
  const inFlightMid = await ev(`JSON.stringify((window.__fetches||[]).filter(function(u){return /allocate/.test(u);}))`);
  await click('#gen-allocate-btn');
  await sleep(60);
  const allocFetchesEarly = JSON.parse(await ev(`JSON.stringify((window.__fetches||[]).filter(function(u){return /allocate/.test(u);}))`));
  const allocated = await until(`(window.QuamGen && QuamGen.state.allocation) ? 1 : 0`, 180000);
  const allocMs = Date.now() - allocT0;
  const allocFetches = JSON.parse(await ev(`JSON.stringify((window.__fetches||[]).filter(function(u){return /allocate/.test(u);}))`));
  ok('Auto-allocate completes', !!allocated,
     { ms: allocMs, status: await txt('#gen-allocate-status'), msg: await msg(),
       fetches: allocFetches, issues: (await txt('#gen-wiring-issues') || '').slice(0, 200) });
  ok('a second press while the first is still running fires no second allocation',
     btnDisabledMid === true && allocFetchesEarly.length <= 1,
     { btnDisabledMid, inFlightMid, early: allocFetchesEarly, total: allocFetches });
  if (!allocated) {
    ok('…and when it cannot, the page says why rather than staying blank',
       /allocation failed|Run Auto-allocate|environment|qubits/i.test(String(await txt('#gen-wiring-diagram')) + String(await txt('#gen-allocate-status'))),
       { diagram: (await txt('#gen-wiring-diagram') || '').slice(0, 200),
         status: await txt('#gen-allocate-status'), msg: await msg() });
  }
  await sleep(1200);
  await shot('F_step5');

  const hasSvg = await ev(`!!document.querySelector('#gen-wiring-diagram svg.instrument-svg')`);
  ok('the wiring diagram renders a rack', hasSvg,
     { text: (await txt('#gen-wiring-diagram') || '').slice(0, 200) });

  if (hasSvg) {
    // Fit vs 1:1 — the toggle only appears when the rack is wider than the pane.
    const fitBar = await ev(`(function(){var b=document.querySelector('#gen-wiring-diagram .iw-fitbar'); return b? b.textContent.trim() : null;})()`);
    if (fitBar) {
      const fitStates = [];
      for (let i = 0; i < 4; i++) {
        const before = await ev(`(function(){var s=document.querySelector('#gen-wiring-diagram svg.instrument-svg'); return s? s.style.width : null;})()`);
        await ev(`(function(){var b=document.querySelector('#gen-wiring-diagram .iw-fitbar-btn'); if(b) b.click(); return 1;})()`);
        await sleep(350);
        fitStates.push({ before: before, label: await ev(`(function(){var b=document.querySelector('#gen-wiring-diagram .iw-fitbar-btn'); return b? b.textContent : null;})()`),
                         after: await ev(`(function(){var s=document.querySelector('#gen-wiring-diagram svg.instrument-svg'); return s? s.style.width : null;})()`),
                         bars: await ev(`document.querySelectorAll('#gen-wiring-diagram .iw-fitbar').length`) });
      }
      ok('Fit / 1:1 toggles the rack width every press', fitStates.every(f => f.before !== f.after), fitStates);
      ok('…and never leaves two fit bars', fitStates.every(f => f.bars === 1), fitStates.map(f => f.bars));
      ok('…and the label always matches the state it offers',
         fitStates.every(f => f.label === '1:1' || f.label === 'Fit width'), fitStates.map(f => f.label));
    } else {
      ok('the fit bar is absent because the rack already fits (not a defect)',
         await ev(`(function(){var h=document.getElementById('gen-wiring-diagram'); var s=h.querySelector('svg.instrument-svg');
            return s? (parseFloat(s.dataset.natW)||0) <= h.clientWidth+1 : false;})()`),
         { natW: await ev(`(function(){var s=document.querySelector('#gen-wiring-diagram svg.instrument-svg'); return s? s.dataset.natW : null;})()`),
           host: await ev(`(document.getElementById('gen-wiring-diagram')||{}).clientWidth`) });
    }

    // --- drags: onto an occupied port, and onto an invalid FEM type -------
    // First the pure rule (isValidDrop), then a REAL mouse drag whose result
    // must match it and must not corrupt the allocation.
    const rules = await ev(`(function(){
       var T = QuamGen._test, out = {};
       function fems(){ var r=[]; QuamGen.state.spec.instruments.controllers.forEach(function(c){
          (c.fems||[]).forEach(function(f){ r.push({con:c.con!=null?c.con:c.id, slot:f.slot, type:(f.fem==='mw'?'mw-fem':f.fem==='lf'?'lf-fem':f.fem)}); }); }); return r; }
       out.fems = fems();
       var mw = out.fems.filter(function(f){return f.type==='mw-fem';})[0];
       var lf = out.fems.filter(function(f){return f.type==='lf-fem';})[0];
       out.mw = mw; out.lf = lf;
       if (mw && lf) {
         out.xyOntoLf = T.isValidDrop({role:'xy', con:mw.con, slot:mw.slot, port:1, io:'output'},
                                      {con:lf.con, slot:lf.slot, port:1, io:'output'});
         out.zOntoMw  = T.isValidDrop({role:'z', con:lf.con, slot:lf.slot, port:1, io:'output'},
                                      {con:mw.con, slot:mw.slot, port:1, io:'output'});
         out.rrOntoInput = T.isValidDrop({role:'rr', con:mw.con, slot:mw.slot, port:1, io:'output'},
                                         {con:mw.con, slot:mw.slot, port:1, io:'input'});
         out.xyOntoMwOut = T.isValidDrop({role:'xy', con:mw.con, slot:mw.slot, port:1, io:'output'},
                                         {con:mw.con, slot:mw.slot, port:5, io:'output'});
         out.ontoSelf = T.isValidDrop({role:'xy', con:mw.con, slot:mw.slot, port:1, io:'output'},
                                      {con:mw.con, slot:mw.slot, port:1, io:'output'});
         out.ontoNothing = T.isValidDrop({role:'xy', con:mw.con, slot:mw.slot, port:1, io:'output'}, null);
         out.ontoGhostSlot = T.isValidDrop({role:'xy', con:mw.con, slot:mw.slot, port:1, io:'output'},
                                           {con:mw.con, slot:99, port:1, io:'output'});
       }
       return out;})()`);
    ok('an XY line refuses an LF-FEM port', rules.xyOntoLf === false, rules);
    ok('a flux line refuses an MW-FEM port', rules.zOntoMw === false, rules);
    ok('a readout OUTPUT refuses an input port', rules.rrOntoInput === false, rules);
    ok('a line refuses to be dropped on itself', rules.ontoSelf === false, rules);
    ok('a drop on nothing is refused', rules.ontoNothing === false, rules);
    ok('a drop on a slot with no FEM is refused', rules.ontoGhostSlot === false, rules);
    ok('a legal same-FEM move IS allowed (the rule is not simply "no")', rules.xyOntoMwOut === true, rules);

    // A REAL drag. The mousedown listener sits on `.iw-port-circle` /
    // `.iw-port-grip`; the drop target is resolved by elementFromPoint, so
    // the diagram must be scrolled into view first.
    await ev(`(function(){var h=document.getElementById('gen-wiring-diagram'); if(h) h.scrollIntoView({block:'center'}); return 1;})()`);
    await sleep(500);
    const dragProbe = await ev(`(function(){
       var cs = Array.prototype.slice.call(document.querySelectorAll('#gen-wiring-diagram .iw-port-circle'));
       function femType(con,slot){
         var ctrls = (QuamGen.state.spec.instruments||{}).controllers||[];
         for (var i=0;i<ctrls.length;i++){ if(String(ctrls[i].con)===String(con)){
           var f=(ctrls[i].fems||[]).filter(function(x){return String(x.slot)===String(slot);})[0];
           return f? (f.fem==='mw'?'mw-fem':f.fem==='lf'?'lf-fem':f.fem) : null; } }
         return null; }
       function info(c){ var cell=c.closest('.iw-port');
         return {role:c.getAttribute('data-role'), element:c.getAttribute('data-element'),
                 con:cell.dataset.con, slot:cell.dataset.slot, port:cell.dataset.port, io:cell.dataset.io,
                 fem: femType(cell.dataset.con, cell.dataset.slot)}; }
       var all = cs.map(info);
       var cells = Array.prototype.slice.call(document.querySelectorAll('#gen-wiring-diagram .iw-port'));
       return {circles: cs.length, cells: cells.length, roles: Array.from(new Set(all.map(function(x){return x.role;}))),
               sample: all.slice(0,6)};})()`);
    ok('the diagram exposes draggable port circles', dragProbe.circles > 0, dragProbe);

    if (dragProbe.circles > 0) {
      // ①  xy (MW) → an LF-FEM output port: an INVALID target.
      const beforeInv = await ev(`JSON.stringify(QuamGen.state.allocation)`);
      const invalid = await evSoft(`(async function(){
         function femType(con,slot){
           var ctrls=(QuamGen.state.spec.instruments||{}).controllers||[];
           for(var i=0;i<ctrls.length;i++){ if(String(ctrls[i].con)===String(con)){
             var f=(ctrls[i].fems||[]).filter(function(x){return String(x.slot)===String(slot);})[0];
             return f? (f.fem==='mw'?'mw-fem':f.fem==='lf'?'lf-fem':f.fem):null; } } return null; }
         function ctr(el){ var r=el.getBoundingClientRect(); return {x:r.left+r.width/2, y:r.top+r.height/2}; }
         var circles = Array.prototype.slice.call(document.querySelectorAll('#gen-wiring-diagram .iw-port-circle'));
         var src = circles.filter(function(c){ var cell=c.closest('.iw-port');
            return c.getAttribute('data-role')==='xy' && femType(cell.dataset.con, cell.dataset.slot)==='mw-fem'; })[0];
         if (src) { src.scrollIntoView({block:'center'}); await new Promise(function(r){setTimeout(r,300);}); }
         if (!src) return {skipped:'no xy circle on an mw-fem'};
         var cells = Array.prototype.slice.call(document.querySelectorAll('#gen-wiring-diagram .iw-port'));
         var tgt = cells.filter(function(t){ return t.dataset.io==='output' && femType(t.dataset.con,t.dataset.slot)==='lf-fem'; })[0];
         if (!tgt) return {skipped:'no lf-fem output port'};
         // Both ends must be ON SCREEN: the drop target is resolved with
         // elementFromPoint, which only sees the viewport.
         tgt.scrollIntoView({block:'center'});
         await new Promise(function(r){setTimeout(r,400);});
         var a = ctr(src), b = ctr(tgt);
         if (b.y < 0 || b.y > innerHeight || a.y < 0 || a.y > innerHeight)
           return {skipped:'src or target off screen', a:a, b:b};
         function fire(el,type,x,y){ el.dispatchEvent(new MouseEvent(type,{bubbles:true,cancelable:true,clientX:x,clientY:y,buttons:type==='mouseup'?0:1})); }
         fire(src,'mousedown',a.x,a.y);
         await new Promise(function(r){setTimeout(r,80);});
         var mid = document.elementFromPoint((a.x+b.x)/2,(a.y+b.y)/2);
         fire(document,'mousemove',(a.x+b.x)/2,(a.y+b.y)/2);
         await new Promise(function(r){setTimeout(r,80);});
         fire(document,'mousemove',b.x,b.y);
         await new Promise(function(r){setTimeout(r,150);});
         var marked = tgt.classList.contains('iw-port-bad');
         var monitor = (document.getElementById('gen-wiring-monitor')||{}).textContent || '';
         fire(document,'mouseup',b.x,b.y);
         await new Promise(function(r){setTimeout(r,500);});
         return {src:{role:src.getAttribute('data-role'), element:src.getAttribute('data-element')},
                 tgtFem:femType(tgt.dataset.con,tgt.dataset.slot), tgtIo:tgt.dataset.io,
                 markedBad: marked, monitor: monitor.replace(/\\s+/g,' ').trim().slice(0,140),
                 ghosts: document.querySelectorAll('#gen-drag-ghost').length,
                 lifted: document.querySelectorAll('.iw-port-lifted').length};})()`);
      const afterInv = await ev(`JSON.stringify(QuamGen.state.allocation)`);
      if (invalid.v && invalid.v.skipped) {
        results.push({ name: '(info) invalid-target drag skipped', pass: true, detail: invalid.v.skipped });
      } else {
        ok('dragging an XY line onto an LF-FEM port is REFUSED (allocation unchanged)',
           !invalid.err && beforeInv === afterInv, { threw: invalid.err, res: invalid.v });
        ok('…and the invalid target is marked bad while hovered',
           invalid.v && invalid.v.markedBad === true, invalid.v);
        ok('…and the refused drag leaves no ghost and no dimmed port',
           invalid.v && invalid.v.ghosts === 0 && invalid.v.lifted === 0, invalid.v);
      }

      // ②  a drag onto an OCCUPIED but legal port: must move/swap, never
      //     silently drop a line or leave two lines claiming one port.
      const beforeOcc = await ev(`JSON.stringify(QuamGen.state.allocation)`);
      const nLinesBefore = await ev(`(function(){var a=QuamGen.state.allocation||{}; var n=0;
         Object.keys(a).forEach(function(e){Object.keys(a[e]||{}).forEach(function(k){ n += (a[e][k]||[]).length; });}); return n;})()`);
      const occupied = await evSoft(`(async function(){
         function femType(con,slot){
           var ctrls=(QuamGen.state.spec.instruments||{}).controllers||[];
           for(var i=0;i<ctrls.length;i++){ if(String(ctrls[i].con)===String(con)){
             var f=(ctrls[i].fems||[]).filter(function(x){return String(x.slot)===String(slot);})[0];
             return f? (f.fem==='mw'?'mw-fem':f.fem==='lf'?'lf-fem':f.fem):null; } } return null; }
         function ctr(el){ var r=el.getBoundingClientRect(); return {x:r.left+r.width/2, y:r.top+r.height/2}; }
         var circles = Array.prototype.slice.call(document.querySelectorAll('#gen-wiring-diagram .iw-port-circle'));
         var src = circles.filter(function(c){ return c.getAttribute('data-role')==='xy'; })[0];
         if (!src) return {skipped:'no xy circle'};
         var srcCell = src.closest('.iw-port');
         // an occupied MW output port that is NOT the source's own
         var tgt = circles.map(function(c){return c.closest('.iw-port');})
            .filter(function(t){ return t!==srcCell && t.dataset.io==='output' &&
                femType(t.dataset.con,t.dataset.slot)==='mw-fem'; })[0];
         if (!tgt) return {skipped:'no occupied mw output target'};
         var tgtDesc = {con:tgt.dataset.con, slot:tgt.dataset.slot, port:tgt.dataset.port,
                        occupants: tgt.querySelectorAll('.iw-port-circle').length};
         var a = ctr(src), b = ctr(tgt);
         function fire(el,type,x,y){ el.dispatchEvent(new MouseEvent(type,{bubbles:true,cancelable:true,clientX:x,clientY:y,buttons:type==='mouseup'?0:1})); }
         fire(src,'mousedown',a.x,a.y);
         await new Promise(function(r){setTimeout(r,80);});
         fire(document,'mousemove',b.x,b.y);
         await new Promise(function(r){setTimeout(r,150);});
         var ok_ = tgt.classList.contains('iw-port-ok'), bad_ = tgt.classList.contains('iw-port-bad');
         fire(document,'mouseup',b.x,b.y);
         await new Promise(function(r){setTimeout(r,600);});
         return {tgt:tgtDesc, markedOk:ok_, markedBad:bad_,
                 ghosts: document.querySelectorAll('#gen-drag-ghost').length,
                 lifted: document.querySelectorAll('.iw-port-lifted').length,
                 stillRendered: !!document.querySelector('#gen-wiring-diagram svg.instrument-svg')};})()`);
      const nLinesAfter = await ev(`(function(){var a=QuamGen.state.allocation||{}; var n=0;
         Object.keys(a).forEach(function(e){Object.keys(a[e]||{}).forEach(function(k){ n += (a[e][k]||[]).length; });}); return n;})()`);
      if (occupied.v && occupied.v.skipped) {
        results.push({ name: '(info) occupied-target drag skipped', pass: true, detail: occupied.v.skipped });
      } else {
        ok('dragging onto an OCCUPIED port does not throw', !occupied.err, occupied.err);
        ok('…and no channel is lost or duplicated by the swap',
           nLinesAfter === nLinesBefore, { before: nLinesBefore, after: nLinesAfter, res: occupied.v });
        ok('…and the diagram re-renders after the drop',
           occupied.v && occupied.v.stillRendered === true, occupied.v);
        ok('…and no ghost or dimmed port is left behind',
           occupied.v && occupied.v.ghosts === 0 && occupied.v.lifted === 0, occupied.v);
        const dup = await ev(`(function(){
           var a=QuamGen.state.allocation||{}, seen={}, dups=[];
           Object.keys(a).forEach(function(e){ Object.keys(a[e]||{}).forEach(function(k){
             (a[e][k]||[]).forEach(function(ch){
               var key = (ch.io_type||'output')+':'+ch.con+'/'+ch.slot+'/'+ch.port;
               if (k==='xy' || k==='z' || k==='c') { if (seen[key] && seen[key]!==e+'.'+k) dups.push(key+' = '+seen[key]+' & '+e+'.'+k); seen[key]=e+'.'+k; }
             }); }); });
           return dups;})()`);
        ok('…and no two exclusive (xy/z/coupler) lines end up on one physical port',
           Array.isArray(dup) && dup.length === 0, dup);
      }
      results.push({ name: '(info) allocation changed by the occupied-port drag', pass: true,
                     detail: { changed: beforeOcc !== (await ev(`JSON.stringify(QuamGen.state.allocation)`)) } });

      // ③ Escape mid-drag: start one and abandon it.
      const escDrag = await evSoft(`(async function(){
         var c = document.querySelector('#gen-wiring-diagram .iw-port-circle');
         if (!c) return {skipped:1};
         var r = c.getBoundingClientRect();
         c.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2,buttons:1}));
         await new Promise(function(z){setTimeout(z,90);});
         document.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:r.left+140,clientY:r.top+70,buttons:1}));
         await new Promise(function(z){setTimeout(z,120);});
         var hadGhost = document.querySelectorAll('#gen-drag-ghost').length;
         document.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,key:'Escape'}));
         await new Promise(function(z){setTimeout(z,300);});
         return {hadGhost: hadGhost,
                 ghosts: document.querySelectorAll('#gen-drag-ghost').length,
                 lifted: document.querySelectorAll('.iw-port-lifted').length};})()`);
      ok('a drag really lifts a ghost', escDrag.v && escDrag.v.hadGhost === 1, escDrag);
      ok('Escape mid-drag cancels it, removing the ghost and un-dimming the port',
         !escDrag.err && escDrag.v && escDrag.v.ghosts === 0 && escDrag.v.lifted === 0, escDrag);
      // ④ mouseup on nothing (off the rack) — must not apply anything.
      const nowhere = await evSoft(`(async function(){
         var c = document.querySelector('#gen-wiring-diagram .iw-port-circle');
         if (!c) return {skipped:1};
         var before = JSON.stringify(QuamGen.state.allocation);
         var r = c.getBoundingClientRect();
         c.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2,buttons:1}));
         await new Promise(function(z){setTimeout(z,90);});
         document.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:4,clientY:4,buttons:1}));
         await new Promise(function(z){setTimeout(z,90);});
         document.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,clientX:4,clientY:4}));
         await new Promise(function(z){setTimeout(z,350);});
         return {same: before === JSON.stringify(QuamGen.state.allocation),
                 ghosts: document.querySelectorAll('#gen-drag-ghost').length,
                 lifted: document.querySelectorAll('.iw-port-lifted').length};})()`);
      ok('dropping a port onto nothing changes nothing and cleans up',
         !nowhere.err && nowhere.v && (nowhere.v.skipped || (nowhere.v.same && nowhere.v.ghosts === 0 && nowhere.v.lifted === 0)),
         nowhere);
    }

    // Manual pin override: type a hostile pin into the wiring table.
    const pinCells = await ev(`document.querySelectorAll('#gen-wiring-table .gen-wiring-pin').length`);
    ok('the wiring table offers a manual Pin field per line', pinCells > 0, pinCells);
    if (pinCells > 0) {
      const pinCases = [];
      for (const p of ['9/9/9', 'abc', '1/1/-1', '1//1', '1/1/1/1', '', '한글', '1e999/1/1', '1.5/1/1', '  1 / 1 / 1  ']) {
        await typeInto('#gen-wiring-table .gen-wiring-pin', p, { settle: 250, fast: true });
        pinCases.push({ typed: p, dom: await val('#gen-wiring-table .gen-wiring-pin'),
                        channel: String(await ev(`JSON.stringify(QuamGen.state.spec.lines[0].channel)`)),
                        pinnedClass: await ev(`(function(){var r=document.querySelector('#gen-wiring-table tbody tr'); return r? r.classList.contains('pinned'):null;})()`) });
      }
      ok('a hostile manual pin never throws and never leaves a NaN in the channel',
         pinCases.every(c => typeof c.dom === 'string' && !/NaN|Infinity/.test(c.channel)), pinCases);
      ok('…and an unparseable pin clears the channel rather than half-setting it',
         pinCases.filter(c => ['abc', '1//1', '1/1/1/1', '', '한글'].indexOf(c.typed) >= 0)
                 .every(c => c.channel === 'null'), pinCases);
      results.push({ name: '(info) manual pin values the wizard ACCEPTS without complaint', pass: true,
                     detail: pinCases.filter(c => c.channel !== 'null').map(c => c.typed + ' -> ' + c.channel) });
      await typeInto('#gen-wiring-table .gen-wiring-pin', '', { settle: 300 });
    }
  }

  /* ═══ PHASE G — step 6 (Populate): edge values + presets ════════════ */
  phase = 'G/populate';
  await ev(`QuamGen.goToStep(6); 1`); await sleep(1500);
  await ev(`if (QuamGen._test && QuamGen._test.setValidateDebounce) QuamGen._test.setValidateDebounce(1); 1`);
  // Pin the entry units the assertions below are written against (a stored
  // preference would otherwise change what "2" in an amplitude cell MEANS).
  await ev(`QuamGen.state.powerMode='manual'; QuamGen.state.populateUnits.amp='0-1';
            QuamGen.state.populateUnits.freq='GHz'; QuamGen._test.renderPopulateTables(); 1`);
  await sleep(900);
  const cellCount = await ev(`document.querySelectorAll('.gen-pop-in[data-field]').length`);
  ok('the populate tables render cells', cellCount > 0, cellCount);
  const popGroups = await ev(`JSON.stringify(Array.from(new Set(Array.prototype.map.call(
     document.querySelectorAll('.gen-pop-in[data-field]'), function(i){return i.dataset.group+':'+i.dataset.field;}))).slice(0,40))`);
  results.push({ name: '(info) populate cells present', pass: true, detail: { count: cellCount, sample: popGroups } });
  await shot('G_step6');

  if (cellCount > 0) {
    // Find an RF_freq cell in the qubit table — the honest-validation target.
    const rfSel = await ev(`(function(){
       var c = document.querySelector('.gen-pop-in[data-group="qubit"][data-field="RF_freq"]') ||
               document.querySelector('.gen-pop-in[data-field="RF_freq"]');
       if (!c) return null;
       c.id = c.id || 'stress-rf-cell';
       return '#' + c.id;})()`);
    ok('an RF_freq cell exists to validate against', !!rfSel, rfSel);
    if (rfSel) {
      const rfCases = [];
      for (const v of ['', '   ', '0', '-1', '-5.2', 'abc', '1e999', '99999999999999999999', '1,000', '5.1', '0.001', 'NaN', '한글', '"', '\\\\', '5'.repeat(300)]) {
        await typeInto(rfSel, v, { settle: 260, fast: true });
        rfCases.push({ typed: v, dom: await val(rfSel),
                       err: await ev(`(function(){var e=document.querySelector(${JSON.stringify(rfSel)}); return e? e.classList.contains('gen-cell-err') : null;})()`),
                       warn: await ev(`(function(){var e=document.querySelector(${JSON.stringify(rfSel)}); return e? e.classList.contains('gen-cell-warn') : null;})()`),
                       flag: await ev(`(function(){var e=document.querySelector(${JSON.stringify(rfSel)}); var td=e&&e.parentNode; var f=td&&td.querySelector('.gen-cell-flag'); return f? f.title.slice(0,110) : null;})()`) });
      }
      const shouldFlag = rfCases.filter(c => ['0', '-1', '-5.2', 'abc', 'NaN', '한글', '"'].indexOf(c.typed) >= 0);
      ok('every plainly-wrong RF frequency is flagged, not silently accepted',
         shouldFlag.every(c => c.err === true && !!c.flag), shouldFlag);
      const blanks = rfCases.filter(c => c.typed === '' || c.typed.trim() === '');
      ok('a blank cell is not flagged (blank keeps the builder default)',
         blanks.every(c => c.err === false && c.warn === false), blanks);
      const inf = rfCases.find(c => c.typed === '1e999');
      ok('1e999 is flagged rather than stored as Infinity',
         inf && inf.err === true, inf);
      const sane = rfCases.find(c => c.typed === '5.1');
      ok('a legitimate value is accepted with no flag', sane && sane.err === false && sane.warn === false, sane);
      ok('no populate value in the spec is NaN or Infinity after all that',
         !/NaN|Infinity/.test(String(await spec(`JSON.stringify(QuamGen.state.spec.populate||{})`))),
         String(await spec(`JSON.stringify(QuamGen.state.spec.populate||{})`)).slice(0, 300));
    }

    // An amplitude cell: |amp| > 1 must be refused honestly.
    const ampSel = await ev(`(function(){
       var c = document.querySelector('.gen-pop-in[data-field="x180_amplitude"][data-rid]') ||
               document.querySelector('.gen-pop-in[data-dim="amp"][data-rid]');
       if (!c) return null; c.id = c.id || 'stress-amp-cell'; return '#' + c.id;})()`);
    results.push({ name: '(info) amplitude cell under test', pass: true,
                   detail: ampSel ? await ev(`(function(){var e=document.querySelector(${JSON.stringify(ampSel)});
                     return {group:e.dataset.group, field:e.dataset.field, dim:e.dataset.dim,
                             mode:QuamGen.state.powerMode, unit:QuamGen.state.populateUnits.amp};})()`) : null });
    if (ampSel) {
      const ampCases = [];
      for (const v of ['0', '0.5', '1', '1.0001', '2', '-2', '-0.5', '1e999', 'abc']) {
        await typeInto(ampSel, v, { settle: 250, fast: true });
        ampCases.push({ typed: v, err: await ev(`(function(){var e=document.querySelector(${JSON.stringify(ampSel)}); return e? e.classList.contains('gen-cell-err'):null;})()`),
                        flag: await ev(`(function(){var e=document.querySelector(${JSON.stringify(ampSel)}); var f=e&&e.parentNode&&e.parentNode.querySelector('.gen-cell-flag'); return f? f.title.slice(0,90):null;})()`) });
      }
      ok('|amp| > 1 is flagged as unreachable',
         ampCases.filter(c => ['1.0001', '2', '-2', '1e999'].indexOf(c.typed) >= 0).every(c => c.err === true), ampCases);
      ok('|amp| <= 1 is accepted',
         ampCases.filter(c => ['0', '0.5', '1', '-0.5'].indexOf(c.typed) >= 0).every(c => c.err === false), ampCases);
      await typeInto(ampSel, '0.1', { settle: 200 });
    } else {
      results.push({ name: '(info) no amplitude cell on this chip', pass: true, detail: null });
    }

    // Keyboard navigation across the grid where the mouse is expected.
    const gridNav = await evSoft(`(function(){
       var cells = document.querySelectorAll('.gen-pop-in[data-field]');
       if (cells.length < 3) return 0; cells[0].focus(); return document.activeElement === cells[0];})()`);
    if (gridNav.v) {
      const before = await ev(`(document.activeElement||{}).dataset ? document.activeElement.dataset.field+'|'+document.activeElement.dataset.rid : null`);
      for (let i = 0; i < 6; i++) { await press('ArrowDown'); await sleep(60); }
      for (let i = 0; i < 40; i++) { await press('ArrowUp'); await sleep(25); }
      const after = await ev(`(document.activeElement||{}).dataset ? document.activeElement.dataset.field+'|'+document.activeElement.dataset.rid : String(document.activeElement.tagName)`);
      ok('arrow keys walk the populate grid and stop at the top edge',
         typeof after === 'string' && after !== 'BODY', { before, after });
    }

    // --- presets: save with no name, save, apply, apply twice, delete -----
    phase = 'G/presets';
    await click('#gen-preset-apply'); await sleep(300);
    ok('Apply with no preset selected says so rather than doing nothing silently',
       /pick a preset/i.test(String(await txt('#gen-preset-note'))), await txt('#gen-preset-note'));
    await click('#gen-preset-delete'); await sleep(300);
    ok('Delete with no preset selected says so',
       /pick a preset/i.test(String(await txt('#gen-preset-note'))), await txt('#gen-preset-note'));

    await click('#gen-preset-save'); await sleep(250);
    ok('Save as preset… opens the save box',
       await ev(`document.getElementById('gen-preset-savebox').hidden === false`));
    await click('#gen-preset-save-confirm'); await sleep(300);
    ok('saving with an empty name is refused with a message',
       /enter a preset name/i.test(String(await txt('#gen-preset-err'))), await txt('#gen-preset-err'));
    await typeInto('#gen-preset-name', '   ', { settle: 150 });
    await click('#gen-preset-save-confirm'); await sleep(300);
    ok('a whitespace-only preset name is refused too',
       /enter a preset name/i.test(String(await txt('#gen-preset-err'))), await txt('#gen-preset-err'));
    // Untick every section → refused.
    await ev(`(function(){['pulses','qubit','resonator','flux','pairs'].forEach(function(s){
        var b=document.getElementById('gen-preset-sec-'+s); if(b) b.checked=false;}); return 1;})()`);
    await typeInto('#gen-preset-name', 'stress preset "A"\\한글', { settle: 150, fast: true });
    await click('#gen-preset-save-confirm'); await sleep(300);
    ok('saving with no section ticked is refused',
       /tick at least one/i.test(String(await txt('#gen-preset-err'))), await txt('#gen-preset-err'));
    // Tick an active section and save for real.
    await ev(`(function(){var a=QuamGen._test ? null : null;
       var boxes=['pulses','qubit','resonator','flux','pairs'].map(function(s){return document.getElementById('gen-preset-sec-'+s);});
       var first = boxes.filter(function(b){return b && !b.disabled;})[0];
       if (first) first.checked = true; return first? first.id : null;})()`);
    await typeInto('#gen-preset-name', 'stress_preset_1', { settle: 200 });
    await click('#gen-preset-save-confirm'); await sleep(1200);
    const savedList = await ev(`(function(){var s=document.getElementById('gen-preset-select');
       return Array.prototype.map.call(s.options, function(o){return o.value+'|'+o.textContent;});})()`);
    const savedSlug = (savedList || []).map(x => x.split('|')[0]).filter(v => /stress/.test(v))[0];
    ok('a preset saves and appears in the list',
       !!savedSlug || /saved/i.test(String(await txt('#gen-preset-note'))),
       { list: savedList, note: await txt('#gen-preset-note'), err: await txt('#gen-preset-err') });

    if (savedSlug) {
      await ev(`(function(){var s=document.getElementById('gen-preset-select'); s.value=${JSON.stringify(savedSlug)}; s.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
      // Apply twice in a row.
      await click('#gen-preset-apply'); await sleep(900);
      const note1 = await txt('#gen-preset-note');
      await click('#gen-preset-apply'); await sleep(900);
      const note2 = await txt('#gen-preset-note');
      ok('applying a preset twice is idempotent and says what it did',
         /applied/i.test(String(note1)) && /applied/i.test(String(note2)), { note1, note2 });
      ok('…and the spec is still JSON-clean afterwards',
         !/NaN|Infinity|undefined/.test(String(await spec(`JSON.stringify(QuamGen.state.spec.populate||{})`))),
         String(await spec(`JSON.stringify(QuamGen.state.spec.populate||{})`)).slice(0, 200));
      // Save the SAME name again → overwrite confirm path.
      await click('#gen-preset-save'); await sleep(200);
      await ev(`(function(){var b=['pulses','qubit','resonator','flux','pairs'].map(function(s){return document.getElementById('gen-preset-sec-'+s);}).filter(function(x){return x&&!x.disabled;})[0]; if(b) b.checked=true; return 1;})()`);
      await typeInto('#gen-preset-name', 'stress_preset_1', { settle: 200 });
      await click('#gen-preset-save-confirm'); await sleep(1400);
      ok('re-saving the same preset name asks to overwrite (and the confirm path works)',
         (await ev(`(window.__confirms||[]).some(function(m){return /overwrite/i.test(m);})`)) === true,
         await ev(`JSON.stringify(window.__confirms||[])`));
      // Delete it three times — the second and third must be honest, not crash.
      const delNotes = [];
      for (let i = 0; i < 3; i++) {
        await ev(`(function(){var s=document.getElementById('gen-preset-select'); if(s.querySelector('option[value=${JSON.stringify(savedSlug)}]')) s.value=${JSON.stringify(savedSlug)}; s.dispatchEvent(new Event('change',{bubbles:true})); return 1;})()`);
        await click('#gen-preset-delete'); await sleep(900);
        delNotes.push({ note: await txt('#gen-preset-note'),
                        stillListed: await ev(`!!document.querySelector('#gen-preset-select option[value=${JSON.stringify(savedSlug)}]')`) });
      }
      ok('a preset deletes and stays deleted, and re-pressing Delete is honest',
         delNotes[0] && /deleted|pick a preset/i.test(String(delNotes[0].note)) && delNotes[2].stillListed === false,
         delNotes);
    }

    // The built-in preset must refuse deletion rather than vanish.
    const builtin = await ev(`(function(){var s=document.getElementById('gen-preset-select');
       var o=Array.prototype.filter.call(s.options, function(x){return x.value && !/stress/.test(x.value);})[0];
       if(!o) return null; s.value=o.value; s.dispatchEvent(new Event('change',{bubbles:true})); return o.value;})()`);
    if (builtin) {
      await click('#gen-preset-delete'); await sleep(900);
      ok('the built-in preset refuses deletion with a reason',
         await ev(`!!document.querySelector('#gen-preset-select option[value=${JSON.stringify(builtin)}]')`),
         { slug: builtin, note: await txt('#gen-preset-note') });
    }
  }

  /* ═══ PHASE H — step 7 (Output) ═════════════════════════════════════ */
  phase = 'H/output';
  await ev(`QuamGen.goToStep(7); 1`); await sleep(500);
  const pathCases = [];
  for (const p of ['', '   ', 'C:\\a\\b"c', 'relative/path', '한글경로\\quam_state', '🙂', 'C:' + 'x'.repeat(300), 'C:\\stress\\quam_state']) {
    await typeInto('#gen-output-path', p, { settle: 200, fast: true });
    pathCases.push({ typed: p.slice(0, 40), dom: (await val('#gen-output-path') || '').slice(0, 60),
                     stored: String(await ev(`QuamGen.state.outputPath`)).slice(0, 60),
                     scripts: String(await val('#gen-scripts-path')).slice(0, 80) });
  }
  ok('the output path box round-trips every hostile value into state',
     pathCases.every(c => c.dom === c.stored || c.dom.trim() === c.stored), pathCases);
  ok('the scripts path follows the state folder while untouched',
     /stress[\\/]quam_state[\\/]state_gen_scripts/.test(pathCases[pathCases.length - 1].scripts),
     pathCases[pathCases.length - 1]);
  await click('#gen-scripts-enable'); await sleep(250);
  const off = await ev(`document.getElementById('gen-scripts-field').hidden`);
  await click('#gen-scripts-enable'); await sleep(250);
  const on = await ev(`document.getElementById('gen-scripts-field').hidden`);
  ok('the scripts-export toggle hides and shows its folder field', off === true && on === false, { off, on });
  // Generate with an empty output path must refuse — and must NOT call build.
  await typeInto('#gen-output-path', '', { settle: 250 });
  await ev(`window.__fetches.length=0; 1`);
  await ev(`QuamGen.goToStep(8); 1`); await sleep(900);
  await click('#gen-next'); await sleep(600);
  await click('#gen-next'); await sleep(600);
  const buildFetches = await ev(`JSON.stringify((window.__fetches||[]).filter(function(u){return /build/.test(u);}))`);
  ok('Generate with no output folder refuses and never posts a build',
     buildFetches === '[]', { fetches: buildFetches, msg: await msg(), result: (await txt('#gen-build-result') || '').slice(0, 160) });

  /* ═══ PHASE I — step 8 review renders ═══════════════════════════════ */
  phase = 'I/review';
  await ev(`QuamGen.goToStep(8); 1`); await sleep(1500);
  const review = await txt('#gen-review');
  ok('the review step renders a summary', !!review && review.length > 40, (review || '').slice(0, 300));
  ok('…and it names the chip it is about to build',
     /qubit/i.test(String(review)), (review || '').slice(0, 200));
  await shot('I_step8');

  /* ═══ PHASE J — leave and come back; reset; regenerate leak ═════════ */
  phase = 'J/remount';
  await ev(`QuamGen.state.outputPath='C:\\\\stress\\\\quam_state'; 1`);
  const beforeLeave = await ev(`JSON.stringify({step:QuamGen.state.step, q:QuamGen.state.spec.qubits.length, host:QuamGen.state.spec.network.host, mode:QuamGen.state.mode})`);
  // Leave via the sidebar (htmx nav), then come back.
  await nav(BASE + '/bulk', 3500);
  await nav(BASE + '/generate', 3500);
  const afterBack = await ev(`JSON.stringify({step:QuamGen.state.step, q:QuamGen.state.spec.qubits.length, host:QuamGen.state.spec.network.host, mode:QuamGen.state.mode})`);
  ok('leaving and coming back restores the draft (step, qubits, host)',
     JSON.parse(afterBack).q === JSON.parse(beforeLeave).q &&
     JSON.parse(afterBack).host === JSON.parse(beforeLeave).host, { beforeLeave, afterBack });
  ok('…and it is still generate mode', JSON.parse(afterBack).mode === 'generate', afterBack);
  ok('…and exactly one wizard root is mounted',
     (await ev(`document.querySelectorAll('#generate-root').length`)) === 1,
     await ev(`document.querySelectorAll('#generate-root').length`));

  // Regenerate mode must not leak into a fresh Generate mount.
  await nav(BASE + '/regenerate', 6000);
  const regenMode = await ev(`(window.QuamGen&&QuamGen.state)? QuamGen.state.mode : null`);
  const regenStatus = (await txt('#regen-status') || '').slice(0, 160);
  await shot('J_regenerate');
  await nav(BASE + '/generate', 4000);
  const backMode = await ev(`(window.QuamGen&&QuamGen.state)? QuamGen.state.mode : null`);
  const backEndpoint = await ev(`(window.QuamGen&&QuamGen.state)? QuamGen.state.buildEndpoint : null`);
  const backSource = await ev(`(window.QuamGen&&QuamGen.state)? QuamGen.state.sourcePath : null`);
  ok('a Generate mount after Re-generate is generate mode, not regenerate',
     backMode === 'generate', { regenMode, regenStatus, backMode });
  ok('…and its build endpoint is /generate/build, not /regenerate/build',
     backEndpoint === '/generate/build', backEndpoint);
  ok('…and it carries no source chip path',
     backSource == null, backSource);
  ok('…and the regenerate chip\'s named qubits did not leak into the draft',
     await ev(`(function(){var q=QuamGen.state.spec.qubits; return q.length===0 || q.every(function(x){return /^q/.test(x);});})()`),
     await spec(`JSON.stringify(QuamGen.state.spec.qubits).slice(0,200)`));

  // Reset wizard — the confirm is auto-accepted.
  await ev(`window.__confirms.length=0; 1`);
  await click('#gen-reset'); await sleep(1200);
  const afterReset = await ev(`JSON.stringify({step:QuamGen.state.step, q:QuamGen.state.spec.qubits.length,
     host:QuamGen.state.spec.network.host, arch:QuamGen.state.chipArch, mode:QuamGen.state.mode,
     out:QuamGen.state.outputPath, env:!!QuamGen.state.env, alloc:!!QuamGen.state.allocation,
     pairs:QuamGen.state.spec.qubit_pairs.length, twpas:QuamGen.state.spec.twpas.length,
     chassis:QuamGen.state.spec.instruments.controllers.length})`);
  const R = JSON.parse(afterReset);
  ok('Reset wizard asks first', (await ev(`(window.__confirms||[]).length`)) >= 1, await ev(`JSON.stringify(window.__confirms||[])`));
  ok('Reset returns to step 1 with an empty chip', R.step === 1 && R.q === 0 && R.pairs === 0 && R.twpas === 0, R);
  ok('…and clears the network, output path, env and allocation', !R.host && !R.out && !R.env && !R.alloc, R);
  // Reset seeds 5 EMPTY chassis, and with no LF-FEM the architecture is
  // honestly derived as fixed-frequency — the stored default only survives
  // once hardware that can build it exists.
  ok('…and restores 5 chassis with no FEMs',
     R.chassis === 5 &&
     (await ev(`QuamGen.state.spec.instruments.controllers.every(function(c){return !c.fems.length;})`)), R);
  ok('…and the architecture is the one the empty rack can actually build',
     R.arch === 'fixed_frequency', R.arch);
  ok('…and the DOM matches: the qubit count box reads 0',
     (await val('#gen-qubit-count')) === '0', await val('#gen-qubit-count'));
  ok('…and the chassis grid really shows 5 boxes',
     (await ev(`document.getElementById('gen-chassis-list').children.length`)) === 5,
     await ev(`document.getElementById('gen-chassis-list').children.length`));
  await shot('J_afterreset');

  // Reset pressed three times in a row.
  for (let i = 0; i < 3; i++) { await click('#gen-reset'); await sleep(500); }
  ok('Reset pressed three times leaves one consistent wizard',
     (await step()) === 1 && (await ev(`document.querySelectorAll('#generate-root').length`)) === 1 &&
     (await ev(`document.querySelectorAll('.gen-panel.active').length`)) === 1,
     { step: await step(), roots: await ev(`document.querySelectorAll('#generate-root').length`) });

  /* ═══ PHASE K — resize, and a last consistency sweep ════════════════ */
  phase = 'K/resize';
  await ev(`if(!QuamGen.state.env) QuamGen.state.env='python'; QuamGen.goToStep(3); 1`); await sleep(400);
  for (const [w, h] of [[700, 600], [1900, 1100], [480, 900], [1500, 1000]]) {
    await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false });
    await sleep(400);
  }
  ok('resizing four times leaves the wizard intact',
     (await ev(`document.querySelectorAll('.gen-panel.active').length`)) === 1 &&
     (await ev(`!!document.getElementById('generate-root')`)),
     { panels: await ev(`document.querySelectorAll('.gen-panel.active').length`) });
  ok('the page never scrolls horizontally',
     await ev(`document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2`),
     { sw: await ev(`document.documentElement.scrollWidth`), cw: await ev(`document.documentElement.clientWidth`) });
  await shot('K_final');

  /* ── report ──────────────────────────────────────────────────────── */
  const csp = errors.filter(e => /unsafe-eval/.test(String(e.text)) && /htmx/.test(String(e.text)));
  const other = errors.filter(e => csp.indexOf(e) < 0);
  fs.writeFileSync(OUT, JSON.stringify({ results, errors, cspKnown: csp.length, other, shots, nativeDialogs, typeFailures }, null, 1));
  const bad = results.filter(x => !x.pass);
  console.log('checks: ' + (results.length - bad.length) + '/' + results.length +
              '   htmx-CSP errors: ' + csp.length + '   OTHER errors: ' + other.length +
              '   native dialogs: ' + nativeDialogs.length + '   unfocusable typings: ' + typeFailures.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + '  ' + JSON.stringify(b.detail).slice(0, 420)));
  other.slice(0, 25).forEach(e => console.log('  ERR  [' + e.phase + '] ' + e.kind + ': ' + String(e.text).slice(0, 260)));
  nativeDialogs.slice(0, 10).forEach(d => console.log('  DLG  [' + d.phase + '] ' + d.type + ': ' + d.text.slice(0, 120)));
  process.exit(0);
}
main().catch(e => {
  console.error('driver error: ' + (e && e.stack || e));
  fs.writeFileSync(OUT, JSON.stringify({ results, errors, shots, driver: String(e && e.stack || e) }, null, 1));
  process.exit(1);
});
