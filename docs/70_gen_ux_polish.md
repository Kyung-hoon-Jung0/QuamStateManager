# 70 — Config-Gen wizard UX polish (r15 feedback ②, 2026-08-02)

Four heavily-requested wizard fixes: a Modify-wiring jump from the Instrument
Wiring page, a cursor-following drag ghost + bigger monitor in the wiring step,
the chassis FEM-chooser popup landing in the wrong place (regression), and the
too-small / too-dim wizard typography.

## CG1 — "Modify wiring…" on /instrument

`_instrument_wiring.html`'s header gained a `btn-sync primary` CTA →
`hx-get="/regenerate?step=5"`. `regenerate_page` (routes.py) now reads a
clamped `step` query arg and passes it through `_regenerate.html`'s bootstrap
into `QuamGen.hydrateFromSpec(spec, {step})` — a parameter the wizard's
`applyDraft` has honored all along (it was just never passed). Step 5 = the
Wiring step, so the user lands on the drag-and-drop rack pre-filled with their
chip; `hydrateFromSpec` sets `wiringTouched: true`, so nothing is auto-refilled
over the reconstructed pins. The button renders only when a chip is loaded.

## CG3 — chassis FEM-chooser popup position (root cause + fix)

Root cause: `#gen-slot-menu` was `position: absolute` as a static child of
`#generate-root`, so its containing block is **`#content-area`**
(`position: relative` — the pageheader-toggle anchor), yet `openSlotMenu`
computed **page** coordinates (`window.scrollX + rect.left`). The menu landed
offset by the sidebar width (user-resizable 160–640px) + topbar + any banner —
and `quam_ui_scale`'s html CSS `zoom` multiplied the error (fixed/absolute px
inside a zoomed root are re-multiplied by the zoom). That's why it "used to be
next to the slot": the offset grows with sidebar width and UI scale.

Fix: `.gen-slot-menu` is now `position: fixed`; `openSlotMenu` places it at
`rect.left / uiZoom()` × `(rect.bottom + 4) / uiZoom()` (`uiZoom()` reads
`documentElement.style.zoom`, the quam_ui_scale mechanism), clamps to the
viewport (right-edge clamp, flip above when it would overflow the bottom), and
closes on `#table-pane` scroll (a fixed menu must not stay pinned while the
tiles scroll under it). The same zoom correction was applied to the
/instrument page's cursor-following `#port-popup` (`_showPortPopup` in app.js)
— same bug class.

## CG2 — drag ghost + monitor

The wiring drag is a custom mouse-event implementation whose
`preventDefault()` on mousedown suppresses even the browser's native drag
snapshot — nothing followed the cursor. Now `onWireDragStart` builds
`#gen-drag-ghost` (body-appended: escapes every clipping/transformed ancestor;
`position: fixed; pointer-events: none`), labelled `<element> · <role>` (grip
= "feedline"), zoom-corrected `clientX/Y + offset` follow on every mousemove,
validity-tinted (`gen-drag-ghost-ok/-bad` border follows `isValidDrop`),
removed on drop / Escape / cleanup.

The docked monitor `#gen-wiring-monitor` (the which-port panel above the
diagram): font 0.84em → **0.95em**, tag 0.82em → 0.9em, src/tgt bolded, and
`position: sticky; top: 0` inside the `#table-pane` scroller so it stays
visible while dragging near the bottom of a tall rack.

## CG4 — typography

**Presets bumped one notch** (user decision: S = old M, M = old L, L bigger):
`--font-size-base` 15px → **17px**; `html[data-font-size="small"]` 13 → **15**;
`"large"` 17 → **19**. The topbar ⚙ labels were STALE ("13/14/16px" vs the
real 13/15/17) — now show the true 15/17/19.

**The wizard now tracks the presets.** ~65 wizard-rule px font-sizes (selector
mentions `.gen-`/`#gen-`/`generate-root`/`.regen`/`.iw-`) were converted to em
against the old 15px default (13px → 0.867em, 12.5px → 0.833em, …), plus the
app-wide `.btn-sync` (13px → 0.867em — Back/Next/Auto-allocate were frozen).
Net effect: at the new S the wizard is byte-identical to the old default; at M
≈ +13% (~+2px — the ask); at L ≈ +27%. **Paddings/line-heights untouched**
(all px/rem/unitless — rem is inert because no rule ever sets the root font
size; verified). The two em-sized square buttons that WOULD have inflated
(`.gen-chassis-del`, `.gen-row-del`, 1.7em boxes) became fixed 26px. The two
wizard selects still riding Pico defaults (`#gen-naming-preset`,
`#gen-preset-select`) joined the compact-field selector list.

**Dark-mode text (app-global by user decision):** the `[data-theme="dark"]`
token block now overrides Pico — `--pico-color #c2c7d0 → #d0d5de` (normal
text brighter) and `--pico-muted-color #7b8495 → #98a1b3` (hint/description
gray clearer, ≈4.7:1 → ≈6.9:1 on the page bg). The wizard's 36 `.muted` uses
and all `--pico-muted-color` consumers app-wide brighten together. Light theme
untouched.

## Pins

`tests/generate_slotmenu_selfcheck.cjs` (fixed positioning, zoom correction,
no scroll terms, pane-scroll + Escape close) and
`tests/generate_dragghost_selfcheck.cjs` (ghost lifecycle, body-append, label,
zoom-corrected follow, grip label, sticky/enlarged monitor CSS) — driven by
`tests/test_gen_ux_selfchecks.py`. Font pins live in
`tests/test_web.py::TestFontPresetsR15` (preset values + topbar labels in
sync). The `_test` harness exports gained `openSlotMenu`/`hideSlotMenu`/
`attachWiringDrag`/`uiZoom` (selfcheck-only, not public API).
