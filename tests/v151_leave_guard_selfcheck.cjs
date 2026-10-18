/* v1.5.1 SB-P2-8: "Leave site?" only when something would truly be lost.
 *
 * Every full navigation with a staged edit raised "Leave site?", although the
 * server checkpoints the tray's rows on disk (they survive a closed tab and a
 * restart). The guard now asks only when the tray's rows are NOT known to be
 * checkpointed (no data-tray-durable); typed-but-uncommitted grid cells keep
 * their own guards.
 *
 * The driver (tests/test_v151_leave_guard.py) renders the REAL full page twice:
 * a checkpointed tray, and one whose checkpoint writes are failing.
 *
 * Run: node tests/v151_leave_guard_selfcheck.cjs <fixture.json>
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) {
    console.log('SKIP: jsdom not installed');
    process.exit(2);
}
const FX = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');

let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

function leave(pageHtml) {
    const body = pageHtml.slice(pageHtml.indexOf('<body'));
    const dom = new JSDOM('<!doctype html><html>' + body.replace(/<script\b[\s\S]*?<\/script>/gi, '') + '</html>',
        { url: 'http://localhost/help', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window;
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
    w.fetch = () => new Promise(() => {});
    w.eval(APP);
    const tray = w.document.getElementById('pending-tray');
    const ev = new w.Event('beforeunload', { cancelable: true });
    w.dispatchEvent(ev);
    return { asked: ev.defaultPrevented, count: tray && tray.getAttribute('data-change-count') };
}

const durable = leave(FX.page_durable);
ok(durable.count === '1', 'fixture: the real page carries one staged edit');
ok(!durable.asked, 'a checkpointed tray: leaving asks nothing');
const failing = leave(FX.page_failing);
ok(parseInt(failing.count, 10) > 0, 'fixture: staged edits whose checkpoint is failing');
ok(failing.asked, 'a tray whose checkpoint is failing: leaving still asks');

console.log(fails ? ('FAILED: ' + fails + ' of ' + n) : 'DONE (' + n + ' assertions)');
process.exit(fails ? 1 : 0);
