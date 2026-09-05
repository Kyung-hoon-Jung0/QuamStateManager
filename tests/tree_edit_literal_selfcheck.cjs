/* docs/145 -- Json Tree inline edit, two customer items:
 *  1. a STRING edits as its JSON literal: the file says "direct", the editor
 *     shows "direct" (quotes included); committing a quoted literal unwraps
 *     it, so the server receives the raw value; unquoted text degrades to
 *     the old behavior (sent as typed).
 *  2. while editing, the hover row actions + ? help are hidden (the async
 *     type chip used to paint straight under them), and the editing value
 *     box is inline-flex so the chip widens it instead of overflowing.
 * Run: node tests/tree_edit_literal_selfcheck.cjs   (needs jsdom)
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
global.requestAnimationFrame = (f) => setTimeout(f, 0); window.requestAnimationFrame = global.requestAnimationFrame;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} }; window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} }; window.ResizeObserver = global.ResizeObserver;
window.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {} }; global.htmx = window.htmx;
// realm-installed fetch mock (docs/125 rule + its docs/144 dual): capture
// /field/edit bodies, answer /field/peek with an expected-type payload
window.__fetchLog = [];
window.eval("fetch = window.fetch = function (url, opts) {" +
    " window.__fetchLog.push({ url: String(url), body: opts && opts.body ? String(opts.body) : null });" +
    " if (String(url).indexOf('/field/peek') === 0) return Promise.resolve({ json: function () {" +
    "   return Promise.resolve({ expected: {} }); } });" +
    " return Promise.resolve({ json: function () { return Promise.resolve(" +
    "   { ok: true, stored: 'smooth', formatted: 'smooth' }); } });" +
    "};");
global.fetch = window.fetch;
window.eval(fs.readFileSync(path.join(STATIC, 'search-query.js'), 'utf8'));
window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

const d = window.document;
window.renderJsonTree('explorer-tree-state', {
    qubits: { q1: { gate_shape: 'direct', f_01: 4.5e9, esc: 'a"b',
                    // the customer's case: a value that should be a number and
                    // is stored as text. SM's own alarm flags it; the fix is to
                    // let the tree editor be the place it gets fixed.
                    length: '3124',
                    // its own leaf: the blocks above commit to `length`, and
                    // the mocked response rewrites what the row displays
                    length2: '3124' } },
}, { defaultDepth: 8, crud: true });

(async () => {
    // leaves materialize through the search pipeline (same as the cap harness)
    window.jsonTreeSearch('explorer-tree-state', 'q1');
    await new Promise((r) => setTimeout(r, 300));   // past the 200ms debounce
    const val = d.querySelector('.tree-node[data-path="qubits.q1.gate_shape"] .tree-val');
    ok(!!val && val.classList.contains('tree-val-string'), 'string leaf rendered');
    val.click();
    await new Promise((r) => setTimeout(r, 30));
    const input = val.querySelector('input.tree-edit-input');
    ok(!!input, 'clicking the value opens the inline editor');
    ok(input.value === '"direct"', 'the editor shows the JSON literal, quotes included (' + input.value + ')');

    // commit a QUOTED literal -> the POSTed value is unwrapped
    input.value = '"smooth"';
    input.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));
    const edit = window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).pop();
    ok(!!edit, 'commit POSTs /field/edit');
    ok(edit && /(^|&)value=smooth(&|$)/.test(edit.body),
       'a quoted literal is UNWRAPPED before sending (' + (edit && edit.body) + ')');

    // a string with an inner quote round-trips through JSON escaping
    const esc = d.querySelector('.tree-node[data-path="qubits.q1.esc"] .tree-val');
    esc.click();
    await new Promise((r) => setTimeout(r, 30));
    const einp = esc.querySelector('input.tree-edit-input');
    ok(einp && einp.value === JSON.stringify('a"b'),
       'inner quotes are escaped exactly as the file spells them (' + (einp && einp.value) + ')');
    einp.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    // a NUMBER edits exactly as before (no quoting)
    const nval = d.querySelector('.tree-node[data-path="qubits.q1.f_01"] .tree-val');
    nval.click();
    await new Promise((r) => setTimeout(r, 30));
    const ninp = nval.querySelector('input.tree-edit-input');
    ok(ninp && ninp.value.charAt(0) !== '"', 'a number edits unquoted (' + (ninp && ninp.value) + ')');
    ninp.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    // unquoted text still goes through as typed (old behavior kept)
    const val2 = d.querySelector('.tree-node[data-path="qubits.q1.gate_shape"] .tree-val');
    val2.click();
    await new Promise((r) => setTimeout(r, 30));
    const inp2 = val2.querySelector('input.tree-edit-input');
    inp2.value = 'bare_text';
    inp2.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));
    const edit2 = window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).pop();
    ok(edit2 && /(^|&)value=bare_text(&|$)/.test(edit2.body),
       'unquoted text is sent as typed (' + (edit2 && edit2.body) + ')');

    // 3. A NUMERIC STRING, edited to the number (customer, 2026-09-05)
    //    SM flags the leaf as text-that-reads-like-a-number and tells the user
    //    to fix it. Opening the editor shows "3124"; deleting the quotes and
    //    typing 3124 is how a user says "make this a number". The commit guard
    //    compared the typed text to the RAW stored value -- both the JS string
    //    "3124" -- and cancelled, so the request that answers this was never
    //    sent and the edit silently did nothing.
    const nsVal = d.querySelector('.tree-node[data-path="qubits.q1.length"] .tree-val');
    ok(!!nsVal && nsVal.classList.contains('tree-val-string'),
       'the numeric string renders as a string leaf');
    nsVal.click();
    await new Promise((r) => setTimeout(r, 30));
    const nsInp = nsVal.querySelector('input.tree-edit-input');
    ok(nsInp && nsInp.value === '"3124"',
       'its editor shows the quoted literal (' + (nsInp && nsInp.value) + ')');
    const beforeN = window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).length;
    nsInp.value = '3124';                       // the quotes deleted, on purpose
    nsInp.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));
    const posts = window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0);
    ok(posts.length === beforeN + 1,
       'dropping the quotes POSTs -- the intent reaches the server (posts: '
       + (posts.length - beforeN) + ')');
    ok(posts.length > beforeN && /(^|&)value=3124(&|$)/.test(posts[posts.length - 1].body),
       'and sends the number as typed (' + (posts[posts.length - 1] || {}).body + ')');
    ok(posts.length > beforeN && !/value_quoted/.test(posts[posts.length - 1].body),
       'with NO quoted marker, because the user removed the quotes');

    // 4. The same leaf, re-quoted: "keep this text", and SM must not ask.
    //    The server reads a leading quote as that intent (routes.py
    //    _type_fix_offer), but this editor unwraps the literal before posting
    //    -- so the FACT travels instead of the character.
    const keepVal = d.querySelector('.tree-node[data-path="qubits.q1.length"] .tree-val');
    keepVal.click();
    await new Promise((r) => setTimeout(r, 30));
    const keepInp = keepVal.querySelector('input.tree-edit-input');
    const beforeK = window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).length;
    keepInp.value = '"3125"';                   // quotes kept, value changed
    keepInp.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));
    const kposts = window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0);
    ok(kposts.length === beforeK + 1, 'a quoted change posts too');
    const kbody = (kposts[kposts.length - 1] || {}).body || '';
    ok(/(^|&)value=3125(&|$)/.test(kbody),
       'still UNWRAPPED, as docs/145 pinned (' + kbody + ')');
    ok(/(^|&)value_quoted=1(&|$)/.test(kbody),
       'but the quotes are reported, so the server does not offer to convert ('
       + kbody + ')');

    // 5. An unchanged value still must not post.
    const sameVal = d.querySelector('.tree-node[data-path="qubits.q1.length"] .tree-val');
    sameVal.click();
    await new Promise((r) => setTimeout(r, 30));
    const sameInp = sameVal.querySelector('input.tree-edit-input');
    const beforeS = window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).length;
    sameInp.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));
    ok(window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).length === beforeS,
       'committing an untouched editor still posts nothing');

    // 6b. Retyping a NON-numeric string unquoted is NOT a type change, and
    //     must still not post -- /field/edit does not no-op an identical value
    //     (measured: the tray's change count goes 9 -> 10), which is what the
    //     guard exists for. `gate_shape` holds bare text, so JSON.parse throws
    //     and there is no other type being asked for.
    const bareVal = d.querySelector('.tree-node[data-path="qubits.q1.gate_shape"] .tree-val');
    bareVal.click();
    await new Promise((r) => setTimeout(r, 30));
    const bareInp = bareVal.querySelector('input.tree-edit-input');
    const curText = JSON.parse(bareInp.value);          // the raw stored string
    const beforeB = window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).length;
    bareInp.value = curText;                            // quotes deleted, same text
    bareInp.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));
    ok(window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).length === beforeB,
       'unquoting a non-numeric string that did not change posts nothing');

    // 6c. The intent test is only for text the user UNQUOTED. A quoted literal
    //     spelled differently but meaning the same NUMERIC string is still
    //     unchanged -- judging its intent would post a no-op.
    const numRe = d.querySelector('.tree-node[data-path="qubits.q1.length2"] .tree-val');
    numRe.click();
    await new Promise((r) => setTimeout(r, 30));
    const numReInp = numRe.querySelector('input.tree-edit-input');
    const beforeR = window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).length;
    // "3124" is another spelling of the same four characters
    numReInp.value = '"312' + String.fromCharCode(92) + 'u0034"';
    ok(JSON.parse(numReInp.value) === '3124' && numReInp.value !== '"3124"',
       'the fixture is a different spelling of the same numeric string ('
       + numReInp.value + ')');
    numReInp.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));
    ok(window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).length === beforeR,
       'a re-spelled QUOTED numeric string posts nothing (the quotes said "text")');

    // 6. An UNWRAPPED literal that means the same value must still not post.
    //    This is the one case the first no-op guard cannot see: the typed text
    //    differs from what the editor showed, but unwraps to the value already
    //    stored. `esc` holds a"b, so the editor shows "a\"b" and " spells
    //    the same character a different way.
    const escVal2 = d.querySelector('.tree-node[data-path="qubits.q1.esc"] .tree-val');
    escVal2.click();
    await new Promise((r) => setTimeout(r, 30));
    const escInp2 = escVal2.querySelector('input.tree-edit-input');
    const beforeE = window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).length;
    // built from the code point so no source-level escape -- JS, Python
    // or a shell heredoc -- can eat the backslash on the way in
    escInp2.value = '"a' + String.fromCharCode(92) + 'u0022b"';
    ok(escInp2.value.length === 10 && JSON.parse(escInp2.value) === 'a"b',
       'the fixture really is another spelling of the SAME value ('
       + escInp2.value + ')');
    escInp2.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise((r) => setTimeout(r, 30));
    ok(window.__fetchLog.filter((f) => f.url.indexOf('/field/edit') === 0).length === beforeE,
       'a differently-spelled literal for the SAME value posts nothing');

    // 2. the overlap fix is a stylesheet contract -- pin the rules
    const css = fs.readFileSync(path.join(STATIC, 'style.css'), 'utf8');
    ok(/\.tree-row:has\(\.tree-val-editing\)[^{]*\.tree-row-actions/.test(css)
       && css.indexOf('display: none') > -1,
       'editing hides the hover row actions (the chip used to paint under them)');
    ok(/\.tree-val-editing \{[^}]*inline-flex/.test(css),
       'the editing value box is inline-flex so the async chip widens it');

    console.log(fails ? ('FAILED: ' + fails) : 'ALL OK (25 assertions)');
    process.exit(fails ? 1 : 0);
})().catch((e) => { console.error('FATAL', e && e.message); process.exit(1); });
