# docs/290 -- a size control over the wiring rack (Instrument Wiring + Generate Config)

2026-10-05, user request: on Instrument Wiring the readout port labels are too
hard to read; put a size control above the wiring layout, like other SM pages
have, and give the Generate Config wiring the same control.

## Why the labels were unreadable

A multiplexed readout port draws one small sub-circle per qubit, and
`_appendPortCircle` labels those at a fixed **7 px** (the single-line circles
get up to 14 px). The rack is then *scaled to fit* the pane (docs/135): an
8-FEM rack in a 1250 px pane is drawn at ~92 %, in the wizard's 860 px
panel at ~63 %, so a readout label lands at 4-6 px on screen. The row
numbers (10 px) and the OUT/IN heads (8 px) shrink the same way.

## What changed

- **One renderer, one control.** Every rack (page, dropped-chip preview,
  wizard step 5, wizard step 6, compare, report frame, floating panel) is
  drawn by `renderInstrumentWiring` in `app.js`. A caller that passes
  `{sizeControl: '<surface>'}` gets a size bar as the first child of the
  host, directly above the rack. Opted in: `'instrument'`
  (`_instrument_wiring.html`, `_instrument_preview.html`) and `'generate'`
  (`generate.js`, step 5 and the step-6 copy -- one key, so both follow one
  press). Not opted in, unchanged: the side-by-side compare, the report
  frame, the floating panel.
- **The control copies Chip Status's map zoom** (`.topo-hero-zoomctl`):
  minus / slider / plus / Fit, same button and slider rule (the CSS now lists
  `.iw-size-btn` / `.iw-size-slider` beside `.topo-hero-zbtn` /
  `.topo-hero-zslider`, so the look -- including the Pico `width:auto;
  margin:0` override -- is literally shared), no `role="group"` (Pico's
  full-width trap). Chosen over the Json Tree View's `Aa S/M/L` control
  because that one scales text rows, while this one, like the map, sizes an
  SVG diagram through its viewBox. Two additions: a percent readout, and a
  **1:1** button -- the docs/135 Fit/1:1 bar used to offer 1:1 in one press,
  and that bar is replaced on an opted-in surface (its note moves into the
  size bar, so there are never two bars).
- **Semantics.** The size is the rack's scale against its natural drawing
  (1 = the docs/135 1:1 mode) or `fit`. It is applied as the svg's
  width/height over its viewBox, so the labels are re-drawn larger, never
  stretched as a bitmap. Plus/minus walk a 0.25 grid from the scale on screen
  (a fitted 63 % goes to 75 % / 50 %); range 50-300 %. Magnifying is allowed
  only as the viewer's explicit choice; Fit still never magnifies.
- **Default = today.** With no per-surface record the docs/135 logic decides
  exactly as before (Fit while legible, 1:1 below the floor, the shared
  `quam_instrument_fit` choice still honoured). A corrupt or out-of-range
  record counts as no record.
- **Per surface, remembered**: `quam_wiring_size_instrument`,
  `quam_wiring_size_generate`; every access in try/catch, with an in-memory
  copy so the buttons still work where storage is blocked (the docs/135
  contract).
- **Never rebuilt mid-drag**: the bar is created once per render and updated
  in place by every re-apply (resize observer, press, slider), so the slider
  under the pointer survives. Wizard re-renders and htmx swaps re-run the
  renderer, which re-creates and re-binds the bar with the stored size.
- **Sticky** left and top inside its host, on `--pico-background-color`: it
  stays over the rack while the rack scrolls under it (the step-6 copy is
  capped at 48vh and scrolls both ways).

## Verified in real headless Chrome

A copy of a real 8-FEM chip (four multiplexed readout feedlines), served
from this worktree in a sandboxed home. Instrument
Wiring: default (Fit 92 %) renders the same rack as before; plus to 100 /
125 / 150 / 175 %, then 300 %; at >= 150 % the readout labels read clearly;
sideways scroll keeps the bar in place; a reload keeps the size; away to
Qubits and back, and browser back/forward, keep it and the buttons still
work; a real mouse drag on the slider resizes continuously with one bar.
Generate Config (the Re-generate wizard on the same chip): step 5 at 63 %
fit and at 200 % / 300 %, step 6's copy follows the same key, its bar stays
put while the capped box scrolls both ways; a reload keeps the size. Both
themes. No console errors (the only logged error was a 400 from a mistyped
folder path in the harness itself). The button metrics match the map
control exactly (25.1 px tall, 13.26 px text).

Seen, left alone: in the wizard's editable step 5 the feedline grip is drawn
over the first readout sub-circle and hides the first characters of its
label (`qA1` reads `A1`). It predates this change and is visible at every
size; moving it is a drag-affordance decision of its own.

## Pins

`tests/wiring_size_selfcheck.cjs` (62 assertions, real `app.js` and real
`generate.js` under jsdom) via `tests/test_wiring_size.py` (5 tests: the
selfcheck, `/instrument` and `/instrument/preview` opt in, the report frame
and compare do not). `tests/stress_generate.cjs` (manual CDP driver) now
presses the size bar's Fit/1:1 instead of the old bar's button.

Mutation sweep: 26 mutations, all red -- opt-in ignored; default forced to
fit; size not applied; never stored; other racks of the surface not following
(two variants); bar rebuilt on every apply; one key for all surfaces; no
in-memory copy; out-of-range record trusted; plus without the grid; no upper
clamp; no lower clamp; notes swapped; shared Fit/1:1 choice ignored; old bar
still drawn; Fit not marked active; bar below the rack; CSS rule not shared;
bar not sticky; wizard step 5 / step 6 not opted in; wizard on the page's
key; page / preview template not opted in; slider ignored. Two pins went
green on the first sweep and were strengthened: the "never two bars" check
ran only in a state where the old bar is not drawn anyway (now asked in
every sizing case), and the clamp check could land on its answer by parity
(now checked after every extra press).
