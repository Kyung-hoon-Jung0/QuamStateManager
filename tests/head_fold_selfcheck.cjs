// D-103: the page-head fold boot (templates/_ui_head_boot.html) in jsdom.
// Prints "ok head_fold_selfcheck N passed, M failed"; exit code = failures.
const fs = require("fs"), path = require("path");
const { JSDOM } = require("jsdom");
const src = fs.readFileSync(path.join(__dirname, "..", "quam_state_manager", "web", "templates", "_ui_head_boot.html"), "utf8");
const script = src.slice(src.indexOf("<script>") + 8, src.indexOf("</script>"));
let pass = 0, fail = 0;
function ok(c, m) { if (c) pass++; else { fail++; console.log("FAIL " + m); } }
const HEAD = '<header class="ui-head ui-head--page"><h2 class="ui-head-title">T</h2>'
    + '<div class="ui-actions"><button class="btn-sm">Act</button></div><p class="ui-head-gist">G</p>'
    + '<button type="button" class="ui-btn-icon ui-head-fold" aria-expanded="true"></button></header>';
function world(height, stored) {
    const dom = new JSDOM("<!doctype html><html><head></head><body>" + HEAD + "</body></html>", { runScripts: "outside-only", url: "http://x/" });
    const w = dom.window;
    Object.defineProperty(w, "innerHeight", { value: height, configurable: true });
    if (stored !== undefined) w.localStorage.setItem("quam_head_compact", stored);
    w.eval(script);
    w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
    return w;
}
const folded = (w) => w.document.documentElement.classList.contains("ui-heads-compact");
ok(folded(world(700)), "no stored choice + 700 px tall -> folded");
ok(!folded(world(900)), "no stored choice + 900 px tall -> unfolded");
ok(!folded(world(700, "0")), "stored '0' wins over a short window");
ok(folded(world(900, "1")), "stored '1' wins over a tall window");
const w = world(900);
ok(w.document.querySelector(".ui-head-fold").getAttribute("aria-expanded") === "true", "unfolded -> aria-expanded true");
w.document.querySelector(".ui-head-fold").click();
ok(folded(w), "a press folds");
ok(w.localStorage.getItem("quam_head_compact") === "1", "a press stores the choice");
ok(w.document.querySelector(".ui-head-fold").getAttribute("aria-expanded") === "false", "folded -> aria-expanded false");
w.document.querySelector(".ui-head-fold").click();
ok(!folded(w) && w.localStorage.getItem("quam_head_compact") === "0", "a second press unfolds and stores '0'");
// the actions are never hidden by the kit: no fold rule touches .ui-actions display
const kit = fs.readFileSync(path.join(__dirname, "..", "quam_state_manager", "web", "static", "ui-kit.css"), "utf8");
const rules = kit.replace(/\/\*[\s\S]*?\*\//g, "").split("}").filter((r) => r.includes("ui-heads-compact"));
ok(rules.length > 0 && rules.every((r) => !(/ui-actions/.test(r.split("{")[0]) && /display\s*:\s*none/.test(r))), "actions never hidden");
console.log("ok head_fold_selfcheck " + pass + " passed, " + fail + " failed");
process.exit(fail);
