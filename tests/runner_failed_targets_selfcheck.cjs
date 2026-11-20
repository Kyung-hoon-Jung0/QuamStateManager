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
    const posts = []; const bodies = {};
    const failed = { id: "a", name: "node", kind: "node", status: "done", enabled: true,
      failed_targets: ["q1"], n_targets: 2, outcome_note: note,
      failed_writes: [{ path: "/quam/qubits/q1/f_01", dotted: "qubits.q1.f_01", target: "q1" }] };
    const clean = { id: "b", name: "node", kind: "node", status: "done", enabled: true };
    // after a partial revert one value is left; after a full one, none
    const fw3 = ["f_01", "T1", "f_12"].map(k => ({ path: "/quam/qubits/q1/" + k, dotted: "qubits.q1." + k, target: "q1" }));
    const partial = Object.assign({}, failed, { id: "c", failed_writes: fw3, failed_revert: { ok: true,
      reverted: [{ path: "qubits.q1.f_01" }], refused: [{ path: "qubits.q1.T1", code: "changed" }], remaining: ["qubits.q1.f_12"],
      at: "2026-01-02T03:04:05Z", message: "reverted 1: qubits.q1.f_01 — not reverted 1: qubits.q1.T1: the live value changed since the run (now 2.6)" } });
    // an earlier press is named with its time, in the same message
    partial.failed_revert.message += " \u2014 reverted earlier 1: qubits.q1.f_12 at 2026-01-01T00:00:00+00:00";
    const full = Object.assign({}, failed, { id: "d", failed_writes: fw3, failed_revert: { ok: true,
      reverted: fw3.map(x => ({ path: x.dotted })), refused: [], remaining: [], message: "reverted 3" } });
    const stuck = Object.assign({}, failed, { id: "e", failed_writes: fw3, failed_revert: { ok: true,
      reverted: [{ path: "qubits.q1.f_01" }], refused: [{ path: "qubits.q1.T1" }, { path: "qubits.q1.f_12" }], remaining: [], message: "x" } });
    // a refused value that came back: the server says it is retryable now
    const back = Object.assign({}, failed, { id: "f", failed_writes: fw3, revert_retryable: ["qubits.q1.T1"],
      revert_offer: [{ path: "qubits.q1.T1", value: 3e-05, old: 2.1e-05, value_text: "3e-05", old_text: "2.1e-05" }],
      failed_revert: { ok: true, reverted: [{ path: "qubits.q1.f_01" }], remaining: [], message: "second",
        presses: [{ at: "t0", message: "first press words" }] } });
    const afterA = Object.assign({}, failed, { revert_retryable: [], failed_revert: { ok: true,
      reverted: [{ path: "qubits.q1.f_01" }], remaining: [], message: "reverted 1: qubits.q1.f_01" } });
    let statusQueue = null;
    w.fetch = async (url, opts) => {
      if (opts && opts.method === "POST") { posts.push(String(url)); bodies[String(url)] = JSON.parse(opts.body || "{}"); }
      return { json: async () => String(url).startsWith("/scheduler/status")
        ? { queue: statusQueue || [failed, clean, partial, full, stuck, back], run: { status: "idle" } }
        : (String(url).includes("/revert-failed") ? { ok: true, message: "reverted 1: qubits.q1.f_01",
            state: { queue: [afterA, clean, partial, full, stuck, back], run: { status: "idle" } } } : {}) };
    };
    // the page's one time format (app.js); a stub that shows it was used
    w.SnapTime = { display: (d, short) => "T<" + d.toISOString() + (short ? ",short" : "") + ">" };
    w.eval(fs.readFileSync(path.join(__dirname, "..", "quam_state_manager", "web", "static", "scheduler.js"), "utf8"));
    w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
    await new Promise(resolve => setTimeout(resolve, 100));
    let checks = 0;
    const row = w.document.querySelector('.sched-item[data-id="a"]');
    const ok = w.document.querySelector('.sched-item[data-id="b"]');
    assert(row && ok, "both items render"); checks++;
    assert.strictEqual(row.querySelector(".sched-item-status").textContent,
      "done — 1 of 2 targets not successful"); checks++;
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
    const fb = w.document.querySelector('.sched-item[data-id="f"] [data-act="revert-failed"]');
    assert(fb && fb.textContent === "Revert the 1 remaining value", "a value that came back is offered again"); checks++;
    const det = w.document.querySelector('.sched-item[data-id="f"] details[data-revert-presses="1"]');
    assert(det && !det.open && /first press words/.test(det.textContent), "earlier presses sit collapsed"); checks++;
    assert(/as of the press at /.test(pm.textContent), "a stored refusal says when it was true"); checks++;
    assert(pm.textContent.indexOf("press at T<2026-01-02T03:04:05.000Z>)") > 0,
      "the press time is in the page's time format, not the browser locale: " + pm.textContent); checks++;
    assert(/qubits\.q1\.f_12 at T<2026-01-01T00:00:00\.000Z>$/.test(pm.textContent),
      "a press time inside the message is in the same format: " + pm.textContent); checks++;
    assert(/^As of /.test(det.querySelector("div").textContent), "an earlier press line says when"); checks++;
    assert(fb.title === "Puts back: qubits.q1.T1 (run value 3e-05 -> 2.1e-05)",
      "the button title names path, run value and the value it goes back to: " + fb.title); checks++;
    const fn = w.document.querySelector('.sched-item[data-id="f"] [data-revert-names="1"]');
    assert(fn && fn.textContent === "Will revert: qubits.q1.T1", "three or fewer: the names sit under the button"); checks++;
    const en = w.document.querySelector('.sched-item[data-id="e"] [data-revert-none]');
    assert(en && /^Nothing to revert: /.test(en.textContent), "a pressed row with nothing left says why"); checks++;
    assert(!w.document.querySelector('.sched-item[data-id="a"] [data-revert-none]'), "a fresh row says nothing of the kind"); checks++;
    btn.focus();
    btn.click();
    await new Promise(resolve => setTimeout(resolve, 50));
    assert(posts.some(u => u === "/scheduler/item/a/revert-failed"), "the press posts the item's revert"); checks++;
    assert.deepStrictEqual(bodies["/scheduler/item/a/revert-failed"], {}, "a fresh row sends no list"); checks++;
    w.document.querySelector('.sched-item[data-id="f"] [data-act="revert-failed"]').click();
    await new Promise(resolve => setTimeout(resolve, 50));
    assert.deepStrictEqual(bodies["/scheduler/item/f/revert-failed"], { shown: ["qubits.q1.T1"] },
      "the press sends the list it showed"); checks++;
    const rowA = w.document.querySelector('.sched-item[data-id="a"]');
    const msgA = rowA.querySelector("[data-failed-revert]");
    assert(msgA && msgA.textContent === "Revert: reverted 1: qubits.q1.f_01", "the row shows the press without a reload"); checks++;
    assert(!rowA.querySelector('[data-act="revert-failed"]'), "the button goes without a reload"); checks++;
    // another window pressed: the next status poll alone redraws this row
    statusQueue = [afterA, clean, partial, full, Object.assign({}, stuck, {
      failed_revert: Object.assign({}, stuck.failed_revert, { message: "pressed elsewhere", at: "t9" }) }), back];
    await new Promise(resolve => setTimeout(resolve, 1700));
    const rowC = w.document.querySelector('.sched-item[data-id="e"]');
    assert(/pressed elsewhere/.test(rowC.querySelector("[data-failed-revert]").textContent)
      && !rowC.querySelector('[data-act="revert-failed"]'), "a poll shows another window's press"); checks++;
    console.log(checks + " checks passed");
  } finally { dom.window.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
