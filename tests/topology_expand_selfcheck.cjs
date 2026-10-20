// Exercise real rendered pages, spark markup, CSS, and shipped map scripts.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = process.env.SM_UI_SOURCE_ROOT || path.join(__dirname, '..', 'quam_state_manager', 'web');
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const source = name => fs.readFileSync(path.join(root, 'static', name), 'utf8');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
let failures = 0, checks = 0;
async function check(name, fn) {
  checks++;
  try { await fn(); console.log('ok - ' + name); }
  catch (e) { failures++; console.error('FAIL: ' + name + '\n' + e.stack); }
}
function world(component = false, touch = false) {
  const dom = new JSDOM(component ? fixture.component : fixture.page,
    {runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/'});
  const w = dom.window, d = w.document;
  const style = d.createElement('style'); style.textContent = source('style.css'); d.head.append(style);
  w.matchMedia = () => ({matches: touch, addListener() {}, addEventListener() {}});
  Object.defineProperty(w.HTMLElement.prototype, 'offsetWidth', {get() { return this.tagName === 'BUTTON' ? 40 : 0; }});
  w._detailReparents = 0;
  const append = w.Node.prototype.appendChild;
  w.Node.prototype.appendChild = function (node) {
    if (node.isConnected && node.matches && node.matches('.topo-card-popup, .topo-pair-popup')) w._detailReparents++;
    return append.call(this, node);
  };
  w._detailInspects = [];
  w.htmx = {ajax(method, url) { w._detailInspects.push(url); }, process() {}, on() {}};
  w.fetch = url => Promise.resolve({ok: true, text: () => Promise.resolve(fixture.sparks),
    json: () => Promise.resolve(fixture.topo)});
  w.eval(source('app.js') + '\n' + source('topo-graph.js') + '\n' + source('component-map.js') + '\n' + source('chip-status.js'));
  if (component) w.ComponentMap.mount(d.querySelector('#component-map'));
  else w.ChipStatus.mount({topo: fixture.topo, historyCount: 2, rawWiring: {},
    defaultThresholds: {}, diagFindings: [], metricMeta: {}});
  return w;
}
function event(w, el, type, args = {}) {
  el.dispatchEvent(new w.MouseEvent(type, {bubbles: true, ...args}));
}
function key(w, el, value, extra = {}) {
  const e = new w.KeyboardEvent('keydown', {key: value, bubbles: true, cancelable: true, ...extra});
  el.dispatchEvent(e); return e.defaultPrevented;
}
function button(d) { const b = d.querySelector('.topo-expand'); assert.ok(b, 'Expand is available'); return b; }
function panel(d) { return d.querySelector('.topo-card-popup, .topo-pair-popup, .cm-popup:not([hidden])'); }
(async () => {
  for (const component of [false, true]) {
    const w = world(component), d = w.document;
    await wait(80);
    const selector = component ? '[data-cm]' : '[data-hero-qubit]';
    const node = d.querySelector(selector);
    await check((component ? 'component' : 'hero') + ' hover only offers Expand', async () => {
      event(w, node, 'mouseenter'); await wait(350);
      assert.equal(panel(d), null, 'hover must not open details');
      assert.equal(button(d).textContent, 'Expand');
      if (component) assert.equal(w.getComputedStyle(d.querySelector('.cm-edge-hit')).pointerEvents, 'stroke');
      assert.equal(button(d).tagName, 'BUTTON');
      for (const child of node.children) if (child.tagName.toLowerCase() === 'title')
        assert.ok(child.textContent.trim(), 'a node keeps a non-empty native title');
      assert.ok(button(d).getAttribute('aria-label').startsWith('Expand '));
    });
    await check('Expand fits above the stone without covering its neighbour', async () => {
      node.getBoundingClientRect = () => ({left: 200, right: 272, top: 200, bottom: 272, width: 72, height: 72});
      button(d).getBoundingClientRect = () => ({width: 60, height: 28});
      w.TopoGraph.positionDetail(button(d), node);
      if (!node.matches('[data-cm^="p:"]')) {
        assert.equal(button(d).style.left, '206px'); assert.equal(button(d).style.top, '167px');
      }
      node.getBoundingClientRect = () => ({left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0});
    });
    await check('grace allows reaching Expand and then dismisses', async () => {
      event(w, node, 'mouseleave'); await wait(80); assert.ok(button(d));
      event(w, button(d), 'mouseenter'); await wait(260); assert.ok(button(d));
      event(w, button(d), 'mouseleave'); await wait(300);
      assert.equal(d.querySelector('.topo-expand'), null);
    });
    await check('keyboard focus offers Expand and Tab reaches it', async () => {
      node.focus(); assert.ok(button(d)); key(w, node, 'Tab');
      assert.equal(d.activeElement, button(d));
    });
    await check('Expand opens a single anchored dialog; Escape restores focus', async () => {
      button(d).click(); await wait(60);
      assert.ok(panel(d)); assert.equal(panel(d).getAttribute('role'), 'dialog');
      assert.ok(panel(d).contains(d.activeElement));
      assert.equal(d.querySelector('.topo-expand'), null);
      assert.equal(node.getAttribute('aria-expanded'), 'true');
      assert.ok(panel(d).style.left.endsWith('px'));
      assert.equal(w._detailReparents, 0, 'lazy trends preserve the focused panel node');
      const controls = Array.from(panel(d).querySelectorAll('a[href], button, [tabindex]:not([tabindex="-1"])')).filter(e => e.offsetWidth || e.offsetHeight);
      controls[controls.length - 1].focus();
      assert.ok(key(w, d.activeElement, 'Tab'), 'Tab wraps inside the dialog');
      assert.ok(key(w, d.activeElement, 'Tab', {shiftKey: true}), 'Shift+Tab wraps inside the dialog');
      event(w, d.querySelectorAll(selector)[1], 'mouseenter');
      assert.equal(d.querySelector('.topo-expand'), null, 'hover cannot replace an open panel');
      key(w, d.activeElement, 'Escape');
      assert.equal(panel(d), null); assert.equal(d.activeElement, node);
      assert.equal(node.getAttribute('aria-expanded'), 'false');
    });
    await check('outside and X close; opening does not immediately close', async () => {
      event(w, node, 'mouseenter'); button(d).click(); assert.ok(panel(d));
      event(w, d.body, 'click'); assert.equal(panel(d), null);
      event(w, node, 'mouseenter'); button(d).click(); assert.ok(panel(d));
      panel(d).querySelector('.topo-popup-close').click(); assert.equal(panel(d), null);
    });
    if (component) await check('component details retain the inspector action', async () => {
      event(w, node, 'mouseenter'); button(d).click();
      const action = panel(d).querySelector('.cm-popup-hint');
      assert.equal(action.tagName, 'BUTTON'); action.click();
      assert.equal(panel(d), null); assert.equal(w._detailInspects.length, 1);
    });
    if (!component) {
      await check('values share a compact label-value grid; chart is separate and bounded', async () => {
        event(w, node, 'mouseenter'); button(d).click(); await wait(70);
        const p = panel(d), values = p.querySelector('.topo-detail-values');
        assert.ok(values, 'metric grid is independent of trends');
        assert.equal(w.getComputedStyle(values).display, 'grid');
        for (const row of values.children) {
          assert.equal(row.children.length, 2);
          assert.ok(row.children[0].matches('.topo-prop-label'));
          assert.ok(row.children[1].matches('.topo-prop-value'));
          assert.equal(w.getComputedStyle(row.children[1]).textAlign, 'left');
        }
        const chart = p.querySelector('.topo-spark-chart');
        assert.ok(chart && !values.contains(chart));
        assert.equal(w.getComputedStyle(chart).gridColumn, '1 / -1');
        assert.match(w.getComputedStyle(chart.parentElement).gridTemplateColumns, /1fr/);
        assert.equal(w.getComputedStyle(chart).maxHeight, '84px');
        assert.equal(w.getComputedStyle(chart.querySelector('svg')).maxHeight, '48px');
        assert.match(chart.textContent, /\u00b5s/);
        const axes = Array.from(p.querySelectorAll('.topo-spark-chart')).map(e => e.textContent).join(' ');
        assert.match(axes, /GHz/); assert.match(axes, /%/);
        assert.match(chart.textContent, /Earlier[\s\S]*Latest/);
        key(w, d.activeElement, 'Escape');
      });
      await check('pair hover also requires Expand', async () => {
        const pair = d.querySelector('[data-hero-pair]');
        event(w, pair, 'mouseenter'); assert.equal(panel(d), null);
        button(d).click(); assert.ok(d.querySelector('.topo-pair-popup'));
        const section = d.querySelector('.topo-pair-popup .topo-popup-section');
        assert.equal(w.getComputedStyle(section).display, 'grid');
        assert.equal(w.getComputedStyle(section.querySelector('.topo-popup-row')).gridTemplateColumns, 'subgrid');
        key(w, d.activeElement, 'Escape'); assert.equal(panel(d), null);
      });
    }
    await check('scroll keeps the surface anchored; offscreen nodes dismiss', async () => {
      event(w, node, 'mouseenter');
      d.dispatchEvent(new w.Event('scroll')); assert.ok(button(d));
      button(d).click();
      panel(d).dispatchEvent(new w.Event('scroll')); assert.ok(panel(d));
      node.getBoundingClientRect = () => ({top: -100, bottom: -10, left: 20, right: 60});
      d.dispatchEvent(new w.Event('scroll')); assert.equal(panel(d), null);
      node.getBoundingClientRect = () => ({top: 0, bottom: 40, left: 0, right: 40});
    });
    await check('background polling preserves details', async () => {
      event(w, node, 'mouseenter'); button(d).click();
      const polling = d.createElement('div'); d.body.append(polling);
      d.dispatchEvent(new w.CustomEvent('htmx:beforeSwap', {detail: {target: polling}}));
      assert.ok(panel(d)); key(w, d.activeElement, 'Escape');
    });
    await check('navigation tears down panel and trap', async () => {
      event(w, node, 'mouseenter'); button(d).click();
      d.dispatchEvent(new w.CustomEvent('htmx:beforeSwap', {detail: {target: d.querySelector('#table-pane')}}));
      assert.equal(panel(d), null); assert.equal(d.querySelector('.topo-expand'), null);
    });
    w.close();
  }
  await check('standalone report dialogs trap focus without app.js helpers', async () => {
    const w = world(true), d = w.document; await wait(80);
    w.trapFocus = undefined; w.htmx = undefined;
    const n = d.querySelector('[data-cm]'); event(w, n, 'mouseenter'); button(d).click();
    assert.ok(panel(d).contains(d.activeElement));
    assert.equal(panel(d).querySelector('.cm-popup-hint'), null);
    assert.ok(key(w, d.activeElement, 'Tab'));
    assert.ok(key(w, d.activeElement, 'Tab', {shiftKey: true}));
    key(w, d.activeElement, 'Escape'); assert.equal(panel(d), null);
    assert.equal(d.activeElement, n); w.close();
  });
  await check('touch pulse maps retain their filter action in details', async () => {
    const w = world(true, true), d = w.document; await wait(80);
    d.querySelector('#component-map').setAttribute('data-cm-pick', '1');
    let picked = null; w.pulsePickOwner = id => { picked = id; };
    const n = d.querySelector('[data-cm="q:q1"]');
    event(w, n, 'click'); button(d).click();
    panel(d).querySelector('.cm-popup-hint').click();
    assert.equal(picked, 'q1'); assert.equal(panel(d), null); w.close();
  });
  await check('touch tap offers Expand without inspecting; second tap opens', async () => {
    const w = world(false, true), d = w.document;
    let inspected = 0; w._inspectQubit = () => inspected++;
    const n = d.querySelector('[data-hero-qubit]'); event(w, n, 'click');
    assert.equal(panel(d), null); button(d).click(); assert.ok(panel(d));
    await wait(460); assert.equal(inspected, 0); w.close();
  });
  console.log(checks + ' checks, ' + failures + ' failures');
  if (!failures) console.log('topology_expand_selfcheck: all checks passed');
  process.exit(failures ? 1 : 0);
})();
