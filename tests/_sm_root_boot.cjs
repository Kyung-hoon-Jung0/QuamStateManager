/* Boot the client's URL-prefix module into a jsdom harness (spec docs/226 §5.2).
 *
 * In a real page `sm-root.js` is the FIRST script of every full document: it
 * reads `<html data-root="…">` and defines `window.SM` (root, url, path) before
 * any first-party JS runs; under a prefix it also wraps fetch/history and
 * registers the `sm-root` htmx extension. A selfcheck that evaluates
 * first-party JS without it would see `window.SM` undefined -- so every
 * harness boots it exactly the way the page does.
 *
 *   const boot = require('./_sm_root_boot.cjs');
 *   boot(window)            // prefix = process.env.SM_TEST_URL_PREFIX || ''
 *   boot(window, '/sm')     // explicit prefix
 *   boot.install()          // FIRST statement of a harness: every JSDOM window
 *                           // the process creates is booted (see below)
 *
 * Mode switch: `SM_TEST_URL_PREFIX=/sm npm run selfcheck` runs every harness
 * under the prefix; unset, the boot is identity (data-root="" -> window.SM with
 * identity url/path, no wrapper, no hook -- sm-root.js returns early).
 *
 * `install()` exists because a line "after `global.window = window;`" reaches
 * only the harnesses written in that style (89 of the 234 that require jsdom on
 * 91c8aae); the rest run scripts inside jsdom's own realm (`runScripts`), build
 * a window per scenario, or build it in a helper module. install() replaces the
 * shared `require('jsdom').JSDOM` export with a subclass that boots each window
 * it constructs:
 *   - by default right after construction (jsdom fires DOMContentLoaded
 *     asynchronously, so this still precedes every DCL handler and every
 *     script a harness evaluates afterwards);
 *   - when the harness hands jsdom HTML with an INLINE executable <script> and
 *     `runScripts: 'dangerously'`, those scripts run DURING construction -- so
 *     a one-line boot script is injected as the first script of the document
 *     (where the page has sm-root.js) and removes itself after running.
 *     The harness's own `beforeParse` runs first (a fetch/history stub installed
 *     there is the "browser's" API that sm-root.js wraps, as in a real page).
 * Each window is booted exactly once. Under a prefix the harness page itself is
 * moved under it too (an http(s) `url` option, and reconfigure({url})): behind
 * the proxy http://localhost/bulk is http://localhost/sm/bulk, and code under
 * test that reads location.pathname must see that. Identity at root.
 *
 * install() also makes the harness see what Jinja renders for the ONE variable
 * this feature adds: a template read through fs.readFileSync(…templates/…,
 * 'utf8') has `{{ root }}` (and `{{ root|tojson }}`) replaced by the mode
 * prefix, so after the template rewrite `href="{{ root }}/x"` reads `/x` at
 * root and `/sm/x` under the prefix -- before any harness-side Jinja stripper
 * runs, and without touching the 57 template-reading call sites one by one.
 *
 * sm-root.js is C2's file. When it is missing this module THROWS -- it never
 * shims `window.SM`, because a shim would keep every selfcheck green while the
 * page itself has no module to load.
 *
 * Node-realm rule (docs/125): sm-root.js references only `window.*`, so it is
 * evaluated with `new Function('window', src)(window)` -- a bare `document` /
 * `history` / `fetch` identifier inside it would throw here, loudly.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const REPO = path.join(__dirname, '..');
const SM_ROOT_JS = path.join(REPO, 'quam_state_manager', 'web', 'static', 'sm-root.js');
// Directories whose .html files are treated as Jinja templates by the read hook.
// A pin may push a temp dir here to exercise the hook on a file that carries
// `{{ root }}` before the real templates do.
const TEMPLATE_DIRS = [path.join(REPO, 'quam_state_manager', 'web', 'templates')];

const INSTALLED = Symbol.for('sm-root-boot.installed');
const HOOK = '__smRootBoot__';
const booted = new WeakSet();
const _realReadFileSync = fs.readFileSync;
let _src = null;

// The mode prefix must already be in normalised form (spec §1.3). Git Bash
// rewrites an env value that looks like a POSIX path before a native program
// sees it -- `SM_TEST_URL_PREFIX=/sm node …` arrives as "C:/Program Files/Git/sm"
// -- so a malformed value is refused loudly instead of silently testing nonsense.
// Run with MSYS_NO_PATHCONV=1 (or MSYS2_ENV_CONV_EXCL=SM_TEST_URL_PREFIX) there.
function modePrefix() {
  const p = process.env.SM_TEST_URL_PREFIX || '';
  if (p && !/^(\/[A-Za-z0-9._~-]+)+$/.test(p)) {
    throw new Error('_sm_root_boot: SM_TEST_URL_PREFIX=' + JSON.stringify(p) + ' is not a normalised '
      + 'URL prefix like "/sm". Under Git Bash set MSYS_NO_PATHCONV=1 (the shell rewrote "/sm" into a Windows path).');
  }
  return p;
}

function source() {
  if (_src === null) {
    if (!fs.existsSync(SM_ROOT_JS)) {
      throw new Error('_sm_root_boot: ' + SM_ROOT_JS + ' not found -- C2\'s sm-root.js must be '
        + 'present (spec §4.2). The selfcheck boot never shims window.SM.');
    }
    _src = _realReadFileSync.call(fs, SM_ROOT_JS, 'utf8');
  }
  return _src;
}

function boot(window, prefix) {
  const p = prefix == null ? modePrefix() : prefix;
  window.document.documentElement.setAttribute('data-root', p);
  new Function('window', source())(window);           // sm-root.js references only window.*
  booted.add(window);
  return window.SM;
}

// SM_BOOT_TRACE=1 -> one stderr line per booted window saying which path booted it.
function trace(kind) {
  if (process.env.SM_BOOT_TRACE) process.stderr.write('[sm-root-boot] ' + kind + ' ' + require('path').basename(process.argv[1] || '') + '\n');
}

/* What Jinja renders for `root` in a template, for the prefix of this mode. */
function renderRoot(text, prefix) {
  const p = prefix == null ? modePrefix() : prefix;
  return text
    .replace(/\{\{\s*root\s*\|\s*tojson\s*\}\}/g, JSON.stringify(p))
    .replace(/\{\{\s*root\s*\}\}/g, p);
}

function isTemplatePath(p) {
  if (typeof p !== 'string' && !(p instanceof URL)) return false;
  const f = path.resolve(String(p instanceof URL ? p.pathname.replace(/^\/([A-Za-z]:)/, '$1') : p));
  if (!/\.html$/i.test(f)) return false;
  const lf = f.toLowerCase();
  return TEMPLATE_DIRS.some((d) => lf.startsWith(path.resolve(d).toLowerCase() + path.sep));
}

function isUtf8(opts) {
  const enc = typeof opts === 'string' ? opts : (opts && opts.encoding);
  return typeof enc === 'string' && /^utf-?8$/i.test(enc);
}

// Under a prefix the page itself lives under it: a harness page at
// http://localhost/bulk is, behind the proxy, http://localhost/sm/bulk. So in
// prefix mode a harness's http(s) `url` (constructor or reconfigure) is moved
// under the prefix -- that is what makes location.pathname reads in the code
// under test see what a real prefixed page sees. Identity at root, for
// about:/file: URLs, and for a URL already under the prefix.
function underPrefix(u) {
  const p = modePrefix();
  if (!p || !/^https?:\/\//i.test(u)) return u;
  let x;
  try { x = new URL(u); } catch (e) { return u; }
  const pn = x.pathname;
  if (pn === p || pn.startsWith(p + '/')) return u;
  x.pathname = p + pn;
  return x.href;
}

// An inline script that jsdom would execute while parsing (no src, JS type).
function hasInlineScript(html) {
  const re = /<script\b([^>]*)>/gi;
  let m;
  while ((m = re.exec(html))) {
    const attrs = m[1];
    if (/\bsrc\s*=/i.test(attrs)) continue;
    const t = /\btype\s*=\s*["']?([^"'\s>]+)/i.exec(attrs);
    if (t && !/^(text\/javascript|application\/javascript|module)$/i.test(t[1])) continue;
    return true;
  }
  return false;
}

// Put the boot script where the page has sm-root.js: first thing in <head>
// (or right after <html>, or after the doctype, or at the very start).
function injectBootScript(html) {
  const tag = '<script>window.' + HOOK + '(document.currentScript)</scr' + 'ipt>';
  const at = (re) => { const m = re.exec(html); return m ? m.index + m[0].length : -1; };
  let i = at(/<head\b[^>]*>/i);
  if (i < 0) i = at(/<html\b[^>]*>/i);
  if (i < 0) i = at(/^\s*<!doctype[^>]*>/i);
  if (i < 0) i = 0;
  return html.slice(0, i) + tag + html.slice(i);
}

boot.install = function install() {
  let jsdom;
  try { jsdom = require('jsdom'); } catch (e) { jsdom = null; }  // the harness's own require reports "jsdom not installed"
  if (!fs.readFileSync[INSTALLED]) {
    const hooked = function readFileSync(p, opts) {
      const out = _realReadFileSync.apply(fs, arguments);
      return (typeof out === 'string' && isUtf8(opts) && isTemplatePath(p)) ? renderRoot(out) : out;
    };
    hooked[INSTALLED] = true;
    fs.readFileSync = hooked;
  }
  if (!jsdom || jsdom.JSDOM[INSTALLED]) return !!jsdom;
  source();                                            // fail at install time, not mid-scenario
  const Base = jsdom.JSDOM;
  const JSDOM = class JSDOM extends Base {
    constructor(input, options) {
      const o = Object.assign({}, options);
      if (typeof o.url === 'string') o.url = underPrefix(o.url);
      let html = input;
      if (o.runScripts === 'dangerously' && typeof html === 'string' && hasInlineScript(html)) {
        const userBeforeParse = o.beforeParse;
        o.beforeParse = function (w) {
          if (typeof userBeforeParse === 'function') userBeforeParse.call(this, w);
          w[HOOK] = function (el) {
            try { delete w[HOOK]; } catch (e) { w[HOOK] = undefined; }
            if (el && el.parentNode) el.parentNode.removeChild(el);
            if (!booted.has(w)) { boot(w); trace('early'); }
          };
        };
        html = injectBootScript(html);
      }
      super(html, o);
      if (!booted.has(this.window)) { boot(this.window); trace(html === input ? 'late' : 'late-INJECTED-SCRIPT-DID-NOT-RUN'); }
    }

    reconfigure(settings) {
      if (settings && typeof settings.url === 'string') settings = Object.assign({}, settings, { url: underPrefix(settings.url) });
      return super.reconfigure(settings);
    }
  };
  JSDOM[INSTALLED] = true;
  jsdom.JSDOM = JSDOM;
  return true;
};

boot.renderRoot = renderRoot;
boot.isTemplatePath = isTemplatePath;
boot.hasInlineScript = hasInlineScript;
boot.injectBootScript = injectBootScript;
boot.underPrefix = underPrefix;
boot.isBooted = (w) => booted.has(w);
boot.TEMPLATE_DIRS = TEMPLATE_DIRS;
boot.SM_ROOT_JS = SM_ROOT_JS;

module.exports = boot;
