'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..', 'quam_state_manager', 'web');
const read = f => fs.readFileSync(path.join(root, 'static', f), 'utf8');
const dom = new JSDOM('<div id="qualibrate-running-slot"></div><div id="topo-health-tiles"></div>',
  {runScripts: 'outside-only', url: 'http://localhost/'});
const w = dom.window;
w.fetch = () => new Promise(() => {});
w.htmx = {process() {}, ajax() {}};
w.eval(read('app.js'));
const loadError = {target: Object.assign(w.document.createElement('div'), {id:'table-pane'}),
  xhr: {status:400, responseText:'<div class="load-failed-panel">Cannot open this folder</div>'}, shouldSwap:false};
w.document.dispatchEvent(new w.CustomEvent('htmx:beforeSwap', {detail:loadError}));
assert(loadError.shouldSwap && !loadError.isError, 'failed current-chip loads show their reason');
const slot = w.document.getElementById('qualibrate-running-slot');
function sample(html) {
  const detail = {target: slot, xhr: {status: 200, responseText: html}, serverResponse: html, shouldSwap: true};
  slot.dispatchEvent(new w.CustomEvent('htmx:beforeSwap', {bubbles: true, detail}));
  if (detail.shouldSwap) slot.innerHTML = html;
}
sample('<div>running</div>');
sample('');
assert(slot.textContent.includes('running'), 'one miss must retain the banner');
sample('<div>running</div>');
sample(''); sample('');
assert(slot.textContent.includes('running'), 'a hit resets consecutive misses');
sample('');
assert.equal(slot.textContent, '', 'three misses hide the banner');
const base = fs.readFileSync(path.join(root, 'templates', 'base.html'), 'utf8');
const css = read('ui-kit.css');
const failures = [];
function pin(name, check) {
  try { check(); } catch (error) { failures.push(name + ': ' + error.message); }
}
pin('landing only, reserved space', () => {
  assert(!base.includes('id="qualibrate-running-slot"'), 'global slot covers other pages');
  for (const template of ['_landing_shell.html', '_landing_welcome.html']) {
    const html = fs.readFileSync(path.join(root, 'templates', template), 'utf8');
    assert(html.includes("{% include '_qualibrate_running_slot.html' %}"), template);
  }
  const style = w.document.createElement('style');
  style.textContent = css;
  w.document.head.appendChild(style);
  slot.className = 'ui-running-overlay';
  const computed = w.getComputedStyle(slot);
  assert(!['fixed', 'absolute', 'sticky'].includes(computed.position), 'banner stays in flow');
  // An empty slot takes no room (it left a 10rem gap above every landing);
  // a notice is capped and scrolls inside the cap.
  assert(computed.maxHeight && computed.maxHeight !== 'none', 'notices are capped');
  const rule = (css.match(/\.ui-running-overlay\s*\{[^}]*\}/) || [''])[0];
  assert(rule && !/(^|[^-])height\s*:/.test(rule.replace(/max-height/g, '')), 'no reserved height when empty');
  assert.equal(computed.overflow, 'auto', 'multiple notices scroll inside reserved space');
});
for (const endpoint of ['/qualibrate/dismiss-running', '/qualibrate/use-location']) {
  pin(endpoint + ' clears immediately', () => {
    function complete(successful, path = endpoint, verb = 'post') {
      const form = w.document.createElement('form');
      form.setAttribute('hx-post', path);
      slot.appendChild(form);
      form.dispatchEvent(new w.CustomEvent('htmx:afterRequest', {bubbles:true, detail:{
        elt:form, successful, requestConfig:{path, verb}, xhr:{status:successful ? 200 : 500}
      }}));
      form.remove();
    }
    sample('<div>running</div>'); sample(''); sample('');
    complete(false);
    assert(slot.textContent.includes('running'), 'failed actions retain the notice');
    complete(true, '/unrelated');
    assert(slot.textContent.includes('running'), 'unrelated actions retain the notice');
    complete(true, endpoint, 'get');
    assert(slot.textContent.includes('running'), 'only posts clear the notice');
    complete(true);
    assert.equal(slot.textContent, '', 'successful action clears before any poll');
    assert.equal(slot._runningMisses, 0, 'successful action resets hysteresis');
    sample('<div>running again</div>'); sample(''); sample('');
    assert(slot.textContent.includes('running again'), 'later notices retain hysteresis');
    sample('');
  });
}
assert.deepEqual(failures, [], failures.join('\n'));
w.eval(read('topo-graph.js'));
w.eval(read('chip-status.js'));
w.ChipStatus.mount({topo: {nodes: [{id:'q1'}], edges: [], summary:{}}, rawWiring:{}, defaultThresholds:{}, diagFindings:[], metricMeta:{}});
const tiles = [...w.document.querySelectorAll('#topo-health-tiles .topo-health-tile')];
for (const label of ['qubits below spec', 'CZ below spec']) {
  const tile = tiles.find(t => t.textContent.includes(label));
  assert(tile && tile.textContent.includes('—') && tile.textContent.includes('no data yet'), label);
}
console.log('offline3 passed');
dom.window.close();
