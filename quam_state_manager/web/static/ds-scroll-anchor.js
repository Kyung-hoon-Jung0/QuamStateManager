/* Datasets run detail: keep the reader's place EXACTLY across run switches.
 *
 * Queue item 6 (customer, 2026-09-25: "it slips a little" -- every run switch moved
 * the detail a little, so every run needed re-scrolling). Measured in real
 * Chrome on the KH rig, 32 consecutive switches, three mechanisms:
 *
 *  1. The remembered place was RE-DERIVED from wherever the last restore
 *     landed. One run whose section was too short to honour the offset (a
 *     run with fewer figures, a run with no figures) clamped the restore, and
 *     the next beforeSwap captured THAT clamped spot as the new place: the
 *     reader's position was gone for good (20/32 switches off after one short
 *     run). The place the reader chose is now an INTENT, captured only when
 *     the reader actually moved -- a scroll position (the pane's or an inner
 *     scroller's) that changed and is not where this module last set it, or
 *     a tab the reader picked -- and kept verbatim across switches they did
 *     not move in. A click alone is not a move (it used to be: one click on a
 *     clamped run re-captured the clamped landing, -2626 px).
 *  2. The restore ran 150 ms (+ a rAF, + a 250 ms retry) after the swap, so
 *     every switch first painted the new run at the old pixel offset and then
 *     jumped. The restore now runs in the swap itself.
 *  3. Lazy content (figure <img>s without intrinsic size, JSON trees fetched
 *     after the swap, ndview / interactive tiles) grows AFTER any fixed-delay
 *     restore. A pin re-applies the anchor on every resize of the detail until
 *     the reader moves or the next run swaps in.
 *
 * The anchor is not a pixel offset: it is a chain of LANDMARKS from the active
 * tab's container down to the deepest keyed element under the READING LINE
 * (section -> figure card / <details> / JSON-tree path / ndview block), plus
 * the offset of the line inside each. On the next run the deepest
 * landmark that exists AND is tall enough to hold the offset is put back at
 * the same offset -- identical, not "roughly". Where the next run cannot hold
 * it (no such landmark, or it is shorter), the nearest shallower one is used
 * and the result says `exact:false`; the intent itself is untouched, so the
 * next run that can hold it lands exactly again.
 *
 * The reading line (final QA, 2026-09-27) is the bottom edge of the pane's
 * sticky .inspector-header, not the pane's top edge: the top 61 px of the
 * pane are UNDER that header, so the old line measured a place nobody can
 * see. Where it fell in the margin between two per-qubit blocks while the
 * visible line was already inside the next one, the chain stopped at the
 * section, and a neighbour run whose first block was taller put the reader's
 * block 102 px off (6 of 6 visits on the KH rig). The line is measured on
 * each run's own header, so a header that wraps to another height moves it.
 *
 * A line that falls in a GAP -- between two sibling landmarks with nothing
 * rendered between them (a CSS margin) -- is held by the block BELOW it, at a
 * negative offset: the reader is looking at where that block starts, and a
 * taller block above it on the next run must not move it. Deterministic: the
 * gap is only a gap when a landmark ends at/above the line and the next one
 * (its sibling, only empty elements between) starts below it; anything else
 * (the line in non-landmark content, above the first landmark, after the
 * last) stays with the parent, as before.
 *
 * Pure DOM: getBoundingClientRect / scrollTop / clientHeight only, so the
 * jsdom selfcheck can drive it against a fake layout
 * (tests/ds_scroll_anchor_selfcheck.cjs).
 */
(function () {
    'use strict';

    // Keyed landmark kinds. Order does not matter; nesting does (a landmark's
    // children are the landmarks with no other landmark between them and it).
    var KINDS = [
        { sel: '[data-fvsec]', key: function (el) { return 'sec:' + el.getAttribute('data-fvsec'); } },
        { sel: '.figure-card', key: function (el) {
            var c = el.querySelector('.figure-label code');
            return 'fig:' + (c ? c.textContent.trim() : '');
        } },
        { sel: '.ds-interactive-fig[data-fig]', key: function (el) { return 'ifig:' + el.getAttribute('data-fig'); } },
        { sel: 'details', key: function (el) { return 'det:' + _summaryText(el); } },
        { sel: '.tree-node[data-path]', key: function (el) { return 'path:' + el.getAttribute('data-path'); } },
        { sel: '.ndv-files, .ndv-vars, .ndv-controls, .ndv-plot', key: function (el) {
            var cls = ['ndv-files', 'ndv-vars', 'ndv-controls', 'ndv-plot'];
            for (var i = 0; i < cls.length; i++) if (el.classList.contains(cls[i])) return 'ndv:' + cls[i];
            return 'ndv:?';
        } },
    ];
    var SEL = KINDS.map(function (k) { return k.sel; }).join(', ');

    // A <details>' identity is its summary's OWN text: the summary also holds
    // per-run buttons ("Apply all ->" only where fit targets exist) and run ids
    // ("Changes from previous run #123"), which must not change the key.
    function _summaryText(det) {
        var s = null;
        for (var i = 0; i < det.children.length; i++) {
            if (det.children[i].tagName === 'SUMMARY') { s = det.children[i]; break; }
        }
        if (!s) return '';
        var t = '';
        for (var j = 0; j < s.childNodes.length; j++) {
            if (s.childNodes[j].nodeType === 3) t += s.childNodes[j].nodeValue;
        }
        return t.replace(/\s+/g, ' ').trim();
    }

    function _keyOf(el) {
        for (var i = 0; i < KINDS.length; i++) {
            if (el.matches(KINDS[i].sel)) return KINDS[i].key(el);
        }
        return null;
    }

    function _isLandmark(el) { return !!(el && el.nodeType === 1 && el.matches(SEL)); }

    // Scrollers of their own inside a tab: a landmark behind one of these
    // moves with ITS scroll, so the pane's chain never descends into it (it is
    // captured separately, see _captureInner).
    var INNER_SEL = '.json-tree, .fig-info-col, .ds-inline-tree';

    // Landmarks directly under `parent` (no landmark, no inner scroller, in between).
    function _children(parent) {
        var out = [];
        var all = parent.querySelectorAll(SEL);
        for (var i = 0; i < all.length; i++) {
            var up = all[i].parentElement, direct = true;
            while (up && up !== parent) {
                if (_isLandmark(up) || up.matches(INNER_SEL)) { direct = false; break; }
                up = up.parentElement;
            }
            if (direct && up === parent) out.push(all[i]);
        }
        return out;
    }

    function _paneTop(pane) {
        return pane.getBoundingClientRect().top + (pane.clientTop || 0);
    }

    function _visible(r) { return r.height > 0; }

    // The sticky header that covers the top of the pane (the dataset run
    // header). Its bottom edge is the reading line; a header that is not
    // covering the top edge (scrolled away, or none) leaves the pane top.
    var HEADER_SEL = '.inspector-header';

    function _lineOf(pane) {
        var y = _paneTop(pane);
        var hs = pane.querySelectorAll(HEADER_SEL);
        for (var i = 0; i < hs.length; i++) {
            var r = hs[i].getBoundingClientRect();
            if (_visible(r) && r.top <= y + 0.5 && r.bottom > y + 0.5) y = r.bottom;
        }
        return y;
    }

    // Nothing rendered between two siblings: the space between them is a
    // margin, not content of its own.
    function _emptyBetween(a, b) {
        if (a.parentElement !== b.parentElement) return false;
        for (var e = a.nextElementSibling; e && e !== b; e = e.nextElementSibling) {
            if (e.getBoundingClientRect().height > 0) return false;
        }
        return e === b;
    }

    // The landmark just BELOW `y` when `y` sits in a gap between two of
    // `kids` (see the header comment), else null. Called only when no kid
    // contains `y`, so the visible kid before the first one that starts
    // below `y` has already ended at or above it.
    function _gapBelow(kids, y) {
        for (var i = 0; i < kids.length; i++) {
            var r = kids[i].getBoundingClientRect();
            if (!_visible(r) || r.top <= y + 0.5) continue;
            for (var j = i - 1; j >= 0; j--) {
                if (!_visible(kids[j].getBoundingClientRect())) continue;
                return _emptyBetween(kids[j], kids[i]) ? kids[i] : null;
            }
            return null;   // nothing above it: the line is in the parent's own lead
        }
        return null;
    }

    /* The reader's place, as a landmark chain. `container` is the active tab's
     * content element. Above the container (the run header / tab strip) there
     * is nothing to anchor on, so the raw offset is kept. */
    function capture(pane, container) {
        var out = _captureIn(pane, container, _lineOf(pane));
        out.inner = container ? _captureInner(container) : [];
        return out;
    }

    // A scroller INSIDE the tab (every JSON tree is one -- .json-tree has its
    // own max-height -- and so are the Figures tab's fit/parameter columns).
    // Keyed by id, else by class + index; captured the same way, relative to
    // its own top edge, with its raw offset as the fallback.
    function _innerKey(el, container) {
        if (el.id) return '#' + el.id;
        var cls = el.classList && el.classList[0];
        if (!cls) return null;
        var same = container.getElementsByClassName(cls);
        for (var i = 0; i < same.length; i++) if (same[i] === el) return '.' + cls + ':' + i;
        return null;
    }
    function _findInner(container, key) {
        if (key.charAt(0) === '#') {
            var id = key.slice(1), all = container.querySelectorAll('[id]');
            for (var i = 0; i < all.length; i++) if (all[i].id === id) return all[i];
            return null;
        }
        var m = /^\.([^:]+):(\d+)$/.exec(key);
        return m ? (container.getElementsByClassName(m[1])[+m[2]] || null) : null;
    }
    function _captureInner(container) {
        var out = [], all = container.getElementsByTagName('*');
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (!(el.scrollTop > 0)) continue;
            var key = _innerKey(el, container);
            if (!key) continue;
            var a = _captureIn(el, el, _paneTop(el));   // its own top edge: no header inside
            a.key = key;
            out.push(a);
        }
        return out;
    }

    function _captureIn(pane, container, y) {
        var out = { scrollTop: pane.scrollTop, chain: null };
        if (!container) return out;
        var cr = container.getBoundingClientRect();
        if (!_visible(cr) || cr.top > y + 0.5) return out;
        var chain = [{ key: '@container', n: 0, within: y - cr.top }];
        var parent = container;
        for (var depth = 0; depth < 64; depth++) {
            var kids = _children(parent), hit = null;
            for (var i = 0; i < kids.length; i++) {
                var r = kids[i].getBoundingClientRect();
                if (_visible(r) && r.top <= y + 0.5 && r.bottom > y + 0.5) { hit = kids[i]; break; }
            }
            if (!hit) {
                var below = _gapBelow(kids, y);
                if (below) {   // held by the block below, at a negative offset
                    var bk = _keyOf(below), bn = 0;
                    for (var b = 0; b < kids.length && kids[b] !== below; b++) if (_keyOf(kids[b]) === bk) bn++;
                    chain.push({ key: bk, n: bn, within: y - below.getBoundingClientRect().top, gap: true });
                }
                break;
            }
            var key = _keyOf(hit), n = 0;
            for (var k = 0; k < kids.length && kids[k] !== hit; k++) if (_keyOf(kids[k]) === key) n++;
            chain.push({ key: key, n: n, within: y - hit.getBoundingClientRect().top });
            parent = hit;
        }
        out.chain = chain;
        return out;
    }

    // Deepest element of `chain` that exists (and is laid out) under `container`.
    function _resolve(container, chain) {
        var el = container, depth = 0;
        for (var i = 1; i < chain.length; i++) {
            var kids = _children(el), seen = 0, found = null;
            for (var k = 0; k < kids.length; k++) {
                if (_keyOf(kids[k]) !== chain[i].key) continue;
                if (seen === chain[i].n) { found = kids[k]; break; }
                seen++;
            }
            if (!found || !_visible(found.getBoundingClientRect())) break;
            el = found; depth = i;
        }
        return { el: el, depth: depth };
    }

    // The scrollTop each scroller was last SET to by this module. A scroll
    // event whose target still sits there is this module's own write (fired
    // a frame later, possibly after the pin that wrote it has stopped), not
    // the reader moving -- see isOwnScroll.
    var _written = typeof WeakMap === 'function' ? new WeakMap() : null;

    function isOwnScroll(el) {
        if (!_written || !el || !_written.has(el)) return false;
        return Math.abs(el.scrollTop - _written.get(el)) <= 1;
    }

    function _setTop(pane, top) {
        top = Math.max(0, top);
        if (typeof pane.scrollTo === 'function') {
            try { pane.scrollTo({ top: top, behavior: 'instant' }); } catch (e) { pane.scrollTop = top; }
        } else {
            pane.scrollTop = top;
        }
        if (Math.abs(pane.scrollTop - top) > 1) pane.scrollTop = top;   // scrollTo unsupported / ignored
        if (_written) _written.set(pane, pane.scrollTop);   // where it LANDED (a clamp included)
    }

    /* Put `anchor` back. Returns {exact, depth, key, target}: exact means the
     * deepest captured landmark was found, holds the offset, and the pane
     * could scroll there (not clamped by its scroll range). */
    function apply(pane, container, anchor) {
        var res = _applyIn(pane, container, anchor, false);
        var inner = (anchor && anchor.inner) || [];
        res.inner = [];
        for (var i = 0; i < inner.length && container; i++) {
            var el = _findInner(container, inner[i].key);
            if (!el || !_visible(el.getBoundingClientRect())) { res.inner.push({ key: inner[i].key, exact: false, depth: -1 }); continue; }
            var r = _applyIn(el, el, inner[i], true);
            r.key = inner[i].key;
            res.inner.push(r);
        }
        return res;
    }

    function _applyIn(pane, container, anchor, isInner) {
        if (!anchor) return { exact: false, depth: -1 };
        if (!anchor.chain || !container || !_visible(container.getBoundingClientRect())) {
            _setTop(pane, anchor.scrollTop || 0);
            return { exact: Math.abs(pane.scrollTop - (anchor.scrollTop || 0)) < 1, depth: -1,
                     target: anchor.scrollTop || 0 };
        }
        var chain = anchor.chain;
        var res = _resolve(container, chain);
        // Walk up until a level can hold its offset: a landmark shorter than the
        // offset would put the edge in whatever follows it.
        var el = res.el, depth = res.depth;
        while (depth > 0 && chain[depth].within >= el.getBoundingClientRect().height) {
            depth--;
            el = depth === 0 ? container : _resolve(container, chain.slice(0, depth + 1)).el;
        }
        if (depth === 0 && isInner) {
            // nothing keyed survived inside this scroller: its own offset
            _setTop(pane, anchor.scrollTop || 0);
            return { exact: false, depth: 0, key: '@px', target: anchor.scrollTop || 0 };
        }
        var r = el.getBoundingClientRect();
        var within = Math.min(chain[depth].within, Math.max(0, r.height - 1));
        // the same line the capture measured from, on THIS run's header
        var target = pane.scrollTop + (r.top - (isInner ? _paneTop(pane) : _lineOf(pane))) + within;
        _setTop(pane, target);
        var landed = Math.abs(pane.scrollTop - Math.max(0, target)) < 1;
        return { exact: landed && depth === chain.length - 1 && within === chain[depth].within,
                 depth: depth, key: chain[depth].key, target: target };
    }

    /* Keep `anchor` in place while the new run's lazy content settles. Ends on
     * stop(), on reader input in the pane, or on a scroll this pin did not
     * cause (onUser is called for the last two). No timer: until the reader
     * moves, the place they chose IS the place. */
    function pin(pane, getContainer, anchor, onUser) {
        var lastSet = null, stopped = false, ro = null;
        var prevAnchorCss = pane.style.overflowAnchor;
        pane.style.overflowAnchor = 'none';   // one mechanism, not two fighting
        function reapply() {
            if (stopped) return null;
            var res = apply(pane, getContainer(), anchor);
            lastSet = pane.scrollTop;
            ctl.last = res;
            return res;
        }
        function onScroll() {
            if (stopped || lastSet === null) return;
            if (Math.abs(pane.scrollTop - lastSet) > 1) user();
        }
        function onLoad() { schedule(); }
        function schedule() {
            if (stopped) return;
            reapply();   // synchronously: a later frame would paint the drift first
        }
        function user() {
            if (stopped) return;
            stop();
            if (onUser) onUser();
        }
        function stop() {
            if (stopped) return;
            stopped = true;
            if (ro) ro.disconnect();
            pane.removeEventListener('scroll', onScroll);
            pane.removeEventListener('load', onLoad, true);
            pane.style.overflowAnchor = prevAnchorCss;
        }
        var ctl = { stop: stop, reapply: reapply, user: user, last: null,
                    get active() { return !stopped; } };
        pane.addEventListener('scroll', onScroll);
        pane.addEventListener('load', onLoad, true);   // <img> load does not bubble
        if (typeof ResizeObserver === 'function') {
            ro = new ResizeObserver(schedule);
            var kids = pane.children;   // the run header + every tab live under these
            for (var i = 0; i < kids.length; i++) ro.observe(kids[i]);
            var c = getContainer();
            if (c) ro.observe(c);
        }
        reapply();
        return ctl;
    }

    window.DsScrollAnchor = { capture: capture, apply: apply, pin: pin, isOwnScroll: isOwnScroll,
                              _children: _children, _keyOf: _keyOf, _findInner: _findInner,
                              _lineOf: _lineOf,
                              SEL: SEL, INNER_SEL: INNER_SEL, HEADER_SEL: HEADER_SEL };
})();
