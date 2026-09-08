/* The Agent pill (docs/173 §3.1). Core script: on every page, in the topbar.
 * Renders GET /api/agent/now -- one state in the precedence order
 *   waiting > limited > stalled > failed > running > between > human-ran > idle
 * -- plus the reservation line (who, until, mode). Refreshes when live-wake
 * wakes (the server bumps its tick on every agent event), on load, and on
 * a slow safety timer. Click -> the Calibration log. */
"use strict";

window.AgentPill = (function () {
  var SAFETY_MS = 60000;
  var el = null, lastSeq = -1, timer = null, inFlight = false;

  function $() { if (!el) el = document.getElementById("agent-pill"); return el; }

  function fmtSince(ts) {
    if (!ts) return "";
    var s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return Math.round(s) + "s";
    if (s < 5400) return Math.round(s / 60) + "m";
    return (s / 3600).toFixed(1) + "h";
  }
  function fmtClock(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    return d.getHours().toString().padStart(2, "0") + ":" + d.getMinutes().toString().padStart(2, "0");
  }

  /* pure: payload -> {state, text, title} */
  function describe(d) {
    d = d || {};
    var st = d.state || "idle";
    var r = d.running || {};
    var who = d.session && d.session.backend ? " · by_" + d.session.backend : (r.backend ? " · by_" + r.backend : "");
    var text, title = "";
    if (st === "waiting") { text = "Agent: waiting for approval (" + (d.waiting || 0) + ")"; }
    else if (st === "limited") { text = "Agent: limited · resets " + (d.limited_resets || "?"); }
    else if (st === "stalled") { text = "Agent: no sign of life " + fmtSince(d.last && d.last.ts); }
    else if (st === "failed") { text = "Agent: ✗ " + (d.failures_today || 0) + " today"; }
    else if (st === "running") {
      text = "Agent: " + (r.node || r.tool || "running") + (r.targets ? " · " + r.targets : "") + " · " + fmtSince(r.since) + who;
      if (r.typical_s) title = "usually ~" + Math.round(r.typical_s / 60) + " min";
    }
    else if (st === "between") { text = "Agent: thinking" + who; }
    else if (st === "human-ran") { text = "human ran " + (d.human_ran && d.human_ran.node || "a node") + " · " + fmtSince(d.human_ran && d.human_ran.ts) + " ago"; }
    else { text = "Agent"; }
    if (d.mode) text += " · " + d.mode;
    var s = d.session;
    if (s && s.owner) {
      title = (title ? title + " · " : "") + "reserved by " + s.owner + (s.until ? " until " + fmtClock(s.until) : "") + (s.stopped ? " · STOPPED" : "");
    }
    if (d.failures_today && st !== "failed") title = (title ? title + " · " : "") + "✗ " + d.failures_today + " today";
    return { state: st, text: text, title: title, short: shortText(st, d, r, who) };
  }

  // customer feedback 2026-09-09: in the TOPBAR the full sentence was 366 px wide
  // ("Agent: limited · resets 14:00 · ask-writes human") and pushed the search box
  // and the tool row off a 980 px window. The pill says the state in as few words
  // as carry it; the full sentence stays in `text` (the Agent page's own strip) and
  // in the pill's title. The mode rides its own chip, which CSS drops when narrow.
  function shortText(st, d, r, who) {
    if (st === "waiting") { return (d.waiting || 0) + " waiting"; }
    if (st === "limited") { return "limited → " + (d.limited_resets || "?"); }
    if (st === "stalled") { return "no sign " + fmtSince(d.last && d.last.ts); }
    if (st === "failed") { return "✗ " + (d.failures_today || 0) + " today"; }
    if (st === "running") { return (r.node || r.tool || "running") + (r.targets ? " " + r.targets : "") + " · " + fmtSince(r.since); }
    if (st === "between") { return "thinking" + (who || ""); }
    if (st === "human-ran") { return "human ran " + (d.human_ran && d.human_ran.node || "a node"); }
    return "Agent";
  }

  function render(d) {
    var p = $();
    if (!p) return;
    var v = describe(d);
    p.className = "agent-pill agent-" + v.state;
    p.hidden = false;
    var t = p.querySelector(".agent-pill-text");
    if (t) t.textContent = v.short;                      // the compact form; the full one is the title
    // the whole sentence is the tooltip, so nothing is lost by the short label
    p.title = v.text + (v.title ? " · " + v.title : "") + " — click for the Calibration log";
    var modeEl = p.querySelector(".agent-pill-mode");
    if (modeEl) {
      if (d && d.mode) { modeEl.hidden = false; modeEl.textContent = d.mode; }
      else modeEl.hidden = true;
    }
    var res = p.querySelector(".agent-pill-res");
    if (res) {
      var s = d && d.session;
      if (s && s.owner) { res.hidden = false; res.textContent = s.owner + (s.until ? " → " + fmtClock(s.until) : ""); }
      else res.hidden = true;
    }
    p.setAttribute("data-state", v.state);
  }

  function refresh(force) {
    if (!$() || inFlight) return;
    inFlight = true;
    fetch("/api/agent/now", { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        inFlight = false;
        if (!d || !d.ok) return;
        if (!force && typeof d.seq === "number" && d.seq === lastSeq && d.state !== "running") return;
        lastSeq = typeof d.seq === "number" ? d.seq : lastSeq;
        render(d);
      })
      .catch(function () { inFlight = false; });
  }

  function arm() {
    if (timer) clearInterval(timer);
    timer = setInterval(function () { if (!document.hidden) refresh(true); }, SAFETY_MS);
  }

  document.addEventListener("sm:runs-changed", function (e) {
    var seq = e && e.detail && e.detail.agent_seq;
    if (typeof seq === "number" && seq === lastSeq) return;
    refresh(true);
  });
  document.addEventListener("visibilitychange", function () { if (!document.hidden) refresh(true); });
  if (document.readyState !== "loading") { refresh(true); arm(); }
  else document.addEventListener("DOMContentLoaded", function () { refresh(true); arm(); });

  return { describe: describe, render: render, refresh: refresh, fmtSince: fmtSince };
})();
