/* Calibration log page (docs/173 S2). Page bundle: day navigation, the
 * since-last-visit marker, path tokens -> the value-history popover, the
 * "I ran this" claim, moving unassigned lines under the chip. */
"use strict";

window.JournalPage = (function () {
  function $(id) { return document.getElementById(id); }

  /* docs/291: a day reloaded in place (a claim, the gates arriving, lines
   * moved) keeps the rows a paged page shows; another day starts at its
   * newest rows. ``extra`` replaces that: a jump to a row, a new filter. */
  function day(d, extra) {
    var hidden = $("jr-day");
    if (!hidden) return;
    var same = !!d && d === hidden.value;
    hidden.value = d || "";
    var pick = $("jr-day-pick");
    if (pick && d) pick.value = d;
    var form = $("jr-filters");
    if (form && window.htmx) {
      if (!extra || !extra.at) jumpTo = null;     // a jump only lands on its own answer
      form._jrExtra = extra || (same ? windowParams() : {});
      form._jrDayRequest = true;
      htmx.trigger(form, "submit");
      form._jrDayRequest = false;
    }
  }

  /* docs/291: the rows of a paged day (more than a page of cards): only a
   * window of them is on the page, so its search is the server's. */
  function pagedList() {
    var body = $("jr-body");
    return body ? body.querySelector(".jr-cards[data-jr-paged]") : null;
  }

  function rowsOf(list) {
    return Array.prototype.filter.call(list.children, function (el) { return el.classList.contains("jr-card"); });
  }

  function windowParams() {
    var list = pagedList();
    var rows = list ? rowsOf(list) : [];
    if (!rows.length) return {};
    var out = { from: rows[0].id };
    if (list.querySelector(".jr-more-later")) out.to = rows[rows.length - 1].id;
    return out;
  }

  /* Every request of the filter form carries what day()/serverFilter()
   * last asked for (a request htmx queues behind one in flight too). */
  document.addEventListener("htmx:configRequest", function (e) {
    var form = $("jr-filters");
    var extra = form && e.detail && e.detail.elt === form ? form._jrExtra : null;
    if (!extra) return;
    Object.keys(extra).forEach(function (k) { if (extra[k]) e.detail.parameters[k] = extra[k]; });
  });

  function seenKey(chip) { return "quam_journal_seen:" + (chip || "chip"); }

  // the stamp read when the open day was loaded: a paged day's filter and
  // its earlier rows are marked against it, not against the stamp the load
  // itself just wrote (docs/291)
  var seen = null;

  function markNew(cards, last) {
    var n = 0;
    Array.prototype.forEach.call(cards, function (c) {
      var ts = parseFloat(c.getAttribute("data-ts") || "0") || 0;
      if (last && ts > last) { c.classList.add("jr-new"); n++; }
    });
    return n;
  }

  /* docs/291: a paged day carries every card's instant (milliseconds,
   * rounded up, as differences), so "Day total" is the whole day's. */
  function countNew(deltas, last) {
    if (!last || !deltas) return 0;
    var limit = Math.round(last * 1000), ms = 0, n = 0;
    deltas.split(",").forEach(function (d) { ms += Number(d) || 0; if (ms > limit) n++; });
    return n;
  }

  /* since-last-visit: cards newer than the stored stamp get a dot; the
   * stamp moves to now when the day body lands. Per chip, per browser. */
  function markSince(root) {
    var counts = (root || document).querySelector(".jr-counts");
    if (!counts) return;
    var chip = counts.getAttribute("data-chip"), shown = counts.getAttribute("data-day");
    var again = counts.hasAttribute("data-jr-reused") && seen && seen.chip === chip && seen.day === shown;
    var last = 0;
    if (again) last = seen.last;
    else {
      try { last = parseFloat(localStorage.getItem(seenKey(chip)) || "0") || 0; } catch (e) { /* storage may be off */ }
      seen = { chip: chip, day: shown, last: last };
    }
    var fresh = markNew((root || document).querySelectorAll(".jr-card[data-ts]"), last);
    var all = counts.getAttribute("data-jr-ts");
    if (all !== null) fresh = countNew(all, last);
    var since = $("jr-since");
    if (since) {
      if (last && fresh) {
        since.textContent = "Day total: " + fresh + " new since your last visit (" + (window.SnapTime ? window.SnapTime.display(new Date(last * 1000)) : new Date(last * 1000).toISOString()) + ")";   // docs/244
        since.hidden = false;
      } else { since.hidden = true; }
    }
    if (again) return;
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
    // the label to fold back to, kept before the first unfold (a strip that
    // arrived after the day -- docs/291 -- was never labelled by the search)
    if (!btn.getAttribute("data-all") && box.classList.contains("jr-digest-folded")) btn.setAttribute("data-all", btn.textContent);
    var folded = box.classList.toggle("jr-digest-folded");
    btn.textContent = folded ? btn.getAttribute("data-all") || btn.textContent : "fewer";
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
    // docs/291: a paged day's rows are the server's answer to this filter
    // over the whole day -- they carry no search text, and the count, the
    // strip and the empty line are rendered from the whole day; only the
    // journal lines are filtered here
    var paged = pagedList();
    if (paged) matches += rowsOf(paged).length;
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
    if (!paged) {
      body.querySelectorAll(".jr-pill[href]").forEach(function (pill) { show(pill, !!visibleRuns[pill.getAttribute("href").slice(1)]); });
      body.querySelectorAll(".jr-seg, .jr-digest-row, .jr-digest").forEach(function (group) {
        show(group, group.querySelectorAll(".jr-pill:not([hidden])").length > 0);
      });
    }
    var head = body.querySelector(".jr-runs-head");
    if (head && !paged) {
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
    if (targets && !paged) {
      var label = "all " + body.querySelectorAll(".jr-digest-row:not([hidden])").length + " targets";
      targets.setAttribute("data-all", label);
      if (body.querySelector(".jr-digest-folded")) targets.textContent = label;
    }
  }

  /* docs/291: on a paged day a new filter is a request -- the day's last
   * build filtered again on the server, its newest matching rows back. The
   * same filter twice asks nothing. */
  function serverFilter() {
    var form = $("jr-filters"), input = $("jr-q"), author = $("jr-author"), list = pagedList();
    if (!form || !window.htmx) return;
    if (list && input && list.getAttribute("data-jr-q") === input.value &&
        list.getAttribute("data-jr-who") === (author ? author.value : "")) { filterSearch(); return; }
    form._jrExtra = { reuse: "1" };
    form._jrDayRequest = true;
    htmx.trigger(form, "submit");
    form._jrDayRequest = false;
  }

  function applyFilter() {
    if (pagedList()) serverFilter(); else filterSearch();
  }

  function bindSearch() {
    var form = $("jr-filters"), input = $("jr-q");
    if (!form || !input || form._jrSearchBound) return;
    form._jrSearchBound = true;
    function flush() { window.clearTimeout(searchTimer); applyFilter(); }
    input.addEventListener("input", function () {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(applyFilter, pagedList() ? 250 : 150);
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

  function scrollerOf(el) {
    for (var p = el.parentElement; p; p = p.parentElement) {
      var oy = window.getComputedStyle(p).overflowY;
      if ((oy === "auto" || oy === "scroll") && p.scrollHeight > p.clientHeight) return p;
    }
    return document.scrollingElement || document.documentElement;
  }

  function windowCount() {
    var list = pagedList(), n = document.querySelector("#jr-body .jr-window-n");
    if (list && n) n.textContent = rowsOf(list).length.toLocaleString("en-US");
  }

  /* docs/291: a paged day's earlier (or later) rows, in place of the
   * control that asked for them. Earlier rows go above the row the reader
   * is at, which stays where it is on the screen. */
  function more(btn) {
    var row = btn && btn.closest(".jr-more-row");
    if (!row || row._jrBusy) return;
    var list = row.parentNode;
    var keep = row.classList.contains("jr-more-earlier") ? row.nextElementSibling : null;
    var top = keep ? keep.getBoundingClientRect().top : 0;
    var label = btn.getAttribute("data-label") || btn.textContent;
    btn.setAttribute("data-label", label);
    row._jrBusy = true;
    btn.disabled = true;
    btn.textContent = "Loading...";
    return fetch(btn.getAttribute("data-url"), { headers: { "HX-Request": "true" } })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.text(); })
      .then(function (html) {
        if (!row.parentNode) return;
        var tpl = document.createElement("template");
        tpl.innerHTML = html;
        var added = Array.prototype.slice.call(tpl.content.children);
        list.replaceChild(tpl.content, row);
        added.forEach(function (el) {
          if (window.htmx && window.htmx.process) window.htmx.process(el);
          bindPaths(el);
        });
        if (keep && keep.parentNode) {
          var scroller = scrollerOf(keep);
          scroller.scrollTop += keep.getBoundingClientRect().top - top;
        }
        markNew(added.filter(function (el) { return el.classList.contains("jr-card"); }), seen ? seen.last : 0);
        windowCount();
        searchItems = null;
      })
      .catch(function () {
        row._jrBusy = false;
        btn.disabled = false;
        btn.textContent = "Retry: " + label;
      });
  }

  /* docs/291: a link to a row the page does not hold (a strip pill, an undo
   * link, a deep link) asks the server for the rows around it; when the row
   * cannot be shown the page says so, never a silent no-op. */
  var jumpTo = null;
  function reveal(id) {
    if (!id || !/^(card|write|agent)-/.test(id) || document.getElementById(id)) return false;
    if (!pagedList()) return false;
    jumpTo = id;
    day($("jr-day") ? $("jr-day").value : "", { at: id });
    return true;
  }

  function landJump() {
    if (!jumpTo) return;
    var id = jumpTo, el = document.getElementById(id);
    jumpTo = null;
    var list = document.querySelector("#jr-body .jr-cards");
    if (!el) {
      if (list) {
        var note = document.createElement("p");
        note.className = "jr-note warn jr-jump-miss";
        note.textContent = "The row that link names is not shown: it is not on this day, or the filter hides it.";
        list.parentNode.insertBefore(note, list);
      }
      return;
    }
    if (el.scrollIntoView) el.scrollIntoView({ block: "center" });
    el.classList.add("jr-jumped");
    window.setTimeout(function () { el.classList.remove("jr-jumped"); }, 2400);
  }

  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest('#jr-body a[href^="#"]') : null;
    if (!a) return;
    var id = a.getAttribute("href").slice(1);
    try { id = decodeURIComponent(id); } catch (err) { /* keep it as written */ }
    if (reveal(id)) e.preventDefault();
  });

  function init(root) {
    searchItems = null;
    bindSearch();
    filterSearch();
    bindPaths(root);
    markSince(root);
    watchGates(root);
    landJump();
    var who = tabActor();
    if (who) (root || document).querySelectorAll(".jr-who").forEach(function (i) { if (!i.value) i.value = who; });
  }

  /* a page opened on a link to one row (another day's undo link) */
  function revealHash() {
    var id = (window.location.hash || "").slice(1);
    try { id = decodeURIComponent(id); } catch (e) { /* keep it as written */ }
    reveal(id);
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
  if (document.readyState !== "loading") { init(); revealHash(); }
  else document.addEventListener("DOMContentLoaded", function () { init(); revealHash(); });

  return { day: day, claim: claim, adopt: adopt, copyDigest: copyDigest, toggleDigest: toggleDigest, init: init, markSince: markSince,
           more: more, reveal: reveal, _seenKey: seenKey };
})();
