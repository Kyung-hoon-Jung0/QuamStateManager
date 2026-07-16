# 54 — Cross-Resonance (CR) / ZZ-Drive Integration

Binding design record for first-class fixed-frequency CR-gate chip support:
generation (wizard + regenerate + emitted scripts), editing (inspector, grids,
Pulses), and browsing (Chip Status, Compare, diagnostics, Param History).
Foundation module: `core/cr_semantics.py`. Fixture corpus: `tests/cr_fixtures.py`.

## 1. The three schema flavors

The quam-builder CR/ZZ schema exists in (at least) three generations. Real chips
from all of them must render and edit correctly. **All accessors are key-driven,
never version-driven** — read what is actually in the JSON.

| flavor | builder source | CR channel keys | levers live | pair ZZ key | notes |
|---|---|---|---|---|---|
| `lo_if` | quam-builder @ `a08bf66` (installed lab envs; wizard-built chips) | `target_qubit_LO_frequency` + `target_qubit_IF_frequency` literals, `bell_state_fidelity` | on channel | `zz_drive` | exhaustive-null serialization; dedicated CR port per pair |
| `rf` | `feat/add-cr-cz-macros` @ **`fa540b6`** (2026-07-14) — **all customer artifacts** | `target_qubit_RF_frequency` (usually a `#/qubits/<qt>/xy/RF_frequency` pointer) | on channel (macro carries `qc/qt_correction_phase` too) | `zz_drive` | sparse serialization possible (null keys stripped, bare `{__class__}` macro); qubits may be `FixedFrequencyZZDriveTransmon`; shared control-xy port + dual upconverter |
| `rf_drive` | branch tip @ `c119d62` (2026-07-16) — **PROVISIONAL, no artifact** | `target_qubit_RF_frequency` only | on the CRGate macro | `zz` | classes renamed `CrossResonanceDriveMW/IQ`, module `components/cross_resonance_drive` |

**Detection precedence** (`cr_semantics.detect_pair_flavor`):
1. channel `__class__` module contains `.cross_resonance_drive.` or leaf starts
   `CrossResonanceDrive` → `rf_drive` (class evidence is the ONLY `rf_drive`
   trigger — a sparse `rf` chip omits lever keys too and must never read as tip);
2. any of `target_qubit_LO_frequency` / `target_qubit_IF_frequency` /
   `bell_state_fidelity` present → `lo_if`;
3. `target_qubit_RF_frequency` present (with or without lever keys) → `rf`;
4. CR channel present but no signal → `unknown`; CR macro without channel →
   `unknown`; no CR content → `none`.
Chip level = majority over pairs (deterministic tie-break), `mixed` flag when
pairs disagree.

## 2. Class-name tolerance registry

`cr_semantics.classify_class` mirrors `pulse_catalog.resolve_qclass`: exact
registry paths → `"exact"`, leaf-name fallback → `"leaf"` ("leaf-match renders,
exact-match trusts"). Registered paths (provenance = commit observed at):

- `components.cross_resonance.CrossResonanceMW/IQ` (a08bf66, fa540b6)
- `components.cross_resonance_drive.CrossResonanceDriveMW/IQ` (c119d62)
- `components.zz_drive.ZZDriveMW/IQ` (all)
- `custom_gates.fixed_transmon_pair.two_qubit_gates.CRGate` /
  `StarkInducedCZGate` (+ flux-tunable-tree and package-top re-export variants)
- qubits: `fixed_frequency_transmon.FixedFrequencyTransmon` /
  `FixedFrequencyZZDriveTransmon` (the latter exists ONLY on the branch)

`StarkInducedCZGate` is leaf-matched **before** `CZGate` (substring overlap).

## 3. Effective frequency chain (`effective_frequencies`)

The CR/ZZ channel's `intermediate_frequency`/`RF_frequency` are
`#./inferred_*` runtime `@property`s — `pointer_resolver` deliberately returns
them raw. `cr_semantics.effective_frequencies(store, pair_id, channel)`
re-implements the arithmetic (the `pulse_catalog.inferred_length` precedent):

- **LO**: `LO_frequency` absolute pointer (rf flavor:
  `#/qubits/<qc>/xy/opx_output/upconverters/2/frequency` — the resolver's
  through-pointer support crosses the `opx_output` wiring hop) — or
  `#./upconverter_frequency` self-ref → emulate `MWChannel`: port
  `upconverters[str(channel.upconverter)]["frequency"]` else scalar
  `upconverter_frequency`.
- **target RF**: `target_qubit_RF_frequency` (pointer or literal) — or
  `target_qubit_LO_frequency + target_qubit_IF_frequency` (lo_if flavor).
- **IF** = target RF − LO (+ `detuning` for ZZ channels).
- **Verdict**: `|IF| ≤ MW_MAX_ABS_IF_HZ` (= 400 MHz, homed in `core/mw_fem.py`
  — the exact bound the customer's populate scripts assert) + port-band
  consistency. Unresolvable inputs become `problems` strings — never
  exceptions, never guesses. Advisory only (warn-never-block philosophy).

## 4. Fidelity precedence (binding)

`cr_semantics.fidelity`: **macro first** (`CRGate.fidelity` exists in every
generation; ladder = `StandardRB.average_gate_fidelity` → bare `StandardRB`
float ⇒ `clifford=True` → `Bell_State.Fidelity` → bare float), **channel
`bell_state_fidelity` fallback** (exists only in `lo_if`). Consumers: Chip
Status edge fidelity, Compare `canonical_pair_fidelity`, pairs list.

## 5. The phantom-"Cr" fix

`query.get_pair`'s CZ-shape detection keyed on the PRESENCE of
`{flux_pulse_qubit, coupler_flux_pulse, phase_shift_control,
phase_shift_target, fidelity}`. Modern CRGate macros carry a (null) `fidelity`
field → an all-None CZ-shaped editable "Cr" section whose Apply always 400'd.
Fixed via `cr_semantics.is_cz_shaped_macro`: `fidelity` removed from the
presence set; a macro that positively classifies as CR/Stark-CZ is never
CZ-shaped; a macro merely NAMED `cr` but CZ-shaped is not a CR gate.

## 6. Directed-pair policy (binding)

CR is directional; both directions of a physical edge are **independent
calibration targets** (separate LO2/levers/ops). Surfaces stay directed —
no edge-collapsed data model. `physical_edge_key` gives the undirected
identity for adjacency/sorting/rendering offsets; `directed_partner` finds the
reverse pair; `is_active` reads `active_qubit_pair_names` (absent/empty list
⇒ everything active — CZ chips and old states must not render "inactive").
Qubit names come from the `qubit_control`/`qubit_target` pointers, **never**
from splitting the pair id (`"q0-1"` splits to `"1"`, not `"q1"`).

## 7. Fixture & scrub rules

`tests/cr_fixtures.py` builders (`make_flavor_a/b/c`, `make_cz_reference`)
are the committed corpus — synthetic shapes copied from the real artifacts
with **scrubbed network** (`127.0.0.1` / `my_cluster`) and round frequencies
so IF math is exact (flavor B: +200/−50/+50 MHz and one deliberate +450 MHz
violation on `q2-1`). Real-artifact tests are path-gated with placeholder
paths and skip everywhere public; assertions never compare network values.

## 8. Environment matrix

| env | quam-builder | writes flavor | loads customer states? |
|---|---|---|---|
| `KRISS_CR3` | a08bf66 (metadata says 0.2.0 — versions lie, probe by symbol) | `lo_if` | **no** (unknown field + missing qubit class) |
| `QRS` | a08bf66 (same git pin) | `lo_if` | **no** |
| `CR_FA540B6` (to create) | `git+…quam-builder.git@fa540b6` | `rf` | **yes** |

The branch churns daily (R2) — the tip CANNOT load fa540b6-flavor states
(module rename); pin the SHA, never the branch name. Windows conda pythons
cannot see WSL `/tmp` — probe scripts must live on drive-letter paths.

## 8b. Live verification results (2026-07-17)

- **Shared-port build**: a 3-qubit CR+ZZ spec (`cr_port_mode=shared_xy`,
  directed pairs, `cr_shapes=full`) built in the fa540b6 env with ZERO
  warnings; every CR/ZZ wiring port == its control's xy port, dual
  `upconverters {1,2}` installed (LO2 = partner mean), the full RF pointer
  web, 4 drive shapes + 4 cancel twins, `stark_cz` macro, and the target's
  `xy_detuned` twins (created by direct assignment — no wiring line type
  exists for it). Wirer gotcha encoded in `allocate_full`: within ONE
  `allocate_wiring` call, used channels stay blocked until call end, so
  shared-mode pair lines get ONE allocate call each (two CR lines pinned to
  the same control port collide otherwise — the customer script's
  allocate-after-every-add idiom).
- **Customer state loads**: the real CR chip (root
  `quam_config.my_quam.Quam`) now loads + `generate_config()`s in the
  fa540b6 env via the preview loader's fallback chain — extended with
  `FixedFrequencyZZDriveQuam` and a lossless shim that retries from a
  scratch COPY with unknown-but-EMPTY root keys stripped (the chip's
  `active_twpa_names: []` predates the root's schema; real data never
  dropped, source never touched).
- **Effective-IF oracle**: `cr_semantics.effective_frequencies` matches the
  freshly generated config's per-CR-element `intermediate_frequency`
  EXACTLY (8/8 directed pairs, < mHz) on the real customer chip —
  confirming both the emulation and the staleness of the shipped
  `qua_config.json`.

## 9. Known caveats

- The shipped `CR_gates/.../qua_config.json` is a **stale** oracle (config IFs
  predate the state's recalibration — e.g. config `cr_q1_q2` +205 MHz vs
  +131.4 MHz recomputed from the current state). Use it as a *structural*
  golden only; numeric parity tests must generate a **fresh** config in the
  fa540b6 env.
- `/mnt/d/work_laptop/quam_states/KRISS_CR` is NOT a CR chip despite its name
  (flux-tunable CZ) — the real references are `CR_state/` and `gen_2x3_cr/`.

## 10. Open risks

- **R1** `rf_drive` is provisional — lever homes / ZZ module names unverified;
  re-pin `make_flavor_c` + the registry the moment a tip-built state exists.
- **R2** branch churn — registry absorbs new paths; treat the branch as unstable.
- **R3** fa540b6 env resolution (quam floor, qm-qua availability) unverified
  until the env is created.
- **R4** quam `upconverters` parent-quirk (`.parent = None` in the customer
  populate) + `xy_detuned` type-union warning — live-env goldens are the gate.
- **R5** Param-History chip-fingerprint collisions between generic `q0..q7`
  chips — deferred, documented.
