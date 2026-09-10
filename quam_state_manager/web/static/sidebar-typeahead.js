/* Typeahead for a search box — a suggestion list under the caret's token.
 *
 * Customer, 2026-09-10: "사람은 m, mu, mul, mult, multi... 이렇게 순차적으로
 * 타이핑하잖아? youtube나 vscode에서 자동완성 후보군 보여주는 것처럼 즉각적으로
 * m을 치면 m으로 시작하는 parameter들이 쭉 아래로 팝업되게 할수있어?"
 *
 * Two stages, because ONE stage would suggest something that finds nothing.
 * A bare param name is routed to free text by the server's parser and matches
 * zero runs (tests/test_sidebar_param_search.py pins that on the real archive),
 * and `param:key` with no value matches every run carrying the key -- useless
 * on the 123 of 210 keys that have a single value. So: pick a KEY and the box
 * gets `key=` and immediately lists that key's VALUES; pick a value and the
 * token is complete and the search runs. Only the second step fires a request.
 *
 * Instant by construction: the whole vocabulary is 16 KB (measured on 1,766
 * real runs; still ~13 KB at 20,000 runs, because a vocabulary does not grow
 * with the archive the way an index does). It is fetched once per workspace
 * version and filtered here, so a keystroke costs no network at all.
 *
 * The widget is deliberately SOURCE-AGNOSTIC. `Typeahead.attach()` takes an
 * input and a `suggest(stage, key, stem)` function; the parameter vocabulary is
 * one caller. The Live-Edit and Json-Tree key completion the customer asked for
 * next is another caller of this same widget, not a second widget.
 *
 * Placement is body-level through the app's own `_anchorPopover`. Both other
 * options have already failed on THIS box: `position:absolute` is clipped by
 * `#sidebar { overflow-y:auto }` (style.css:1504), and a static in-flow list
 * pushed the run tree down the page (docs/120 item 3).
 */
'use strict';

window.Typeahead = (function () {
    var PANEL_ID = 'sm-typeahead';
    // 8, not 12: at 12 the panel is ~450 px and _anchorPopover flips it ABOVE
    // the box, covering the sidebar it belongs to (measured in real Chrome --
    // the box sits ~575 px down a 1000 px viewport). 8 rows fit underneath,
    // which is where a typeahead belongs, and the list scrolls for the rest.
    var MAX_ROWS = 8;

    function _panel() {
        var el = document.getElementById(PANEL_ID);
        if (el) return el;
        el = document.createElement('ul');
        el.id = PANEL_ID;
        el.className = 'sm-typeahead';
        el.setAttribute('role', 'listbox');
        el.setAttribute('aria-label', 'Suggestions');
        el.hidden = true;
        document.body.appendChild(el);
        return el;
    }

    // One binding per input, keyed by the element (several boxes will use this).
    var _bound = [];
    var _open = null;        // {input, cfg, rows, active, span, dismissed}

    function _close() {
        if (!_open) return;
        var p = _panel();
        p.hidden = true;
        p.textContent = '';
        var inp = _open.input;
        if (inp) {
            inp.removeAttribute('aria-activedescendant');
            inp.setAttribute('aria-expanded', 'false');
        }
        _open = null;
    }

    /* What the caret is finishing, classified. Returns null when we must not
       complete: no token begun, an unbalanced quote, a quote anywhere in the
       span (re-quoting a quoted span would move text the user cannot see), or
       another scope's token (`name:`). */
    function classify(text, caret) {
        var SQ = window.SearchQuery;
        if (!SQ || !SQ.caretSpan) return null;
        var span = SQ.caretSpan(text, caret);
        if (!span) return null;
        var raw = text.slice(span[0], span[1]);
        if (raw.indexOf('"') >= 0) return null;
        var body = span[2], neg = '';
        if (body.charAt(0) === '-') { neg = '-'; body = body.slice(1); }
        var scope = '';
        var m = /^(param|p):/i.exec(body);
        if (m) { scope = m[0]; body = body.slice(m[0].length); }
        else if (body.indexOf(':') >= 0) return null;   // some other scope
        var eq = body.indexOf('=');
        return {
            start: span[0], end: span[1], neg: neg, scope: scope,
            stage: eq >= 0 ? 'value' : 'key',
            key: eq >= 0 ? body.slice(0, eq) : '',
            stem: eq >= 0 ? body.slice(eq + 1) : body
        };
    }

    /* A PLAIN box (Live State Edit, Json Tree View) has no scope and no
       `key=value` grammar -- its query is AND-ed words, so the whole token is
       the stem and a completion is one step. Classifying it with the sidebar's
       rules would refuse a perfectly ordinary word the moment it contained a
       colon or an equals sign. */
    function classifyPlain(text, caret) {
        var SQ = window.SearchQuery;
        if (!SQ || !SQ.caretSpan) return null;
        var span = SQ.caretSpan(text, caret);
        if (!span) return null;
        if (text.slice(span[0], span[1]).indexOf('"') >= 0) return null;
        return { start: span[0], end: span[1], neg: '', scope: '',
                 stage: 'key', key: '', stem: span[2] };
    }

    /* Prefix hits first, then the rest by substring — "m을 치면 m으로 시작하는"
       is the ask, and a substring-only rank would bury `multiplexed` under
       every key that merely contains an m. */
    function rank(names, stem) {
        var lo = String(stem || '').toLowerCase();
        var pre = [], sub = [];
        for (var i = 0; i < names.length; i++) {
            var n = String(names[i]);
            var l = n.toLowerCase();
            if (!lo) { pre.push(n); continue; }
            var at = l.indexOf(lo);
            if (at === 0) pre.push(n);
            else if (at > 0) sub.push(n);
        }
        return { pre: pre, sub: sub };
    }

    function _row(text, meta, cls) {
        var li = document.createElement('li');
        li.className = 'sm-th-row' + (cls ? ' ' + cls : '');
        li.setAttribute('role', 'option');
        var a = document.createElement('span');
        a.className = 'sm-th-label';
        a.textContent = text;
        li.appendChild(a);
        if (meta) {
            var b = document.createElement('span');
            b.className = 'sm-th-meta';
            b.textContent = meta;
            li.appendChild(b);
        }
        return li;
    }

    function _render(st) {
        var p = _panel();
        p.textContent = '';
        var items = st.items;
        for (var i = 0; i < items.length; i++) {
            var it = items[i];
            var li = _row(it.label, it.meta, it.note ? 'sm-th-note' : '');
            li.id = PANEL_ID + '-' + i;
            if (!it.note) {
                li.setAttribute('data-i', String(i));
                li.addEventListener('mousedown', function (ev) {
                    // mousedown + preventDefault: focus never leaves the box, so
                    // no blur, no focus-restore dance -- and the sidebar's poll
                    // tick stays deferred while the box has focus, which is what
                    // stops a tree swap landing mid-selection.
                    ev.preventDefault();
                    _accept(Number(this.getAttribute('data-i')));
                });
            }
            p.appendChild(li);
        }
        p.hidden = items.length === 0;
        if (!p.hidden && window._anchorPopover) {
            try { window._anchorPopover(p, st.input); } catch (e) {}
        }
        _paintActive(st);
    }

    function _paintActive(st) {
        var p = _panel();
        var rows = p.querySelectorAll('.sm-th-row');
        for (var i = 0; i < rows.length; i++) {
            rows[i].classList.toggle('active', i === st.active);
        }
        if (st.active >= 0 && rows[st.active]) {
            st.input.setAttribute('aria-activedescendant', rows[st.active].id);
            try { rows[st.active].scrollIntoView({ block: 'nearest' }); } catch (e) {}
        } else {
            st.input.removeAttribute('aria-activedescendant');
        }
    }

    function _accept(i) {
        if (!_open) return;
        var st = _open, it = st.items[i];
        if (!it || it.note || it.insert == null) return;
        var inp = st.input, v = inp.value;
        inp.value = v.slice(0, st.span.start) + it.insert + v.slice(st.span.end);
        var caret = st.span.start + it.insert.length;
        try { inp.setSelectionRange(caret, caret); } catch (e) {}
        _close();
        if (it.fire) {
            // A complete token: let the box's own listeners run the search, and
            // do NOT re-open on it. The re-render would suggest the very value
            // just accepted, so the panel sat there after the search had run
            // (measured in real Chrome). Same mechanism Escape uses.
            st.cfg.dismissed = it.insert;
            inp.dispatchEvent(new Event('input', { bubbles: true }));
        } else {
            // A key accept leaves `key=` — an intermediate state that matches
            // nothing. Re-open on the values WITHOUT firing: a programmatic
            // value assignment fires no input event, so nothing was requested.
            _refresh(inp, st.cfg);
        }
    }

    function _refresh(input, cfg) {
        var st = (cfg.plain ? classifyPlain : classify)(input.value, input.selectionStart);
        if (!st) { _close(); return; }
        // The dismissal is compared HERE and nowhere else. Clearing it in the
        // input handler instead wiped the flag with the very event an accept
        // dispatches, so a completed token immediately re-suggested itself
        // (measured in real Chrome). Same text -> stay closed; the moment the
        // user types on, it is a different token and the panel is free again.
        if (cfg.dismissed != null) {
            if (cfg.dismissed === input.value.slice(st.start, st.end)) {
                _close(); return;
            }
            cfg.dismissed = null;
        }
        var out = cfg.suggest(st.stage, st.key, st.stem, st);
        if (!out || !out.items || !out.items.length) { _close(); return; }
        var items = out.items.slice(0, MAX_ROWS + (out.note ? 0 : 0));
        if (out.note) items = items.concat([{ label: out.note, note: true }]);
        _open = { input: input, cfg: cfg, items: items, active: -1, span: st };
        input.setAttribute('aria-expanded', 'true');
        _render(_open);
    }

    function attach(inputId, cfg) {
        if (_bound.indexOf(inputId) >= 0) return;
        _bound.push(inputId);

        // Delegated on document: this file evaluates in <head>, before the
        // sidebar exists (docs/149 — a load-time binding here is silently dead,
        // and a pre-built-DOM harness hides it).
        document.addEventListener('input', function (e) {
            if (!e.target || e.target.id !== inputId) return;
            if (e.isComposing) return;    // Hangul fires per jamo; keys are ASCII
            _refresh(e.target, cfg);
        });
        document.addEventListener('compositionend', function (e) {
            if (e.target && e.target.id === inputId) _refresh(e.target, cfg);
        });
        document.addEventListener('keyup', function (e) {
            // A caret move with no input event still changes the active token.
            if (!e.target || e.target.id !== inputId) return;
            if (e.key === 'ArrowLeft' || e.key === 'ArrowRight'
                || e.key === 'Home' || e.key === 'End') _refresh(e.target, cfg);
        });
        document.addEventListener('keydown', function (e) {
            var k = e.key;
            // Key first, no DOM query: this runs on every keystroke in the app.
            if (k !== 'ArrowDown' && k !== 'ArrowUp' && k !== 'Enter'
                && k !== 'Tab' && k !== 'Escape') return;
            if (!_open) return;
            if (!e.target || e.target.id !== inputId) return;
            if (e.ctrlKey || e.metaKey || e.altKey) return;   // Ctrl+K still opens the palette
            var st = _open, last = st.items.length - 1;
            while (last >= 0 && st.items[last].note) last--;
            if (k === 'ArrowDown') {
                st.active = Math.min(st.active + 1, last);
                _paintActive(st); e.preventDefault();
            } else if (k === 'ArrowUp') {
                st.active = Math.max(st.active - 1, -1);
                _paintActive(st); e.preventDefault();
            } else if (k === 'Escape') {
                cfg.dismissed = st.input.value.slice(st.span.start, st.span.end);
                _close(); e.preventDefault();
            } else if (k === 'Enter' || k === 'Tab') {
                // NOTHING is preselected, so with no active row Enter means
                // exactly what it meant yesterday. That costs one keystroke
                // versus VS Code and is the price of not redefining Enter.
                if (st.active < 0) { if (k === 'Tab') _close(); return; }
                _accept(st.active); e.preventDefault();
            }
        });
        document.addEventListener('focusout', function (e) {
            if (!_open || !e.target || e.target.id !== inputId) return;
            var to = e.relatedTarget;
            if (to && to.closest && to.closest('#' + PANEL_ID)) return;
            _close();
        });
        document.addEventListener('pointerdown', function (e) {
            if (!_open) return;
            var t = e.target;
            if (t && t.closest && (t.closest('#' + PANEL_ID) || t.id === inputId)) return;
            _close();
        }, true);
        // NOT a blanket close on htmx swaps. This box fires `/workspace/tree`
        // on a 250 ms debounce, so every keystroke swaps #sidebar-tree -- a
        // close here dismissed the panel ~250 ms after it opened, every single
        // time (measured in real Chrome; the panel was correct and invisible).
        // The panel is body-level, so a tree swap cannot destroy it; the only
        // swap that matters is one that takes the INPUT away.
        document.addEventListener('htmx:afterSwap', function () {
            if (!_open) return;
            // There are THREE instances, each with this handler and its own id.
            // Without this line the Live-Edit instance's handler answered for a
            // panel the SIDEBAR opened, decided the input was not its own, and
            // closed it (caught by the harness once it dispatched a swap at
            // all -- the browser pass never saw it because only one box was
            // ever attached in the pages it drove).
            if (_open.input.id !== inputId) return;
            var el = document.getElementById(inputId);
            if (!el || el !== _open.input) { _close(); return; }
            var p = _panel();
            if (!p.hidden && window._anchorPopover) {
                try { window._anchorPopover(p, el); } catch (e) {}
            }
        });
        window.addEventListener('resize', _close);
    }

    return { attach: attach, classify: classify, classifyPlain: classifyPlain, rank: rank,
             close: _close, panelId: PANEL_ID,
             _state: function () { return _open; } };
})();

/* ── consumer 1: the sidebar's experiment-parameter vocabulary ─────────── */
window.SidebarTypeahead = (function () {
    var INPUT = 'sidebar-filter-input';
    var vocab = null;          // {v, keys:[{k,n,v:[[val,count]],more}], hydrating}
    var byKey = null;
    var loading = false;

    function _index() {
        byKey = {};
        (vocab && vocab.keys ? vocab.keys : []).forEach(function (d) { byKey[d.k] = d; });
    }

    function load(force) {
        if (loading) return;
        loading = true;
        var url = '/workspace/param-vocab';
        if (!force && vocab && vocab.v != null) url += '?v=' + encodeURIComponent(vocab.v);
        fetch(url, { headers: { 'Accept': 'application/json' } }).then(function (r) {
            if (r.status === 204) return null;        // unchanged
            if (!r.ok) return null;
            return r.json();
        }).then(function (j) {
            if (j) { vocab = j; _index(); }
            loading = false;
            var el = document.getElementById(INPUT);
            if (j && el && document.activeElement === el) {
                window.Typeahead.close();
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }
        }).catch(function () { loading = false; });
    }

    function refresh(v) {
        if (vocab && String(vocab.v) === String(v)) return;
        load(true);
    }

    function suggest(stage, key, stem) {
        if (!vocab) {
            load(false);
            return { items: [{ label: 'loading parameters…', note: true }] };
        }
        var PV = window.__paramVocabInsert;   // the insert rule, mirrored below
        if (stage === 'key') {
            var names = vocab.keys.map(function (d) { return d.k; });
            var r = window.Typeahead.rank(names, stem);
            var picked = r.pre.concat(r.sub).slice(0, 8);
            if (!picked.length) {
                return vocab.hydrating
                    ? { items: [{ label: '⌛ indexing runs — parameters are still being read', note: true }] }
                    : null;
            }
            return {
                items: picked.map(function (k) {
                    var d = byKey[k];
                    var nv = d.v.length + (d.more || 0);
                    return {
                        label: k,
                        meta: nv + (nv === 1 ? ' value · ' : ' values · ') + d.n + ' runs',
                        insert: k + '=', fire: false
                    };
                }),
                note: vocab.hydrating ? '⌛ still indexing — more may appear' : ''
            };
        }
        // value stage: the key the user typed may be a prefix of the real one
        var exact = byKey[key];
        var d = exact || vocab.keys.filter(function (x) {
            return x.k.toLowerCase() === String(key).toLowerCase();
        })[0];
        if (!d) return null;
        var vals = d.v.map(function (p) { return p[0]; });
        var rr = window.Typeahead.rank(vals, stem);
        var order = rr.pre.concat(rr.sub).slice(0, 8);
        if (!order.length) return null;
        var counts = {};
        d.v.forEach(function (p) { counts[p[0]] = p[1]; });
        return {
            items: order.map(function (val) {
                return {
                    label: val, meta: counts[val] + ' runs',
                    insert: PV ? PV(d.k, val) : (d.k + '=' + val), fire: true
                };
            }),
            note: d.more ? ('…and ' + d.more + ' more values') : ''
        };
    }

    function init() {
        if (!window.Typeahead) return;
        window.Typeahead.attach(INPUT, { suggest: suggest });
    }
    init();
    return { load: load, refresh: refresh, suggest: suggest,
             _vocab: function () { return vocab; } };
})();

/* The insert rule, mirrored from core/param_vocab.insert_token and pinned
   against it. Kept tiny and beside its only caller: a value the grammar cannot
   carry never reaches the client (the server omits it), so this only has to
   reproduce the SHAPE. */
window.__paramVocabInsert = function (key, value) {
    if (key == null || value == null) return null;
    key = String(key); value = String(value);
    if (!key || key.indexOf('"') >= 0 || value.indexOf('"') >= 0) return null;
    if (key.indexOf('=') >= 0 || key.indexOf(':') >= 0) return null;
    var bare = /^[A-Za-z][\w.\-]*$/.test(key);
    var scope = bare ? '' : 'p:';
    if (scope && value !== value.replace(/^\s+|\s+$/g, '')) return null;
    if (!value) return null;
    var tok = scope + key + '=' + value;
    if (/[\s,]/.test(tok)) tok = '"' + tok + '"';
    return tok;
};

/* ── consumer 2: Live State Edit — the chip's own column names ───────────
 *
 * Customer, 2026-09-10: "live edit 하고 json tree view에서 -- 이게 진짜 SM의
 * 가치인데 -- 사람이 ampl까지만 치면 amplitude에 관련된 json key들이 모두 다
 * 뜰수있게 만들수있겠어?"
 *
 * No new data and no request: the column names are the <th>s already on the
 * page, for every grid on it (qubits, pairs, and each discovered collection).
 * That is also what makes it adaptive by construction -- a key a lab added by
 * hand is a column, so it is a suggestion.
 *
 * One stage: this box's grammar is plain AND-ed words, so a column name IS a
 * complete, useful token. Nothing like the sidebar's `key=value` two-step is
 * needed or wanted here.
 */
window.BulkTypeahead = (function () {
    var INPUT = 'bulk-search';

    /* WORDS, not whole labels. This box's tokenizer is `split(/\s+/)` with no
       quote handling (search-query.js:47), so inserting `op · x180_DragCosine ·
       amplitude` would silently become three AND-ed tokens -- measured. A word
       is one token by construction, and a word is also what was asked for:
       type `ampl` and see every amplitude key there is.

       Counted over the column HEADERS, which exist for every column including
       the server-cold ones, so the vocabulary does not depend on how far the
       grid has been scrolled. */
    function vocab() {
        var counts = {};
        var heads = document.querySelectorAll('#table-pane th.bulk-col-head');
        for (var i = 0; i < heads.length; i++) {
            var lab = heads[i].querySelector('.bulk-col-label');
            var text = (lab ? lab.textContent : '') + ' '
                     + (heads[i].getAttribute('data-col-key') || '');
            var words = text.split(/[^A-Za-z0-9]+/);
            var seen = {};
            for (var w = 0; w < words.length; w++) {
                var word = words[w];
                if (word.length < 3 || /^\d+$/.test(word)) continue;
                if (seen[word]) continue;
                seen[word] = 1;
                counts[word] = (counts[word] || 0) + 1;
            }
        }
        return counts;
    }

    function suggest(stage, key, stem) {
        if (!stem) return null;
        var v = vocab();
        var names = Object.keys(v);
        if (!names.length) return null;
        var r = window.Typeahead.rank(names, stem);
        var all = r.pre.concat(r.sub);
        var picked = all.slice(0, 8);
        if (!picked.length) return null;
        return {
            items: picked.map(function (n) {
                return { label: n,
                         meta: v[n] + (v[n] === 1 ? ' column' : ' columns'),
                         insert: n, fire: true };
            }),
            note: all.length > picked.length
                ? ('…and ' + (all.length - picked.length) + ' more') : ''
        };
    }

    if (window.Typeahead) window.Typeahead.attach(INPUT, { suggest: suggest, plain: true });
    return { suggest: suggest, vocab: vocab };
})();

/* ── consumer 3: Json Tree View — every key name in the chip ─────────────
 *
 * The vocabulary is derived from `_treeData`, which the explorer already holds
 * -- the very object the tree renders from -- so nothing is fetched and nothing
 * can disagree with what is on screen. Numeric segments (list indices) are
 * dropped: `confusion.0.1` contributes `confusion`, not `0` and `1`. Without
 * that a 5-qubit chip offers 2,379 "keys" of which 2,192 are digits (measured).
 *
 * Cached against the model object itself, so a chip switch or a re-render
 * rebuilds it and a keystroke never walks 12,000 leaves twice.
 */
window.TreeTypeahead = (function () {
    var INPUT = 'explorer-search';
    var _cacheFor = null, _cache = null;

    /* The Json Tree View renders state and wiring into TWO containers
       (app.js:16805-16807), so the vocabulary is the union -- a wiring key is a
       key a user types too. `_treeData` is the object the tree itself renders
       from, so nothing here can disagree with what is on screen. */
    var IDS = ['explorer-tree-state', 'explorer-tree-wiring', 'json-panel-tree'];
    function _models() {
        var out = [];
        for (var i = 0; i < IDS.length; i++) {
            var c = document.getElementById(IDS[i]);
            if (c && c._treeData) out.push(c._treeData);
        }
        return out;
    }

    function vocab() {
        var ms = _models();
        if (!ms.length) return null;
        var tag = ms.length + ':' + (ms[0] === _cacheFor);
        if (_cacheFor === ms[0] && _cache) return _cache;
        var counts = {};
        var stack = ms.slice(), guard = 0;
        while (stack.length && guard++ < 400000) {
            var node = stack.pop();
            if (node && typeof node === 'object') {
                if (Object.prototype.toString.call(node) === '[object Array]') {
                    for (var i = 0; i < node.length; i++) stack.push(node[i]);
                } else {
                    for (var k in node) {
                        if (!Object.prototype.hasOwnProperty.call(node, k)) continue;
                        // a list index is not a key anyone types
                        if (!/^\d+$/.test(k)) counts[k] = (counts[k] || 0) + 1;
                        stack.push(node[k]);
                    }
                }
            }
        }
        _cacheFor = ms[0]; _cache = counts;
        return counts;
    }

    function suggest(stage, key, stem) {
        if (!stem) return null;
        var v = vocab();
        if (!v) return null;
        var names = Object.keys(v);
        if (!names.length) return null;
        var r = window.Typeahead.rank(names, stem);
        var all = r.pre.concat(r.sub);
        var picked = all.slice(0, 8);
        if (!picked.length) return null;
        return {
            items: picked.map(function (n) {
                return { label: n, meta: v[n] + (v[n] === 1 ? ' place' : ' places'),
                         insert: n, fire: true };
            }),
            note: all.length > picked.length
                ? ('…and ' + (all.length - picked.length) + ' more keys') : ''
        };
    }

    if (window.Typeahead) window.Typeahead.attach(INPUT, { suggest: suggest, plain: true });
    return { suggest: suggest, vocab: vocab };
})();
