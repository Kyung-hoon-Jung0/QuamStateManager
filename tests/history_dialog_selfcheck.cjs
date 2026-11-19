"use strict";
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");
const staticDir = path.join(__dirname, "../quam_state_manager/web/static");
const app = fs.readFileSync(path.join(staticDir, "app.js"), "utf8");
const markup = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const tick = () => new Promise(resolve => setTimeout(resolve, 20));

(async function () {
    for (const action of ["stage", "restore-live"]) {
        const dom = new JSDOM('<body><form><input name="seen_changes" value="3">' + markup
            + '</form><div id="status-bar"></div><div id="state-history-detail"></div></body>',
        { url: "http://localhost/state-history", runScripts: "dangerously", pretendToBeVisual: true });
        const w = dom.window;
        try {
            const sent = [];
            w.XPathEvaluator = function () {};
            w.XPathEvaluator.prototype.createExpression = function () {
                return { evaluate() { return { iterateNext() { return null; } }; } };
            };
            class XHR {
                constructor() { this.upload = { addEventListener() {} }; }
                open(method, url) { this.method = method; this.url = url; }
                setRequestHeader() {}
                overrideMimeType() {}
                addEventListener() {}
                send(body) { sent.push({ method: this.method, url: this.url, body: String(body) }); }
            }
            w.XMLHttpRequest = XHR;
            w.confirm = () => { throw new Error("native confirm must never run"); };
            // Use the actual focus trap, dialog, adapter and HTMX request implementation.
            w.eval("var _focusTrapStack = [];\n" + app.slice(app.indexOf("function _focusableIn("),
                app.indexOf("/**\n * Show a transient toast")));
            w.eval(fs.readFileSync(path.join(staticDir, "htmx.min.js"), "utf8"));
            await tick();
            const button = w.document.querySelector('[hx-post*="/' + action + '?"]');
            assert(button, action);
            w.htmx.process(w.document.body);
            button.click();
            assert.equal(sent.length, 0, "opening the dialog must not send");
            assert(w.document.querySelector("#sm-confirm-overlay"));
            w.document.querySelector(".sm-confirm-cancel").click();
            await tick();
            assert.equal(sent.length, 0, "Cancel must not send");
            button.click();
            w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
            await tick();
            assert.equal(sent.length, 0, "Escape must not send");
            assert(!w.document.querySelector("#sm-confirm-overlay"), "Escape closes the dialog");
            button.click();
            button.click(); // A second press must not make a second pending dialog/request.
            assert.equal(sent.length, 0);
            w.document.querySelector(".sm-confirm-ok").click();
            await tick();
            const suffix = action === "stage" ? "target=status&chip_key=chip" : "chip_key=chip";
            assert.deepEqual(sent, [{ method: "POST", url: "/state-history/snapshot/" + action + "?" + suffix,
                body: "seen_changes=3" }], "confirmation resumes the original HTMX request exactly once");
        } finally { dom.window.close(); }
    }
    console.log("DONE: both history requests wait for confirm; cancel and Escape send nothing");
})().catch(error => { console.error(error); process.exitCode = 1; });
