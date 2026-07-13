// Node selfcheck for compare-hub.js — pins the URL-canonical basket
// mechanics that only a harness can catch (both were real review findings):
//   1. pushState happens BEFORE the in-flight gate, so adds chained while a
//      reload is still in flight accumulate (multi-folder drop lost middle
//      sources without this).
//   2. removeAt addresses rows by TOKEN — a second fast × click carries a
//      stale positional index and used to remove the WRONG source.
//   3. The init() strictness one-shot uses replaceState (pushState made
//      Back bounce between preset-less and preset-ful entries forever).
// Run: node tests/compare_hub_selfcheck.cjs   (driven by test_compare_hub_js.py)
"use strict";
const fs = require("fs");
const path = require("path");

let fails = 0;
function ok(cond, msg) {
    if (cond) { console.log("  ok  " + msg); }
    else { console.error("FAIL  " + msg); fails += 1; }
}

// ── minimal browser stubs ────────────────────────────────────────────
const loc = { pathname: "/compare-hub", search: "" };
const historyLog = [];
const history = {
    pushState(_s, _t, url) {
        historyLog.push(["push", url]);
        const q = url.indexOf("?");
        loc.search = q >= 0 ? url.slice(q) : "";
    },
    replaceState(_s, _t, url) {
        historyLog.push(["replace", url]);
        const q = url.indexOf("?");
        loc.search = q >= 0 ? url.slice(q) : "";
    },
};

// htmx.ajax stub: returns promises we resolve manually to simulate slow GETs
const pendingAjax = [];
const htmx = {
    ajax(_verb, url) {
        let resolve;
        const p = new Promise((res) => { resolve = res; });
        pendingAjax.push({ url, resolve });
        return p;
    },
};

function makeEl(overrides) {
    return Object.assign({
        dataset: {}, classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
        querySelector() { return null; }, querySelectorAll() { return { forEach() {} }; },
        appendChild() {}, closest() { return null; },
        getAttribute() { return null; }, setAttribute() {},
    }, overrides || {});
}
const rootEl = makeEl({
    id: "cmp-hub-root",
    dataset: { bucket: "1", preset: "lab", ref: "0", sources: "2" },
    _rows: [],
    querySelector(sel) {
        const m = /data-src-idx="(\d+)"/.exec(sel);
        if (m) return this._rows.find(r => r.dataset.srcIdx === m[1]) || null;
        return null;
    },
    querySelectorAll() { return { forEach() {} }; },
});
const document = {
    getElementById(id) { return id === "cmp-hub-root" ? rootEl : null; },
    addEventListener() {}, createElement() { return makeEl(); },
    body: makeEl({ id: "body" }),
};
const localStorageStore = {};
const localStorage = {
    getItem(k) { return Object.prototype.hasOwnProperty.call(localStorageStore, k) ? localStorageStore[k] : null; },
    setItem(k, v) { localStorageStore[k] = String(v); },
    removeItem(k) { delete localStorageStore[k]; },
};
const window = { location: loc, addEventListener() {} };

// eval the real file with our stubs in scope
const SRC = fs.readFileSync(
    path.join(__dirname, "..", "quam_state_manager", "web", "static", "compare-hub.js"),
    "utf8");
new Function("window", "document", "history", "localStorage", "htmx",
    "location", "setTimeout", SRC)(
    window, document, history, localStorage, htmx, loc, setTimeout);
const cmpHub = window.cmpHub;
ok(!!cmpHub, "cmpHub exported");

// ── 1. chained adds while a reload is in flight ──────────────────────
loc.search = "";
historyLog.length = 0;
cmpHub.add("ws:/a");            // reload #1 → ajax in flight
cmpHub.add("ws:/b");            // queued — must still push its URL
cmpHub.add("ws:/c");            // queued — must see /b in location.search
const lastPush = historyLog[historyLog.length - 1][1];
const srcs = new URLSearchParams(lastPush.split("?")[1] || "").getAll("src");
ok(srcs.length === 3 && srcs.join(",") === "ws:/a,ws:/b,ws:/c",
    "3 chained adds accumulate in the pushed URL (got [" + srcs.join(",") + "])");
// drain the ajax queue (each done() may issue the pending URL)
while (pendingAjax.length) pendingAjax.shift().resolve();

// ── 2. removeAt by token with a stale DOM ────────────────────────────
function makeRow(srcIdx, ref, validIdx) {
    const row = makeEl({ dataset: { srcIdx: String(srcIdx), ref: ref, validIdx: String(validIdx) } });
    const btn = makeEl({ closest: (sel) => (sel === ".cmp-src-row" ? row : null) });
    return { row, btn };
}
return_check: {
    loc.pathname = "/compare-hub";
    loc.search = "?src=ws:/a&src=ws:/b&src=ws:/c&ref=0";
    const A = makeRow(0, "ws:/a", 0), B = makeRow(1, "ws:/b", 1);
    rootEl._rows = [A.row, B.row];
    cmpHub.removeAt(A.btn);          // removes a; URL now [b, c]
    // stale DOM: user clicks × on row "b" which still says src-idx=1,
    // but in the NEW list b sits at index 0 — token addressing must win
    cmpHub.removeAt(B.btn);
    const left = new URLSearchParams(loc.search).getAll("src");
    ok(left.length === 1 && left[0] === "ws:/c",
        "fast double-remove removes the CLICKED rows (left: [" + left.join(",") + "])");
    while (pendingAjax.length) pendingAjax.shift().resolve();
}

// ── 3. strictness one-shot replaces, never pushes ────────────────────
localStorage.setItem("quam_cmp_strictness", "exact");
loc.search = "?src=ws:/a&src=ws:/b&bucket=1";
rootEl.dataset.preset = "lab";
rootEl.dataset.bucket = "1";
rootEl.dataset.sources = "2";
historyLog.length = 0;
cmpHub._rescan();
const strictOps = historyLog.filter(([op, url]) => url.includes("preset=exact"));
ok(strictOps.length >= 1 && strictOps.every(([op]) => op === "replace"),
    "strictness auto-apply uses replaceState only (" +
    strictOps.map(([op]) => op).join(",") + ")");
while (pendingAjax.length) pendingAjax.shift().resolve();

// ── 4. basket edits drop hint (U1b) ──────────────────────────────────
loc.search = "?src=ws:/a&src=ws:/b&hint=1";
historyLog.length = 0;
cmpHub.add("ws:/d");
const afterAdd = historyLog[historyLog.length - 1][1];
ok(!/hint=1/.test(afterAdd), "add() strips hint (manual basket, U1b)");
while (pendingAjax.length) pendingAjax.shift().resolve();

// ── 5. setRef by token with a stale DOM (same class as removeAt) ─────
// The ★ (setRef) used to write the render-time valid-index straight to the
// URL, so a star click during a pending remove/reorder starred the WRONG
// source. Now it resolves by token: fresh click → uses the rendered index;
// stale click (list shifted under the row) → bails, never stars a different
// source.
setref_check: {
    // (a) fresh: token still at its rendered src position → ref moves to it
    loc.pathname = "/compare-hub";
    loc.search = "?src=ws:/a&src=ws:/b&ref=0";
    const A = makeRow(0, "ws:/a", 0), B = makeRow(1, "ws:/b", 1);
    rootEl._rows = [A.row, B.row];
    cmpHub.setRef(B.btn);
    ok(new URLSearchParams(loc.search).get("ref") === "1",
        "setRef on an in-sync row sets ref to that row's valid index");
    while (pendingAjax.length) pendingAjax.shift().resolve();

    // (b) stale: remove A, then a star click on B whose DOM still says
    // src-idx=1 (now ws:/c in the shifted list) must NOT star that source
    loc.search = "?src=ws:/a&src=ws:/b&src=ws:/c&ref=0";
    const A2 = makeRow(0, "ws:/a", 0), B2 = makeRow(1, "ws:/b", 1);
    rootEl._rows = [A2.row, B2.row];
    cmpHub.removeAt(A2.btn);                 // list → [ws:/b, ws:/c]
    while (pendingAjax.length) pendingAjax.shift().resolve();
    const refBefore = new URLSearchParams(loc.search).get("ref");
    cmpHub.setRef(B2.btn);                   // stale row: src-idx=1 → ws:/c ≠ ws:/b
    const refAfter = new URLSearchParams(loc.search).get("ref");
    ok(refAfter === refBefore,
        "stale ★ click bails instead of starring the shifted-in source " +
        "(ref " + refBefore + " → " + refAfter + ")");
    while (pendingAjax.length) pendingAjax.shift().resolve();
}

if (fails) { console.error(fails + " check(s) FAILED"); process.exit(1); }
console.log("compare_hub_selfcheck: all checks passed");
