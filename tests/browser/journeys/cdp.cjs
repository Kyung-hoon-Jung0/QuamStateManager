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

/* URL prefix (SM mounted under a reverse-proxy subpath, docs/226).
 * ONE knob: SM_BASE_URL = the full base a journey talks to, e.g.
 * http://127.0.0.1:5341/sm (printed by sm_qa_rigs/_tools/proxy/proxy_rig.sh).
 * The mount prefix is SM_URL_PREFIX when set, else the path of SM_BASE_URL.
 * A journey that takes `--base` hands it to setBase(), which derives the prefix
 * the same way. With neither set, every helper here is the identity and
 * open()/send() behave exactly as before. */
let BASE_URL = '';
let PREFIX = '';
const RIG = !!process.env.SM_BASE_URL;   // extra diagnostics only when a rig says where SM is
function _norm(p) {
  p = String(p || '');
  if (p === '/') p = '';
  while (p.length && p.charAt(p.length - 1) === '/') p = p.slice(0, -1);
  return p;
}
// Git Bash rewrites a POSIX-looking env value handed to node.exe: SM_URL_PREFIX=/sm
// arrives as 'C:/Program Files/Git/sm' (measured). A prefix is a URL path or nothing.
function _checkPrefix(p, src) {
  if (p && !/^(\/[A-Za-z0-9._~-]+)+$/.test(p))
    throw new Error(src + ' gives URL prefix ' + JSON.stringify(p) + ', not a URL path'
      + (/^[A-Za-z]:/.test(p) ? ' (Git Bash path conversion: export MSYS_NO_PATHCONV=1)' : ''));
  return p;
}
function setBase(url) {
  BASE_URL = url ? String(url).replace(/\/+$/, '') : '';
  PREFIX = '';
  if (process.env.SM_URL_PREFIX) PREFIX = _checkPrefix(_norm(process.env.SM_URL_PREFIX), 'SM_URL_PREFIX');
  else if (BASE_URL) { try { PREFIX = _norm(new URL(BASE_URL).pathname); } catch (e) { PREFIX = ''; } _checkPrefix(PREFIX, 'SM_BASE_URL'); }
  return BASE_URL;
}
setBase(process.env.SM_BASE_URL || '');
// the base URL for a journey that was handed a PORT (root: http://127.0.0.1:<port>)
function base(port) { return BASE_URL || ('http://127.0.0.1:' + port); }
// a journey that takes --base: an explicit value wins and sets the prefix too
function baseFrom(explicit, port) { return explicit ? setBase(explicit) : base(port); }
function prefix() { return PREFIX; }
// app-relative path of a location.pathname / pathname+search read in the page
function smPath(p) {
  if (!PREFIX || typeof p !== 'string') return p;
  if (p === PREFIX) return '/';
  if (p.indexOf(PREFIX + '/') === 0) return p.slice(PREFIX.length);
  if (p.indexOf(PREFIX + '?') === 0 || p.indexOf(PREFIX + '#') === 0) return '/' + p.slice(PREFIX.length);
  return p;
}
// an app route as the browser must request it (idempotent; '//x', 'http:..', 'x' untouched)
function smUrl(p) {
  if (!PREFIX || typeof p !== 'string' || p.charAt(0) !== '/' || p.charAt(1) === '/') return p;
  if (p === PREFIX || p.indexOf(PREFIX + '/') === 0 || p.indexOf(PREFIX + '?') === 0 || p.indexOf(PREFIX + '#') === 0) return p;
  return PREFIX + p;
}
// an unadapted journey must fail loudly, never silently test the proxy's root
function guard(url) {
  if (!BASE_URL || !PREFIX || typeof url !== 'string') return;
  let u, b;
  try { u = new URL(url); b = new URL(BASE_URL); } catch (e) { return; }
  if (u.origin !== b.origin) return;
  if (u.pathname === PREFIX || u.pathname.indexOf(PREFIX + '/') === 0) return;
  throw new Error('journey navigated outside the prefix: ' + url);
}

function put(url) {
  return new Promise((res, rej) => {
    const req = http.request(url, { method: 'PUT' }, r => {
      let d = ''; r.on('data', c => d += c); r.on('end', () => { try { res(JSON.parse(d)); } catch (e) { rej(e); } });
    });
    req.on('error', rej); req.end();
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

// opts.network: enable the Network domain BEFORE the first navigation, so
// requests() below sees the page's own load (a leak is usually made there).
async function open(url, w = 1600, h = 950, opts) {
  guard(url);
  const t = await put(`http://127.0.0.1:${CDP_PORT}/json/new?about:blank`);
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  let id = 0; const P = new Map(); const events = [];
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && P.has(m.id)) { P.get(m.id)(m); P.delete(m.id); } else if (m.method) events.push(m);
  };
  await new Promise(r => ws.onopen = r);
  const send0 = (method, params = {}) => new Promise(r => { const i = ++id; P.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const send = (method, params = {}) => { if (method === 'Page.navigate') guard(params && params.url); return send0(method, params); };
  const ev = async e => {
    const m = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true });
    if (m.result && m.result.exceptionDetails) return 'EXC ' + JSON.stringify(m.result.exceptionDetails).slice(0, 300);
    return m.result && m.result.result && m.result.result.value;
  };
  await send('Runtime.enable'); await send('Page.enable'); await send('Log.enable');
  if (opts && opts.network) await send('Network.enable');
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
    if (e.method === 'Log.entryAdded' && e.params.entry.level === 'error') return 'LOG ' + e.params.entry.text + (RIG && e.params.entry.url ? ' <' + e.params.entry.url + '>' : '');
    if (e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error') return 'CON ' + e.params.args.map(a => a.value || a.description || '').join(' ');
    return null;
  }).filter(m => m && !/EvalError|unsafe-eval|Content Security Policy|favicon/i.test(m)).map(m => m.slice(0, 240));
  // With opts.network: every same-origin request whose path is OUTSIDE the
  // prefix (a leak -- behind a real proxy it reaches the platform, not SM) and
  // every response >= 400, since `mark`. Empty at root (no prefix = no leak).
  const requests = (mark = 0) => {
    const out = { leaks: [], bad: [], total: 0 };
    let b = null; try { b = BASE_URL ? new URL(BASE_URL) : new URL(url); } catch (e) { b = null; }
    events.slice(mark).forEach(e => {
      if (e.method === 'Network.requestWillBeSent') {
        out.total++;
        let u = null; try { u = new URL(e.params.request.url); } catch (x) { return; }
        if (PREFIX && b && u.origin === b.origin && !(u.pathname === PREFIX || u.pathname.indexOf(PREFIX + '/') === 0))
          out.leaks.push(e.params.request.method + ' ' + u.pathname + u.search);
      } else if (e.method === 'Network.responseReceived' && e.params.response.status >= 400) {
        out.bad.push(e.params.response.status + ' ' + e.params.response.url);
      } else if (e.method === 'Network.loadingFailed' && !e.params.canceled) {
        out.bad.push('failed ' + e.params.errorText + ' ' + (e.params.type || ''));
      }
    });
    return out;
  };
  return { send, ev, click, key, shot, close, sleep, events, errors, requests };
}

module.exports = { open, sleep, base, baseFrom, setBase, prefix, smPath, smUrl, guard };
