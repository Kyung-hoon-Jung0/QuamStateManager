# 104 — 1.0-prep red team: three personas, the fixes, and the ranked backlog

*2026-08-10. Three persona sweeps over the real implementation (a 21Q
calibration researcher · a first-day grad student · a 10k-run/4-chip/2-window
power user), every finding grounded in file:line before it was accepted.
~70 findings total. This document records what was FIXED tonight (here and in
the sibling 1.0-prep branches) and ranks everything else as the 1.0 backlog —
deferral is explicit, nothing is silently dropped.*

## Fixed tonight

| # | Finding (persona) | Fix | Where |
|---|---|---|---|
| 1 | ~~CRITICAL — a live-file write with no confirm~~ **REVERSED BY THE OWNER (2026-08-10)**: the finding flagged the tray's ⚡ *Apply to live now* calling `doStateSync('apply')` bare, and a confirm was briefly added — then the owner clarified the no-confirm behavior is a DELIBERATE design: a confirm existed once and was removed on purpose, because pressing a button labeled "Apply to live now" *is* the consent — a dialog re-asking what the label already asked is friction, not safety. Safety lives elsewhere (merge semantics, the pre-apply snapshot arming *Revert last apply*, the one-click review path on the status badge). The confirm was reverted; the intent is now documented at the button so no future red team re-flags it | intent documented, confirm reverted | main (follow-up) |
| 6 | The empty state every pre-load click renders said "pick a folder from the **Workspace** panel" — renamed to *State Load* long ago (new user #6) | text corrected | this branch |
| 13 | Compare said "up to 5" in the toolbar and "2-8" in an unreachable alert (researcher #13) | one number (5) everywhere | this branch |
| 17 | Second `qsm serve` on a bound port printed the "open http://…" banner then died with a raw WinError traceback (new user #17) | friendly port-in-use message + exit 1 | this branch |
| 7 | `/bulk` (the slowest page) had NO loading cue; the main pane never dimmed in flight (researcher #7) | `#table-pane.htmx-request` + SLOW_PREFIXES for /bulk /diff /topology /autofit /compare-hub | `perf/loading-search` (docs/103) |
| — | `/bulk`'s 10 MB payload | gzip, 25× smaller on the wire | `perf/loading-search` (docs/103) |
| — | cp949 `--help` crash; unguarded `__main__` app launch; env-accepting version floors (deps audit) | fixed | `audit/deps-state` (docs/102) |
| — | 18 cross-platform defects incl. per-OS analysis-rev splits and a path traversal (xplat audit) | fixed | `audit/xplat-10` (docs/101) |

## Backlog — ranked (severity · size · persona)

**Tier 1 — architectural speed (the two places the app is structurally slow):**
1. **Live State Edit column-window virtualization** (blocker-adjacent · L ·
   researcher #2/#17): ~55k DOM nodes on a 452-column chip; hidden columns
   still in the DOM; search re-touches every cell. The wire cost is now
   solved (gzip); the mount/search cost needs windowing.
2. **Param History filters: one checkbox = one full dashboard re-render**
   (blocker · M · researcher #1): 18 unchecks = 18 full round-trips on the
   app's slowest route. Debounce + client-side filter + All/None per row.
3. **Dataset arrival latency + silence** (blocker · S+M · researcher #3):
   60 s default poll, held while active, and a new run splices in with no
   flash/pill. Active-date fast path + "N new runs ↑" pill.

**Tier 2 — scale robustness (power user; each is a real wedge at 10k runs):**
4. Poll budget is between-folder only — one slow folder blows the client
   abort with no `partial` (S1 · M · #1); Rescan = unbudgeted full re-parse
   under the scan lock (S1 · S · #2); post-rescan delta ships the whole
   workspace uncapped (S1 · M · #3).
5. **Working-copy GC never consults the instance registry** — window 2's GC
   can delete the folder window 1 is about to save into (S1 · M · #23;
   `instances.peers()` exists precisely for this).
6. Leaf-index freshness compares two different disk rules → any meta-only
   snapshot dir causes a full rebuild on EVERY query (S1 · M · #11).
   → **fixed** on `fix/history-scale` (docs/106; docs/83 amendment).
7. ~11k `os.stat` per idle poll tick across 3 roots (5× per-folder date-dir
   stats; `/datasets/poll` doubles it) (S2 · M · #4/#6); sidebar spine probe
   up to 9k stats/60s (S2 · M · #9); `_workspace_token` still `max(mtime)`
   — the one staleness probe without the D6 count rider (S2 · M · #15).
8. Discovery-walk 50k-dir cap is silent in the UI (S1-honesty · S · #10);
   `/datasets` embeds all ~10k rows ungzipped (S2 · M/L · #17); History
   panel defaults to no pagination (S2 · S · #12 → **fixed** on
   `fix/history-scale`, docs/106: default 50, All kept); `_prune` effectively
   disabled at `DEFAULT_MAX_SNAPSHOTS=100_000` with no size surface (S3 ·
   S · #13 → **size surfaced** on `fix/history-scale`, docs/106 — honest
   header line; the default budget itself deliberately unchanged);
   200 MB pragma + count-budgeted extract cache can pin ~1 GB per
   window (S3 · M · #14); scanner LRU 10 thrashes at 4 chips × surfaces
   (S2 · S · #20).
9. **No timing instrumentation anywhere** on the hot paths — every symptom
   presents as "it stopped updating" (S2 · M · #25). Stamp scan_ms/stats
   into the poll response + a one-line status strip.

**Tier 3 — daily-flow UX (researcher):**
10. No side-by-side: every nav swaps the one main pane; compare renders
    into the NARROW pane; no run pin-bar/tabs (#5/#12/#20 · M).
11. Dyn-column checkbox reloads the whole grid and threatens the user's
    edits with a "Leave?" confirm (#6 · M); no fill-down/paste/multi-select
    in the grid (#8 · M); no column/row pinning (#18/#19 · M/S).
12. Datasets keyboard navigation: none (j/k/Enter/Space) (#11 · M); sort
    restored from yesterday buries today's runs — no "↻ Newest" affordance
    (#4 · S); digest band describes a different day than the filtered table
    (#23 · M).
13. Shortcuts: no `/` focus-search, no Ctrl+Enter apply-all, no `?`
    cheat-sheet overlay (#15 · S); N-D viewer resets state on every file-tab
    switch (#14 · M); banners can stack 4-deep with no group collapse
    (#25 · S); staleness indicators lack a "last checked Ns ago" cue
    (#16/#22 · S).

**Tier 4 — first-hour experience (new user):**
14. No "Open a folder" CTA on the landing; the WSL locate box leads the page
    (#4/#5 · S). The working-copy glossary exists on ONE swap-away pane and
    there is no `/help`; stage/pull/apply semantics live in `title=`
    tooltips (#8/#9 · M). The tray teaching element is hidden exactly while
    a new user forms their model (#10 · S).
15. Non-chip folder → a 6-second corner toast; the folder browser
    auto-submits with no chip check and only literal `quam_state` names get
    hints (#11/#12 · M). Dangling pointers render as raw strings with a
    tooltip that says "Resolves to: <the pointer itself>" (#13 · M).
16. Write-permission preflight nowhere — read-only shares fail at apply
    time with a vanishing toast (#3 · M). README: no screenshots, teaches
    neither the model nor the features, still says `git clone` only
    (#14–16 · S). `/chip-name/set`'s response destroys the banner's swap
    anchor (later OOB swaps have no target) (#20 · S). Value-history clocks
    are invisible at rest AND `tabindex="-1"` (#19 · S).

## Method note

Persona sweeps are grounded but adversarial — each item was accepted only
with a file:line trace, and features that already exist (search grammar,
column history, diff workbench, JAZZ click contracts…) were excluded by
checking first. The docs/80 fixes were treated as prior art; only NEW holes
in them are listed (#4-#9 above are new).
