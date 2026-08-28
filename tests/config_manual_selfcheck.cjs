/* jsdom selfcheck for the Config Manual window (manual.js, 2026-08-27).
 * Pins, against the real manual.js + search-query.js:
 *   1. the sidebar button opens the window and loads /api/manual ONCE per chip
 *   2. every entry shows key / class / type, its description AND its source;
 *      an undescribed key says so (never a fabricated line)
 *   3. the house search grammar filters entries (space = AND, | = OR)
 *   4. openConfigManual({path}) renders the "this place" view: set keys,
 *      "keys you could add", the focused leaf highlighted, back to search
 *   5. dragging the header commits the window to fixed coords (movable);
 *      Escape closes; F1 on a state cell opens the node view for its path
 * Run: node tests/config_manual_selfcheck.cjs   (driven by tests/test_config_manual.py)
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }

const dom = new JSDOM(
    '<!doctype html><html><body>' +
    '<div id="pending-tray"><span class="state-status-name">chipA</span></div>' +
    '<button id="manual-btn" class="manual-btn"></button>' +
    '<input class="bulk-cell" id="cell" data-dot-path="qubits.q1.z.joint_offset">' +
    '<div id="manual-popover" class="manual-popover manual-hidden">' +
    '<div class="manual-header" id="manual-header"><strong>Config Manual</strong>' +
    '<span class="manual-header-tools"><button class="manual-close">×</button></span></div>' +
    '<div class="manual-searchbar"><input type="search" class="manual-search"></div>' +
    '<div class="manual-body"></div></div>' +
    '</body></html>',
    { url: 'http://localhost/bulk', pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.CSS = window.CSS;
global.getComputedStyle = window.getComputedStyle.bind(window);
global.Event = window.Event; global.MouseEvent = window.MouseEvent; global.KeyboardEvent = window.KeyboardEvent;
global.navigator = window.navigator; global.location = window.location;
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
window._anchorPopover = function (p) { p.style.top = '40px'; p.style.left = '10px'; };
window._toolTrigger = function () { return document.getElementById('manual-btn'); };
global._anchorPopover = window._anchorPopover; global._toolTrigger = window._toolTrigger;
const nav = [];
window._navigateToExplorerPath = function (p) { nav.push(p); };

const ENTRIES = [
    { id: 'FluxLine.joint_offset', key: 'joint_offset', cls: 'FluxLine', type: 'float', required: false, default: 0.0,
      doc: 'the flux bias at the joint operating point in V.', docs: null, source: 'class docstring',
      examples: ['qubits.q1.z.joint_offset'], present_in: 1, choices: null },
    { id: 'MWFEMAnalogOutputPort.band', key: 'band', cls: 'MWFEMAnalogOutputPort', type: 'int', required: true, default: null,
      doc: null, docs: { summary: 'The frequency band the MW-FEM port operates in.', allowed: [{ value: 1, meaning: '50 MHz – 5.5 GHz' }, { value: 2, meaning: '4.5–7.5 GHz' }],
      docs: 'Guides/opx1000_fems.md#bands', quote: 'q', unit: null, default: null }, source: 'QM docs', examples: [], present_in: 0, choices: null },
    { id: 'FluxLine.mystery', key: 'mystery', cls: 'FluxLine', type: 'str', required: false, default: null,
      doc: null, docs: null, source: null, examples: [], present_in: 0, choices: null },
    // a lab docstring is third-party text: it must render as text, never as markup
    { id: 'Lab.evil', key: 'evil', cls: 'Lab', type: 'str', required: false, default: null,
      doc: '<img src=x onerror="window.__pwned=1"> <b>bold</b>', docs: null, source: 'class docstring', examples: [], present_in: 0, choices: null },
];
const NODE = { ok: true, path: 'qubits.q1.z.joint_offset', owner: 'qubits.q1.z', focus: 'joint_offset', cls: 'FluxLine',
    cls_doc: 'QUAM component for a flux line.', known: true,
    fields: [
        Object.assign({}, ENTRIES[0], { present: true, focus: true }),
        { id: 'FluxLine.independent_offset', key: 'independent_offset', cls: 'FluxLine', type: 'float', required: false, default: 0.0,
          doc: 'the independent point.', docs: null, source: 'class docstring', examples: [], present_in: 0, present: false, focus: false, choices: null },
    ], unset: ['independent_offset'], note: null };
// docs/141 4h: category > class > key; a class the chip uses is open, one it
// does not is collapsed until a search reaches into it; the catalogue may
// still be warming ("loading") and the open window re-asks
ENTRIES[0].category = 'Flux & couplers'; ENTRIES[0].used = true;
ENTRIES[1].category = 'Ports'; ENTRIES[1].used = false;
ENTRIES[2].category = 'Flux & couplers'; ENTRIES[2].used = true;
ENTRIES[3].category = 'Lab (quam_config)'; ENTRIES[3].used = false;
const CATEGORIES = ['Ports', 'Flux & couplers', 'Lab (quam_config)'];
const CLASSES = [{ cls: 'FluxLine', cls_path: 'quam.components.channels.FluxLine', doc: 'QUAM component for a flux line.', category: 'Flux & couplers', fields: 2, count: 1, known: true, used: true },
                 { cls: 'MWFEMAnalogOutputPort', cls_path: 'quam.components.ports.MWFEMAnalogOutputPort', doc: 'An MW-FEM output port.', category: 'Ports', fields: 1, count: 0, known: true, used: false },
                 { cls: 'Lab', cls_path: 'quam_config.lab.Lab', doc: null, category: 'Lab (quam_config)', fields: 1, count: 0, known: true, used: false }];
window.__catalogState = 'ready';
window.__manualPollMs = 120;
const calls = [];
window.fetch = function (url) {
    calls.push(String(url));
    const body = String(url).indexOf('/api/manual/node') === 0
        ? NODE : { ok: true, entries: ENTRIES.slice(), classes: CLASSES.slice(), categories: CATEGORIES, env: true, catalog: true,   // a fresh copy per response, as JSON would be
                   catalog_state: window.__catalogState, note: window.__catalogNote || null, chip: 'chipA' };
    return Promise.resolve({ json: () => Promise.resolve(body) });
};
global.fetch = window.fetch;

window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'manual.js'), 'utf8'));
const d = window.document;
const pop = d.getElementById('manual-popover');
const body = () => pop.querySelector('.manual-body');

window.toggleConfigManual(d.getElementById('manual-btn'));
setTimeout(function () {
    // 1
    ok(!pop.classList.contains('manual-hidden'), 'the sidebar button opens the window');
    ok(calls.filter((c) => c === '/api/manual').length === 1, 'the manual loaded once (' + calls.join(',') + ')');
    // 2
    const txt = body().textContent;
    ok(/joint_offset/.test(txt) && /the flux bias at the joint/.test(txt), 'an entry shows key + its own docstring');
    ok(/source: class docstring/.test(txt), 'and names the docstring as its source');
    ok(/band/.test(txt) && /50 MHz/.test(txt) && /opx1000_fems\.md#bands/.test(txt), 'a QM-docs entry shows allowed values + the docs page');
    ok(/mystery/.test(txt) && /no description/.test(txt), 'an undescribed key says so — nothing invented');
    ok(body().querySelectorAll('.manual-req').length >= 1, 'a required field is marked');
    // ── docs/141 4h: category > class > key ─────────────────────────────
    const cats = Array.from(body().querySelectorAll('.manual-cat > summary')).map((s) => s.textContent.replace(/\s+/g, ' ').trim());
    ok(cats.length === 3 && /^Ports/.test(cats[0]) && /^Flux & couplers/.test(cats[1]) && /^Lab \(quam_config\)/.test(cats[2]),
       'categories render in the server order (' + cats.join(' | ') + ')');
    const flux = Array.from(body().querySelectorAll('.manual-class')).find((c) => /FluxLine/.test(c.querySelector('.manual-class-name').textContent));
    const port = Array.from(body().querySelectorAll('.manual-class')).find((c) => /MWFEMAnalogOutputPort/.test(c.querySelector('.manual-class-name').textContent));
    ok(flux && flux.open && /in this state/.test(flux.querySelector('summary').textContent), 'a class the chip uses is open and says so');
    ok(port && !port.open, 'a class the chip does not use is collapsed');
    ok(/QUAM component for a flux line/.test(flux.querySelector('.manual-class-doc').textContent), 'the class doc reads in the class row');
    ok(!body().querySelector('.manual-desc:not(.manual-nodesc).muted'), 'a description is never rendered muted (annotations only)');
    ok(/745 keys|4 keys/.test(body().querySelector('.manual-status').textContent) && /full catalogue of the selected environment/.test(body().querySelector('.manual-status').textContent),
       'the status line names the catalogue');
    window.ConfigManual._renderSearch('band');
    const port2 = Array.from(body().querySelectorAll('.manual-class')).find((c) => /MWFEMAnalogOutputPort/.test(c.querySelector('.manual-class-name').textContent));
    ok(port2 && port2.open, 'a search opens the class it reached into');
    window.ConfigManual._renderSearch('');
    // 3 search grammar
    const s = pop.querySelector('.manual-search');
    window.ConfigManual._renderSearch('flux joint');
    ok(body().querySelectorAll('.manual-entry').length === 1, 'space = AND narrows to the one matching entry');
    window.ConfigManual._renderSearch('band | mystery');
    ok(body().querySelectorAll('.manual-entry').length === 2, '| = OR widens (' + body().querySelectorAll('.manual-entry').length + ')');
    window.ConfigManual._renderSearch('');
    ok(body().querySelectorAll('.manual-entry').length === 4, 'empty query lists everything');
    ok(!body().querySelector('img') && !body().querySelector('b') && /<img src=x/.test(body().textContent) && !window.__pwned,
       'a docstring renders as TEXT — markup in third-party text is escaped, never executed');
    // 4 node view via deep link
    window.openConfigManual({ path: 'qubits.q1.z.joint_offset' });
    setTimeout(function () {
        const t2 = body().textContent;
        ok(calls.some((c) => c.indexOf('/api/manual/node?path=qubits.q1.z.joint_offset') === 0), 'the node view asked for THAT path');
        ok(/qubits\.q1\.z/.test(t2) && /FluxLine/.test(t2) && /QUAM component for a flux line/.test(t2), 'the node view names the place, its class and the class doc');
        ok(/Set here \(1\)/.test(t2) && /Keys you could add \(1\)/.test(t2), 'set keys and addable keys are separated');
        const foc = body().querySelector('.manual-focus');
        ok(foc && foc.getAttribute('data-key') === 'joint_offset', 'the focused leaf is highlighted');
        ok(!!body().querySelector('.manual-unset[data-key="independent_offset"]'), 'an unset key is rendered as addable');
        body().querySelector('.manual-goto').click();
        ok(nav[0] === 'qubits.q1.z.joint_offset', '"used at" navigates to the explorer path');
        body().querySelector('.manual-back').click();
        ok(/4 keys/.test(body().textContent) && body().querySelectorAll('.manual-entry').length === 4, 'back returns to the search over all keys');
        // 5 drag commits to fixed coords
        const head = pop.querySelector('.manual-header');
        pop.getBoundingClientRect = () => ({ left: 10, top: 40, width: 500, height: 300 });
        head.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true, button: 0, clientX: 100, clientY: 100 }));
        d.dispatchEvent(new window.MouseEvent('mousemove', { bubbles: true, buttons: 1, clientX: 160, clientY: 150 }));
        d.dispatchEvent(new window.MouseEvent('mouseup', { bubbles: true }));
        ok(pop.classList.contains('manual-floating'), 'a header drag commits the window to floating');
        ok(pop.style.left === '70px' && pop.style.top === '90px', 'and moves it by the drag delta (' + pop.style.left + ',' + pop.style.top + ')');
        // Escape closes
        pop.querySelector('.manual-search').dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
        ok(pop.classList.contains('manual-hidden'), 'Escape closes the window');
        // F1 on a state cell opens the node view for its path
        calls.length = 0;
        const cell = d.getElementById('cell'); cell.focus();
        cell.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'F1', bubbles: true }));
        setTimeout(function () {
            ok(!pop.classList.contains('manual-hidden'), 'F1 on a value opens the manual');
            ok(calls.some((c) => c.indexOf('/api/manual/node?path=qubits.q1.z.joint_offset') === 0), 'on the cell\'s own path');
            // ── the catalogue warms in the background: an open window re-asks ──
            window.__catalogState = 'loading';
            window.openConfigManual({ q: '' });
            window.ConfigManual.load(true).then(function () {
                window.ConfigManual._renderSearch('');
                const n0 = calls.filter((c) => c === '/api/manual').length;
                ok(/loading in the background/.test(body().querySelector('.manual-status').textContent), 'a cold catalogue says it is loading');
                window.__catalogState = 'ready';
                setTimeout(function () {
                    const n1 = calls.filter((c) => c === '/api/manual').length;
                    ok(n1 > n0, 'the open window re-asked (' + (n1 - n0) + ' more request(s))');
                    ok(/full catalogue of the selected environment/.test(body().querySelector('.manual-status').textContent), 'and re-rendered when it landed');
                    // ── the window remembers its size ──
                    window.localStorage.setItem('quam_manual_size', JSON.stringify({ w: 700, h: 520 }));   // jsdom's real Storage
                    window.toggleConfigManual(); window.toggleConfigManual(d.getElementById('manual-btn'));
                    ok(pop.style.width === '700px' && pop.style.height === '520px', 'a remembered size is restored on open (' + pop.style.width + ' x ' + pop.style.height + '; stored=' + window.localStorage.getItem('quam_manual_size') + ' vw=' + window.innerWidth + ' open=' + !pop.classList.contains('manual-hidden') + ')');
                    reviewPins(function () { process.exit(fails ? 1 : 0); });
                }, 400);
            });
            return;
            process.exit(fails ? 1 : 0);
        }, 20);
    }, 20);
}, 30);

/* docs/141 4l-review pins (run last; the window is open and the catalogue 'ready') */
function reviewPins(done) { setTimeout(function () { reviewPinsRun(done); }, 80); }   // let an in-flight load settle first
function reviewPinsRun(done) {
    const mk = (i, cls, clsPath, cat, used) => ({ id: cls + '.k' + i, key: 'k' + i, cls: cls, cls_path: clsPath, category: cat, used: used,
        type: 'float', required: false, default: null, doc: 'd' + i, docs: null, source: 'class docstring', examples: [], present_in: 0, choices: null });
    // (a) past the budget (400 rows): the OPEN class renders, a collapsed class defers to its toggle -- never an empty header
    for (let i = 0; i < 430; i++) ENTRIES.push(mk(i, 'BigPort', 'quam.components.ports.BigPort', 'Ports', false));
    for (let i = 0; i < 12; i++) ENTRIES.push(mk(i, 'UsedPulse', 'quam.components.pulses.UsedPulse', 'Pulses', true));
    CLASSES.push({ cls: 'BigPort', cls_path: 'quam.components.ports.BigPort', doc: null, category: 'Ports', fields: 430, count: 0, known: true, used: false });
    CLASSES.push({ cls: 'UsedPulse', cls_path: 'quam.components.pulses.UsedPulse', doc: null, category: 'Pulses', fields: 12, count: 2, known: true, used: true });
    // (b) two classes sharing a leaf name
    ENTRIES.push(mk(0, 'DragPulse', 'quam.components.pulses.DragPulse', 'Pulses', false));
    ENTRIES.push(mk(1, 'DragPulse', 'quam_builder.common.pulses.DragPulse', 'Pulses', false));
    CLASSES.push({ cls: 'DragPulse', cls_path: 'quam.components.pulses.DragPulse', doc: 'quam drag', category: 'Pulses', fields: 1, count: 0, known: true, used: false });
    CLASSES.push({ cls: 'DragPulse', cls_path: 'quam_builder.common.pulses.DragPulse', doc: 'builder drag', category: 'Pulses', fields: 1, count: 0, known: true, used: false, abstract: true });
    window.__catalogState = 'ready';
    window.openConfigManual({ q: '' });               // back to SEARCH mode (the F1 pin above left the node view)
    window.ConfigManual.load(true).then(function () {
        window.ConfigManual._renderSearch('');
        const cls = (leaf) => Array.from(body().querySelectorAll('.manual-class')).filter((c) => c.querySelector('.manual-class-name').textContent === leaf);
        const used = cls('UsedPulse')[0], big = cls('BigPort')[0];
        ok(used && used.open && used.querySelectorAll('.manual-entry').length === 12, 'past the budget the class the chip uses renders every key (' + (used && used.querySelectorAll('.manual-entry').length) + ')');
        ok(big && !big.open && big.querySelectorAll('.manual-entry').length === 0 && !!big.querySelector('.manual-lazy'),
           'a collapsed class past the budget defers its keys (no empty header: it says ' + (big && big.querySelector('.manual-badge').textContent) + ')');
        big.open = true; big.dispatchEvent(new window.Event('toggle', { bubbles: true }));
        ok(big.querySelectorAll('.manual-entry').length === 430 && !big.querySelector('.manual-lazy'), 'opening it renders its keys (' + big.querySelectorAll('.manual-entry').length + ')');
        const drags = cls('DragPulse');
        ok(drags.length === 2, 'two classes sharing a leaf name are two rows (' + drags.length + ')');
        ok(drags.some((c) => /quam\.components\.pulses/.test(c.querySelector('.manual-class-mod').textContent))
           && drags.some((c) => /quam_builder\.common\.pulses/.test(c.querySelector('.manual-class-mod').textContent)), 'each names its module');
        ok(drags.some((c) => /abstract/.test(c.querySelector('summary').textContent)) && drags.some((c) => !/abstract/.test(c.querySelector('summary').textContent)),
           'an abstract class is badged, the other is not');
        // (d) a poll re-render keeps what the user opened, and does not re-render at all when nothing changed
        window.__catalogState = 'loading';
        window.ConfigManual.load(true).then(function () {
            window.ConfigManual._renderSearch('');
            const big2 = cls('BigPort')[0];
            big2.open = true; big2.dispatchEvent(new window.Event('toggle', { bubbles: true }));
            const n0 = calls.filter((c) => c === '/api/manual').length;
            setTimeout(function () {
                const n1 = calls.filter((c) => c === '/api/manual').length;
                ok(n1 > n0, 'while loading the window polls (' + (n1 - n0) + ')');
                ok(cls('BigPort')[0] === big2 && big2.open, 'an unchanged poll re-renders NOTHING (the same node, still open)');
                ENTRIES.push(mk(999, 'UsedPulse', 'quam.components.pulses.UsedPulse', 'Pulses', true));   // the catalogue grew
                setTimeout(function () {
                    const big3 = cls('BigPort')[0];
                    // (two open classes past the budget: the user's class keeps its keys up to the budget, and says how many more)
                    ok(big3 !== big2 && big3.open && big3.querySelectorAll('.manual-entry').length >= 380 && /more keys in this class/.test(big3.textContent),
                       'a changed poll re-renders, and the class the user opened stays open with its keys (' + big3.querySelectorAll('.manual-entry').length + ' new=' + (big3 !== big2) + ' open=' + big3.open + ' note=' + /more keys in this class/.test(big3.textContent) + ')');
                    const used3 = cls('UsedPulse')[0];
                    ok(used3 && used3.querySelectorAll('.manual-entry').length === 13, "the chip's own class keeps every key -- the budget serves it first (" + (used3 && used3.querySelectorAll('.manual-entry').length) + ')');
                    // (c) an error state is final: the note shows and the poll stops
                    window.__catalogState = 'error'; window.__catalogNote = 'The class catalogue could not be built: no quam in this interpreter';
                    window.ConfigManual.load(true).then(function () {
                        window.ConfigManual._renderSearch('');
                        ok(/no quam in this interpreter/.test(body().textContent), 'the error state shows the reason');
                        const e0 = calls.filter((c) => c === '/api/manual').length;
                        setTimeout(function () {
                            ok(calls.filter((c) => c === '/api/manual').length === e0, 'and the window stops polling');
                            done();
                        }, 400);
                    });
                }, 400);
            }, 400);
        });
    });
}
