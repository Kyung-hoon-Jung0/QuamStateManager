/* jsdom selfcheck: the sidebar container is inert exactly while the layout is
 * collapsed (closed off-canvas or collapsed on a wide window), whichever code
 * changes the class; the toggle in the top bar is never inert. Runs the real
 * SidebarInert block of app.js. Run: node tests/sidebar_inert_selfcheck.cjs
 * (driven by tests/test_focus_and_finite_json.py). */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const APP = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
function flush(ms) { return new Promise(function (r) { setTimeout(r, ms || 5); }); }

const start = APP.indexOf('window.SidebarInert = (function');
const end = APP.indexOf('})();', start);
if (start < 0 || end < 0) { console.error('FAIL: no SidebarInert block in app.js'); process.exit(1); }
const block = APP.slice(start, end + 5);

(async function () {
    const dom = new JSDOM('<!doctype html><body><header><button class="sidebar-toggle">t</button></header>'
        + '<div class="app-layout sidebar-collapsed"><aside id="sidebar"><button id="in">in</button></aside><main></main></div></body>',
        { url: 'http://localhost/', runScripts: 'outside-only' });
    const w = dom.window, d = w.document;
    w.eval(block);
    await flush(20);
    const sb = d.getElementById('sidebar'), layout = d.querySelector('.app-layout');
    ok(sb.hasAttribute('inert'), 'a layout that loads collapsed has an inert sidebar');
    ok(!d.querySelector('.sidebar-toggle').closest('[inert]'), 'the toggle is never inert');
    layout.classList.remove('sidebar-collapsed');
    await flush(20);
    ok(!sb.hasAttribute('inert'), 'an open sidebar is not inert');
    layout.classList.add('sidebar-collapsed');
    await flush(20);
    ok(sb.hasAttribute('inert'), 'closing the sidebar makes it inert again');
    w.close();
    if (fails) { console.error(fails + ' check(s) failed'); process.exit(1); }
    console.log('all checks passed');
})();
