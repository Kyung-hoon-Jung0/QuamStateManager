/* How wide does the typeahead panel actually need to be?
 *
 * The stress screenshot showed the meta clipped ("198 ru…") and a horizontal
 * scrollbar inside the panel. Measure the real rows on the real archive rather
 * than picking a width by eye. argv[2]=cdp port, argv[3]=base
 */
const CDP = process.argv[2] || '9407';
const BASE = process.argv[3] || 'http://127.0.0.1:5407';

async function main() {
  const targets = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(res => ws.onopen = res);
  let id = 0; const pending = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => {
    const rr = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) throw new Error(JSON.stringify(rr.result.exceptionDetails.exception));
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(res => setTimeout(res, ms));
  await send('Runtime.enable'); await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: BASE + '/' });
  await sleep(3500);

  const out = { stems: {} };
  for (const stem of ['ampl', 'num', 'freq', 'wait', 'a', 'reset']) {
    await ev(`(function(){var e=document.querySelector('#sidebar-filter-input'); e.focus(); e.value=${JSON.stringify(stem)}; e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
    await sleep(450);
    out.stems[stem] = await ev(`(function(){
      var p=document.getElementById('sm-typeahead');
      if(!p||p.hidden) return null;
      var w=p.getBoundingClientRect().width;
      var need=0, rows=[];
      p.querySelectorAll('.sm-th-row').forEach(function(r){
        var l=r.querySelector('.sm-th-label'), m=r.querySelector('.sm-th-meta');
        var n=(l?l.scrollWidth:0)+(m?m.scrollWidth:0)+ 8 /*gap*/ + 18 /*padding*/;
        if(n>need) need=n;
        rows.push([(l||{}).textContent, l?l.scrollWidth:0, m?m.scrollWidth:0]);
      });
      return {panel:Math.round(w), scrollW:Math.round(p.scrollWidth), needed:Math.round(need),
              overflowX: p.scrollWidth > p.clientWidth + 1,
              widest: rows.sort(function(a,b){return (b[1]+b[2])-(a[1]+a[2]);})[0]};})()`);
  }
  out.sidebar_width = await ev(`getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width') || (document.getElementById('sidebar')||{}).offsetWidth`);
  out.root_font = await ev(`getComputedStyle(document.documentElement).fontSize`);
  console.log(JSON.stringify(out, null, 1));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
