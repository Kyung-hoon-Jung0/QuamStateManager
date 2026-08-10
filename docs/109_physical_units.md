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

## Verification

`tests/test_physical_units.py` (12): the −20 dBm identity (FSP 0 + amp 0.1 —
the docs/95-pinned example), alias-path equivalence, LF volts + formats, MW
zero / text / non-amplitude / broken-chain / no-ancestor all blank, /bulk +
/qubit surfaces annotate, and a portless chip renders ZERO annotations.
Guard suites green: `test_bulk_edit`, `test_pair_columns`, `test_bulk_virt`,
`test_value_delta`, `bulk_dyncols/bulk_virt/tab_focus/ctrlz` selfchecks.
