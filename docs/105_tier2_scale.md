# 105 — Tier 1+2 of the red-team backlog, executed (#1–#11)

*2026-08-10. The user approved autonomous execution of docs/104's Tier 1
(architectural speed) and Tier 2 (scale robustness) — eleven items, one
branch per work group, every fix pinned, every group's suites green before
integration. Tier 3/4 remain reserved for a joint confirm session. Item
numbers below are docs/104's.*

| # | Item | Branch | Record |
|---|---|---|---|
| 1 | Live-Edit grid cold-column hydration (server render unchanged; >4,000 cells prunes tds beyond ~2.5 viewports, widths frozen, search stays whole-chip from a value map, one-way hydration on scroll/nav/path-repaint; below the gate byte-identical) | `perf/grid-col-virtualization` | this doc + `bulk_virt_selfcheck.cjs` |
| 2 | Param History filter churn: 500 ms debounce + `hx-sync this:replace` + All/None per row (htmx 2.0.4's `changed` modifier is broken for checkboxes — deliberately NOT used, pinned) | `perf/param-history-filter` | `test_param_history_filter_ux.py` |
| 3 | Dataset arrival: default poll 60→15 s (`datasetPollInterval` > lab-tuned `autoRefreshInterval` > 15; the shipped 60 is "not a choice"; sibling pollers untouched), held-delta "N new runs ↑" pill (click = flush + scroll-top), 2.6 s row flash | `feat/dataset-arrival` | `dataset_poll_selfcheck.cjs` (+15 checks) |
| 4 | The poll budget reaches INSIDE the folder: `_scan(deadline=…)` stops the date-dir walk early with continuation semantics — vanish pass skipped (no false 'vanished'), staleness gate left open, date-fingerprints merged, dates/experiments unioned | `fix/poll-scan-budgets` | `test_poll_scan_budgets.py` |
| 5 | The Rescan button is bounded (20 s); truncated re-checks continue through the ordinary poll | `fix/poll-scan-budgets` | 〃 |
| —(3-flood) | After force_rescan the delta poll shipped the whole workspace as "updated": content-equal re-parses now keep their old `last_parsed` — only genuinely changed rows flow | `fix/poll-scan-budgets` | 〃 |
| 6 | Working-copy GC unions in every LIVE peer window's open-chip key (`instances.peers()` + `working_copy.key_for` — THE derivation, never a twin); liveness alone protects (peer memory is invisible to the scan — the point); registry trouble degrades, never blocks GC | `fix/gc-instance-registry` | `test_multi_instance.py::TestGcPeerProtection` |
| 7 | Leaf-index freshness compares against what the rebuild CAN ingest (state.json-bearing dirs); a meta-only dir no longer costs a rebuild per query; failing-ingest timestamps memoized per dir-set (re-attempt when the set changes; dirty-rebuilds bypass) | `fix/history-scale` | docs/106 |
| 8 | One fewer full stat sweep per scan (the inside-lock gate sample IS the scan cursor). A TTL memo was tried and **reverted** — it broke the pinned write-then-poll contract; the revert is documented in code and pinned | `fix/poll-scan-budgets` | `test_poll_scan_budgets.py` |
| 9 | The 50k-dir discovery cap is surfaced as an honest line under the sidebar root (docs/94 rule) | `fix/poll-scan-budgets` | 〃 |
| 10 | History panel defaults to 50/page with **All** first-class (the pagination doctrine); "N snapshots · X MB" footprint line (whole-dir walk, cached per (count, newest-ts)); retention mentioned only when a real budget exists | `fix/history-scale` | docs/106 |
| 11 | `/datasets/changes-since` carries `scan_ms`/`folders`/`partial`; per-folder deltas carry `scan_ms`; slow rescans log — "the table stopped updating" is now diagnosable | `fix/poll-scan-budgets` | 〃 |

## Verification shape

Each branch ran its own pinned suites green before push (docs/80 poll
suites 90; bulk suites 75; history suites 249; multi-instance/working-copy
94; param-history 59+); the six branches merged conflict-free; the full
Windows suite ran on the integrated tree against the 14-fail environmental
baseline (result recorded in the integration commit). Two design reversals
happened DURING the work and are themselves pinned: the `_current_mtime`
TTL memo (#8, broke write-then-poll) and htmx's `changed` modifier (#2,
broken for checkboxes in 2.0.4) — both replaced with contract-preserving
designs rather than relaxed tests.

## Still open from docs/104

Tier 2's un-taken residue stays listed there honestly: `/datasets` full-page
row embed (#17-adjacent), `_workspace_token` max() rider (#15), alignment-
scan stat batching (#16), pragma/extract-cache sizing (noted in docs/106 as
out of scope). Tier 3/4 (~22 UX items) are reserved for the joint session.
