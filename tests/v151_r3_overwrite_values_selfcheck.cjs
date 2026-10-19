/* R3 MINOR 10a: the actual Keep mine push declares the replaced values.
 * Real app.js under jsdom; driven by tests/test_v151_r3_overwrite_values.py. */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const w = new JSDOM('<!doctype html><body><div id="status-bar"></div></body>',
    { url: 'http://localhost/bulk', runScripts: 'outside-only' }).window;
w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
w.fetch = () => new Promise(() => {});
const posts = [];
w.htmx = { ajax: (method, url) => { posts.push(url); return Promise.resolve(); },
    trigger() {}, process() {}, on() {}, config: {} };
w.eval(fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager/web/static/app.js'), 'utf8'));
w.SyncControl = null;
let n = 0, fails = 0;
function ok(cond, msg) { n++; if (!cond) { fails++; console.error('FAIL: ' + msg); } }

w.eval('_keepMinePush({live_hash: "whole", replaced_hash: "values"})');
ok(posts[0] && posts[0].includes('expect_replaced_hash=values'), 'declares the replaced values: ' + posts[0]);
ok(posts[0] && !posts[0].includes('expect_live_hash='), 'not the whole-document hash: ' + posts[0]);
// a preflight from a server without the replaced set keeps the old declaration
w.eval('_keepMinePush({live_hash: "whole"})');
ok(posts[1] && posts[1].includes('expect_live_hash=whole'), 'an older preflight keeps its hash: ' + posts[1]);

console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
process.exit(fails ? 1 : 0);
