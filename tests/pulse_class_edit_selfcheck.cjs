// Pulse class edit screen under jsdom: the real cm6-python.min.js bundle and the
// real pulse-class-edit.js on the real template markup, with a mocked server.
// Pins: form redraw from every adopted text, stale form asks, SM loads are not
// undoable, the END marker line end is guarded, a keystroke during a poll is
// kept, the editor theme follows the page, old previews carry the previous
// line, form values are never invented, labelled field rows, the layout
// tokens, ui-kit-only editor colours. Exit code is the verdict.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "..");
const STATIC = path.join(ROOT, "quam_state_manager", "web", "static");
const read = (p) => fs.readFileSync(path.join(ROOT, p), "utf8");
let n = 0, bad = 0;
function ok(c, m) { n++; if (!c) { bad++; console.log("FAIL " + m); } }
const flush = async (k = 8) => { for (let i = 0; i < k; i++) await new Promise((r) => setTimeout(r, 0)); };

// Facts from their source: the marker lines and the previous-version line.
const tpl = read("quam_state_manager/core/pulse_class_template.py");
const BEGIN = /^REGION_BEGIN = "(.*)"$/m.exec(tpl)[1];
const END = /^REGION_END = "(.*)"$/m.exec(tpl)[1];
const PREVIOUS = /^PREVIOUS_LINE = "(.*)"$/m.exec(read("quam_state_manager/core/pulse_class_drafts.py"))[1];

function classText(fields, formula) {
  return "import numpy as np\n\nclass FlatTop(Base):\n" + BEGIN + "\n" +
    fields.map((f) => "    " + f.name + ": " + f.type + " = " + f.default + "\n").join("") +
    "    # formula: " + formula + "\n" + END + "\n\n    def waveform_function(self):\n        return 0\n";
}
// The same rule as the server's describe: markers and everything between.
function info(text, fields, formula) {
  const bm = text.indexOf(BEGIN), em = text.indexOf(END);
  return { editable: true, reason: "", protect: [bm, em + END.length], spec: { fields, formula } };
}
const F2 = [{ name: "amplitude", type: "float", default: 0.1, unit: "V" }, { name: "length", type: "int", default: 40, unit: "ns" }];
const F3 = F2.concat([{ name: "sigma", type: "float", default: 4.0, unit: "ns" }]);

// ---------------------------------------------------------------- markup
let html = read("quam_state_manager/web/templates/pulse_class_edit.html");
html = html.replace(/\{%[\s\S]*?%\}/g, "").replace(/\{\{[\s\S]*?\}\}/g, "");
const dom = new JSDOM("<!doctype html><html><body><div id=\"table-pane\">" + html + "</div></body></html>",
  { runScripts: "outside-only", pretendToBeVisual: true });
const win = dom.window, doc = win.document;
// jsdom has no layout: CodeMirror's measuring reads client rects.
const noRects = function () { return { length: 0, item: () => null, [Symbol.iterator]: function* () {} }; };
const zeroRect = function () { return { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 }; };
win.Range.prototype.getClientRects = noRects;
win.Range.prototype.getBoundingClientRect = zeroRect;
const root = doc.getElementById("pulse-class-page");
ok(root.querySelector("[data-pce-unlock]").textContent.trim() === "Edit the template lines by hand",
  "initial template: the hand-edit control has the label named by the server");
root.setAttribute("data-pce-cls", "FlatTop");
root.setAttribute("data-pce-previous-line", PREVIOUS);
// the stripped template drops the new-class section's Jinja branches: keep only the class view
root.querySelectorAll("[data-pce-new], [data-pce-new-name], [data-pce-new-error]").forEach((x) => x.closest("section") && x.closest("section").remove());
const env = root.querySelector("[data-pce-env]");
env.innerHTML = '<option value="">Choose an environment</option><option value="py">env</option>';

// ---------------------------------------------------------------- mock server
const server = { text: classText(F2, "a * t"), sha: "s1", fields: F2, formula: "a * t" };
const calls = [];
// These error/confirm bodies match the answers returned by the server doors.
const REGION_ERROR = "This save changes the template line of field 'amplitude'. The form owns these lines: change them in the form, or press \"Edit the template lines by hand\" first.";
const WRITING_ERROR = "The draft file is still being written; choose again when it has stopped changing.";
const answers = {};
let enforceRegion = false;
function answer(url, status, body) { (answers[url] || (answers[url] = [])).push({ http: status, body }); }
function conflict(fileSha, filePath = "D:/draft/FlatTop.py") {
  return { question: "The draft file changed outside SM while this editor has changes that are not saved. Which one do you keep?",
    file_sha: fileSha, choices: [
      { keep: "sm", label: "Keep the SM editor", loses: "The outside edit in " + filePath + " will be lost." },
      { keep: "file", label: "Keep the file on disk", loses: "Your unsaved changes in this editor will be lost." }] };
}
function confirm(fileSha, filePath = "D:/classes/flat.py") {
  return { question: "A file is already there. Replace it with this draft?", path: filePath, file_sha: fileSha,
    loses: filePath + " will be replaced; a copy of it is kept next to it." };
}
let hold = null;            // {url-prefix: deferred} to hold one answer
function deferred() { let res; const p = new Promise((r) => { res = r; }); return { p, res }; }
const plots = { wave: 0, timeline: 0 };
let previewAnswer = () => ({ ok: true, samples: [0, 1, 1, 0], sampling_rate: 1e9, draft_sha256: server.sha, proportional: { proportional: true } });
let pollAnswer = () => ({ status: "unchanged", sha: server.sha, status_line: "Draft, not saved" });
function route(method, url, body) {
  calls.push({ method, url, body });
  const u = url.split("?")[0];
  if (answers[u] && answers[u].length) return answers[u].shift();
  if (u === "/pulse-class/load") return { ok: true, cls: "FlatTop", text: server.text, sha: server.sha,
    info: info(server.text, server.fields, server.formula), path: "draft/FlatTop.py", status: "Draft, not saved", target: "",
    last_preview: { samples: [0, 2, 0], sampling_rate: 1e9, draft_sha256: "s0" } };
  if (u === "/pulse-class/poll") return pollAnswer();
  if (u === "/pulse-class/preview") return previewAnswer();
  if (u === "/pulse-class/save") {
    const region = (text) => text.slice(text.indexOf(BEGIN), text.indexOf(END) + END.length);
    if (enforceRegion && !body.hand_edit && region(body.text) !== region(server.text)) {
      return { http: 400, body: { ok: false, error: REGION_ERROR, region: true } };
    }
    server.text = body.text; server.sha = "s" + calls.length;
    return { ok: true, text: server.text, sha: server.sha, info: info(server.text, server.fields, server.formula), status: "Draft, not saved" };
  }
  if (u === "/pulse-class/form") {
    server.fields = body.fields; server.formula = body.formula; server.text = classText(body.fields, body.formula); server.sha = "s" + calls.length;
    return { ok: true, text: server.text, sha: server.sha, info: info(server.text, server.fields, server.formula), status: "Draft, not saved" };
  }
  return { ok: false, error: "no route" };
}
win.__route = (m, u, b) => {
  const key = Object.keys(hold || {}).find((k) => u.startsWith(k));
  if (key) { const d = hold[key]; delete hold[key]; return d.p.then(() => route(m, u, b)); }
  return Promise.resolve(route(m, u, b));
};
win.eval(`window.fetch = function (url, o) {
  o = o || {};
  if (window.__fetchMode === "reject") return Promise.reject(new TypeError("network down"));
  if (window.__fetchMode === "null-body") return Promise.resolve({ status: 200, json: function () { return Promise.resolve(null); } });
  if (window.__fetchMode === "bad-json") return Promise.resolve({ status: 200, json: function () { throw new Error("broken body"); } });
  return Promise.resolve(window.__route(o.method || "GET", url, o.body ? JSON.parse(o.body) : null)).then(function (j) {
    return { status: j.http || 200, json: function () { return Promise.resolve(JSON.parse(JSON.stringify(j.http ? j.body : j))); } };
  });
};
window.setInterval = function () { return 1; };
window.clearInterval = function () {};
window._plotlyRender = function () { window.__plots.wave++; };
window.GateTimeline = { render: function () { window.__plots.timeline++; } };`);
win.__plots = plots;

win.eval(fs.readFileSync(path.join(STATIC, "vendor", "cm6", "cm6-python.min.js"), "utf8"));
const CMP = win.SmCodePython;
ok(CMP && typeof CMP.create === "function", "bundle exposes SmCodePython.create");

const $ = (s) => root.querySelector(s);
const vis = (s) => { const e = $(s); return !!e && !e.hidden && e.textContent.trim() !== ""; };
const rows = () => Array.from(root.querySelectorAll("[data-pce-field]"));
const rowNames = () => rows().map((r) => r.querySelector('[data-k="name"]').value);
function type(view, pos, s) { view.dispatch({ changes: { from: pos, insert: s }, userEvent: "input.type" }); }
function since(i, u) { return calls.slice(i).filter((c) => c.url.startsWith(u)); }
function dialogOpen() { return $("[data-pce-conflict]").hasAttribute("open"); }
function closeDialog() { const d = $("[data-pce-conflict]"); d.removeAttribute("open"); const P = win.PulseClassEdit.state(); if (P) P.conflictOpen = false; }
function clickChoice(label) {
  const b = Array.from(root.querySelectorAll("[data-pce-conflict-actions] button")).find((x) => x.textContent === label);
  if (b) b.click();
  return !!b;
}
function outsideEdit(fields, formula) {
  server.text = classText(fields, formula); server.fields = fields; server.formula = formula; server.sha = "o" + calls.length;
  const t = server.text, s = server.sha, inf = info(t, fields, formula);
  pollAnswer = () => { pollAnswer = () => ({ status: "unchanged", sha: s, status_line: "Draft, not saved" });
    return { status: "changed", sha: s, text: t, info: inf, status_line: "Draft, not saved",
      conflict: conflict(s) }; };
}

(async () => {
  // P2-7: no data-theme attribute is the default "qm" theme, which is dark
  doc.documentElement.removeAttribute("data-theme");
  win.eval(fs.readFileSync(path.join(STATIC, "pulse-class-edit.js"), "utf8"));
  await flush();
  const P = win.PulseClassEdit.state();
  ok(P && P.cm, "the screen booted with an editor");
  const cm = P.cm, view = cm.view;
  ok(cm.isDark() === true, "P2-7: the default qm theme gives a dark editor");
  doc.documentElement.setAttribute("data-theme", "light"); await flush();
  ok(cm.isDark() === false, "P2-7: a switch to light recolours the editor");
  doc.documentElement.setAttribute("data-theme", "dark"); await flush();
  ok(cm.isDark() === true, "P2-7: a switch back to dark recolours the editor");

  // load
  ok(cm.getDoc() === server.text, "load: the pane shows the draft");
  ok($("[data-pce-status]").textContent === "Draft, not saved", "load: the status line is the server's line");
  ok(!vis("[data-pce-dirty]"), "P3: a clean pane shows no dirty note");
  ok(rowNames().join() === "amplitude,length", "load: the form shows the draft's fields");
  // P2-10: one labelled header row
  const head = root.querySelector("[data-pce-field-head]");
  ok(head && Array.from(head.querySelectorAll(".ui-field-label")).map((x) => x.textContent).join() === "Name,Type,Default,Unit",
    "P2-10: the field rows carry a Name / Type / Default / Unit header");
  const firstField = root.querySelector("[data-pce-field]");
  ok(head && firstField && head.children.length === firstField.children.length,
    "the header row has as many cells as a field row, so the grid columns stay in step");
  // P2-8: the stored preview ran on another draft: labelled
  ok(plots.wave === 1 && vis("[data-pce-prev-label]") && $("[data-pce-prev-label]").textContent === PREVIOUS,
    "P2-8: a stored preview of another draft carries the previous-version line on load");
  // P1-3: the load is not undoable
  ok(CMP.commands.undo(view) === false && cm.getDoc() === server.text, "P1-3: Ctrl+Z after a load does not empty the pane");

  // P2-3: the END marker line is guarded at its end
  const loaded = cm.getDoc();
  const pr = cm.getProtected();
  type(view, pr.to, "X");
  ok(cm.getDoc() === loaded, "P2-3: typing at the end of the END marker line is refused");
  type(view, pr.from, "X");
  ok(cm.getDoc() === loaded, "P2-3: typing at the start of the BEGIN marker line is refused");
  view.dispatch({ changes: { from: pr.from - 1, to: pr.from }, userEvent: "delete" });
  ok(cm.getDoc() === loaded, "P2-3: deleting the line break before BEGIN is refused");
  type(view, pr.to + 1, "#");
  ok(cm.getDoc() !== loaded && cm.getDoc().indexOf(END + "\n#") >= 0, "P2-3: typing on the line after END is allowed");
  ok(CMP.commands.undo(view) === true && cm.getDoc() === loaded, "P1-3: Ctrl+Z takes back the user's own typing only");
  ok(CMP.commands.undo(view) === false && cm.getDoc() === loaded, "P1-3: a second Ctrl+Z stops at the loaded text");
  // a marker at the very end of the document has no line break to lean on
  const edgeHost = doc.createElement("div"); doc.body.appendChild(edgeHost);
  const edgeText = "x = 1\n" + BEGIN + "\n    a: float = 1\n" + END;
  const edge = CMP.create(edgeHost, { doc: edgeText });
  edge.setProtected({ from: edgeText.indexOf(BEGIN), to: edgeText.length });
  type(edge.view, edgeText.length, "Y");
  ok(edge.getDoc() === edgeText, "P2-3: typing after an END marker that ends the document is refused");
  edge.setGuard(false);
  type(edge.view, edgeText.length, "Y");
  ok(edge.getDoc() === edgeText + "Y", "unlock: by hand the marker line can be edited");
  ok(edge.getProtected() && edge.getProtected().to === edgeText.length + 1, "unlock: the range keeps following the edits");
  edge.destroy();
  // the edits above made the pane dirty: bring it back to the loaded text cleanly
  cm.setDoc(loaded);
  ok(cm.getProtected() === null, "a new text clears the old range (it would cover the whole document)");
  cm.setProtected(pr); P.dirty = false; $("[data-pce-dirty]").hidden = true;

  // P3: two phrases
  type(view, 0, "#");
  ok(vis("[data-pce-dirty]") && $("[data-pce-dirty]").textContent !== "Draft, not saved",
    "P3: unsaved pane edits have their own phrase");
  ok($("[data-pce-status]").textContent === "Draft, not saved", "P3: the status line keeps the server's meaning");
  let i0 = calls.length;
  await win.PulseClassEdit._save(); await flush();
  ok(!vis("[data-pce-dirty]"), "save: the dirty note goes after a save");
  ok(since(i0, "/pulse-class/save").length === 1, "save: one save request");

  // P2-12 / P1-1: an outside edit on a clean pane, no env chosen: the form redraws, SM writes nothing
  i0 = calls.length;
  outsideEdit(F3, "a * t * 2");
  await win.PulseClassEdit._poll(); await flush();
  ok(since(i0, "/pulse-class/poll")[0].url.indexOf("dirty=1") >= 0, "P2-4: the poll always asks for the question block");
  ok(cm.getDoc() === server.text, "outside edit: the clean pane shows the new file");
  ok(rowNames().join() === "amplitude,length,sigma", "P1-1: the form redraws from the outside edit without a preview");
  ok($("[data-pce-formula]").value === "a * t * 2", "P1-1: the formula redraws from the outside edit");
  ok(since(i0, "/pulse-class/save").length === 0 && since(i0, "/pulse-class/form").length === 0, "counter-example: SM writes nothing on an outside edit");
  ok(vis("[data-pce-prev-label]"), "P2-8: with no env the old waveform is labelled the previous version");
  // with an env: waveform and timeline redraw too
  env.value = "py";
  const w0 = plots.wave, t0 = plots.timeline;
  outsideEdit(F2, "a * t");
  await win.PulseClassEdit._poll(); await flush();
  ok(rowNames().join() === "amplitude,length", "P2-12: form redraw on an outside edit");
  ok(plots.wave === w0 + 1 && plots.timeline === t0 + 1, "P2-12: waveform and timeline redraw on an outside edit");
  ok(!vis("[data-pce-prev-label]"), "P2-8: a preview of the current draft carries no previous line");
  // a failed preview keeps the previous one, labelled
  previewAnswer = () => ({ ok: false, error: "boom", previous: { line: PREVIOUS, preview: { samples: [1], sampling_rate: 1e9, draft_sha256: "old" } } });
  outsideEdit(F3, "a * t * 3");
  await win.PulseClassEdit._poll(); await flush();
  ok(vis("[data-pce-prev-label]") && rowNames().length === 3, "P2-8: a failed preview draws the previous one with its line; the form still redraws");
  previewAnswer = () => ({ ok: true, samples: [0, 1, 0], sampling_rate: 1e9, draft_sha256: server.sha });
  env.value = "";

  // P1-1: a form being edited is not overwritten; Apply asks and sends nothing stale
  const amp = () => rows()[0].querySelector('[data-k="default"]');
  amp().value = "0.3"; amp().dispatchEvent(new win.Event("input", { bubbles: true }));
  outsideEdit(F2, "a * t");
  await win.PulseClassEdit._poll(); await flush();
  ok(rowNames().length === 3 && amp().value === "0.3", "P1-1: the form the user is editing stays");
  ok(vis("[data-pce-form-note]"), "P1-1: the form says it is older than the file");
  i0 = calls.length;
  await win.PulseClassEdit._applyForm(); await flush();
  ok(since(i0, "/pulse-class/form").length === 0, "P1-1: Apply the form with a stale form sends nothing");
  ok(dialogOpen() && $("[data-pce-conflict-title]").textContent === "The form is older than the file", "P1-1: Apply the form asks which one to keep");
  ok(root.querySelectorAll("[data-pce-conflict-losses] li").length === 2, "P1-1: each choice names what it loses");
  ok(clickChoice("Reload the form from the file"), "P1-1: the reload choice is offered");
  await flush();
  ok(rowNames().join() === "amplitude,length" && !vis("[data-pce-form-note]"), "P1-1: reload draws the file's form");
  ok(since(i0, "/pulse-class/form").length === 0, "P1-1: reload writes nothing");
  // stale again, this time keep the form
  amp().value = "0.5"; amp().dispatchEvent(new win.Event("input", { bubbles: true }));
  outsideEdit(F3, "a");
  await win.PulseClassEdit._poll(); await flush();
  await win.PulseClassEdit._applyForm(); await flush();
  ok(dialogOpen(), "P1-1: asked again for the second stale form");
  i0 = calls.length;
  clickChoice("Apply my form"); await flush();
  ok(since(i0, "/pulse-class/form").length === 1 && since(i0, "/pulse-class/form")[0].body.fields[0].default === 0.5,
    "P1-1: Apply my form sends the user's values");
  ok(rowNames().join() === "amplitude,length", "P1-1: after the form wrote, it shows what it wrote");
  // a fresh form applies without a question
  i0 = calls.length;
  await win.PulseClassEdit._applyForm(); await flush();
  ok(!dialogOpen() && since(i0, "/pulse-class/form").length === 1, "a current form applies without a question");
  // F4: saving a helper in the pane leaves the form's template base current.
  amp().value = "0.6"; amp().dispatchEvent(new win.Event("input", { bubbles: true }));
  type(view, 0, "# helper\n");
  await win.PulseClassEdit._save(); await flush();
  ok(!vis("[data-pce-form-note]"), "F4: own pane save does not label unchanged template lines old");
  i0 = calls.length;
  await win.PulseClassEdit._applyForm(); await flush();
  ok(!dialogOpen() && since(i0, "/pulse-class/form").length === 1,
    "F4: own pane save does not make the form old");

  // F5: the form door retries the captured form values, even if the form changes meanwhile.
  amp().value = "0.65"; amp().dispatchEvent(new win.Event("input", { bubbles: true }));
  answer("/pulse-class/form", 409, { ok: false, conflict: conflict("form-outside") });
  await win.PulseClassEdit._applyForm(); await flush();
  amp().value = "0.9";
  i0 = calls.length;
  clickChoice("Keep the SM editor"); await flush();
  const formRetry = since(i0, "/pulse-class/form")[0];
  ok(formRetry && formRetry.body.keep === "sm" && formRetry.body.file_sha === "form-outside" &&
    formRetry.body.fields[0].default === 0.65 && since(i0, "/pulse-class/save").length === 0,
    "F5: keeping SM retries the form values rather than the pane");
  ok(server.fields[0].default === 0.65 && amp().value === "0.65", "F5: applied form values redraw after conflict");

  // a hand edit of the template lines makes the form stale too
  root.querySelector("[data-pce-unlock]").click();
  const pr2 = cm.getProtected();
  type(view, pr2.from + BEGIN.length + 5, "Z");
  ok(cm.getDoc().indexOf("Z") > 0, "unlock: a hand edit inside the template lines is allowed");
  i0 = calls.length;
  await win.PulseClassEdit._applyForm(); await flush();
  ok(since(i0, "/pulse-class/form").length === 0 && dialogOpen(), "P1-1: a hand edit of the template lines makes Apply the form ask");
  closeDialog();
  enforceRegion = true;
  await win.PulseClassEdit._save(); await flush();
  ok(calls.filter((c) => c.url === "/pulse-class/save").pop().body.hand_edit === true && server.text === cm.getDoc(),
    "unlock: a save made by hand says so");
  enforceRegion = false;
  root.querySelector("[data-pce-unlock]").click();

  // P2-9: no invented values
  i0 = calls.length;
  amp().value = ""; await win.PulseClassEdit._applyForm(); await flush();
  ok(since(i0, "/pulse-class/form").length === 0 && /empty/.test($("[data-pce-error]").textContent) && amp().getAttribute("aria-invalid") === "true",
    "P2-9: an empty float default is refused, named, not sent as 0");
  amp().value = "1e400"; await win.PulseClassEdit._applyForm(); await flush();
  ok(since(i0, "/pulse-class/form").length === 0 && /amplitude/.test($("[data-pce-error]").textContent) &&
    amp().getAttribute("aria-invalid") === "true", "F8 float: non-finite defaults are named, invalid, never sent");
  amp().value = "0.1";
  const len = () => rows()[1].querySelector('[data-k="default"]');
  len().value = "9007199254740993"; await win.PulseClassEdit._applyForm(); await flush();
  ok(since(i0, "/pulse-class/form").length === 0 && /length/.test($("[data-pce-error]").textContent) &&
    len().getAttribute("aria-invalid") === "true", "F8 int: unsafe integers are named, invalid, never sent");
  len().value = "1.5"; await win.PulseClassEdit._applyForm(); await flush();
  ok(since(i0, "/pulse-class/form").length === 0 && /whole number/.test($("[data-pce-error]").textContent),
    "P2-9: 1.5 for an int is refused, not truncated");
  len().value = "abc"; await win.PulseClassEdit._applyForm(); await flush();
  ok(since(i0, "/pulse-class/form").length === 0, "P2-9: text for an int is refused");
  len().value = "40";
  root.querySelector("[data-pce-add-field]").click();
  const added = rows()[rows().length - 1];
  ok(added.querySelector('[data-k="default"]').value === "", "P2-9: a new field starts empty, not 0");
  await win.PulseClassEdit._applyForm(); await flush();
  ok(since(i0, "/pulse-class/form").length === 0 && /name is empty/.test($("[data-pce-error]").textContent), "P2-9: a nameless field is refused");
  added.querySelector('[data-k="type"]').value = "bool";
  added.querySelector('[data-k="name"]').value = "on";
  added.querySelector('[data-k="default"]').value = "maybe";
  await win.PulseClassEdit._applyForm(); await flush();
  ok(since(i0, "/pulse-class/form").length === 0 && /True or False/.test($("[data-pce-error]").textContent), "P2-9: a bool needs True or False");
  added.querySelector('[data-k="default"]').value = "True";
  await win.PulseClassEdit._applyForm(); await flush();
  const sent = since(i0, "/pulse-class/form");
  ok(sent.length === 1 && sent[0].body.fields[2].default === true && sent[0].body.fields[0].default === 0.1 && sent[0].body.fields[1].default === 40,
    "P2-9: valid values go out typed");

  // P2-4: a keystroke during an in-flight poll is never overwritten
  outsideEdit(F3, "b");
  const askedSha = server.sha;
  hold = { "/pulse-class/poll": deferred() };
  const d = hold["/pulse-class/poll"];
  const baseBefore = P.baseSha;
  const pp = win.PulseClassEdit._poll(); await flush(2);
  type(view, 0, "K");
  const typed = cm.getDoc();
  d.res(); await pp; await flush();
  ok(cm.getDoc() === typed, "P2-4: the keystroke made during the poll stays");
  ok(P.baseSha === baseBefore, "P2-4: the base does not move to a file the pane has not adopted");
  ok(dialogOpen() && $("[data-pce-conflict-title]").textContent === "Two editors changed the draft", "P2-4: the user is asked");
  i0 = calls.length;
  clickChoice("Keep the SM editor"); await flush();
  const ks = since(i0, "/pulse-class/save")[0];
  ok(ks && ks.body.keep === "sm" && ks.body.file_sha === askedSha && ks.body.text === typed,
    "P2-1 (client): the choice carries the file sha the question was about");

  // P2-4 (save side): typing while a save is out keeps the keystroke
  type(view, 0, "M");
  hold = { "/pulse-class/save": deferred() };
  const d2 = hold["/pulse-class/save"];
  const sp = win.PulseClassEdit._save(); await flush(2);
  type(view, 0, "N");
  const typed2 = cm.getDoc();
  d2.res(); await sp; await flush();
  ok(cm.getDoc() === typed2 && vis("[data-pce-dirty]"), "P2-4: a keystroke made during a save stays, still unsaved");

  // Merge seam: locked-region 400 preserves the user's text and server words.
  cm.setGuard(false);
  type(view, cm.getDoc().indexOf(BEGIN) + BEGIN.length + 1, "    # hand\n");
  const refusedText = cm.getDoc(), diskBefore = server.text;
  enforceRegion = true;
  await win.PulseClassEdit._save(); await flush();
  ok($("[data-pce-status]").textContent === REGION_ERROR, "merge region: 400 words appear in the status line");
  ok(cm.getDoc() === refusedText && server.text === diskBefore && vis("[data-pce-dirty]"),
    "merge region: refused save preserves pane text and disk");
  enforceRegion = false;

  // Merge seam: save 409 has only conflict.file_sha, and a moved choice asks again.
  const c1 = conflict("first"), c2 = conflict("second", "D:/draft/new.py");
  answer("/pulse-class/save", 409, { ok: false, conflict: c1 });
  await win.PulseClassEdit._save(); await flush();
  ok(dialogOpen() && $("[data-pce-conflict-question]").textContent === c1.question, "merge conflict: save 409 opens its question");
  answer("/pulse-class/save", 409, { ok: false, conflict: c2, moved: true });
  clickChoice("Keep the SM editor"); await flush();
  ok(calls[calls.length - 1].body.file_sha === "first", "merge conflict: save choice sends nested file_sha");
  ok(dialogOpen() && $("[data-pce-conflict-losses]").textContent.includes("D:/draft/new.py"),
    "merge conflict: moved 409 reopens with the new facts");
  answer("/pulse-class/save", 400, { ok: false, error: WRITING_ERROR });
  clickChoice("Keep the file on disk"); await flush();
  ok(calls[calls.length - 1].body.keep === "file" && calls[calls.length - 1].body.file_sha === "second",
    "merge conflict: keep file sends the refreshed hash");
  ok(dialogOpen() && $("[data-pce-error]").textContent === WRITING_ERROR && cm.getDoc() === refusedText,
    "merge conflict: still being written shows the error and keeps the question and pane");
  i0 = calls.length;
  await win.PulseClassEdit._poll(); await flush();
  ok(calls.length === i0, "merge conflict: polling stays paused while the question is open");
  closeDialog();

  // F2: an expired base names the hand-edit control and ends the question.
  answer("/pulse-class/save", 409, { ok: false, conflict: c1 });
  await win.PulseClassEdit._save(); await flush();
  const unknown = "The template base is no longer known. To save these lines, press \"Edit the template lines by hand\" first, then Save draft.";
  const watchSource = read("quam_state_manager/core/pulse_class_watch.py");
  const unknownReply = /"error": "(The template base is no longer known\.[^\n]*)"\s*\n\s*"((?:\\.|[^"\\])*)"/.exec(watchSource);
  ok(unknownReply && unknownReply[1] + JSON.parse('"' + unknownReply[2] + '"') === unknown &&
    $("[data-pce-unlock]").textContent.trim() === "Edit the template lines by hand",
    "unknown base: server message names the page's hand-edit control");
  answer("/pulse-class/save", 409, { ok: false, base_unknown: true, error: unknown });
  clickChoice("Keep the SM editor"); await flush();
  ok(!dialogOpen() && $("[data-pce-error]").textContent === unknown && cm.getDoc() === refusedText,
    "F2: unknown base does not reopen the conflict dialog or discard typing");
  ok($("[data-pce-status]").textContent === unknown,
    "unknown base: status shows the instruction for the existing control");
  answer("/pulse-class/form", 409, { ok: false, conflict: c1 });
  await win.PulseClassEdit._applyForm(true); await flush();
  answer("/pulse-class/form", 409, { ok: false, base_unknown: true, error: unknown });
  ok(clickChoice("Keep the SM editor"), "unknown base: the form offers its conflict choice");
  await flush();
  ok(!dialogOpen() && $("[data-pce-error]").textContent === unknown && cm.getDoc() === refusedText,
    "F2 form: unknown base ends the dialog, shows the instruction, and keeps typing");

  // Merge seam: overwrite confirmation may move repeatedly; target is the asked path.
  const targetBox = $("[data-pce-target]"), firstConfirm = confirm("target-one"), nextConfirm = confirm("target-two");
  targetBox.value = firstConfirm.path;
  answer("/pulse-class/save-file", 409, { ok: false, confirm: firstConfirm });
  $("[data-pce-save-file]").click(); await flush();
  ok(dialogOpen() && $("[data-pce-conflict-question]").textContent.includes(firstConfirm.question) && $("[data-pce-conflict-question]").textContent.includes(firstConfirm.path) &&
    $("[data-pce-conflict-losses]").textContent.includes(firstConfirm.loses),
    "merge overwrite: kit question names the path and loss");
  ok($("[data-pce-conflict-question]").querySelectorAll("br").length >= 1,
    "F7: confirmation question and path render on separate lines");
  ok(/saved draft, not the unsaved pane/.test($("[data-pce-file-note]").textContent) &&
    /saved draft, not the unsaved pane/.test($("[data-pce-conflict-question]").textContent),
    "F6: dirty pane save explains which text will be written before confirmation");
  i0 = calls.length;
  clickChoice("No"); await flush();
  ok(!dialogOpen() && calls.length === i0, "merge overwrite: No writes nothing");
  answer("/pulse-class/save-file", 409, { ok: false, confirm: firstConfirm });
  $("[data-pce-save-file]").click(); await flush();
  targetBox.value = "D:/classes/other.py";
  answer("/pulse-class/save-file", 409, { ok: false, confirm: nextConfirm });
  clickChoice("Yes"); await flush();
  ok(calls[calls.length - 1].body.replace_sha === "target-one" && calls[calls.length - 1].body.target === firstConfirm.path,
    "merge overwrite: Yes sends replace_sha for the asked path");
  ok(dialogOpen() && $("[data-pce-conflict-question]").textContent.includes(nextConfirm.path),
    "merge overwrite: second 409 asks again");
  answer("/pulse-class/save-file", 200, { ok: true, status: "Saved to D:/classes/flat.py, same as draft",
    backup: "D:/classes/flat.py.bak", target: firstConfirm.path });
  clickChoice("Yes"); await flush();
  ok(calls[calls.length - 1].body.replace_sha === "target-two", "merge overwrite: second Yes sends the new hash");
  ok(!dialogOpen() && $("[data-pce-file-note]").textContent.includes("D:/classes/flat.py.bak"), "merge overwrite: success names backup path");
  answer("/pulse-class/poll", 200, { status: "unchanged", status_line: "Saved to D:/classes/flat.py, same as draft" });
  await win.PulseClassEdit._poll(); await flush();
  await win.PulseClassEdit._poll(); await flush();
  ok(vis("[data-pce-file-note]") && $("[data-pce-file-note]").textContent.includes("D:/classes/flat.py.bak"),
    "F1: backup path persists through repeated polls after overwrite success");
  ok(/saved draft was written, not the unsaved pane/.test($("[data-pce-file-note]").textContent),
    "F6: dirty pane success explains that the saved draft was written");
  answer("/pulse-class/save-file", 200, { ok: true, status: "Saved to D:/classes/flat.py, same as draft", backup: null, target: firstConfirm.path });
  $("[data-pce-save-file]").click(); await flush();
  ok($("[data-pce-file-note]").textContent.includes("Saved; no earlier file to back up."), "F7: first save uses plain no-earlier-file wording");

  // Save to file shows that it works: busy while the request is out, one request per press, normal again on every outcome.
  const sf = $("[data-pce-save-file]");
  const idle = (b) => !b.hasAttribute("aria-busy") && !b.disabled;
  ok(idle(sf), "busy: Save to file is idle before a press");
  hold = { "/pulse-class/save-file": deferred() };
  const dSave = hold["/pulse-class/save-file"];
  i0 = calls.length;
  sf.click(); sf.click(); await flush();
  ok(sf.getAttribute("aria-busy") === "true" && sf.disabled, "busy: Save to file is busy and disabled while the request is out");
  win.PulseClassEdit._saveFile(firstConfirm.path); await flush();
  answer("/pulse-class/save-file", 200, { ok: true, status: "Saved", backup: null, target: firstConfirm.path });
  dSave.res(); await flush();
  ok(idle(sf) && since(i0, "/pulse-class/save-file").length === 1, "busy: one request for three presses, and success returns the button to normal");
  answer("/pulse-class/save-file", 409, { ok: false, error: "scheduler_running", message: "Locked while a run is in progress." });
  sf.click(); await flush();
  ok(idle(sf), "busy: a refusal returns the button to normal");
  answer("/pulse-class/save-file", 409, { ok: false, confirm: firstConfirm });
  sf.click(); await flush();
  ok(idle(sf) && dialogOpen(), "busy: the replace question leaves the button normal while the dialog asks");
  clickChoice("No"); await flush();
  answer("/pulse-class/save-file", 500, { ok: false, error: "The server failed." });
  sf.click(); await flush();
  ok(idle(sf), "busy: a server error returns the button to normal");
  // Ratchet: every request button of the page ends idle when fetch rejects or a body throws.
  const requestButtons = ["[data-pce-save]", "[data-pce-apply-form]", "[data-pce-save-file]", "[data-pce-save-config]"];
  const unhandled = [];
  const onUnhandled = (e) => unhandled.push(String(e));
  process.on("unhandledRejection", onUnhandled);
  for (const mode of ["reject", "bad-json", "null-body"]) {
    for (const sel of requestButtons) {
      const b = $(sel);
      const before = b.disabled;
      win.__fetchMode = mode;
      b.click(); await flush();
      win.__fetchMode = "";
      ok(!b.hasAttribute("aria-busy") && b.disabled === before, "busy ratchet: " + sel + " ends not busy after " + mode);
      if (sel === "[data-pce-save-file]" && mode === "null-body") {
        const err = $("[data-pce-error]");
        ok(!err.hidden && err.textContent === "The server sent no answer.",
          "a 200 with a null body tells Save to file the server sent no answer");
      }
    }
  }
  win.__fetchMode = "";
  if (dialogOpen()) closeDialog();
  // a handler that throws after the answer: the button still ends idle
  const stat = $("[data-pce-status]"), statParent = stat.parentNode, statNext = stat.nextSibling;
  stat.remove();
  answer("/pulse-class/save-file", 200, { ok: true, status: "Saved", backup: null, target: firstConfirm.path });
  sf.click(); await flush();
  statParent.insertBefore(stat, statNext);
  ok(idle(sf) && vis("[data-pce-error]") && /failed/.test($("[data-pce-error]").textContent), "busy: a throwing handler ends idle with a message");
  win.__fetchMode = "reject";
  sf.click(); await flush();
  win.__fetchMode = "";
  ok(vis("[data-pce-error]"), "busy: a failed request shows an honest message");
  process.off("unhandledRejection", onUnhandled);
  ok(unhandled.length === 0, "busy: no unhandled rejection (" + unhandled.join("; ") + ")");

  await win.PulseClassEdit._save(); await flush();
  for (const message of ["Class saving is locked while a run is in progress; try again when it ends. <i>Wait</i>", null]) {
    answer("/pulse-class/save-config", 409, Object.assign({ ok: false, error: "scheduler_running" }, message ? { message } : {}));
    $("[data-pce-save-config]").click(); await flush();
    ok($("[data-pce-error]").textContent === (message || "scheduler_running") && !$("[data-pce-error]").querySelector("i"),
      "class save lock shows the message as text or the existing error fallback");
  }

  // Merge seam: missing poll status_line is displayed verbatim.
  const gone = "The draft file is gone: D:/draft/FlatTop.py";
  answer("/pulse-class/poll", 200, { status: "missing", status_line: gone });
  await win.PulseClassEdit._poll(); await flush();
  ok($("[data-pce-status]").textContent === gone, "merge missing: status_line is shown as is");

  // P2-11 / P3: layout and colour tokens
  const css = fs.readFileSync(path.join(STATIC, "ui-kit.css"), "utf8");
  const tok = (name) => { const m = new RegExp("--" + name + ":\\s*([^;]+);").exec(css); return m && m[1].trim(); };
  ok(/\.pce-code-col \{ flex: 3 1 var\(--ui-pce-code-min\); \}/.test(css), "P2-11: the code column grows three times the others");
  ok(/\.pce-preview \{ flex: 1 1 var\(--ui-pce-preview-min\); \}/.test(css), "P2-11: the preview column has its own narrower basis");
  ok(parseInt(tok("ui-pce-code-min"), 10) > parseInt(tok("ui-pce-preview-min"), 10), "P2-11: code basis wider than preview basis");
  ok(root.querySelector("[data-pce-code] .cm-content.cm-lineWrapping"), "P2-11: long code lines wrap");
  ok(/\.pce-field-row \{ display: grid; grid-template-columns: var\(--ui-pce-field-cols\);/.test(css), "P2-10: field rows are one grid row each");
  const entry = read("tools/cm6/entry-python.js");
  ok(!/#[0-9a-fA-F]{3,8}\b/.test(entry), "P3: entry-python.js carries no hex colour");
  const used = Array.from(new Set(Array.from(entry.matchAll(/v\("(--ui-[a-z0-9-]+)"\)/g), (m) => m[1])));
  const missing = used.filter((u) => !tok(u.slice(2)));
  ok(used.length > 10 && missing.length === 0, "P3: every token the editor uses is defined in ui-kit.css (" + missing.join(" ") + ")");
  const target = root.querySelector("[data-pce-target]");
  ok(!target.hasAttribute("placeholder") && doc.getElementById(target.getAttribute("aria-describedby")).classList.contains("ui-field-hint"),
    "P3: the class file box has a hint line, not a placeholder");

  win.PulseClassEdit.state() && root.remove();
  console.log((bad ? "FAILED " : "ok ") + "(" + n + " assertions)");
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.log("FAIL crash " + (e && e.stack || e)); process.exit(1); });
