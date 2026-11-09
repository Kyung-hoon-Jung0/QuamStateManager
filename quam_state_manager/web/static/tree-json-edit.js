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

    var RAIL_LIST_CAP = 500;
    var RAIL_LARGE = 2 * 1048576;
    var RAIL_DEBOUNCE_MS = 120;
    var RAIL_DEBOUNCE_LARGE_MS = 400;

    function lineAt(text, off) {
        off = Math.max(0, Math.min(off, text.length));
        return text.slice(0, off).split("\n").length;
    }

    /* spans [{from,to,path}] for valid JSON; invalid text has no spans */
    function jsonSpans(text) {
        var i = 0, n = text.length, spans = [];
        function ws() {
            while (i < n) {
                var c = text[i];
                if (c === "\n" || c === " " || c === "\t" || c === "\r") i++;
                else break;
            }
        }
        function mark(from, to, path) {
            if (path != null && from < to) spans.push({from: from, to: to, path: path});
        }
        function str() {
            var start = i;
            if (text[i] !== '"') return start;
            i++;
            while (i < n && text[i] !== '"') { if (text[i] === "\\") i++; i++; }
            if (i < n) i++;
            return start;
        }
        function readStr() {
            var start = str();
            try { return JSON.parse(text.slice(start, i)); } catch (e) { return ""; }
        }
        function val(path) {
            ws();
            var start = i, c = text[i];
            if (c === "{") {
                mark(i, i + 1, path); i++; ws();
                if (text[i] === "}") { mark(i, i + 1, path); i++; return; }
                while (i < n) {
                    ws(); var ks = i; var k = readStr();
                    var cp = path ? path + "." + k : String(k);
                    mark(ks, i, cp);
                    ws(); if (text[i] === ":") i++;
                    val(cp); ws();
                    if (text[i] === ",") { i++; continue; }
                    if (text[i] === "}") { mark(i, i + 1, path); i++; return; }
                    return;
                }
            } else if (c === "[") {
                mark(i, i + 1, path); i++; ws();
                if (text[i] === "]") { mark(i, i + 1, path); i++; return; }
                for (var idx = 0; i < n; idx++) {
                    var ip = path ? path + "." + idx : String(idx);
                    val(ip); ws();
                    if (text[i] === ",") { i++; continue; }
                    if (text[i] === "]") { mark(i, i + 1, path); i++; return; }
                    return;
                }
            } else if (c === '"') { str(); mark(start, i, path); }
            else { while (i < n && ",]}\n\r\t ".indexOf(text[i]) < 0) i++; mark(start, i, path); }
        }
        try {
            JSON.parse(text);
            val("");
        } catch (e) { return []; }
        return spans;
    }

    function pathAtOffset(text, offset, base) {
        offset = Math.max(0, Math.min(offset, text.length));
        return indexedPath(jsonSpans(text), offset, base) || "line " + lineAt(text, offset);
    }

    function indexedPath(spans, offset, base) {
        var lo = 0, hi = spans.length - 1, hit;
        while (lo <= hi) {
            var mid = (lo + hi) >>> 1, s = spans[mid];
            if (offset < s.from) hi = mid - 1;
            else if (offset >= s.to) lo = mid + 1;
            else { hit = s; break; }
        }
        if (!hit) return "";
        var p = hit.path;
        if (base) p = p ? (base + "." + p) : base;
        return p;
    }

    function readSearchSpec(host) {
        var root = host && host.querySelector ? host.querySelector(".cm-search") : null;
        if (!root) return {query: "", case: false, re: false, word: false};
        var inputs = root.querySelectorAll("input.cm-textfield");
        var q = (inputs[0] && inputs[0].value) || "";
        var caseOn = !!(root.querySelector('input[name="case"]') && root.querySelector('input[name="case"]').checked);
        var reOn = !!(root.querySelector('input[name="re"]') && root.querySelector('input[name="re"]').checked);
        var word = root.querySelector('input[name="word"]');
        return {query: q, case: caseOn, re: reOn, word: !!(word && word.checked)};
    }

    function findMatches(text, spec) {
        var q = spec.query || "";
        if (!q) return [];
        var out = [], from = 0, re, line = 1, scanned = 0;
        function add(at, end) {
            // Advance through the document once, even when it has many hits.
            while (scanned < at) { if (text.charCodeAt(scanned) === 10) line++; scanned++; }
            if (spec.word && (/[\w]/.test(text[at - 1] || "") || /[\w]/.test(text[end] || ""))) return;
            out.push({from: at, to: end, line: line});
        }
        if (spec.re) {
            try {
                re = new RegExp(q, spec.case ? "gm" : "gim");
                var m;
                while ((m = re.exec(text))) {
                    if (!m[0].length) { re.lastIndex++; continue; }
                    add(m.index, m.index + m[0].length);
                }
                return out;
            } catch (e) { return []; }
        }
        // CodeMirror's default (non-literal) search reads \n, \r, \t and \\ as escapes.
        q = q.replace(/\\([nrt\\])/g, function (_, ch) { return ch === "n" ? "\n" : ch === "r" ? "\r" : ch === "t" ? "\t" : "\\"; });
        var needle = spec.case ? q : q.toLowerCase();
        var hay = spec.case ? text : text.toLowerCase();
        while (from <= hay.length) {
            var at = hay.indexOf(needle, from);
            if (at < 0) break;
            add(at, at + q.length);
            from = at + (q.length || 1);
        }
        return out;
    }

    function currentMatchIndex(matches, head) {
        if (!matches.length) return -1;
        for (var j = 0; j < matches.length; j++) {
            if (head >= matches[j].from && head < matches[j].to) return j;
            if (j + 1 < matches.length && head >= matches[j].from && head < matches[j + 1].from) return j;
        }
        if (head > matches[matches.length - 1].from) return matches.length - 1;
        return 0;
    }

    function groupPrefix(path) {
        if (/^line \d+$/.test(path)) return path;
        var parts = path.split(".");
        return parts.length >= 2 ? parts[0] + "." + parts[1] : (parts[0] || path);
    }

    function destroyRail(sess) {
        if (sess && sess.railTimer) { clearTimeout(sess.railTimer); sess.railTimer = null; }
        if (sess && sess.railObs) { try { sess.railObs.disconnect(); } catch (e) { /* */ } }
        if (sess && sess.railInputBound && sess.railHost) {
            sess.railHost.removeEventListener("input", sess.railInputBound, true);
            sess.railHost.removeEventListener("change", sess.railInputBound, true);
            sess.railHost.removeEventListener("keyup", sess.railInputBound, true);
            sess.railHost.removeEventListener("scroll", updateRailViewport, true);
        }
        if (sess && sess.railResize) sess.railResize.disconnect();
        if (sess && sess.railFrame) cancelAnimationFrame(sess.railFrame);
    }

    function scheduleRail() {
        if (!S || !S.rail) return;
        var ms = getText().length > RAIL_LARGE ? RAIL_DEBOUNCE_LARGE_MS : RAIL_DEBOUNCE_MS;
        if (S.railTimer) clearTimeout(S.railTimer);
        S.railTimer = setTimeout(refreshRail, ms);
    }

    function gotoOffset(off, end) {
        if (!S || !S.ed) return;
        var v = S.ed.view;
        try { v.dispatch({selection: end == null ? {anchor: off} : {anchor: off, head: end}, scrollIntoView: true}); } catch (e) { /* harness */ }
        S.ed.focus();
    }

    function refreshRail() {
        if (!S || !S.rail || !S.ed) return;
        var text = getText(), spec = readSearchSpec(S.railHost);
        var key = JSON.stringify(spec), rebuild = S.railText !== text || S.railKey !== key;
        if (rebuild) {
            S.railText = text;
            S.railKey = key;
            S.railMatches = findMatches(text, spec);
            var spans = S.railMatches.length ? jsonSpans(text) : [];
            S.railMatches.forEach(function (m) {
                m.path = indexedPath(spans, m.from, S.path) || "line " + m.line;
            });
        }
        var matches = S.railMatches;
        var head = 0;
        try {
            var selection = S.ed.view.state.selection.main;
            head = selection.from == null ? selection.head : selection.from;
        } catch (e) { /* */ }
        var cur = currentMatchIndex(matches, head);
        S.railCur = cur;
        var count = S.railCount;
        if (matches.length) {
            count.textContent = (cur < 0 ? "1" : String(cur + 1)) + " of " + matches.length;
        } else if (spec.query) count.textContent = "0 of 0";
        else count.textContent = "";
        var total = 1;
        try {
            var doc = S.ed.view.state.doc;
            total = Math.max(1, doc.lines);
        } catch (e) {
            total = Math.max(1, text.split("\n").length);
        }
        var marks = S.railMarks;
        if (rebuild) {
            if (S.railFrame) cancelAnimationFrame(S.railFrame);
            marks.innerHTML = "";
            var sess = S, next = 0;
            function appendMarks() {
                if (S !== sess) return;
                var frag = document.createDocumentFragment(), end = Math.min(next + 300, matches.length);
                for (; next < end; next++) {
                    var m = matches[next], idx = next;
                    var b = el("button", "ui-jedit-rail-mark" + (idx === sess.railCur ? " ui-jedit-rail-mark-cur" : ""));
                    b.type = "button";
                    b.style.top = ((m.line - 0.5) / total * 100) + "%";
                    b.title = m.path;
                    b.setAttribute("aria-label", m.path);
                    b.setAttribute("data-from", String(m.from));
                    b.setAttribute("data-to", String(m.to));
                    b.addEventListener("click", function (e) { gotoOffset(+e.currentTarget.getAttribute("data-from"), +e.currentTarget.getAttribute("data-to")); refreshRail(); });
                    frag.appendChild(b);
                }
                marks.appendChild(frag);
                if (next < matches.length) sess.railFrame = requestAnimationFrame(appendMarks);
            }
            appendMarks();
        } else {
            var prev = marks.querySelector(".ui-jedit-rail-mark-cur");
            if (prev) prev.classList.remove("ui-jedit-rail-mark-cur");
            if (marks.children[cur]) marks.children[cur].classList.add("ui-jedit-rail-mark-cur");
        }
        updateRailViewport();
        if (!rebuild && S.railRenderedListOn === S.railListOn) return;
        S.railRenderedListOn = S.railListOn;
        var list = S.railList;
        list.innerHTML = "";
        list.classList.toggle("ui-jedit-rail-list-on", S.railListOn && !!matches.length);
        if (!S.railListOn || !matches.length) { list.hidden = true; return; }
        list.hidden = false;
        var groups = Object.create(null), order = [];
        matches.forEach(function (m) {
            var g = groupPrefix(m.path);
            if (!groups[g]) { groups[g] = []; order.push(g); }
            groups[g].push(m);
        });
        var shown = 0;
        order.forEach(function (g) {
            if (shown >= RAIL_LIST_CAP) return;
            var group = groups[g];
            list.appendChild(el("div", "ui-jedit-rail-group", g + " (" + group.length + ")"));
            group.forEach(function (m) {
                if (shown >= RAIL_LIST_CAP) return;
                var row = btn("ui-jedit-rail-list-row btn-xs outline", m.path, function () { gotoOffset(m.from, m.to); refreshRail(); });
                row.title = m.path;
                list.appendChild(row);
                shown++;
            });
        });
        if (matches.length > shown) list.appendChild(el("div", "ui-jedit-rail-list-more", (matches.length - shown) + " more not shown"));
    }

    function updateRailViewport() {
        if (!S || !S.rail) return;
        var sc = S.railHost.querySelector(".cm-scroller") || S.railHost;
        var vp = S.railViewport;
        if (sc && sc.scrollHeight > sc.clientHeight + 2) {
            var top = sc.scrollTop / sc.scrollHeight * 100;
            var ht = (sc.clientHeight / sc.scrollHeight) * 100;
            vp.style.display = "";
            vp.style.top = top + "%";
            vp.style.height = Math.max(2, ht) + "%";
        } else {
            vp.style.top = "0%";
            vp.style.height = "100%";
        }
    }

    function mountSearchRail(sess) {
        destroyRail(sess);
        var wrap = el("div", "ui-jedit-body");
        var host = sess.railHost = sess.panel.querySelector(".ui-jedit-host");
        host.parentNode.insertBefore(wrap, host);
        wrap.appendChild(host);
        var rail = el("aside", "ui-jedit-rail");
        rail.setAttribute("aria-label", "Search overview");
        var head = el("div", "ui-jedit-rail-head");
        var count = el("span", "ui-jedit-rail-count");
        var listBtn = btn("btn-xs outline ui-jedit-rail-listbtn", "List", function () {
            sess.railListOn = !sess.railListOn;
            listBtn.setAttribute("aria-pressed", String(sess.railListOn));
            rail.classList.toggle("ui-jedit-rail-expanded", sess.railListOn);
            listBtn.classList.toggle("ui-jedit-rail-listbtn-on", sess.railListOn);
            refreshRail();
        });
        listBtn.setAttribute("aria-pressed", "false");
        head.appendChild(count);
        head.appendChild(listBtn);
        var track = el("div", "ui-jedit-rail-track");
        var vp = el("div", "ui-jedit-rail-viewport");
        var marks = el("div", "ui-jedit-rail-marks");
        var tooltip = el("div", "ui-jedit-rail-tooltip");
        tooltip.setAttribute("role", "tooltip");
        function showPath(e) {
            var mark = e.target.closest(".ui-jedit-rail-mark");
            if (!mark) { hidePath(); return; }
            tooltip.textContent = mark.title;
            tooltip.style.top = (mark.getBoundingClientRect().top - rail.getBoundingClientRect().top) + "px";
            tooltip.classList.add("ui-jedit-rail-tooltip-on");
        }
        function hidePath() { tooltip.classList.remove("ui-jedit-rail-tooltip-on"); }
        marks.addEventListener("pointerover", showPath);
        marks.addEventListener("pointermove", showPath);
        marks.addEventListener("focusin", showPath);
        marks.addEventListener("pointerleave", hidePath);
        marks.addEventListener("focusout", hidePath);
        track.appendChild(vp);
        track.appendChild(marks);
        var list = el("div", "ui-jedit-rail-list");
        list.hidden = true;
        rail.appendChild(head);
        rail.appendChild(track);
        rail.appendChild(list);
        rail.appendChild(tooltip);
        wrap.appendChild(rail);
        sess.rail = rail;
        sess.railCount = count;
        sess.railMarks = marks;
        sess.railViewport = vp;
        sess.railList = list;
        sess.railListOn = false;
        sess.railInputBound = function () { scheduleRail(); };
        host.addEventListener("input", sess.railInputBound, true);
        host.addEventListener("change", sess.railInputBound, true);
        host.addEventListener("keyup", sess.railInputBound, true);
        host.addEventListener("scroll", updateRailViewport, true);
        if (window.ResizeObserver) {
            sess.railResize = new ResizeObserver(updateRailViewport);
            sess.railResize.observe(host);
        }
        if (window.MutationObserver) {
            sess.railObs = new MutationObserver(scheduleRail);
            sess.railObs.observe(host, {childList: true, subtree: true, characterData: true,
                                        attributes: true, attributeFilter: ["class"]});
        }
        scheduleRail();
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
                    expect_chip: window.__chipToken || "", tree_kind_change: true};
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
                var before = JSON.parse(S.initial), after = JSON.parse(text);
                function kind(v) { return v === null ? "null" : Array.isArray(v) ? "list" : typeof v === "object" ? "dict" : typeof v === "number" ? (Number.isInteger(v) ? "int" : "float") : typeof v; }
                var typeNote = kind(before) !== kind(after) ? " \u00b7 type: " + kind(before) + " \u2192 " + kind(after) : "";
                afterWrite(d);
                close(true);
                var warn = (d.warnings || []).map(function (w) { return w.path + " " + w.warning; });
                if (window.showToast) window.showToast((n ? "Saved " + n + " change" + (n === 1 ? "" : "s")
                    + " in one undo step" : "Nothing changed")
                    + typeNote + (warn.length ? ". Warning: " + warn.join("; ") : ""), warn.length ? "warning" : "info");
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
        destroyRail(s);
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
        var railExts = [];
        var EV = window.SmCodeMirror && window.SmCodeMirror.EditorView;
        if (EV) {
            railExts.push(EV.updateListener.of(function (u) {
                if (S && (u.selectionSet || u.docChanged || u.geometryChanged)) scheduleRail();
            }));
        }
        S.ed = window.SmCodeMirror.create(host, {
            doc: d.text, dark: isDark(), lint: true,
            onChange: function () { if (S) { S.ask.hidden = true; lint(); scheduleRail(); } },
            extensions: railExts
        });
        // the find/replace panel is open from the start; focus returns to the text below
        if (S.ed.openSearch) S.ed.openSearch();
        mountSearchRail(S);
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
        _parseError: parseError, _lineOf: lineOf, _pathAtOffset: pathAtOffset,
        _findMatches: findMatches, _refreshRail: refreshRail,
        _state: function () { return S; }
    };
})();
