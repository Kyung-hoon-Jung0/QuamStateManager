/* The top-bar health snapshot expires even when SM cannot answer another GET. */
(function () {
    "use strict";
    function unknown(node, reason) {
        node.setAttribute("data-health", "grey");
        node.classList.add("opxmon-health-dot--grey");
        node.textContent = "\u25cf OPX unknown: " + reason;
        var scope = node.closest("#opxmon-body, #opxmon-alarm-slot");
        if (scope) scope.querySelectorAll(".opxmon-alarms, .opxmon-alarm-badge").forEach(function (alarm) { alarm.hidden = true; });
    }
    function watch() {
        document.querySelectorAll(".opxmon-health[data-valid-ms]").forEach(function (node) {
            if (node.__opxWatching) return;
            node.__opxWatching = true;
            var ms = Number(node.getAttribute("data-valid-ms"));
            if (!Number.isFinite(ms) || ms <= 0) {
                if (node.getAttribute("data-health") !== "grey") unknown(node, "data freshness expired; awaiting a new answer");
                return;
            }
            window.setTimeout(function () {
                if (node.isConnected) unknown(node, "data freshness expired; awaiting a new answer");
            }, ms);
        });
    }
    function failed(event) {
        var elt = event.detail && event.detail.elt;
        if (!elt || (elt.id !== "opxmon-body" && elt.id !== "opxmon-alarm-slot")) return;
        document.querySelectorAll(".opxmon-health").forEach(function (node) { unknown(node, "SM did not answer the refresh"); });
    }
    document.addEventListener("DOMContentLoaded", watch);
    document.addEventListener("htmx:afterSwap", watch);
    ["htmx:sendError", "htmx:responseError", "htmx:timeout"].forEach(function (event) { document.addEventListener(event, failed); });
    window.OpxHealth = {watch: watch, failed: failed};
    watch();
})();
