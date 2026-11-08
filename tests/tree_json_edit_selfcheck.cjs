// Edit as JSON panel (D-22..D-28) driven against the shipped tree-json-edit.js.
// JSON_EDIT_MUTATION=<name> breaks one behaviour in the source before it runs;
// every mutation must turn this harness red.
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { JSDOM } = require('jsdom');

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'tree-json-edit.js');
// a Windows checkout may carry CRLF; the mutation targets are written with LF
let src = fs.readFileSync(SRC, 'utf8').replace(/\r\n/g, '\n');
const MUT = {
    tree_type: ['tree_kind_change: true', 'tree_kind_change: false'],
    type_note: ['+ typeNote + (warn.length', '+ "" + (warn.length'],
    parse: ['var t = getText(), pe = parseError(t);', 'var t = getText(), pe = null;'],
    no_save_on_error: ['if (lint()) return;                 // D-24', 'lint();                 // D-24'],
    goto: ['var line = lineOf(text, S.path, r.path);', 'var line = 1;'],
    ctrls: ['(e.key === "s" || e.key === "S")', '(e.key === "never")'],
    esc_dirty: ['close(false);\n        }\n    }', 'close(true);\n        }\n    }'],
    rebase: ['if (rebase) { body.rebase = true; body.base_text = S.initial; body.open_token = S.token || ""; }', ''],
    token: ['body.open_token = S.token || "";', ''],
    warn: ['(warn.length ? ". Warning: " + warn.join("; ") : "")', '""'],
    large: ['if (!d.path && d.large) return warnLarge(opts, d);', ''],
    one_warn: ['document.querySelectorAll(".ui-jedit-warn").forEach(function (x) { x.remove(); });', ''],
    dark: ['doc: d.text, dark: isDark(), lint: true,', 'doc: d.text, dark: false, lint: true,'],
    seq: ['base_seq: S.seq,', 'base_seq: null,'],
    all_or_nothing_msg: ['showRefused(rows);', ''],
};
const m = process.env.JSON_EDIT_MUTATION;
if (m) {
    const [a, b] = MUT[m] || [];
    if (!a || src.indexOf(a) < 0) { console.error('mutation target exists? NO: ' + m); process.exit(3); }
    src = src.replace(a, b);
}

function world(theme) {
    const dom = new JSDOM('<!doctype html><html data-theme="' + (theme || 'light') + '"><body>'
        + '<div class="tree-toolbar"><button id="explorer-json-whole"></button></div>'
        + '<div id="explorer-tree-state"></div><div id="explorer-tree-wiring" style="display:none"></div>'
        + '</body></html>', { runScripts: 'outside-only' });
    const w = dom.window;
    const calls = [];
    const responses = [];
    w.fetch = (url, opts) => {
        calls.push({ url, opts, body: opts && opts.body ? JSON.parse(opts.body) : null });
        const r = responses.shift() || { status: 200, d: { ok: true, results: [] } };
        return Promise.resolve({ status: r.status, json: () => Promise.resolve(r.d) });
    };
    const eds = [];
    w.SmCodeMirror = {
        create(host, o) {
            let doc = o.doc;
            const ed = {
                host, o, dark: o.dark, diags: null, focused: 0, destroyed: false,
                getDoc: () => doc,
                set(t) { doc = t; o.onChange && o.onChange(t); },
                setDark(v) { ed.dark = v; },
                setDiagnostics(d) { ed.diags = d; },
                focus() { ed.focused++; },
                destroy() { ed.destroyed = true; },
                view: { state: { doc: { lines: 99, line: (n) => ({ from: n * 10 }) } }, dispatch(t) { ed.sel = t.selection.anchor; } },
            };
            const ta = w.document.createElement('textarea');
            host.appendChild(ta);
            ed.ta = ta;
            eds.push(ed);
            return ed;
        },
    };
    w.__chipToken = 'tok';
    w.eval(src);
    return { w, calls, responses, eds };
}
const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle() { for (let i = 0; i < 6; i++) await tick(); }
const SOURCE = { ok: true, path: 'qubits.q1', tab: 'state', mutation_seq: 7, bytes: 40, open_token: 'open-1',
                 text: '{\n  "T1": 2e-05,\n  "f_01": 5000000000.0,\n  "id": "q1"\n}' };
function key(w, target, k, mods) {
    const e = new w.KeyboardEvent('keydown', Object.assign({ key: k, bubbles: true, cancelable: true }, mods || {}));
    target.dispatchEvent(e);
    return e;
}

(async () => {
    let n = 0;
    const ok = (c, msg) => { assert.ok(c, msg); n++; };

    // 1 open: the panel sits above the tree, the editor holds the text, theme follows the page
    {
        const { w, calls, responses, eds } = world('dark');
        responses.push({ status: 200, d: SOURCE });
        await w.TreeJsonEdit.open({ tab: 'state', path: 'qubits.q1' });
        await settle();
        ok(/\/tree\/json-edit\/source\?tab=state&path=qubits.q1/.test(calls[0].url), 'source fetched');
        const p = w.document.querySelector('.ui-jedit');
        ok(p && p.nextElementSibling.id === 'explorer-tree-state', 'panel above the tree');
        ok(eds[0].getDoc() === SOURCE.text, 'editor holds the subtree text');
        ok(eds[0].dark === true, 'dark page -> dark editor');
        w.document.documentElement.setAttribute('data-theme', 'light');
        await settle();
        ok(eds[0].dark === false, 'theme change follows');
    }

    // 1b the default QM theme is a dark theme: the editor must be dark on it
    {
        const { w, responses, eds } = world('qm');
        responses.push({ status: 200, d: SOURCE });
        await w.TreeJsonEdit.open({ tab: 'state', path: 'qubits.q1' });
        await settle();
        ok(eds[0].dark === true, 'default qm theme -> dark editor');
    }

    // 2 parse error: marks + one line above, and Save writes nothing
    {
        const { w, calls, responses, eds } = world();
        responses.push({ status: 200, d: SOURCE });
        await w.TreeJsonEdit.open({ tab: 'state', path: 'qubits.q1' });
        await settle();
        eds[0].set('{\n  "T1": 2e-05,\n}');
        const msg = w.document.querySelector('.ui-jedit-msg');
        ok(/^Line 3, column 1/.test(msg.textContent), 'line/column stated: ' + msg.textContent);
        ok(msg.classList.contains('ui-jedit-msg-error'), 'error look');
        ok(eds[0].diags && eds[0].diags.length === 1, 'editor marked');
        const before = calls.length;
        w.TreeJsonEdit.save();
        await settle();
        ok(calls.length === before, 'a parse error posts nothing');
    }

    // 3 refusal list: every path named, go-to-line moves the cursor; Ctrl+S saves with the seq
    {
        const { w, calls, responses, eds } = world();
        responses.push({ status: 200, d: SOURCE });
        await w.TreeJsonEdit.open({ tab: 'state', path: 'qubits.q1' });
        await settle();
        eds[0].set(SOURCE.text.replace('5000000000.0', '"x"').replace('"q1"', '"q9"'));
        responses.push({ status: 409, d: { ok: false, error: '2 of 2 change(s) refused; nothing was written',
            refused: [{ path: 'qubits.q1.f_01', error: 'expected a number' }, { path: 'qubits.q1.id', error: 'read-only' }] } });
        key(w, eds[0].ta, 's', { ctrlKey: true });
        await settle();
        const save = calls[calls.length - 1];
        ok(save.url === '/tree/json-edit/save' && save.body.base_seq === 7 && save.body.path === 'qubits.q1', 'Ctrl+S saved with the opened seq');
        const rows = w.document.querySelectorAll('.ui-jedit-refused-row');
        ok(rows.length === 2, 'both refused paths listed');
        ok(/nothing was written/.test(w.document.querySelector('.ui-jedit-msg').textContent), 'says nothing landed');
        const go = rows[1].querySelector('.ui-jedit-goto');
        ok(go.getAttribute('data-line') === '4', 'id is on line 4, got ' + go.getAttribute('data-line'));
        go.click();
        ok(w.TreeJsonEdit._state().lastGoto === 4 && eds[0].sel === 40, 'cursor moved to line 4');
    }

    // 4 Esc: dirty asks, clean closes
    {
        const { w, responses, eds } = world();
        responses.push({ status: 200, d: SOURCE });
        await w.TreeJsonEdit.open({ tab: 'state', path: 'qubits.q1' });
        await settle();
        eds[0].set(SOURCE.text.replace('2e-05', '3e-05'));
        key(w, eds[0].ta, 'Escape');
        ok(w.document.querySelector('.ui-jedit') && !w.document.querySelector('.ui-jedit-ask').hidden, 'dirty Esc asks');
        w.document.querySelector('.ui-jedit-discard').click();
        ok(!w.document.querySelector('.ui-jedit'), 'discard closes');
        responses.push({ status: 200, d: SOURCE });
        await w.TreeJsonEdit.open({ tab: 'state', path: 'qubits.q1' });
        await settle();
        key(w, eds[1].ta, 'Escape');
        ok(!w.document.querySelector('.ui-jedit') && eds[1].destroyed, 'clean Esc closes');
    }

    // 5 moved seq: refused, re-diff offered, and the re-diff sends the opened text as the base
    {
        const { w, calls, responses, eds } = world();
        responses.push({ status: 200, d: SOURCE });
        await w.TreeJsonEdit.open({ tab: 'state', path: 'qubits.q1' });
        await settle();
        eds[0].set(SOURCE.text.replace('2e-05', '3e-05'));
        responses.push({ status: 409, d: { ok: false, moved: true, mutation_seq: 9, error: 'moved' } });
        w.TreeJsonEdit.save();
        await settle();
        const rb = w.document.querySelector('.ui-jedit-rebase');
        ok(rb && !rb.hidden, 're-diff offered');
        responses.push({ status: 200, d: { ok: true, results: [{}] } });
        rb.click();
        await settle();
        const last = calls[calls.length - 1].body;
        ok(last.rebase === true && last.base_text === SOURCE.text, 're-diff carries the opened text');
        ok(last.open_token === 'open-1', 're-diff carries the token the source served');
        ok(!w.document.querySelector('.ui-jedit'), 'closed after the write');
    }

    // 6 whole file over 2 MB warns first
    {
        const { w, responses, eds } = world();
        responses.push({ status: 200, d: Object.assign({}, SOURCE, { path: '', large: true, bytes: 3e6 }) });
        responses.push({ status: 200, d: Object.assign({}, SOURCE, { path: '', large: true, bytes: 3e6 }) });
        w.TreeJsonEdit.openWhole(); w.TreeJsonEdit.openWhole();   // a double press before the answer
        await settle();
        ok(w.document.querySelectorAll('.ui-jedit-warn').length === 1, 'one warning, never two');
        ok(w.document.querySelector('.ui-jedit-warn') && eds.length === 0, 'warned, no editor yet');
        w.document.querySelector('.ui-jedit-open-anyway').click();
        ok(eds.length === 1 && !w.document.querySelector('.ui-jedit-warn'), 'Open anyway opens');
    }

    // 7 a relink to nothing saves and says so, path by path
    {
        const { w, responses, eds } = world();
        const toasts = [];
        w.showToast = (t, lvl) => toasts.push([t, lvl]);
        responses.push({ status: 200, d: SOURCE });
        await w.TreeJsonEdit.open({ tab: 'state', path: 'qubits.q1' });
        await settle();
        eds[0].set(SOURCE.text.replace('2e-05', '3e-05'));
        responses.push({ status: 200, d: { ok: true, results: [{}], warnings: [
            { path: 'qubits.q1.alias', warning: 'points to nothing: #/x' }] } });
        w.TreeJsonEdit.save();
        await settle();
        const t = toasts[toasts.length - 1] || [];
        ok(/qubits\.q1\.alias points to nothing/.test(t[0] || '') && t[1] === 'warning',
           'the warning names the path');
    }

    {
        const {w, calls, responses, eds} = world();
        const toasts = []; w.showToast = (s) => toasts.push(s);
        responses.push({status:200,d:{...SOURCE,path:'value',text:'null'}});
        await w.TreeJsonEdit.open({tab:'state',path:'value'}); await settle();
        eds[0].set('[1,2]');
        responses.push({status:200,d:{ok:true,results:[{}]}});
        await w.TreeJsonEdit.save(); await settle();
        ok(calls[calls.length-1].body.tree_kind_change === true, 'JSON panel permits type changes');
        ok(toasts.some(t=>t.includes('type: null \u2192 list')), 'JSON panel announces the new type');
    }
    console.log('ALL OK (' + n + ' assertions)');
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
