# 95 — Generate-Config end-to-end verification: tunable-coupler chip, two git-revision envs

*2026-08-09. A verification campaign, not a code change. The Generate-Config
wizard was exercised from scratch to a loaded, editable chip in **two conda
environments built from a customer lab's own two pyprojects** — a 3×2
tunable-qubit + tunable-coupler chip (6 qubits, 7 couplers, 2 feedlines). The
concern that prompted it: both envs install `quam-builder` from **git
revisions**, so their "0.4.0" version string may not be the same code the wizard
was verified against. Result: **every gate passed in both envs; zero SM defects
found.** No SM code changed, so there is no CLAUDE.md paragraph and no new test
pin — this document is the record.*

## The two environments

Built with `conda create -n <env> python=3.11`, then the pyproject's dependency
list installed verbatim (git URLs included), then the customer tree
`pip install --no-deps` (PEP-517 temp build; the read-only customer tree is never
touched). Resolved commits read back from each package's `direct_url.json`:

| env | quam-builder | qualibration-libs | quam / qualang-tools / qm-qua |
| --- | --- | --- | --- |
| **env-head** (HEAD-tracking pyproject) | git HEAD `71fffe7f` (2026-07-28) | `9735a04f` (0.3.0) | 0.6.0 / 0.23.0 / 1.3.1 |
| **env-pinned** (commit-pinned pyproject) | pinned `b2056cd9` (2026-07-02) | pinned `09fc7357` (0.2.1) | 0.6.0 / 0.23.0 / 1.3.1 |

Both report quam-builder version string **"0.4.0"** — the "version strings lie"
case. The builder delta between them is exactly **one commit**
(`b2056cd9..71fffe7f` = #138, single-shot active reset), which touches only
`ResetMacro` / `reset_qubit_active` — **outside the FluxTunable build path**.

`qualibration-libs` differs by more (0.2.1 vs 0.3.0), but the wizard build path
does not import it, so it has no effect here.

## What was verified (both envs, identical gate set)

The test chip: **6 tunable qubits** in a 3×2 grid (row-major q1–q3 / q4–q6),
**7 tunable couplers** = the 7 grid edges via the wizard's Grid(NN) preset,
**2 readout feedlines** (per-feedline = 3 → q1–q3 on MW out8/in1, q4–q6 on
out1/in2), on one OPX1000 with 1 MW-FEM + 2 LF-FEM. `pair_gate = cz_tunable`
(the wizard default architecture).

**P2 — capability probe.** 11/11 TC blockers available in both envs. The
capability cache keys on the interpreter path **and** a versions tuple that
includes `quam_builder_commit`, so the two same-"0.4.0" envs cached under
**distinct commits** (`71fffe7f` vs `b2056cd9`) — proven empirically, not just
by code reading (`config_generator.py:1111/1149`).

**P3 — scripted preflight (de-risk before the browser).** `allocate` dry-run
packed 1 MW-FEM exactly (6 xy + 2 feedline out/in) + 2 LF-FEM (6 qubit-flux +
7 coupler-flux = 13 outputs); `build` produced `FluxTunableQuam`, 7 pairs,
18 class schemas; `run_config_preview` did a **real `Quam.load()` +
`generate_config()`** in-env → 25 elements / 117 pulses. This is the earliest
Acceptance-criterion-① signal, and it landed green in both envs.

**P4 — wizard run, the canonical proof.** All 8 steps driven in a real Chrome
via CDP, one screenshot per step (11 shots per env). The wizard-POSTed spec was
captured and diffed against the P3 spec: identical but for the populate defaults
the wizard fills in (expected).

**P5 — structure + spec-fidelity.** Rebuilding from the **captured wizard spec**
produced a state **byte-identical** to the wizard's own output (`wizard ==
scripted`), proving the wizard's spec assembly is faithful. On the built state:
root `FluxTunableQuam`; q1–q6 each carry xy/z/resonator; exactly the 7 grid-edge
pairs; a `TunableCoupler` subtree with non-empty `operations` on all 7; the
unipolar/flattop/bipolar CZ macros carry `coupler_flux_pulse` references (21
across the chip), SNZ carries none (design); 2 feedline port pairs; 13 LF outputs.

**P6 — load + diagnostics + validate-deeply.** `/load` → env-card warm →
`/diagnostics/banner` **204** (zero error-severity findings) → `/config/regenerate`
**200** (the real in-env `Quam.load()` + `generate_config()` through the UI path).
A forced re-probe (`env-probe force=1`) left the same clean result. The only
findings are two **warnings** (null `RF_frequency` with an inferred-pointer
default; two downconverter LOs 0 MHz apart on a shared feedline) — both expected
for a design-time chip with no populated frequencies.

**P7 — Live State Edit completeness (Acceptance-criterion-②).** `/bulk/all-values`
satisfied its invariant (`total + arrays + empties == rows`) and every leaf of
the offline-walked state+wiring appeared in it (0 missing). The qubit grid
rendered its Z/XY/Resonator Port+ columns through the port pointer chains with
**no truncation note** (333 columns, far under the 1200 cap); the pair grid
carried all 8 coupler columns (offsets, flux_point, const op len/amp, opx_output)
plus the four CZ-variant sections. `/field/peek` returned the correct value on
~15 representative paths and put a deliberately-absent path in `errors` (the
instrument detects absence). `/api/search` found coupler / cz_unipolar leaves.

**P8 — edit round-trip ("generation through editing").** Via the LSE grids:
a qubit scalar (`f_01`), a coupler parameter (`decouple_offset`, pair grid) and
a port list-through-pointer field (`z_delay`) were edited, Applied to live, and
survived `/config/regenerate` → 200 and a reload (values persisted).

## P10 — comparative analysis

- **env-head vs env-pinned: state.json AND wiring.json are byte-identical**, and
  their `class_schemas` are identical. The one-commit builder delta does not
  reach TC generation. This is the campaign's cleanest result: the git-revision
  difference that prompted the whole exercise produces **exactly the same chip**.
- **vs an old-builder reference** (a 3×2 cz_tunable chip generated by the wizard
  under an earlier builder): every diff classifies as informational — no SM
  defect:
  - build-envelope: `+__package_versions__`, `+extras`, `-active_twpa_names`
    (the modern builder stamps versions/extras and omits the empty twpa-names
    list);
  - modern schema growth: `+ports.*.exponential_dc_gain` / `high_pass_filter`,
    `+mw_inputs.*.lo_mode` (quam 0.6.0 port dataclasses carry more fields — all
    reached LSE, per P7);
  - documented builder renames: `CZGate.duration_control → duration_qubit`, and
    `moving_qubit` **relocated from the macro to the pair** (which is where the
    customer's real 10Q chip carries it too);
  - variant set: `-cz_flattop_erf` (ErfSquarePulse is absent from these git
    revisions of quam-builder — surfaced as the honest per-pair build warning,
    "the other variants still seed"; the reference chip was built by a builder
    that had the class);
  - pulse-class field evolution: coupler flux pulses lost `sigma`, gained
    `flat_length` / `smoothing_length` / `post_zero_padding_length`.

## Informational deltas worth keeping (all expected, none a defect)

- SM's generated root is **`FluxTunableQuam`** (quam-builder's own class); the
  customer's real 10Q chip's root is a **lab-custom root class** in their private
  `quam_config` package. `run_generate_config._load_machine` reads the state's
  own `__class__`, so both load; the wizard simply doesn't wrap the chip in the
  lab package (it isn't meant to).
- The wizard's Populate step exposes only `coupler_interaction_offset` of the
  four coupler attributes `run_build` accepts, and no coupler-amplitude column
  (it derives from `cz_amplitude`). Everything else on the coupler is editable
  **after load in SM** — which P8 exercised directly.
- `flattop_erf` is the one CZ variant these builder revisions can't seed; the
  build says so per pair. A build env that ships `ErfSquarePulse` would seed it.

## Notes for the future

- Both testbed envs are kept for further testing (see the gitignored decoder for
  their real conda names and the customer state path).
- The scripted-vs-wizard byte-identity (P5) is the strongest single assertion
  here — it means a future regression in `generate.js` spec assembly would be
  caught by rebuilding from a captured spec and diffing, without a browser.
- No code changed, so the Windows test baseline is unmoved (docs/94: 14 + the
  documented `os_replace` flake).
