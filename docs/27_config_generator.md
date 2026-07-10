# 27: Generate Configuration Files — Interactive QUAM Config Builder

> A "Generate Config" wizard that lets a user assemble their lab hardware
> (OPX1000 chassis → FEM modules → ports), define qubits/pairs/TWPAs, assign
> wiring, enter physics values, and emit a valid `state.json` + `wiring.json`
> pair — then load it straight into the app.
>
> **Read this if you are:** working on the config builder, the generator
> subprocess, or wondering why the app shells out to a separate Python env.
>
> **Status:** complete (v1). Built across 27 phases on `feat/config-generator`;
> the phase table at the bottom tracks what shipped. The only deferred item
> is TWPA populate — blocked by a `quam_builder` 0.2.0 limitation.

---

## Why this exists

Authoring a QUAM `state.json` + `wiring.json` for a fresh hardware install
today means hand-editing `generate_quam.py` / `populate_quam_*.py` (from the
QM `qualibration_graphs` repo) and running them in a terminal — error-prone
and opaque to new users. This feature turns that into a guided visual
wizard inside the State Manager.

## Engine — why a subprocess

The real generation is done by Quantum Machines' own libraries
(`qualang_tools.wirer`, `quam_builder`, `quam`). Those are heavy and pull in
the `qm` package; the State Manager deliberately keeps a minimal dependency
set (Flask / Typer / Rich / h5py / pywebview) for a small, fast bundle.

So the app **shells out**: it writes a spec JSON, runs
`generator/run_build.py` with a *user-selected conda env's* Python (one that
has the QM stack installed), and reads the result back. The State Manager
process never imports the QM libraries.

```
 Browser wizard ──► routes.py ──► core/config_generator.py ──subprocess──► generator/run_build.py
                                  (Flask, minimal deps)     (chosen env)   (QM libraries)
```

The chosen env is picked by the user from a scan of their conda
environments — they own which env / library versions are correct. The
output folder is always an explicit wizard choice; the app never inherits an
ambient `QUAM_STATE_PATH`.

## `generator/run_build.py`

A standalone script — imports only QM libraries + stdlib, never
`quam_state_manager`. Two modes, both always writing a `_result.json`
envelope (the single thing the parent reads: `status`, `error`,
`traceback`, `versions`, plus a mode payload). It never raises to the OS —
the top-level guard captures any failure into `_result.json`.

`_result.json` is written **next to the spec file** — a private temp work
dir created by `run_generator` — never into `--out`. QUAM's loader reads
*every* `.json` in a folder, so a stray file in the output directory would
corrupt the `state.json` the build just wrote. The output folder therefore
ends up holding only `state.json` + `wiring.json`.

For the same reason, the `/generate/build` route guards the *user-chosen*
output folder: if it already contains any `.json` other than `state.json` /
`wiring.json`, the build is blocked with a `needs_confirm` response listing
the stray files, and the wizard offers an explicit **Generate anyway**
override (state/wiring are excluded because a re-build is expected to replace
them).

| Mode | What it does |
|---|---|
| `allocate` | Builds `Instruments` + `Connectivity`, runs `allocate_wiring`, returns the per-element port assignment. Writes no QUAM files. Powers the wizard's "Auto-allocate" button. |
| `build` | `allocate`, then `build_quam_wiring` + `build_quam` (+ populate), writing `state.json` / `wiring.json` into `--out`. Qubit + qubit-pair lines (coupler / cross-resonance / ZZ); OPX1000 / OPX+ / Octave instruments. The populate step arrives in later phases. |

## The spec JSON (UI ⇄ generator contract)

```jsonc
{
  "network":     {"host": "...", "cluster_name": "...", "port": null},
  "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}, ...]}],
                  "opx_plus": [], "octaves": []},
  "qubits":      ["q1", "q2", "q3"],
  "qubit_pairs": [["q1", "q2"], ["q2", "q3"]],
  "twpas":       [],
  "lines": [
    {"element": "q1", "line": "resonator", "group": "feedline1",
     "channel": {"kind": "mw_fem", "con": 1, "slot": 1, "in_port": 1, "out_port": 1}},
    {"element": "q1", "line": "drive", "channel": {"kind": "mw_fem", "con": 1, "slot": 1}},
    {"element": "q1", "line": "flux",  "channel": {"kind": "lf_fem", "con": 1}}
  ],
  "populate": {}
}
```

- A `channel` is a partial port pin; missing fields stay free for the
  allocator. `kind` ∈ `mw_fem` / `lf_fem` / `opx` / `octave`.
- `resonator` lines with the same `group` collapse into one shared
  (multiplexed) feedline. No `group` ⇒ a dedicated feedline for that qubit.
  The wizard's step-4 **Qubits per readout feedline** field (`#gen-mux-size`,
  default 6) drives this: `deriveLines()` stamps every qubit with a `feedlineN`
  group so readout is *always* multiplexed. A dedicated feedline per qubit
  would exhaust the 2 RF inputs on each MW-FEM — `NotEnoughChannelsException`.
- The output folder and chosen env are invocation parameters, not spec
  fields.

`docs/examples/sample_spec_3q.json` is a runnable 3-qubit OPX1000 reference
used by tests and the smoke check below.

## Multiple chassis (multi-controller)

`instruments.controllers` is a **list**, and the whole pipeline is
controller-aware end to end — there is no single-chassis assumption anywhere.
A spec may declare `con: 1`, `con: 2`, … each with its own `fems`, and the
generator allocates, builds, and links across all of them.

- **Wizard UI.** Step 3's *OPX1000 chassis* count field accepts 0–20
  controllers; `+ OPX+` / `+ Octave` add the other instrument types.
  `syncCons()` (`generate.js`) re-derives controller numbers on every
  add/remove so they stay `con 1..N` (OPX1000 first, then OPX+), with Octaves
  indexed `1..K`. Each chassis renders its own 8-slot grid.
- **Pinning vs. free.** A line's `channel.con` pins that line to a specific
  controller (e.g. `{"kind": "mw_fem", "con": 2, "slot": 1}`); omit `con` and
  the `qualang_tools` allocator is free to place it on any controller with
  spare capacity. `build_instruments()` loops every controller and calls
  `add_mw_fem(controller=con, …)` / `add_lf_fem(controller=con, …)`.
- **Controller-aware pointers.** The built `wiring.json` / `state.json` embed
  the controller in every port pointer, e.g.
  `#/ports/mw_outputs/con2/1/1/upconverter_frequency`. The FEM-slot dedup key
  is `(con, slot)`, so the *same* slot number on different controllers is
  valid (slot 1 on con1 and slot 1 on con2 do not collide).
- **Downconverter linking stays on-chassis.** The post-save
  `_link_input_downconverters_to_outputs` fixup is purely path-based: it links
  each readout input's `downconverter_frequency` to the *same* readout port's
  output `upconverter_frequency`, so a readout on con2 links con2→con2 and can
  never cross-link to con1.

`docs/examples/sample_spec_multichassis.json` is a runnable 2-chassis
reference (q1 on con1, q2/q3 on con2, with a cross-chassis q1–q2 pair). The
`TestMultiChassisIntegration` cases in `tests/test_config_generator.py` run it
through allocate + build and assert channels land on both controllers and that
no downconverter cross-links chassis. (Both auto-skip when no QM-capable conda
env is present, like the other integration tests.)

## The chassis step — keyboard navigation

Step 3 (chassis builder) is fully keyboard-operable. The OPX1000 slot tiles
form a roving-tabindex grid: `Tab` enters the grid at one tile, `←` / `→`
move the highlight across all slots, `↑` / `↓` cycle the focused slot's FEM
(empty → MW → LF) — as do the `M` / `L` / `Delete` keys — and `Enter` opens
the FEM picker menu (itself ↑↓ / Enter / Esc navigable). `activeSlot`
(`generate.js`) tracks the highlight by `{con, slot}` so it survives
`renderChassis()` rebuilding every tile; focus is restored to the same slot
after an edit. The mouse path (click a tile → menu) is unchanged.

## Wizard keyboard flow & draft persistence

The wizard is built for keyboard-only completion. `render()`'s `focusStep()`
auto-focuses each step's primary control on entry — chassis count (step 3),
qubit count (step 4), the Auto-allocate button (step 5, so `Enter` runs it).
Step 5 carries its **own inline Back / Next** on the Auto-allocate row
(`#gen-wiring-back` / `#gen-wiring-next`); the global footer `.gen-nav` is
hidden on step 5, so the flow is Auto-allocate → `Enter` → `Tab` → Back →
`Tab` → Next, without tabbing through the diagram.

The wizard's `state` is in-memory only — opening another sidebar page swaps
`#table-pane` away and would drop it. A `htmx:beforeSwap` listener saves a
**draft** (`state` + the DOM-only mux size / output path) to `sessionStorage`
(`quam_generate_draft`) whenever the user leaves the wizard; `init()` restores
it on return (`applyDraft()` + `repaintFromState()` for the steps that aren't
state-repainted). The draft survives navigation but is cleared by the **Reset
wizard** button (`resetWizard()`) or by closing the app (sessionStorage).

## The wiring step — diagram, drag-editing, validation

Step 5 is the spec JSON's `lines` made visual and editable. After
**Auto-allocate**, the wizard draws an FEM/port chassis diagram and lets the
user drag ports around before generating.

### Diagram

`renderWiringDiagram()` (`generate.js`) regroups the element-keyed allocation
into the controller-keyed shape `renderInstrumentWiring()` (`app.js`) already
draws for the Instrument Wiring page, then calls it with `{editable: true}`.
Editable mode tags every port `<g>` with `data-con/slot/port/io`, adds a drag
grip to multiplexed cells, and skips the inspector click handlers (the wizard
has no loaded chip to inspect). Every FEM configured in step 3 is drawn even
if empty, so its ports stay visible as drop targets.

### Port monitor

Hovering a port fills a docked **monitor panel** above the diagram
(`#gen-wiring-monitor`) instead of a cursor-following popup — the popup used
to occlude neighbouring ports while dragging. `renderInstrumentWiring()` takes
an `onPortHover` callback; in the wizard's editable mode it routes a port's
`mouseenter` / `mouseleave` to `setMonitorHover()` (`generate.js`) rather than
the popup. During a drag the same panel shows the source line, the hovered
target port, and a valid/invalid badge (the cursor-following drag ghost is
retired). The main Instrument Wiring page is unchanged — it keeps the popup.

### Drag-editing

Two drag handles, different scope:
- a **port circle** drags one element's line — one qubit's readout endpoint,
  one drive, one flux/coupler;
- a **feedline grip** (on cells with ≥2 assignments) drags a whole
  multiplexed feedline as a unit.

`isValidDrop()` enforces the hardware: drives and readout outputs are
interchangeable on any MW-FEM **output** port; readout inputs go to MW-FEM
**input** ports; z/coupler go to LF-FEM outputs. Readout output and input are
independent — dragging one never snaps the other. Drops mutate
`state.allocation` + `state.spec.lines` client-side (`applyPortEdit` /
`applyQubitReadoutEdit` → `syncSpecChannels`); nothing reaches the generator
until **Generate**. Once the user drag-edits, `state.wiringTouched` is set and
`deriveLines()` stops overwriting their feedline groups on revisits.

### LO-safe auto-allocation

A MW-FEM has 5 LOs, each shared by a port pair (Out1+In1, Out2+Out3,
Out4+Out5, Out6+Out7, Out8+In2). `deriveLines()` pins resonator feedlines to
alternating LO-safe pairs — odd feedline → Out8+In1, even → Out1+In2 — which
confines readout to LO1+LO5 and leaves Out2–7 (LO2/3/4) free for drives.
con/slot stay free for the allocator.

### Validation

`validateWiring()` is a rule engine over `state.allocation`:
- **R1 (error)** — qubits sharing one readout output port must share one
  readout input port, and vice versa: one physical output feeds one physical
  input. R1 errors ring the offending ports red and **gate the Generate
  button**.
- **R2 (warning)** — a feedline whose output and input land on different
  MW-FEMs. Allowed, but flagged.

### Band / LO

MW-FEM band / LO selection is derived at the **Populate** step from the
entered RF frequencies — see *`RF_freq`, LO auto-assignment, and units* below.

## How QM generation works (verified reference)

`Instruments()` (`add_mw_fem` / `add_lf_fem` / `add_opx_plus` / `add_octave`)
→ `Connectivity()` (`add_resonator_line` / `add_qubit_drive_lines` /
`add_qubit_flux_lines` / `add_qubit_pair_flux_lines` / `add_twpa_lines` / …)
→ `allocate_wiring()` → `build_quam_wiring()` (emits `wiring.json`) →
`build_quam()` (emits `state.json`) → populate step.

OPX1000 = 8 slots; MW-FEM 2-in/8-out; LF-FEM 2-in/8-out; OPX+ 2-in/10-out;
Octave 2-in/5-out.

## How to verify (current)

```bash
# allocate mode, inside a QM-capable env
<env python> quam_state_manager/generator/run_build.py \
    --mode allocate --spec docs/examples/sample_spec_3q.json --out <tmp>
cat <tmp>/_result.json    # status: ok, with per-qubit rr/xy/z assignments
```

Verified in the `LabA` conda env (`qualang_tools` 0.22.0, `quam_builder`
0.2.0, `quam` 0.5.0a3, `qm` 1.2.6): the 3-qubit sample allocates a shared
multiplexed readout on MW-FEM slot 1 port 1, XY drives on slot 1 ports 2-4,
and flux Z lines on LF-FEM slot 5 ports 1-3.

```bash
# build mode — writes real state.json + wiring.json into --out
<env python> quam_state_manager/generator/run_build.py \
    --mode build --spec docs/examples/sample_spec_3q.json --out <tmp>
```

The build of the 3-qubit sample produces a `wiring.json` whose
`#/ports/...` pointer shape matches the reference
`<quam-states>/example_lab\wiring.json`, and a `state.json` whose
top-level keys (`octaves`, `mixers`, `twpas`, `qubits`, `qubit_pairs`,
`active_qubit_names`, `active_qubit_pair_names`, `ports`, `__class__`) and
`ports` sub-keys match the reference exactly. Each transmon carries the full
`xy` / `z` / `resonator` channel structure.

```bash
# multi-chassis — allocate + build a 2-controller spec
<env python> quam_state_manager/generator/run_build.py \
    --mode build --spec docs/examples/sample_spec_multichassis.json --out <tmp>
```

Verified in the same env: the 2-chassis sample allocates q1 (+ the q1–q2
coupler) onto **con1** and q2/q3 (+ the q2–q3 coupler) onto **con2**; the built
`state.json` / `wiring.json` carry both `con1` and `con2` port pointers, each
readout's input and output share a controller, and every
`downconverter_frequency` points to an `upconverter_frequency` on the *same*
chassis.

## Known limitation — TWPA

`run_build.py` accepts `twpa_pump` / `twpa_isolation` lines in the spec, but
the QM build pipeline in `quam_builder` 0.2.0 cannot generate TWPA wiring:
its `LineTypeRegistry` has no TWPA category, and `WiringLineType.TWPA_PUMP`'s
enum value `"p"` collides with `PLUNGER_GATE`, so the pump would be misfiled
under `qubits` and `build_quam` would reject it. The generator therefore
**skips TWPA lines and records a warning** in `_result.json["warnings"]`
rather than crashing. Upgrade `quam_builder` in the selected env once it
gains TWPA support. Cross-resonance and ZZ lines build correctly.

The wizard's step-4 TWPA subsection shows this limitation inline. Note the
wizard does not yet emit `twpa_*` lines into the spec at all (`deriveLines()`
covers only qubit/pair lines), so a wizard build neither wires TWPAs nor
produces the skip warning — TWPA entries are carried in `spec.twpas` but
currently unused. Wiring the wizard's TWPA step through is tracked as D7.

## Populate parameter catalog (Track D)

`build_quam` produces a structurally-complete `state.json` with QUAM's
*default* physics values. Track D adds a **populate** step: `run_build.py`
applies a `populate` block from the spec onto the machine after `build_quam`
and before the final save. Cataloged against the LabA-version libraries
(`quam` 0.5.0a3, `quam_builder` 0.2.0).

### QUAM components and their populate-relevant fields

| Component | Class | Populate-relevant fields |
|---|---|---|
| Qubit | `BaseTransmon` / `FluxTunableTransmon` | `f_01`, `f_12`, `anharmonicity`, `T1`, `T2ramsey`, `T2echo`, `chi`, `GEF_frequency_shift`, `grid_location`, `freq_vs_flux_01_quad_term`, `phi0_current`, `phi0_voltage` |
| XY drive | `XYDriveMW` / `XYDriveIQ` | `RF_frequency`, `LO_frequency` (= `opx_output.upconverter_frequency`), `opx_output.band`, `opx_output.full_scale_power_dbm`; `intermediate_frequency` is inferred; `set_output_power(dBm)` helper |
| Resonator | `ReadoutResonatorMW` / `…IQ` | `depletion_time`, `frequency_bare`, `f_01`, `time_of_flight`, `RF_frequency`, `LO_frequency`, `confusion_matrix`, `gef_centers`; readout-pulse `length` / `amplitude` |
| Flux line | `FluxLine` | `independent_offset`, `joint_offset`, `min_offset`, `arbitrary_offset`, `flux_point` ∈ joint/independent/min/arbitrary/zero, `settle_time`; `opx_output.output_mode` ∈ direct/amplified, `opx_output.upsampling_mode` ∈ mw/pulse |
| Tunable coupler | `TunableCoupler` | `decouple_offset`, `interaction_offset`, `arbitrary_offset`, `flux_point` ∈ off/on/arbitrary/zero, `settle_time` |
| 1Q gate pulses | `quam.components.pulses` | DragCosine `x180`/`x90`/`y…`: `length`, `amplitude`, `alpha`, `detuning`; `saturation` Square: `length`, `amplitude` |
| Qubit pair (CZ) | `CZGate` macro | `cz_interaction_duration` (ns), CZ flux-pulse `amplitude`, `moving_qubit` (control/target — which qubit's z line plays the flux pulse) |
| TWPA | `TWPA` | pump `frequency` / `amplitude`, gain — **blocked**: TWPA wiring is not built (see the limitation above), so there is no TWPA object to populate |

### The `populate` spec block

```jsonc
"populate": {
  "resonator": {            // per qubit
    "q1": {"RF_freq": 4.395e9, "LO_frequency": 4.75e9, "depletion_time": 2500,
           "time_of_flight": 28, "readout_length": 2500, "readout_amplitude": 0.1,
           "full_scale_power_dbm": -5}
  },
  "qubit": {                // per qubit
    "q1": {"RF_freq": 6.012e9, "anharmonicity": -200e6, "LO_frequency": 6.0e9,
           "grid_location": "0,0", "full_scale_power_dbm": 1}
  },
  "flux": {                 // per qubit (flux-tunable)
    "q1": {"joint_offset": 0.0, "independent_offset": 0.0, "min_offset": 0.0,
           "flux_point": "joint", "output_mode": "amplified",
           "upsampling_mode": "pulse"}
  },
  "pulses": {               // per qubit 1Q-gate pulse params
    "q1": {"x180_length": 40, "x180_amplitude": 0.1, "drag_alpha": -0.15,
           "drag_detuning": 0, "saturation_length": 20000,
           "saturation_amplitude": 0.1}
  },
  "pairs": {                // per pair
    "q1-q2": {"cz_interaction_duration": 100, "cz_amplitude": 0.1,
              "moving_qubit": "control"}
  }
}
```

Every group and every field is optional — missing values keep `build_quam`'s
defaults. `run_build.py`'s `apply_populate(machine, populate)` runs after
`build_quam`, sets whatever is present, then saves.

### `full_scale_power_dbm` (MW-FEM ports) and amplitude semantics

MW-FEM analog output ports each carry a `full_scale_power_dbm` (FSP) that
sets the **maximum power** the port emits when a pulse's `amplitude` is at
its rail (±1). Pulse `amplitude` is therefore a **dimensionless fraction
[-1, 1]** on MW ports — not a voltage. The QM library exposes
authoritative conversion helpers in
`quam_builder/tools/power_tools.py`:

- `P_out_dBm = FSP + 20·log10(|amplitude|)`
- `V_peak = amplitude · sqrt(2 · 50 · 10^(FSP/10) / 1000)` (at 50 Ω)
- FSP allowed range **[-11, +16] dBm in 3 dB steps**
  (`qualang_tools/config/instrument_limits.py:OPX1000_MW_POWER_MIN/MAX/STEP`).

LF-FEM output (`z` lines, CZ flux pulses) is **direct voltage**, not
dimensionless: `cz_amplitude`, flux `joint_offset` etc. are absolute volts.

The wizard's Populate step exposes `full_scale_power_dbm` as a column for
both qubits (XY drive) and resonators (readout). **Multiplexed readout
qubits share one MW-FEM port**, so editing one resonator's FSP cell
auto-syncs that value across every qubit in the same feedline group
(`recomputeReadoutFSP()` in `generate.js`, mirroring how `recomputeLOs()`
handles `LO_frequency`); cells are colour-tagged by group.

### Per-qubit 1Q gate pulses

Real device states show per-qubit calibration of x180 length, amplitude,
DRAG α, and detuning (e.g. LabA uses 8 distinct DRAG α values across nine
qubits; variant-B has `x180_length` 48 vs 80 ns per qubit). `populate.pulses`
is therefore a **per-qubit** map:

```jsonc
"pulses": {
  "q1": {"x180_length": 40, "x180_amplitude": 0.1, "drag_alpha": -0.15, ...},
  "q2": {"x180_length": 40, "x180_amplitude": 0.1, "drag_alpha":  0.00, ...}
}
```

The wizard's pulses table follows the same per-qubit shape as the other
populate tables; the **"Set all →" row** is the new way to express a
common default (typing once propagates to every qubit). `_apply_pulses()`
in `run_build.py` reads `vals[qid]` per qubit; missing keys fall back to
QUAM defaults (`x180_length=40`, `x180_amplitude=0.1`, α=0, detuning=0).

### `RF_freq`, LO auto-assignment, and units

`RF_freq` (renamed from `f_01`) is the RF frequency a qubit's XY drive or a
resonator's readout emits. `run_build.py` writes it to the channel's
`RF_frequency` *and* the QUAM `f_01` attribute.

**LO auto-assignment.** An MW-FEM has 5 LOs, each shared by a port pair
(Out1+In1, Out2+Out3, Out4+Out5, Out6+Out7, Out8+In2), and an LO can
up/down-convert RF only within ±0.4 GHz of itself — a 0.8 GHz IF window.
As `RF_freq` values are entered, the wizard's `recomputeLOs()` (`generate.js`)
sets each LO to the midpoint of the RF values on its port pair and writes it
into every element's `LO_frequency`. Example: drives at 6.5 GHz and 6.2 GHz
sharing an LO → LO 6.35 GHz. It warns when the RF spread on a pair exceeds
0.8 GHz (no single LO can cover them), when an RF lands outside every Nyquist
band, or when a feedline's output- and input-side LOs diverge. Each warning
carries the elements involved, so `recomputeLOs()` also **rings the offending
ports amber** in the step-6 wiring diagram (`.iw-port-conflict`) — amber to
match the conflict panel and stay distinct from the red dashed structural
`.iw-port-invalid` rings; hovering a conflict line emphasises just that
warning's ports. The `LO_frequency` cells stay editable — a hand-typed LO is
kept until the next `RF_freq` edit re-derives it.

**LO-group visualisation.** Because users shuffle port allocations between the
Wiring and Populate steps, `computeLoAssignments()` also returns the LO
*groups* — which qubits share each physical LO. `recomputeLOs()` colour-keys
every `LO_frequency` cell by its LO (same colour = same LO) with a short `LON`
tag, and `renderLoMap()` fills a collapsed-by-default `<details>` panel
(`#gen-lo-map`) that lists each occupied LO per MW-FEM with its port pair,
qubits, frequency and band. The panel is compact and scrolls, so it scales to
many FEMs; its open state persists in `localStorage` (`quam_lo_map_open`).

**Populate wiring diagram.** The Populate step also carries a collapsed-by-default
`<details>` (`#gen-pop-wiring`) holding a **read-only** copy of the step-5
wiring diagram — `renderPopWiring()` calls `renderInstrumentWiring()` with an
`onPortHover` callback but no `editable` flag, so there is no drag (re-wiring
stays in step 5). Hovering a port fills a docked monitor with that qubit's
typed Populate values (in the active units) and highlights its table row;
hovering a table row rings the qubit's port(s) in the diagram — a two-way
cross-reference. To support this, `renderInstrumentWiring()`'s hover routes to
`onPortHover` whenever the callback is supplied, and the inspector
click/double-click handlers are skipped in any wizard context. The panel is
`position: sticky` — it stays pinned to the top while the user scrolls down
the populate tables, so the diagram (and the hovered-port values) remain
visible while filling in values for qubits far down the list. Open state
persists in `localStorage` (`quam_pop_wiring_open`).

**Unit toggle.** The Populate step has four stage-wide unit selectors —
Frequency (Hz/MHz/GHz), Time (ns/µs), Voltage (V/mV), and **Amplitude**
(0–1 / dBm / peak V at 50 Ω) — so a value can be typed in whichever form
matches how the user thinks about the signal. The selectors are
display-only: `spec.populate` always stores the SI base form (Hz, ns, V,
and dimensionless [-1, 1] for MW amplitudes), the generator's contract.
The Amplitude conversion is **context-dependent** — it uses each row's
`full_scale_power_dbm` (qubit XY FSP for `x180_amplitude` /
`saturation_amplitude`; resonator FSP for `readout_amplitude`; fallback
`-11 dBm` = the QUAM port default) — so a single typed dBm value can
resolve to different dimensionless amplitudes across qubits with
different FSPs. Editing a row's FSP cell refreshes every amp cell that
depends on it. The choice persists in `localStorage` under
`quam_populate_units`. Formulas verified against
`quam_builder/tools/power_tools.py` (`set_output_power_mw_channel` /
`get_output_power_mw_channel` / `calculate_voltage_scaling_factor`).

**Set all.** Each per-row populate table has a tinted "Set all →" row as its
first body row. Typing a value into a column's "Set all" cell and committing
it (Enter / blur) writes that value to every qubit/pair in the column,
respecting the active unit; an empty commit clears the column. It overwrites
all rows — seed the common value, then tweak outliers. `LO_frequency` and
`grid_location` are **excluded** (shown as a muted "—"): an LO must stay
auto-derived from RF to respect its IF window, and grid positions are unique
per qubit. The row carries no persisted state, so a unit switch rebuilds it
blank.

### Post-save fix-up: shared readout LO encoding

The Populate step asks the user for **one `LO_frequency` per readout
feedline**, not two — there is no separate upconverter/downconverter
input. Hardware-wise the readout output and input on a given MW-FEM
port share a single physical local oscillator, so the wizard's single-
value model is correct. `_set_channel_lo()` in `run_build.py` writes
that one LO to both the output's `upconverter_frequency` and the
input's `downconverter_frequency`.

The problem is the **encoding** in the saved `state.json`. Without
intervention QUAM sometimes serializes the input as a JSON pointer
to the output (example-9q-rack style) and sometimes as two
independent floats (variant-B style). In the second form the constraint
is implicit and silently drifts the moment anyone edits either side
(inline editor, Plotly click popup, external tool).

To lock the constraint in, after `machine.save()` the build flow
runs `_link_input_downconverters_to_outputs(state_path, wiring_path)`.
Algorithm:

1. Walk `wiring.qubits[*].rr` for every qubit; collect the
   `(opx_input, opx_output)` pointer pairs.
2. For each pair, if the output port has a literal numeric
   `upconverter_frequency`, rewrite the input port's
   `downconverter_frequency` to the JSON pointer
   `#/ports/mw_outputs/<con>/<slot>/<port>/upconverter_frequency`.
3. Atomic write back to `state.json` via `os.replace`.

Idempotent (re-running is a no-op), defensive (no crash on missing
files or malformed wiring), and scoped only to `downconverter_frequency` —
`band` stays a literal, matching the example-9q-rack pattern already
in production.

Tests: `tests/test_run_build_link_downconverter.py` covers 14 cases
(happy path, multi-feedline, multi-qubit dedup, idempotent, already-
pointer input, missing upconverter, no readout, malformed wiring,
missing state file, band-stays-literal).

### Track D phase mapping

D2 resonator · D3 qubit/XY · D4 flux · D5 1Q-gate pulses · D6 pairs/CZ ·
D7 TWPA (blocked by the wiring limitation — deferred) · D8 the wizard's
Populate step + form.

## Phase status

| Phase | Scope | Status |
|---|---|---|
| A1+A2 | `run_build.py` skeleton + `allocate` mode (qubit lines) | **Done** |
| A3 | `build` mode (qubits only) | **Done** |
| A4 | qubit pairs + tunable couplers | **Done** |
| A5 | OPX+/Octave, cross-resonance/ZZ (TWPA skipped — see above) | **Done** |
| B1 | `core/config_generator.py` — spec validation | **Done** |
| B2 | conda-env discovery + selection | **Done** |
| B3 | subprocess runner + result parsing | **Done** |
| B4 | Flask routes for `/generate` | **Done** |
| C1 | wizard shell + step navigation | **Done** |
| C2 | step — Environment picker | **Done** |
| C3 | step — Network | **Done** |
| C4 | step — Chassis builder; keyboard-navigable slot grid (roving tabindex, arrow keys, M/L/Del hotkeys, keyboard FEM menu) | **Done** |
| C5 | step — Qubits / Pairs / TWPA | **Done** |
| C6 | step — Wiring: LO-safe auto-allocate + drag-editable FEM/port diagram (all FEMs shown, grip vs per-qubit drag); structural validation — R1 one-output↔one-input error gates Generate, R2 off-FEM-feedline warning; band/LO flagged in step 6; docked hover/drag monitor panel | **Done** |
| C7 | step — Output folder | **Done** |
| C8 | step — Review & Generate | **Done** |
| D1 | catalog the QUAM populate schema | **Done** |
| D2 | populate — resonator | **Done** |
| D3 | populate — qubit / XY | **Done** |
| D4 | populate — flux | **Done** |
| D5 | populate — 1Q-gate pulses | **Done** |
| D6 | populate — pairs / CZ | **Done** |
| D7 | populate — TWPA | **Deferred** — TWPA wiring is blocked by `quam_builder` 0.2.0, so no TWPA object exists to populate. Revisit when the library gains TWPA support. |
| D8 | wizard Populate step + form; `RF_freq` rename, MW-FEM LO auto-assignment from RF, stage-wide unit toggle; shared-LO group colour-coding + collapsible LO-map panel; read-only wiring diagram with table↔diagram linking | **Done** |
| E1 | PyInstaller spec — ship generator/ | **Done** |
| E2 | CSS polish + graceful no-env state | **Done** |
| E3 | docs + full verification | **Done** |

See the plan file for the full phase breakdown and dependency order.

## 2026-06-12 update — post-build preview, runner dedup

- **Post-build "Preview config".** The wizard's build-result panel gained
  a `Preview config` button next to `Load into app`: it POSTs
  `/generate/preview-config {path}`, which runs `run_config_preview`
  directly against the just-built output folder (no QuamStore needed)
  and renders the config into the shared `#json-panel` tree
  (`renderJsonTree(..., {valueClick: "copy"})`) plus a summary line
  (qubit/pair counts, lib versions, warnings). This closes the old
  4-hop round trip (build → Load → find Config Viewer → Regenerate).
- **Seed-on-load.** The preview result is stashed in `_PREVIEW_SEEDS`
  (max 4 entries, 15-min TTL, path-keyed) and `/generate/load`
  transplants it onto the freshly activated store's
  `generated_config`/`_meta` when the loaded content's hash equals the
  hash taken before the preview spawn — so detail pages show the config
  immediately, without a second subprocess. On hash mismatch (files
  edited between preview and load, or an old working copy rehydrated)
  the seed is skipped and the user regenerates.
- **Runner dedup.** `run_generator` / `run_config_preview` now share
  `_blank_outcome()` + `_run_script_outcome()` (same `_result.json`
  parse/cleanup tail; error strings unchanged), and the two script-path
  locators collapsed into `_script_path(filename)`. The copy-pasted
  `_library_versions()` moved into `generator/_script_common.py`; both
  standalone scripts import it after a defensive
  `sys.path.insert(0, <script dir>)` (CPython normally prepends the
  script dir, but `PYTHONSAFEPATH`/`-P` suppress it). PyInstaller's
  datas already ships the whole `generator/` dir, so the shared module
  travels with the frozen bundle automatically — verify the next
  `dist/` listing once.
