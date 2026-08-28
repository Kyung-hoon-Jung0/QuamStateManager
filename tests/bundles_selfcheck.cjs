/* Executes app.js's Bundles loader (docs/141 4l) under jsdom: the manifest,
 * present-tag detection, the URL -> bundle map, ordered loading, the
 * htmx:confirm gate (hold the request until the page's bundles are here,
 * never strand it on a lost script), the Back/Forward hold (htmx restores
 * history WITHOUT an htmx:confirm) and Bundles.call for global controls.
 * Only the loader block is evaluated -- the rest of app.js is not needed. */
const fs = require("fs");
const path = require("path");
let JSDOM;
try { ({ JSDOM } = require("jsdom")); } catch (e) { console.log("SKIP jsdom not installed"); process.exit(0); }

const appJs = fs.readFileSync(path.join(__dirname, "..", "quam_state_manager", "web", "static", "app.js"), "utf8");
const start = appJs.indexOf("window.Bundles = (function () {");
const end = appJs.indexOf("})();", start) + 5;
if (start < 0 || end < 5) { console.log("FAIL loader block not found in app.js"); process.exit(1); }
const block = appJs.slice(start, end);

const MANIFEST = {
    files: { "a.js": "/static/a.js?v=1", "b.js": "/static/b.js?v=1", "c.js": "/static/c.js?v=1", "p.js": "/static/p.js?v=1" },
    bundles: { grid: ["a.js", "b.js"], pulses: ["p.js"], topo: ["c.js"], components: ["c.js", "a.js"] },
    pages: { bulk: ["grid"], pulses: ["pulses"] },
};
const dom = new JSDOM(`<!doctype html><html><head>
<script src="/static/app.js"></script>
<script src="/static/p.js?v=1" data-bundle-file="p.js"></script>
<script id="bundle-manifest" type="application/json">${JSON.stringify(MANIFEST)}</script>
</head><body></body></html>`, { url: "http://localhost/explorer" });
const { window } = dom;
const toasts = [];
window.showToast = function (m, lvl) { toasts.push([m, lvl]); };
// stands in for htmx's own handler (htmx assigns window.onpopstate at DOMContentLoaded; app.js chains it)
const popCalls = [];
window.onpopstate = function (e) { popCalls.push(e); };
let fails = 0, asserts = 0;
function ok(cond, msg) { asserts++; if (!cond) { fails++; console.log("FAIL " + msg); } }

// the harness rule: Node-realm eval sees no window properties as bare globals -- bridge them
global.window = window; global.document = window.document; global.CustomEvent = window.CustomEvent; global.location = window.location;
eval(block);
const B = window.Bundles;
ok(B && typeof B.need === "function" && typeof B.forPath === "function", "loader exported");

// B1 manifest + present tags
const m = B._manifest();
ok(m.files["a.js"] === "/static/a.js?v=1", "manifest parsed from the page");
ok(B.loaded("pulses") === true, "a bundle whose file is already on the page counts as loaded");
ok(B.loaded("grid") === false, "a bundle with no tag on the page is not loaded");
ok(B.loaded(["pulses", "grid"]) === false, "loaded() over a list is all-or-nothing");

// B2 URL -> bundles (mirrors the routes)
const fp = (p) => B.forPath(p).join(",");
ok(fp("/bulk") === "grid" && fp("/bulk?q=x") === "grid" && fp("/bulk/rows") === "grid", "/bulk -> grid");
ok(fp("/bulkx") === "", "a prefix that is not the route matches nothing");
ok(fp("/pulses") === "pulses" && fp("/pulse/detail?path=q1") === "pulses" && fp("/pulse/new/env-strip") === "pulses", "/pulse/* -> pulses");
ok(fp("/topology") === "chipstatus,components" && fp("/wiring") === "chipstatus,components", "/topology (+ its /wiring alias) -> chip status + components");
ok(fp("/qubits") === "components" && fp("/qubit/q1") === "components" && fp("/pair/q1-2") === "components" && fp("/qdac") === "components", "component pages");
ok(fp("/datasets") === "datasets" && fp("/dataset/abc123") === "datasets" && fp("/dataset/abc/ndview?x=1") === "datasets", "dataset routes");
ok(fp("/generate") === "generate" && fp("/regenerate") === "generate", "wizard routes");
ok(fp("/instrument") === "wiring" && fp("/instrument/preview") === "wiring" && fp("/scheduler") === "scheduler" && fp("/autofit") === "autofit", "single-bundle pages");
ok(fp("/compare-hub?src=a") === "compare" && fp("/diff?a=x&b=y") === "compare", "compare routes");
ok(fp("http://localhost:5199/bulk?x=1") === "grid", "an absolute URL is reduced to its path");
ok(fp("/api/progress") === "" && fp("/state/drift") === "" && fp("/undo?n=2") === "", "polls and writes need nothing");
ok(fp("") === "" && fp(null) === "", "empty/null path is safe");

// B3 need(): tags inserted in order, deduped, resolved on load
const tagsFor = () => Array.from(window.document.querySelectorAll("script[data-bundle-file]")).map((s) => s.getAttribute("data-bundle-file"));
let resolved = false;
B.need(["components", "grid"]).then(() => { resolved = true; });
const inserted = tagsFor().filter((f) => f !== "p.js");
ok(inserted.join(",") === "c.js,a.js,b.js", "files appended once each, in bundle order (" + inserted.join(",") + ")");
const scripts = Array.from(window.document.querySelectorAll("script[data-bundle-file]")).filter((s) => s.getAttribute("data-bundle-file") !== "p.js");
ok(scripts.every((s) => s.async === false), "dynamic tags carry async=false so they execute in insertion order");
ok(scripts[0].src.indexOf("/static/c.js?v=1") >= 0, "src comes from the manifest (versioned url)");
B.need("grid");
ok(tagsFor().filter((f) => f === "a.js").length === 1, "an in-flight file is not appended twice");
scripts.forEach((s) => s.onload());

function fire(p) {
    const calls = [];
    const evt = new window.CustomEvent("htmx:confirm", { cancelable: true, bubbles: true,
        detail: { path: p, issueRequest: function (skip) { calls.push(skip); } } });
    window.document.dispatchEvent(evt);
    return { prevented: evt.defaultPrevented, calls: calls };
}
function pop(url, state) {
    dom.reconfigure({ url: url });
    const e = new window.PopStateEvent("popstate", { state: state });
    window.dispatchEvent(e);
    return e;
}

setTimeout(() => {
    ok(resolved === true, "need() resolves once every file loaded");
    ok(B.loaded(["grid", "components", "topo"]) === true, "loaded() sees the freshly loaded files");
    ok(tagsFor().length === 4, "no further tags after load");

    // B4 Back/Forward: htmx's own popstate handler is chained and HELD until the page's bundles are here
    m.bundles.wiring = ["w.js"]; m.files["w.js"] = "/static/w.js?v=1";
    const p1 = pop("http://localhost/instrument", { htmx: true });
    ok(popCalls.length === 0, "a Back to a page whose bundle is missing waits for it");
    const w = window.document.querySelector('script[data-bundle-file="w.js"]');
    ok(!!w, "the Back appended the missing file");
    w.onload();
    setTimeout(() => {
        ok(popCalls.length === 1 && popCalls[0] === p1, "htmx's own handler then runs with the ORIGINAL event");
        pop("http://localhost/bulk?x=1", { htmx: true });
        ok(popCalls.length === 2, "a Back to a page whose bundles are loaded goes straight through");
        pop("http://localhost/instrument", null);
        ok(popCalls.length === 3, "a popstate that is not htmx's goes straight through untouched");

        // B5 the htmx:confirm gate
        ok(fire("/bulk").prevented === false, "a page whose bundles are loaded is not held");
        ok(fire("/api/progress").prevented === false, "a poll is never held");
        m.bundles.scheduler = ["z.js"]; m.files["z.js"] = "/static/z.js?v=1";
        const r = fire("/scheduler");
        ok(r.prevented === true && r.calls.length === 0, "a page whose bundle is missing is held (request not issued yet)");
        const z = window.document.querySelector('script[data-bundle-file="z.js"]');
        ok(!!z, "the hold appended the missing file");
        z.onload();
        setTimeout(() => {
            ok(r.calls.length === 1 && r.calls[0] === true, "the held request is issued once, skipping a second confirm");
            m.bundles.autofit = ["lost.js"]; m.files["lost.js"] = "/static/lost.js?v=1";
            const r2 = fire("/autofit");
            ok(r2.prevented === true, "held for the lost script");
            window.document.querySelector('script[data-bundle-file="lost.js"]').onerror();
            setTimeout(() => {
                ok(r2.calls.length === 1, "onerror still issues the request (the page renders, its widgets degrade)");
                ok(B.loaded("autofit") === false, "a failed file is not marked loaded (a retry may succeed)");
                const r3 = fire("/autofit");
                ok(r3.prevented === true && window.document.querySelectorAll('script[data-bundle-file="lost.js"]').length === 2, "the next navigation retries the lost file");

                // B6 call(): load then invoke; a missing function toasts
                const got = [];
                window.BulkEdit = { setFont: function (v) { got.push(v); return "done"; } };
                B.call("grid", "BulkEdit.setFont", 0.85).then((v) => {
                    ok(got.length === 1 && got[0] === 0.85 && v === "done", "call() reaches the loaded bundle's function with its argument");
                    B.call("grid", "BulkEdit.nope", 1).then(() => {
                        ok(toasts.length === 1 && /not available/.test(toasts[0][0]) && toasts[0][1] === "error", "a missing target toasts instead of throwing");
                        console.log((fails ? "FAIL " : "ok ") + "bundles_selfcheck (" + asserts + " assertions, " + fails + " failed)");
                        process.exit(fails ? 1 : 0);
                    });
                });
            }, 0);
        }, 0);
    }, 0);
}, 0);
