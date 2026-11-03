/* One explicit identity offer across field, batch and type-conversion callers. */
(function () {
    "use strict";
    var original = window.fetch;
    var choices = Promise.resolve();
    function choose(offer) {
        var answer = choices.then(function () { return showChoice(offer); });
        choices = answer.catch(function () {});
        return answer;
    }
    function showChoice(offer) {
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
            // HTMX 2 exposes parameters as a FormData proxy. Copy its real
            // formData so repeated values and files survive the retry.
            var values;
            if (config.formData) {
                values = new FormData();
                config.formData.forEach(function (value, key) { values.append(key, value); });
                values.set("port_ack", mode);
            } else {
                values = Object.assign({}, config.parameters, {port_ack: mode});
            }
            window.htmx.ajax("POST", config.path, {
                source: config.elt, target: detail.target,
                values: values, headers: config.headers
            });
        });
    });
    window.fetch = function (input, init) {
        var url;
        try { url = new URL(input.url || input, window.location.href); }
        catch (_) { return original.apply(this, arguments); }
        if (url.origin !== window.location.origin || (!["/field/edit", "/field/edit-batch", "/type-fix/apply", "/field/create", "/pulse/edit"].includes(url.pathname) && !/^\/(qubit|pair)\/[^/]+\/edit$/.test(url.pathname)) ||
                String((init && init.method) || input.method).toUpperCase() !== "POST") return original.apply(this, arguments);
        // Clone before fetch consumes a Request body.
        var request = input && typeof input.clone === "function" ? input.clone() : null;
        return original(input, init).then(function (response) {
            if (response.status !== 409) return response;
            return response.clone().json().then(function (data) {
                if (!data.port_identity) return response;
                return choose(data.port_identity).then(async function (mode) {
                    if (mode === "cancel") return response;
                    var options = init || {};
                    var headers = new Headers(options.headers || (request && request.headers) || {});
                    var source = options.body;
                    if (source === undefined && request) {
                        source = (headers.get("Content-Type") || "").includes("multipart/form-data")
                            ? await request.formData() : await request.text();
                    }
                    var body;
                    if ((headers.get("Content-Type") || "").includes("application/json")) {
                        var payload = JSON.parse(source);
                        payload.port_ack = mode;
                        body = JSON.stringify(payload);
                    } else if (source instanceof FormData) {
                        body = new FormData();
                        source.forEach(function (value, key) { body.append(key, value); });
                        body.set("port_ack", mode);
                        // The new FormData body needs its own multipart boundary.
                        headers.delete("Content-Type");
                    } else {
                        body = new URLSearchParams(source);
                        body.set("port_ack", mode);
                        body.set("port_identity_ack", mode);
                        body = body.toString();
                    }
                    // Resolve the caller's original promise with the retry response;
                    // its normal success handler repaints the affected view.
                    var retry = Object.assign({}, options, {body: body, headers: headers});
                    return request ? original(new Request(request, retry)) : original(input, retry);
                });
            }, function () { return response; });
        });
    };
})();
