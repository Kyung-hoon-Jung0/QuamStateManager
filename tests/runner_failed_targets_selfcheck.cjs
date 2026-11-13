"use strict";
// A finished runner item whose node marked targets failed: the row says how
// many failed and what was written for them, and offers the revert; an item
// with every target successful renders as before.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
(async () => {
  const dom = new JSDOM('<body><div id="sched-root" class="sched-root"><div id="sched-queue"></div></div></body>',
    { url: "http://localhost/scheduler", runScripts: "outside-only", pretendToBeVisual: true });
  try {
    const w = dom.window;
    const note = "1 of 2 targets failed — q1: still wrote qubits.q1.f_01 (5 -> 6) to the chip";
    const posts = [];
    const failed = { id: "a", name: "node", kind: "node", status: "done", enabled: true,
      failed_targets: ["q1"], n_targets: 2, outcome_note: note,
      failed_writes: [{ path: "/quam/qubits/q1/f_01", dotted: "qubits.q1.f_01", target: "q1" }] };
    const clean = { id: "b", name: "node", kind: "node", status: "done", enabled: true };
    // gl-aat: after a partial revert one value is left; after a full one, none
    const fw3 = ["f_01", "T1", "f_12"].map(k => ({ path: "/quam/qubits/q1/" + k, dotted: "qubits.q1." + k, target: "q1" }));
    const partial = Object.assign({}, failed, { id: "c", failed_writes: fw3, failed_revert: { ok: true,
      reverted: [{ path: "qubits.q1.f_01" }], refused: [{ path: "qubits.q1.T1", code: "changed" }], remaining: ["qubits.q1.f_12"],
      message: "reverted 1: qubits.q1.f_01 — not reverted 1: qubits.q1.T1: the live value changed since the run (now 2.6)" } });
    const full = Object.assign({}, failed, { id: "d", failed_writes: fw3, failed_revert: { ok: true,
      reverted: fw3.map(x => ({ path: x.dotted })), refused: [], remaining: [], message: "reverted 3" } });
    const stuck = Object.assign({}, failed, { id: "e", failed_writes: fw3, failed_revert: { ok: true,
      reverted: [{ path: "qubits.q1.f_01" }], refused: [{ path: "qubits.q1.T1" }, { path: "qubits.q1.f_12" }], remaining: [], message: "x" } });
    w.fetch = async (url, opts) => {
      if (opts && opts.method === "POST") posts.push(String(url));
      return { json: async () => String(url).startsWith("/scheduler/status")
        ? { queue: [failed, clean, partial, full, stuck], run: { status: "idle" } }
        : (String(url).includes("/revert-failed") ? { ok: true, message: "reverted 1: qubits.q1.f_01" } : {}) };
    };
    w.eval(fs.readFileSync(path.join(__dirname, "..", "quam_state_manager", "web", "static", "scheduler.js"), "utf8"));
    w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
    await new Promise(resolve => setTimeout(resolve, 100));
    let checks = 0;
    const row = w.document.querySelector('.sched-item[data-id="a"]');
    const ok = w.document.querySelector('.sched-item[data-id="b"]');
    assert(row && ok, "both items render"); checks++;
    assert.strictEqual(row.querySelector(".sched-item-status").textContent,
      "done — 1 of 2 targets failed"); checks++;
    const warn = row.querySelector('[data-failed-targets="1"]');
    assert(warn && warn.getAttribute("role") === "status" && warn.textContent === note,
      "the failure and its written path are announced"); checks++;
    const btn = row.querySelector('[data-act="revert-failed"]');
    assert(btn && /Revert the 1 value/.test(btn.textContent), "the revert is offered"); checks++;
    assert.strictEqual(ok.querySelector(".sched-item-status").textContent, "done"); checks++;
    assert(!ok.querySelector("[data-failed-targets]") && !ok.querySelector('[data-act="revert-failed"]')
      && !ok.classList.contains("sched-item-capture-warning"), "a clean item is unchanged"); checks++;
    const pb = w.document.querySelector('.sched-item[data-id="c"] [data-act="revert-failed"]');
    assert(pb && pb.textContent === "Revert the 1 remaining value", "a partial revert names what is left: " + (pb && pb.textContent)); checks++;
    assert(!w.document.querySelector('.sched-item[data-id="d"] [data-act="revert-failed"]'), "a full revert leaves no button"); checks++;
    assert(!w.document.querySelector('.sched-item[data-id="e"] [data-act="revert-failed"]'), "nothing revertable left: no button"); checks++;
    const pm = w.document.querySelector('.sched-item[data-id="c"] [data-failed-revert]');
    assert(pm && !/CAS|patch/.test(pm.textContent) && /now 2\.6/.test(pm.textContent), "the note is in plain words"); checks++;
    btn.click();
    await new Promise(resolve => setTimeout(resolve, 50));
    assert(posts.some(u => u === "/scheduler/item/a/revert-failed"), "the press posts the item's revert"); checks++;
    console.log(checks + " checks passed");
  } finally { dom.window.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
