/* The newcomer round: someone who has never seen SM, told only
 * "use the agent to calibrate".
 *
 * argv[2] = json out path, argv[3] = CDP port, argv[4] = base url, argv[5] = phase
 */
const fs = require('fs');
const OUT = process.argv[2];
const CDP = process.argv[3] || '9435';
const BASE = process.argv[4] || 'http://127.0.0.1:5435';
const PHASE = process.argv[5] || 'A';

const errors = [];
const notes = [];
function note(k, v) { notes.push({ k: k, v: v }); }

async function connect() {
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
  return { send, ev };
}

const sleep = ms => new Promise(res => setTimeout(res, ms));

async function main() {
  const { send, ev } = await connect();
  await send('Page.enable'); await send('Runtime.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  const shot = async (name) => {
    const s = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    const p = OUT.replace(/\.json$/, '_' + name + '.png');
    fs.writeFileSync(p, Buffer.from(s.result.data, 'base64'));
    return p;
  };
  const shotFull = async (name) => {
    const s = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
    const p = OUT.replace(/\.json$/, '_' + name + '.png');
    fs.writeFileSync(p, Buffer.from(s.result.data, 'base64'));
    return p;
  };
  const nav = async (path, wait) => { await send('Page.navigate', { url: BASE + path }); await sleep(wait || 3500); };

  const KEYS = { Enter: 13, Tab: 9, Escape: 27, ArrowDown: 40, ArrowUp: 38, Backspace: 8 };
  const press = async (key) => {
    const p = { type: 'rawKeyDown', key: key, windowsVirtualKeyCode: KEYS[key], nativeVirtualKeyCode: KEYS[key] };
    if (key === 'Enter' || key === 'Tab') { p.text = key === 'Enter' ? '\r' : '\t'; p.type = 'keyDown'; }
    await send('Input.dispatchKeyEvent', p);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: key, windowsVirtualKeyCode: KEYS[key] });
  };
  const typeChar = async (ch) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, unmodifiedText: ch, key: ch });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
  };
  const typeInto = async (sel, text) => {
    await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)}); if(!e) return 0; e.focus(); if('value' in e){e.value='';} else {e.textContent='';} e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
    for (const ch of text) { await typeChar(ch); await sleep(8); }
    await sleep(200);
  };

  const G = { send, ev, shot, shotFull, nav, press, typeChar, typeInto, note, sleep };
  await require(OUT.replace(/\.json$/, '_phase' + PHASE + '.cjs'))(G);

  fs.writeFileSync(OUT, JSON.stringify({ notes: notes, errors: errors }, null, 1));
  console.log('notes: ' + notes.length + '  console errors: ' + errors.length);
  errors.slice(0, 20).forEach(e => console.log('  ERR  ' + e.kind + ': ' + String(e.text).slice(0, 260)));
  process.exit(0);
}
main().catch(e => {
  console.error('driver error: ' + (e && e.stack || e));
  fs.writeFileSync(OUT, JSON.stringify({ notes: notes, errors: errors, driver: String(e && e.stack || e) }, null, 1));
  process.exit(1);
});
