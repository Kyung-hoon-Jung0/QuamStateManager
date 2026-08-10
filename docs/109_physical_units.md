# 109 — What actually leaves the instrument: dBm / V under every amplitude

*2026-08-10. User feedback: the entity surfaces carry no TRUE physical signal
characteristics — an ``amplitude`` is a bare decimal ("이게 대체 몇 dBm인지,
몇 volt인지 … 전혀 물리적 정보가 없다"). Wanted: the actual outgoing pulse's
physical dBm / V / ns shown abbreviated on the display panel and in the
tables, next to amp. The user invited a better-UX counter-proposal.*

## The physics (nothing invented)

- **MW channel** (drive/readout — the resolved port carries
  ``full_scale_power_dbm``): ``P_dBm = FSP + 20·log10(|amp|)`` — the exact
  identity the FSP-compensation feature (r12) and ``autofit/power_rows`` pin
  bit-exactly, and the same formula in calc.js / generate.js.
- **LF channel** (flux — the port resolves under ``ports.analog_outputs``):
  the waveform amplitude IS the output voltage; the annotation is unit
  NAMING (``12 mV`` / ``1.2 V``), which is exactly the missing information.
- **Lengths** needed no new work: stored in ns, the inspector's ``qty``
  filter already prints "100 ns" and the grid headers carry ``(ns)``.

`core/physical_units.py` (pure): `channel_of` walks ≤4 ancestors of the
amplitude leaf to the dict carrying ``opx_output`` (works on the alias AND
the resolved path — a pointer-aliased op like ``operations.x180 =
"#./x180_DragCosine"`` lands in the same channel), then resolves
``<channel>.opx_output.full_scale_power_dbm`` through the ONE resolver
(`pointer_path.resolve_field_target` — all hops in one call). **Honesty
rules**: text values, MW amp 0 (a fabricated "−∞ dBm" helps no one), a
dangling chain, or no channel ancestor ⇒ ``None`` ⇒ the surface stays blank.
Extension-shaped: a chip with no port chain renders byte-zero annotations
(pinned).

## The UX (the counter-proposal, as invited)

The user suggested a table COLUMN next to amp. On a real 452-column model a
twin column per amplitude would double the amp real estate; the same
information at the same place without the width cost is an **always-visible
muted sub-line inside the amp cell** (`.bulk-phys`, block-level like the
`.bulk-band-msg` precedent — aligned with the standing "all data always
visible, never hover-only" doctrine), plus the tooltip naming the formula.
The inspector gets the twin `.phys-note` ("≈ −20.0 dBm") beside the existing
unit-preview chip. **Live while typing**: the server stamps
``data-phys-kind``/``data-phys-fsp`` on the input and ONE delegated app.js
listener (`PhysAmp`) recomputes the sub-line as the user types — both grids,
zero per-module wiring; the server re-render on commit stays canonical.

Wiring: `_build_bulk_cell` gains ``cell["phys"]`` (both grids share it —
qubit AND pair cells annotate through the one builder); the inspector rides
a new ``phys_amp`` Jinja filter (web/app.py, reads the ACTIVE store) used by
`_qubit_detail.html` + `_pair_detail.html`, so every emit site converges on
one implementation.

## Verification (stage 1)

`tests/test_physical_units.py` (12): the −20 dBm identity (FSP 0 + amp 0.1 —
the docs/95-pinned example), alias-path equivalence, LF volts + formats, MW
zero / text / non-amplitude / broken-chain / no-ancestor all blank, /bulk +
/qubit surfaces annotate, and a portless chip renders ZERO annotations.
Guard suites green: `test_bulk_edit`, `test_pair_columns`, `test_bulk_virt`,
`test_value_delta`, `bulk_dyncols/bulk_virt/tab_focus/ctrlz` selfchecks.

## Stage 2 (user-approved ①②③): unit setting · Components columns · map hover card

The user's original intent was the **Chip Components menu** — and some labs
think in volts, not dBm.

**① One global unit setting.** Settings → "MW power display": `dBm · V rms ·
Both` (`localStorage quam_phys_unit`). The server always renders canonical
dBm and stamps `data-dbm`; `window.PhysAmp` reformats EVERY `[data-dbm]`
surface (grid sub-lines, Components cells, inspector notes) + the
`.phys-unit-label` column headers on load / every swap / unit change — one
setting, every surface, no server round-trip. V is **always labeled rms**
and the 50 Ω assumption is stated on the V surfaces (button + header
titles), never assumed silently. `vrms = sqrt(50·10^((dBm−30)/10))` — the
calculator's own dBm↔V section is the identity's reference.

**② Components tables.** `_qubits.html` + `_resonators.html` gained a
**P(RO)** column (real column — these tables are narrow, the /bulk width
argument doesn't apply): canonical dBm text + `data-dbm` + `data-sort` (the
shared sorter now prefers a cell's `data-sort`, so ordering is
display-unit-independent — "12 mV" vs "1.2 V" would parseFloat-compare 12 >
1.2). A cell annotates ONLY `kind == 'mw'` (an LF-resolving readout chain
must not print volts under a dBm header); the `-` blank carries an honest
reason title. Flux/Couplers already carried `(V)`/`(ns)` labels; the Pairs
CR drive amp is deliberately not annotated in the TABLE (its channel home is
flavor-dependent — `cr_semantics`; the pair INSPECTOR + Live-Edit pair grid
annotate it through the shared resolvers).

**③ Component-map hover card.** Hovering a stone/edge shows a floating
summary card = **the entity's table row re-read as label→value pairs at
hover time** (headers from the row's own `<thead>`) — it can never disagree
with the page, needs no endpoint, and the P(·) columns ride along already
unit-formatted. docs/92 §2.4 holds: numbers stay OFF the drawing (pinned);
the card is transient detail-on-demand and the same data lives permanently
in the table below ("all data visible" holds too). Body-level singleton,
`pointer-events:none`, DOM-API text only. Entities with no row on the
current page (a qubit stone on the couplers page) get the minimal
title+hint card, so hover never feels dead.

## The audit (user-mandated, adversarial)

A 31-agent workflow (5 lenses: JS correctness · Py/Jinja · user-UX · pinned
regressions · physics honesty → per-finding adversarial verify) confirmed
25 findings; every one was fixed or consciously ruled:

- popup: colSpan-aware header pairing (the pairs page's poisoned-run
  `colspan=7` rows would have labeled error text "Control"); native SVG
  `<title>` parked while the card shows (double-tooltip); hidden on
  re-mount + `htmx:beforeSwap` + capture-phase scroll (orphan/stale-position
  cases); same-entity memo (rebuild churn); minimal card for row-less
  entities.
- units: swap gates widened to `[data-dbm], .phys-unit-label` (an all-blank
  P(·) fragment must still relabel its header); grid tooltips became
  formula-only (a baked dBm value contradicted the V setting); `.bulk-phys`
  wraps under `Both` (23-ch nowrap widened the column);
  `PhysAmp.applyAll` runs BEFORE `_virtInit` (frozen widths + stashed cold
  HTML in the viewer's unit) and again on hydration.
- coverage: the popup had ZERO selfcheck coverage (mutation-verified) —
  `component_map_selfcheck.cjs` now pins card content/labels/minimal-card/
  re-mount-hide.
- consciously accepted: Python `%.3g` vs JS `toPrecision(3)` diverge only
  outside the DAC's physical range (±2.5 V); JS `toFixed(1)` vs Python
  `%.1f` rounding differs only on exact half-ULP boundaries.
