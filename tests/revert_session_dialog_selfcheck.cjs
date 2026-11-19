"use strict";
// "Revert this session" asks through the in-page dialog: Cancel, Escape and a backdrop click send nothing,
// OK sends the button's own HTMX request exactly once. argv[2] = JSON fixture {markup, expected}.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");
const staticDir = path.join(__dirname, "../quam_state_manager/web/static");
const app = fs.readFileSync(path.join(staticDir, "app.js"), "utf8");
const fixture = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const tick = () => new Promise(resolve => setTimeout(resolve, 20));

function slice(from, to) {
    const a = app.indexOf(from);
    const b = app.indexOf(to, a);
    assert(a >= 0 && b > a, "app.js slice " + from);
    return app.slice(a, b);
}

(async function () {
    const dom = new JSDOM('<body><div id="pending-tray">' + fixture.markup + '</div><div id="status-bar"></div></body>',
        { url: "http://localhost/", runScripts: "dangerously", pretendToBeVisual: true });
    const w = dom.window;
    try {
        const sent = [];
        w.XPathEvaluator = function () {};
        w.XPathEvaluator.prototype.createExpression = function () {
            return { evaluate() { return { iterateNext() { return null; } }; } };
        };
        class XHR {
            constructor() { this.upload = { addEventListener() {} }; this.headers = {}; }
            open(method, url) { this.method = method; this.url = url; }
            setRequestHeader(k, v) { this.headers[k] = String(v); }
            overrideMimeType() {}
            addEventListener() {}
            send(body) {
                sent.push({ method: this.method, url: this.url, body: body == null ? null : String(body),
                    headers: this.headers });
            }
        }
        w.XMLHttpRequest = XHR;
        w.confirm = () => { throw new Error("native confirm must never run"); };
        w.alert = () => { throw new Error("native alert must never run"); };
        // In-realm mocks: the code reads fetch and showToast through the realm's own globals.
        w.eval("window.__preflight = [];\nwindow.__toasts = [];\n"
            + "window.fetch = function (url, opts) { window.__preflight.push(url);\n"
            + "  return Promise.resolve({ json: function () {\n"
            + "    return Promise.resolve(" + JSON.stringify(fixture.preflight)
            + "); } }); };\n"
            + "window.showToast = function (m, l) { window.__toasts.push([String(m), l]); };");
        w.eval("var _focusTrapStack = [];\n" + slice("function _focusableIn(", "/**\n * Show a transient toast"));
        w.eval(slice("window.revertSessionConfirm = function", "/* QA correctness-r2-09"));
        w.eval(fs.readFileSync(path.join(staticDir, "htmx.min.js"), "utf8"));
        await tick();
        w.htmx.process(w.document.body);
        const button = w.document.querySelector(".tray-revert-apply");
        assert(button, "the tray renders the revert button");

        async function open() {
            button.click();
            await tick();
            const overlay = w.document.querySelector("#sm-confirm-overlay");
            assert(overlay, "the press opens the in-page dialog");
            assert(/Revert this Auto-Sync session\?/.test(overlay.textContent), "the dialog keeps its question");
            assert.equal(sent.length, 0, "opening the dialog must not send");
            return overlay;
        }
        await open();
        w.document.querySelector(".sm-confirm-cancel").click();
        await tick();
        assert.equal(sent.length, 0, "Cancel must not send");
        assert(!w.document.querySelector("#sm-confirm-overlay"), "Cancel closes the dialog");

        await open();
        w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
        await tick();
        assert.equal(sent.length, 0, "Escape must not send");
        assert(!w.document.querySelector("#sm-confirm-overlay"), "Escape closes the dialog");

        const overlay = await open();
        overlay.click();
        await tick();
        assert.equal(sent.length, 0, "backdrop click must not send");
        assert(!w.document.querySelector("#sm-confirm-overlay"), "backdrop closes the dialog");
        assert.equal(w.__toasts.filter(t => /nothing was changed/.test(t[0])).length, 3,
            "each dismissal says nothing changed");

        await open();
        w.document.querySelector(".sm-confirm-ok").click();
        await tick();
        await tick();
        assert.equal(w.__preflight.length, 4, "every press preflights once");
        assert.deepEqual(sent, [fixture.expected], "OK sends the button's own request exactly once");
    } finally { dom.window.close(); }
    console.log("DONE: revert asks in-page; cancel, Escape and backdrop send nothing; OK sends one request");
})().catch(error => { console.error(error); process.exitCode = 1; });
