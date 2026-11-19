/* The top-bar health snapshot and the page's verdict band expire even when SM cannot answer another GET. */
(function () {
    "use strict";
    var TONES = ["ui-verdict--ok", "ui-verdict--warn", "ui-verdict--danger", "ui-verdict--note"];
    function setStatus(line, state) {
        ["ok", "stale", "error", "starting", "unconfigured"].forEach(function (s) { line.classList.remove("ui-opxmon-status--" + s); });
        line.classList.add("ui-opxmon-status--" + state);
    }
    function unknown(node, reason) {
        node.setAttribute("data-health", "grey");
        node.classList.remove("ui-opxmon-verdict--red");
        if (node.classList.contains("ui-verdict")) {
            /* the band: grey, says unknown, keeps its parts */
            TONES.forEach(function (t) { node.classList.remove(t); });
            node.classList.add("ui-verdict--neutral");
            node.setAttribute("role", "status");
            var key = node.querySelector(".ui-verdict-key");
            var why = node.querySelector(".ui-verdict-why");
            if (key) key.textContent = "UNKNOWN";
            if (why) why.textContent = "OPX unknown: " + reason + ". The values below may be old.";
        } else {
            node.classList.add("ui-opxmon-health-dot--grey");
            /* the top bar stays short; the reason goes to the tooltip */
            node.textContent = String.fromCharCode(9679) + " OPX unknown";
            node.title = "OPX unknown: " + reason;
        }
        var scope = node.closest("#ui-opxmon-body, #ui-opxmon-alarm-slot");
        if (scope && scope.id === "ui-opxmon-body") {
            scope.setAttribute("data-state", "stale");
            var sentence = scope.querySelector(".ui-opxmon-sentence");
            if (sentence) {
                sentence.textContent = "No fresh answer; " + reason;
                sentence.title = sentence.textContent;
                sentence.setAttribute("data-state", "stale");
            }
            var line = scope.querySelector(".ui-opxmon-status");
            if (line) setStatus(line, "stale");
            scope.querySelectorAll(".ui-opxmon-close, .ui-opxmon-close-all, .ui-opxmon-close-idle, .ui-opxmon-confirm-accept").forEach(function (button) { button.disabled = true; });
        }
        if (scope) scope.querySelectorAll(".ui-opxmon-alarms, .ui-opxmon-alarm-badge").forEach(function (alarm) { alarm.hidden = true; });
    }
    var WATCHED = ".ui-opxmon-health[data-valid-ms], .ui-opxmon-verdict[data-valid-ms]";
    function watch() {
        document.querySelectorAll(WATCHED).forEach(function (node) {
            if (node.__opxWatching) return;
            node.__opxWatching = true;
            var ms = Number(node.getAttribute("data-valid-ms"));
            if (!Number.isFinite(ms) || ms <= 0) {
                return;
            }
            window.setTimeout(function () {
                if (node.isConnected) unknown(node, "data freshness expired; awaiting a new answer");
            }, ms);
        });
    }
    function failed(event) {
        var elt = event.detail && event.detail.elt;
        if (!elt || ["ui-opxmon-body", "ui-opxmon-band", "ui-opxmon-alarm-slot"].indexOf(elt.id) < 0) return;
        document.querySelectorAll(".ui-opxmon-health, .ui-opxmon-verdict[data-valid-ms]").forEach(function (node) { unknown(node, "SM did not answer the refresh"); });
    }
    document.addEventListener("DOMContentLoaded", watch);
    document.addEventListener("htmx:afterSwap", watch);
    ["htmx:sendError", "htmx:responseError", "htmx:timeout"].forEach(function (event) { document.addEventListener(event, failed); });
    window.OpxHealth = {watch: watch, failed: failed, setStatus: setStatus};
    watch();
})();
