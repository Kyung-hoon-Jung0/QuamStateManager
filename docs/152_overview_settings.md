# docs/152 — the visible door: an Overview settings button, global + per-tile statistic (2026-09-01, user)

The docs/150 customization worked — and nobody found it: the kebab appears
only on hover ("마우스를 hover해야만 아니까"). Discoverability fix, scoped
by the user: a visible settings button beside the Overview title, opening a
panel that applies a statistic GLOBALLY to all panels at once and also per
panel individually.

## Behavior

**⚙ Panels** (always visible, next to the Overview title; second press
toggles closed) opens a compact panel:

- **Statistic for ALL panels** — a segmented avg / median / min / max row.
  One click applies to every stat-capable tile (user-added tiles included);
  when every tile shares one stat, that segment shows active.
- **Per-tile list** — every tile whose big number IS an aggregate (a muted
  no-data tile has nothing to switch), each with its own select. Applies
  immediately; the panel STAYS OPEN across applies so tuning is one flow.
- **Reset all** (disabled when nothing deviates) + a hint line naming the
  other doors: "each tile's ⋮ can also remove it · '+ Add panel' adds one" —
  the panel teaches the hover features it complements.

Same storage (`quam_overview_tiles_v1`), same default-elision (a global
'avg' apply deletes every entry — storage stays empty at defaults), same
"customized · reset" note. Opening the settings closes the kebab popover
and the hover popup, and vice versa.

Pinned by C7 in `overview_custom_selfcheck.cjs` (open, ≥3 rows, hint,
global min applies + persists for every stat tile, panel stays open,
uniform segment active, per-tile back to default deletes only that entry,
panel reset clears storage, button toggles). Mutations red ×2 (global
apply emptied, stat-tile snapshot emptied). CDP on a served chip: ⚙ Panels
visible, 5 rows, global min → every tile `min`-tagged + stored
(`gate1q/ro_ge/ro_gef/t1/t2ramsey`), per-tile t1 → max while others stay
min, reset → storage null + `24.0 µs avg`.
