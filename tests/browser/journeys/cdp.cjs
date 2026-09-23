/* Minimal Chrome DevTools Protocol driver for the journey tests (docs/205).
 *
 * No puppeteer: Node's built-in WebSocket against a Chrome started with
 *   chrome.exe --headless=new --remote-debugging-port=9333 --remote-allow-origins=*
 * Every click is a REAL mouse event (Input.dispatchMouseEvent), never a
 * synthetic .click() -- a synthetic click skips hit-testing, so it "works" on a
 * button hidden under an overlay, which is exactly what a journey must catch.
 */
'use strict';
const http = require('http');
const fs = require('fs');

const CDP_PORT = +(process.env.SM_CDP_PORT || 9333);
function put(url) {
  return new Promise((res, rej) => {
    const req = http.request(url, { method: 'PUT' }, r => {
      let d = ''; r.on('data', c => d += c); r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } });
    });
    req.on('error', rej); req.end();
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function open(url, w = 1600, h = 950) {
  const t = await put(`http://127.0.0.1:${CDP_PORT}/json/new?about:blank`);
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  let id = 0; const P = new Map(); const events = [];
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && P.has(m.id)) { P.get(m.id)(m); P.delete(m.id); } else if (m.method) events.push(m);
  };
  await new Promise(r => ws.onopen = r);
  const send = (method, params = {}) => new Promise(r => { const i = ++id; P.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async e => {
    const m = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (m.result && m.result.exceptionDetails) return 'EXC ' + JSON.stringify(m.result.exceptionDetails).slice(0, 300);
    return m.result && m.result.result && m.result.result.value;
  };
  await send('Runtime.enable'); await send('Page.enable'); await send('Log.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url });
  const host = new URL(url).host;
  for (let i = 0; i < 150; i++) { const s = await ev("location.host+'|'+document.readyState"); if (s === host + '|complete') break; await sleep(200); }
  const click = async (x, y) => {
    for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased'])
      await send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 });
  };
  const key = async (k, code, vk) => {
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: k, code, windowsVirtualKeyCode: vk });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk });
  };
  const shot = async f => { const r = await send('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(f, Buffer.from(r.result.data, 'base64')); };
  const close = async () => { try { ws.close(); } catch (e) {} await new Promise(r => http.get(`http://127.0.0.1:${CDP_PORT}/json/close/${t.id}`, () => r()).on('error', () => r())); };
  // JS exceptions + console errors since `mark`, minus the known CSP-eval noise
  // (htmx compiling hx-trigger filters with new Function; docs/175 records it).
  const errors = (mark = 0) => events.slice(mark).map(e => {
    if (e.method === 'Runtime.exceptionThrown') return 'EXC ' + ((e.params.exceptionDetails.exception || {}).description || e.params.exceptionDetails.text);
    if (e.method === 'Log.entryAdded' && e.params.entry.level === 'error') return 'LOG ' + e.params.entry.text;
    if (e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error') return 'CON ' + e.params.args.map(a => a.value || a.description || '').join(' ');
    return null;
  }).filter(m => m && !/EvalError|unsafe-eval|Content Security Policy|favicon/i.test(m)).map(m => m.slice(0, 240));
  return { send, ev, click, key, shot, close, sleep, events, errors };
}

module.exports = { open, sleep };
