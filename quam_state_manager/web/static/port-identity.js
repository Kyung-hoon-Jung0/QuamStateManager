/* One explicit identity offer for every caller of the /field/edit door. */
(function () {
    "use strict";
    var original = window.fetch;
    function choose(offer) {
        return new Promise(function (resolve) {
            var dialog = document.createElement("dialog");
            var message = document.createElement("p");
            message.textContent = offer.message;
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
    window.fetch = function (input, init) {
        var url;
        try { url = new URL(input.url || input, window.location.href); }
        catch (_) { return original.apply(this, arguments); }
        if (url.origin !== window.location.origin || url.pathname !== "/field/edit" || !init ||
                String(init.method).toUpperCase() !== "POST") return original.apply(this, arguments);
        return original(input, init).then(function (response) {
            if (response.status !== 409) return response;
            return response.clone().json().then(function (data) {
                if (!data.port_identity) return response;
                return choose(data.port_identity).then(function (mode) {
                    if (mode === "cancel") return response;
                    var body = new URLSearchParams(init.body);
                    body.set("port_identity_ack", mode);
                    return original(input, Object.assign({}, init, {body: body.toString()})).then(function (saved) {
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
