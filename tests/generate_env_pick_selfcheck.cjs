'use strict';
// The wizard's automatic env pick is deterministic: the remembered (server
// selected) env when it probes usable, else the first usable env in discovery
// order -- never whichever probe happens to answer first.
const fs = require('fs');
const path = require('path');
const assert = require('assert');
let JSDOM;
try { ({JSDOM} = require('jsdom')); } catch (_) { process.exit(2); }
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_generate.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'quam_state_manager/web/static/generate.js'), 'utf8');
const tick = w => new Promise(r => w.setTimeout(r, 0));

async function world(envs, selected, usable, order) {
  const dom = new JSDOM('<body>' + html + '</body>', {runScripts: 'outside-only', url: 'http://localhost/'});
  const w = dom.window;
  w.NumberInput = {fit() {}, attach() {}, format() {}, strip(s) { return String(s || ''); }};
  w.armPlainResize = function () {};
  w.renderInstrumentWiring = function () {};
  const pend = {};
  const reply = b => Promise.resolve({json: () => Promise.resolve(b)});
  w.fetch = function (url) {
    if (url.startsWith('/generate/envs')) {
      return reply({envs: envs.map(p => ({name: p, python: p})), selected});
    }
    if (url.startsWith('/generate/probe?python=')) {
      const py = decodeURIComponent(url.split('=')[1]);
      return new Promise(res => { pend[py] = () => res({json: () => Promise.resolve(
        usable[py] ? {usable: true, versions: {}} : {usable: false, missing: ['quam']})}); });
    }
    if (url.startsWith('/generate/lab-code-dir')) return reply({folder: null});
    return new Promise(() => {});
  };
  w.eval(js);
  await tick(w); await tick(w); await tick(w);
  for (const py of order) { if (pend[py]) pend[py](); await tick(w); await tick(w); }
  const env = w.QuamGen.state.env;
  dom.window.close();
  return env;
}

(async function () {
  const envs = ['a', 'b', 'c'];
  const all = {a: true, b: true, c: true};
  // no remembered env: discovery order, whatever the probe order
  for (const order of [['a', 'b', 'c'], ['c', 'b', 'a'], ['b', 'c', 'a']]) {
    assert.strictEqual(await world(envs, null, all, order), 'a', 'first in discovery order (' + order + ')');
  }
  // an unusable earlier env is skipped deterministically
  assert.strictEqual(await world(envs, null, {a: false, b: true, c: true}, ['c', 'b', 'a']), 'b');
  // the remembered env wins when it is usable, even if it answers last
  assert.strictEqual(await world(envs, 'c', all, ['a', 'b', 'c']), 'c', 'remembered env kept');
  // the remembered env unusable: the deterministic fallback
  assert.strictEqual(await world(envs, 'c', {a: true, b: true, c: false}, ['b', 'a', 'c']), 'a',
    'unusable remembered env falls back to discovery order');
  console.log('env pick selfcheck passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
