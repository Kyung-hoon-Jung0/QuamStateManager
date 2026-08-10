# 113 — Keyboard polish, overhead-gated (docs/110 #13)

*2026-08-11. docs/104 #13, user-approved with an explicit gate: implement
each item ONLY if its overhead is small. Four shipped, two gated out with
reasons — deferral is explicit, nothing silently dropped.*

## Shipped (all client-side, app.js + style.css)

- **`/` focuses the page's primary search** — the first visible search box
  in the main pane, else the topbar global search. Never hijacked while
  typing (visibility test is layout-aware so jsdom pins stay honest).
- **Ctrl+Enter = Apply all** when the Live-Edit button is ARMED (a disabled
  button does silently nothing — this is a CLICK, so the button's own
  confirm/warning path runs unchanged; no covenant bypass). Works FROM a
  grid cell, because that is where the user's hands are.
- **`?` shortcut cheat sheet** — a static overlay documenting every
  shortcut SM ships (Ctrl+K, /, ?, Alt+C, Ctrl+Z / Ctrl+Shift+Z, Tab
  hopping, the docs/111 grid selection keys, Ctrl+Enter, j/k/Enter/Space,
  [ / ]). Esc/backdrop/Close dismiss; the shared `trapFocus` keeps Tab
  inside; DOM-built text only.
- **Staleness gets a "when"** (docs/104 #16/#22): the status badge's
  tooltip now carries "last checked HH:MM:SS", stamped by the existing
  drift poll — "Synced" becomes a claim with a timestamp, not a vibe. No
  new poller.

## Gated out (the user's overhead condition)

- **ndview per-run view-state memory**: ndview's view state (variable,
  axes, slicers, decimation) is deeply coupled to the cube-cache
  mount-generation contract (docs/67/82); a naive sessionStorage restore
  risks cross-render bugs the corpus pins exist to prevent. Needs its own
  design pass — deferred, not dropped.
- **Banner group-collapse**: banners are independent partials owned by
  different subsystems (drift, type-alarm, chip-name, multi-instance, GC);
  grouping them is a cross-cutting layout refactor, not a polish item.
  Deferred to a dedicated banner-shell design.

Pinned by `tests/kb_polish_selfcheck.cjs` (9) + `tests/test_kb_polish.py`;
`ctrlz/tab_focus/sidebar_tools` selfchecks stay green.
