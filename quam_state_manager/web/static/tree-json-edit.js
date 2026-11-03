/* Edit as JSON (Json Tree free edit, D-12, D-22..D-28).

   One panel above the Json Tree: a CodeMirror 6 editor (window.SmCodeMirror)
   over one subtree or a whole file. Save posts the text; the server diffs it
   against the working copy in the tree's leaf grammar and writes ONE
   /field/edit-batch (one undo group). A refusal writes nothing and every
   refused path is listed with a "go to line" link. An FSP change commits with
   the batch and raises the shared compensation notice (the global fetch hook
   reads X-SM-Fsp-Changed -- no second helper here). */
(function () {
    "use strict";
    var S = null;                 // the open session

    function isDark() {
        // SM themes are light, dark and qm (the default, a dark theme): only "light" is light.
        return (document.documentElement.getAttribute("data-theme") || "qm") !== "light";
    }
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    function btn(cls, text, fn) {
        var b = el("button", cls, text);
        b.type = "button";
        b.addEventListener("click", fn);
        return b;
    }

    /* line/column of a JSON.parse failure (null when the text parses) */
    function parseError(text) {
        try { JSON.parse(text); return null; } catch (e) {
            var msg = String(e && e.message || e), off = null, line = null, col = null;
            var lc = /line (\d+) column (\d+)/.exec(msg);
            var pm = /position (\d+)/.exec(msg);
            if (pm) off = +pm[1];
            if (lc) { line = +lc[1]; col = +lc[2]; }
            if (off == null && lc) {
                var ls = text.split("\n"), o = 0;
                for (var i = 0; i < line - 1 && i < ls.length; i++) o += ls[i].length + 1;
                off = o + col - 1;
            }
            if (off == null) off = text.length;
            if (line == null) {
                var before = text.slice(0, off).split("\n");
                line = before.length; col = before[before.length - 1].length + 1;
            }
            return {line: line, col: col, offset: Math.min(off, text.length),
                    message: msg.replace(/^JSON\.parse:\s*/, "")};
        }
    }

    /* { relative dot path: line } for a text that parses (keys at their key,
       list elements at their value). */
    function pathLines(text) {
        var i = 0, n = text.length, line = 1, out = {};
        function ws() {
            while (i < n) {
                var c = text[i];
                if (c === "\n") { line++; i++; }
                else if (c === " " || c === "\t" || c === "\r") i++;
                else break;
            }
        }
        function str() {
            var start = i; i++;
            while (i < n && text[i] !== '"') { if (text[i] === "\\") i++; i++; }
            i++;
            try { return JSON.parse(text.slice(start, i)); } catch (e) { return text.slice(start + 1, i - 1); }
        }
        function val(p) {
            ws();
            if (!(p in out)) out[p] = line;
            var c = text[i];
            if (c === "{") {
                i++; ws();
                if (text[i] === "}") { i++; return; }
                while (i < n) {
                    ws(); var kl = line; var k = str();
                    var cp = p ? p + "." + k : String(k);
                    out[cp] = kl;
                    ws(); i++;            // ':'
                    val(cp); ws();
                    if (text[i] === ",") { i++; continue; }
                    i++; return;
                }
            } else if (c === "[") {
                i++; ws();
                if (text[i] === "]") { i++; return; }
                for (var idx = 0; i < n; idx++) {
                    val(p ? p + "." + idx : String(idx)); ws();
                    if (text[i] === ",") { i++; continue; }
                    i++; return;
                }
            } else if (c === '"') { str(); }
            else { while (i < n && ",]}\n\r\t ".indexOf(text[i]) < 0) i++; }
        }
        val("");
        return out;
    }
    /* the line of an absolute path in the editor text (nearest ancestor when
       the path itself is gone, e.g. a deleted key) */
    function lineOf(text, base, path) {
        var rel = base ? (path === base ? "" : path.slice(base.length + 1)) : path;
        var map;
        try { map = pathLines(text); } catch (e) { return 1; }
        while (true) {
            if (rel in map) return map[rel];
            if (!rel) return 1;
            var k = rel.lastIndexOf(".");
            rel = k < 0 ? "" : rel.slice(0, k);
        }
    }

    function getText() { return S && S.ed ? S.ed.getDoc() : ""; }
    function dirty() { return !!S && getText() !== S.initial; }

    function say(text, level) {
        if (!S) return;
        S.msg.textContent = text || "";
        S.msg.className = "ui-jedit-msg" + (level ? " ui-jedit-msg-" + level : "");
    }

    function lint() {
        if (!S || !S.ed) return null;
        var t = getText(), pe = parseError(t);
        if (pe) {
            say("Line " + pe.line + ", column " + pe.col + ": " + pe.message, "error");
            if (S.ed.setDiagnostics) S.ed.setDiagnostics([{from: pe.offset, to: Math.min(pe.offset + 1, t.length),
                                                          severity: "error", message: pe.message}]);
        } else {
            if (S.msg.classList.contains("ui-jedit-msg-error")) say("");
            if (S.ed.setDiagnostics) S.ed.setDiagnostics([]);
        }
        return pe;
    }

    function gotoLine(line) {
        if (!S || !S.ed) return;
        var v = S.ed.view;
        try {
            var ln = v.state.doc.line(Math.max(1, Math.min(line, v.state.doc.lines)));
            v.dispatch({selection: {anchor: ln.from}, scrollIntoView: true});
        } catch (e) { /* a fake view in a harness */ }
        S.lastGoto = line;
        S.ed.focus();
    }

    function showRefused(rows) {
        S.list.innerHTML = "";
        S.list.hidden = !rows.length;
        var text = getText();
        rows.forEach(function (r) {
            var li = el("li", "ui-jedit-refused-row");
            li.appendChild(el("code", "ui-jedit-path", r.path));
            li.appendChild(el("span", "ui-jedit-reason", r.error || "refused"));
            var line = lineOf(text, S.path, r.path);
            var go = btn("btn-sm outline ui-jedit-goto", "go to line " + line, function () { gotoLine(line); });
            go.setAttribute("data-line", String(line));
            li.appendChild(go);
            S.list.appendChild(li);
        });
    }

    function afterWrite(d) {
        if (d && d.tray_html && typeof window._swapPendingTray === "function") {
            window._swapPendingTray(d.tray_html);
            if (window._restoreTrayState) window._restoreTrayState();
        }
        window.__lastUserAct = 0;
        if (window._followOnExplorer) window._followOnExplorer();
        if (window._diagChanged) window._diagChanged();
    }

    function save(rebase) {
        if (!S || S.busy) return;
        if (lint()) return;                 // D-24: a parse error saves nothing
        var text = getText();
        if (!rebase && text === S.initial) { close(true); return; }
        S.busy = true;
        S.save.disabled = true;
        say("Saving…");
        var body = {tab: S.tab, path: S.path, text: text, base_seq: S.seq,
                    expect_chip: window.__chipToken || ""};
        if (rebase) { body.rebase = true; body.base_text = S.initial; body.open_token = S.token || ""; }
        return fetch("/tree/json-edit/save", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body)
        }).then(function (r) {
            return r.json().then(function (d) { return {status: r.status, d: d}; });
        }).then(function (res) {
            if (!S) return;
            S.busy = false; S.save.disabled = false;
            var d = res.d || {};
            if (d.ok) {
                var n = (d.results || []).length;
                afterWrite(d);
                close(true);
                var warn = (d.warnings || []).map(function (w) { return w.path + " " + w.warning; });
                if (window.showToast) window.showToast((n ? "Saved " + n + " change" + (n === 1 ? "" : "s")
                    + " in one undo step" : "Nothing changed")
                    + (warn.length ? ". Warning: " + warn.join("; ") : ""), warn.length ? "warning" : "info");
                return d;
            }
            if (d.moved) {
                S.moved = true;
                say("The working copy changed since this editor opened (another edit or window). Nothing was written.", "warn");
                S.list.innerHTML = ""; S.list.hidden = true;
                S.rebase.hidden = false;
                return d;
            }
            if (d.parse_error) { lint(); return d; }
            var rows = d.refused || (d.results || []).filter(function (x) {
                return !x.applied && x.error && !/^rolled back|^not written/.test(x.error);
            }).map(function (x) { return {path: x.dot_path, error: x.error}; });
            var why = d.error || "Refused";
            say(/nothing was written/i.test(why) ? why : why + " — nothing was written.", "error");
            showRefused(rows);
            return d;
        }).catch(function () {
            if (!S) return;
            S.busy = false; S.save.disabled = false;
            say("Couldn't reach the app; nothing was saved.", "error");
        });
    }

    function close(force) {
        if (!S) return;
        if (!force && dirty()) { askDiscard(); return; }
        var s = S;
        S = null;
        try { if (s.ed) s.ed.destroy(); } catch (e) { /* already gone */ }
        if (s.themeObs) s.themeObs.disconnect();
        s.panel.remove();
        if (s.back && s.back.isConnected && s.back.focus) s.back.focus();
    }

    function askDiscard() {
        S.ask.hidden = false;
        var keep = S.ask.querySelector(".ui-jedit-keep");
        if (keep) keep.focus();
    }

    function keys(e) {
        if (!S) return;
        if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "s" || e.key === "S")) {
            e.preventDefault(); e.stopPropagation(); save(S.moved);
        } else if (e.key === "Escape") {
            // the search panel's own Escape closes the search first
            if (e.target && e.target.closest && e.target.closest(".cm-search")) return;
            e.preventDefault(); e.stopPropagation();
            if (!S.ask.hidden) { S.ask.hidden = true; S.ed.focus(); return; }
            close(false);
        }
    }

    function treeHost(tab) {
        return document.getElementById(tab === "wiring" ? "explorer-tree-wiring" : "explorer-tree-state");
    }

    function build(opts, d) {
        var panel = el("section", "ui-jedit");
        panel.setAttribute("aria-label", "Edit as JSON");
        var head = el("div", "ui-jedit-head");
        head.appendChild(el("span", "ui-jedit-title",
            "Edit as JSON — " + (d.path || ((d.tab === "wiring" ? "wiring" : "state") + ".json (whole file)"))));
        var saveB = btn("btn-sm ui-jedit-save", "Save", function () { save(S && S.moved); });
        saveB.title = "Save (Ctrl+S) — one undo step";
        var rebaseB = btn("btn-sm outline ui-jedit-rebase", "Re-diff on the new base", function () { save(true); });
        rebaseB.title = "Write only your own changes on top of the changed working copy";
        rebaseB.hidden = true;
        var closeB = btn("btn-sm outline ui-jedit-close", "Close", function () { close(false); });
        closeB.title = "Close (Esc)";
        head.appendChild(saveB); head.appendChild(rebaseB); head.appendChild(closeB);
        var msg = el("div", "ui-jedit-msg");
        msg.setAttribute("role", "status");
        var ask = el("div", "ui-jedit-ask");
        ask.hidden = true;
        ask.appendChild(el("span", "ui-jedit-ask-text", "Discard your unsaved changes?"));
        ask.appendChild(btn("btn-sm ui-jedit-discard", "Discard", function () { close(true); }));
        ask.appendChild(btn("btn-sm outline ui-jedit-keep", "Keep editing", function () {
            ask.hidden = true; if (S) S.ed.focus();
        }));
        var list = el("ul", "ui-jedit-refused");
        list.hidden = true;
        var host = el("div", "ui-jedit-host");
        panel.appendChild(head); panel.appendChild(msg); panel.appendChild(ask);
        panel.appendChild(list); panel.appendChild(host);
        var tree = treeHost(d.tab);
        if (tree && tree.parentNode) tree.parentNode.insertBefore(panel, tree);
        else document.body.appendChild(panel);
        S = {panel: panel, msg: msg, ask: ask, list: list, save: saveB, rebase: rebaseB,
             tab: d.tab, path: d.path || "", seq: d.mutation_seq, initial: d.text, token: d.open_token || "",
             back: opts.back || null, busy: false, moved: false};
        S.ed = window.SmCodeMirror.create(host, {
            doc: d.text, dark: isDark(), lint: true,
            onChange: function () { if (S) { S.ask.hidden = true; lint(); } }
        });
        panel.addEventListener("keydown", keys, true);
        if (window.MutationObserver) {
            S.themeObs = new MutationObserver(function () { if (S && S.ed.setDark) S.ed.setDark(isDark()); });
            S.themeObs.observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
        }
        S.ed.focus();
        if (panel.scrollIntoView) try { panel.scrollIntoView({block: "nearest"}); } catch (e) { /* jsdom */ }
        return S;
    }

    /* D-22: a whole file over 2 MB warns before opening */
    function warnLarge(opts, d) {
        var tree = treeHost(d.tab);
        // one warning at a time (a second press while the first is up)
        document.querySelectorAll(".ui-jedit-warn").forEach(function (x) { x.remove(); });
        var w = el("div", "ui-jedit ui-jedit-warn");
        w.appendChild(el("span", "ui-jedit-warn-text",
            "This file is " + (d.bytes / 1048576).toFixed(1) + " MB; the editor may be slow. "
            + "Editing one subtree (Edit as JSON on its row) is faster."));
        w.appendChild(btn("btn-sm ui-jedit-open-anyway", "Open anyway", function () { w.remove(); build(opts, d); }));
        w.appendChild(btn("btn-sm outline ui-jedit-cancel", "Cancel", function () { w.remove(); }));
        if (tree && tree.parentNode) tree.parentNode.insertBefore(w, tree);
        else document.body.appendChild(w);
        return w;
    }

    function open(opts) {
        opts = opts || {};
        if (!window.SmCodeMirror) return Promise.resolve(null);
        if (S) {
            if (S.path === (opts.path || "") && S.tab === (opts.tab || "state")) { S.ed.focus(); return Promise.resolve(S); }
            if (dirty()) { askDiscard(); return Promise.resolve(null); }
            close(true);
        }
        var stale = document.querySelector(".ui-jedit-warn");
        if (stale) stale.remove();
        var q = "/tree/json-edit/source?tab=" + encodeURIComponent(opts.tab || "state")
              + "&path=" + encodeURIComponent(opts.path || "");
        return fetch(q, {cache: "no-store"}).then(function (r) { return r.json(); }).then(function (d) {
            if (!d || !d.ok) {
                if (window.showToast) window.showToast((d && d.error) || "Could not open the editor", "error");
                return null;
            }
            if (!d.path && d.large) return warnLarge(opts, d);
            return build(opts, d);
        });
    }

    function openWhole() {
        var tab = (window._activeTreeId && window._activeTreeId() === "explorer-tree-wiring") ? "wiring" : "state";
        return open({tab: tab, path: "", back: document.getElementById("explorer-json-whole")});
    }

    window.TreeJsonEdit = {
        open: open, openWhole: openWhole, close: close, save: save,
        _parseError: parseError, _lineOf: lineOf, _state: function () { return S; }
    };
})();
