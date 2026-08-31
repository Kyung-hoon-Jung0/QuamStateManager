/* docs/141 4am -- the capped tree search spends its budget SHALLOWEST-FIRST.
 *
 * Reported at a customer site: searching "analog" showed ONE slot under
 * ports.analog_outputs and cut the rest (scrolling showed every slot fine).
 * Reproduced on the PJ 20Q chip: 868 matches -- once an ancestor KEY matches,
 * every descendant matches by path -- and the 4b cap took the first 150 in
 * FLAT (depth-first) order, which never left the first slot. The notice was
 * present and honest; the budget was simply spent on 150 leaves of one branch
 * instead of the levels the user was looking for.
 *
 * Pins: with an ancestor-key match whose subtree exceeds the cap, every
 * mid-level node (device, channel) is materialised + highlighted BEFORE any
 * leaf takes a slot; the budget and the show-all notice are unchanged; a flat
 * result set (every 4b pin's fixture) orders exactly as before.
 * Run: node tests/tree_cap_depth_selfcheck.cjs   (needs jsdom)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div class="explorer-pane">'
    + '<div id="explorer-tree-state" class="json-tree"></div></div></body></html>',
    { url: 'http://localhost/explorer', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent;
global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
window.localStorage = global.localStorage; global.sessionStorage = global.localStorage; window.sessionStorage = global.localStorage;
global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
window.__treeSearchMaterializeMax = 20;         // small cap for the fixture
window.openConfigManual = function () {};
window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

// An ancestor-key match: 'analog_bank' > 4 devs > 3 chans each > 4 leaves +
// a sub-object. Total matching nodes (all by path): 1 + 4 + 12 + 12*5 + 12*2
// = way past the cap of 20. Shallowest-first, 20 = the bank + 4 devs + 12
// chans + the first 3 depth-3 leaves.
const DATA = { qubits: { q1: { f_01: 4.5e9 } }, analog_bank: {} };
for (let dv = 1; dv <= 4; dv++) {
    const chans = {};
    for (let ch = 1; ch <= 3; ch++) {
        chans['chan' + ch] = { offs: 0.1, gain: 2, mode: 'amp', extra: { a: 1, b: 2 } };
    }
    DATA.analog_bank['dev' + dv] = chans;
}
const d = window.document;
const c = d.getElementById('explorer-tree-state');
window.renderJsonTree('explorer-tree-state', DATA, { defaultDepth: 1, crud: true });

window.jsonTreeSearch('explorer-tree-state', 'analog');
setTimeout(function () {
    const hp = Array.prototype.map.call(
        c.querySelectorAll('.tree-node.tree-highlight'),
        (n) => n.getAttribute('data-path'));
    const devs = hp.filter((p) => /^analog_bank\.dev\d+$/.test(p)).length;
    const chans = hp.filter((p) => /^analog_bank\.dev\d+\.chan\d+$/.test(p)).length;
    const leaves = hp.filter((p) => p.split('.').length >= 4).length;
    ok(hp.indexOf('analog_bank') >= 0, 'the matched ancestor itself is shown');
    ok(devs === 4, 'every device level is in the capped set (' + devs + ' of 4)');
    ok(chans === 12, 'and every channel, before any of their leaves (' + chans + ' of 12)');
    ok(hp.length === 20, 'still exactly the budget (' + hp.length + ')');
    ok(leaves === 20 - 1 - 4 - 12,
       'the leftover budget -- and only that -- goes to leaves (' + leaves + ')');
    const notice = c.querySelector(':scope > .tree-search-results');
    ok(!!notice && /show all/.test(notice.textContent),
       'the notice still names the truth and offers show-all');

    // and a FLAT result set is untouched by the ordering change: 20 same-depth
    // matches in tree order, exactly as 4b shipped it
    // 'offs' matches 12 LEAF keys and nothing by path (leaves have no
    // descendants) -- genuinely under the cap. ('chan' would not do: every
    // chan's own subtree matches by path, 96 in all, and the cap fires.)
    window.jsonTreeSearch('explorer-tree-state', 'offs');
    setTimeout(function () {
        const hp2 = Array.prototype.map.call(
            c.querySelectorAll('.tree-node.tree-highlight'),
            (n) => n.getAttribute('data-path'));
        ok(hp2.length === 12 && !c.querySelector(':scope > .tree-search-results'),
           'a below-cap query still gets the classic whole-set highlight (' + hp2.length + ')');
        console.log(fails ? ('FAILED: ' + fails) : 'ALL OK (7 assertions)');
        process.exit(fails ? 1 : 0);
    }, 280);   // jsonTreeSearch debounces 200 ms
}, 280);   // jsonTreeSearch debounces 200 ms
