/* Config Manual (2026-08-27; catalogue + redesign 2026-08-28, user-directed).
 *
 * Sidebar "Config Manual" (under Settings / Calculator) opens #manual-popover,
 * a body-level window like the Calculator: anchored under its button until
 * dragged, then fixed where the user left it; resizable (size remembered).
 *
 * What it lists: EVERY key of EVERY component class the selected environment
 * offers (the catalogue: quam, quam_builder, the lab's quam_config), grouped
 * category > class > key -- not only the classes the open chip happens to
 * use. A key the chip does set carries "used at" places that open the tree.
 * The catalogue is probed once per env in the background (a chip load starts
 * it; a cold manual shows the chip's own classes + the QM docs keys and fills
 * in when the probe lands). Every description names its SOURCE -- the class's
 * own docstring or the QM docs page -- and a key nobody described says so.
 */
window.ConfigManual = (function () {
    'use strict';

    var _data = null;          // {entries, classes, categories, env, catalog, catalog_state, note, chip}
    var _loadedChip = null;
    var _loading = null;
    var _mode = 'search';      // 'search' | 'node'
    var _nodePath = null;
    var _pollTimer = null;
    var _polls = 0;
    var MAX_ROWS = 400;
    var SIZE_KEY = 'quam_manual_size';
    var POLL_MS = window.__manualPollMs || 3000;   // the catalogue warm-up re-ask (overridable for the harness)

    function pop() { return document.getElementById('manual-popover'); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function currentChip() {
        var el = document.querySelector('#pending-tray .state-status-name');
        return el ? el.textContent.trim() : '';
    }

    /* ── data ────────────────────────────────────────────────────── */
    function load(force) {
        var chip = currentChip();
        if (!force && _data && _loadedChip === chip) return Promise.resolve(_data);
        if (_loading) return _loading;
        _loading = fetch('/api/manual', { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                _data = d && d.ok ? d : { entries: [], classes: [], categories: [], env: false, note: (d && d.error) || 'manual unavailable' };
                _loadedChip = chip;
                _loading = null;
                schedulePoll();
                return _data;
            })
            .catch(function () {
                _loading = null;
                _data = { entries: [], classes: [], categories: [], env: false, note: 'manual unavailable (network)' };
                return _data;
            });
        return _loading;
    }
    /* The catalogue warms in the background; while it says "loading" the
       open window re-asks every few seconds and re-renders when it lands. */
    function schedulePoll() {
        clearTimeout(_pollTimer); _pollTimer = null;
        if (!_data || _data.catalog_state !== 'loading' || !isOpen() || _polls > 40) return;
        _pollTimer = setTimeout(function () {
            _polls++;
            load(true).then(function () { if (isOpen() && _mode === 'search') renderSearch(pop().querySelector('.manual-search').value); });
        }, POLL_MS);
    }

    function hay(e) {
        return [e.key, e.cls, e.category, e.type, e.doc, e.docs && e.docs.summary,
                (e.examples || []).join(' '), (e.choices || []).join(' ')].join(' ').toLowerCase();
    }

    /* ── rendering ───────────────────────────────────────────────── */
    function allowedHtml(allowed) {
        if (!allowed || !allowed.length) return '';
        return '<div class="manual-allowed">' + allowed.map(function (a) {
            return '<span class="manual-chip" title="' + esc(a.meaning || '') + '"><code>' + esc(a.value) + '</code>'
                + (a.meaning ? ' <span class="muted">' + esc(a.meaning) + '</span>' : '') + '</span>';
        }).join('') + '</div>';
    }

    function entryHtml(e, opts) {
        opts = opts || {};
        var desc = e.doc || (e.docs && e.docs.summary) || null;
        var h = '<div class="manual-entry' + (opts.focus ? ' manual-focus' : '') + (opts.unset ? ' manual-unset' : '') + '" data-key="' + esc(e.key) + '">';
        h += '<div class="manual-head">';
        if (opts.mark) h += '<span class="manual-mark" title="' + esc(opts.markTitle || '') + '">' + opts.mark + '</span> ';
        h += '<code class="manual-key">' + esc(e.key) + '</code>';
        if (opts.showCls && e.cls) h += ' <span class="manual-cls">' + esc(e.cls) + '</span>';
        if (e.type) h += ' <span class="manual-type">' + esc(e.type) + '</span>';
        if (e.required) h += ' <span class="manual-badge manual-req" title="No default in the class — must be set">required</span>';
        if (e.present_in > 0 && !opts.mark) h += ' <span class="manual-badge manual-used" title="set in the open state">in this state</span>';
        if (e.undeclared) h += ' <span class="manual-badge manual-req" title="This key is not a field of the class in the selected environment">undeclared</span>';
        h += '</div>';
        if (desc) h += '<div class="manual-desc">' + esc(desc) + '</div>';
        else h += '<div class="manual-desc manual-nodesc">no description — neither the class docstring nor the QM docs describe this key</div>';
        if (e.choices && e.choices.length) {
            h += '<div class="manual-allowed">' + e.choices.map(function (c) { return '<span class="manual-chip"><code>' + esc(c) + '</code></span>'; }).join('') + '</div>';
        }
        if (e.docs) h += allowedHtml(e.docs.allowed);
        var meta = [];
        if (e.default !== null && e.default !== undefined) meta.push('default <code>' + esc(JSON.stringify(e.default)) + '</code>');
        else if (e.default_repr) meta.push('default <code>' + esc(e.default_repr) + '</code>');
        else if (e.docs && e.docs.default !== null && e.docs.default !== undefined) meta.push('default <code>' + esc(JSON.stringify(e.docs.default)) + '</code> (docs)');
        if (e.docs && e.docs.unit) meta.push('unit ' + esc(e.docs.unit));
        if (e.docs && e.docs.since) meta.push(esc(e.docs.since));
        if (e.source) {
            var src = 'source: ' + esc(e.source);
            if (e.docs && e.docs.docs) src += ' — <code title="' + esc(e.docs.quote || '') + '">' + esc(e.docs.docs) + '</code>';
            if (e.docs && e.doc) src += ' · also in QM docs <code>' + esc(e.docs.docs) + '</code>';
            meta.push(src);
        }
        if (meta.length) h += '<div class="manual-meta">' + meta.join(' · ') + '</div>';
        if (e.examples && e.examples.length) {
            h += '<div class="manual-examples">used at ' + e.examples.map(function (p) {
                return '<a href="#" class="manual-goto" data-path="' + esc(p) + '"><code>' + esc(p) + '</code></a>';
            }).join(', ') + (e.present_in > e.examples.length ? ' … (' + e.present_in + ' places)' : '') + '</div>';
        }
        return h + '</div>';
    }

    function statusHtml(d, shown, total) {
        var parts = ['<span class="manual-live">' + shown + (shown !== total ? ' of ' + total : '') + ' keys</span>'];
        if (d.classes && d.classes.length) parts.push(d.classes.length + ' classes');
        if (d.catalog_state === 'loading') parts.push('full catalogue loading in the background…');
        else if (d.catalog_state === 'ready') parts.push('full catalogue of the selected environment');
        else if (d.catalog_state === 'none') parts.push('no environment selected — QM docs keys' + (d.env ? ' + this chip’s classes' : ''));
        if (d.chip) parts.push(esc(d.chip));
        return '<div class="manual-status">' + parts.join('<span aria-hidden="true">·</span>') + '</div>';
    }

    function renderSearch(q) {
        var body = pop().querySelector('.manual-body');
        var d = _data || { entries: [], classes: [], categories: [], note: 'loading…' };
        var groups = (window.SearchQuery && q) ? window.SearchQuery.groups(q) : null;
        var rows = d.entries.filter(function (e) {
            if (!groups) return true;
            return window.SearchQuery.matchesHay(hay(e), groups);
        });
        var h = statusHtml(d, rows.length, d.entries.length);
        if (d.note) h += '<p class="manual-note">' + esc(d.note) + '</p>';
        // category > class > key. A class opens when the chip uses it or a
        // search reached into it; a category opens when it has anything.
        var byCat = {};
        var order = d.categories && d.categories.length ? d.categories.slice() : [];
        rows.forEach(function (e) {
            var cat = e.category || 'Other components';
            if (order.indexOf(cat) < 0) order.push(cat);
            var cls = e.cls || '—';
            byCat[cat] = byCat[cat] || { order: [], classes: {} };
            if (!byCat[cat].classes[cls]) { byCat[cat].classes[cls] = []; byCat[cat].order.push(cls); }
            byCat[cat].classes[cls].push(e);
        });
        var clsInfo = {};
        (d.classes || []).forEach(function (c) { clsInfo[c.cls] = c; });
        var budget = MAX_ROWS;
        order.forEach(function (cat) {
            var g = byCat[cat];
            if (!g) return;
            var n = g.order.reduce(function (s, c) { return s + g.classes[c].length; }, 0);
            h += '<details class="manual-cat" open><summary>' + esc(cat) + ' <span class="manual-n">' + g.order.length + ' class' + (g.order.length === 1 ? '' : 'es') + ' · ' + n + ' keys</span></summary>';
            g.order.forEach(function (cls) {
                var list = g.classes[cls];
                var info = clsInfo[cls] || {};
                var used = list.some(function (e) { return e.used; }) || !!info.used;
                var open = !!groups || used;
                h += '<details class="manual-class"' + (open ? ' open' : '') + '><summary>'
                   + '<span class="manual-class-name">' + esc(cls) + '</span>'
                   + '<span class="manual-badge">' + list.length + ' key' + (list.length === 1 ? '' : 's') + '</span>'
                   + (used ? '<span class="manual-badge manual-used" title="the open state uses this class">in this state' + (info.count ? ' × ' + info.count : '') + '</span>' : '')
                   + (info.doc ? '<span class="manual-class-doc">' + esc(info.doc) + '</span>' : '')
                   + '</summary>';
                list.forEach(function (e) { if (budget-- > 0) h += entryHtml(e); });
                h += '</details>';
            });
            h += '</details>';
        });
        if (rows.length > MAX_ROWS) h += '<p class="manual-note">… ' + (rows.length - MAX_ROWS) + ' more keys — narrow the search</p>';
        if (!rows.length) h += '<p class="manual-note">nothing matches <code>' + esc(q) + '</code></p>';
        body.innerHTML = h;
    }

    function renderNode(nd) {
        var body = pop().querySelector('.manual-body');
        if (!nd || !nd.ok) {
            body.innerHTML = '<p class="manual-note">' + esc((nd && nd.reason) || 'nothing here') + '</p>';
            return;
        }
        var h = '<div class="manual-nodehead"><a href="#" class="manual-back">&larr; all keys</a> '
              + '<code>' + esc(nd.owner) + '</code>'
              + (nd.cls ? ' <span class="manual-cls">' + esc(nd.cls) + '</span>' : '') + '</div>';
        if (nd.cls_doc) h += '<div class="manual-desc">' + esc(nd.cls_doc) + '</div>';
        if (nd.note) h += '<p class="manual-note">' + esc(nd.note) + '</p>';
        var set = nd.fields.filter(function (f) { return f.present; });
        var unset = nd.fields.filter(function (f) { return !f.present; });
        h += '<div class="manual-section">Set here (' + set.length + ')</div>';
        set.forEach(function (f) { h += entryHtml(f, { focus: f.focus, mark: '✓', markTitle: 'present in the state' }); });
        if (unset.length) {
            h += '<div class="manual-section">Keys you could add (' + unset.length + ')</div>';
            unset.forEach(function (f) { h += entryHtml(f, { unset: true, mark: '○', markTitle: 'declared by the class, not set here' }); });
        }
        body.innerHTML = h;
        var fo = body.querySelector('.manual-focus');
        if (fo && fo.scrollIntoView) { try { fo.scrollIntoView({ block: 'center' }); } catch (e) {} }
    }

    function refresh() {
        if (!pop()) return;
        if (_mode === 'node' && _nodePath) {
            fetch('/api/manual/node?path=' + encodeURIComponent(_nodePath), { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (nd) { if (_mode === 'node' && _nodePath) renderNode(nd); })
                .catch(function () { renderNode({ ok: false, reason: 'unavailable' }); });
            return;
        }
        var q = pop().querySelector('.manual-search').value;
        load().then(function () { renderSearch(q); schedulePoll(); });
    }

    /* ── window plumbing (mirrors calc.js) ───────────────────────── */
    function isOpen() { var p = pop(); return !!(p && !p.classList.contains('manual-hidden')); }

    function restoreSize(p) {
        try {
            var s = JSON.parse(window.localStorage.getItem(SIZE_KEY) || 'null');
            if (s && s.w > 300 && s.h > 200) {
                // never larger than the viewport (a viewport the harness does
                // not size reports 0: fall back to the remembered size)
                var vw = window.innerWidth || 0, vh = window.innerHeight || 0;
                p.style.width = (vw > 340 ? Math.min(s.w, vw - 16) : s.w) + 'px';
                p.style.height = (vh > 240 ? Math.min(s.h, vh - 16) : s.h) + 'px';
            }
        } catch (e) {}
    }
    function watchSize(p) {
        if (p._manualSized || !window.ResizeObserver) return;
        p._manualSized = true;
        var t = null;
        new ResizeObserver(function () {
            if (!isOpen()) return;
            clearTimeout(t);
            t = setTimeout(function () {
                try { window.localStorage.setItem(SIZE_KEY, JSON.stringify({ w: p.offsetWidth, h: p.offsetHeight })); } catch (e) {}
            }, 250);
        }).observe(p);
    }

    function setOpen(open, trigger) {
        var p = pop();
        var btn = (window._toolTrigger ? window._toolTrigger('.manual-btn', trigger)
                                       : document.getElementById('manual-btn'));
        if (!p) return;
        p.classList.toggle('manual-hidden', !open);
        document.querySelectorAll('.manual-btn').forEach(function (b) {
            b.classList.toggle('manual-open', open);
            b.setAttribute('aria-expanded', open ? 'true' : 'false');
        });
        if (open) {
            restoreSize(p);
            watchSize(p);
            if (!p.classList.contains('manual-floating') && window._anchorPopover && btn) window._anchorPopover(p, btn);
            refresh();
            var s = p.querySelector('.manual-search');
            if (s && _mode === 'search') setTimeout(function () { s.focus(); }, 0);
        } else {
            clearTimeout(_pollTimer); _pollTimer = null;
            if (btn) btn.focus();
        }
    }

    window.toggleConfigManual = function (trigger) { setOpen(!isOpen(), trigger); };

    /* Deep link: {q} pre-fills the search, {path} opens the "this place" view. */
    window.openConfigManual = function (opts) {
        opts = opts || {};
        var p = pop();
        if (!p) return;
        if (opts.path) { _mode = 'node'; _nodePath = String(opts.path); }
        else {
            _mode = 'search'; _nodePath = null;
            if (typeof opts.q === 'string') p.querySelector('.manual-search').value = opts.q;
        }
        if (isOpen()) refresh(); else setOpen(true, null);
    };

    function enableDrag(p) {
        var head = p.querySelector('.manual-header');
        var dragging = false, committed = false, sx = 0, sy = 0, ox = 0, oy = 0;
        function endDrag() {
            dragging = false; committed = false;
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', endDrag);
        }
        function commit() {
            var r = p.getBoundingClientRect();
            p.classList.add('manual-floating');
            p.style.left = r.left + 'px'; p.style.top = r.top + 'px'; p.style.width = r.width + 'px';
            ox = r.left; oy = r.top; committed = true;
        }
        function onMove(e) {
            if (!dragging) return;
            if (e.buttons === 0) { endDrag(); return; }
            if (!committed) {
                if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) < 4) return;
                commit();
            }
            var w = p.offsetWidth, h = p.offsetHeight;
            var nx = ox + (e.clientX - sx), ny = oy + (e.clientY - sy);
            var maxX = window.innerWidth - w - 4, maxY = window.innerHeight - h - 4;
            nx = Math.max(4, Math.min(nx, Math.max(4, maxX)));
            ny = Math.max(4, Math.min(ny, Math.max(4, maxY)));
            p.style.left = nx + 'px'; p.style.top = ny + 'px';
        }
        head.addEventListener('mousedown', function (e) {
            if (e.button !== 0) return;
            if (e.target.closest && e.target.closest('.manual-header-tools')) return;
            dragging = true; committed = false; sx = e.clientX; sy = e.clientY;
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', endDrag);
            e.preventDefault();
        });
        window.addEventListener('blur', function () { if (dragging) endDrag(); });
    }

    function wire() {
        var p = pop();
        if (!p || p._manualWired) return;
        p._manualWired = true;
        enableDrag(p);
        var s = p.querySelector('.manual-search');
        var timer = null;
        s.addEventListener('input', function () {
            _mode = 'search'; _nodePath = null;
            clearTimeout(timer);
            timer = setTimeout(function () { renderSearch(s.value); }, 80);
        });
        s.addEventListener('keydown', function (e) { if (e.key === 'Escape') { setOpen(false, null); e.preventDefault(); } });
        p.addEventListener('keydown', function (e) { if (e.key === 'Escape') { setOpen(false, null); } });
        p.addEventListener('click', function (e) {
            var back = e.target.closest && e.target.closest('.manual-back');
            if (back) { e.preventDefault(); _mode = 'search'; _nodePath = null; renderSearch(s.value); s.focus(); return; }
            var go = e.target.closest && e.target.closest('.manual-goto');
            if (go) {
                e.preventDefault();
                var path = go.getAttribute('data-path');
                if (window._navigateToExplorerPath) window._navigateToExplorerPath(path);
                return;
            }
            var close = e.target.closest && e.target.closest('.manual-close');
            if (close) { setOpen(false, null); }
        });
    }

    /* The ? buttons on the state surfaces carry their target as data
       attributes (never an inline onclick string -- a key with a quote in it
       would end the script), one delegated handler serves them all. */
    document.addEventListener('click', function (e) {
        var b = e.target && e.target.closest ? e.target.closest('.key-help-btn[data-help-path], .key-help-btn[data-help-q]') : null;
        if (!b) return;
        e.preventDefault();   // no stopPropagation: a click-away listener elsewhere must still see this click
        if (b.hasAttribute('data-help-path')) window.openConfigManual({ path: b.getAttribute('data-help-path') });
        else window.openConfigManual({ q: b.getAttribute('data-help-q') });
    });

    /* F1 on a focused state cell / tree row / inspector input opens "this place". */
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'F1' || e.ctrlKey || e.altKey || e.metaKey) return;
        var t = e.target;
        if (!t || !t.closest) return;
        var path = null;
        var cell = t.closest('[data-dot-path]');
        if (cell) path = cell.getAttribute('data-dot-path');
        if (!path) {
            var node = t.closest('.tree-node[data-path]');
            if (node) path = node.getAttribute('data-path');
        }
        if (!path) {
            var form = t.closest('form');
            var hid = form && form.querySelector('input[type="hidden"][name="dot_path"]');
            if (hid) path = hid.value;
        }
        if (!path) return;
        e.preventDefault();
        window.openConfigManual({ path: path });
    });

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
    else wire();

    return { load: load, refresh: refresh, _hay: hay, _entryHtml: entryHtml, _renderSearch: renderSearch, _renderNode: renderNode, _statusHtml: statusHtml, _restoreSize: restoreSize };
})();
