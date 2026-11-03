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

    function onClick(e) {
        var btn = e.target && e.target.closest && e.target.closest(".ui-opxmon-close, .ui-opxmon-close-all, .ui-opxmon-close-idle");
        if (!btn || btn.disabled) return;
        e.preventDefault();
        var existing = document.querySelector('.ui-opxmon-confirm');
        if (existing) existing.remove();
        var panel = document.createElement('section');
        panel.className = 'ui-opxmon-confirm ui-state';
        panel.setAttribute('role', 'region');
        panel.setAttribute('aria-label', 'Confirm close');
        var message = document.createElement('p');
        message.textContent = confirmText(btn);
        panel.appendChild(message);
        var ids = (btn.getAttribute('data-qm-ids') || btn.getAttribute('data-qm-id') || '').split(',').map(function (id) { return id.trim(); }).filter(Boolean);
        ids.forEach(function (id) {
            var row = Array.from(document.querySelectorAll('.ui-opxmon-close')).find(function (b) {return b.getAttribute('data-qm-id') === id;});
            var detail = document.createElement('p');
            detail.textContent = id + ': ' + (row ? (row.getAttribute('data-basis') || 'activity unknown') + '; FEMs ' + (row.getAttribute('data-ports') || 'unknown') + '; last job ' + (row.getAttribute('data-last-job') || row.getAttribute('data-jobs') || 'none') : 'activity, FEMs and last job unknown');
            panel.appendChild(detail);
        });
        var typed = null;
        if (btn.classList.contains('ui-opxmon-close-all')) {
            var label = document.createElement('label');
            label.textContent = 'Type the count (' + ids.length + ') to close all';
            typed = document.createElement('input');
            typed.className = 'ui-opxmon-confirm-count';
            label.appendChild(typed); panel.appendChild(label);
        }
        var accept = document.createElement('button');
        accept.type = 'button'; accept.className = 'btn-sm ui-opxmon-confirm-accept'; accept.textContent = 'Confirm close';
        var cancel = document.createElement('button');
        cancel.type = 'button'; cancel.className = 'btn-sm outline ui-opxmon-confirm-cancel'; cancel.textContent = 'Cancel';
        cancel.onclick = function () { panel.remove(); };
        accept.onclick = function () {
            if (typed && typed.value !== String(ids.length)) { say("Nothing closed. Type " + ids.length + " to confirm closing all listed QMs.", true); typed.setAttribute("aria-invalid", "true"); typed.focus(); return; }
            accept.disabled = true;
            submitClose(btn, typed ? typed.value : null);
            panel.remove();
        };
        panel.appendChild(accept); panel.appendChild(cancel);
        var anchor = btn.closest('table') || btn.closest('p') || btn;
        anchor.insertAdjacentElement('afterend', panel);
        accept.focus();
    }

    function submitClose(btn, count) {
        var fd = new FormData();
        if (btn.classList.contains("ui-opxmon-close-all") || btn.classList.contains("ui-opxmon-close-idle")) {
            fd.append(btn.classList.contains("ui-opxmon-close-idle") ? "idle" : "all", "1");
            fd.append("qm_ids", btn.getAttribute("data-qm-ids") || "");
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
        if (line) line.className = "ui-opxmon-status ui-opxmon-status--error";
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
    window.OpxMonitor = {confirmText: confirmText, onClick: onClick, markUnreachable: markUnreachable, syncNav: syncNav};
})();
