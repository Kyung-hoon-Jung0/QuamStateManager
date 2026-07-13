/* Compare hub (docs/49) — URL-canonical basket helpers.
 *
 * The hub is stateless: the basket IS the query string. Every control
 * rewrites the params, pushes the URL, and re-GETs /compare-hub into
 * #table-pane (house pattern: manual pushState + htmx.ajax with an explicit
 * source — see the datasets nav; omitting `source` queues on document.body
 * and wedges clicks). Loaded once from base.html like every page script.
 *
 * History contract (post-review): hub pushes carry state {cmpHub: true};
 * a popstate onto such an entry (or any /compare-hub URL htmx doesn't own)
 * refetches the partial for the restored URL — Back/forward between basket
 * states works. htmx-owned entries (state.htmx) are left to htmx; the
 * server side of A7 lives in routes._is_htmx (history restores get the
 * full page).
 */
(function () {
    "use strict";

    var STRICT_KEY = "quam_cmp_strictness";   // 'exact' | 'lab' | 'wide'
    var META_KEY = "quam_cmp_meta";           // '1' = show link/schema rows

    function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
    function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }

    function root() { return document.getElementById("cmp-hub-root"); }

    function currentParams() {
        // The hub always pushes its canonical URL, so location.search is
        // authoritative once we're on /compare-hub.
        if (window.location.pathname === "/compare-hub") {
            return new URLSearchParams(window.location.search || "");
        }
        return new URLSearchParams("");
    }

    function hubToast(msg) {
        var r = root() || document.body;
        var t = document.createElement("div");
        t.className = "cmp-toast";
        t.textContent = msg;
        r.appendChild(t);
        setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 2600);
    }

    // Serialize reloads: a slow response must never paint over a newer
    // click (the fast-click stale-paint class of bug). Last request wins.
    var _inflight = false;
    var _pending = null;

    // Basket edits keep the sources panel open across the re-render;
    // compare-intent actions (bucket/preset/map) let it collapse.
    var _reopenSetup = false;

    function reload(p, opts) {
        opts = opts || {};
        if (opts.keepSetupOpen) _reopenSetup = true;
        // Stored strictness rides along whenever the URL doesn't pin one —
        // the server default is 'lab', so only exact/wide need carrying.
        if (!p.get("preset")) {
            var s = lsGet(STRICT_KEY);
            if (s === "exact" || s === "wide") p.set("preset", s);
        }
        var qs = p.toString();
        var url = "/compare-hub" + (qs ? "?" + qs : "");
        // Push the canonical URL BEFORE any in-flight gate: every basket op
        // reads location.search, so queued ops must see each other's params
        // (a multi-folder drop chains adds while the first GET is still in
        // flight — queuing before pushing silently LOST middle sources).
        // opts.replace amends the current entry instead of stacking one —
        // the init() strictness auto-apply uses it, otherwise Back would
        // bounce forever between preset-less and preset-ful entries.
        try {
            if (opts.replace) history.replaceState({ cmpHub: true }, "", url);
            else history.pushState({ cmpHub: true }, "", url);
        } catch (e) { /* file:// etc. */ }
        if (_inflight) { _pending = url; return; }   // last pushed URL wins
        _inflight = true;
        issue(url);
    }

    function issue(url) {
        var done = function () {
            if (_pending) {
                var u = _pending;
                _pending = null;
                issue(u);
            } else {
                _inflight = false;
            }
        };
        htmx.ajax("GET", url, {
            target: "#table-pane", swap: "innerHTML",
            source: root() || document.body,
        }).then(done, done);
    }

    function srcList(p) { return p.getAll("src"); }

    // A1 — persist a confirmed ② mapping for this source pair. Fire-and-
    // forget: a failed save only costs the reload-persistence convenience;
    // the map itself still applies through the URL.
    function saveMapping(m) {
        var p = currentParams();
        var srcs = p.getAll("src");
        if (srcs.length !== 2) return;
        try {
            fetch("/compare-hub/map/save", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    srcs: srcs,
                    ref: parseInt(p.get("ref") || "0", 10) || 0,
                    map: m,
                }),
            }).then(function (resp) { return resp.json(); })
              .then(function (d) {
                  if (!d) return;
                  if (d.ok && d.persisted === false) {
                      hubToast("Mapping applies this session only — " +
                          (d.reason || "not persisted"));
                  } else if (d.warning) {
                      hubToast(d.warning);
                  }
              })
              .catch(function () { });
        } catch (e) { /* fetch unavailable — URL param still applies */ }
    }

    function setSrcs(p, list) {
        p.delete("src");
        for (var i = 0; i < list.length; i++) p.append("src", list[i]);
    }

    var cmpHub = {
        add: function (ref) {
            if (!ref) return;
            var p = currentParams();
            var list = srcList(p);
            if (list.length >= 8) {
                // pool-bounded basket — honest, not silent (tokens count,
                // including unreadable rows: remove those to make room)
                hubToast("The basket holds at most 8 sources — remove one first.");
                return;
            }
            list.push(ref);
            setSrcs(p, list);
            p.delete("map");   // the basket changed — a confirmed ② map is stale
            p.delete("hint");  // an edited basket is manual — no primary CTA (U1b)
            reload(p, { keepSetupOpen: true });
        },
        addFrom: function (el) { if (el && el.dataset && el.dataset.ref) cmpHub.add(el.dataset.ref); },
        removeAt: function (btnOrIdx) {
            var p = currentParams();
            var list = srcList(p);
            // Address the row by TOKEN, not position: the pane re-render is
            // async, so a second × click carries a stale positional index
            // (harness-proven: it removed the WRONG source). The row's
            // data-ref disambiguates; positional index is the tie-breaker
            // for duplicate tokens (interchangeable anyway).
            var row = btnOrIdx && btnOrIdx.closest
                ? btnOrIdx.closest(".cmp-src-row") : null;
            var srcIdx = row ? parseInt(row.dataset.srcIdx, 10)
                : parseInt(btnOrIdx, 10);
            var token = row ? row.dataset.ref : null;
            if (isNaN(srcIdx)) return;
            if (token != null && list[srcIdx] !== token) {
                srcIdx = list.indexOf(token);
            }
            if (srcIdx < 0 || srcIdx >= list.length) return;   // already gone
            // Keep the ★ on the same source: rows carry the valid-index map.
            var validIdx = row && row.dataset.validIdx !== undefined && row.dataset.validIdx !== ""
                ? parseInt(row.dataset.validIdx, 10) : NaN;
            var ref = parseInt(p.get("ref") || "0", 10) || 0;
            if (!isNaN(validIdx)) {
                if (validIdx === ref) ref = 0;
                else if (validIdx < ref) ref -= 1;
            }
            list.splice(srcIdx, 1);
            setSrcs(p, list);
            p.set("ref", String(ref));
            p.delete("map");
            p.delete("hint");
            reload(p, { keepSetupOpen: true });
        },
        setRef: function (btnOrIdx) {
            var p = currentParams();
            var validIdx;
            var row = btnOrIdx && btnOrIdx.closest
                ? btnOrIdx.closest(".cmp-src-row") : null;
            if (row) {
                // Address the row by TOKEN, exactly like removeAt: the pane
                // re-render is async, so a ★ click during a pending remove /
                // reorder carries a stale positional valid-index and would star
                // the WRONG source (the same harness-proven failure class
                // removeAt was hardened against — server ref-clamping keeps it
                // in range but does NOT keep it on the right source). Only trust
                // the rendered valid-index when the row's token still sits where
                // it was rendered in the CURRENT src list; if the basket shifted
                // under it, ignore the stale click (the user re-clicks after the
                // re-render) rather than move the ★ onto a different source.
                var token = row.dataset.ref;
                var srcIdx = parseInt(row.dataset.srcIdx, 10);
                var list = srcList(p);
                if (token != null && !(srcIdx >= 0 && list[srcIdx] === token)) return;
                validIdx = parseInt(row.dataset.validIdx, 10);
            } else {
                validIdx = parseInt(btnOrIdx, 10);
            }
            if (isNaN(validIdx)) return;
            p.set("ref", String(validIdx));
            // A confirmed ② map is oriented ref-side→other: moving the ★
            // would silently invert it and compare NOTHING (review P1) —
            // drop it and let the suggestion panel re-offer.
            p.delete("map");
            p.delete("hint");
            reload(p, { keepSetupOpen: true });
        },
        setBucket: function (el) {
            // U4 — declaring the context IS the trigger (no extra button).
            var b = el && el.dataset ? el.dataset.bucket : el;
            if (b !== "1" && b !== "2" && b !== "3" && b !== 1 && b !== 2 && b !== 3) return;
            var p = currentParams();
            p.set("bucket", String(b));
            reload(p);
        },
        setPreset: function (el) {
            var v = el && el.dataset ? el.dataset.preset : el;
            if (v !== "exact" && v !== "lab" && v !== "wide") return;
            lsSet(STRICT_KEY, v);
            var p = currentParams();
            p.set("preset", v);
            reload(p);
        },
        useMap: function (el) {
            var m = el && el.dataset ? el.dataset.map : "";
            if (!m) return;
            saveMapping(m);   // A1 — best-effort persist (session-only for drops)
            var p = currentParams();
            p.set("map", m);
            reload(p);
        },
        reload: function () { reload(currentParams()); },
        _tab: "changes",      // survives re-renders within the page session
        _rescan: init,
    };
    window.cmpHub = cmpHub;

    // ── picker handlers ────────────────────────────────────────────────
    window.cmpHubPickWorkspace = function (sel) {
        var opt = sel.selectedOptions && sel.selectedOptions[0];
        sel.selectedIndex = 0;
        if (!opt || !opt.value) return;
        var url = "/compare-hub/options?path=" + encodeURIComponent(opt.value) +
            "&name=" + encodeURIComponent(opt.dataset.name || "");
        htmx.ajax("GET", url, { target: "#cmp-options-pop", swap: "innerHTML", source: root() || document.body });
    };

    window.cmpHubPickHistory = function (sel) {
        var opt = sel.selectedOptions && sel.selectedOptions[0];
        sel.selectedIndex = 0;
        if (!opt || !opt.value) return;
        var url = "/compare-hub/options?chip=" + encodeURIComponent(opt.value) +
            "&name=" + encodeURIComponent(opt.dataset.name || opt.value);
        htmx.ajax("GET", url, { target: "#cmp-options-pop", swap: "innerHTML", source: root() || document.body });
    };

    window.cmpHubPickRecent = function (sel) {
        var v = sel.value;
        sel.selectedIndex = 0;
        if (v) cmpHub.add("ws:" + v);
    };

    window.cmpHubBrowsePicked = function (input) {
        if (input && input.value) {
            cmpHub.add("ws:" + input.value);
            input.value = "";
        }
    };

    window.cmpHubAddOption = function (el) {
        var pop = document.getElementById("cmp-options-pop");
        if (pop) pop.innerHTML = "";
        cmpHub.addFrom(el);
    };

    // ── mapping editor (P3) ────────────────────────────────────────────
    window.cmpHubMapApply = function (btn) {
        var ed = btn.closest("[data-cmp-map-editor]");
        if (!ed) return;
        var pairs = [], used = {}, dup = null;
        ed.querySelectorAll("select[data-map-a]").forEach(function (sel) {
            sel.classList.remove("cmp-map-dup");
            var b = sel.value;
            if (!b) return;
            if (used[b]) { dup = b; sel.classList.add("cmp-map-dup"); return; }
            used[b] = true;
            pairs.push(sel.dataset.mapA + ":" + b);
        });
        if (dup) {
            hubToast("Duplicate target '" + dup + "' — each qubit maps at most once.");
            return;
        }
        if (!pairs.length) {
            hubToast("Map at least one qubit.");
            return;
        }
        var m = pairs.join(",");
        saveMapping(m);
        var p = currentParams();
        p.set("map", m);
        reload(p);
    };

    // ── result-zone toggles ────────────────────────────────────────────
    window.cmpHubToggleMeta = function (cb) {
        var r = root();
        if (!r) return;
        r.classList.toggle("cmp-show-meta", !!cb.checked);
        lsSet(META_KEY, cb.checked ? "1" : "0");
    };

    window.cmpHubTab = function (btn) {
        var r = root();
        if (!r) return;
        var tab = btn.dataset.cmpTab;
        cmpHub._tab = tab;   // restored by init() after the next re-render
        r.querySelectorAll(".cmp-tab").forEach(function (b) {
            var on = b === btn;
            b.classList.toggle("active", on);
            b.setAttribute("aria-pressed", on ? "true" : "false");
        });
        r.querySelectorAll(".cmp-tabpane").forEach(function (pane) {
            pane.hidden = pane.dataset.cmpPane !== tab;
        });
    };

    // ── one-time document-level wiring ─────────────────────────────────
    if (!window._cmpHubDelegated) {
        window._cmpHubDelegated = true;

        // Lazy group loading ('toggle' doesn't bubble — capture phase).
        document.addEventListener("toggle", function (ev) {
            var d = ev.target;
            if (!d || !d.classList || !d.classList.contains("cmp-group")) return;
            if (!d.open || !d.dataset.lazy || d.dataset.loaded) return;
            d.dataset.loaded = "1";   // guards a double-fire while in flight
            var body = d.querySelector(".cmp-group-body");
            if (!body || !d.dataset.url) return;
            htmx.ajax("GET", d.dataset.url, { target: body, swap: "innerHTML", source: body })
                .then(function () {
                    // htmx resolves without swapping on send/response errors —
                    // the placeholder still there means the load failed.
                    // Clear the guard so collapse/reopen retries (review P2).
                    if (body.querySelector(".cmp-group-loading")) {
                        d.dataset.loaded = "";
                        body.innerHTML = '<p class="muted cmp-group-loading">' +
                            "Load failed — close and reopen to retry.</p>";
                    }
                }, function () {
                    d.dataset.loaded = "";
                    body.innerHTML = '<p class="muted cmp-group-loading">' +
                        "Load failed — close and reopen to retry.</p>";
                });
        }, true);

        // Back/forward across hub basket states: htmx only handles its own
        // history entries; ours carry {cmpHub: true} and need a refetch.
        window.addEventListener("popstate", function (ev) {
            if (ev.state && ev.state.htmx) return;             // htmx's own
            if (window.location.pathname !== "/compare-hub") return;
            if (!document.getElementById("table-pane")) return;
            htmx.ajax("GET", window.location.pathname + window.location.search, {
                target: "#table-pane", swap: "innerHTML",
                source: root() || document.body,
            });
        });

        // "which state?" popover: Escape or an outside click closes it.
        document.addEventListener("keydown", function (ev) {
            if (ev.key !== "Escape") return;
            var pop = document.getElementById("cmp-options-pop");
            if (pop && pop.firstChild) pop.innerHTML = "";
        });
        document.addEventListener("click", function (ev) {
            var pop = document.getElementById("cmp-options-pop");
            if (!pop || !pop.firstChild) return;
            if (pop.contains(ev.target)) return;
            // the pickers that OPEN it shouldn't insta-close it
            if (ev.target.closest && ev.target.closest(".cmp-pickers")) return;
            pop.innerHTML = "";
        });
    }

    // ── per-render init (called from the partial's inline script and on
    //    full-page load; all bindings are idempotent) ──
    function init() {
        var r = root();
        if (!r) return;
        // recents dropdown — client-side only store (localStorage.recentFolders)
        var sel = document.getElementById("cmp-recent-select");
        if (sel && !sel.dataset.filled) {
            sel.dataset.filled = "1";
            var folders = [];
            try { folders = JSON.parse(localStorage.getItem("recentFolders") || "[]") || []; } catch (e) { }
            folders.forEach(function (pth) {
                var o = document.createElement("option");
                o.value = pth;
                o.textContent = pth.length > 50 ? "…" + pth.slice(-49) : pth;
                sel.appendChild(o);
            });
        }
        // structure strips (P2) — server ships the card data as JSON; we
        // render read-only TopoGraph minis (auto layout for grid-less chips)
        var stripData = document.getElementById("cmp-strips-json");
        if (stripData && window.TopoGraph) {
            var cards = [];
            try { cards = JSON.parse(stripData.textContent || "[]") || []; } catch (e) { }
            r.querySelectorAll(".cmp-strip-mount").forEach(function (m) {
                if (m.dataset.rendered) return;
                m.dataset.rendered = "1";
                var card = cards[parseInt(m.dataset.stripIdx, 10)];
                if (card) {
                    TopoGraph.renderStatic(m, {
                        qubits: card.qubits, pairs: card.pairs, gate: card.gate,
                        cell: 22, stoneR: 8, layout: "auto",
                    });
                }
            });
        }
        // meta-rows toggle restore
        if (lsGet(META_KEY) === "1") {
            r.classList.add("cmp-show-meta");
            var cb = r.querySelector(".cmp-meta-toggle input");
            if (cb) cb.checked = true;
        }
        // basket-edit renders keep the sources panel open (one-shot flag)
        if (_reopenSetup) {
            _reopenSetup = false;
            var setup = r.querySelector(".cmp-setup");
            if (setup) setup.open = true;
        }
        // Summary tab survives re-renders within the page session
        if (cmpHub._tab === "summary") {
            var btn = r.querySelector('.cmp-tab[data-cmp-tab="summary"]');
            if (btn && !btn.classList.contains("active")) window.cmpHubTab(btn);
        }
        // Persisted strictness applies on FIRST render too, not just the
        // next interaction — but only when results are actually showing
        // (bucket declared, ≥2 sources) and the URL didn't pin a preset.
        var stored = lsGet(STRICT_KEY);
        if (window.location.pathname === "/compare-hub"
            && (stored === "exact" || stored === "wide")
            && r.dataset.preset !== stored
            && r.dataset.bucket !== "0"
            && parseInt(r.dataset.sources || "0", 10) >= 2
            && !new URLSearchParams(window.location.search || "").get("preset")) {
            var p = currentParams();
            p.set("preset", stored);
            // one-shot; REPLACE the entry — pushing here made Back bounce
            // between the preset-less and preset-ful URLs forever
            reload(p, { replace: true });
        }
    }

    document.addEventListener("DOMContentLoaded", init);
})();
