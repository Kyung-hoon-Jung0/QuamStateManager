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
    function note(text, id) { var n = el("p", "ui-note", text); if (id) n.id = id; return n; }
    function check(text, checked, disabled, id, describedby) {
        var l = el("label", disabled ? "ui-check ui-check--off" : "ui-check");
        var i = document.createElement("input");
        i.type = "checkbox";
        if (id) i.id = id;
        if (describedby) i.setAttribute("aria-describedby", describedby);
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
    function callout(tone, title, text, body, acts) {
        var n = el("div", "ui-callout ui-callout--" + tone);
        n.setAttribute("role", "note");
        n.appendChild(el("div", "ui-callout-title", title));
        if (text) n.appendChild(el("p", "ui-callout-text", text));
        if (body && body.length) {
            var b = el("div", "ui-callout-body");
            body.forEach(function (x) { add(b, render(x)); });
            n.appendChild(b);
        }
        if (acts && acts.length) n.appendChild(actions(acts));
        return n;
    }
    function facts(list, label) {
        var d = el("dl", "ui-facts");
        if (label) d.setAttribute("aria-label", label);
        (list || []).forEach(function (f) {
            var row = el("div", "ui-facts-row");
            row.appendChild(el("dt", "ui-facts-label", f.label));
            var v = el("dd", "ui-facts-value", f.value);
            if (f.note) v.appendChild(el("span", "ui-facts-note", f.note));
            row.appendChild(v);
            d.appendChild(row);
        });
        return d;
    }
    function verdict(tone, key, why, strong, meta) {
        var n = el("div", "ui-verdict ui-verdict--" + tone + (strong ? " ui-verdict--strong" : ""));
        if (strong) n.setAttribute("role", tone === "danger" ? "alert" : "status");
        var d = el("span", "ui-dot");
        d.setAttribute("aria-hidden", "true");
        n.appendChild(d);
        n.appendChild(el("strong", "ui-verdict-key", key));
        if (why) n.appendChild(el("span", "ui-verdict-why", why));
        if (meta) n.appendChild(el("span", "ui-verdict-meta", meta));
        return n;
    }
    function isObj(c) { return c !== null && typeof c === "object"; }
    function cell(c, tag, num) {
        var cls = (tag === "th" ? "ui-table-head" : "ui-table-cell") + (num ? " ui-table-num" : "")
            + (isObj(c) && c.muted ? " ui-table-muted" : "") + (isObj(c) && c.tone ? " ui-cell--" + c.tone : "");
        var n = el(tag, cls);
        if (tag === "th") n.setAttribute("scope", isObj(c) && c.row ? "row" : "col");
        if (isObj(c) && c.title) n.title = c.title;
        if (isObj(c) && has(c.chip)) n.appendChild(chip(c.chip));
        else if (isObj(c) && has(c.dot)) n.appendChild(verdict(c.dot, c.text));
        else if (isObj(c)) {
            if (c.line) n.appendChild(lineDot(c.line));
            n.appendChild(document.createTextNode(String(c.text)));
            if (c.note) n.appendChild(el("span", "ui-cell-note", c.note));
        } else n.textContent = String(c);
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
            if (!Array.isArray(r)) {
                var sr = el("tr", "ui-table-sub");
                var sc = el("td", "ui-table-cell");
                sc.setAttribute("colspan", String(columns && columns.length ? columns.length : 1));
                (r.sub || []).forEach(function (x) { add(sc, render(x)); });
                sr.appendChild(sc);
                tb.appendChild(sr);
                return;
            }
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
    function segmented(options, label, labelledby) {
        var g = el("div", "ui-segmented");
        g.setAttribute("role", "group");
        if (labelledby) g.setAttribute("aria-labelledby", labelledby); else g.setAttribute("aria-label", label);
        (options || []).forEach(function (o) {
            var b = el(o.href ? "a" : "button", "ui-segmented-opt" + (o.selected ? " ui-segmented-on" : "") + (o.href && o.selected ? " active" : ""), o.label);
            if (o.href) b.setAttribute("href", o.href); else b.type = "button";
            Object.keys(o.attrs || {}).forEach(function (k) { b.setAttribute(k, o.attrs[k]); });
            if (o.href) { if (o.selected) b.setAttribute("aria-current", "true"); }
            else if (!o.command) b.setAttribute("aria-pressed", o.selected ? "true" : "false");
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
        if (o.describedby) s.setAttribute("aria-describedby", o.describedby);
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
        if (o.describedby) i.setAttribute("aria-describedby", o.describedby);
        if (o.disabled) i.setAttribute("disabled", "");
        if (!o.unit) return i;
        var w = el("span", "ui-input-unit" + (o.block ? " ui-input-unit--block" : "") + (o.invalid ? " ui-input-unit--invalid" : "") + (o.disabled ? " ui-input-unit--disabled" : ""));
        w.appendChild(i);
        w.appendChild(el("span", "ui-input-unit-text", o.unit));
        return w;
    }
    var uidN = 0;
    function uid(prefix) { uidN += 1; return (prefix || "ui") + "-js" + uidN; }
    function validId(v) {
        if (typeof v !== "string" || !v || /\s/.test(v)) throw new Error("ui menu id must be a non-empty string without whitespace: " + JSON.stringify(v));
    }
    function uniq(list) { var out = []; list.forEach(function (x) { if (x && out.indexOf(x) < 0) out.push(x); }); return out; }
    function field(o) {
        // Same rules as the macro: id = o.id, else control.id, else generated;
        // input / select / check take it and are named by <label for>; any
        // other part is named by the label text (unknown parts get a group
        // wrapper). A caller's describedby / labelledby come first.
        var c = o.control || {};
        if (!o.control || !c.part) throw new Error("ui field control needs a part");
        var fid = o.id || c.id || uid("uif");
        validId(fid);
        var single = c.part === "input" || c.part === "select" || c.part === "check";
        var lb = fid + "-flabel";
        var desc = uniq(String(c.describedby || "").split(/\s+/).concat([o.hint ? fid + "-hint" : null, o.error ? fid + "-error" : null])).join(" ");
        var lab = uniq(String(c.labelledby || "").split(/\s+/).concat([lb])).join(" ");
        var f = el("div", "ui-field" + (o.inline ? " ui-field--inline" : ""));
        var h = el("div", "ui-field-head");
        var l;
        if (single) { l = el("label", "ui-field-label", o.label); l.setAttribute("for", fid); }
        else { l = el("span", "ui-field-label", o.label); l.id = lb; }
        h.appendChild(l);
        if (o.source) h.appendChild(badge(o.source.text, o.source.tone));
        f.appendChild(h);
        var ctl;
        if (c.part === "segmented") ctl = segmented(c.options, c.label || o.label, lab);
        else if (c.part === "toolbar") ctl = toolbar(c.items, c.label || o.label, lab);
        else if (c.part === "menu") ctl = menu(Object.assign({}, c, { id: fid, label: c.label || o.label, labelledby: lab }));
        else if (c.part === "select" || c.part === "input") ctl = render(Object.assign({}, c, { id: fid, label: null, describedby: desc || null }));
        else if (c.part === "check") ctl = check(c.text, c.checked, c.disabled, fid, desc || null);
        else {
            ctl = el("div", "ui-field-control");
            ctl.setAttribute("role", "group");
            ctl.setAttribute("aria-labelledby", lab);
            if (desc) ctl.setAttribute("aria-describedby", desc);
            add(ctl, render(c));
        }
        add(f, ctl);
        if (o.hint) { var hn = el("p", "ui-field-hint", o.hint); hn.id = fid + "-hint"; f.appendChild(hn); }
        if (o.error) { var e = el("p", "ui-field-error", o.error); e.id = fid + "-error"; e.setAttribute("role", "alert"); f.appendChild(e); }
        return f;
    }
    function menu(o) {
        validId(o.id);
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
            t.setAttribute("aria-labelledby", (o.labelledby || o.id + "-label") + " " + o.id + "-value");
            if (!o.labelledby) {
                var sl = el("span", "ui-sr", o.label);
                sl.id = o.id + "-label";
                t.appendChild(sl);
            }
            var sv = el("span", "ui-menu-value" + (title === null ? " ui-menu-placeholder" : ""), title === null ? (o.placeholder || "Choose") : title);
            sv.id = o.id + "-value";
            t.appendChild(sv);
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
            if (has(it.value)) r.setAttribute("data-value", String(it.value));
            if (pick) r.setAttribute("aria-selected", has(o.value) && it.value === o.value ? "true" : "false");
            if (it.disabled) r.setAttribute("aria-disabled", "true");
            if (pick) { var c = el("span", "ui-option-check"); c.setAttribute("aria-hidden", "true"); r.appendChild(c); }
            r.appendChild(el("span", "ui-option-title", it.title));
            var d = it.disabled && it.reason ? it.reason : it.desc;
            if (d) r.appendChild(el("span", "ui-option-desc", d));
            if (it.note) { var nt = el("span", "ui-option-desc", it.note); nt.setAttribute("data-ui-option-note", ""); r.appendChild(nt); }
            list.appendChild(r);
        });
        root.appendChild(list);
        return root;
    }
    function toolbar(items, label, labelledby) {
        var n = el("div", "ui-toolbar");
        n.setAttribute("role", "toolbar");
        if (labelledby) n.setAttribute("aria-labelledby", labelledby); else n.setAttribute("aria-label", label);
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


    // K19 reading order (docs/308 section 4c).
    function text(t) {
        return isObj(t) ? el("span", "ui-text-" + (t.level || "strong"), t.text) : document.createTextNode(String(t));
    }
    function panel(o) {
        var fold = !!o.fold;
        var p = el(fold ? "details" : "section", "ui-panel" + (fold ? " ui-panel--fold" : ""));
        if (fold && o.open) p.setAttribute("open", "");
        if (!fold) p.setAttribute("aria-label", o.title);
        var h = el(fold ? "summary" : "div", "ui-panel-head");
        if (has(o.step)) { var st = el("span", "ui-panel-step", o.step); st.setAttribute("aria-hidden", "true"); h.appendChild(st); }
        h.appendChild(el("h3", "ui-panel-title", o.title));
        if (o.hint) h.appendChild(el("span", "ui-panel-hint", o.hint));
        if (o.summary && o.summary.length) {
            var sm = el("span", "ui-panel-sum");
            o.summary.forEach(function (t) { add(sm, isObj(t) && t.part ? render(t) : text(t)); });
            h.appendChild(sm);
        }
        p.appendChild(h);
        if (o.body && o.body.length) {
            var b = el("div", "ui-panel-body" + (o.flush ? " ui-panel-body--flush" : ""));
            o.body.forEach(function (x) { add(b, typeof x === "string" ? el("p", "ui-text-quiet", x) : render(x)); });
            p.appendChild(b);
        }
        return p;
    }
    function zone(o) {
        var z = el(o.fold ? "details" : "section", "ui-zone" + (o.fill ? " ui-zone--fill" : "") + (o.quiet ? " ui-zone--quiet" : "")
            + (o.tone ? " ui-zone--" + o.tone : "") + (o.fold ? " ui-zone--fold" : ""));
        z.setAttribute("aria-label", o.title);
        if (o.fold && o.open) z.setAttribute("open", "");
        var h = el(o.fold ? "summary" : "div", "ui-zone-head");
        if (has(o.step)) { var st = el("span", "ui-zone-step", o.step); st.setAttribute("aria-hidden", "true"); h.appendChild(st); }
        h.appendChild(el("h3", "ui-zone-title", o.title));
        if (o.summary && o.summary.length) {
            var sm = el("span", "ui-zone-sum");
            o.summary.forEach(function (t) { add(sm, isObj(t) && t.part ? render(t) : text(t)); });
            h.appendChild(sm);
        }
        z.appendChild(h);
        var b = el("div", "ui-zone-body");
        if (o.quiet) b.appendChild(el("p", "ui-zone-empty", o.quiet));
        else (o.body || []).forEach(function (x) { add(b, typeof x === "string" ? el("p", "ui-text-quiet", x) : render(x)); });
        z.appendChild(b);
        return z;
    }
    function stats(tiles, label) {
        var g = el("div", "ui-stats");
        g.setAttribute("role", "group");
        if (label) g.setAttribute("aria-label", label);
        (tiles || []).slice(0, 4).forEach(function (t) {
            var d = el("div", "ui-stat" + (t.tone ? " ui-stat--" + t.tone : ""));
            if (t.title) d.title = t.title;
            d.appendChild(el("div", "ui-stat-label", t.label));
            var v = el("p", "ui-stat-value", t.value);
            if (t.unit) v.appendChild(el("span", "ui-stat-unit", t.unit));
            d.appendChild(v);
            if (t.note) d.appendChild(el("p", "ui-stat-note", t.note));
            g.appendChild(d);
        });
        return g;
    }
    var RESULT_WORD = { pass: "passed", fail: "failed", skip: "skipped" };
    function checks(list, label) {
        var w = el("div", "ui-checks");
        var ul = el("ul", "ui-checks-row");
        ul.setAttribute("aria-label", label || "Checks");
        (list || []).forEach(function (c) {
            var li = el("li", "ui-result" + (c.state !== "pass" ? " ui-result--" + c.state : ""));
            li.appendChild(el("span", "ui-sr", RESULT_WORD[c.state] + ": "));
            li.appendChild(document.createTextNode(c.name));
            ul.appendChild(li);
        });
        w.appendChild(ul);
        (list || []).forEach(function (c) {
            if (c.state !== "fail") return;
            var r = el("p", "ui-fail-row");
            r.appendChild(el("strong", "ui-fail-name", c.name + "."));
            r.appendChild(document.createTextNode(" " + (c.why || "")));
            w.appendChild(r);
        });
        return w;
    }
    function slot(o) {
        var d = el("div", "ui-slot ui-line-" + (o.line || 1));
        var h = el("div", "ui-slot-head", o.title);
        h.appendChild(el("code", "ui-slot-chip", o.chip));
        d.appendChild(h);
        if (o.body && o.body.length) {
            var b = el("div", "ui-slot-body");
            o.body.forEach(function (x) { add(b, render(x)); });
            d.appendChild(b);
        }
        return d;
    }
    function lineDot(line) { var d = el("span", "ui-line-dot ui-line-" + (line || 1)); d.setAttribute("aria-hidden", "true"); return d; }
    function grid2(items) { var g = el("div", "ui-grid2"); (items || []).forEach(function (x) { add(g, render(x)); }); return g; }

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
            case "callout": return callout(s.tone, s.title, s.text, s.body, s.actions);
            case "facts": return facts(s.items, s.label);
            case "verdict": return verdict(s.tone, s.key, s.why, s.strong, s.meta);
            case "item": return item(s);
            case "table": return table(s.columns, s.rows, s.kv, s.numeric, s.label);
            case "actions": return actions(s.buttons, s.label);
            case "chip": return chip(s.text);
            case "badge": return badge(s.text, s.tone);
            case "count": return count(s.text);
            case "note": return note(s.text, s.id);
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
            case "zone": return zone(s);
            case "panel": return panel(s);
            case "stats": return stats(s.tiles, s.label);
            case "checks": return checks(s.checks, s.label);
            case "slot": return slot(s);
            case "grid2": return grid2(s.items);
            case "field-stack": { var fs = el("div", "ui-field-stack ui-field-stack--dense"); (s.items || []).forEach(function (x) { add(fs, render(x)); }); return fs; }
            case "placeholder": return el("div", "ui-placeholder", s.text);
            case "line": { var ln = el("span"); ln.appendChild(lineDot(s.line)); ln.appendChild(document.createTextNode(String(s.text))); return ln; }
            case "dialog": return dialog(s);
            default: return null;
        }
    }

    /* A question takes the focus on its safe button: `safe` is a selector for it
       (Cancel); without one, the first enabled button. */
    function focusQuestion(root, safe) {
        if (!root) return;
        var first = (safe && root.querySelector(safe + ':not(:disabled)')) || root.querySelector('button:not(:disabled)');
        if (root.scrollIntoView) root.scrollIntoView({ block: 'nearest' });
        if (first) first.focus();
    }
    window.UI = {
        focusQuestion: focusQuestion,
        button: button, actions: actions, chip: chip, badge: badge, count: count, note: note, check: check, pill: pill, path: path, headFold: headFold,
        head: head, notice: notice, callout: callout, facts: facts, verdict: verdict, table: table, segmented: segmented,
        disclosure: disclosure, item: item, dialog: dialog, render: render,
        select: select, input: input, field: field, menu: menu, toolbar: toolbar,
        zone: zone, panel: panel, stats: stats, checks: checks, slot: slot, lineDot: lineDot, text: text, grid2: grid2,
        /* Menu behaviour for script callers: open / close / pick by element. */
        menuOpen: function (root) { if (root && window.UIMenu) window.UIMenu.open(root); },
        menuClose: function (root) { if (root && window.UIMenu) window.UIMenu.close(root); },
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
                Array.prototype.forEach.call(slot.querySelectorAll("[id], [for], [aria-controls], [aria-labelledby], [aria-describedby]"), function (x) {
                    ["id", "for", "aria-controls", "aria-labelledby", "aria-describedby"].forEach(function (a) {
                        if (x.hasAttribute(a)) x.setAttribute(a, x.getAttribute(a).split(" ").map(function (v) { return v + "-js"; }).join(" "));
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
