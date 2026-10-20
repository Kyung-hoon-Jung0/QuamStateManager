"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const dir = path.join(__dirname, "..", "quam_state_manager", "web", "static");
const message = "The live chip is being written right now -- nothing was pulled or applied; try again in a moment.";
(async () => {
  for (const door of ["panel", "top", "automatic"]) {
    const dom = new JSDOM('<body><div id="status-bar"></div><div id="pending-tray"></div>'
      + '<div id="state-review-host"><button id="sp-merge"></button></div></body>',
      { url: "http://localhost/bulk", runScripts: "outside-only", pretendToBeVisual: true });
    const w = dom.window, calls = [], toasts = [];
    w.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    w.htmx = { ajax: () => Promise.resolve(), trigger: () => {}, process: () => {}, on: () => {}, config: {} };
    w.fetch = async (url, init) => {
      if (!String(url).startsWith("/state/sync")) return { json: async () => ({}), text: async () => "" };
      calls.push({ url, body: String(init.body || "") });
      return { ok: false, status: 409, headers: { get: () => null },
        json: async () => ({ status: "error", error_code: "live_busy", message,
          tray_html: '<div id="pending-tray">Edits kept; retry in a moment.</div>' }) };
    };
    w.eval(fs.readFileSync(path.join(dir, "app.js"), "utf8"));
    w.eval(fs.readFileSync(path.join(dir, "auto-apply.js"), "utf8"));
    w.showToast = text => toasts.push(text);
    w.confirm = () => { throw new Error("busy refusal must not ask for overwrite"); };
    if (door === "panel") w.SyncPanel.merge(w.document.getElementById("sp-merge"));
    if (door === "top") w.doStateSync("apply");
    if (door === "automatic") w.document.dispatchEvent(new w.CustomEvent("autoSyncMerge", { detail: { chip: "token" } }));
    await new Promise(resolve => setTimeout(resolve, 150));
    assert.strictEqual(calls.length, 1, door + ": no write retry");
    assert(calls[0].body.includes("check_collisions=1"), door + ": checks collisions");
    assert(toasts.some(t => t.includes(message)), door + ": named message shown");
    assert(!w.document.getElementById("pending-tray").textContent.includes("resolving this"), door);
    assert(!w._applyInFlight, door + ": releases latch");
    dom.window.close();
  }
  console.log("DONE: panel, top bar and Auto-Sync busy refusals");
})().catch(e => { console.error(e); process.exitCode = 1; });
