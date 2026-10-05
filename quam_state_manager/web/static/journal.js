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
    if (form && window.htmx) {
      form._jrDayRequest = true;
      htmx.trigger(form, "submit");
      form._jrDayRequest = false;
    }
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
        since.textContent = "Day total: " + fresh + " new since your last visit (" + (window.SnapTime ? window.SnapTime.display(new Date(last * 1000)) : new Date(last * 1000).toISOString()) + ")";   // docs/244
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

  /* docs/272 (C-23): whose name THIS tab records -- the Agent panel's per-tab
     name (agent.js, a core script) -- so a claim saved with an empty box and
     a Stop pressed in the same tab can never carry two different people. The
     shared keys are only the fallback when agent.js is not on the page. */
  function tabActor() {
    if (window.AgentPanel && typeof window.AgentPanel.actorName === "function") return window.AgentPanel.actorName() || "";
    try { return localStorage.getItem("quam_actor_name") || localStorage.getItem("quam_actor") || ""; } catch (e) { return ""; }
  }

  function claim(btn) {
    var box = btn.closest(".jr-claim");
    var run = box && box.getAttribute("data-run");
    // docs/281: the card's own identity (data folder + number) keys the claim
    var uid = (box && box.getAttribute("data-uid")) || "";
    var who = (box.querySelector(".jr-who") || {}).value || "";
    var note = (box.querySelector(".jr-note-in") || {}).value || "";
    // docs/173 S8: one name across the app — the same the chat's picker sets;
    // docs/272 (C-23): THIS tab's, not the key every tab shares
    if (!who) who = tabActor();
    if (who) { try { localStorage.setItem("quam_actor_name", who); } catch (e) { /* ignore */ } }
    fetch("/journal/claim", { method: "POST", headers: { "Content-Type": "application/json" },
                              body: JSON.stringify(uid ? { run_id: run, uid: uid, who: who, note: note }
                                                       : { run_id: run, who: who, note: note }) })
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
      var segs = r.querySelectorAll(".jr-seg");
      if (segs.length) {
        // the segmented strip (2026-09-08): "Res spec #159✗ #160✗ · ToF #165✓"
        var parts = Array.prototype.map.call(segs, function (s) {
          var fam = (s.querySelector(".jr-seg-fam") || {}).textContent || "";
          var runs = Array.prototype.map.call(s.querySelectorAll(".jr-pill"), function (p) {
            var mark = p.classList.contains("jr-out-failed") ? "✗" : (p.classList.contains("jr-out-ok") ? "✓" : "");
            return "#" + p.textContent.trim() + mark;
          });
          return fam.trim() + " " + runs.join(" ");
        });
        rows.push(t + ": " + parts.join(" · "));
        return;
      }
      var pills = Array.prototype.map.call(r.querySelectorAll(".jr-pill"), function (p) { return p.textContent.replace(/\s+/g, " ").trim(); });
      rows.push(t + ": " + pills.join(" -> "));
    });
    var text = rows.join("\n");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { btn.textContent = "copied"; });
    }
    return text;
  }

  var searchTimer;
  var searchBody;
  var searchItems;

  function show(el, visible) {
    if (el.hidden === !visible && el.style.display === (visible ? "" : "none")) return;
    el.hidden = !visible;
    // Author display rules can override the browser's hidden attribute.
    el.style.display = visible ? "" : "none";
  }

  function filterSearch() {
    var body = $("jr-body"), input = $("jr-q"), author = $("jr-author");
    if (!body || !input || !window.SearchQuery) return;
    if (searchBody !== body || !searchItems) {
      var cached = body.querySelector(".jr-search-cache");
      if (cached) {
        var fragment = document.createElement("template");
        fragment.innerHTML = JSON.parse(cached.textContent);
        var cards = body.querySelector(".jr-cards");
        cards.appendChild(fragment.content);
        Array.prototype.slice.call(cards.children).sort(function (a, b) {
          return Number(a.getAttribute("data-jr-order")) - Number(b.getAttribute("data-jr-order"));
        }).forEach(function (card) { cards.appendChild(card); });
        if (window.htmx && window.htmx.process) window.htmx.process(cards);
        cached.remove();
        bindPaths(body);
      }
      searchBody = body;
      searchItems = Array.prototype.map.call(body.querySelectorAll("[data-jr-search]"), function (el) {
        return { el: el, hay: el.getAttribute("data-jr-search").toLowerCase(),
                 author: el.getAttribute("data-jr-author") || "",
                 attached: !!el.closest(".jr-run .jr-lines") };
      });
    }
    var grps = window.SearchQuery.groups(input.value);
    var who = author ? author.value : "";
    var matches = 0, runs = 0;
    var visibleRuns = Object.create(null);
    searchItems.forEach(function (item) {
      var visible = (!who || item.author.indexOf(who) === 0) && window.SearchQuery.matchesHay(item.hay, grps);
      show(item.el, visible);
      if (visible && !item.attached) matches++;
      if (item.el.classList.contains("jr-run")) {
        visibleRuns[item.el.id] = visible;
        if (visible) runs++;
      }
    });
    body.querySelectorAll(".jr-line-group").forEach(function (group) {
      var n = group.querySelectorAll("[data-jr-search]:not([hidden])").length;
      show(group, n > 0);
      var count = group.querySelector(".jr-line-count");
      if (count) count.textContent = n + " journal line" + (n === 1 ? "" : "s") + " " + count.getAttribute("data-suffix");
    });
    body.querySelectorAll(".jr-pill[href]").forEach(function (pill) { show(pill, !!visibleRuns[pill.getAttribute("href").slice(1)]); });
    body.querySelectorAll(".jr-seg, .jr-digest-row, .jr-digest").forEach(function (group) {
      show(group, group.querySelectorAll(".jr-pill:not([hidden])").length > 0);
    });
    var head = body.querySelector(".jr-runs-head");
    if (head) {
      show(head, runs > 0);
      head.querySelector(".jr-sec-count").textContent = runs;
    }
    var empty = body.querySelector(".jr-empty");
    if (empty) {
      show(empty, matches === 0);
      if (grps.length || who) empty.textContent = "Nothing matches the filter.";
      else empty.textContent = empty.getAttribute("data-empty-text");
    }
    var targets = body.querySelector(".jr-digest-more");
    if (targets) {
      var label = "all " + body.querySelectorAll(".jr-digest-row:not([hidden])").length + " targets";
      targets.setAttribute("data-all", label);
      if (body.querySelector(".jr-digest-folded")) targets.textContent = label;
    }
  }

  function bindSearch() {
    var form = $("jr-filters"), input = $("jr-q");
    if (!form || !input || form._jrSearchBound) return;
    form._jrSearchBound = true;
    function flush() { window.clearTimeout(searchTimer); filterSearch(); }
    input.addEventListener("input", function () {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(filterSearch, 150);
    });
    input.addEventListener("search", flush);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); flush(); }
    });
    form.addEventListener("submit", function (e) {
      if (form._jrDayRequest) return;
      e.preventDefault(); e.stopImmediatePropagation(); flush();
    }, true);
    form.addEventListener("change", function (e) { if (e.target.id === "jr-author") flush(); });
  }

  /* docs/281: gates computed in the background -- refresh the day once they
   * are done, but never under a person reading an open card or typing. */
  var gateTimer = null, gateTries = 0;
  function watchGates(root) {
    window.clearTimeout(gateTimer);
    var note = (root || document).querySelector(".jr-gates-pending");
    if (!note) { gateTries = 0; return; }
    gateTimer = window.setTimeout(function () {
      var busy = document.querySelector(".jr-cards .jr-card[open]") ||
                 document.activeElement === $("jr-q");
      if (busy && gateTries < 40) { gateTries++; watchGates(document); return; }
      gateTries = 0;
      day($("jr-day") ? $("jr-day").value : note.getAttribute("data-gates-day"));
    }, 2500);
  }

  function init(root) {
    searchItems = null;
    bindSearch();
    filterSearch();
    bindPaths(root);
    markSince(root);
    watchGates(root);
    var who = tabActor();
    if (who) (root || document).querySelectorAll(".jr-who").forEach(function (i) { if (!i.value) i.value = who; });
  }

  document.addEventListener("htmx:afterSwap", function (e) {
    var t = e.target || e.detail && e.detail.target;
    if (t && (t.id === "jr-body" || t.id === "table-pane") && document.querySelector("#jr-body")) init(t);
  });
  /* docs/281: a very large day's card body arrives when the card is first
   * opened -- bind its paths and let the search see its lines. */
  document.addEventListener("htmx:afterSettle", function (e) {
    var t = e.target;
    var card = t && t.closest && t.closest(".jr-card");
    if (!card || !t.classList || !t.classList.contains("jr-body") || t.classList.contains("jr-body-lazy")) return;
    bindPaths(card);
    searchItems = null;
    filterSearch();
  });
  if (document.readyState !== "loading") init(); else document.addEventListener("DOMContentLoaded", function () { init(); });

  return { day: day, claim: claim, adopt: adopt, copyDigest: copyDigest, toggleDigest: toggleDigest, init: init, markSince: markSince, _seenKey: seenKey };
})();
