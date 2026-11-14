/* QSM UI kit parts for script-rendered UI (docs/308).
 *
 * window.UI.<part>(...) returns a DOM node with the SAME tag / class / role /
 * text tree as the Jinja macro of the same name in templates/_ui.html;
 * UI.render(spec) dispatches on spec.part exactly like ui_render().
 * Text is always set through textContent (never as markup), so every value
 * is escaped. No inline handlers, no eval: the one listener is delegated.
 * Loaded as a page bundle only (the /ui-kit sample page), never core.
 */
(function () {
    "use strict";

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }
    function has(v) { return v !== undefined && v !== null; }
    function add(parent, child) { if (child) parent.appendChild(child); return parent; }

    function button(b) {
        var n = el("button", b.cls || "btn-sync", b.label);
        n.type = "button";
        if (b.title) n.title = b.title;
        if (b.aria_label) n.setAttribute("aria-label", b.aria_label);
        if (b.theme) n.setAttribute("data-ui-theme", b.theme);
        if (b.disabled) n.disabled = true;
        return n;
    }
    function actions(buttons, label) {
        var n = el("div", "ui-actions");
        n.setAttribute("role", "group");
        if (label) n.setAttribute("aria-label", label);
        (buttons || []).forEach(function (b) { n.appendChild(button(b)); });
        return n;
    }
    function chip(text) { return el("code", "ui-chip", text); }
    function badge(text, tone) { return el("span", "ui-badge ui-badge--" + (tone || "neutral"), text); }
    function count(text) { return el("span", "ui-count", text); }
    function note(text) { return el("p", "ui-note", text); }
    function check(text, checked, disabled) {
        var l = el("label", "ui-check");
        var i = document.createElement("input");
        i.type = "checkbox";
        if (checked) i.setAttribute("checked", "");
        if (disabled) i.setAttribute("disabled", "");
        l.appendChild(i);
        l.appendChild(document.createTextNode(String(text)));
        return l;
    }

    function pill(text, tone) { return el("span", "ui-pill ui-pill--" + (tone || "neutral"), text); }
    // The middle gives way; the last segment (from the last . or /) never does.
    function path(p) {
        p = String(p);
        var i = Math.max(p.lastIndexOf("."), p.lastIndexOf("/"));
        if (i <= 0) i = p.length;
        var n = el("span", "ui-path");
        n.title = p;
        n.appendChild(el("span", "ui-path-head", p.slice(0, i)));
        n.appendChild(el("span", "ui-path-tail", p.slice(i)));
        return n;
    }
    function headFold() {
        var b = el("button", "ui-btn-icon ui-head-fold");
        b.type = "button";
        // the same words _ui_head_boot.html keeps on a server-rendered one
        var c = document.documentElement.classList.contains("ui-heads-compact");
        b.setAttribute("aria-label", c ? "Unfold the page header" : "Fold the page header");
        b.setAttribute("aria-expanded", c ? "false" : "true");
        b.title = "Fold or unfold the page header (all pages)";
        return b;
    }
    function head(title, level, eyebrow, gist, cnt, acts, data, bdg, page) {
        var h = el("header", (data ? "ui-head ui-head--data" : "ui-head") + (page ? " ui-head--page" : ""));
        if (eyebrow && !data) h.appendChild(el("div", "ui-head-eyebrow", eyebrow));
        var t = el("h" + (level || 2), "ui-head-title", title);
        if (has(cnt)) { t.appendChild(document.createTextNode(" ")); t.appendChild(count(cnt)); }
        if (bdg) t.appendChild(badge(bdg.text, bdg.tone));
        h.appendChild(t);
        if (acts && acts.length) h.appendChild(actions(acts));
        if (gist && !data) h.appendChild(el("p", "ui-head-gist", gist));
        if (page && gist && !data) h.appendChild(headFold());
        return h;
    }
    function notice(tone, key, text, acts) {
        var n = el("div", "ui-notice ui-notice--" + tone);
        n.setAttribute("role", tone === "danger" ? "alert" : "status");
        var ic = el("span", "ui-notice-icon", (tone === "warn" || tone === "danger") ? "⚠" : "ℹ");
        ic.setAttribute("aria-hidden", "true");
        n.appendChild(ic);
        n.appendChild(el("strong", "ui-notice-key", key));
        n.appendChild(el("span", "ui-notice-text", text));
        if (acts && acts.length) n.appendChild(actions(acts));
        return n;
    }
    function callout(tone, title, text) {
        var n = el("div", "ui-callout ui-callout--" + tone);
        n.setAttribute("role", "note");
        n.appendChild(el("div", "ui-callout-title", title));
        n.appendChild(el("p", "ui-callout-text", text));
        return n;
    }
    function verdict(tone, key, why) {
        var n = el("div", "ui-verdict ui-verdict--" + tone);
        var d = el("span", "ui-dot");
        d.setAttribute("aria-hidden", "true");
        n.appendChild(d);
        n.appendChild(el("strong", "ui-verdict-key", key));
        if (why) n.appendChild(el("span", "ui-verdict-why", why));
        return n;
    }
    function isObj(c) { return c !== null && typeof c === "object"; }
    function cell(c, tag, num) {
        var cls = (tag === "th" ? "ui-table-head" : "ui-table-cell") + (num ? " ui-table-num" : "")
            + (isObj(c) && c.muted ? " ui-table-muted" : "");
        var n = el(tag, cls);
        if (tag === "th") n.setAttribute("scope", isObj(c) && c.row ? "row" : "col");
        if (isObj(c) && c.title) n.title = c.title;
        if (isObj(c) && has(c.chip)) n.appendChild(chip(c.chip));
        else if (isObj(c) && has(c.dot)) n.appendChild(verdict(c.dot, c.text));
        else n.textContent = String(isObj(c) ? c.text : c);
        return n;
    }
    function table(columns, rows, kv, numeric, label) {
        numeric = numeric || [];
        var t = el("table", kv ? "ui-table ui-table--kv" : "ui-table");
        if (label) t.setAttribute("aria-label", label);
        if (columns && columns.length) {
            var hr = el("tr");
            columns.forEach(function (c, i) { hr.appendChild(cell(c, "th", numeric.indexOf(i) >= 0)); });
            t.appendChild(el("thead")).appendChild(hr);
        }
        var tb = el("tbody");
        (rows || []).forEach(function (r) {
            var tr = el("tr");
            r.forEach(function (c, i) {
                if (kv && i === 0) tr.appendChild(cell(typeof c === "string" ? { text: c, row: true } : c, "th", false));
                else tr.appendChild(cell(c, "td", numeric.indexOf(i) >= 0));
            });
            tb.appendChild(tr);
        });
        t.appendChild(tb);
        return t;
    }
    function segmented(options, label) {
        var g = el("div", "ui-segmented");
        g.setAttribute("role", "group");
        g.setAttribute("aria-label", label);
        (options || []).forEach(function (o) {
            var b = el("button", "ui-segmented-opt" + (o.selected ? " ui-segmented-on" : ""), o.label);
            b.type = "button";
            if (!o.command) b.setAttribute("aria-pressed", o.selected ? "true" : "false");
            g.appendChild(b);
        });
        return g;
    }
    // K18 choosing (docs/308 §4b): the class sits on the native control.
    function select(o) {
        var s = el("select", "ui-select" + (o.block ? " ui-select--block" : ""));
        if (o.id) s.id = o.id;
        if (o.name) s.setAttribute("name", o.name);
        if (o.label) s.setAttribute("aria-label", o.label);
        if (o.invalid) s.setAttribute("aria-invalid", "true");
        if (o.disabled) s.setAttribute("disabled", "");
        (o.options || []).forEach(function (x) {
            var obj = isObj(x), v = obj ? x.value : x;
            var op = el("option", null, obj ? x.label : x);
            op.setAttribute("value", String(v));
            if (has(o.value) && v === o.value) op.setAttribute("selected", "");
            if (obj && x.disabled) op.setAttribute("disabled", "");
            s.appendChild(op);
        });
        return s;
    }
    function input(o) {
        var i = document.createElement("input");
        i.setAttribute("type", o.type || "text");
        i.className = "ui-input" + (o.num ? " ui-input--num" : "") + (o.block && !o.unit ? " ui-input--block" : "");
        if (o.id) i.id = o.id;
        if (o.name) i.setAttribute("name", o.name);
        if (has(o.value)) i.setAttribute("value", String(o.value));
        if (o.placeholder) i.setAttribute("placeholder", o.placeholder);
        if (o.label) i.setAttribute("aria-label", o.label);
        if (o.invalid) i.setAttribute("aria-invalid", "true");
        if (o.disabled) i.setAttribute("disabled", "");
        if (!o.unit) return i;
        var w = el("span", "ui-input-unit" + (o.block ? " ui-input-unit--block" : ""));
        w.appendChild(i);
        w.appendChild(el("span", "ui-input-unit-text", o.unit));
        return w;
    }
    function field(o) {
        var f = el("div", "ui-field" + (o.inline ? " ui-field--inline" : ""));
        var h = el("div", "ui-field-head");
        var l = el("label", "ui-field-label", o.label);
        if (o.control && o.control.id) l.setAttribute("for", o.control.id);
        h.appendChild(l);
        if (o.source) h.appendChild(badge(o.source.text, o.source.tone));
        f.appendChild(h);
        add(f, render(o.control));
        if (o.hint) f.appendChild(el("p", "ui-field-hint", o.hint));
        if (o.error) { var e = el("p", "ui-field-error", o.error); e.setAttribute("role", "alert"); f.appendChild(e); }
        return f;
    }
    function menu(o) {
        var pick = o.kind !== "action";
        var items = o.items || [];
        var title = null;
        if (pick && has(o.value)) items.forEach(function (it) { if (!it.sep && it.value === o.value) title = it.title; });
        var root = el("div", "ui-menu" + (o.block ? " ui-menu--block" : ""));
        root.setAttribute("data-ui-menu", pick ? "pick" : "action");
        if (pick && has(o.value)) root.setAttribute("data-value", String(o.value));
        var tcls = pick ? "ui-select" + (o.block ? " ui-select--block" : "") : (o.trigger && o.trigger.cls) || "btn-sm secondary";
        var t = el("button", "ui-menu-trigger " + tcls);
        t.type = "button";
        t.id = o.id;
        t.setAttribute("aria-haspopup", pick ? "listbox" : "menu");
        t.setAttribute("aria-expanded", "false");
        t.setAttribute("aria-controls", o.id + "-list");
        if (pick) {
            t.setAttribute("aria-label", o.label);
            t.appendChild(el("span", "ui-menu-value" + (title === null ? " ui-menu-placeholder" : ""), title === null ? (o.placeholder || "Choose") : title));
        } else {
            t.textContent = (o.trigger && o.trigger.label) || o.label;
        }
        root.appendChild(t);
        var list = el("div", "ui-menu-list" + (pick ? " ui-menu-list--pick" : "") + (o.end ? " ui-menu-list--end" : "") + (o.up ? " ui-menu-list--up" : ""));
        list.id = o.id + "-list";
        list.setAttribute("role", pick ? "listbox" : "menu");
        list.setAttribute("aria-label", o.label);
        list.setAttribute("hidden", "");
        items.forEach(function (it) {
            if (it.sep) { var sp = el("div", "ui-menu-sep"); sp.setAttribute("role", "separator"); list.appendChild(sp); return; }
            var r = el("div", "ui-option");
            r.setAttribute("role", pick ? "option" : "menuitem");
            r.setAttribute("tabindex", "-1");
            r.setAttribute("data-value", String(it.value));
            if (pick) r.setAttribute("aria-selected", has(o.value) && it.value === o.value ? "true" : "false");
            if (it.disabled) r.setAttribute("aria-disabled", "true");
            if (pick) { var c = el("span", "ui-option-check"); c.setAttribute("aria-hidden", "true"); r.appendChild(c); }
            r.appendChild(el("span", "ui-option-title", it.title));
            var d = it.disabled && it.reason ? it.reason : it.desc;
            if (d) r.appendChild(el("span", "ui-option-desc", d));
            list.appendChild(r);
        });
        root.appendChild(list);
        return root;
    }
    function toolbar(items, label) {
        var n = el("div", "ui-toolbar");
        n.setAttribute("role", "toolbar");
        n.setAttribute("aria-label", label);
        (items || []).forEach(function (s) {
            if (s.part === "toolbar-sep" || s.part === "toolbar-spacer") {
                var x = el("span", s.part === "toolbar-sep" ? "ui-toolbar-sep" : "ui-toolbar-spacer");
                x.setAttribute("aria-hidden", "true");
                n.appendChild(x);
            } else if (s.part === "button") n.appendChild(button(s));
            else add(n, render(s));
        });
        return n;
    }

    // ---- UI.menu behaviour: one delegated set of listeners serves every
    // .ui-menu on the page, server- or script-rendered. No inline handlers.
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
    document.addEventListener("keydown", function (e) {
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

    function disclosure(summary, cnt, body, open) {
        var d = el("details", "ui-disclosure");
        if (open) d.setAttribute("open", "");
        var s = el("summary", "ui-disclosure-summary", summary);
        if (has(cnt)) { s.appendChild(document.createTextNode(" ")); s.appendChild(count(cnt)); }
        d.appendChild(s);
        var b = el("div", "ui-disclosure-body");
        (body || []).forEach(function (x) { add(b, render(x)); });
        d.appendChild(b);
        return d;
    }
    function item(o) {
        var a = el("article", "ui-item");
        var h = el("div", "ui-item-head");
        if (o.chip) h.appendChild(chip(o.chip));
        if (o.badge) h.appendChild(badge(o.badge.text, o.badge.tone));
        if (o.title) h.appendChild(el("span", "ui-item-title", o.title));
        if (has(o.count)) h.appendChild(count(o.count));
        a.appendChild(h);
        (o.body || []).forEach(function (s) { add(a, typeof s === "string" ? el("p", "ui-item-body", s) : render(s)); });
        if (o.note) a.appendChild(note(o.note));
        if (o.actions && o.actions.length) { var f = el("div", "ui-item-foot"); f.appendChild(actions(o.actions)); a.appendChild(f); }
        return a;
    }
    function dialog(o) {
        var size = o.size || "m";
        var hd = o.head || {};
        var d = el("div", "ui-dialog ui-dialog--" + size);
        d.setAttribute("role", "dialog");
        d.setAttribute("aria-label", hd.title);
        var h = el("div", "ui-dialog-head");
        h.appendChild(head(hd.title, hd.level, hd.eyebrow, hd.gist, hd.count, null, size !== "l" && size !== "xl"));
        if (o.close !== false) h.appendChild(button({ label: "✕", cls: "ui-btn-icon", aria_label: "Close" }));
        d.appendChild(h);
        var b = el("div", "ui-dialog-body");
        (o.body || []).forEach(function (x) { add(b, render(x)); });
        d.appendChild(b);
        if ((o.foot && o.foot.length) || (o.foot_actions && o.foot_actions.length)) {
            var f = el("div", "ui-dialog-foot");
            (o.foot || []).forEach(function (x) { add(f, render(x)); });
            if (o.foot_actions && o.foot_actions.length) f.appendChild(actions(o.foot_actions));
            d.appendChild(f);
        }
        return d;
    }

    function render(s) {
        switch (s && s.part) {
            case "head": return head(s.title, s.level, s.eyebrow, s.gist, s.count, s.actions, s.data, s.badge, s.page);
            case "notice": return notice(s.tone, s.key, s.text, s.actions);
            case "callout": return callout(s.tone, s.title, s.text);
            case "verdict": return verdict(s.tone, s.key, s.why);
            case "item": return item(s);
            case "table": return table(s.columns, s.rows, s.kv, s.numeric, s.label);
            case "actions": return actions(s.buttons, s.label);
            case "chip": return chip(s.text);
            case "badge": return badge(s.text, s.tone);
            case "count": return count(s.text);
            case "note": return note(s.text);
            case "check": return check(s.text, s.checked, s.disabled);
            case "pill": return pill(s.text, s.tone);
            case "path": return path(s.text);
            case "segmented": return segmented(s.options, s.label);
            case "disclosure": return disclosure(s.summary, s.count, s.body, s.open);
            case "select": return select(s);
            case "input": return input(s);
            case "field": return field(s);
            case "menu": return menu(s);
            case "toolbar": return toolbar(s.items, s.label);
            case "dialog": return dialog(s);
            default: return null;
        }
    }

    window.UI = {
        button: button, actions: actions, chip: chip, badge: badge, count: count, note: note, check: check, pill: pill, path: path, headFold: headFold,
        head: head, notice: notice, callout: callout, verdict: verdict, table: table, segmented: segmented,
        disclosure: disclosure, item: item, dialog: dialog, render: render,
        select: select, input: input, field: field, menu: menu, toolbar: toolbar,
        /* Menu behaviour for script callers: open / close / pick by element. */
        menuOpen: function (root) { if (root) mOpen(mParts(root), "first"); },
        menuClose: function (root) { if (root) mClose(mParts(root), false); },
        /* The /ui-kit page: fill every script-side slot from the page's spec list. */
        mountSamples: function (root) {
            root = root || document;
            var src = root.getElementById ? root.getElementById("ui-kit-specs") : null;
            if (!src) return 0;
            var specs = JSON.parse(src.textContent || "[]");
            var n = 0;
            specs.forEach(function (s, i) {
                var slot = root.getElementById("uk-j-" + i);
                if (!slot) return;
                slot.textContent = "";
                add(slot, render(s));
                // The template side already holds these ids: keep the page's ids unique.
                Array.prototype.forEach.call(slot.querySelectorAll("[id], [for], [aria-controls]"), function (x) {
                    ["id", "for", "aria-controls"].forEach(function (a) {
                        if (x.hasAttribute(a)) x.setAttribute(a, x.getAttribute(a) + "-js");
                    });
                });
                n++;
            });
            return n;
        },
    };

    // Delegated: the sample page's theme buttons.
    document.addEventListener("click", function (e) {
        var t = e.target && e.target.closest ? e.target.closest("[data-ui-theme]") : null;
        if (t && typeof window.setTheme === "function") window.setTheme(t.getAttribute("data-ui-theme"));
    });
    function boot() { window.UI.mountSamples(document); }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
    document.addEventListener("htmx:afterSettle", boot);
})();
