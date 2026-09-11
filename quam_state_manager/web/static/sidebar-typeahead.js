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
        // The FIRST of `= > <`, scanned rather than regexed, because the
        // scope form carries keys the bare-key pattern deliberately rejects
        // (`p:_foo=bar`) and a key regex here would send them to the wrong
        // stage. `>=` and `<=` are two characters; `=` is one.
        var oi = -1, op = '';
        for (var ci = 0; ci < body.length; ci++) {
            var ch = body.charAt(ci);
            if (ch === '=' || ch === '>' || ch === '<') {
                oi = ci; op = ch;
                if (ch !== '=' && body.charAt(ci + 1) === '=') op = ch + '=';
                break;
            }
        }
        return {
            start: span[0], end: span[1], neg: neg, scope: scope,
            stage: oi >= 0 ? 'value' : 'key',
            op: oi >= 0 ? op : '',
            key: oi >= 0 ? body.slice(0, oi) : '',
            stem: oi >= 0 ? body.slice(oi + op.length) : body
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
        // Deliberately NOT the operator split: on a box whose grammar is
        // AND-ed words, `a>=b` is one word, and completing "values of a" would
        // be a different query from the one being typed.
        return { start: span[0], end: span[1], neg: '', scope: '', op: '',
                 stage: 'key', key: '', stem: span[2] };
    }

    /* ── a typo still finds the key ────────────────────────────────────
     *
     * Customer, 2026-09-10: "특히 파라미터를 입력하면 사실 많은 사람들이
     * multiplzed...뭐 이런식으로 오타 나잖아? 이렇게 오타로 해도 vscode나
     * 유투브는 알아서 비슷한거 유사한거 리스팅을 해주던데?"
     *
     * The customer's own example settles the algorithm. `multiplzed` is NOT a
     * subsequence of `multiplexed` -- there is no `z` in the target -- so any
     * subsequence matcher returns zero rows on the very word that was asked
     * about. Damerau-Levenshtein(multiplzed, multiplexed) is 2, so a
     * distance-1 cap misses it too. What is needed is edit distance with k=2.
     *
     * Distance to a PREFIX of the candidate, not to the whole name, because
     * typing is a forward process: the stem is a prefix in progress. That also
     * makes the cost O(stem x band) -- independent of how long the candidate
     * name is -- where a whole-string similarity would pay for the name and
     * would score `multz` against `multiplexed` as barely related.
     *
     * OSA transposition is one line in the inner loop and turns `mutliplexed`
     * and `wiat_time` into distance 1 instead of 2, which matters because at
     * k=2 a single transposition would otherwise rank level with a genuine
     * two-edit neighbour.
     *
     * These are the constants a different lab's key set might want retuned,
     * which is why each is named: */
    var FUZZ_MIN_STEM = 4;   // below 4 characters a miss IS a miss
    var FUZZ_TRIGGER = 3;    // guess only while the honest hits are this few
    function _maxEdits(n) { return n < FUZZ_MIN_STEM ? 0 : (n < 7 ? 1 : 2); }

    /* A 32-bit character-presence set. Bucket collisions (`'0'` and `'p'` both
       land on bit 16) only WEAKEN the filter -- they can never make it reject a
       real match, which is the only property that matters here. */
    function _mask(l) {
        var m = 0;
        for (var i = 0; i < l.length; i++) m |= 1 << (l.charCodeAt(i) & 31);
        return m;
    }

    function _popcount(x) {
        x = x - ((x >> 1) & 0x55555555);
        x = (x & 0x33333333) + ((x >> 2) & 0x33333333);
        x = (x + (x >> 4)) & 0x0f0f0f0f;
        return (x * 0x01010101) >> 24;
    }

    /* Banded prefix-OSA distance: the cheapest way to turn `s` into ANY prefix
       of `t`, capped at k. Returns k+1 for "further than k", so the caller
       never has to distinguish "expensive" from "impossible". */
    function _prefixDist(s, t, k) {
        var m = s.length;
        var n = Math.min(t.length, m + k);
        var INF = k + 1;
        if (n < m - k) return INF;
        var prev2 = null, prev = new Array(n + 1), cur = new Array(n + 1);
        for (var j = 0; j <= n; j++) prev[j] = j <= k ? j : INF;
        for (var i = 1; i <= m; i++) {
            var from = i - k > 1 ? i - k : 1;
            var to = i + k < n ? i + k : n;
            for (var z = 0; z <= n; z++) cur[z] = INF;
            cur[0] = i <= k ? i : INF;
            var best = INF;
            for (var j = from; j <= to; j++) {
                var d = prev[j - 1] + (s.charCodeAt(i - 1) === t.charCodeAt(j - 1) ? 0 : 1);
                var a = prev[j] + 1; if (a < d) d = a;
                var b = cur[j - 1] + 1; if (b < d) d = b;
                if (i > 1 && j > 1 && prev2
                    && s.charCodeAt(i - 1) === t.charCodeAt(j - 2)
                    && s.charCodeAt(i - 2) === t.charCodeAt(j - 1)) {
                    var tr = prev2[j - 2] + 1; if (tr < d) d = tr;
                }
                if (d > INF) d = INF;
                cur[j] = d;
                if (d < best) best = d;
            }
            if (best >= INF) return INF;      // the whole row is over budget
            var tmp = prev2; prev2 = prev; prev = cur;
            cur = tmp || new Array(n + 1);
        }
        var out = INF, lo = m - k > 0 ? m - k : 0;
        for (var j2 = lo; j2 <= n; j2++) if (prev[j2] < out) out = prev[j2];
        return out;
    }

    /* Lower-cased names + their character masks, computed once per vocabulary
       rather than once per keystroke. `cacheKey` is the caller's own statement
       of when its vocabulary changed; without one nothing is remembered. */
    var _preps = {}, _prepN = 0;
    function prepare(names, cacheKey) {
        if (cacheKey != null && _preps[cacheKey]
            && _preps[cacheKey].names === names) return _preps[cacheKey];
        var lower = new Array(names.length), mask = new Array(names.length);
        for (var i = 0; i < names.length; i++) {
            var l = String(names[i]).toLowerCase();
            lower[i] = l; mask[i] = _mask(l);
        }
        var p = { names: names, lower: lower, mask: mask };
        if (cacheKey != null) {
            if (_prepN > 8) { _preps = {}; _prepN = 0; }
            _preps[cacheKey] = p; _prepN++;
        }
        return p;
    }

    /* Prefix hits first, then the rest by substring — "m을 치면 m으로 시작하는"
       is the ask, and a substring-only rank would bury `multiplexed` under
       every key that merely contains an m. `fuzz` is a THIRD bin, never mixed
       into the first two: a guess must not be able to look like a match. */
    function rank(names, stem, cacheKey) {
        var lo = String(stem || '').toLowerCase();
        var prep = prepare(names, cacheKey);
        var pre = [], sub = [], hit = null;
        for (var i = 0; i < names.length; i++) {
            var n = String(names[i]);
            if (!lo) { pre.push(n); continue; }
            var at = prep.lower[i].indexOf(lo);
            if (at === 0) pre.push(n);
            else if (at > 0) sub.push(n);
            else continue;
            if (!hit) hit = {};
            hit[i] = 1;
        }
        var fuzz = [];
        if (lo.length >= FUZZ_MIN_STEM && (pre.length + sub.length) < FUZZ_TRIGGER) {
            fuzz = _fuzzy(prep, lo, hit);
        }
        return { pre: pre, sub: sub, fuzz: fuzz };
    }

    function _fuzzy(prep, lo, hit) {
        var k = _maxEdits(lo.length);
        if (!k) return [];
        var want = _mask(lo), out = [];
        for (var i = 0; i < prep.lower.length; i++) {
            if (hit && hit[i]) continue;                    // already an honest hit
            var l = prep.lower[i];
            // Two rejections, both LOWER BOUNDS on the real distance, so
            // neither can throw away a true match:
            if (l.length < lo.length - k) continue;         // too short to reach
            if (_popcount(want & ~prep.mask[i]) > k) continue;  // characters it has not got
            var d = _prefixDist(lo, l, k);
            if (d <= k) out.push({ n: String(prep.names[i]), d: d, i: i, len: l.length });
        }
        // distance, then the shorter name, then the order the caller gave --
        // which for the sidebar is coverage-descending, so the tie-break is a
        // frequency ordering for free.
        out.sort(function (a, b) {
            return (a.d - b.d) || (a.len - b.len) || (a.i - b.i);
        });
        return out.map(function (o) { return o.n; });
    }

    /* The one place a guessed block is described, so every box says the same
       thing. It never claims the guess is a match. */
    function fuzzNote(honest) {
        return honest ? 'closest, in case of a typo' : 'nothing matched — closest:';
    }

    /* Honest rows, then -- only if there is room left -- a separator and the
       guesses beneath it. Every box composes its panel through here, so no box
       can accidentally present a guess as a match. */
    function compose(r, mk) {
        var honest = r.pre.concat(r.sub);
        var picked = honest.slice(0, MAX_ROWS);
        var items = [];
        for (var i = 0; i < picked.length; i++) items.push(mk(picked[i]));
        var room = MAX_ROWS - items.length - 1;   // -1 for the separator itself
        if (room > 0 && r.fuzz && r.fuzz.length) {
            items.push({ label: fuzzNote(picked.length), note: true, cls: 'sm-th-fuzzsep' });
            var g = r.fuzz.slice(0, room);
            for (var j = 0; j < g.length; j++) {
                var it = mk(g[j]);
                it.cls = 'sm-th-fuzzy';
                it.title = 'the closest thing to what you typed — not an exact match';
                items.push(it);
            }
        }
        return { items: items, hidden: honest.length - picked.length };
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
            var li = _row(it.label, it.meta,
                          (it.note ? 'sm-th-note' : '') + (it.cls ? ' ' + it.cls : ''));
            li.id = PANEL_ID + '-' + i;
            if (it.title) li.title = it.title;
            if (!it.note) {
                li.setAttribute('data-i', String(i));
                li.addEventListener('mousedown', function (ev) {
                    // mousedown + preventDefault: focus never leaves the box, so
                    // no blur, no focus-restore dance -- and the sidebar's poll
                    // tick stays deferred while the box has focus, which is what
                    // stops a tree swap landing mid-selection.
                    ev.preventDefault();
                    _accept(Number(this.getAttribute('data-i')), true);
                });
            }
            p.appendChild(li);
        }
        _keysRow(p, st);
        p.hidden = items.length === 0;
        if (!p.hidden && window._anchorPopover) {
            try { window._anchorPopover(p, st.input); } catch (e) {}
        }
        _paintActive(st);
    }

    /* One muted line saying what the keys do — the customer's complaint is
       that nobody knows how to use this, and a completion panel that never
       names its own keys is the smallest version of that. Tab differs from
       Enter only where a key can be completed WITHOUT its value, so the
       two-stage boxes say so and the plain ones do not. */
    function _keysRow(p, st) {
        if (!p.children.length) return;
        var li = document.createElement('li');
        li.className = 'sm-th-keys';
        li.setAttribute('aria-hidden', 'true');
        var parts = ['<kbd>\u2191\u2193</kbd> choose', '<kbd>Enter</kbd> pick'];
        if (!st.cfg.plain) parts.push('<kbd>Tab</kbd> key only');
        parts.push('<kbd>Esc</kbd> close');
        li.innerHTML = parts.map(function (x) { return '<span>' + x + '</span>'; }).join('');
        p.appendChild(li);
    }

    function _paintActive(st) {
        var p = _panel();
        var rows = p.querySelectorAll('.sm-th-row');   // the keys row is not one
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

    /* Arrow navigation SKIPS note rows. Since the fuzzy block sits under its
       own separator, a note is no longer only a trailing line -- landing on one
       would be a keypress that does nothing. */
    function _step(st, dir) {
        var i = st.active;
        for (var n = 0; n < st.items.length; n++) {
            i += dir;
            if (i < 0) return -1;
            if (i >= st.items.length) return st.active;
            if (!st.items[i].note) return i;
        }
        return st.active;
    }

    /* `whole` is Enter or a click -- a commit gesture. On a key that has only
       ONE value there is nothing to choose, so those finish the token outright
       (121 of the customer's 210 keys are single-valued). Tab still inserts
       `key=` alone, for anyone typing a value of their own. */
    function _accept(i, whole) {
        if (!_open) return;
        var st = _open, it = st.items[i];
        if (!it || it.note) return;
        var ins = (whole && it.wholeInsert != null) ? it.wholeInsert : it.insert;
        var fire = (whole && it.wholeInsert != null) ? !!it.wholeFire : !!it.fire;
        if (ins == null) return;
        var inp = st.input, v = inp.value;
        inp.value = v.slice(0, st.span.start) + ins + v.slice(st.span.end);
        var caret = st.span.start + ins.length;
        try { inp.setSelectionRange(caret, caret); } catch (e) {}
        _close();
        if (fire) {
            // A complete token: let the box's own listeners run the search, and
            // do NOT re-open on it. The re-render would suggest the very value
            // just accepted, so the panel sat there after the search had run
            // (measured in real Chrome). Same mechanism Escape uses.
            st.cfg.dismissed = ins;
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

        /* The combobox roles. `aria-expanded` was already maintained, but
           without these a screen reader is never told the box HAS a list, so
           the whole feature is invisible to one. Set lazily, because this file
           evaluates before the sidebar exists (docs/149). */
        var _roled = false;
        function _role(el) {
            if (_roled || !el) return;
            _roled = true;
            el.setAttribute('role', 'combobox');
            el.setAttribute('aria-autocomplete', 'list');
            el.setAttribute('aria-controls', PANEL_ID);
            el.setAttribute('aria-expanded', 'false');
        }
        document.addEventListener('focusin', function (e) {
            if (e.target && e.target.id === inputId) _role(e.target);
        });

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
            var st = _open;
            if (k === 'ArrowDown') {
                st.active = _step(st, +1);
                _paintActive(st); e.preventDefault();
            } else if (k === 'ArrowUp') {
                st.active = _step(st, -1);
                _paintActive(st); e.preventDefault();
            } else if (k === 'Escape') {
                cfg.dismissed = st.input.value.slice(st.span.start, st.span.end);
                _close(); e.preventDefault();
            } else if (k === 'Enter' || k === 'Tab') {
                // NOTHING is preselected, so with no active row Enter means
                // exactly what it meant yesterday. That costs one keystroke
                // versus VS Code and is the price of not redefining Enter.
                if (st.active < 0) { if (k === 'Tab') _close(); return; }
                _accept(st.active, k === 'Enter'); e.preventDefault();
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

    return { attach: attach, classify: classify, classifyPlain: classifyPlain,
             rank: rank, prepare: prepare, fuzzNote: fuzzNote, compose: compose,
             prefixDist: _prefixDist, maxEdits: _maxEdits, MAX_ROWS: MAX_ROWS,
             close: _close, panelId: PANEL_ID,
             _state: function () { return _open; } };
})();

/* ── consumer 1: the sidebar's experiment-parameter vocabulary ─────────── */
window.SidebarTypeahead = (function () {
    var INPUT = 'sidebar-filter-input';
    var vocab = null;          // {v, keys:[{k,n,v:[[val,count]],more}], hydrating}
    var byKey = null;
    var loading = false;

    var names = [];
    function _index() {
        byKey = {};
        names = [];
        (vocab && vocab.keys ? vocab.keys : []).forEach(function (d) {
            byKey[d.k] = d; names.push(d.k);
        });
        // Held, not rebuilt per keystroke: `Typeahead.prepare` remembers the
        // lower-cased names and their character masks against THIS array's
        // identity, and a fresh `.map()` every call would never hit that cache.
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

    /* A finite number, or null -- the twin of routes._as_number and of
       dataset-virtual's `_num`. A boolean is not a magnitude. */
    function _num(v) {
        if (typeof v === 'boolean' || v == null || v === '') return null;
        var f = typeof v === 'number' ? v : Number(String(v).trim());
        return (typeof f === 'number' && isFinite(f)) ? f : null;
    }

    /* The predicate `op`+`want` names, or null when it cannot be evaluated yet
       (nothing typed after the operator, or a right side that is not a number).
       The SAME rules routes._param_hit applies, so the count this panel prints
       is the count the search will return. */
    function _pred(op, want) {
        if (op === '=' && String(want).indexOf('..') >= 0) {
            var dd = String(want).indexOf('..');
            var a = _num(String(want).slice(0, dd)), b = _num(String(want).slice(dd + 2));
            if (a == null || b == null) return null;
            var lo = Math.min(a, b), hi = Math.max(a, b);
            return function (n) { return n >= lo && n <= hi; };
        }
        var w = _num(want);
        if (w == null) return null;
        if (op === '>=') return function (n) { return n >= w; };
        if (op === '>') return function (n) { return n > w; };
        if (op === '<=') return function (n) { return n <= w; };
        if (op === '<') return function (n) { return n < w; };
        return null;
    }

    function suggest(stage, key, stem, st) {
        if (!vocab) {
            load(false);
            return { items: [{ label: 'loading parameters…', note: true }] };
        }
        var PV = window.__paramVocabInsert;   // the insert rule, mirrored below
        if (stage === 'key') {
            var r = window.Typeahead.rank(names, stem, 'sb-keys:' + vocab.v);
            var c = window.Typeahead.compose(r, function (k) {
                var d = byKey[k];
                var nv = d.v.length + (d.more || 0);
                var it = { label: k, insert: k + '=', fire: false };
                if (nv === 1 && d.v.length === 1) {
                    // Nothing to choose. Enter (or a click) finishes the token;
                    // Tab still leaves `key=` for a value of your own.
                    var whole = PV ? PV(d.k, d.v[0][0]) : (d.k + '=' + d.v[0][0]);
                    it.meta = '= ' + d.v[0][0] + ' · ' + d.n + ' runs';
                    if (whole) { it.wholeInsert = whole; it.wholeFire = true; }
                } else {
                    it.meta = nv + ' values · '
                            + (d.num ? d.min + ' … ' + d.max + ' · ' : '')
                            + d.n + ' runs';
                }
                return it;
            });
            // docs/182: the words a PERSON typed, under their own heading so
            // they can never be mistaken for a parameter key. They come LAST
            // because a parameter key is what this box mostly completes.
            var tagged = (window.TagVocab ? window.TagVocab.items(stem) : []);
            if (tagged.length) {
                // The widget caps the panel at MAX_ROWS, so without a
                // reserved slice the person's own words would be the ones
                // sliced off whenever the parameter list was already full —
                // i.e. exactly on a rich archive.
                var MR = window.Typeahead.MAX_ROWS || 8;
                var keep = Math.max(2, MR - tagged.length - 1);
                if (c.items.length > keep) c.items = c.items.slice(0, keep);
                if (c.items.length) {
                    c.items.push({ label: 'tags and notes', note: true,
                                   cls: 'sm-th-fuzzsep' });
                }
                c.items = c.items.concat(tagged);
            }
            if (!c.items.length) {
                return vocab.hydrating
                    ? { items: [{ label: '⌛ indexing runs — parameters are still being read', note: true }] }
                    : null;
            }
            return {
                items: c.items,
                note: vocab.hydrating ? '⌛ still indexing — more may appear' : ''
            };
        }
        // value stage: the key the user typed may be a prefix of the real one
        var exact = byKey[key];
        var d = exact || vocab.keys.filter(function (x) {
            return x.k.toLowerCase() === String(key).toLowerCase();
        })[0];
        if (!d) return null;
        if (!d._vals) {                       // held on the vocabulary entry itself
            d._vals = d.v.map(function (p) { return p[0]; });
            d._counts = {};
            d.v.forEach(function (p) { d._counts[p[0]] = p[1]; });
        }
        var op = (st && st.op) || '=';

        /* A RANGE, previewed before it is run.
         *
         * Customer, on site: "amp같은 경우는 value도 많고 범위도 많기 때문에
         * 까다로워." Picking one value from a list is only an answer while the
         * list is short; on the eleven keys with more than twelve values it is
         * not. So the operator gets a first row that says what it will select,
         * counted from the vocabulary already in the browser -- still no
         * request per keystroke -- with the covered values listed underneath,
         * so nothing about the filter is blind. */
        var pred = _pred(op, stem);
        if (pred) {
            var covered = [], runs = 0;
            for (var i = 0; i < d.v.length; i++) {
                var nv = _num(d.v[i][0]);
                if (nv != null && pred(nv)) { covered.push(d.v[i]); runs += d.v[i][1]; }
            }
            var tok = PV ? PV(d.k, stem, op === '..' ? '=' : op)
                         : (d.k + op + stem);
            var head = {
                label: d.k + ' ' + op + ' ' + stem,
                meta: covered.length + ' of ' + d.v.length + ' values · ' + runs + ' runs',
                insert: tok, fire: true
            };
            if (!covered.length) {
                head.meta = 'no recorded value is ' + op + ' ' + stem;
                head.insert = null;         // offering it would find nothing
            }
            var rows = [head];
            for (var j = 0; j < covered.length && rows.length < 8; j++) {
                rows.push({
                    label: covered[j][0], meta: covered[j][1] + ' runs',
                    insert: PV ? PV(d.k, covered[j][0]) : (d.k + '=' + covered[j][0]),
                    fire: true
                });
            }
            return { items: rows,
                     note: d.more ? ('…and ' + d.more + ' more values not listed') : '' };
        }
        var rr = window.Typeahead.rank(d._vals, stem, 'sb-vals:' + vocab.v + ':' + d.k);
        var cc = window.Typeahead.compose(rr, function (val) {
            return {
                label: val, meta: d._counts[val] + ' runs',
                insert: PV ? PV(d.k, val) : (d.k + '=' + val), fire: true
            };
        });
        if (!cc.items.length) return null;
        /* The note tells the truth about the whole key, not about the eight
           rows on screen -- 22 of 30 values used to be silently invisible --
           and on a numeric key it is also the one place the operator is taught,
           because nobody types a syntax they do not know exists. */
        var shown = cc.items.filter(function (x) { return !x.note; }).length;
        var total = d.v.length + (d.more || 0);
        var note = shown < total ? (shown + ' of ' + total + ' values') : '';
        if (d.num) {
            if (!d.more) note += (note ? ' · ' : '') + d.min + ' … ' + d.max;
            note += (note ? ' · ' : '') + 'type >= <= or 100..1000 for a range';
        }
        return { items: cc.items, note: note };
    }

    function init() {
        if (!window.Typeahead) return;
        window.Typeahead.attach(INPUT, { suggest: suggest });
    }
    init();
    return { load: load, refresh: refresh, suggest: suggest,
             _vocab: function () { return vocab; } };
})();

/* ── the words a PERSON typed: tags and notes (docs/182) ─────────────────
 *
 * Customer, on-site: "제발 data tag랑 note에 사용자가 기재한 단어들도
 * 넣어달라고 함!!!! 다만, 검색 pop up할때 뜨는건 run 번호: tag 이름 (혹은
 * note) 이렇게 뜨도록. note는 내용이 다 담기게 하는게 아니고 그냥 note
 * (검색어 ...) 그냥 이렇게 compact하게."
 *
 * The grammar could always find these — `tag:` and `note:` are in the search
 * help — but you had to already know the word. Every other vocabulary the box
 * completes from is machine-generated; these are the only words in the archive
 * a person chose, which makes them exactly the ones worth being reminded of.
 *
 * Shared by every box that owns a typeahead, because a tag is a property of a
 * RUN and every one of those boxes searches runs.
 */
window.TagVocab = (function () {
    var data = null;            // {v, tags:[{t,n,r:[ids]}], notes:[{r,w:[words]}]}
    var loading = false;

    function load(force) {
        if (loading) return;
        loading = true;
        var url = '/workspace/tag-vocab';
        if (!force && data && data.v != null) url += '?v=' + encodeURIComponent(data.v);
        fetch(url, { headers: { 'HX-Request': 'true' } })
            .then(function (r) { return r.status === 204 ? null : r.json(); })
            .then(function (j) { if (j) data = j; })
            .catch(function () { /* a vocabulary is an accelerator, never a gate */ })
            .then(function () { loading = false; });
    }

    /* One row per (run, tag) and one per (run, note word) — the customer asked
     * for the run number to be visible, and a note belongs to exactly one run.
     * Capped, and the cap is SAID rather than silently applied. */
    var MAX_ROWS = 8;

    function items(stem) {
        if (!data) { load(false); return []; }
        stem = String(stem || '').toLowerCase();
        if (stem.length < 2) return [];      // one letter matches half the archive
        var out = [], truncated = 0, unsearchable = 0;

        (data.tags || []).forEach(function (d) {
            if (String(d.t).toLowerCase().indexOf(stem) < 0) return;
            // Accepting searches for the TAG, not the single run: the row says
            // where the word was found, the token says what to look for.
            // `tag:` is the grammar's own scope.
            var tok = _scoped('tag', d.t);
            if (tok === null) { unsearchable++; return; }   // never offer an inert row
            var runs = d.r || [];
            for (var i = 0; i < runs.length; i++) {
                if (out.length >= MAX_ROWS) { truncated++; continue; }
                out.push({
                    label: '#' + runs[i] + ': ' + d.t,
                    insert: tok,
                    fire: true,
                    meta: d.n === 1 ? 'tag' : 'tag · ' + d.n + ' runs'
                });
            }
        });

        (data.notes || []).forEach(function (d) {
            var hit = null;
            for (var i = 0; i < (d.w || []).length && !hit; i++) {
                if (d.w[i].indexOf(stem) >= 0) hit = d.w[i];
            }
            if (!hit) return;
            var ntok = _scoped('note', hit);
            if (ntok === null) { unsearchable++; return; }
            if (out.length >= MAX_ROWS) { truncated++; return; }
            out.push({
                // The note's CONTENT is deliberately not here — only the word
                // that matched. A whole note would not fit and was explicitly
                // not wanted.
                label: '#' + d.r + ': note (' + hit + ')',
                insert: ntok,
                fire: true,
                meta: 'note'
            });
        });

        if (truncated) {
            out.push({ label: '…and ' + truncated + ' more tagged or noted run'
                              + (truncated === 1 ? '' : 's'), note: true });
        }
        // docs/175's rule, kept: a suggestion that finds nothing is worse than
        // no suggestion. A word the tokenizer would mangle (it strips quotes
        // with no escape) is not offered — and not dropped in silence either.
        if (unsearchable) {
            out.push({ label: unsearchable + ' contain a quote and cannot be searched',
                       note: true });
        }
        return out;
    }

    /* The search grammar strips quotes with no escape, so a value carrying one
     * could never match what it displays (the docs/175 rule: a suggestion that
     * finds nothing is worse than no suggestion). Such a value is not offered. */
    function _scoped(scope, value) {
        value = String(value);
        if (value.indexOf('"') >= 0) return null;
        var tok = scope + ':' + value;
        if (/[\s,]/.test(tok)) tok = scope + ':"' + value + '"';
        return tok;
    }

    return { load: load, items: items, _scoped: _scoped,
             _data: function () { return data; },
             _set: function (d) { data = d; } };
})();

/* The insert rule, mirrored from core/param_vocab.insert_token and pinned
   against it. Kept tiny and beside its only caller: a value the grammar cannot
   carry never reaches the client (the server omits it), so this only has to
   reproduce the SHAPE. */
window.__paramVocabInsert = function (key, value, op) {
    if (key == null || value == null) return null;
    key = String(key); value = String(value);
    op = op == null ? '=' : String(op);
    if (['>=', '<=', '>', '<', '='].indexOf(op) < 0) return null;
    if (!key || key.indexOf('"') >= 0 || value.indexOf('"') >= 0) return null;
    if (key.indexOf('=') >= 0 || key.indexOf(':') >= 0) return null;
    var bare = /^[A-Za-z][\w.\-]*$/.test(key);
    var scope = bare ? '' : 'p:';
    if (scope && value !== value.replace(/^\s+|\s+$/g, '')) return null;
    if (!value) return null;
    var tok = scope + key + op + value;
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
    /* Cached against the header row itself. Without this the whole scan ran
       ON EVERY KEYSTROKE -- a querySelectorAll plus a querySelector, an
       attribute read and a regex split per header, at 309 headers on the
       customer's 20Q chip and up to MAX_DYNAMIC_COLUMNS = 1200. The grid is
       re-rendered wholesale rather than header by header, so identity of the
       first header plus the count is a sufficient key, and an htmx swap drops
       it anyway. */
    var _cache = null, _cacheFor = null, _cacheN = -1;
    if (window.document) {
        document.addEventListener('htmx:afterSwap', function () { _cache = null; });
    }

    function _prepared() {
        var heads = document.querySelectorAll('#table-pane th.bulk-col-head');
        if (_cache && _cacheN === heads.length && _cacheFor === heads[0]) return _cache;
        var counts = {};
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
        _cache = { counts: counts, names: Object.keys(counts), n: heads.length };
        _cacheFor = heads[0]; _cacheN = heads.length;
        return _cache;
    }

    /* The counts, which is what this has always returned. */
    function vocab() { return _prepared().counts; }

    function suggest(stage, key, stem) {
        if (!stem) return null;
        var vc = _prepared();
        var v = vc.counts;
        if (!vc.names.length) return null;
        var r = window.Typeahead.rank(vc.names, stem, 'bulk:' + vc.n);
        var c = window.Typeahead.compose(r, function (n) {
            return { label: n,
                     meta: v[n] + (v[n] === 1 ? ' column' : ' columns'),
                     insert: n, fire: true };
        });
        if (!c.items.length) return null;
        return {
            items: c.items,
            note: c.hidden > 0 ? ('…and ' + c.hidden + ' more') : ''
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
    var _cacheFor = null, _cache = null, _seq = 0;

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

    function _prepared() {
        var ms = _models();
        if (!ms.length) return null;
        // Element-wise, and the whole list is kept. Keying on `ms[0]` alone
        // meant a wiring container mounting AFTER the state one kept serving
        // the state-only vocabulary for ever (the tag computed beside it was
        // never actually used).
        if (_cache && _cacheFor && _cacheFor.length === ms.length) {
            var same = true;
            for (var q = 0; q < ms.length; q++) if (_cacheFor[q] !== ms[q]) { same = false; break; }
            if (same) return _cache;
        }
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
        _cacheFor = ms.slice();
        _cache = { counts: counts, names: Object.keys(counts), n: _seq++ };
        return _cache;
    }

    function vocab() { var p = _prepared(); return p ? p.counts : null; }

    function suggest(stage, key, stem) {
        if (!stem) return null;
        var vc = _prepared();
        if (!vc) return null;
        var v = vc.counts;
        if (!vc.names.length) return null;
        var r = window.Typeahead.rank(vc.names, stem, 'tree:' + vc.n);
        var c = window.Typeahead.compose(r, function (n) {
            return { label: n, meta: v[n] + (v[n] === 1 ? ' place' : ' places'),
                     insert: n, fire: true };
        });
        if (!c.items.length) return null;
        return {
            items: c.items,
            note: c.hidden > 0 ? ('…and ' + c.hidden + ' more keys') : ''
        };
    }

    if (window.Typeahead) window.Typeahead.attach(INPUT, { suggest: suggest, plain: true });
    return { suggest: suggest, vocab: vocab };
})();
