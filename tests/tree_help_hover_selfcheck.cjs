/* jsdom selfcheck for docs/141 4w: the ? (Config Manual) on a Json-tree row
 * is a hover tool like the row's edit/copy/add/delete group, and it sits
 * RIGHT of that group. The group is built lazily on the first mouseover,
 * AFTER the ? was appended at render time -- without the re-append the ?
 * lands left of the group (the user's screenshot: "? ⧉ + ✕").
 * Run: node tests/tree_help_hover_selfcheck.cjs   (driven by tests/test_config_manual.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM('<!doctype html><html><body><div class="explorer-pane"><div id="t" class="json-tree"></div></div></body></html>',
    { url: 'http://localhost/explorer', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.CustomEvent = window.CustomEvent; global.KeyboardEvent = window.KeyboardEvent; global.MouseEvent = window.MouseEvent;
global.navigator = window.navigator; global.location = window.location;
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }; window.localStorage = global.localStorage; global.sessionStorage = global.localStorage;
global.fetch = () => new Promise(() => {}); window.fetch = global.fetch;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
window.openConfigManual = function () {};
window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

const d = window.document;
const c = d.getElementById('t');
window.renderJsonTree('t', { qubits: { q1: { z: { operations: { const: { length: 100, amplitude: 0.1 } } } } } }, { defaultDepth: 6, crud: true });
// children materialise lazily on expand: open every collapsed node
for (let i = 0; i < 10; i++) { const t = c.querySelectorAll('.tree-toggle.collapsed'); if (!t.length) break; t.forEach((x) => x.click()); }

// 1. render time: the ? is the row's last child (nothing else after the value)
const rows = Array.prototype.filter.call(c.querySelectorAll('.tree-row'), (r) => r.querySelector(':scope > .tree-help'));
ok(rows.length >= 3, 'the crud tree carries ? rows (' + rows.length + ')');
const container = rows[0];                                   // a container row (operations / const)
const leaf = rows.find((r) => r.querySelector(':scope > .tree-val'));
ok(!!leaf, 'and a leaf row among them');
ok(!container.querySelector(':scope > .tree-row-actions'), 'the action group is NOT built at render time (lazy on hover)');
ok(container.lastElementChild === container.querySelector(':scope > .tree-help'), 'before hover the ? is the last child');

// 2. hover builds the group -- and the ? must end up AFTER it
function hover(row) { row.dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true })); }
hover(container);
let acts = container.querySelector(':scope > .tree-row-actions');
ok(!!acts && acts.querySelectorAll('button').length >= 2, 'mouseover builds the action group on a container row');
let help = container.querySelector(':scope > .tree-help');
ok(container.lastElementChild === help, 'the ? is the last child after the group is built');
ok(acts.compareDocumentPosition(help) & window.Node.DOCUMENT_POSITION_FOLLOWING, 'the ? FOLLOWS the action group in DOM order');
ok(container.querySelectorAll(':scope > .tree-help').length === 1, 'still exactly one ? (moved, not duplicated)');
hover(container);
ok(container.querySelectorAll(':scope > .tree-row-actions').length === 1 && container.lastElementChild === help, 'a second hover changes nothing');

// 3. same on a leaf row (the value path has its own append site)
hover(leaf);
acts = leaf.querySelector(':scope > .tree-row-actions'); help = leaf.querySelector(':scope > .tree-help');
ok(!!acts, 'mouseover builds the group on a leaf row');
ok(leaf.lastElementChild === help && (acts.compareDocumentPosition(help) & window.Node.DOCUMENT_POSITION_FOLLOWING), 'and the leaf ? follows its group too');

// 4. the stylesheet: hover-revealed, like the other tools (exact rules)
const css = fs.readFileSync(path.join(STATIC, 'style.css'), 'utf8');
ok(/\.key-help-btn\.tree-help \{ opacity: 0;/.test(css), 'the tree ? is invisible at rest (two-class rule beats .key-help-btn 0.55)');
ok(/\.tree-row:hover \.key-help-btn\.tree-help \{ opacity: 0\.7; \}/.test(css), 'and revealed on row hover at the tools opacity (0.7)');
ok(/\.tree-row \.key-help-btn\.tree-help:hover \{ opacity: 1; \}/.test(css), 'full on direct hover');
ok(css.indexOf('.key-help-btn.tree-help { opacity: 0;') > css.indexOf('.key-help-btn:hover { opacity: 1;'), 'placed after the generic rules');

console.log(fails ? ('FAILED: ' + fails) : 'ALL OK (' + 15 + ' assertions)');
process.exit(fails ? 1 : 0);
