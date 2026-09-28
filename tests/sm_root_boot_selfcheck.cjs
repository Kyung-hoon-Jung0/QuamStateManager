/* Pins for tests/_sm_root_boot.cjs (spec docs/226 §5.2): the selfcheck harnesses
 * boot sm-root.js the way a real page does, in every way a harness builds a window.
 *
 * Runs in both modes: `node tests/sm_root_boot_selfcheck.cjs` (root) and
 * `SM_TEST_URL_PREFIX=/sm node tests/sm_root_boot_selfcheck.cjs` (prefix).
 * Every expectation is written against PREFIX, so each mode checks its own truth.
 *
 * What would go red:
 *  - boot() not setting data-root            -> every "data-root" / "SM.root" line
 *  - install() not booting after construction -> B/C/D/E/F (window.SM missing)
 *  - no early boot for inline scripts          -> A/A2 (inline script ran before SM)
 *  - boot script not removing itself           -> A "scripts.length"
 *  - booting twice                             -> A "THE one"
 *  - template-read hook missing / wrong        -> H
 *  - harness page URL not moved under prefix   -> I (prefix mode)
 */
'use strict';
const boot = require('./_sm_root_boot.cjs');
boot.install();
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');

const PREFIX = process.env.SM_TEST_URL_PREFIX || '';
let fails = 0;
function ok(c, m) { if (c) console.log('ok - ' + m); else { fails++; console.log('FAIL - ' + m); } }

function smOk(w, label) {
  ok(w.document.documentElement.getAttribute('data-root') === PREFIX, label + ': <html data-root> === mode prefix ' + JSON.stringify(PREFIX));
  ok(w.SM && w.SM.root === PREFIX, label + ': window.SM.root === mode prefix');
  ok(w.SM && w.SM.url('/x') === PREFIX + '/x' && w.SM.path(PREFIX + '/x') === '/x', label + ': SM.url / SM.path agree with the mode');
}

// 0. install(): one subclass, idempotent, jsdom API intact
const Base = Object.getPrototypeOf(JSDOM);
ok(Base && Base.name === 'JSDOM' && JSDOM.name === 'JSDOM' && JSDOM !== Base, '0 require("jsdom").JSDOM is the booting subclass of the real JSDOM');
boot.install();
ok(require('jsdom').JSDOM === JSDOM, '0 install() twice keeps ONE subclass (no double wrap)');
const d0 = new JSDOM('<p>x</p>');
ok(d0 instanceof Base && d0 instanceof JSDOM, '0 instanceof the real JSDOM is preserved');
ok(typeof JSDOM.fragment === 'function' && JSDOM.fragment('<b>y</b>').firstChild.tagName === 'B', '0 statics are inherited');
ok(d0.window.document.querySelector('p').textContent === 'x', '0 the harness HTML parses unchanged');

// A. runScripts:'dangerously' + an INLINE script: it runs during construction,
//    so the boot must already have happened when it runs (sm-root.js is the
//    first script of the page).
const dA = new JSDOM('<!doctype html><html><head><script>window.__seen = (typeof window.SM === "object") ? window.SM : null; window.__root = document.documentElement.getAttribute("data-root");</script></head><body></body></html>',
  { runScripts: 'dangerously' });
ok(!!dA.window.__seen, 'A inline <script> saw window.SM while the document was parsing');
ok(dA.window.__seen === dA.window.SM, 'A ... and it is THE window.SM (booted exactly once)');
ok(dA.window.__root === PREFIX, 'A ... and data-root was already set when it ran');
ok(dA.window.document.scripts.length === 1, 'A the injected boot script removed itself (1 script left, the harness\'s)');
ok(!('__smRootBoot__' in dA.window), 'A the one-shot boot hook is gone from window');
smOk(dA.window, 'A');

// A2. fragment HTML (no <html>/<head>), a fetch stub from the harness's own
//     beforeParse: the stub is the "browser's fetch" that sm-root.js wraps.
const calls = [];
const dA2 = new JSDOM('<div id="x"></div><script>window.fetch("/field/edit"); window.__seen = !!window.SM;</script>',
  { runScripts: 'dangerously', beforeParse(w) { w.fetch = function (u) { calls.push(u); }; } });
ok(dA2.window.__seen === true, 'A2 fragment HTML: the inline script saw window.SM');
ok(calls.length === 1 && calls[0] === PREFIX + '/field/edit', 'A2 the beforeParse fetch stub received ' + JSON.stringify(PREFIX + '/field/edit') + ' (prefixed once, identity at root): ' + JSON.stringify(calls));
ok(dA2.window.document.getElementById('x') !== null, 'A2 the harness markup is intact');

// A3. only a JSON data <script>: nothing executes during parse, no injection
const dA3 = new JSDOM('<script type="application/json" id="d">{"a":1}</script>', { runScripts: 'dangerously' });
ok(dA3.window.document.scripts.length === 1 && dA3.window.document.getElementById('d').textContent === '{"a":1}', 'A3 a JSON <script> is left alone');
smOk(dA3.window, 'A3');
ok(boot.hasInlineScript('<script src="/static/app.js"></script>') === false && boot.hasInlineScript('<script>1</script>') === true
   && boot.hasInlineScript('<script type="module">1</script>') === true && boot.hasInlineScript('<script type="text/template">x</script>') === false,
   'A3 inline-script detection: src/json/template no, classic/module yes');

// B. runScripts:'dangerously', script evaluated after construction
const dB = new JSDOM('<p></p>', { runScripts: 'dangerously' });
ok(dB.window.eval('typeof window.SM.path') === 'function', 'B window.eval after construction sees window.SM');
smOk(dB.window, 'B');

// C. runScripts:'outside-only'
const dC = new JSDOM('<p></p>', { runScripts: 'outside-only' });
ok(dC.window.eval('window.SM.root') === PREFIX, 'C outside-only window.eval sees window.SM');

// D. Node realm: global.window + eval (the `global.window = window;` harness style)
const dD = new JSDOM('<p></p>', { url: 'http://localhost/' });
global.window = dD.window;
ok(eval('window.SM.url("/q")') === PREFIX + '/q', 'D Node-realm eval sees window.SM');

// E. vm.runInThisContext
ok(vm.runInThisContext('window.SM.path(window.SM.url("/q"))') === '/q', 'E vm.runInThisContext sees window.SM');

// F. new Function('window', src)(window)
ok(new Function('window', 'return window.SM.root')(dD.window) === PREFIX, 'F new Function(window) sees window.SM');

// G. mode-specific: identity at root, wrappers under the prefix
const dG = new JSDOM('<p></p>', { url: 'http://localhost/start' });
const wG = dG.window;
if (!PREFIX) {
  ok(!Object.prototype.hasOwnProperty.call(wG.history, 'pushState') && !Object.prototype.hasOwnProperty.call(wG.history, 'replaceState'),
     'G root: history.pushState/replaceState are the originals (no wrapper installed)');
  wG.history.pushState(null, '', '/diff');
  ok(wG.location.pathname === '/diff', 'G root: pushState("/diff") lands on /diff');
} else {
  wG.history.pushState(null, '', '/diff');
  ok(wG.location.pathname === PREFIX + '/diff', 'G prefix: pushState("/diff") lands on ' + PREFIX + '/diff');
  wG.history.replaceState(null, '', PREFIX + '/qubits');
  ok(wG.location.pathname === PREFIX + '/qubits', 'G prefix: an already-prefixed URL is not prefixed twice');
}
const explicit = boot(new JSDOM('<p></p>').window, '/other');
ok(explicit.root === '/other', 'G boot(window, "/other") honours an explicit prefix');

// I. the harness page itself lives under the prefix (as a proxied page does)
const dI = new JSDOM('<p></p>', { url: 'http://localhost/bulk?x=1#h' });
ok(dI.window.location.pathname === PREFIX + '/bulk' && dI.window.location.search === '?x=1' && dI.window.location.hash === '#h',
   'I a harness page at /bulk sits at ' + JSON.stringify(PREFIX + '/bulk') + ' (query and hash kept): ' + dI.window.location.href);
ok(new JSDOM('<p></p>', { url: 'http://localhost' + PREFIX + '/qubits' }).window.location.pathname === PREFIX + '/qubits',
   'I a page URL already under the prefix is not prefixed twice');
ok(new JSDOM('<p></p>', { url: 'http://localhost/' }).window.location.pathname === PREFIX + '/', 'I the app root "/" sits at ' + JSON.stringify(PREFIX + '/'));
ok(new JSDOM('<p></p>', { url: 'about:blank' }).window.location.href === 'about:blank', 'I about:blank is untouched');
dI.reconfigure({ url: 'http://localhost/pulses' });
ok(dI.window.location.pathname === PREFIX + '/pulses', 'I reconfigure({url}) lands under the prefix too');
ok(dI.window.SM.path(dI.window.location.pathname) === '/pulses', 'I SM.path(location.pathname) is the app route in both modes');

// H. templates: what Jinja renders for `root`
ok(boot.renderRoot('href="{{ root }}/x" v={{root|tojson}} y="{{  root  }}/y"', '/sm') === 'href="/sm/x" v="/sm" y="/sm/y"', 'H renderRoot under /sm');
ok(boot.renderRoot('href="{{ root }}/x" v={{ root | tojson }}', '') === 'href="/x" v=""', 'H renderRoot at root');
ok(boot.renderRoot('{{ root_path }} {{ roots }}', '/sm') === '{{ root_path }} {{ roots }}', 'H other variables (root_path, roots) untouched');
const TPL_DIR = path.join(__dirname, '..', 'quam_state_manager', 'web', 'templates');
ok(boot.isTemplatePath(path.join(TPL_DIR, 'base.html')) && boot.isTemplatePath(TPL_DIR.replace(/\\/g, '/') + '/_generate.html'),
   'H a real template path is recognised (either slash style)');
ok(!boot.isTemplatePath(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js')), 'H a static .js is not a template');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'smboot_'));
try {
  boot.TEMPLATE_DIRS.push(tmp);
  fs.writeFileSync(path.join(tmp, 't.html'), '<a href="{{ root }}/datasets?q=1" hx-get="{{ root }}/qubits">');
  ok(fs.readFileSync(path.join(tmp, 't.html'), 'utf8') === '<a href="' + PREFIX + '/datasets?q=1" hx-get="' + PREFIX + '/qubits">',
     'H fs.readFileSync(template, "utf8") returns the rendered root');
  ok(fs.readFileSync(path.join(tmp, 't.html'), { encoding: 'utf-8' }).indexOf('{{') < 0, 'H ... also with {encoding: "utf-8"}');
  ok(Buffer.isBuffer(fs.readFileSync(path.join(tmp, 't.html'))), 'H a Buffer read is untouched');
  fs.writeFileSync(path.join(tmp, 't.js'), 'x = "{{ root }}"');
  ok(fs.readFileSync(path.join(tmp, 't.js'), 'utf8') === 'x = "{{ root }}"', 'H a non-.html file is untouched');
} finally {
  boot.TEMPLATE_DIRS.pop();
  fs.rmSync(tmp, { recursive: true, force: true });
}

console.log(fails ? ('FAIL ' + fails) : 'all ok');
process.exit(fails ? 1 : 0);
