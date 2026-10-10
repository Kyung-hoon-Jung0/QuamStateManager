/* v1.3.0 B1 P1: the Experiment Runner sends a TYPED interpreter path by POST.
 *
 * GET /generate/probe and GET /scheduler/effective-config now run only an
 * interpreter SM found or the person already chose (any page can make a
 * browser send a GET). The Runner's "Probe" and "Read config" buttons carry
 * the text of #sched-env-path, which may be a path typed a moment ago, so
 * they must POST it. Runs the shipped scheduler.js under jsdom.
 *
 *   node tests/v130_scheduler_typed_env_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

let fails = 0, n = 0;
function ok(c, m) { n++; if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }

const SRC = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'scheduler.js'), 'utf8');
const HTML = '<!doctype html><html><body><div id="sched-root">' +
  '<input id="sched-env-path"><button id="sched-env-probe-btn">Probe</button><span id="sched-env-probe-out"></span>' +
  '<button id="sched-read-config">Read config</button><div id="sched-config-out"></div><span id="sched-busy" hidden></span>' +
  '</div></body></html>';

const dom = new JSDOM(HTML, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:5050/scheduler' });
const w = dom.window;
const calls = [];
w.fetch = function (url, o) {
  calls.push({ url: String(url), method: (o && o.method) || 'GET', body: (o && o.body) || '' });
  return w.Promise.resolve({ ok: true, status: 200, json: () => w.Promise.resolve({}) });
};
w.eval(SRC);
w.document.dispatchEvent(new w.Event('DOMContentLoaded'));

const TYPED = 'D:\\envs\\typed lab\\python.exe';
const want = 'python=' + encodeURIComponent(TYPED);
w.document.getElementById('sched-env-path').value = TYPED;

calls.length = 0;
w.document.getElementById('sched-env-probe-btn').click();
const probe = calls.filter((c) => c.url.indexOf('/generate/probe') === 0);
ok(probe.length === 1, 'Probe asks the server once (got ' + JSON.stringify(calls) + ')');
ok(probe[0] && probe[0].method === 'POST' && probe[0].url === '/generate/probe' && probe[0].body === want,
   'Probe POSTs the typed path in the body, never in a GET query: ' + JSON.stringify(probe[0]));

calls.length = 0;
w.document.getElementById('sched-read-config').click();
const eff = calls.filter((c) => c.url.indexOf('/scheduler/effective-config') === 0);
ok(eff.length === 1, 'Read config asks the server once (got ' + JSON.stringify(calls) + ')');
ok(eff[0] && eff[0].method === 'POST' && eff[0].url === '/scheduler/effective-config' && eff[0].body === want,
   'Read config POSTs the typed path in the body: ' + JSON.stringify(eff[0]));

if (fails) { console.error(fails + ' failure(s)'); process.exit(1); }
console.log('v130_scheduler_typed_env_selfcheck: all checks passed (' + n + ' assertions)');
process.exit(0);   // the Runner page keeps its status poll alive
