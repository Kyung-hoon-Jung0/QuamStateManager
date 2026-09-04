/* FloatPanel (docs/141 §4u) — the ONE drag core for the app's floating tool
   windows (Calculator, Settings, Config Manual). Two copies of the same
   algorithm lived in calc.js and manual.js; the Settings dropdown had none.
   A panel opens ANCHORED under its trigger (app.js _anchorPopover); a real
   drag of its header (≥ 4 px, never a plain click) commits it to fixed
   coordinates — the owner's float class is added, the position kept inside
   the viewport, the header's own buttons excluded from the grab. A floated
   panel is the user saying "keep it around": owners exempt it from their
   outside-click close. */
(function () {
    'use strict';
    if (window.FloatPanel) return;
    var THRESHOLD = 4, PAD = 4;

    function drag(panel, opts) {
        opts = opts || {};
        if (!panel || !panel.querySelector) return false;
        var head = opts.handle || panel.querySelector(opts.handleSelector || '.fp-handle');
        if (!head || head._fpBound) return false;
        head._fpBound = true;
        var toolsSel = opts.tools || null;
        var floatClass = opts.floatClass || 'fp-floating';
        var dragging = false, committed = false, sx = 0, sy = 0, ox = 0, oy = 0;
        function endDrag() {
            dragging = false; committed = false;
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', endDrag);
        }
        function commit() {
            var r = panel.getBoundingClientRect();
            panel.classList.add(floatClass);
            panel.classList.add('fp-floating');
            panel.style.left = r.left + 'px'; panel.style.top = r.top + 'px'; panel.style.width = r.width + 'px';
            ox = r.left; oy = r.top; committed = true;
            if (typeof opts.onFloat === 'function') { try { opts.onFloat(panel); } catch (e) {} }
        }
        function onMove(e) {
            if (!dragging) return;
            if (e.buttons === 0) { endDrag(); return; }   // a mouseup missed (released over browser chrome)
            if (!committed) {
                if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) < THRESHOLD) return;   // a click, not a drag
                commit();
            }
            var w = panel.offsetWidth, h = panel.offsetHeight;
            var nx = ox + (e.clientX - sx), ny = oy + (e.clientY - sy);
            var maxX = window.innerWidth - w - PAD, maxY = window.innerHeight - h - PAD;
            nx = Math.max(PAD, Math.min(nx, Math.max(PAD, maxX)));
            ny = Math.max(PAD, Math.min(ny, Math.max(PAD, maxY)));
            panel.style.left = nx + 'px'; panel.style.top = ny + 'px';
        }
        head.addEventListener('mousedown', function (e) {
            if (e.button !== 0) return;
            if (toolsSel && e.target.closest && e.target.closest(toolsSel)) return;   // the header's buttons stay clickable
            dragging = true; committed = false; sx = e.clientX; sy = e.clientY;
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', endDrag);
            e.preventDefault();
        });
        window.addEventListener('blur', function () { if (dragging) endDrag(); });
        return true;
    }
    /* docs/165 (user): "크기 조절을 할수있으면 좋겠다 -- 마우스로 edge에
       가져갔을 때". CSS `resize: both` gives ONE grip, in the bottom-right
       corner, and only two of the three windows even had it. This gives all
       three every edge and every corner: the cursor changes as the pointer
       crosses the border band, and a drag from there resizes.

       A resize FLOATS the panel first. Anchored, a panel is pinned by `right`
       and `top: 100%`, so dragging its north or west edge would have to move
       an origin it does not own -- the box would grow the wrong way. Floating
       it gives it explicit left/top, which is also what the user means by
       grabbing a window's edge. The existing clamp then keeps it on screen.

       Minimums come from the panel's OWN computed style, so each window keeps
       the floor its CSS declares rather than a number repeated here. */
    var EDGE = 6;
    var CURSORS = { n: 'ns-resize', s: 'ns-resize', e: 'ew-resize', w: 'ew-resize',
                    ne: 'nesw-resize', sw: 'nesw-resize', nw: 'nwse-resize', se: 'nwse-resize' };

    function edgeAt(panel, e) {
        var r = panel.getBoundingClientRect();
        var v = '';
        if (e.clientY - r.top <= EDGE) v = 'n';
        else if (r.bottom - e.clientY <= EDGE) v = 's';
        if (e.clientX - r.left <= EDGE) v += 'w';
        else if (r.right - e.clientX <= EDGE) v += 'e';
        return v;
    }

    function minOf(panel, prop, fallback) {
        var v = parseFloat(getComputedStyle(panel)[prop]);
        return (isNaN(v) || v <= 0) ? fallback : v;
    }

    function resize(panel, opts) {
        opts = opts || {};
        if (!panel || panel._fpResizeBound) return false;
        panel._fpResizeBound = true;
        var floatClass = opts.floatClass || 'fp-floating';
        var side = '', active = '', sx = 0, sy = 0, r0 = null;

        panel.addEventListener('mousemove', function (e) {
            if (active) return;                       // mid-resize, cursor is set
            if (e.target.closest && e.target.closest('input, textarea, select, button, a')) {
                side = ''; panel.style.cursor = ''; return;
            }
            side = edgeAt(panel, e);
            panel.style.cursor = CURSORS[side] || '';
        });
        panel.addEventListener('mouseleave', function () {
            if (!active) { side = ''; panel.style.cursor = ''; }
        });

        function onMove(e) {
            if (!active) return;
            if (e.buttons === 0) { endResize(); return; }
            var minW = minOf(panel, 'minWidth', 160), minH = minOf(panel, 'minHeight', 120);
            var dx = e.clientX - sx, dy = e.clientY - sy;
            var x = r0.left, y = r0.top, w = r0.width, h = r0.height;
            if (active.indexOf('e') >= 0) w = r0.width + dx;
            if (active.indexOf('s') >= 0) h = r0.height + dy;
            if (active.indexOf('w') >= 0) { w = r0.width - dx; x = r0.left + dx; }
            if (active.indexOf('n') >= 0) { h = r0.height - dy; y = r0.top + dy; }
            if (w < minW) { if (active.indexOf('w') >= 0) x = r0.right - minW; w = minW; }
            if (h < minH) { if (active.indexOf('n') >= 0) y = r0.bottom - minH; h = minH; }
            panel.style.width = Math.round(w) + 'px';
            panel.style.height = Math.round(h) + 'px';
            panel.style.left = Math.round(x) + 'px';
            panel.style.top = Math.round(y) + 'px';
            e.preventDefault();
        }
        function endResize() {
            if (!active) return;
            active = ''; panel.style.cursor = '';
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', endResize);
            clampIntoView(panel);
            try {
                panel.dispatchEvent(new CustomEvent('fp:resized', { bubbles: true }));
            } catch (e2) {}
        }
        panel.addEventListener('mousedown', function (e) {
            if (e.button !== 0) return;
            var s2 = edgeAt(panel, e);
            if (!CURSORS[s2]) return;                 // not on a border band
            if (!isFloating(panel)) {                 // grabbing an edge floats it
                var r = panel.getBoundingClientRect();
                panel.classList.add(floatClass);
                panel.classList.add('fp-floating');
                panel.style.left = r.left + 'px'; panel.style.top = r.top + 'px';
                panel.style.width = r.width + 'px'; panel.style.height = r.height + 'px';
                if (typeof opts.onFloat === 'function') { try { opts.onFloat(panel); } catch (e3) {} }
            }
            active = s2; sx = e.clientX; sy = e.clientY;
            r0 = panel.getBoundingClientRect();
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', endResize);
            e.preventDefault(); e.stopPropagation();
        });
        window.addEventListener('blur', function () { if (active) endResize(); });
        return true;
    }

    function isFloating(panel) { return !!panel && panel.classList.contains('fp-floating'); }
    /* docs/141 4ac: the clamp was a DRAG-time invariant, and the viewport is
       the other half of the constraint -- it changes without a drag (a window
       resize, maximise/restore, the desktop window being resized, devtools
       docking, a zoom change). A floated panel is position:fixed with inline
       coordinates, so narrowing the window left it off screen for the life of
       the page: reopening does not help (the owners skip re-anchoring while
       floating) and `unfloat()` has no production caller. */
    function clampIntoView(panel) {
        if (!isFloating(panel)) return;
        var w = panel.offsetWidth, h = panel.offsetHeight;
        var x = parseFloat(panel.style.left), y = parseFloat(panel.style.top);
        if (isNaN(x) || isNaN(y)) return;
        var maxX = window.innerWidth - w - PAD, maxY = window.innerHeight - h - PAD;
        panel.style.left = Math.max(PAD, Math.min(x, Math.max(PAD, maxX))) + 'px';
        panel.style.top = Math.max(PAD, Math.min(y, Math.max(PAD, maxY))) + 'px';
    }
    function clampAll() {
        Array.prototype.forEach.call(document.querySelectorAll('.fp-floating'), clampIntoView);
    }
    window.addEventListener('resize', clampAll);
    // back to "anchored under the trigger" (an owner may offer this on close)
    function unfloat(panel, floatClass) {
        if (!panel) return;
        panel.classList.remove('fp-floating');
        if (floatClass) panel.classList.remove(floatClass);
        panel.style.left = ''; panel.style.top = ''; panel.style.width = '';
        panel.style.height = ''; panel.style.cursor = '';
    }
    /* The one place the tool windows are named. docs/141 4ac: each closer used
       to carry its own literal pair, so the third window (the Config Manual)
       was "outside" both of the others and closed them. */
    var TOOLS_SEL = '.settings-btn, #settings-dropdown, .calc-btn, #calc-popover, .manual-btn, #manual-popover';
    window.FloatPanel = { drag: drag, resize: resize, isFloating: isFloating,
                          unfloat: unfloat, edgeAt: edgeAt,
                          clampIntoView: clampIntoView, clampAll: clampAll,
                          TOOLS_SEL: TOOLS_SEL, THRESHOLD: THRESHOLD, EDGE: EDGE };
})();
