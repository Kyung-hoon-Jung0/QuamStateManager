/* Shared jsdom world for the Datasets > Trends view selfchecks
 * (trends_render_queue / trends_data_click / trends_view). It mounts the
 * SHIPPED shell (templates/_trends_data.html, its Jinja placeholders filled)
 * and runs the shell's own inline script against the SHIPPED app.js
 * (window.DatasetTrends), with:
 *   - fetch answering /trends/series from a payload the test controls;
 *   - _plotlyRender recording every draw (and giving the element Plotly's
 *     .on() registry);
 *   - htmx.ajax recorded (the Parameter Differences request, a run click);
 *   - IntersectionObserver recorded, so a test can fire a scroll burst.
 * Node-realm eval does not see window properties as bare globals, so all of
 * it is evaluated INSIDE the window (w.eval).
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.join(__dirname, '..');
const TPL = path.join(ROOT, 'quam_state_manager', 'web', 'templates', '_trends_data.html');
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
const APP = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
const SQ = fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8');

const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
async function until(fn, ms) { const t = Date.now(); while (!fn() && Date.now() - t < (ms || 3000)) await tick(5); await tick(30); }
/* drain the microtask queue WITHOUT letting a macrotask (timer) run */
async function microtasks(n) { for (let i = 0; i < (n || 200); i++) await null; }

function shellHtml(id) {
  const src = fs.readFileSync(TPL, 'utf8').replace(/\{#[\s\S]*?#\}/g, '');
  const i = src.lastIndexOf('<script>');
  const markup = src.slice(0, i)
    .replace('{{ series_url }}', '/trends/series?experiment=E' + (id || ''))
    .replace('{{ params_url }}', '/trends/param-diff?experiment=E' + (id || ''))
    .replace('{{ experiment }}', 'E').replace("{{ qubit or '' }}", '');
  const script = src.slice(i + '<script>'.length, src.indexOf('</script>', i));
  if (/\{\{|\{%/.test(markup + script)) throw new Error('an unfilled Jinja placeholder is left in the shell');
  return { markup, script };
}

function world() {
  const dom = new JSDOM('<!doctype html><html><body><div id="table-pane"><div id="trends-content"></div></div>'
    + '<div id="inspector-pane"></div></body></html>',
    { url: 'http://localhost/trends', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  const answers = [];           // queued fetch answers for /trends/series
  const fetches = [];
  w.fetch = function (url) {
    if (!/\/trends\/series/.test(url)) return new Promise(function () {});
    fetches.push(url);
    const a = answers.shift();
    if (!a) return new Promise(function () {});
    if (a.error) return Promise.reject(new Error(a.error));
    return Promise.resolve({ ok: a.status === undefined || a.status < 400, status: a.status || 200,
                             json: function () { return Promise.resolve(JSON.parse(JSON.stringify(a.body))); } });
  };
  w.eval(SQ);
  w.eval(APP);
  const draws = [];
  w._plotlyRender = function (el, data, layout, config) {
    el.data = data; el.layout = layout;
    el.__handlers = {};
    el.on = function (ev, fn) { (el.__handlers[ev] = el.__handlers[ev] || []).push(fn); };
    draws.push({ el: el, data: data, layout: layout, connected: el.isConnected,
                 name: data[data.length - 1].name });
    return Promise.resolve(el);
  };
  const ajax = [];
  w.htmx = { ajax: function (verb, url, opts) { ajax.push({ verb: verb, url: url, opts: opts }); return Promise.resolve(); } };
  const ios = [];
  w.IntersectionObserver = class {
    constructor(cb) { this.cb = cb; this.targets = []; ios.push(this); }
    observe(t) { this.targets.push(t); }
    unobserve() {}
    disconnect() {}
  };
  w.PlotHost = { observe: function () {} };
  return {
    w, dom, answers, fetches, draws, ajax, ios,
    /* swap a shell into #trends-content and run its inline script, as htmx does */
    mount(id) {
      const s = shellHtml(id);
      w.document.getElementById('trends-content').innerHTML = s.markup;
      w.eval(s.script);
      return w.document.getElementById('trends-view');
    },
  };
}

/* a /trends/series payload: n runs, the given series ({q, m, v}) */
function payload(n, series, extra) {
  const runs = [];
  for (let i = 0; i < n; i++) runs.push([1000 + i, Date.UTC(2026, 8, 1, 0, i) , 'kk:' + (1000 + i)]);
  return Object.assign({ v: 'v1', ver: 1, experiment: 'E', qubit: null, n_runs: n, undated: 0,
                         incomplete: 0, indexing: false, runs: runs, series: series,
                         fig_keys: [], fig_runs: [] }, extra || {});
}
function seriesOf(tag, count, n, fn) {
  const out = [];
  for (let s = 0; s < count; s++) {
    const v = [];
    for (let i = 0; i < n; i++) v.push(fn ? fn(s, i) : s + i * 0.1);
    out.push({ q: 'q' + s, m: tag + s, v: v });
  }
  return out;
}

module.exports = { world, payload, seriesOf, tick, until, microtasks, shellHtml, ROOT, STATIC };
