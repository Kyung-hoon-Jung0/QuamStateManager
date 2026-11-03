/* The top-bar health snapshot expires even when SM cannot answer another GET. */
(function () {
    "use strict";
    function unknown(node, reason) {
        node.setAttribute("data-health", "grey");
        node.classList.remove("ui-opxmon-verdict--red");
        node.classList.add("ui-opxmon-health-dot--grey");
        node.textContent = "\u25cf OPX unknown: " + reason;
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
            if (line) line.className = "ui-opxmon-status ui-opxmon-status--stale";
            scope.querySelectorAll(".ui-opxmon-close, .ui-opxmon-close-all, .ui-opxmon-close-idle, .ui-opxmon-confirm-accept").forEach(function (button) { button.disabled = true; });
        }
        if (scope) scope.querySelectorAll(".ui-opxmon-alarms, .ui-opxmon-alarm-badge").forEach(function (alarm) { alarm.hidden = true; });
    }
    function watch() {
        document.querySelectorAll(".ui-opxmon-health[data-valid-ms]").forEach(function (node) {
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
        if (!elt || (elt.id !== "ui-opxmon-body" && elt.id !== "ui-opxmon-alarm-slot")) return;
        document.querySelectorAll(".ui-opxmon-health").forEach(function (node) { unknown(node, "SM did not answer the refresh"); });
    }
    document.addEventListener("DOMContentLoaded", watch);
    document.addEventListener("htmx:afterSwap", watch);
    ["htmx:sendError", "htmx:responseError", "htmx:timeout"].forEach(function (event) { document.addEventListener(event, failed); });
    window.OpxHealth = {watch: watch, failed: failed};
    watch();
})();
