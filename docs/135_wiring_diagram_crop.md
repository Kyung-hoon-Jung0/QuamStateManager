# docs/135 — The rack was never truncated by the data; it was cropped by the drawing

**Date**: 2026-08-23 · **Branch**: `fix/wiring-diagram-crop` · **Surfaces**: Instrument Wiring, Generate/Re-generate wizard step 5

Follow-up to docs/134, from the same customer session on the CQT
`#1810_20_qubit_flux_short_distortion_122315` 20Q chip. Three reports.

## ① "Why does it take so long?" — and the answer had to be visible

Measured, real Chrome, real chip, cold server: **6.1 s** from pressing
"Modify wiring…" to a diagram. The breakdown was not where it looked:

| step | cost |
|---|---|
| `/regenerate?step=5` | 0.03 s |
| `/regenerate/reconstruct` | 0.28 s |
| **`/generate/envs`** | **3.80 s** |
| 7 × `/generate/probe` (parallel) | ~0.7 s wall |
| `/generate/allocate` (QM-stack subprocess) | 1.38 s |

`/generate/envs` was `conda env list --json` — **4.0 s measured**, against
~0.04 s for everything else discovery does (`~/.conda/environments.txt`
1 ms, uv/.venv scan 40 ms). It re-answered the same question on every
visit, in front of the whole auto-allocate chain.

**Fixes:**

- `config_generator._conda_env_paths()` memoizes the conda subprocess on an
  **mtime fingerprint of the inventory** — `~/.conda/environments.txt` plus
  every known env's *parent* directory. Creating or removing an env touches
  both, so the memo invalidates exactly when the answer changes; nothing is
  time-based. A conda that is unreachable or errors is **never** cached (the
  registry file still carries the envs, and a transient failure must not
  stick). `discover_envs(refresh=True)` / `GET /generate/envs?refresh=1` is
  the escape hatch for anything a stat cannot see.
- `create_app` warms that memo in a **daemon thread at startup**, so even the
  first wizard visit of a process pays nothing. Skipped under `testing=True`
  and under `SM_DISABLE_ENV_WARMUP=1` (set by `tests/conftest.py` — a dozen
  test modules build a real app, and a 4 s background subprocess in each is
  both waste and a race against the cache-reset fixture).

Measured after: `/generate/envs` **4.14 s → 0.03 s** on the second call,
and the wizard's zero-click diagram **6.1 s → 1.8 s** on the real chip.
The remaining ~1.4 s is the allocate subprocess importing the QM stack —
a real cost, not a stall.

**And it now says so while it works** (the user's explicit ask: "at least
show `...` cycling `.` → `..` → `...`"). `busyHtml(label)` +
a pure-CSS `.sm-dots` ellipsis (three spans on staggered opacity keyframes —
no JS timer to leak across the re-renders every wizard edit triggers, no
inline script for the CSP to block, `prefers-reduced-motion` respected).
Each waiting state **names the phase it is in** rather than merely spending
the seconds: `Finding a Python environment…` → `Checking wiring…`, written
both into the diagram host and into `#gen-allocate-status` **at the button**
(docs/134's lesson: an answer in the scrolled-away `#gen-message` is no
answer).

## ② "3 MW + 4 LF on the page, 3 MW + 2 LF and cut off in the wizard"

One chip cannot have two inventories. Ground truth from `state.json`'s
`ports`: **3 MW-FEMs (slots 1–3) + 5 LF-FEMs (slots 4–8)**. `reconstruct_spec`
returns exactly that (verified directly: 8 FEMs, 79 lines), and the wizard's
`buildInstrumentData` explicitly adds every step-3 FEM even with nothing
allocated to it. The data was right on both surfaces. Both were **cropping**.

`renderInstrumentWiring` built each rack as:

```js
svg.setAttribute('width', svgW);              // 1884 on this chip
svg.setAttribute('style', 'max-width:100%;'); // and NO viewBox
```

With no viewBox, a width limit shrinks the **element's box** while the
drawing keeps its own coordinate system. Everything past the host width is
painted outside the visible box — and because the element itself now *fits*,
the host's `overflow-x: auto` never sees an overflow to scroll either. So
the rack was silently clipped, with no scrollbar and no hint, at whatever
width the pane happened to be:

How many were lost depended on the pane, so the two reports disagree with
each other and with the chip:

| surface | rack | the user's own window | measured here |
|---|---|---|---|
| `/instrument` | 1884 px (DIG column) | "3 MW + 4 LF" — FEMs 1–7 | 1194 px pane → FEMs 1–5 |
| wizard step 5 | 1356 px (no DIG column then) | "3 MW + 2 LF, cut off" | 862 px pane → FEMs 1–5 |

The two right-hand columns are different measurements, not one: the user's
counts come from their own (wider) window, mine from the panes I measured in
headless Chrome. What both agree on is the part that matters — **the drawing
was silently cut at whatever width the pane happened to be**, with no
scrollbar and no hint, and the user's *reference* view was already wrong:
the chip has 3 MW + 5 LF and neither surface said so.

**Fix** — every rack now carries `viewBox="0 0 W H"` +
`preserveAspectRatio` and its natural size in `data-nat-w/h`, and
`_applyInstrumentFit(container)` picks one of exactly two honest
presentations after mount:

- **Fit** — `width: min(100%, <natural>px); height:auto`; the viewBox scales
  the *whole* rack into the pane. Nothing is cropped, everything is on
  screen — and the cap means it is never MAGNIFIED either: `width:100%` on a
  viewBox'd svg scales up as happily as down, which would have blown up every
  chip whose rack is narrower than its pane (review finding).
- **1:1** — intrinsic px size, `max-width:none`; the host's existing
  `overflow-x:auto` genuinely scrolls. Full-size port labels and drag
  targets.

Default with no recorded preference: **Fit while it stays legible**
(`_INSTR_FIT_FLOOR = 0.55`), 1:1 below that — scaling a rack to a third of
its size trades one unusable view for another. On this chip `/instrument`
lands at 0.63 and shows all 8 FEMs at once; the wizard's 862 px panel, once
the DIG column widened the rack to 1884 px, falls to 0.46 and stays 1:1 with
the bar offering Fit. A rack that already fits gets no chrome
at all; one that does not gets a sticky bar (`left:0`, so the hint does not
scroll out from under the thing it is explaining) naming the situation and
offering the other mode. The choice rides `localStorage` (`quam_instrument_fit`)
and always beats the width-derived default, in both directions — with an
in-memory copy so the toggle still works where the write is refused (private
mode, site data blocked), released again as soon as a write lands so another
tab's change is not shadowed for the session. A
`ResizeObserver` on the container re-decides when the *pane* changes without
the window doing so (docs/122's lesson: sidebar collapse, split drag and
panel swaps are invisible to a window-resize listener).

Because this lives inside the renderer, it applies to every mount at once:
`/instrument`, the wizard, the instrument-compare view, the docs/126
floating panel.

## ③ "The QDAC driver — is it missing?"

The wizard showed:

> ⚠ 11 QDAC-biased qubit(s) carried into the spec (q1, q11, q13, q15, q17,
> q18, q20, q3, q5, q7, q9) — the rebuild needs an env with
> `quam_config.qdac_components`.

**Nothing was missing.** Probed in the `cqt` env: `quam_config.qdac_components`
imports (from the customer's own
`CQT\CS_installations\...\quam_config\qdac_components.py`) and `qdac_2_driver`
imports (from `D:\work\Customer_Codes\qdac2-driver`). `POST /generate/capabilities`
reports `instr.qdac` and `wire.qdac_trigger_line` **available: true**. The
capability registry has covered both since docs/119, and
`required_capabilities` already requests them whenever `spec.qdac.qubits` is
non-empty.

What actually happened: the demo server had inherited a stale
`PYTHONPATH=D:/work/temp/iqcc_2/.../superconducting` from the shell that
launched it, and *that* tree's `quam_config` has no `qdac_components`. Import
resolution took the shadowing copy. The server was relaunched with a clean
environment and the capability probe answers `true`.

Three real defects in the note itself:

- **Every reconstruct note wore a warning glyph.** `_regenerate.html` rendered
  the whole list as `"⚠ " + notes.join(...)` in warn colour, so a statement
  about what was carried was indistinguishable from something that could
  bite. `ReconstructedSpec` now has a second list — `info_notes` — and the
  renderer gives it its own muted `ℹ` line. That split is the reason the user
  went looking for a driver at all.

- It **asserted the requirement as if it were unmet**, from code that has no
  idea what the env contains. It is now a statement of fact — "11 qubit(s)
  are flux-biased by the QDAC-II rather than an OPX port … carried into the
  spec as they are" — and points at the capability check on the review step,
  which is the thing that actually knows.
- The qubit list was **lexically sorted** (`q1, q11, q13, …, q3, q5`), which
  reads as scrambled on a 20-qubit chip. `_nat_key` sorts digit runs as
  numbers, with each part tagged so a mixed naming scheme sorts instead of
  raising: `q1, q3, q5, q7, q9, q11, q13, q15, q17, q18, q20`.

## ④ "Can the circle move with the cursor too?"

The step-5 drag ghost (r15 CG2, docs/70) was a bordered label box with a
small primary-coloured dot: it read as *some text is following me* rather
than *I am holding this port*.

The ghost now **carries the port itself** — `portGlyph(el)` clones the
dragged node out of the rack into a small standalone `<svg>`:

- **Cloned, never re-drawn.** Role colour, radius, label truncation and the
  chord-fitted font size all live in `app.js`'s `_appendPortCircle`; a second
  copy of that logic in `generate.js` would drift the first time either
  changed.
- The `<svg>`'s `viewBox` is the source's own `getBBox()` (+2 units, because
  the circle's 1.5px stroke sits half outside the geometry box) and its
  `width`/`height` come from `getBoundingClientRect()` — so the glyph matches
  the rack whether it is drawn 1:1 or scaled to fit (§②), **floored at 24 px**
  so a fit-scaled feedline sub-circle (~15 px) is not a speck under the
  cursor. `overflow:visible`, since an SVG root clips by default.
- **A grip carries the whole cell.** Dragging the feedline grip moves every
  qubit on that port, so the glyph is the whole `.iw-port` group, not the grey
  grip bar — which on its own says nothing about what is being moved.
- **The original dims** (`.iw-port-lifted`, opacity 0.3) on exactly the node
  that was lifted — one circle, or the whole cell for a grip — so dragging one
  qubit off a shared port never greys out its peers, and the port is never in
  two places at once. Cleared in `removeDragGhost`, because a *cancelled* drag
  (Escape, invalid target, mouseup on nothing) does not re-render the rack and
  a permanently faded port looks broken.
- A stale `iw-port-ok`/`iw-port-bad` ring is stripped from the copy: validity
  is about the **target**, not about what you are carrying.
- The text label stays. It looked redundant next to a circle that already
  shows the id — until the real-browser check: on a fit-scaled rack the
  circle's own label is ~7 px and unreadable.
- Degrades by construction: no `getBBox` (a detached node, a realm without
  layout) → `portGlyph` returns null and the original dot renders, never a
  throw.

## ⑤ "The digital ports vanish when I press Modify wiring"

Reported mid-session, on the same chip. `/instrument` draws a DIG sub-column
per FEM (docs/126 #22) with the 11 QDAC-II trigger lines on it; the wizard's
step-5 diagram, reached through that page's own "Modify wiring…" button,
showed no digital column at all. Two independent causes, stacked:

1. **The dry run never allocated them.** The QDAC trigger lines are allocated
   by a *second, isolated* `Connectivity` (`_allocate_qdac_triggers` — docs/119:
   `create_wiring()` raises on any line type outside its whitelist, and the
   trigger's `"qt"` type is not in it). `run_build`'s **build** mode called it;
   `run_allocate` did not. So the allocation the wizard drew from simply had no
   `qt` entries. The build was always going to create those lines — only the
   preview was blind. `run_allocate` now runs the same pass, sharing the same
   `instruments` pool, and merges its result in.
2. **The regroup knew only analog.** `generate.js`'s `buildInstrumentData`
   mapped every channel into `output_ports`/`input_ports`; the word "digital"
   did not appear in the file. `qt` (and any `io_type: "digital"`) now lands in
   `digital_ports`, which is exactly what `renderInstrumentWiring`'s existing
   `hasDigital`/DIG-column code has been reading since docs/126. Trigger ports
   are deliberately **not draggable**: they are derived from `spec.qdac`, not
   from `spec.lines`, so offering to re-pin one would offer an edit nothing
   could carry out.

### And then the picture showed something worse

With the column finally visible, the two surfaces disagreed: `/instrument`
showed the 11 triggers on **4** ports, the wizard on **11**. The chip is real
and the lab's cabling is the reason —

```
con1/4/1 <- q1, q9, q17    (all trigger_port ext1)
con1/4/2 <- q3, q11, q18   (ext2)
con1/4/3 <- q5, q13, q20   (ext3)
con1/4/4 <- q7, q15        (ext4)
```

one OPX digital output per QDAC-II **ext trigger input**, shared by every
qubit armed on it, port N ↔ extN. `_allocate_qdac_triggers` gives each biased
qubit its **own dedicated** port — a documented docs/119 decision ("no port
sharing"), and harmless for a fresh build. On a **re-generate** it is not: the
rebuilt chip would expect 11 cables where the bench has 4, silently, and
nothing said so. Before this change the whole question was invisible.

So the pin is now carried: `reconstruct_spec` records each biased qubit's
existing `qt.digital_output` as `spec.qdac.qubits[q].trigger_pin`
(`{con, slot, port}`), and `_allocate_qdac_triggers` uses pins **verbatim**,
auto-allocating only the unpinned. Sharing round-trips for free — two qubits
with the same pin keep the same port. A pin on a FEM the reconstruction does
not declare is **dropped out loud** (that qubit returns to the allocator)
rather than becoming a dangling reference; a malformed pin warns and falls
back. An all-pinned chip returns before `qualang_tools` is even imported, so
the preserving path does not depend on the allocator being available.

## Verified

- **Live, real Chrome, real chip** — wizard step 5: **1.8 s** to diagram with
  zero clicks (was 6.1 s); all **8 FEM columns** present, 5 visible at 1:1
  with the rack scrolling to reach 6–8, and **all 8 visible** after "Fit
  width"; `Checking wiring` + animated dots observed in both the diagram host
  and the button's status during the wait.
- `tests/instrument_fit_selfcheck.cjs` — **21 executed jsdom assertions**
  against the real `app.js` renderer (all 8 FEMs in the DOM in every mode ·
  a width limit is never applied without a viewBox · fit above the floor ·
  1:1-and-scrollable below it · the bar appears only on overflow, flips the
  mode and persists it · a stored choice beats the default both ways),
  **mutation-checked, 8/8 CAUGHT** (viewBox removed + crop restored · fit
  sizing disabled · stored preference ignored · floor removed · bar
  suppressed · fit ceiling removed · max-width ceiling removed · in-memory
  choice removed).
- `tests/generate_autoalloc_selfcheck.cjs` extended to **83 assertions** —
  the waiting lines are pinned to name their phase AND to carry the animated
  ellipsis, in the diagram host and at the button.
- `TestEnvDiscoveryCache` (4 pins: one subprocess for an unchanged
  inventory · `refresh` forces a rescan · a changed envs directory
  invalidates · a failed scan is never cached) +
  `test_qdac_qubit_list_is_naturally_ordered` + the reworded-note pin
  (which also asserts the carry does **not** ride the ⚠ `notes` list).
- Live: the note renders as `ℹ 11 qubit(s) are flux-biased by the QDAC-II
  … (q1, q3, q5, q7, q9, q11, q13, q15, q17, q18, q20)` in muted colour.
- **Drag glyph, real Chrome, real drags**: a circle drag carries an orange
  `q1` circle (24 px, floored up from the fit-scaled 15 px) beside the
  `q1 · rr` label with the source at opacity 0.3; Escape restores it; a grip
  drag carries all **7** circles of that feedline with the whole cell dimmed;
  no `.iw-port-lifted` left behind after any path.
- **Digital triggers, end to end on the real chip.** The wizard's step-5
  diagram now shows the same DIG columns as `/instrument`: 11 trigger circles
  in **4 occupied cells**, identical on both surfaces (before: 0 in the
  wizard). A trigger circle reports `cursor: default` and a mousedown+drag on
  it creates no ghost. The `/generate/allocate` dry run returns 11 `qt`
  channels on 4 distinct ports.
  **The proof that matters**: a real `run_build --mode build` from the
  reconstructed spec produced trigger wiring **byte-identical to the source
  chip** — `q1,q9,q17 → con1/4/1 · q3,q11,q18 → con1/4/2 · q5,q13,q20 →
  con1/4/3 · q7,q15 → con1/4/4` — with all 11 QDAC-biased qubits and the
  top-level `qdac` instrument rebuilt. Before the pin carry this was 11
  dedicated ports.
- `TestQdacTriggerPins*` in `tests/test_regen_spec.py`: the pin is carried,
  sharing survives, a pin on an undeclared FEM is dropped with a note, a
  qubit with no trigger wiring gets no pin, a malformed pin warns and falls
  back, and an all-pinned chip resolves with `instruments=None` — proving
  that path never reaches the allocator.
- `tests/generate_dragghost_selfcheck.cjs` extended (G16–G17: the
  allocation→diagram regroup buckets `qt` as digital, shared ports collapse
  into ONE cell, analog lines are untouched, an empty FEM still gets a
  bucket, and a digital port is not draggable while the analog port beside
  it still is), G18 (the UI-zoom division), plus (G10–G15: the clone and
  its colour/label, viewBox + on-screen sizing, the size floor, the
  hasglyph marker, per-node dimming and its cleanup, the grip carrying the
  whole feedline, the stripped validity ring, and the two CSS rules) —
  **mutation-checked, 15/15 CAUGHT** — including the four the first pass
  could not have caught, which the review found: sizing from the bbox rather
  than the on-screen rect, height computed from width, the label dropped
  whenever a glyph is present, and the UI-zoom division. Those three asserts
  were under-discriminating because the fixture handed one node the SAME
  bbox and client rect; the fixture now makes them differ (84×63 vs 42×42). Its pre-existing G4–G9 cases use nodes
  with no `getBBox` at all, so they double as the pin that a realm without
  layout still gets the old dot instead of an exception.
- All 73 selfcheck suites green; full `cqt` pytest sweep at the docs/87
  environmental baseline.

## Review round (same-day, 39 agents: 4 lenses → per-finding refutation)

34 findings raised, **12 survived refutation** plus 6 from the completeness
critic — 18 fixed, all of them in this document's own work. The ones that
mattered:

- **MAJOR — Fit had no ceiling, and the selfcheck pinned the magnification as
  correct.** `scale` clamps at 1 for the *decision*, but the style applied was
  an unconditional `width:100%` on a now-viewBox'd svg, which scales up as
  freely as down. Every chip whose rack is narrower than its pane — i.e. every
  chip smaller than the 8-FEM one this was measured on — would have been blown
  up past natural size, with no fit bar in that state to undo it, in a picture
  whose whole job is to be a faithful rack. Worse, `A4 a rack that fits is
  simply drawn` asserted exactly the magnified state. Capped at natural width;
  the assertion now rejects a bare `100%` (a percentage that happens to parse
  below the natural width is not a ceiling), and two mutations cover it.
- **MAJOR — a pinned QDAC trigger port was never reserved.** The auto pass
  cannot see a pin: the main allocation consumes only analog lines, so the
  digital pool it walks is fresh and it hands the first unpinned qubit the
  first free digital output — exactly the port the pinned qubits are cabled
  to. Not cosmetic: the OPX digital output drives one QDAC ext trigger INPUT,
  so a qubit declaring `trigger_port: "ext3"` that lands on the ext1 cable
  pulses ext1 while its own channel waits on ext3 and the bias never arms. A
  colliding auto result is now moved to the next free port on that FEM and
  announced; a full FEM degrades instead of double-booking.
- **MAJOR — a pin was honoured with no FEM check at the point of use.** The
  membership guard lives in `reconstruct_spec` and runs once, at hydration —
  and step 3 lets the user delete a FEM afterwards. The pin then reached
  `_apply_qdac`'s `create=True` and landed a digital port on a FEM the rebuilt
  chip does not declare: the dangling reference the guard exists to prevent,
  by a route the guard cannot see. Re-tested in `_allocate_qdac_triggers`.
- **MAJOR — the preview drew trigger ports the build would refuse to create**
  when `quam_config.qdac_components` is absent. Now caveated — using a
  `find_spec`-based presence check rather than a real import, because the real
  import costs ~2.8 s in the customer's env (3× the whole allocation) and the
  first attempt at this warning tripled the dry run to 4.2 s before the
  measurement caught it. Only the definite negative is used; "present" is
  never claimed to mean "importable".
- **MAJOR — the startup warm-up ran `conda` on every launch of the packaged
  desktop app**, which is a `console=False` build, so it flashed a console
  window with no user action. `_run_command` now passes `CREATE_NO_WINDOW` on
  Windows — which also silences every other generator subprocess, a flash that
  was reachable long before this change.
- **MINOR ×3 (one defect) — the drag glyph was sized in zoom-inflated px.**
  The ghost is placed in the zoomed coordinate space (that is why
  `moveDragGhost` divides by `uiZoom`), so a size taken straight from
  `getBoundingClientRect` is scaled a second time and the carried port comes
  out `uiZoom×` too big at any UI scale ≠ 100%. Divided out; pinned by G18.
- **MINOR — three of the new drag-glyph asserts could not fail for the
  property they named.** The fixture handed one node the SAME bbox and client
  rect (42×42), so "sized from the on-screen rect" passed identically either
  way; the label assert read `ghost.textContent`, which the clone's own
  `<text>` satisfies; and every stub rect was square, so an aspect error was
  structurally invisible. Fixture now uses 84×63 against a 42×42 bbox, the
  label assert reads the label span, and height is asserted — the mutation set
  went from 11/11 to **15/15**, the four new ones being exactly the mutations
  the old fixture could not see.
- **MINOR — this document's own §② table was arithmetically impossible.**
  1884 px of rack cannot show FEMs 1–7 in a 1194 px pane. The user's counts
  and my measurements were two different window widths presented as one row;
  they are now separated and labelled. The same impossible sentence had been
  copied into an `app.js` comment and the selfcheck header (both fixed), and
  that comment also named `_fitInstrumentSvg`, a function that does not exist.

Also fixed: the Fit/1:1 button was inert where `localStorage.setItem` throws
(in-memory copy, released once a write lands); an empty conda scan was cached
self-confirmingly (never cached now); the reworded QDAC note asserted the
*result* of a check nothing had run (it now says the review step reports it);
`_allocate_qdac_triggers`'s return annotation still said `tuple[dict, list]`;
the `?refresh=1` escape hatch had no caller in the product (a "Rescan
environments" button now); the busy ellipsis did not actually cycle
`.` → `..` → `...` after its first period (`animation-delay` shifts a periodic
timeline — per-dot keyframes instead); and `run_allocate`'s QDAC merge had no
automated test at all (`TestAllocateModeShowsTheTriggers`).
