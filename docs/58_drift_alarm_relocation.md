# 58 — Live-drift alarm → Param History card

**Feedback:** when a user opens or updates states, a main-screen alarm banner
pops — *“📊 N parameters changed on the live chip since \<time\> [View
changes] [Reset baseline] [×]”*. Users report they almost never press Reset
baseline and don't find the popping alarm useful. Requested: move the
information under Param History so nobody has to deal with an alarm.

## What changed

- **The main-screen banner is gone.** `#live-drift-slot` removed from
  `base.html`; the entire banner-rendering path (`_renderBanner`,
  `_dismissDrift`, dismiss bookkeeping, the ts-local baseline formatter it
  used) removed from `app.js`. Nothing pops, ever.
- **Param History gains the card.** A collapsible (open-by-default) “Live
  changes since baseline” card at the top of `/param-history`, lazy-loading
  the same `/state/drift/view?embed=1` embed the State History page already
  uses — count, baseline time, per-param before/after/Δ table, and the Reset
  baseline button. The two pages share the `#live-drift-panel` id (only one
  renders at a time), so every existing `liveDriftChanged` refresh path
  covers both.
- **State History keeps its existing panel** (unchanged).
- **The `/state/drift` poll survives** — it was never the problem (server
  work is stat()-gated). With the banner gone its only job is dispatching
  `liveDriftChanged` when the count moves, which keeps an open Param/State
  History panel accumulating live, plus the one-shot auto-pull toast on
  chip load. `openDrift()`/the overlay stay for surfaces that link to them.

## Why not remove tracking entirely

The tracking layer (baseline sidecar, `/state/drift`, settle-gated reads) is
untouched — the complaint was the *alarm surface*, not the data. A watch-only
user still gets the accumulated diff — now at the moment they go looking,
in the two history pages, instead of over whatever they were doing.

## Tests

`tests/test_live_drift.py::TestDriftAlarmRelocation` — pins: no
`live-drift-slot` / `_renderBanner` anywhere in base.html or app.js (while
`_pollDrift` + `resetBaseline` must survive), the Param History card embeds
`/state/drift/view?embed=1`, and State History keeps its panel.
