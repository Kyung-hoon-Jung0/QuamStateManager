/* The two plain boxes on a REAL 20-qubit chip, not the 5Q rig.
 *
 * Customer, earlier in the same round: "지금 5Q는 너무 규모가 작아 CQT 칩도
 * 알아봐, 그리고 adaptive하게 해야해... 30, 50개 큐빗일수도있거든."
 *
 * This is where the per-keystroke cost lives: 300+ column headers on the Live
 * Edit grid and ~12,000 leaves in the Json tree. It measures the keystroke,
 * not just the answer. argv[2]=json out, argv[3]=cdp, argv[4]=base
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9407';
const BASE = process.argv[4] || 'http://127.0.0.1:5407';
const results = [];
const errors = [];
function ok(n, c, d) { results.push({ name: n, pass: !!c, detail: d === undefined ? null : d }); }

async function main() {
  const targets = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(res => ws.onopen = res);
  let id = 0; const pending = new Map();
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') errors.push(String((m.params.exceptionDetails.exception || {}).description || m.params.exceptionDetails.text));
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push((m.params.args || []).map(a => a.value || a.description || '').join(' '));
  };
  const send = (mm, p = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method: mm, params: p })); });
  const ev = async (x) => {
    const rr = await send('Runtime.evaluate', { expression: x, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) throw new Error(JSON.stringify(rr.result.exceptionDetails.exception).slice(0, 300));
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const typeChar = async (ch) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  };
  const rows = async () => ev(`(function(){var p=document.getElementById('sm-typeahead'); if(!p||p.hidden) return [];
    return Array.prototype.map.call(p.querySelectorAll('.sm-th-row'), function(r){return {t:(r.querySelector('.sm-th-label')||{}).textContent, fuzzy:r.classList.contains('sm-th-fuzzy'), note:r.classList.contains('sm-th-note')};});})()`);

  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  /* ── Live State Edit: 300+ headers ─────────────────────────────────── */
  await send('Page.navigate', { url: BASE + '/bulk' });
  await sleep(9000);
  const heads = await ev(`document.querySelectorAll('#table-pane th.bulk-col-head').length`);
  ok('the big chip really is big', heads > 100, { headers: heads });

  const SEL = '#bulk-search';
  await ev(`(function(){var e=document.querySelector('${SEL}'); e.focus(); e.value=''; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  // per-keystroke cost, measured in the page's own clock across the whole word
  const times = [];
  for (const ch of 'amplitude') {
    const t0 = await ev(`performance.now()`);
    await typeChar(ch);
    await sleep(70);
    const t1 = await ev(`performance.now()`);
    times.push(Math.round(t1 - t0));
  }
  ok('typing a nine-character word on a 300-column grid stays under a second per key',
     Math.max.apply(null, times) < 1000, { per_key_ms: times, headers: heads });
  ok('…and it found the amplitude columns',
     (await rows()).some(x => /ampl/i.test(x.t)), (await rows()).map(x => x.t).slice(0, 6));

  // the vocabulary cache: the scan used to run per keystroke over every header
  const cached = await ev(`(function(){var a=window.BulkTypeahead.vocab(); var b=window.BulkTypeahead.vocab(); return a===b;})()`);
  ok('the header scan is cached, not re-run per keystroke', cached === true, cached);

  await ev(`(function(){var e=document.querySelector('${SEL}'); e.focus(); e.value='amplitide'; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  await sleep(500);
  ok('a mistyped column on the big chip still finds it',
     (await rows()).some(x => x.fuzzy), (await rows()).map(x => x.t));

  /* ── Json Tree: ~12,000 leaves ─────────────────────────────────────── */
  await send('Page.navigate', { url: BASE + '/explorer' });
  await sleep(8000);
  const keys = await ev(`(function(){var v=window.TreeTypeahead.vocab(); return v?Object.keys(v).length:0;})()`);
  ok('the tree vocabulary is derived from the chip itself', keys > 50, { distinct_keys: keys });

  const ES = '#explorer-search';
  await ev(`(function(){var e=document.querySelector('${ES}'); e.focus(); e.value=''; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  const t2 = await ev(`performance.now()`);
  for (const ch of 'amplitude') { await typeChar(ch); await sleep(50); }
  await sleep(300);
  const t3 = await ev(`performance.now()`);
  ok('typing a word on a 20-qubit tree stays responsive', (t3 - t2) < 9000, { total_ms: Math.round(t3 - t2), keys: keys });
  ok('…and lists the chip\'s own amplitude keys',
     (await rows()).some(x => /ampl/i.test(x.t)), (await rows()).map(x => x.t).slice(0, 6));

  await ev(`(function(){var e=document.querySelector('${ES}'); e.focus(); e.value='amplitide'; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  await sleep(600);
  ok('a mistyped chip key on the big chip still finds it',
     (await rows()).some(x => x.fuzzy), (await rows()).map(x => x.t));

  // the worst case for the fuzzy pass: a long stem that matches nothing
  const t4 = await ev(`performance.now()`);
  await ev(`(function(){var e=document.querySelector('${ES}'); e.focus(); e.value='qqqqwwwweeeerrrr'; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  await sleep(400);
  const t5 = await ev(`performance.now()`);
  ok('a long stem that matches nothing does not stall the box',
     (t5 - t4) < 3000, { ms: Math.round(t5 - t4) });

  fs.writeFileSync(OUT, JSON.stringify({ results: results, errors: errors }, null, 1));
  const bad = results.filter(x => !x.pass);
  console.log('big-chip checks: ' + (results.length - bad.length) + '/' + results.length
              + '   errors: ' + errors.filter(e => !/unsafe-eval/.test(e)).length + ' (non-CSP)');
  results.forEach(r => console.log('  ' + (r.pass ? 'ok  ' : 'FAIL') + ' ' + r.name + '  ' + JSON.stringify(r.detail)));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); fs.writeFileSync(OUT, JSON.stringify({ results, errors, driver: String(e) }, null, 1)); process.exit(1); });
