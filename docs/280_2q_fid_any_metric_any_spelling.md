# docs/280 -- 2Q Fid. shows any gate's 2Q fidelity, in any spelling

2026-10-04, user request after docs/278: "if a user measures XEB or IRB for an
arbitrary waveform, can SM show it adaptively?"

## What was fixed only half-way

- **Gate names were already adaptive.** Every macro under
  `qubit_pairs.<pair>.macros.<gate>` with a `fidelity` block was read, so a new
  waveform (`cz_my_waveform`) got its own panels with no code change.
- **Metric names were not.** The server's `rb_level` already ignored case
  (`irb` = `IRB`), but the panel builder in `chip-status.js` compared exact
  strings (`'StandardRB'`, `'InterleavedRB'`, `'IRB'`, and since docs/278
  `XEB`). A lab writing `irb`, `srb`, `Interleaved_RB`, `xeb_fidelity`, a
  Bell-state fidelity, or a metric of its own got its value read and then
  dropped -- no panel. JSON keys ARE case-sensitive; the defect was that SM
  had two classifications of the same name, one tolerant and one exact.

## One classification, on the server

`query.fidelity_family(metric)` decides once, and every row of
`edges[*].gate_fidelities` carries it as `family`:

| family | from | panel heading |
|---|---|---|
| `StandardRB` | `rb_level == "clifford"`: StandardRB / Standard_RB / standardrb / SRB | Standard RB per Clifford (1 − EPC) |
| `InterleavedRB` | `rb_level == "gate"`: InterleavedRB / Interleaved_RB / IRB / irb | Interleaved RB per gate (1 − EPG) |
| `Bell` | `rb_level == "state"`: Bell_State / bell state / Bell | Bell state fidelity |
| `XEB` | any name starting with `xeb` or `crossentropy` (`XEB`, `xeb_fidelity`) | XEB as stored |
| `other:<name>` | a metric no table knows (`MyGateScore`, a bare `RB`) | `<name>` as stored |
| none | an RB decay base (`*_alpha`), an error (`error_per_*`), a run id (`*_id`, `*_run`), an uncertainty (`*_err`, `*_std`, …) | not drawn |

- `rb_level` now also ignores separators (`_`, `-`, spaces) and knows two
  short forms (`SRB`, `Bell`). A bare `RB` is deliberately NOT mapped: it is
  not clear whether a lab means the Clifford or the gate number, so it is shown
  under its own name, as stored. The docs/138 (0,1] physical bound follows the
  level, so `interleaved_rb = 1.53` now loses its `value` like `IRB = 1.53`.
- Only Standard RB and Interleaved RB are judged against the CZ-fidelity spec
  threshold; XEB, Bell and unknown metrics are flagged by MAD only.
- The builder reads `family`; a row without it (an older payload) falls back
  to the three exact names it knew.

## Measured on a copy of a real device state

Families found: StandardRB (cz_unipolar 32, cz_bipolar 15, cz_SNZ 1, …),
InterleavedRB (IRB 4, InterleavedRB 1), XEB (cz_SNZ 7), Bell (cz_unipolar 32,
cz_bipolar 15, cz_cosine 1, cz_gaussian 1), and no family for 6 decay rows.
The Bell-state panels are new on that chip: the values were stored and read
but never drawn.

## Pins

`tests/test_adaptive_2q_fid.py` (every spelling -> one family; what is not a
fidelity -> none; the physical bound on a separated spelling; rows of an
arbitrary gate carry their family and their flat uncertainty) and
`tests/chip_status_adaptive_fid_selfcheck.cjs` (9 assertions: spellings in
one panel, the Bell and unknown-metric panels and their wording, a no-family
row draws nothing, the sub-heading). Mutation check 8/9 RED; the ninth (the
RB-only sub-heading branch) produces the same text as the general branch and
is held by `test_chip_status_layout.py`'s source pin.
