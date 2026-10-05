# 286: The Qubits page shows the XY drive

Chip Components > Qubits listed f_01, T1, T2ramsey, the readout amplitude and
its power, gate fidelity and grid location. It showed nothing about the drive:
no x180 or x90 amplitude, although these are the numbers a calibration changes
most often. This round adds the drive columns.

## 1. The columns

| Column | Value | Source |
|---|---|---|
| XY IF (MHz) | the drive IF; the title gives the RF | `cr_semantics.channel_effective_rf_if` (quam's `RF - LO` for `#./inferred_intermediate_frequency`, the call the printable chip report already makes) |
| x180 Amp | amplitude of the pulse `x180` resolves to | `qubits.<q>.xy.operations.x180.amplitude` |
| P(x180) (dBm) | peak output power of that pulse | `physical_units.amp_annotation` |
| x90 Amp | amplitude of the pulse `x90` resolves to | `qubits.<q>.xy.operations.x90.amplitude` |
| P(x90) (dBm) | peak output power of that pulse | `physical_units.amp_annotation` |
| x180 Len (ns) | length of the x180 pulse | `...x180.length` |
| DRAG alpha | the x180 pulse's `alpha`, when its class has one | `...x180.alpha` |

The page now has 16 columns. The new cells use the markup the readout columns
already use: `col-phys` + `data-dbm` + `data-sort`. The unit setting in
Settings (dBm / V rms / both) therefore applies to them with no new client
code.

## 2. One vocabulary with the Live State Edit grid

`core/xy_drive.py` takes the operation paths from the grid's own curated
columns, `param_specs._BULK_COLUMNS_SPEC` keys `x180_amplitude` and
`x90_amplitude`, so the two pages read the same leaf. A pin checks this
(`test_the_paths_are_the_live_edit_grids_paths`). Every value goes through
`pointer_path.resolve_field_target`, the follower that the grid, the fit
targets and Diagnostics also use. The dBm comes from the same
`amp_annotation(merged, resolved, value, alias_path=alias)` call that
`_build_bulk_cell` makes. A pin compares the two surfaces on the same chip down
to 1e-12 dB (`TestSameNumberAsTheGrid`).

`QueryEngine.get_qubit` still reads its own `x180_amplitude` from the
hard-coded `x180_DragCosine` name (Chip Status, history, metric provenance).
This round leaves that alone. The two only differ on a chip whose `x180` alias
points somewhere other than `x180_DragCosine` (section 5).

## 3. Lab-flexible, without guessing

On a builder chip, `operations.x180` is an alias (`"#./x180_DragCosine"`).
Another lab may point the alias at a pulse with a different name, or store the
pulse inline under `x180`. The page follows the alias to the pulse actually in
force, and each cell's title names it: `x180 -> x180_DragCosine
(DragCosinePulse)`. A sibling that only shares the prefix (`x180_Square` beside
an alias to `x180_DragCosine`) is never read.

A qubit that has no `x180` (or `x90`) operation gets an em dash. Its title lists
the x180-like pulses the qubit does carry, and says the state does not record
which of them plays. SM does not pick one: in the local corpus of 54 chips,
every qubit that has any x180 pulse also has the `x180` alias. A guess would
therefore only ever fire on a chip where it could also be wrong (for example
an `x180_EF` pulse).

Other blanks also come with a reason in the title, never a raw value:

- a field the pulse class does not have (a Square pulse has no DRAG `alpha`);
- a pointer that ends in a run-time value, such as `#./inferred_length`;
- an amplitude stored as text, which is shown quoted, the way the page already
  shows a readout amplitude stored as text;
- a power the class cannot give (docs/248).

A pointer string is never shown as a value. Every em dash carries
`data-sort="-"`, so blanks sort below every number.

## 4. Verified

- Pins: `tests/test_qubits_xy_columns.py`, 16 tests on a synthetic five-qubit
  chip: an inline pulse, the builder alias with a prefix decoy and pointer
  leaves, a qubit without x90, a lab-named pulse through the alias, and a lab
  pulse with no alias.
- Mutation check: 15 mutations (a dropped cell, the old colspan, a hard-coded
  `x180_DragCosine` path, the alias not followed, an inline pulse ignored, no
  power, a second dBm formula that ignores the class peak, an absent field read
  as 0, a pointer shown as text, leaf pointers not followed, the IF read raw, an
  em dash without its sort key, a prefix fallback, numeric text coerced, the
  route dropping the map). Each pin went RED under at least one of them: 16/16.
- A copy of a real 21-qubit chip, in headless Chrome. Every row shows all 16
  columns. Three qubits were checked by hand against the state, following each
  pointer and recomputing the DragCosine peak independently: amplitude, dBm,
  length, alpha and IF all match. There were 0 console errors, and Back and
  reload keep the table intact. Sorting by P(x180) orders the rows by the
  number. At 1440 px wide, the table scrolls inside its pane; the page itself
  does not overflow.

## 5. Not done here

- `get_qubit`'s `x180_amplitude` / `x180_length` / `x180_alpha` / `x90_amplitude`,
  and `_QUBIT_PROPERTY_MAP`'s XY Drive rows, still name `x180_DragCosine`
  directly. On a chip whose alias points elsewhere, Chip Status and the qubit
  inspector would show the DragCosine pulse while the gates play another one.
  Moving them onto the alias touches history keys and metric provenance, so
  that is a separate change.
- The other Chip Components pages already show their drive amplitudes: Pairs
  (CR drive amp, CZ flattop amp) and Resonators (readout amp + P(RO)). Flux and
  Couplers carry offsets, not pulses. None of them has the gap this page had.
