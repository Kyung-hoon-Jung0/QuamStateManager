/* OPX monitor page (page bundle 'opxmon').
   - Clean up: every close asks first, naming the QM, the FEMs it claims and
     its jobs; the answer is shown in place and the body re-fetches.
   - A poll that SM itself does not answer marks the status line, so a page
     whose server went away never keeps showing "Updated 2 s ago". */
(function () {
    "use strict";

    var lastResult = null;

    function body() { return document.getElementById("opxmon-body"); }

    function confirmText(btn) {
        if (btn.classList.contains("opxmon-close-all") || btn.classList.contains("opxmon-close-idle")) {
            var ids = btn.getAttribute("data-qm-ids") || "";
            return (btn.classList.contains("opxmon-close-idle") ? "Close idle quantum machines (activity re-checked before closing)?\n\n" : "Close ALL open quantum machines?\n\n") + ids.split(", ").join("\n") +
                "\n\nAny job they run stops, for whoever opened them.";
        }
        var id = btn.getAttribute("data-qm-id");
        var ports = btn.getAttribute("data-ports") || "unknown";
        var jobs = btn.getAttribute("data-jobs") || "none";
        return "Close quantum machine " + id + "?\n\nFEMs claimed: " + ports +
            "\nJobs: " + jobs + "\n\nAny job it runs stops, for whoever opened it.";
    }

    function say(text, bad) {
        lastResult = {text: text, bad: bad};
        var b = body();
        var el = b && b.querySelector(".opxmon-close-result");
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
        var btn = e.target && e.target.closest && e.target.closest(".opxmon-close, .opxmon-close-all, .opxmon-close-idle");
        if (!btn) return;
        e.preventDefault();
        if (!window.confirm(confirmText(btn))) return;
        var fd = new FormData();
        if (btn.classList.contains("opxmon-close-all") || btn.classList.contains("opxmon-close-idle")) {
            fd.append(btn.classList.contains("opxmon-close-idle") ? "idle" : "all", "1");
            fd.append("qm_ids", btn.getAttribute("data-qm-ids") || "");
        }
        else fd.append("qm_id", btn.getAttribute("data-qm-id"));
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
                else say("Not closed: " + ((j && j.error) || "no reason given"), true);
                refresh();
            }, function (err) {
                say("Not closed: SM did not answer (" + err + ").", true);
                btn.disabled = false;
            });
    }

    function markUnreachable(e) {
        var t = e && e.detail && e.detail.elt;
        if (!t || t.id !== "opxmon-body") return;
        var s = t.querySelector(".opxmon-sentence");
        var line = t.querySelector(".opxmon-status");
        if (s) s.textContent = "SM did not answer the last refresh; the values below may be old.";
        if (line) line.className = "opxmon-status opxmon-status--error";
        t.setAttribute("data-state", "error");
    }

    /* the sidebar's sub-links follow the tab shown (a tab click swaps only the pane) */
    function syncNav() {
        var b = body();
        var nav = document.getElementById("opxmon-subnav");
        if (!b || !nav) return;
        var tab = b.getAttribute("data-tab");
        nav.querySelectorAll("a").forEach(function (a) {
            a.classList.toggle("active", a.getAttribute("href") === "/opx-monitor/" + tab);
        });
    }

    document.addEventListener("click", onClick);
    document.addEventListener("htmx:afterSwap", syncNav);
    document.addEventListener("htmx:afterSwap", function () {
        if (lastResult) say(lastResult.text, lastResult.bad);
    });
    document.addEventListener("htmx:sendError", markUnreachable);
    document.addEventListener("htmx:responseError", markUnreachable);
    window.OpxMonitor = {confirmText: confirmText, onClick: onClick, markUnreachable: markUnreachable, syncNav: syncNav};
})();
