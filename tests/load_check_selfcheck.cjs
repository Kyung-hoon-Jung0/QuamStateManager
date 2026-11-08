const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function wait(ms) { return new Promise((r) => setTimeout(r, ms || 0)); }

const dom = new JSDOM(
  '<!doctype html><html><body>'
  + '<div id="status-bar"></div>'
  + '<button class="js-load-check-btn">\u2713 Load check</button>'
  + '<span class="js-load-check-status"></span>'
  + '</body></html>',
  { url: 'http://localhost/bulk', pretendToBeVisual: true }
);
const { window } = dom;
global.window = window;
global.document = window.document;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.navigator = window.navigator;
global.location = window.location;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
const store = new Map();
const st = { getItem: (k) => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
global.localStorage = st; global.sessionStorage = st;
window.localStorage = st; window.sessionStorage = st;

window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} };
global.htmx = window.htmx;

window._navigateToExplorerPath = function (p) { window.__lastNav = p; };
let envRequiredEvents = 0;
document.addEventListener('sm-env-required', () => { envRequiredEvents += 1; });

let calls = 0;
window.fetch = global.fetch = function () {
  calls += 1;
  if (calls === 1) {
    return new Promise((resolve) => {
      setTimeout(() => resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ ok: true, lib_versions: { quam: '0.6.0' } })
      }), 20);
    });
  }
  if (calls === 2) {
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({
        ok: false,
        exc_type: 'AttributeError',
        detail: "object has no attribute 'f_01'",
        links: [{ path: 'qubits.qA1.f_01', label: 'f_01' }]
      })
    });
  }
  return Promise.resolve({
    ok: false,
    status: 409,
    json: () => Promise.resolve({
      ok: false,
      error: 'env_required',
      project: 'alpha',
      message: 'Choose env',
      open_url: ''
    })
  });
};

let appJs = fs.readFileSync(path.join(STATIC, 'load_check.js'), 'utf8');
const mut = process.env.LOADCHECK_MUTATION || '';
const mutations = {
  no_disable: ['b.disabled = true;', 'b.disabled = false;'],
  no_checking: ["b.textContent = 'Checking\u2026';", "b.textContent = 'Idle';"],
  no_restore: ['setRunning(false);', 'setRunning(true);'],
  no_ok: ["setStatus('ok', 'Loads OK", "setStatus('fail', 'Wrong"],
  no_detail: ["var detail = esc((data && data.detail)", "var detail = esc((data && data.missing)"],
  no_env: ["new CustomEvent('sm-env-required'", "new CustomEvent('wrong-event'"],
  no_link_nav: ['if (p && window._navigateToExplorerPath) window._navigateToExplorerPath(p);', 'if (p) window.__lastNav = "";']
};
if (mut) {
  const [from, to] = mutations[mut];
  if (!appJs.includes(from)) throw new Error('Mutation target missing: ' + mut);
  appJs = appJs.replace(from, to);
}
window.eval(appJs);

(async function () {
  const btn = document.querySelector('.js-load-check-btn');
  const status = document.querySelector('.js-load-check-status');
  ok(typeof window.runLoadCheck === 'function', 'runLoadCheck exported');

  const p1 = window.runLoadCheck(btn);
  ok(btn.disabled, 'button disabled while checking');
  ok(btn.textContent === 'Checking\u2026', 'button shows Checking while running');
  await p1;
  ok(!btn.disabled, 'button re-enabled after check');
  ok(status.textContent === 'Loads OK (quam 0.6.0)' && status.classList.contains('is-ok'), 'green ok version rendered');

  await window.runLoadCheck(btn);
  ok(status.textContent.includes("AttributeError: object has no attribute 'f_01'"), 'exception type and detail rendered verbatim');
  const link = status.querySelector('.load-check-link');
  ok(!!link, 'failure result renders a path link');
  if (link) {
    link.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    ok(window.__lastNav === 'qubits.qA1.f_01', 'path link navigates to Json tree path');
  }

  await window.runLoadCheck(btn);
  ok(envRequiredEvents === 1, 'env_required emits sm-env-required event');

  if (fails) process.exit(1);
  console.log('load_check_selfcheck ok');
  process.exit(0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });

