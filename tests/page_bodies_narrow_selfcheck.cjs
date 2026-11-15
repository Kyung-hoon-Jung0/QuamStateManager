'use strict';
if (!process.argv[2]) {
  console.error('Usage: node tests/page_bodies_narrow_selfcheck.cjs <rendered-page.html> (missing rendered-page.html argument)');
  process.exit(1);
}
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const { JSDOM } = require('jsdom');
const splitSource = fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/split.min.js'), 'utf8');
const page = fs.readFileSync(process.argv[2], 'utf8')
  .replace(/<script src="[^"]*"><\/script>/g, '');
let n = 0;
function eq(actual, expected, why) { n++; assert.deepEqual(actual, expected, why); }
function world(width, realSplit = false) {
  const builds = [];
  const dom = new JSDOM(page, {
    url: 'http://localhost/workbench', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(w) {
      w.innerWidth = width;
      w.localStorage.setItem('quam_workbench_sizes', '[30,70]');
      w.setInterval = w.setTimeout = () => 1;
      w.fetch = () => Promise.resolve({ json: () => Promise.resolve({}) });
      if (realSplit) {
        w.eval(splitSource);
        return;
      }
      w.Split = (selectors, opts) => {
        builds.push(opts);
        const els = selectors.map(s => w.document.querySelector(s));
        const gutter = w.document.createElement('div');
        gutter.className = 'gutter';
        els[0].after(gutter);
        let sizes = opts.sizes;
        function setSizes(s) { sizes = s; els.forEach((el, i) => { el.style.width = s[i] + '%'; }); }
        setSizes(sizes);
        return { setSizes, getSizes: () => sizes, destroy() {
          gutter.remove(); els.forEach(el => el.removeAttribute('style'));
        } };
      };
    }
  });
  const w = dom.window;
  return { w, builds, el: s => w.document.querySelector(s),
    css: (s, p) => w.getComputedStyle(w.document.querySelector(s)).getPropertyValue(p),
    resize(v) { w.innerWidth = v; w.dispatchEvent(new w.Event('resize')); },
    view(v) { w.document.querySelector('[data-view="' + v + '"]').click(); }
  };
}
for (const width of [400, 700, 1440]) {
  const t = world(width);
  const narrow = width <= 700;
  eq(t.el('body').hasAttribute('data-narrow'), narrow, 'breakpoint at boot');
  eq(t.builds.length, narrow ? 0 : 1, 'Split only owns desktop widths');
  eq(t.css('#wb-bar', 'flex-wrap'), 'wrap', 'toolbar wraps');
  eq(t.css('#wb-link', 'flex-wrap'), 'wrap', 'status bar wraps');
  eq(t.css('#wb-bar input', 'max-width'), '100%', 'address input stays inside bar');
  eq(t.css('#wb-link .wb-link-chip', 'overflow-wrap'), 'anywhere', 'long chip path wraps');
  eq(t.css('#wb-link-load', 'white-space'), 'normal', 'long load label wraps');
  eq(t.css('#wb-nudge', 'flex-wrap'), 'wrap', 'notification wraps');
  eq(t.css('#wb-watch', 'overflow-wrap'), 'anywhere', 'watch path wraps');
  eq(t.css('#wb-fallback', 'overflow-wrap'), 'anywhere', 'long fallback URL wraps');
  eq(t.css('#panes', 'flex-direction'), narrow ? 'column' : 'row', 'stack or desktop row');
  if (narrow) {
    eq(t.css('#panes', 'overflow-y'), 'auto', 'stack is reachable by visible vertical scroll');
    for (const s of ['#wb-left', '#wb-right']) {
      eq(t.css(s, 'width'), '100%', 'both frames use the full width');
      eq(t.el(s).style.width, '', 'no stale Split width');
      eq(t.css(s, 'min-height'), '320px', 'each stacked frame stays usable');
    }
    t.view('qb'); eq(t.css('#wb-right', 'display'), 'none', 'left-only view');
    t.view('sm'); eq(t.css('#wb-left', 'display'), 'none', 'right-only view');
    t.view('split'); eq(t.css('#wb-left', 'display'), 'block', 'split restores left');
    eq(t.css('#wb-right', 'display'), 'block', 'split restores right');
    t.resize(1440);
    eq(t.builds.length, 1, 'restores desktop splitter on resize');
  }
  eq(t.el('body').hasAttribute('data-narrow'), false, 'wide state');
  eq(t.css('#panes', 'flex-direction'), 'row', 'wide is a row');
  eq(t.el('#wb-left').style.width, '30%', 'saved ratio restored');
  t.view('sm'); t.resize(400);
  eq(t.el('#wb-left').style.cssText, '', 'desktop collapse styles cleared');
  eq(t.css('#wb-left', 'display'), 'none', 'single-view choice survives resize');
  t.view('split');
  eq(t.css('#wb-left', 'width'), '100%', 'return to stacked split');
  eq(t.el('.gutter'), null, 'desktop gutter removed');
  t.resize(1440);
  eq(t.el('#wb-left').style.width, '30%', 'ratio survives both breakpoints');
  t.w.close();
}
// Exercise the shipped Split.js destroy/rebuild, including exact breakpoint
// edges and repeated crossings. The stub above isolates application state.
const real = world(1440, true);
const savedWidth = real.el('#wb-left').style.width;
assert.match(savedWidth, /30%/); n++;
for (const width of [700, 701, 400, 1440, 700, 701]) {
  real.resize(width);
  const narrow = width <= 700;
  eq(real.el('body').hasAttribute('data-narrow'), narrow, 'real Split breakpoint');
  eq(real.el('#wb-left').style.width, narrow ? '' : savedWidth, 'real Split clears and restores width');
  eq(real.el('.gutter') !== null, !narrow, 'real Split removes and rebuilds gutter');
  eq(real.css('#panes', 'flex-direction'), narrow ? 'column' : 'row', 'real Split layout survives crossing');
}
real.view('sm'); real.resize(700);
eq(real.css('#wb-left', 'display'), 'none', 'real Split preserves single view');
real.resize(701);
eq(real.el('#wb-left').style.width, '0px', 'real Split restores desktop single view');
real.view('split');
eq(real.el('#wb-left').style.width, savedWidth, 'real Split preserves split ratio after single view');
real.w.close();
console.log('DONE (' + n + ' assertions)');
