/* OPX monitor page (page bundle 'opxmon').
   - Clean up: every close asks first, naming the QM, the FEMs it claims and
     its jobs; the answer is shown in place and the body re-fetches.
   - A poll that SM itself does not answer marks the status line, so a page
     whose server went away never keeps showing "Updated 2 s ago". */
(function () {
    "use strict";

    var lastResult = null;

    function body() { return document.getElementById("ui-opxmon-body"); }

    function confirmText(btn) {
        return (btn.classList.contains("ui-opxmon-close-all") ? "Close ALL listed QMs?" :
            btn.classList.contains("ui-opxmon-close-idle") ? "Close idle QMs (activity re-checked)?" : "Close this QM?") +
            " Any running job will stop.";
    }

    function say(text, bad) {
        lastResult = {text: text, bad: bad};
        var b = body();
        var el = b && b.querySelector(".ui-opxmon-close-result");
        if (!el) return;
        el.hidden = false;
        el.textContent = text;
        el.classList.toggle("ui-state", !!bad);
        el.classList.toggle("ui-state--error", !!bad);
    }

    function refresh() {
        var b = body();
        if (b && window.htmx && b.getAttribute("hx-get")) {
            window.htmx.ajax("GET", b.getAttribute("hx-get"), {target: b, swap: "outerHTML"});
        }
    }

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }

    function kindOf(btn) {
        return btn.classList.contains("ui-opxmon-close-all") ? "all" : btn.classList.contains("ui-opxmon-close-idle") ? "idle" : "one";
    }

    /* the button a confirm belongs to, found again in a re-drawn body */
    function findButton(kind, id) {
        var b = body();
        if (!b) return null;
        if (kind === "all") return b.querySelector(".ui-opxmon-close-all");
        if (kind === "idle") return b.querySelector(".ui-opxmon-close-idle");
        return Array.from(b.querySelectorAll(".ui-opxmon-close")).find(function (x) { return x.getAttribute("data-qm-id") === id; }) || null;
    }

    function fact(dl, label, value, note) {
        var row = el("div", "ui-facts-row");
        row.appendChild(el("dt", "ui-facts-label", label));
        var v = el("dd", "ui-facts-value", value);
        if (note) v.appendChild(el("span", "ui-facts-note", note));
        row.appendChild(v);
        dl.appendChild(row);
    }

    var openConfirm = null;   // {kind, id, ids, typed}: survives the periodic refresh

    function idsOf(btn) {
        return (btn.getAttribute("data-qm-ids") || btn.getAttribute("data-qm-id") || "").split(",").map(function (id) { return id.trim(); }).filter(Boolean);
    }
    /* the set a confirm was drawn for, in one comparable form */
    function setKey(ids) { return ids.slice().sort().join("\n"); }

    function closeConfirm() {
        var existing = document.querySelector(".ui-opxmon-confirm");
        if (existing) {
            var sub = existing.closest(".ui-table-sub");
            (sub || existing).remove();
        }
        openConfirm = null;
    }

    function openPanel(btn, typedValue, focusTyped) {
        var existing = document.querySelector(".ui-opxmon-confirm");
        if (existing) { var sub0 = existing.closest(".ui-table-sub"); (sub0 || existing).remove(); }
        var kind = kindOf(btn);
        var panel = el("div", "ui-callout ui-callout--warn ui-opxmon-confirm");
        panel.setAttribute("role", "region");
        panel.setAttribute("aria-label", "Confirm close");
        var ids = idsOf(btn);
        panel.appendChild(el("div", "ui-callout-title", kind === "all" ? "Close ALL " + ids.length + " listed QMs?" :
            kind === "idle" ? "Close " + ids.length + " idle QM" + (ids.length === 1 ? "" : "s") + " (activity re-checked)?" : "Close " + (ids[0] || "this QM") + "?"));
        panel.appendChild(el("p", "ui-callout-text", "Any running job will stop."));
        var dl = el("dl", "ui-facts");
        dl.setAttribute("aria-label", "What will be closed");
        var rowOf = function (id) { return Array.from(document.querySelectorAll(".ui-opxmon-close")).find(function (b) { return b.getAttribute("data-qm-id") === id; }); };
        if (kind === "one") {
            var r = rowOf(ids[0]) || btn;
            fact(dl, "State", r.getAttribute("data-basis") || "activity unknown");
            fact(dl, "FEMs", r.getAttribute("data-ports") || "unknown");
            fact(dl, "Last job", r.getAttribute("data-last-job") || r.getAttribute("data-jobs") || "none");
            if (r.getAttribute("data-owner")) fact(dl, "Owner", r.getAttribute("data-owner"));
        } else {
            ids.forEach(function (id) {
                var row = rowOf(id);
                if (!row) { fact(dl, id, "activity, FEMs and last job unknown"); return; }
                fact(dl, id, row.getAttribute("data-basis") || "activity unknown",
                     "FEMs " + (row.getAttribute("data-ports") || "unknown") + "; last job " + (row.getAttribute("data-last-job") || row.getAttribute("data-jobs") || "none") +
                     (row.getAttribute("data-owner") ? "; " + row.getAttribute("data-owner") : ""));
            });
        }
        var bodyEl = el("div", "ui-callout-body");
        bodyEl.appendChild(dl);
        var typed = null;
        if (kind === "all") {
            var label = el("label", "ui-field-label", "Type the count (" + ids.length + ") to close all ");
            typed = el("input", "ui-input ui-input--num ui-opxmon-confirm-count");
            if (typedValue) typed.value = typedValue;
            typed.addEventListener("input", function () { if (openConfirm) openConfirm.typed = typed.value; });
            label.appendChild(typed);
            bodyEl.appendChild(label);
        }
        panel.appendChild(bodyEl);
        var acts = el("div", "ui-actions");
        acts.setAttribute("role", "group");
        var accept = el("button", "btn-sm ui-opxmon-confirm-accept", "Confirm close");
        accept.type = "button";
        var cancel = el("button", "btn-sm outline ui-opxmon-confirm-cancel", "Cancel");
        cancel.type = "button";
        cancel.onclick = function () { closeConfirm(); };
        accept.onclick = function () {
            if (typed && typed.value !== String(ids.length)) { say("Nothing closed. Type " + ids.length + " to confirm closing all listed QMs.", true); typed.setAttribute("aria-invalid", "true"); typed.focus(); return; }
            accept.disabled = true;
            var current = findButton(kind, ids[0]) || btn;
            closeConfirm();
            submitClose(current, typed ? typed.value : null, ids);
        };
        acts.appendChild(accept); acts.appendChild(cancel);
        panel.appendChild(acts);
        var tr = kind === "one" && btn.closest("tr");
        if (tr) {
            var sub = el("tr", "ui-table-sub");
            var td = el("td", "ui-table-cell");
            td.setAttribute("colspan", String(tr.children.length || 1));
            td.appendChild(panel);
            sub.appendChild(td);
            tr.insertAdjacentElement("afterend", sub);
        } else {
            (btn.closest("p") || btn).insertAdjacentElement("afterend", panel);
        }
        openConfirm = {kind: kind, id: ids[0] || null, ids: setKey(ids), typed: typed ? typed.value : null};
        if (typed && focusTyped) typed.focus(); else accept.focus();
    }

    function onClick(e) {
        var btn = e.target && e.target.closest && e.target.closest(".ui-opxmon-close, .ui-opxmon-close-all, .ui-opxmon-close-idle");
        if (!btn || btn.disabled) return;
        e.preventDefault();
        openPanel(btn, null, false);
    }

    /* The 5 s refresh re-draws the body; an open confirm and the folds the user opened come back,
       built from the fresh rows, and the scroll position stays where it was. */
    var folds = {};
    function rememberFold(event) {
        var d = event.target;
        if (d && d.getAttribute && d.hasAttribute("data-fold") && d.closest("#ui-opxmon-body")) folds[d.getAttribute("data-fold")] = d.open;
    }
    var keptScroll = null, typedFocused = false;
    function beforeSwap(event) {
        var t = event.detail && event.detail.target;
        if (!t || t.id !== "ui-opxmon-body") return;
        var pane = t.closest("#table-pane") || document.scrollingElement;
        keptScroll = pane ? {el: pane, top: pane.scrollTop} : null;
        var typed = t.querySelector(".ui-opxmon-confirm-count");
        typedFocused = !!(typed && document.activeElement === typed);
        if (typed && openConfirm) openConfirm.typed = typed.value;
    }
    function afterSwap() {
        var b = body();
        if (!b) return;
        b.querySelectorAll("details[data-fold]").forEach(function (d) {
            var k = d.getAttribute("data-fold");
            if (Object.prototype.hasOwnProperty.call(folds, k) && d.open !== folds[k]) d.open = folds[k];
        });
        if (openConfirm && !b.querySelector(".ui-opxmon-confirm")) {
            var want = openConfirm;
            var btn = findButton(want.kind, want.id);
            if (btn && !btn.disabled && want.kind !== "one" && setKey(idsOf(btn)) !== want.ids) {
                /* the consent was for the set the user saw: a different set asks again from the start */
                openConfirm = null;
                say("The list changed; check it again. Nothing was closed.", true);
            } else if (btn && !btn.disabled) {
                if (want.kind === "all") { var dz = btn.closest("details"); if (dz) dz.open = true; }
                openPanel(btn, want.typed, typedFocused);
            } else {
                openConfirm = null;
                say("The close question was withdrawn: " + (want.kind === "one" ? want.id + " is no longer listed" : "the list changed") + " or the data is not fresh. Nothing was closed.", true);
            }
        }
        if (keptScroll && keptScroll.el && keptScroll.el.isConnected !== false) keptScroll.el.scrollTop = keptScroll.top;
        keptScroll = null;
    }

    /* ``seen``: the ids the confirm listed; the server refuses them when its list differs */
    function submitClose(btn, count, seen) {
        var fd = new FormData();
        if (btn.classList.contains("ui-opxmon-close-all") || btn.classList.contains("ui-opxmon-close-idle")) {
            fd.append(btn.classList.contains("ui-opxmon-close-idle") ? "idle" : "all", "1");
            fd.append("qm_ids", (seen || idsOf(btn)).join(", "));
        }
        else fd.append("qm_id", btn.getAttribute("data-qm-id"));
        if (count !== null) fd.append('confirm_count', count);
        try {
            var who = window.localStorage && window.localStorage.getItem("quam_actor_name");
            if (who) fd.append("by", who);
        } catch (err) { /* storage blocked: no name */ }
        btn.disabled = true;
        return window.fetch("/opx-monitor/close", {method: "POST", body: fd, credentials: "same-origin"})
            .then(function (r) { return r.json().catch(function () { return {ok: false, error: "HTTP " + r.status}; }); })
            .then(function (j) {
                if (j && j.ok) say("Closed: " + ((j.closed || []).join(", ") || "none") +
                    ((j.skipped && j.skipped.length) ? " (skipped: " + j.skipped.map(function (id) { return id + ": " + ((j.skip_reasons || {})[id] || "already gone"); }).join(", ") + ")" : "") +
                    ". Written to History.", false);
                else say(((j && j.closed && j.closed.length) ? "Closed: " + j.closed.join(", ") + ". " : "") + "Not closed: " + ((j && j.error) || "no reason given"), true);
                refresh();
            }, function (err) {
                say("Not closed: SM did not answer (" + err + ").", true);
                refresh();
            });
    }

    function markUnreachable(e) {
        var t = e && e.detail && e.detail.elt;
        if (!t || t.id !== "ui-opxmon-body") return;
        var s = t.querySelector(".ui-opxmon-sentence");
        var line = t.querySelector(".ui-opxmon-status");
        if (s) {
            s.textContent = "SM did not answer the last refresh; the values below may be old.";
            s.title = s.textContent;
            s.setAttribute("data-state", "error");
        }
        if (line) {
            ["ok", "stale", "starting", "unconfigured"].forEach(function (x) { line.classList.remove("ui-opxmon-status--" + x); });
            line.classList.add("ui-opxmon-status--error");
        }
        t.setAttribute("data-state", "error");
    }

    /* the sidebar's sub-links follow the tab shown (a tab click swaps only the pane) */
    var observedBody = null, tableObserver = null;
    function syncTables() {
        var b = body();
        if (!b) return;
        b.querySelectorAll('.ui-opxmon-table').forEach(function (table) {
            table.classList.toggle('ui-opxmon-table--stacked', b.clientWidth <= 768);
        });
        if (observedBody !== b && window.ResizeObserver) {
            if (tableObserver) tableObserver.disconnect();
            observedBody = b;
            tableObserver = new window.ResizeObserver(syncTables);
            tableObserver.observe(b);
        }
    }

    function syncNav() {
        var b = body();
        var nav = document.getElementById("ui-opxmon-subnav");
        if (!b || !nav) return;
        var tab = b.getAttribute("data-tab");
        nav.querySelectorAll("a").forEach(function (a) {
            a.classList.toggle("active", a.getAttribute("href") === "/opx-monitor/" + tab);
        });
    }

    document.addEventListener("click", onClick);
    // Stage 0 uses a modifier instead of an attribute selector for expanded tiles.
    document.addEventListener("toggle", function (event) {
        var tile = event.target;
        if (tile.classList && tile.classList.contains("ui-opxmon-tile")) {
            tile.classList.toggle("ui-opxmon-tile--open", tile.open);
        }
    }, true);
    document.addEventListener("toggle", rememberFold, true);
    document.addEventListener("htmx:beforeSwap", beforeSwap);
    document.addEventListener("htmx:afterSwap", afterSwap);
    document.addEventListener("htmx:afterSwap", syncNav);
    document.addEventListener("htmx:afterSwap", syncTables);
    document.addEventListener("DOMContentLoaded", syncTables);
    window.addEventListener("resize", syncTables);
    syncTables();
    document.addEventListener("htmx:afterSwap", function () {
        if (lastResult) say(lastResult.text, lastResult.bad);
    });
    document.addEventListener("htmx:sendError", markUnreachable);
    document.addEventListener("htmx:responseError", markUnreachable);
    window.OpxMonitor = {confirmText: confirmText, onClick: onClick, afterSwap: afterSwap, beforeSwap: beforeSwap, markUnreachable: markUnreachable, syncNav: syncNav};
})();
