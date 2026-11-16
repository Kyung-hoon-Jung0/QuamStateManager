/* QSM UI kit: menu and toolbar BEHAVIOUR (docs/308 section 4b).
 *
 * A CORE script (base.html, every page): one delegated set of document
 * listeners serves every .ui-menu and .ui-toolbar, whether the server
 * rendered it, an htmx partial swapped it in, a dialog element holds it, or
 * static/ui.js (the page bundle that BUILDS parts in script) created it.
 * Nothing binds per element, so nothing needs re-binding after a swap.
 * No inline handlers, no eval. Guarded against a second evaluation.
 */
(function () {
    "use strict";
    if (window.UIMenu) return;
    var openMenu = null;
    function mParts(root) {
        return { root: root, trigger: root.querySelector(".ui-menu-trigger"), list: root.querySelector(".ui-menu-list") };
    }
    function mRows(m, enabledOnly) {
        return Array.prototype.filter.call(m.list.querySelectorAll(".ui-option"), function (r) {
            return !enabledOnly || r.getAttribute("aria-disabled") !== "true";
        });
    }
    function mClose(m, refocus) {
        if (!m || !m.list) return;
        m.list.setAttribute("hidden", "");
        if (m.list.hasAttribute("data-ui-auto-up")) { m.list.classList.remove("ui-menu-list--up"); m.list.removeAttribute("data-ui-auto-up"); }
        m.trigger.setAttribute("aria-expanded", "false");
        if (openMenu && openMenu.root === m.root) openMenu = null;
        if (refocus) m.trigger.focus();
    }
    function mFocus(m, row) { if (row) row.focus(); }
    function mOpen(m, where) {
        if (openMenu && openMenu.root !== m.root) mClose(openMenu, false);
        m.list.removeAttribute("hidden");
        m.trigger.setAttribute("aria-expanded", "true");
        openMenu = m;
        // Open upward when there is no room below and more room above.
        var tr = m.trigger.getBoundingClientRect(), h = m.list.offsetHeight;
        var vh = window.innerHeight || document.documentElement.clientHeight || 0;
        if (h && tr.bottom + h > vh && tr.top > vh - tr.bottom && !m.list.classList.contains("ui-menu-list--up")) {
            m.list.classList.add("ui-menu-list--up");
            m.list.setAttribute("data-ui-auto-up", "");
        }
        var rows = mRows(m, true);
        var sel = m.list.querySelector('.ui-option[aria-selected="true"]');
        if (sel && sel.getAttribute("aria-disabled") === "true") sel = null;
        mFocus(m, where === "last" ? rows[rows.length - 1] : (sel || rows[0]));
    }
    function mPick(m, row) {
        if (!row || row.getAttribute("aria-disabled") === "true") return false;
        var value = row.getAttribute("data-value");
        var titleEl = row.querySelector(".ui-option-title");
        var title = titleEl ? titleEl.textContent : "";
        var kind = m.root.getAttribute("data-ui-menu") || "pick";
        if (kind === "pick") {
            mRows(m, false).forEach(function (r) { r.setAttribute("aria-selected", r === row ? "true" : "false"); });
            m.root.setAttribute("data-value", value);
            var v = m.trigger.querySelector(".ui-menu-value");
            if (v) { v.textContent = title; v.classList.remove("ui-menu-placeholder"); }
        }
        mClose(m, true);
        m.root.dispatchEvent(new CustomEvent("ui-menu-pick", { bubbles: true, detail: { value: value, title: title, kind: kind } }));
        return true;
    }
    function mMove(m, row, step) {
        var rows = mRows(m, true);
        if (!rows.length) return;
        var i = rows.indexOf(row);
        i = i < 0 ? (step > 0 ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, i + step));
        mFocus(m, rows[i]);
    }
    function mType(m, row, ch) {
        var rows = mRows(m, true);
        if (!rows.length) return;
        var start = rows.indexOf(row);
        ch = ch.toLowerCase();
        for (var k = 1; k <= rows.length; k++) {
            var r = rows[(start + k) % rows.length];
            var t = r.querySelector(".ui-option-title");
            if (t && t.textContent.trim().toLowerCase().indexOf(ch) === 0) { mFocus(m, r); return; }
        }
    }
    function menuOf(node) {
        var root = node && node.closest ? node.closest(".ui-menu") : null;
        return root ? mParts(root) : null;
    }
    document.addEventListener("click", function (e) {
        var t = e.target;
        var m = menuOf(t);
        if (!m) { if (openMenu) mClose(openMenu, false); return; }
        if (t.closest(".ui-menu-trigger")) {
            if (m.list.hasAttribute("hidden")) mOpen(m, "first"); else mClose(m, false);
            return;
        }
        var row = t.closest(".ui-option");
        if (row) mPick(m, row);
    });
    // Modifier rule for every keydown handler below (keys the browser or the OS owns stay theirs):
    //   browserNav  Alt/Meta + ArrowLeft/ArrowRight is history or line navigation: no handler acts on it.
    //   arrowMod    any Alt/Ctrl/Meta with an arrow key: the toolbar and the segmented control leave it alone.
    //   hasMod      any Alt/Ctrl/Meta at all: the segmented control (new, no older behaviour to keep) leaves it alone.
    // Menus keep what they always did for the rest (Alt+ArrowDown opens, Ctrl+Enter picks).
    function hasMod(e) { return !!(e.altKey || e.ctrlKey || e.metaKey); }
    function arrowMod(e) { return hasMod(e) && e.key.indexOf("Arrow") === 0; }
    function browserNav(e) { return !!(e.altKey || e.metaKey) && (e.key === "ArrowLeft" || e.key === "ArrowRight"); }
    document.addEventListener("keydown", function (e) {
        if (browserNav(e)) return;
        var m = menuOf(e.target);
        if (!m) return;
        var k = e.key;
        if (e.target.closest(".ui-menu-trigger")) {
            if (k === "ArrowDown" || k === "Enter" || k === " ") { e.preventDefault(); mOpen(m, "first"); }
            else if (k === "ArrowUp") { e.preventDefault(); mOpen(m, "last"); }
            else if (k === "Escape" && !m.list.hasAttribute("hidden")) { e.preventDefault(); mClose(m, true); }
            return;
        }
        var row = e.target.closest(".ui-option");
        if (!row) return;
        if (k === "ArrowDown") { e.preventDefault(); mMove(m, row, 1); }
        else if (k === "ArrowUp") { e.preventDefault(); mMove(m, row, -1); }
        else if (k === "Home") { e.preventDefault(); mMove(m, null, 1); }
        else if (k === "End") { e.preventDefault(); mMove(m, null, -1); }
        else if (k === "Enter" || k === " ") { e.preventDefault(); mPick(m, row); }
        else if (k === "Escape") { e.preventDefault(); mClose(m, true); }
        else if (k === "Tab") mClose(m, false);
        else if (k.length === 1 && /\S/.test(k) && !e.ctrlKey && !e.metaKey && !e.altKey) mType(m, row, k);
    });

    // Toolbar: Left / Right / Home / End move focus between its controls
    // (role=toolbar); a text field, a select or a radio keeps its own arrow keys.
    function tbItems(tb) {
        return Array.prototype.filter.call(tb.querySelectorAll("button, select, input, [tabindex]"), function (n) {
            if (n.disabled || n.closest(".ui-menu-list") || n.getAttribute("tabindex") === "-1") return false;
            return n.closest(".ui-toolbar") === tb;
        });
    }
    document.addEventListener("keydown", function (e) {
        if (arrowMod(e)) return;
        var k = e.key;
        if (k !== "ArrowLeft" && k !== "ArrowRight" && k !== "Home" && k !== "End") return;
        var t = e.target;
        var tb = t && t.closest ? t.closest(".ui-toolbar") : null;
        if (!tb || t.closest(".ui-menu-list")) return;
        if (segStep(t, k)) return;   // inside a segmented control the control moves first
        var tag = t.tagName;
        if (tag === "SELECT" || (tag === "INPUT" && t.type !== "checkbox")) return;   // text fields, selects and radios keep their own arrow keys
        var items = tbItems(tb), i = items.indexOf(t);
        if (i < 0) return;
        e.preventDefault();
        var j = k === "Home" ? 0 : k === "End" ? items.length - 1 : Math.max(0, Math.min(items.length - 1, i + (k === "ArrowRight" ? 1 : -1)));
        items[j].focus();
    });

    // Navigation segments preserve their links and request attributes.
    function selectSegment(option) {
        var group = option && option.closest('.ui-segmented');
        if (!group) return;
        group.querySelectorAll('.ui-segmented-opt').forEach(function (el) {
            var on = el === option;
            el.classList.toggle('ui-segmented-on', on);
            if (el.tagName === 'A') {
                // a link that navigates carries aria-current; a button that toggles carries aria-pressed
                el.classList.toggle('active', on);
                if (on) el.setAttribute('aria-current', 'true'); else el.removeAttribute('aria-current');
            } else el.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }
    // A button option: Left / Right / Home / End only move focus to another option
    // of the same control; Enter or Space presses it as before, so an arrow key never
    // changes a selection (and never writes what a press of that option would write).
    function segButtons(option) {
        var group = option.closest('.ui-segmented');
        return group ? Array.prototype.filter.call(group.querySelectorAll('button.ui-segmented-opt'), function (b) {
            return b.tagName === 'BUTTON' && !b.disabled && b.closest('.ui-segmented') === group;
        }) : [];
    }
    function segStep(t, k) {
        var option = t && t.closest ? t.closest('button.ui-segmented-opt') : null;
        if (!option || option.tagName !== 'BUTTON') return null;
        var items = segButtons(option), i = items.indexOf(option);
        if (i < 0 || items.length < 2) return null;
        var j = k === 'Home' ? 0 : k === 'End' ? items.length - 1 :
            k === 'ArrowRight' ? i + 1 : k === 'ArrowLeft' ? i - 1 : null;
        if (j === null) return null;
        // like the link options, the ends wrap round; inside a toolbar an end hands over to the toolbar
        if (!option.closest('.ui-toolbar')) j = (j + items.length) % items.length;
        return j >= 0 && j < items.length && j !== i ? items[j] : null;
    }
    document.addEventListener('keydown', function (e) {
        if (hasMod(e) || e.shiftKey) return;
        var next = segStep(e.target, e.key);
        if (!next) return;
        e.preventDefault();
        next.focus();
    });
    document.addEventListener('keydown', function (e) {
        if (hasMod(e) || e.shiftKey) return;
        var option = e.target.closest && e.target.closest('.ui-segmented-opt');
        if (!option || option.tagName !== 'A') return;
        var items = Array.from(option.parentElement.querySelectorAll('.ui-segmented-opt'));
        var i = items.indexOf(option), k = e.key;
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', ' '].includes(k)) return;
        e.preventDefault();
        var j = k === 'Home' ? 0 : k === 'End' ? items.length - 1 :
            k === ' ' ? i : (i + (k === 'ArrowRight' ? 1 : -1) + items.length) % items.length;
        items[j].focus();
        items[j].click();
    });
    document.addEventListener('click', function (e) {
        var option = e.target.closest && e.target.closest('a.ui-segmented-opt');
        if (option) selectSegment(option);
    }, true);

    window.UIMenu = {
        selectSegment: selectSegment,
        open: function (root) { mOpen(mParts(root), "first"); },
        close: function (root) { mClose(mParts(root), false); },
    };
})();
