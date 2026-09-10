/* One screenshot of a state a person has to READ correctly: the typo panel,
   where a guess must not look like a match. argv[2]=out png, argv[3]=cdp,
   argv[4]=base, argv[5]=what to type, argv[6]=selector */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9407';
const BASE = process.argv[4] || 'http://127.0.0.1:5407';
const TEXT = process.argv[5] || 'multiplzed';
const SEL = process.argv[6] || '#sidebar-filter-input';

async function main() {
  const targets = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(res => ws.onopen = res);
  let id = 0; const pending = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const send = (m, p = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method: m, params: p })); });
  const ev = async (x) => (await send('Runtime.evaluate', { expression: x, awaitPromise: true, returnByValue: true })).result.result.value;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  await send('Runtime.enable'); await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: BASE + (SEL === '#explorer-search' ? '/explorer' : SEL === '#bulk-search' ? '/bulk' : '/') });
  await sleep(4500);
  await ev(`(function(){var e=document.querySelector(${JSON.stringify(SEL)}); if(!e) return 0; e.focus(); e.value=${JSON.stringify(TEXT)}; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
  await sleep(700);
  const rows = await ev(`(function(){var p=document.getElementById('sm-typeahead'); if(!p||p.hidden) return 'panel hidden';
    return Array.prototype.map.call(p.querySelectorAll('.sm-th-row'), function(r){
      return (r.classList.contains('sm-th-fuzzsep')?'--- ':'') + (r.classList.contains('sm-th-fuzzy')?'~ ':'') +
             (r.querySelector('.sm-th-label')||{}).textContent + '   ' + ((r.querySelector('.sm-th-meta')||{}).textContent||'');}).join('\\n');})()`);
  console.log(rows);
  const s = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(OUT, Buffer.from(s.result.data, 'base64'));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
