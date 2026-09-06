/* Calibration log page (docs/173 S2). Page bundle: day navigation, the
 * since-last-visit marker, path tokens -> the value-history popover, the
 * "I ran this" claim, moving unassigned lines under the chip. */
"use strict";

window.JournalPage = (function () {
  function $(id) { return document.getElementById(id); }

  function day(d) {
    var hidden = $("jr-day");
    if (!hidden) return;
    hidden.value = d || "";
    var pick = $("jr-day-pick");
    if (pick && d) pick.value = d;
    var form = $("jr-filters");
    if (form && window.htmx) htmx.trigger(form, "submit");
  }

  function seenKey(chip) { return "quam_journal_seen:" + (chip || "chip"); }

  /* since-last-visit: cards newer than the stored stamp get a dot; the
   * stamp moves to now when the day body lands. Per chip, per browser. */
  function markSince(root) {
    var counts = (root || document).querySelector(".jr-counts");
    if (!counts) return;
    var chip = counts.getAttribute("data-chip");
    var last = 0;
    try { last = parseFloat(localStorage.getItem(seenKey(chip)) || "0") || 0; } catch (e) { /* storage may be off */ }
    var fresh = 0;
    (root || document).querySelectorAll(".jr-card[data-ts]").forEach(function (c) {
      var ts = parseFloat(c.getAttribute("data-ts") || "0") || 0;
      if (last && ts > last) { c.classList.add("jr-new"); fresh++; }
    });
    var since = $("jr-since");
    if (since) {
      if (last && fresh) {
        since.textContent = "· " + fresh + " new since your last visit (" + new Date(last * 1000).toLocaleString() + ")";
        since.hidden = false;
      } else { since.hidden = true; }
    }
    try { localStorage.setItem(seenKey(chip), String(Date.now() / 1000)); } catch (e) { /* ignore */ }
  }

  function bindPaths(root) {
    (root || document).querySelectorAll(".jr-path[data-path]").forEach(function (el) {
      if (el._jrBound) return;
      el._jrBound = true;
      el.addEventListener("click", function (ev) {
        ev.preventDefault(); ev.stopPropagation();
        var path = el.getAttribute("data-path");
        if (window.FieldHistory && typeof window.FieldHistory.open === "function") {
          window.FieldHistory.open(el, path, null);
        }
      });
    });
  }

  function claim(btn) {
    var box = btn.closest(".jr-claim");
    var run = box && box.getAttribute("data-run");
    var who = (box.querySelector(".jr-who") || {}).value || "";
    var note = (box.querySelector(".jr-note-in") || {}).value || "";
    // docs/173 S8: one name key across the app — the same the chat's picker sets
    if (!who) { try { who = localStorage.getItem("quam_actor_name") || localStorage.getItem("quam_actor") || ""; } catch (e) { /* ignore */ } }
    if (who) { try { localStorage.setItem("quam_actor_name", who); } catch (e) { /* ignore */ } }
    fetch("/journal/claim", { method: "POST", headers: { "Content-Type": "application/json" },
                              body: JSON.stringify({ run_id: run, who: who, note: note }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { btn.textContent = d.error || "not saved"; return; }
        day($("jr-day") ? $("jr-day").value : "");
      })
      .catch(function () { btn.textContent = "not saved"; });
  }

  function adopt(d) {
    fetch("/journal/adopt", { method: "POST", headers: { "Content-Type": "application/json" },
                              body: JSON.stringify({ day: d }) })
      .then(function (r) { return r.json(); })
      .then(function () { day(d); })
      .catch(function () {});
  }

  function toggleDigest(btn) {
    var box = btn.closest(".jr-digest");
    if (!box) return;
    var folded = box.classList.toggle("jr-digest-folded");
    btn.textContent = folded ? btn.getAttribute("data-all") || btn.textContent : "fewer";
    if (!btn.getAttribute("data-all")) btn.setAttribute("data-all", btn.textContent);
  }

  function copyDigest(btn) {
    var rows = [];
    document.querySelectorAll(".jr-digest-row").forEach(function (r) {
      var t = r.querySelector(".jr-digest-target").textContent;
      var pills = Array.prototype.map.call(r.querySelectorAll(".jr-pill"), function (p) { return p.textContent.replace(/\s+/g, " ").trim(); });
      rows.push(t + ": " + pills.join(" -> "));
    });
    var text = rows.join("\n");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { btn.textContent = "copied"; });
    }
    return text;
  }

  function init(root) {
    bindPaths(root);
    markSince(root);
    var who = "";
    try { who = localStorage.getItem("quam_actor") || ""; } catch (e) { /* ignore */ }
    if (who) (root || document).querySelectorAll(".jr-who").forEach(function (i) { if (!i.value) i.value = who; });
  }

  document.addEventListener("htmx:afterSwap", function (e) {
    var t = e.target || e.detail && e.detail.target;
    if (t && (t.id === "jr-body" || t.id === "table-pane") && document.querySelector(".jr-counts")) init(t);
  });
  if (document.readyState !== "loading") init(); else document.addEventListener("DOMContentLoaded", function () { init(); });

  return { day: day, claim: claim, adopt: adopt, copyDigest: copyDigest, toggleDigest: toggleDigest, init: init, markSince: markSince, _seenKey: seenKey };
})();
