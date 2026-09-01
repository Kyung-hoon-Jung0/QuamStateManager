# docs/153 — Overview tiles drag-reorder (docs/150 v2 item 1) (2026-09-01, user)

The first of the docs/150 v2 follow-ups, user-selected ("1번만 해").

## Behavior

Every identified Overview tile is `draggable` (grab cursor signals it; the
ghost "+ Add panel" tile stays last and is not). Dragging previews the move
LIVE (the tile relocates under the pointer via before/after-midpoint
insertion), the dragged tile ghosts (`.ov-dragging`), and the resulting
order persists on dragend into the SAME prefs key
(`quam_overview_tiles_v1.order`). The docs/150 default-elision rule holds:
a drag that lands back exactly where the default order stands stores
NOTHING (`_ovDefaultOrder` snapshot comparison) — storage stays empty at
defaults, and "customized · reset" toggles accordingly.

At render the stored order is applied after the removed-filter and the
added-tiles append; **ids the stored order does not know append at the end
in their default relative order** (a newly added custom tile, `ro_gef`
appearing on a chip that starts measuring GEF). A stale id in the order
list (a removed tile, a custom index shifted by deletion) is simply unused.
Reset (note link / settings panel / kebab popover) clears the order with
everything else. The settings-panel hint now teaches the drag.

Pinned by C8 in `overview_custom_selfcheck.cjs`: draggable attr, no-move
drag stores nothing, live dragover preview, dragend persists with the moved
tile first, note shows, order survives a full re-mount, reset restores +
clears. Mutations red ×2 (stored order never applied; dragend never
persists). CDP in real Chrome on a served chip: drag t1 to the front →
`order: ["t1","chip_size","gate1q",…]` stored + note shown → full page
RELOAD → t1 still first → reset → chip_size first, storage null.

Remaining docs/150 v2 (still not built): server-side save/share, custom
expression tiles.
