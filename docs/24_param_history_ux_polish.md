# 24: Param History UX Polish — Slow-Route Loader, First-Visit CTA, Auto-Incremental Backfill

> What we did *after* the Phase 1 performance work in
> `23_param_history_performance.md` to soften the remaining cold-cache
> wait and to fix the "alignment banner says 125 experiments are
> available but the grid is empty" first-time-user puzzle.
>
> **Read this if you are:** working on Param History UI/UX, adding
> loaders to other slow routes, or wondering why an empty Param History
> tab shows a big import button instead of a generic "no data" message.

---

## Context — what was wrong

After Phase 1 (caches + server-side SVG), Param History was fast on
warm cache but two UX issues remained:

1. **Cold-cache wait felt like a frozen page.** First click on the tab
   still does the alignment scan + extract_property_history once
   (~500-800 ms), and the user only has a blank pane to look at.
2. **Worst first-time-user puzzle.** With workspace folders added and
   a chip loaded, the alignment banner happily reports
   *"125 of 2195 experiments are for superconducting"* — but the
   sparkline grid is empty. Nothing tells the user that those 125
   experiments need to be **imported** before they show up. The user
   had to find the small *Import from workspace ↺* button up in the
   filter row, click it, then wait, then come back. We saw this trip
   up everyone we tested with.

Both are correct behaviors of the existing code, but they read as bugs.

---

## What shipped

Two coordinated changes. They share the same loader element so the
UX feels consistent.

### 1. Slow-route loader — "QUAM STATE MANAGER" letter-fill animation

A small centered card that appears whenever an HTMX request to a known-
slow route takes longer than 200 ms. The card shows the title text
**Q U A M  S T A T E  M A N A G E R** with a per-letter gradient sweep
that gives the impression of ink filling in left-to-right. Pure CSS
animation — no JS in the hot path.

Why a typewriter-style word reveal instead of a spinner:

- Spinners read as "loading" but say nothing else; users compare them
  unfavorably to fast apps.
- Word-fill animations carry the brand and have a distinct cadence
  (Q → U → A → M…), so the user can *see* time passing in a meaningful
  way. Same trick as Claude Code's `Clauding…` indicator.
- Pure CSS keeps cost negligible. The 18 letter spans each have a
  16 ms `animation-delay` stagger; the sweep itself is a 1.6 s loop
  on `background-position`.

Implementation:

| Layer | Where |
|---|---|
| HTML | `web/templates/base.html` — single `<div id="quam-loader">` with one `<span>` per character (16 letters + 2 spaces). |
| CSS | `web/static/style.css` — `.quam-loader` (the card), `.quam-loader-text` (the row of letters), `@keyframes quam-loader-fill`, per-letter `animation-delay` rules, `@media (prefers-reduced-motion: reduce)` fallback. Uses Pico CSS tokens (`--pico-primary`, `--pico-muted-color`, `--pico-card-background-color`) so it tracks light/dark theme automatically. |
| JS  | `web/static/app.js` → `setupSlowRouteLoader()` IIFE. Listens to `htmx:beforeRequest`, sets a 200 ms timer that adds `.visible` to the loader. Cancels on `htmx:afterRequest` (success or error). Scoped to a `SLOW_PREFIXES = ['/param-history']` list — fast routes (search, sidebar toggles, dataset clicks) never see the loader. |

Why 200 ms grace period: the human "I'm waiting" threshold sits around
200-300 ms. Anything faster than that is perceived as instant; flashing
a loader for a 50 ms request makes the UI feel busier than it is.

To add other slow routes later, push their prefix into `SLOW_PREFIXES`.

### 2. First-visit CTA + auto-incremental backfill on revisit

The empty-state pane was replaced for the case **"history is empty AND
the workspace alignment scan found ingestible experiments"**:

```
┌──────────────────────────────────────────────────────────┐
│         No history imported yet for this chip.           │
│                                                          │
│   125 experiments in workspace match this chip and are   │
│              ready to ingest.                            │
│                                                          │
│              ┌──────────────────────────────┐            │
│              │ Import 125 experiments       │            │
│              └──────────────────────────────┘            │
│                                                          │
│   One-time setup. After this, new experiments will be    │
│   picked up automatically.                               │
└──────────────────────────────────────────────────────────┘
```

The button calls the same `/param-history/backfill` endpoint as the
existing *Import from workspace ↺* control in the filter row. The
button is just a much louder affordance.

While the import is running, the **same** `#quam-loader` element from
the slow-route loader is shown — but with one extra detail: the new
`<div id="quam-loader-progress">` element below the title spans is
populated by the JS poller with `Importing… 234 / 2070`. So the user
sees both the playful animation and concrete numerical progress.

```
        Q U A M   S T A T E   M A N A G E R
                Importing… 234 / 2070
```

Once `status === 'done'` arrives from the polling endpoint, the JS:

1. Hides the loader.
2. Calls `_paramHistoryMarkImported()` which writes
   `localStorage["quam_imported_<chip_key>"] = "1"`.
3. Re-fetches `/param-history` via HTMX so the grid renders with the
   freshly-ingested data.

#### Auto-incremental on revisit

After the user has imported a chip at least once, the localStorage
flag enables a silent re-sync on subsequent visits when there's
meaningful new work to do.

`paramHistoryMaybeAutoBackfill()` runs after every Param History swap
(`htmx:afterSwap` listener targeting `#param-history-root`) and on
direct page load. It auto-fires only when **all** of these hold:

- `data-is-loaded-chip="1"` — the user is viewing the loaded chip
  (backfill always operates on `store.folder_path`, not on archived
  chips browsed via the chip selector).
- `localStorage["quam_imported_<chip_key>"] === "1"` — the user has
  imported this chip at least once. First-visit case is handled by
  the CTA card, never by silent auto-fire.
- `importable_count >= snapshot_total + 5` — at least 5 more workspace
  experiments are aligned than the index has entries. The threshold
  avoids re-running the full backfill for one or two stragglers, which
  the periodic auto-snapshot path already covers.

The threshold of **5** is tunable in one place
(`paramHistoryMaybeAutoBackfill` in `app.js`). Raise it if users
report the loader appearing too often; lower it if backlogs are
piling up unnoticed.

When auto-fire kicks off, the user sees the same loader + counter
they saw on first import — same animation, same progress text. No
hidden surprises.

#### Heuristic limitations (worth knowing)

- `importable_count` (from alignment scan) and `snapshot_total` (from
  SQLite index) measure different populations: the former is workspace
  experiments whose fingerprint matches; the latter includes manual
  saves and live-poll captures that may not be in the workspace at
  all. So `total > importable` doesn't always mean "no new work".
  In that case the auto-fire skips, and the user can still click the
  filter-row Import button manually. We accept the false negative
  rather than over-firing.
- The localStorage flag is per-browser-profile. Switching machines
  resets it — first visit on a new machine shows the CTA again. This
  is intentional: we don't want a second device to silently re-import
  a chip the user hasn't acknowledged there yet.

### Backend changes

Single addition to the `/param-history` route in `web/routes.py`:

```python
importable_count = 0
if alignment is not None:
    importable_count = int(alignment.get("counts", {}).get("aligned", 0) or 0)
```

Passed to the template as `importable_count`. We deliberately use the
`aligned` count only (not `aligned + renamed`); `renamed` would
require `force_renamed=True` to actually ingest, so showing it on the
button risks "I clicked Import N but only got M imported".

The template gates the CTA card on:

```jinja
{% if summary.total == 0 and is_loaded_chip and importable_count > 0 %}
```

So:
- Archived (non-loaded) chips never show the CTA — the import button
  wouldn't work for them anyway.
- Chips with truly empty workspaces still get the previous explanatory
  empty-state ("Snapshots accumulate when you save…").

`#param-history-root` gained four data attributes that the JS reads
to decide whether to auto-fire:

```html
<div id="param-history-root"
     data-loaded-chip-key="superconducting"
     data-is-loaded-chip="1"
     data-snapshot-total="0"
     data-importable-count="125">
```

---

## Files changed (commits `2635ca8` + `2d9ac4c` + `4131ecb` + `5325dc5`)

| File | What changed |
|---|---|
| `quam_state_manager/web/templates/base.html` | New `#quam-loader` + `#quam-loader-progress` elements (rendered at the bottom of every page; CSS keeps them hidden by default). |
| `quam_state_manager/web/static/style.css` | `.quam-loader`, `.quam-loader-text`, `.quam-loader-progress`, `@keyframes quam-loader-fill`, per-letter `animation-delay` stagger, `prefers-reduced-motion` fallback. New `.param-history-cta` card styling. |
| `quam_state_manager/web/static/app.js` | `setupSlowRouteLoader()` IIFE; `paramHistoryImportFromCta(btn)`; `_paramHistoryMarkImported()`; `_paramHistoryHasImportedBefore()`; `paramHistoryMaybeAutoBackfill()`; extended `_paramHistoryPollBackfill` to write the progress counter into the loader; auto-trigger hooked into `htmx:afterSwap` and DOMContentLoaded. |
| `quam_state_manager/web/templates/_param_history.html` | New `param-history-cta` block in the empty-state branch; new data attributes on `#param-history-root`. |
| `quam_state_manager/web/routes.py` | Compute `importable_count` from alignment scan; pass to template. |

771 tests passing, 0 regressions. No new tests were added — the
animation is purely cosmetic and the backfill flow it triggers is
already covered by `TestParamHistory::test_backfill_endpoint_*`.
We rely on manual smoke testing for the visual layer.

---

## How to verify

1. **Slow-route loader.** With Param History on cold cache, click the
   tab. After ~200 ms the centered card should appear with the title
   text filling in letter by letter; on response arrival it fades out.
   On warm cache (rapid back-and-forth), the loader should never
   appear.
2. **First-visit CTA.** With `instance/` wiped (so no chip history),
   load a chip, add a workspace root that contains aligned experiments,
   click Param History. Expected: CTA card with the count, big
   primary button. Click it. Expected: loader animation + counter
   `Importing… X / N` ticking up. On done: page reloads, grid
   populated.
3. **Auto-incremental on revisit.** After step 2, navigate away and
   back. Expected: no CTA (history > 0), grid renders immediately.
   Now add 6+ new aligned experiments to the workspace and revisit.
   Expected: loader briefly appears (importable_count - total >= 5
   triggers auto-fire) and grid refreshes with the new data.
4. **Threshold guard.** Add 1-2 new aligned experiments and revisit.
   Expected: no auto-fire. The new data will land via the next live
   auto-snapshot.
5. **Reduced-motion preference.** In OS settings, enable
   "Reduce motion". Reload. The loader's letter-fill animation
   stops; text shows in static muted color. Functional behavior
   unchanged.

---

## Follow-up changes (after first pass)

Two of the three "Future work" items above shipped shortly after the
original pass. Documented here so the parent feature stays the home
for the whole story.

### Topbar import-status pill (commit `5325dc5`)

The in-page loader hid the moment the user navigated away from Param
History. Backfills run on a background thread, so the work kept going
server-side, but a user who clicked **Import** then switched to
Datasets or Explorer lost all visibility — no progress, no done
notification.

The pill is a small pill-shaped element next to the pending tray in
the topbar that polls `/param-history/backfill/status` with an
**asymmetric cadence**:

| State | Interval | Why |
|---|---|---|
| Idle | 30 s | Cheap recovery check — picks up a backfill that was started in another tab. |
| Running | 1 s | Real-time counter updates. |
| On-demand wake | event-driven | Custom event lets `paramHistoryImportFromCta()` request an immediate poll, so the first counter update lands within ~200 ms of clicking Import instead of waiting up to 30 s for the next idle tick. |

During a running backfill the pill shows `Importing X / Y (P%)` with
a thin spinner (`prefers-reduced-motion` falls back to a static dot).
On terminal states it flashes `Import done (N)` or `Import failed`
for 4 s, **but only if the pill was actually visible before** — a
visibility-guard avoids a phantom "done" flash on first page load
when an old finished job lingers in `_backfill_state`. Clicking the
pill at any time navigates to `/param-history` so the user can jump
back to the page that owns the operation.

Pure CSS + ~95 lines of JS. No new backend — reuses the existing
`/param-history/backfill/status` endpoint.

Files: `web/static/app.js` (+136), `web/static/style.css` (+42),
`web/templates/base.html` (+12).

### Auto-backfill threshold uses experiment-only count (commit `4131ecb`)

This doc itself called the false-negative out under
*Heuristic limitations*: comparing `importable_count` against
`snapshot_total` is wrong because the latter includes save / manual /
auto captures that aren't workspace experiments. Active hand-editors
accumulated enough non-experiment entries that `total` permanently
exceeded `importable`, silently suppressing every auto-fire.

Fix: `_param_history.html` now exposes
`summary.by_trigger.get('experiment', 0)` on `#param-history-root`
as `data-experiment-snapshot-count`, and
`paramHistoryMaybeAutoBackfill()` compares against that instead of
`data-snapshot-total`. Apples-to-apples now — workspace experiments
aligned vs. workspace experiments already ingested.

Behaviour change is strictly more eager: chips that were silently
stuck under the old comparison will now fire when they cross the +5
threshold. Chips with no manual editing see no change at all.

Files: `web/static/app.js` (~6 net), `web/templates/_param_history.html`
(+6).

---

## Future work (not in this pass)

- Apply the slow-route loader pattern to other heavy routes (Compare,
  Trend Tracker, dataset list at very large counts). Just push their
  prefix into `SLOW_PREFIXES`.

---

## See also

- `20_param_history.md` — the feature itself.
- `23_param_history_performance.md` — Phase 1 perf work (caches +
  server-side SVG render). This UX polish builds on top of those
  changes.
