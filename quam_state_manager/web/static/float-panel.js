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
    }
    /* The one place the tool windows are named. docs/141 4ac: each closer used
       to carry its own literal pair, so the third window (the Config Manual)
       was "outside" both of the others and closed them. */
    var TOOLS_SEL = '.settings-btn, #settings-dropdown, .calc-btn, #calc-popover, .manual-btn, #manual-popover';
    window.FloatPanel = { drag: drag, isFloating: isFloating, unfloat: unfloat,
                          clampIntoView: clampIntoView, clampAll: clampAll,
                          TOOLS_SEL: TOOLS_SEL, THRESHOLD: THRESHOLD };
})();
