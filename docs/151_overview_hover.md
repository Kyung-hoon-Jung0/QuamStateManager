# docs/151 — hovering an Overview tile lists every qubit/pair with its value (2026-09-01, customer via user)

Customer ask: *"panel에 마우스 hover하면 qubit 혹은 pair의 리스트 팝업이
뜨고, 옆에는 그 수치들 — modern + compact"*. The aggregates were already
computed FROM per-entity value arrays, so the popup lists exactly those.

## Behavior

Hovering any per-entity Overview tile opens a compact body-level popup:
title = tile title + "per qubit" / "per pair"; one row per entity — a heat
dot colored by the tile's OWN `cardColor` range (neutral gray when the tile
has no calibration range or the value is missing), the entity id, and the
formatted value. Sorted by value descending; missing values render "—" at
the end; over 48 entries an honest "…and N more" line (never silent
truncation). `pointer-events: none` — it can never block the docs/150 kebab
or flicker; hides on leaving the tile strip, on scroll, on re-render, and
when the customization popover opens. Multi-column (CSS columns) above 10
entries, so a 20-qubit chip reads as a dense 2–3 column card.

Composite tiles (Chip Size, RB Coverage, Qubits In Spec, Calibration Age)
register no entries and show no popup — their number is not per-entity.

## Provenance guarantee

The per-edge collectors were refactored into id-carrying `*E` variants
(`collect2QE`, `collect2QFieldE`, `irbEpcE`, `gateLenE`) and the value-only
forms are now DERIVED from them (`entries.map(x => x.v)`), so the hover
list and the tile's aggregate can never disagree — same numbers, same
per-pair best-of-gates rule. Node tiles list `_mv`-gated values (an
unphysical fit shows "—" here exactly as it is excluded from the average).
User-added (docs/150) tiles get entries automatically, node- or edge-kind
by key.

Pinned by C6 in `overview_custom_selfcheck.cjs` (sorted per-qubit list,
pair list with values, composite shows nothing, leave hides); mutations
red ×2 (hover wiring dropped, sort dropped). CDP on a served chip:
"Readout Fidelity (GE) · per qubit" with q0/q1/q2 · 96.00% + 3 heat dots
in a 230×109 popup, composite tile popup-free.
