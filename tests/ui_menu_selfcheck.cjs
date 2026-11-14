/* docs/308 section 4b: the kit menu's behaviour (static/ui.js, UI.menu).
 * Runs the shipped ui.js in a jsdom window realm, builds one picking menu
 * (listbox) and one action menu (menu) with UI.menu, and drives them by
 * keyboard and mouse: open keys, arrows, Home / End, type-ahead, Enter,
 * Escape and focus return, outside click, disabled rows never picked, the
 * one pick event and its value, roles and aria-expanded for both kinds,
 * and the upward flip when there is no room below. */
const fs = require("fs");
const path = require("path");
let JSDOM;
try { ({ JSDOM } = require("jsdom")); } catch (e) { console.log("SKIP jsdom not installed"); process.exit(2); }

const STATIC = path.join(__dirname, "..", "quam_state_manager", "web", "static");
const uiJs = fs.readFileSync(path.join(STATIC, "ui.js"), "utf8");
const menuJs = fs.readFileSync(path.join(STATIC, "ui-menu.js"), "utf8");
const dom = new JSDOM('<!doctype html><html><body><button id="before">before</button><div id="host"></div><p id="outside">outside</p></body></html>',
                      { url: "http://localhost/ui-kit", runScripts: "outside-only" });
const { window } = dom;
window.eval(menuJs);
window.eval(uiJs);
window.eval(menuJs);   // a second evaluation must not add a second set of listeners
const doc = window.document;

let fails = 0, asserts = 0;
function ok(cond, msg) { asserts++; if (!cond) { fails++; console.log("FAIL " + msg); } else console.log("ok - " + msg); }

const PAIRS = [
    { value: "a", title: "Alpha", desc: "first" },
    { value: "b", title: "Bravo" },
    { value: "c", title: "Charlie", disabled: true, reason: "No line on this qubit" },
    { value: "d", title: "Delta" },
];
const host = doc.getElementById("host");
const pick = window.UI.menu({ id: "m1", label: "Pair", items: PAIRS, placeholder: "Choose a pair" });
const act = window.UI.menu({ id: "m2", kind: "action", label: "More", trigger: { label: "Actions" },
                             items: [{ value: "dup", title: "Duplicate" }, { sep: true },
                                     { value: "rm", title: "Remove", disabled: true, reason: "In use" }, { value: "ren", title: "Rename" }, { title: "No value" }] });
host.appendChild(pick);
host.appendChild(act);

const events = [];
doc.addEventListener("ui-menu-pick", (e) => events.push(e.detail));

const T = (m) => m.querySelector(".ui-menu-trigger");
const L = (m) => m.querySelector(".ui-menu-list");
const rows = (m) => [...m.querySelectorAll(".ui-option")];
const isOpen = (m) => !L(m).hasAttribute("hidden");
const act_ = () => doc.activeElement;
function key(k, target) {
    const t = target || act_();
    t.dispatchEvent(new window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
}
function click(n) { n.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true })); }

// roles and initial state, both kinds
ok(T(pick).getAttribute("aria-haspopup") === "listbox" && L(pick).getAttribute("role") === "listbox", "pick: trigger has listbox popup, list is a listbox");
ok(rows(pick).every((r) => r.getAttribute("role") === "option" && r.hasAttribute("aria-selected")), "pick: rows are options with aria-selected");
ok(T(act).getAttribute("aria-haspopup") === "menu" && L(act).getAttribute("role") === "menu", "action: trigger has menu popup, list is a menu");
ok(rows(act).every((r) => r.getAttribute("role") === "menuitem" && !r.hasAttribute("aria-selected")), "action: rows are menuitems without aria-selected");
ok(T(pick).getAttribute("aria-expanded") === "false" && !isOpen(pick), "pick starts closed, aria-expanded false");
ok(T(pick).getAttribute("aria-controls") === L(pick).id, "trigger controls its list");
ok(rows(pick)[2].querySelector(".ui-option-desc").textContent === "No line on this qubit", "a disabled row shows its reason as the second line");
ok(T(pick).querySelector(".ui-menu-placeholder").textContent === "Choose a pair", "nothing chosen: the trigger shows the placeholder");

// keyboard journey: focus trigger, ArrowDown opens on the first enabled row, ArrowDown, Enter picks the second enabled row
T(pick).focus();
key("ArrowDown");
ok(isOpen(pick) && T(pick).getAttribute("aria-expanded") === "true", "ArrowDown on the trigger opens, aria-expanded true");
ok(act_() === rows(pick)[0], "focus lands on the first enabled row");
key("ArrowDown");
ok(act_() === rows(pick)[1], "ArrowDown moves to the next row");
key("Enter");
ok(!isOpen(pick) && T(pick).getAttribute("aria-expanded") === "false", "Enter picks and closes");
ok(act_() === T(pick), "focus returns to the trigger after a pick");
ok(T(pick).querySelector(".ui-menu-value").textContent === "Bravo", "the trigger shows the picked title");
ok(!T(pick).querySelector(".ui-menu-placeholder"), "the placeholder look is gone after a pick");
ok(rows(pick)[1].getAttribute("aria-selected") === "true" && rows(pick).filter((r) => r.getAttribute("aria-selected") === "true").length === 1, "exactly the picked row is aria-selected");
ok(events.length === 1 && events[0].value === "b" && events[0].kind === "pick" && events[0].title === "Bravo", "one pick event carries value b");
ok(pick.getAttribute("data-value") === "b", "the menu root records the value");

// reopen: focus on the selected row; disabled rows are skipped; End / Home
key("Enter", T(pick));
ok(isOpen(pick) && act_() === rows(pick)[1], "Enter on the trigger opens on the selected row");
key("ArrowDown");
ok(act_() === rows(pick)[3], "ArrowDown skips the disabled row");
key("ArrowDown");
ok(act_() === rows(pick)[3], "ArrowDown stops at the last row");
key("Home");
ok(act_() === rows(pick)[0], "Home goes to the first row");
key("End");
ok(act_() === rows(pick)[3], "End goes to the last enabled row");
key("ArrowUp"); key("ArrowUp");
ok(act_() === rows(pick)[0], "ArrowUp skips the disabled row too");
// type-ahead
key("d");
ok(act_() === rows(pick)[3], "typing a letter jumps to the row starting with it");
key("c");
ok(act_() === rows(pick)[3], "typing the letter of a disabled row does not land on it");
// Escape
key("Escape");
ok(!isOpen(pick) && act_() === T(pick), "Escape closes and returns focus to the trigger");
ok(events.length === 1, "Escape fires no pick event");

// disabled row: neither click nor Enter picks it
key("ArrowDown", T(pick));
rows(pick)[2].focus();
key("Enter");
ok(isOpen(pick) && events.length === 1 && pick.getAttribute("data-value") === "b", "Enter on a disabled row picks nothing");
click(rows(pick)[2]);
ok(isOpen(pick) && events.length === 1 && rows(pick)[2].getAttribute("aria-selected") === "false", "a click on a disabled row picks nothing");

// outside click closes
click(doc.getElementById("outside"));
ok(!isOpen(pick) && T(pick).getAttribute("aria-expanded") === "false", "an outside click closes the menu");

// mouse: click the trigger opens, a row click picks
click(T(pick));
ok(isOpen(pick), "a click on the trigger opens");
click(T(pick));
ok(!isOpen(pick), "a second click on the trigger closes");
click(T(pick));
click(rows(pick)[3].querySelector(".ui-option-title"));
ok(!isOpen(pick) && events.length === 2 && events[1].value === "d" && T(pick).querySelector(".ui-menu-value").textContent === "Delta", "a click on a row (on its title) picks it");

// Space and ArrowUp open too
T(pick).focus();
key(" ");
ok(isOpen(pick) && act_() === rows(pick)[3], "Space on the trigger opens on the selected row");
key("Escape");
key("ArrowUp");
ok(isOpen(pick) && act_() === rows(pick)[3], "ArrowUp on the trigger opens on the last enabled row");
key("Escape");

// only one menu open at a time
click(T(pick));
click(T(act));
ok(!isOpen(pick) && isOpen(act), "opening one menu closes the other");

// action menu: pick fires the event, no selection state, trigger text unchanged
ok(act_() === rows(act)[0], "action menu opens on its first item");
key("ArrowDown");
ok(act_() === rows(act)[2], "action menu skips the separator and the disabled item");
key("Enter");
ok(events.length === 3 && events[2].value === "ren" && events[2].kind === "action", "an action pick fires the event with its value");
ok(T(act).textContent === "Actions" && !act.hasAttribute("data-value") && act_() === T(act), "an action keeps its trigger label and returns focus");

// Tab inside the list closes without trapping
click(T(act));
key("Tab");
ok(!isOpen(act), "Tab inside the list closes it");

// upward when there is no room below
window.innerHeight = 300;
T(pick).getBoundingClientRect = () => ({ top: 260, bottom: 286, left: 0, right: 100, width: 100, height: 26 });
Object.defineProperty(L(pick), "offsetHeight", { configurable: true, get: () => 120 });
click(T(pick));
ok(L(pick).classList.contains("ui-menu-list--up"), "no room below: the list opens upward");
key("Escape");
ok(!L(pick).classList.contains("ui-menu-list--up"), "the upward flip is cleared on close");
T(pick).getBoundingClientRect = () => ({ top: 10, bottom: 36, left: 0, right: 100, width: 100, height: 26 });
click(T(pick));
ok(!L(pick).classList.contains("ui-menu-list--up"), "room below: the list opens downward");
key("Escape");

// accessible name: label + value (aria-labelledby, both ids resolve)
const ids = T(pick).getAttribute("aria-labelledby").split(" ");
const nameParts = ids.map((i) => doc.getElementById(i) && doc.getElementById(i).textContent);
ok(!T(pick).hasAttribute("aria-label") && nameParts[0] === "Pair" && nameParts[1] === "Delta", "the trigger's name is the label and the chosen value: " + nameParts.join(" "));
ok(!rows(act).some((r) => r.getAttribute("data-value") === "undefined"), "an action item without a value has no data-value 'undefined'");

// toolbar: Left / Right / Home / End move between its controls
const tb = window.UI.toolbar([{ part: "button", label: "A", cls: "btn-sm" }, { part: "check", text: "B" },
                              { part: "select", label: "S", options: ["x", "y"] }, { part: "button", label: "D", cls: "btn-sm" }], "Tools");
host.appendChild(tb);
const tbb = tb.querySelectorAll("button, input, select");
tbb[0].focus(); key("ArrowRight");
ok(act_() === tbb[1], "toolbar: ArrowRight moves to the next control");
key("End");
ok(act_() === tbb[3], "toolbar: End goes to the last control");
key("Home"); key("ArrowLeft");
ok(act_() === tbb[0], "toolbar: Home, then ArrowLeft stays on the first");
tbb[2].focus(); key("ArrowRight");
ok(act_() === tbb[2], "toolbar: a select keeps its own arrow keys");

// a radio inside a toolbar keeps its own arrow keys
const rtb = window.UI.toolbar([{ part: "button", label: "A", cls: "btn-sm" }], "Radios");
["r1", "r2"].forEach((v) => { const i = doc.createElement("input"); i.type = "radio"; i.name = "rg"; i.value = v; rtb.appendChild(i); });
host.appendChild(rtb);
const r1 = rtb.querySelector('input[value="r1"]');
r1.focus();
const ev = new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true });
r1.dispatchEvent(ev);
ok(!ev.defaultPrevented && act_() === r1, "toolbar: a radio keeps its own arrow keys (the toolbar does not take ArrowRight)");

// a field without a control id: generated id, label for, described by hint + error
const f1 = window.UI.field({ label: "Amplitude", hint: "Volts at the port", error: "Too high", control: { part: "input", num: true, value: "3" } });
host.appendChild(f1);
const inp = f1.querySelector("input"), lab = f1.querySelector("label.ui-field-label");
ok(inp.id && lab.getAttribute("for") === inp.id && !inp.hasAttribute("aria-label"), "field without id: the input gets an id and <label for> names it");
const desc = (inp.getAttribute("aria-describedby") || "").split(" ").map((i) => doc.getElementById(i) && doc.getElementById(i).textContent);
ok(desc.join("|") === "Volts at the port|Too high" && !lab.contains(f1.querySelector(".ui-field-hint")), "hint and error describe the input; they are not in its label");
ok(lab.control === inp, "the label's control is the input (a label click focuses it in a browser)");
// a group control: named by the label text, a click on it does nothing
const f2 = window.UI.field({ label: "Pair", control: { part: "menu", items: PAIRS } });
host.appendChild(f2);
const mt = f2.querySelector(".ui-menu-trigger"), ltext = f2.querySelector(".ui-field-label");
ok(ltext.tagName === "SPAN" && !f2.querySelector("label"), "group field: the label is text, not a <label>");
const mname = mt.getAttribute("aria-labelledby").split(" ").map((i) => doc.getElementById(i) && doc.getElementById(i).textContent);
ok(mname[0] === "Pair", "the menu's name starts with the field label: " + mname.join(" "));
ltext.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
ok(mt.getAttribute("aria-expanded") === "false", "a click on the label text does not open the menu");
const f3 = window.UI.field({ label: "Shape", control: { part: "segmented", options: [{ label: "A" }, { label: "B" }] } });
const sg = f3.querySelector(".ui-segmented");
ok(sg.getAttribute("aria-labelledby") === f3.querySelector(".ui-field-label").id && !sg.hasAttribute("aria-label"), "a segmented field is named by the label text");
// a check field: the check takes the id, the label points at it
const f4 = window.UI.field({ label: "Show", hint: "h", control: { part: "check", text: "Spectators" } });
host.appendChild(f4);
const cb = f4.querySelector("input[type=checkbox]"), l4 = f4.querySelector("label.ui-field-label");
ok(cb.id && l4.getAttribute("for") === cb.id && l4.control === cb, "check field: <label for> names the checkbox");
ok(cb.getAttribute("aria-describedby") === cb.id + "-hint", "check field: described by its hint");
// an unknown part: no for, a role=group wrapper named by the label text
const f5 = window.UI.field({ label: "Source", control: { part: "chip", text: "state.json" } });
host.appendChild(f5);
const w5 = f5.querySelector(".ui-field-control");
ok(!f5.querySelector("[for]") && w5 && w5.getAttribute("role") === "group" && w5.getAttribute("aria-labelledby") === f5.querySelector(".ui-field-label").id, "unknown part: no for, a named group wrapper");
// caller ids are merged first, without duplicates
const f6 = window.UI.field({ label: "Len", hint: "h", control: { part: "input", describedby: "mine mine", id: "len6" } });
ok(f6.querySelector("input").getAttribute("aria-describedby") === "mine len6-hint", "caller describedby first, then the hint: " + f6.querySelector("input").getAttribute("aria-describedby"));
const f7 = window.UI.field({ label: "Pair", control: { part: "segmented", labelledby: "pre", options: [{ label: "A" }] } });
ok(/^pre uif-js[0-9]+-flabel$/.test(f7.querySelector(".ui-segmented").getAttribute("aria-labelledby")), "caller labelledby first, then the label text");

// a field spec's own id wins; a whitespace id and a control without a part are refused
const f8 = window.UI.field({ id: "X", label: "A", control: { part: "input" } });
ok(f8.querySelector("label").getAttribute("for") === "X" && f8.querySelector("input").id === "X", "a field spec's id names the control");
let e1 = "", e2 = "";
try { window.UI.field({ label: "A", control: { part: "input", id: "a b" } }); } catch (e) { e1 = e.message; }
try { window.UI.field({ label: "A", control: { id: "q" } }); } catch (e) { e2 = e.message; }
ok(e1 === 'ui menu id must be a non-empty string without whitespace: "a b"', "a field id with whitespace is refused: " + e1);
ok(e2 === "ui field control needs a part", "a field control without a part is refused: " + e2);

// ids with whitespace are refused
let threw = "";
try { window.UI.menu({ id: "a b", label: "X", items: [] }); } catch (e) { threw = e.message; }
ok(threw === 'ui menu id must be a non-empty string without whitespace: "a b"', "a menu id with whitespace is refused: " + threw);

// no inline handlers in what UI.menu builds
ok(![...host.querySelectorAll("*")].some((n) => [...n.attributes].some((a) => a.name.startsWith("on"))), "no inline on* handlers");

console.log((fails ? "FAIL" : "ok") + " ui_menu_selfcheck (" + asserts + " assertions) " + fails + " failed");
process.exit(fails ? 1 : 0);
