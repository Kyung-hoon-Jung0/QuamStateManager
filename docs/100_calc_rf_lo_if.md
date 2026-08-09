# 100 — Calculator verified + the RF = LO + IF bridge

*2026-08-10, 1.0-prep. Question asked: does the calculator actually compute
correctly, and is anything worth adding? Answer: every existing formula
checked out exactly (nothing to fix), and one new section was added — the
MW-FEM frequency bridge.*

## Verified correct (read + pinned, no changes)

- **Δ(dB) → amplitude factor** `10^(Δ/20)` — the 20·log₁₀ voltage convention,
  with the from/to-dBm absolute pair feeding Δ.
- **FSP bridge** `dBm = FSP + 20·log₁₀|a|` and `a = 10^((target−FSP)/20)` —
  the same identity `autofit/power_rows` pins bit-exact elsewhere.
- **dBm ↔ Volt @ R**: `P_mW = 10^(dBm/10)`, `V_rms = √(P_W·R)`,
  `V_pk = √2·V_rms`, `V_pp = 2√2·V_rms`, reverse from any V row.
- **Safe expression parser**: precedence verified (`-2^2 = −4` by the math
  convention — unary applies after pow; `2^-3`, right-assoc `2^3^2`),
  prototype-name lookups refused at the boundary, Inf/NaN never leak. The
  existing `tests/calc_selfcheck.cjs` already pins all of this plus the
  worked examples; Tab/Alt+C/A11y are pinned by the tab-focus and
  sidebar-tools suites.

## New: RF = LO + IF (the daily MW-FEM conversion)

A fourth section between the Volt bridge and Quick reference: **RF (GHz)**,
**LO (GHz)**, **IF (MHz)** — type any two and the third fills in. `role` =
the field just edited; the other KNOWN field decides which of the remaining
two derives, so both workflows (fix LO, read IF; fix IF, place LO) work
without a mode switch. The result row echoes the IF against the MW-FEM's
**±400 MHz window** (amber outside) — the same hardware constant the
wizard's `solveLoWindow` designs around, echoed here as static text, never
recomputed chip state (the calculator stays chip-independent).

The solver is a PURE function (`window.calcSolveRfLoIf`) so the node
self-check pins the math without a DOM: all three edit roles, both
known-field choices, and the single-known-field case deriving nothing
(never invent a value). Tab-hop picks the three new inputs up automatically
(`input.calc-in` inside an open section — the existing contract).

Pinned by `tests/calc_selfcheck.cjs` (extended) via `tests/test_calc.py`;
tab-focus + sidebar-tools suites unchanged and green (27 passed).
Screenshot: `D:\work\sm-screenshots\2026-08-10_1.0-prep\s6_calc_rf_lo_if.jpg`.
