/* docs/308: the UI kit's two renderers agree. Loads a rendered /ui-kit page
 * (path in argv[2], written by tests/test_ui_contract.py), runs the shipped
 * static/ui.js in the jsdom window realm, and compares every macro-rendered
 * sample (#uk-m-N) with the ui.js one (#uk-j-N) as a TREE: tag, the
 * attributes that matter, and the trimmed text -- not bytes.
 * Also checks escaping: a hostile string renders as text on both sides. */
const fs = require("fs");
const path = require("path");
let JSDOM;
try { ({ JSDOM } = require("jsdom")); } catch (e) { console.log("SKIP jsdom not installed"); process.exit(0); }

const htmlPath = process.argv[2];
if (!htmlPath) { console.log("FAIL no page path given"); process.exit(1); }
const html = fs.readFileSync(htmlPath, "utf8");
const uiJs = fs.readFileSync(path.join(__dirname, "..", "quam_state_manager", "web", "static", "ui.js"), "utf8");

let fails = 0, asserts = 0;
function ok(cond, msg) { asserts++; if (!cond) { fails++; console.log("FAIL " + msg); } }

// Strip every <script> so only ui.js runs; it is evaluated inside the window realm
// (new window.Function), so its bare globals (document, window) are the page's own.
const dom = new JSDOM(html.replace(/<script(?![^>]*id="ui-kit-specs")[\s\S]*?<\/script>/g, ""),
                      { url: "http://localhost/ui-kit", runScripts: "outside-only" });
const { window } = dom;
const themes = [];
window.setTheme = function (t) { themes.push(t); };
window.eval(uiJs);
const doc = window.document;
ok(window.UI && typeof window.UI.render === "function", "ui.js exported window.UI");
const filled = window.UI.mountSamples(doc);

const ATTRS = ["role", "aria-label", "aria-hidden", "aria-pressed", "type", "scope", "title", "disabled", "open", "checked", "data-ui-theme"];
function tree(n) {
    if (n.nodeType === 3) return null;
    const out = { tag: n.tagName.toLowerCase(), cls: [...n.classList].sort().join(" "), attrs: {}, kids: [] };
    ATTRS.forEach((a) => { if (n.hasAttribute(a)) out.attrs[a] = a === "disabled" || a === "open" || a === "checked" ? true : n.getAttribute(a); });
    let text = "";
    const flush = () => { const t = text.replace(/\s+/g, " ").trim(); if (t) out.kids.push("#" + t); text = ""; };
    n.childNodes.forEach((c) => {
        if (c.nodeType === 3) text += c.nodeValue;
        else if (c.nodeType === 1) { flush(); out.kids.push(tree(c)); }
    });
    flush();
    return out;
}
function diff(a, b, where) {
    if (typeof a === "string" || typeof b === "string") return a === b ? null : where + ": " + JSON.stringify(a) + " != " + JSON.stringify(b);
    if (a.tag !== b.tag) return where + ": tag " + a.tag + " != " + b.tag;
    if (a.cls !== b.cls) return where + "<" + a.tag + ">: class '" + a.cls + "' != '" + b.cls + "'";
    const ka = JSON.stringify(a.attrs), kb = JSON.stringify(Object.fromEntries(Object.keys(a.attrs).map((k) => [k, b.attrs[k]])));
    if (ka !== kb || Object.keys(a.attrs).length !== Object.keys(b.attrs).length) return where + "<" + a.tag + ">: attrs " + JSON.stringify(a.attrs) + " != " + JSON.stringify(b.attrs);
    if (a.kids.length !== b.kids.length) return where + "<" + a.tag + "." + a.cls + ">: " + a.kids.length + " children != " + b.kids.length;
    for (let i = 0; i < a.kids.length; i++) { const d = diff(a.kids[i], b.kids[i], where + "/" + a.tag + "[" + i + "]"); if (d) return d; }
    return null;
}

const specs = JSON.parse(doc.getElementById("ui-kit-specs").textContent);
ok(specs.length >= 20, "the page carries its spec list (" + specs.length + ")");
ok(filled === specs.length, "ui.js filled every sample slot (" + filled + " of " + specs.length + ")");
const parts = new Set();
specs.forEach((s, i) => {
    const m = doc.getElementById("uk-m-" + i), j = doc.getElementById("uk-j-" + i);
    ok(m && j, "sample " + i + " has both slots");
    if (!m || !j) return;
    ok(m.children.length === 1 && j.children.length === 1, "sample " + i + " (" + s.part + ") renders one root on each side");
    const d = diff(tree(m), tree(j), "sample " + i + " (" + s.part + ")");
    ok(d === null, d || "");
    parts.add(s.part);
});
["head", "actions", "notice", "verdict", "item", "dialog", "table", "callout", "segmented", "disclosure", "check"].forEach((p) => {
    ok(parts.has(p), "the page samples the '" + p + "' part");
});

// escaping: a hostile string is text on the ui.js side (the macro side is Jinja autoescape, pinned in pytest)
const evil = '<img src=x onerror="window.__pwned=1">';
const n = window.UI.render({ part: "item", chip: evil, title: evil, body: [evil], note: evil, badge: { text: evil } });
ok(n.querySelector("img") === null && n.textContent.indexOf("<img") >= 0, "ui.js escapes every text slot");
const t = window.UI.render({ part: "table", columns: [evil], rows: [[{ chip: evil }, { text: evil, title: evil }]] });
ok(t.querySelector("img") === null, "ui.js escapes table cells");

// the one delegated listener: a theme button calls setTheme
const tb = doc.querySelector('[data-ui-theme="light"]');
ok(!!tb, "the page has the theme buttons");
if (tb) tb.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
ok(themes.join(",") === "light", "a theme button calls window.setTheme");

console.log((fails ? "FAIL" : "ok") + " ui_kit_selfcheck (" + asserts + " assertions) " + fails + " failed");
process.exit(fails ? 1 : 0);
