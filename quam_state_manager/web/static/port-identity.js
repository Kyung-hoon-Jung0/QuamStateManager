/* One explicit identity offer across field, batch and type-conversion callers. */
(function () {
    "use strict";
    var original = window.fetch;
    function choose(offer) {
        return new Promise(function (resolve) {
            var dialog = document.createElement("dialog");
            var message = document.createElement("p");
            message.textContent = (Array.isArray(offer) ? offer : [offer]).map(function (row) { return row.message; }).join("\n\n");
            message.style.whiteSpace = "pre-wrap";
            dialog.appendChild(message);
            [["Fix other side too", "fix"], ["Keep as typed", "keep"], ["Cancel", "cancel"]]
                .forEach(function (choice) {
                    var button = document.createElement("button");
                    button.type = "button";
                    button.textContent = choice[0];
                    button.onclick = function () { dialog.close(); dialog.remove(); resolve(choice[1]); };
                    dialog.appendChild(button);
                });
            dialog.addEventListener("cancel", function (event) {
                event.preventDefault(); dialog.close(); dialog.remove(); resolve("cancel");
            });
            document.body.appendChild(dialog);
            dialog.showModal();
        });
    }
    // Legacy inspector forms use HTMX's XHR transport instead of fetch.
    document.addEventListener("htmx:beforeSwap", function (event) {
        var detail = event.detail || {};
        if (!detail.xhr || detail.xhr.status !== 409) return;
        var data;
        try { data = JSON.parse(detail.xhr.responseText); } catch (_) { return; }
        if (!data.port_identity) return;
        detail.shouldSwap = false;
        detail.isError = false;
        var config = detail.requestConfig;
        if (!config || !window.htmx) return;
        choose(data.port_identity).then(function (mode) {
            if (mode === "cancel") return;
            var values = Object.assign({}, config.parameters, {port_ack: mode});
            window.htmx.ajax("POST", config.path, {
                source: config.elt, target: detail.target,
                values: values, headers: config.headers
            }).then(function () {
                if (mode === "fix") window.location.reload();
            });
        });
    });
    window.fetch = function (input, init) {
        var url;
        try { url = new URL(input.url || input, window.location.href); }
        catch (_) { return original.apply(this, arguments); }
        if (url.origin !== window.location.origin || (!["/field/edit", "/field/edit-batch", "/type-fix/apply", "/field/create", "/pulse/edit"].includes(url.pathname) && !/^\/(qubit|pair)\/[^/]+\/edit$/.test(url.pathname)) || !init ||
                String(init.method).toUpperCase() !== "POST") return original.apply(this, arguments);
        return original(input, init).then(function (response) {
            if (response.status !== 409) return response;
            return response.clone().json().then(function (data) {
                if (!data.port_identity) return response;
                return choose(data.port_identity).then(function (mode) {
                    if (mode === "cancel") return response;
                    var headers = new Headers(init.headers || {});
                    var body;
                    if ((headers.get("Content-Type") || "").includes("application/json")) {
                        var payload = JSON.parse(init.body);
                        payload.port_ack = mode;
                        body = JSON.stringify(payload);
                    } else if (init.body instanceof FormData) {
                        body = new FormData();
                        init.body.forEach(function (value, key) { body.append(key, value); });
                        body.set("port_ack", mode);
                    } else {
                        body = new URLSearchParams(init.body);
                        body.set("port_ack", mode);
                        body.set("port_identity_ack", mode);
                        body = body.toString();
                    }
                    return original(input, Object.assign({}, init, {body: body})).then(function (saved) {
                        if (mode === "fix" && saved.ok) {
                            // The dict entry moved: refresh trees/grids that still show the old key.
                            saved.clone().json().then(function (result) {
                                if (result.ok) window.setTimeout(function () { window.location.reload(); }, 500);
                            });
                        }
                        return saved;
                    });
                });
            });
        });
    };
})();
