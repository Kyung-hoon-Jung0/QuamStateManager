# docs/248: the dBm follows the pulse class

2026-10-03, agent validation campaign, finding **B-05**
(`D:\work\sm_qa_rigs\agent\FINDINGS.md`).

## Report

On the rig chip copy, qB1's `readout` op points at a lab readout class
(area-normalised). Its amplitude is 0.0526 on an MW port whose
`full_scale_power_dbm` is -2. SM showed **-27.6 dBm**
(`-2 + 20*log10(0.0526)`). The waveform that class actually plays peaks at
**-20.6 dBm**. The number was confident and 7 dB wrong.

## Root cause

`core/physical_units.py::amp_annotation` (origin/main) computed
`FSP + 20*log10|amp|` for every amplitude leaf whose channel resolved to an MW
port. That treats the stored `amplitude` as the waveform's peak sample, which
the QM docs never say. What they define is the **sample**:

- `Guides/opx1000_fems.md` § Microwave FEM > Output Power: "This will set the
  power delivered to a 50 Ω load when the waveform is set to full scale
  (`{-1, 1}`)." and "The amplitude is linear in voltage, not power."
- `Introduction/config.md` § Waveforms: "For a MW-FEM using the waveform, the
  sample values give the output amplitude as a fraction of the
  `full_scale_power_dbm` and must be within [-1.0, 1.0]." and, for LF, "For the
  OPX+ or a LF-FEM using the waveform, the sample values give the output in
  volts".

So the peak power is `FSP + 20*log10(max_t |I + iQ|)` (the `|I + iQ|` envelope
is [derived]). The stored amplitude is that maximum only for some classes, and
a lab class can define `amplitude` any way it likes. The LF branch had the same
assumption ("the amplitude IS volts").

## Which classes peak at their amplitude

Measured with `waveform_synth` (the transcription that `test_waveform_golden`
pins bit for bit against quam), over randomized realistic parameters. `r` is
`max|I+iQ| / |amp|`.

| class | r | evidence |
|---|---|---|
| Square, SquareReadout | 1 exactly (any `axis_angle`) | 2,000 draws each, `|r-1| <= 1e-12` |
| SNZ, GaussianFilteredSquare, GaussianFilteredSymmetricBipolar | 1 exactly | 500 draws each (the filtered pair normalise to `|amp|` by construction) |
| FlatTop{Gaussian,Cosine,Tanh,Blackman}, `_FlatTopGaussian` | 1 when `flat_length >= 1` | 500 draws each; with no flat part r is in [0.04, 1] |
| CosineBipolar, `_CosineBipolar` | 1 when `flat_length >= 2` | 500 draws; with no flat part r <= 0.997 |
| Gaussian | 1 only for `subtracted=False` and an odd length | subtracted: r = 1 - g_end, in [0.59, 0.9996]; even length: no centre sample |
| DragCosine | 1 for an odd length and `k^2 <= 2` | see below; even length: r in [0.98, 1.003] |
| DragGaussian | 1 for an odd length, unsubtracted, `kappa^2 <= 1` | see below; random draws reached r = 1.12 |
| ErfSquare | <= 1, approaching 1 as the flat part outgrows the rise | 500 draws, r in [0, 1] |
| Pulse, WaveformPulse, BlackmanIntegral | no `amplitude` field | never annotated |

**DRAG, [derived].** With θ the cosine phase and
`k = alpha*1e9 / ((length-1)(anharmonicity-detuning))`, a DragCosine has
`|z|^2 = (A/2)^2 [(1-cos θ)^2 + k^2 sin^2 θ]`. On `cos θ` in [-1, 1] this
peaks at the centre (`|z| = A`) iff `k^2 <= 2`. Beyond that,
`r = k^2 / (2 sqrt(k^2-1))` (k = 2 gives 1.155, so the plain identity would
under-state the power by 1.25 dB). A DragGaussian, unsubtracted, has
`|z|^2 = A^2 e^{-x^2} (1 + kappa^2 x^2)` with
`kappa = alpha*1e9 / (2 pi sigma (anharmonicity-detuning))`. It peaks at the
centre iff `kappa^2 <= 1`, and otherwise
`r = kappa exp(-(kappa^2-1)/(2 kappa^2))`. With subtraction, the centre stays
the maximum when `kappa^2 <= 1 - g_end`. That condition is necessary at the
centre; its sufficiency was checked numerically (1,500 draws, no
counter-example), not proven. All of these are executable pins in
`TestDragCentreCondition`. Real chips sit well inside the centre condition:
the rig's x180 has k ≈ 0.03. Its length is 48, which is even, so it has no
centre sample and r = cos²(π/94) = 0.9989 (-0.01 dB).

## Decisions

1. **Use the waveform's peak, not a table of `r = 1` classes.** For every class
   SM knows, `r` is computed by synthesizing the waveform at amplitude 1 with
   the pulse's own resolved shape parameters. This goes through `waveform_synth`,
   the same function the Pulses preview uses and the golden tests pin. There is
   no second transcription. Each catalog waveform is homogeneous of degree 1 in
   `amplitude` ([derived], pinned by `TestPeakIsLinearInAmplitude`), so
   `peak = |amp| * r`. `r` is LRU-cached per (class, shape), so the grid's
   cost is one dict lookup per cell after the first. On the rig chip, 717
   amplitude leaves take 24 ms warm, against 6.5 ms for the old identity. The
   classes with `r == 1` still give the old number bit for bit. A unit-modulus
   rotation's float noise (`|e^{2.3i}|` = 0.9999999999999999) is snapped to 1
   within 1e-12.
2. **Known means the full dotted path.** `pulse_catalog.spec_at_known_home`
   accepts these:
   - a catalog `qclass`;
   - an `_EXTRA_HOMES` path;
   - a golden-verified full-path alias (`DragPulse`, the `Smoothed*` pair).

   It rejects:
   - the `leaf` step. A lab's `my_lab.SquarePulse` shares a name, not a
     waveform.
   - a bare key.
   - the `env` step. The selected env's roster proves that a class exists at a
     home, not that its `waveform_function` is the one SM transcribed.
3. **Strict on fields.** Some pulse fields are not in the catalog spec. If such
   a field could shape the waveform (a newer generation of the class), the pulse
   is blank. The Pulses preview only warns in that case. A census of 5,086
   distinct real state files found **zero** known-class pulses with such a
   field, so this costs nothing today. Fields that the catalog declares
   shape-free (`synth=False`: ids, markers, thresholds, weights) are neither
   read nor part of the cache key.
4. **The alias's shape wins.** `-y90.amplitude` points at `x90`'s number, but
   the waveform played is `-y90`'s. The grid and `/bulk/phys` pass the alias
   path (`alias_path=`) as well as the resolved one.
5. **The LF branch does the same thing.** It shows the waveform's signed peak
   sample in volts. That is today's value for every `r == 1` class with a
   leading positive lobe. A negative-polarity ErfSquare now reads negative,
   which is a correction. An unknown or lab LF class is blank. A real chip's
   two-flux SNZ lab class carries a `neg_offset_v`, so its amplitude is not its
   output voltage either. An I/Q waveform on a single-ended LF port is also
   blank.
6. **A class-caused blank says why, briefly.** In this case the annotation
   returns `None` and fills the caller's `why` list with
   `{"mark": "dBm ?" | "V ?", "text": "dBm unknown: pulse class <Name> is not
   one SM can synthesize"}`. The full path is used when the leaf collides with
   a known name. Other blanks (amp 0, a broken chain, no channel) stay silent
   as before.
7. **The read set follows the new inputs (RAM P6).** `reads` now also holds:
   - the pulse dict's path, which covers its `__class__` and every inline shape
     field, because a write's ancestor walk reaches it;
   - each pointer-valued shape field's resolved target, e.g.
     `qubits.<q>.anharmonicity`.

## Changes

- `core/pulse_catalog.py`: `spec_at_known_home(qclass)`.
- `core/physical_units.py`:
  - `pulse_peak(merged, amp_path, alias_path)` → `{ratio, sign, real, reads, why}`;
  - `amp_annotation(..., alias_path=, why=)` → adds `peak` to the annotation;
  - the docstring carries the verbatim QM-docs quotes and the [derived] table.
- `web/routes.py`:
  - `_build_bulk_cell` passes `alias_path` and `why`, and stores
    `phys_blank` on the cell;
  - `POST /bulk/phys` passes `alias_path=dp` and answers `blank: {path: {mark, text}}`.
- `web/app.py`: a `phys_amp_blank` Jinja filter (the reason for a class blank).
- Templates:
  - `_bulk_cell_macros.html`:
    - `data-phys-peak` (only when ≠ 1);
    - the shape factor in the title;
    - a `.bulk-phys-why` marker plus the reason in the input's title.
  - `_qubit_detail.html`, `_pair_detail.html`, `_pulse_params_section.html`:
    the shape factor in the title, and a `phys-note-why` marker.
  - `_qubits.html`, `_resonators.html`, `chip_report.html`: the "-" carries the
    reason as its title.
- `web/static/app.js` (`PhysAmp`):
  - the live recompute multiplies the typed amplitude by `data-phys-peak` (MW
    and LF);
  - `_setKind` keeps the why-marker and the title reason in step with
    `/bulk/phys`;
  - a server-blanked cell has no `data-phys-kind`, so typing never paints a
    number into it.
- `web/static/style.css`: `.bulk-phys-why`.
- `web/templates/_calc_body.html`: one help line ("a is the waveform's peak
  sample: a pulse's stored amplitude only when its class peaks there"). The
  calculator's formula is right for a sample. Only its label could be read as
  "any stored amplitude".
- Tests: `tests/test_dbm_pulse_class.py` (new); fixtures in
  `test_physical_units.py`, `test_chip_report.py` and `_ram_chip.py` now name
  their pulse classes the way real chips do. `_ram_chip`'s DRAG
  `anharmonicity` changed from `#../../../anharmonicity` to
  `#/qubits/<q>/anharmonicity`. The multi-level form appears in no real chip
  (census: 490k `#../X/Y` and 374k absolute, zero multi-level), and SM's
  resolvers follow one `../` only. `phys_live_selfcheck.cjs` gained sections
  5-6.

## Surfaces deliberately left alone

- **`core/mw_fem.py` FSP compensation.** `amp' = amp * 10^((FSP_old -
  FSP_new)/20)` keeps the output constant for any waveform that is homogeneous
  in its amplitude, and it presents no absolute power. The dialog shows FSP
  old → new and the amplitude factor.
- **`core/autofit/power_rows.py`.** It writes the node's own `target_amplitude`
  and target FSP, and the sibling rescale is the same ratio. The power
  convention is the node's (docs/47).
- **`core/autofit/replaybench.drive_power_dbm`.** It is only compared with the
  same target's `saturation` op in another run of the same session (the 1 dB
  "colder" test). The class factor cancels while the class and shape do not
  change, and the value is never displayed.
- **`interactive_plots` recipes (`qubit_spec_vs_power`, `resonator_2d`).** The
  power axis is the node's own coordinate, and a click writes back the
  amplitude that the node's own formula realises. The round-trip goldens pin
  this. It is not SM's claim about a pulse's peak.
- **`web/static/calc.js`.** The calculator takes user input and is correct for a
  sample. Only the help line was added.

## Pins and mutation check

`tests/test_dbm_pulse_class.py` has 52 tests:
- the [derived] r-table;
- the DRAG conditions with their closed forms;
- linearity;
- the annotation: B-05 itself, lab/leaf/env/missing/unmodeled/unsynthesizable
  classes, the shape peak, a DRAG peak above the amplitude, the alias shape,
  reads, LF sign, LF lab, LF I/Q, and silent blanks;
- the verbatim docs quotes;
- every surface, including both cell macros rendered directly. The pair macro
  carries the same markup, and no fixture pair resolves to an annotated channel.

`phys_live_selfcheck.cjs` sections 5-6 cover the client. The randomized RAM
equality pin (`test_patched_grids_equal_a_cold_build_after_every_step`) now
writes `anharmonicity`, so it exercises the new read set end to end.

**30 mutations, 30 red.** Each one broke the guarded code, the named pin went
red, and the code was restored:

| # | mutation | caught by |
|---|---|---|
| M01 | the known-home check accepts a name-only (leaf/env) match | lab-named-like-known, env roster, known homes |
| M02 | the class is ignored (r always 1) | B-05, shaped class |
| M03 | the alias pulse is not preferred | alias shape |
| M04 / M04b | MW reads miss the pulse reads | reads pin; **and the randomized RAM equality pin alone** |
| M05 / M05b | pointer targets are not read | reads pin; **and the RAM pin alone** |
| M06 | no strict unmodeled-field check | unmodeled field |
| M07 | LF drops the peak's sign | negative-polarity erf |
| M08 | LF annotates an unknown class as amp volts | LF lab class |
| M09 | no unit snap | bit-identical SquareReadout at `axis_angle` 2.3 |
| M10 | the reason is not reported | B-05, surfaces |
| M11 | the cache key drops `length` | a shape write moves the answer |
| M12a / M12b | the qubit / pair macro drops `data-phys-peak` | grid stamp, both-macros |
| M13a / M13b | the qubit / pair macro drops the why-marker | grid blank, both-macros |
| M14 | `/bulk/phys` returns no reasons | route reason |
| M15 / M16 | `/bulk/phys` / the cell builder pass no alias | alias shape on route and grid |
| M17 | the `phys_amp_blank` filter never explains | inspector, components, report |
| M18 | the report's dash carries no title | report |
| M19 | JS recompute ignores `data-phys-peak` | selfcheck §5 |
| M20 | JS LF ignores the signed peak | selfcheck §5 |
| M21 | JS never manages the why-marker | selfcheck §6 |
| M22 | JS keeps a stale marker after re-annotation | selfcheck §6 |
| M23 | JS keeps the stale "actual output shown below" promise | selfcheck §6 |
| M24 | a QM-docs quote drifts | verbatim-quote pin |
| M25 | the components tables drop the reason | components |
| M26 | the inspector drops the why note | inspector |

A first draft of M09's pin used `axis_angle` 0.4. `|e^{0.4i}|` happens to be
exactly 1.0 in floats, so that pin would have passed with the snap removed.
It now uses 2.3, where the result is 0.9999999999999999.

Suites run in `cqt` (with `--timeout=900`). The 29 related files gave
**1032 passed, 1 skipped**:
- physical_units, phys_live, ram_liveedit, chip_report, pulses_round3, bulk_edit,
  bulk_markup, fsp_compensation, mw_fem;
- waveform_golden, waveform_synth, pulse_catalog(+env overlay), calc,
  calc_window, back_nav_fresh, store_lock_holds, report_card, pulses_routes;
- pulse_index_ram, pulse_env_canonical_home, pulse_adaptive, cust_0930_ui,
  predelivery_audit_fixes, regen_fsp_compensation, diagnostics_waveform,
  pair_detuning_unit, undo_list_cells.

The later re-run of the touched files plus the name and English-only guards
gave 106 passed.

## Browser verification

SM was served from this worktree on port **5103** by a scratch launcher. It
used a COPY of the rig chip (`D:\work\sm_qa_rigs\agent\chip`), a scratch
instance dir and home, and a nonexistent `QUALIBRATE_CONFIG_FILE`. Headless
Chrome ran on CDP 9343 with a scratch profile and was driven by
`tests/browser/journeys/cdp.cjs`. The journey was open `/bulk` → type → Escape
→ a real click on the sidebar's Qubits link → `history.back()` → reload → the
qB1 inspector. It ran twice, before and after the mutation sweep, and both runs
said **VERDICT PASS** with `b.errors()` empty at every step.

- **qB1 readout** (lab class): no `data-phys-kind`, no number, and a muted
  `dBm ?` whose title is "dBm unknown: pulse class <the lab's readout class>
  is not one SM can synthesize". The class name comes from the chip, and this
  doc does not repeat it. The same holds
  for qB2 and qB3, which point at the same class. Typing `0.1` into the box
  painted nothing. This held after back and after reload.
- **qB1 x180** (`quam.components.pulses.DragCosinePulse`, length 48):
  `-8.4 dBm`, `data-phys-peak` 0.99888. Typing 0.19 gave `-14.4 dBm`, which
  equals the server formula. Escape restored `-8.4 dBm`.
- **qA1 readout** (SquareReadout): `-38.3 dBm`, no peak attribute (r = 1).
- **Qubits page**: P(RO) shows `-` for qB1-qB3, with the reason as the title.
  All other qubits keep their dBm.
- **Inspector `/qubit/qB1`**: `readout_amplitude` carries `dBm ?` with the
  reason.

The screenshots were read one by one (`01_qB1_readout.png`,
`01_qB1_x180.png`, `02_typed_x180.png`, `03_qubits_page.png`,
`04_back_qB1_readout.png`, `05_bulk_reload.png`, `06_inspector_qB1.png`). The
"1 error on chip would crash a node run" banner in them is the rig chip's own
pre-existing diagnostics finding (qB3 drive IF), not this change. The chip
copy, instance, home and Chrome profile were deleted afterwards. Only the
server, its launcher and the Chrome processes started for this check were
stopped.

## Open items

- **The Generate wizard's power-input mode** (`generate.js`, populate step)
  uses quam_builder's `power_tools` convention `P = FSP + 20*log10(amp)` for
  the pulses it is about to build. For a subtracted DragGaussian x180 that is
  the amplitude's dBm, not the peak's: about 0.4 dB at sigma = length/5. It is
  an input convention shared with quam_builder, so it was not changed here.
- **The lab classes.** A lab class stays blank until SM can see its waveform.
  The honest source would be the env's own `generate_config()` waveform for
  that pulse. SM already runs that in a subprocess for the Config Viewer, but
  wiring it into the grid is a separate change.
- **FSP compensation for a lab class** is right only if that class is linear in
  its amplitude, which SM cannot check without its waveform.
