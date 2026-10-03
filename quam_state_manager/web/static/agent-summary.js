/* docs/261 -- the night summary's buttons (templates/_agent_summary.html).
 *
 * The page itself is rendered by the server from SM's records, so it reads the
 * same after a restart; this file only wires its three presses:
 *   Write to chip / Allow run -> POST /api/agent/approvals/<id>/approve (the person's
 *                                press: the window's cookie rides along, docs/252)
 *   Reject                    -> POST /api/agent/approvals/<id>/reject
 *   Undo                      -> hx-post /auto-apply/revert (htmx; the old values are
 *                                staged in the review tray, Apply writes them)
 * and re-reads the summary after each, so what it says is SM's answer, never a guess.
 * Delegated from document: the section arrives by htmx swaps too. */
(function () {
  "use strict";
  var busy = {};
  var last = null;                       // the answer of the last press, said again after the re-read

  function root() { return document.getElementById("agent-summary"); }
  function show() {
    var r = root();
    var el = r && r.querySelector(".ns-msg");
    if (el && last) { el.textContent = last.text; el.classList.toggle("ag-err", !!last.bad); }
  }
  function say(text, bad) {
    last = text ? { text: text, bad: !!bad } : null;
    show();
    if (text && window.showToast) window.showToast(text, bad ? "error" : "success");
  }
  function settled() {
    // the swap REPLACES #agent-summary, so app.js's per-swap localizer may be handed the old
    // (detached) element: the new times stay hidden until they are localized here (measured)
    if (window.applyLocalTimes) window.applyLocalTimes(document);
    show();
  }
  function refresh() {
    var r = root();
    if (!r) return Promise.resolve();
    var plan = r.getAttribute("data-plan");
    var url = "/agent/summary" + (plan ? "?plan=" + encodeURIComponent(plan) : "");
    if (window.htmx && window.htmx.ajax) {
      return Promise.resolve(window.htmx.ajax("GET", url, { target: "#agent-summary", swap: "outerHTML" })).then(settled);
    }
    return fetch(url, { headers: { "HX-Request": "true" }, credentials: "same-origin" })
      .then(function (res) { return res.text(); })
      .then(function (html) {
        var cur = root();
        if (!cur) return;
        var tmp = document.createElement("div");
        tmp.innerHTML = html;
        var fresh = tmp.querySelector("#agent-summary");
        if (fresh) cur.parentNode.replaceChild(fresh, cur);
        settled();
      });
  }
  function decide(id, verb, btn) {
    if (!id || busy[id]) return;
    busy[id] = verb;
    var item = btn && btn.closest(".ns-item");
    if (item) item.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
    if (btn) btn.textContent = verb === "approve" ? "writing…" : "rejecting…";
    return fetch("/api/agent/approvals/" + encodeURIComponent(id) + "/" + verb, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json" }, body: "{}"
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (j) { return { status: res.status, body: j || {} }; });
    }).then(function (r) {
      delete busy[id];
      var ok = r.status === 200 && r.body.ok !== false;
      if (ok) say(verb === "approve" ? (r.body.approval && r.body.approval.kind === "run" ? "Run allowed." : "Written to the chip.") : "Rejected.");
      else say((verb === "approve" ? "Not written: " : "Not rejected: ") + (r.body.error || r.body.how || ("HTTP " + r.status)), true);
      return refresh();
    }, function () {
      delete busy[id];
      say("SM cannot be reached (is the window still open?)", true);
      return refresh();
    });
  }

  document.addEventListener("click", function (ev) {
    var btn = ev.target && ev.target.closest ? ev.target.closest("#agent-summary [data-ns-act]") : null;
    if (!btn) return;
    var act = btn.getAttribute("data-ns-act");
    if (act === "approve" || act === "reject") {
      ev.preventDefault();
      decide(btn.getAttribute("data-id"), act, btn);
    }
    // "undo" is htmx's own hx-post; the summary is re-read once that request ends (below)
  });
  document.addEventListener("htmx:afterRequest", function (ev) {
    var el = ev.detail && ev.detail.elt;
    if (!el || !el.classList || !el.classList.contains("ns-undo")) return;
    var xhr = ev.detail.xhr;
    if (ev.detail.successful) say("Undo staged: the old values wait in the review tray; Apply writes them to the chip.");
    else {
      var txt = "";
      try { var d = document.createElement("div"); d.innerHTML = xhr.responseText || ""; txt = (d.textContent || "").replace(/\s+/g, " ").trim(); } catch (e) { txt = ""; }
      say("Not undone: " + (txt || ("HTTP " + (xhr && xhr.status))), true);
    }
    refresh();
  });

  window.AgentSummary = { refresh: refresh, decide: decide };
})();
