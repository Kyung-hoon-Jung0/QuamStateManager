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

const uiJs = fs.readFileSync(path.join(__dirname, "..", "quam_state_manager", "web", "static", "ui.js"), "utf8");
const dom = new JSDOM('<!doctype html><html><body><button id="before">before</button><div id="host"></div><p id="outside">outside</p></body></html>',
                      { url: "http://localhost/ui-kit", runScripts: "outside-only" });
const { window } = dom;
window.eval(uiJs);
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
                                     { value: "rm", title: "Remove", disabled: true, reason: "In use" }, { value: "ren", title: "Rename" }] });
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
ok(!isOpen(pick) && events.length === 2 && events[1].value === "d" && T(pick).textContent === "Delta", "a click on a row (on its title) picks it");

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

// no inline handlers in what UI.menu builds
ok(![...host.querySelectorAll("*")].some((n) => [...n.attributes].some((a) => a.name.startsWith("on"))), "no inline on* handlers");

console.log((fails ? "FAIL" : "ok") + " ui_menu_selfcheck (" + asserts + " assertions) " + fails + " failed");
process.exit(fails ? 1 : 0);
