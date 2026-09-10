/* Does a collapsed sidebar date group actually load when you open it?
 *
 * The stress round turned up 35 identical CSP EvalErrors from htmx, all from
 * `hx-trigger="toggle[this.open] once"` in _sidebar_tree_macros.html — an
 * htmx event FILTER, which htmx compiles with `new Function`, which this app's
 * CSP forbids (docs/120 ② found the same thing for `hx-on::` handlers).
 *
 * So: open one and look. argv[2] = json out, argv[3] = cdp port, argv[4] = base
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9407';
const BASE = process.argv[4] || 'http://127.0.0.1:5407';

async function main() {
  const targets = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = targets.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(res => ws.onopen = res);
  let id = 0; const pending = new Map(); const reqs = [];
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === 'Network.requestWillBeSent') reqs.push(m.params.request.url);
  };
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expr) => {
    const rr = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) throw new Error(JSON.stringify(rr.result.exceptionDetails.exception));
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(res => setTimeout(res, ms));
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Page.navigate', { url: BASE + '/' });
  await sleep(4000);

  const out = {};
  out.lazy_groups = await ev(`document.querySelectorAll('#sidebar [data-lazy-group]').length`);
  out.total_groups = await ev(`document.querySelectorAll('#sidebar details[data-tpath]').length`);
  if (!out.lazy_groups) {
    out.note = 'no lazy groups on this tree — nothing to measure';
    fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
    console.log(JSON.stringify(out)); process.exit(0);
  }
  out.before = await ev(`(function(){var d=document.querySelector('#sidebar [data-lazy-group]');
    return {open:d.open, entries:d.querySelectorAll('.tree-entry-click').length};})()`);
  reqs.length = 0;
  // a real click on the summary, the way a person opens it
  await ev(`(function(){var d=document.querySelector('#sidebar [data-lazy-group]');
    var s=d.querySelector('summary'); s.click(); return 1;})()`);
  await sleep(2500);
  out.after = await ev(`(function(){var d=document.querySelector('#sidebar [data-lazy-group]');
    return {open:d.open, entries:d.querySelectorAll('.tree-entry-click').length};})()`);
  out.group_requests = reqs.filter(u => /workspace\/tree\/group/.test(u)).length;
  out.verdict = out.after.open && out.after.entries > 0
    ? 'loads' : (out.after.open ? 'OPENED BUT EMPTY' : 'did not open');
  fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
  console.log(JSON.stringify(out, null, 1));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
