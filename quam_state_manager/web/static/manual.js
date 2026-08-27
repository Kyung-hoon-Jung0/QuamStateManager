/* Config Manual (2026-08-27) — the state.json key manual, in a movable window.
 *
 * Sidebar "Config Manual" (under Settings / Calculator) opens #manual-popover,
 * a body-level window like the Calculator: anchored under its button until
 * dragged, then fixed where the user left it. Inside: the house search box
 * (search-query.js grammar — space = AND, `|` = OR) over every key the chip's
 * classes can carry (+ the QM-docs config keys), and a "this place" view for
 * one node (openConfigManual({path})) listing its class's keys, set and unset.
 *
 * Data comes from /api/manual (once per chip) and /api/manual/node. Every
 * description shows its SOURCE — the class's own docstring or the QM docs page
 * — and a key nobody described says "no description". Nothing is invented.
 */
window.ConfigManual = (function () {
    'use strict';

    var _data = null;          // {entries, classes, env, note, chip}
    var _loadedChip = null;
    var _loading = null;
    var _mode = 'search';      // 'search' | 'node'
    var _nodePath = null;
    var MAX_ROWS = 200;

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
                _data = d && d.ok ? d : { entries: [], classes: [], env: false, note: (d && d.error) || 'manual unavailable' };
                _loadedChip = chip;
                _loading = null;
                return _data;
            })
            .catch(function () {
                _loading = null;
                _data = { entries: [], classes: [], env: false, note: 'manual unavailable (network)' };
                return _data;
            });
        return _loading;
    }

    function hay(e) {
        return [e.key, e.cls, e.type, e.doc, e.docs && e.docs.summary,
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
        if (e.cls) h += ' <span class="manual-cls muted">' + esc(e.cls) + '</span>';
        if (e.type) h += ' <span class="manual-type">' + esc(e.type) + '</span>';
        if (e.required) h += ' <span class="manual-req" title="No default in the class — must be set">required</span>';
        if (e.undeclared) h += ' <span class="manual-req" title="This key is not a field of the class in the selected environment">undeclared</span>';
        h += '</div>';
        if (desc) h += '<div class="manual-desc">' + esc(desc) + '</div>';
        else h += '<div class="manual-desc muted">no description — neither the class docstring nor the QM docs describe this key</div>';
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
        if (meta.length) h += '<div class="manual-meta muted">' + meta.join(' · ') + '</div>';
        if (e.examples && e.examples.length) {
            h += '<div class="manual-examples muted">used at ' + e.examples.map(function (p) {
                return '<a href="#" class="manual-goto" data-path="' + esc(p) + '"><code>' + esc(p) + '</code></a>';
            }).join(', ') + (e.present_in > e.examples.length ? ' … (' + e.present_in + ' places)' : '') + '</div>';
        }
        if (e.source) {
            h += '<div class="manual-source muted">source: ' + esc(e.source);
            if (e.docs && e.docs.docs) h += ' — <code title="' + esc(e.docs.quote || '') + '">' + esc(e.docs.docs) + '</code>';
            if (e.docs && e.doc) h += ' · also in QM docs <code>' + esc(e.docs.docs) + '</code>';
            h += '</div>';
        }
        return h + '</div>';
    }

    function renderSearch(q) {
        var body = pop().querySelector('.manual-body');
        var d = _data || { entries: [], note: 'loading…' };
        var groups = (window.SearchQuery && q) ? window.SearchQuery.groups(q) : null;
        var rows = d.entries.filter(function (e) {
            if (!groups) return true;
            return window.SearchQuery.matchesHay(hay(e), groups);
        });
        var h = '';
        if (d.note) h += '<p class="manual-note muted">' + esc(d.note) + '</p>';
        h += '<div class="manual-count muted">' + rows.length + ' of ' + d.entries.length + ' keys'
           + (d.chip ? ' · ' + esc(d.chip) : '') + '</div>';
        rows.slice(0, MAX_ROWS).forEach(function (e) { h += entryHtml(e); });
        if (rows.length > MAX_ROWS) h += '<p class="muted">… ' + (rows.length - MAX_ROWS) + ' more — narrow the search</p>';
        body.innerHTML = h;
    }

    function renderNode(nd) {
        var body = pop().querySelector('.manual-body');
        if (!nd || !nd.ok) {
            body.innerHTML = '<p class="manual-note muted">' + esc((nd && nd.reason) || 'nothing here') + '</p>';
            return;
        }
        var h = '<div class="manual-nodehead"><a href="#" class="manual-back">&larr; all keys</a> '
              + '<code>' + esc(nd.owner) + '</code>'
              + (nd.cls ? ' <span class="manual-cls">' + esc(nd.cls) + '</span>' : '') + '</div>';
        if (nd.cls_doc) h += '<div class="manual-desc">' + esc(nd.cls_doc) + '</div>';
        if (nd.note) h += '<p class="manual-note muted">' + esc(nd.note) + '</p>';
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
        load().then(function () { renderSearch(q); });
    }

    /* ── window plumbing (mirrors calc.js) ───────────────────────── */
    function isOpen() { var p = pop(); return !!(p && !p.classList.contains('manual-hidden')); }

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
            if (!p.classList.contains('manual-floating') && window._anchorPopover && btn) window._anchorPopover(p, btn);
            refresh();
            var s = p.querySelector('.manual-search');
            if (s && _mode === 'search') setTimeout(function () { s.focus(); }, 0);
        } else if (btn) {
            btn.focus();
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
        e.preventDefault(); e.stopPropagation();
        if (b.hasAttribute('data-help-path')) window.openConfigManual({ path: b.getAttribute('data-help-path') });
        else window.openConfigManual({ q: b.getAttribute('data-help-q') });
    }, true);

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

    return { load: load, refresh: refresh, _hay: hay, _entryHtml: entryHtml, _renderSearch: renderSearch, _renderNode: renderNode };
})();
