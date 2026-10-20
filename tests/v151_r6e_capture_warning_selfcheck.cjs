"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
(async () => {
  const dom = new JSDOM('<body><div id="sched-root" class="sched-root"><div id="sched-queue"></div></div></body>',
    { url: "http://localhost/scheduler", runScripts: "outside-only", pretendToBeVisual: true });
  try {
    const w = dom.window;
    const warning = "SM could not record the node's state: PermissionError: <write denied>";
    w.fetch = async url => ({ json: async () => String(url).startsWith("/scheduler/status")
      ? { queue: [{ id: "a", name: "node", kind: "node", status: "done", error: warning, enabled: true }], run: { status: "idle" } }
      : {} });
    w.eval(fs.readFileSync(path.join(__dirname, "..", "quam_state_manager", "web", "static", "scheduler.js"), "utf8"));
    w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
    await new Promise(resolve => setTimeout(resolve, 100));
    const row = w.document.querySelector('.sched-item[data-id="a"]');
    assert(row, "real scheduler status poll renders the item");
    assert(row.textContent.includes("done -- " + warning), row.textContent);
    assert(!row.querySelector("write"), "warning is escaped as text");
    assert(row.querySelector('[role="status"]'), "warning is visible and announced");
    console.log("DONE: capture warning visible on the item");
  } finally { dom.window.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
