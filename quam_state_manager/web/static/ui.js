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
    function check(text, checked) {
        var l = el("label", "ui-check");
        var i = document.createElement("input");
        i.type = "checkbox";
        if (checked) i.setAttribute("checked", "");
        l.appendChild(i);
        l.appendChild(document.createTextNode(String(text)));
        return l;
    }

    function head(title, level, eyebrow, gist, cnt, acts, data) {
        var h = el("header", data ? "ui-head ui-head--data" : "ui-head");
        if (eyebrow && !data) h.appendChild(el("div", "ui-head-eyebrow", eyebrow));
        var t = el("h" + (level || 2), "ui-head-title", title);
        if (has(cnt)) { t.appendChild(document.createTextNode(" ")); t.appendChild(count(cnt)); }
        h.appendChild(t);
        if (acts && acts.length) h.appendChild(actions(acts));
        if (gist && !data) h.appendChild(el("p", "ui-head-gist", gist));
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
        h.appendChild(head(hd.title, hd.level, hd.eyebrow, hd.gist, hd.count, null, size !== "l"));
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
            case "head": return head(s.title, s.level, s.eyebrow, s.gist, s.count, s.actions, s.data);
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
            case "check": return check(s.text, s.checked);
            case "segmented": return segmented(s.options, s.label);
            case "disclosure": return disclosure(s.summary, s.count, s.body, s.open);
            case "dialog": return dialog(s);
            default: return null;
        }
    }

    window.UI = {
        button: button, actions: actions, chip: chip, badge: badge, count: count, note: note, check: check,
        head: head, notice: notice, callout: callout, verdict: verdict, table: table, segmented: segmented,
        disclosure: disclosure, item: item, dialog: dialog, render: render,
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
