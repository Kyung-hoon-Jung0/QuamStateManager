// Smoke check: the vendored CodeMirror 6 bundle loads under jsdom and creates
// a JSON editor on a div (D-21). Exit code is the verdict.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const file = path.join(__dirname, "..", "quam_state_manager", "web", "static", "vendor", "cm6", "cm6.min.js");
const dom = new JSDOM('<!doctype html><body><div id="ed"></div></body>', { runScripts: "outside-only", pretendToBeVisual: true });
const win = dom.window;
let n = 0, bad = 0;
function ok(c, m) { n++; if (!c) { bad++; console.log("FAIL " + m); } }
win.eval(fs.readFileSync(file, "utf8"));
const CM = win.SmCodeMirror;
ok(CM && typeof CM.create === "function", "global SmCodeMirror.create");
ok(CM && CM.EditorView && CM.EditorState && CM.state && CM.view, "namespaces exposed");
let changed = null;
const ed = CM.create(win.document.getElementById("ed"), { doc: '{"a": [1, 2]}', dark: false, lint: true, onChange: (t) => { changed = t; } });
ok(win.document.querySelector("#ed .cm-editor"), "editor mounted");
ok(win.document.querySelector("#ed .cm-lineNumbers"), "line numbers");
ok(win.document.querySelector("#ed .cm-foldGutter"), "fold gutter");
ok(ed.getDoc() === '{"a": [1, 2]}', "doc round-trips");
ed.setDoc('{"b": true}');
ok(changed === '{"b": true}', "onChange fires");
ok(CM.commands.undo(ed.view) && ed.getDoc() === '{"a": [1, 2]}', "undo restores");
ed.setDark(true); ed.setReadOnly(true);
ok(ed.view.state.readOnly === true, "read-only toggles");
ed.openSearch();
ok(win.document.querySelector("#ed .cm-search"), "search panel opens");
ed.destroy();
console.log((bad ? "FAILED " : "ok ") + "(" + n + " assertions)");
process.exit(bad ? 1 : 0);
