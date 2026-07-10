# 29: Create New Gate / Pulse Operations from Detail Pages

> A researcher can click **+ Add gate** on a qubit pair detail page (CZ
> macros, including AC-flux parametric CZ) and **+ Add pulse** on a
> qubit detail page (SquarePulse, DragCosinePulse) and have those
> operations written into the working copy of `state.json`. Phase 1
> (gate macros), Phase 2 (pulses on qubits) and Phase 3a (parametric CZ
> via a new `quam_builder` class) have shipped. Other gate *types*
> (iSWAP, SWAP) remain blocked on more `quam_builder` work.
>
> **Read this if you are:** touching `modifier.create_subtree`, the pair
> or qubit detail page, the change-log / undo flow, or designing a
> follow-up that creates new keys in QUAM state.
>
> **Status:** Phase 1 + Phase 2 shipped. Experimental — verify in browser
> before relying on these flows.

---

## Why this exists

Calibration scripts like `2Q_31_chevron_11_02.py` create CZ gates in code:

```python
from quam.components.pulses import SquarePulse
from quam_builder...two_qubit_gates import CZGate

control_q.z.operations[pulse_id] = SquarePulse(length=100, amplitude=0.1)
pair.macros["cz_unipolar"] = CZGate(flux_pulse_qubit=pulse_id, moving_qubit="control")
```

That works fine *if you can read Python and know the schema*. Researchers
asked for an in-app way to add gates without dropping into scripts, so the
amplitude/length tuning, fidelity tracking, and visual diff stay where the
rest of the workflow already lives.

## What v1 ships

- A **+ Add gate** button on every pair detail page.
- Modal form with name input, gate-type dropdown (`cz_unipolar` /
  `cz_flattop`), type-aware numeric fields with sensible defaults, and a
  live JSON preview.
- Naming-collision rule: **reject** if a gate of that name already exists.
  The user can pick a different name; replacing an existing gate is
  out-of-scope for v1.
- The newly-created macro shows up in the pair detail page immediately,
  editable like every existing gate.
- One entry in the pending-changes tray per created gate (not per leaf).
  Undoing it deletes the whole subtree cleanly.

## What Phase 2 ships (Flavor A — pulses on qubits)

- A **+ Add pulse** button at the bottom of every qubit detail page.
- Modal form with name input, channel dropdown (`xy` / `z` / `resonator`),
  pulse-type dropdown (`SquarePulse` / `DragCosinePulse`), type-aware
  numeric fields with sensible defaults, and a live JSON preview.
- The pulse template includes a `__class__` field
  (`quam.components.pulses.SquarePulse` /
  `quam.components.pulses.DragCosinePulse`) so QUAM's loader can re-hydrate
  the right pulse subclass on next read.
- Newly-created pulses **render automatically** on the qubit detail page —
  the dynamic section sweep picks up any operation not in the static
  `x180_DragCosine` / `x90_DragCosine` / `saturation` / `readout` set.
- Same per-creation `ChangeEntry`, same pending-tray UX, same one-shot
  undo as gate macros.

The chevron-calibration pattern
(`control_q.z.operations[pulse_id] = SquarePulse(...)`) maps directly:
choose **z**, pick **SquarePulse**, set amplitude + length, hit submit.
The channel dropdown is intentionally restricted to the three slots a
QUAM transmon exposes; if a qubit lacks an `operations` dict on a chosen
channel the route returns a clear 400 explaining what to do.

## What v1 does *not* ship

- Flavor C: new gate *types* (iSWAP, SWAP, parametric CZ) are blocked on
  upstream `quam_builder` work. Today `quam_builder` 0.2.0 only exports
  `CZGate`; introducing a new macro class needs a PR there first. See the
  TWPA precedent in [27_config_generator.md](27_config_generator.md).
- Additional pulse shapes (`DragGaussianPulse`, `FlatTopGaussianPulse`,
  arbitrary waveforms) are not in the form yet — the two shipped types
  cover the bread-and-butter chevron / DRAG flows. Extending the registry
  is purely additive (`_PULSE_TYPES` + the JS `QCLASS` map).
- Pointer-creation UX: every field becomes a literal. Pointer-aware editing
  (e.g. shared length via `#../x180/length`) is a v2 concern.
- Gate / pulse deletion: there is no UI to remove an operation. Undoing
  via the pending tray works only until the next save.
- Bulk creation: one gate or pulse at a time.
- Wizard Populate-step integration: explicitly out of scope (user chose
  the pair / qubit detail pages as the only v1 entrypoint).

## The pieces

### `Modifier.create_subtree(dot_path, value)` — `core/modifier.py`

The new primitive. Walks the parent path (must exist), refuses to overwrite
the leaf key (raises `KeyError`), then deep-copies *value* into both the
merged dict and the appropriate source dict (`state.json` or `wiring.json`).
Logs **one** `ChangeEntry` for the whole creation with `created=True`.

Undo / discard / batch-rollback detect `created=True` and delete the new
key (and the whole subtree under it), with no orphan intermediate dicts.

Every leaf inside the new subtree is registered with the search index via
`SearchIndex.add_entry`, so newly-added fields are searchable without a
reload. Undo calls `SearchIndex.remove_entry` on each leaf.

### `ChangeEntry.created: bool` — `core/loader.py`

New field, default `False`. Backwards-compatible: all existing constructions
omit the flag. `created=True` only flows through `create_subtree` today.

### `POST /pair/<name>/gate` — `web/routes.py`

Form fields:

- `gate_name` — must match `^[a-zA-Z][a-zA-Z0-9_]{0,63}$`.
- `gate_type` — one of `cz_unipolar`, `cz_flattop`.
- Type-specific numeric fields: `amplitude`, `length`, `coupler_amplitude`,
  `coupler_length`, `phase_shift_control`, `phase_shift_target`, plus
  `flat_length` / `smoothing_length` for the flat-top variant.

Server-side templates in `_build_gate_template()` produce the exact CZ
macro schema (matching `tests/test_query.py`'s fixture). On 4xx errors the
route returns a `_status.html` partial with a human-readable message; on
success it re-renders the pair detail panel + pending-tray OOB.

### `GET /pair/<name>/gate/new` and `.../cancel`

The form partial and the cancel-restore partial. Both render under the
same `#pair-add-gate-area` wrapper inside the pair detail page; HTMX
swaps drive the show/hide.

### `POST /qubit/<name>/operation` — Phase 2

Form fields:

- `op_name` — must match `^[a-zA-Z][a-zA-Z0-9_]{0,63}$`.
- `channel` — one of `xy`, `z`, `resonator`.
- `pulse_type` — one of `SquarePulse`, `DragCosinePulse`.
- Type-specific numeric fields:
  - **SquarePulse**: `amplitude`, `length`.
  - **DragCosinePulse**: `amplitude`, `length`, `alpha`, `anharmonicity`,
    `detuning`.

`_build_pulse_template()` injects the canonical `__class__` string
(`quam.components.pulses.<Type>`) before handing the dict to
`create_subtree(f"qubits.{name}.{channel}.operations.{op_name}", ...)`.

The companion `GET /new` returns `_qubit_add_pulse.html`; `GET /new/cancel`
returns the restored `_qubit_add_pulse_area.html` button.

### `_qubit_add_pulse.html`

Same UI grammar as the pair form (live JSON preview, type-aware fieldsets,
client-side duplicate check that respects the active channel). Submit
posts via HTMX and the full qubit detail panel re-renders.

### `_pair_add_gate.html`

The form template. Has a small inline `<script>` that:

- Switches the visible fieldset based on the gate-type dropdown.
- Builds a live JSON preview from the current field values so the user
  sees exactly what will be written before submit.
- Refuses duplicate names client-side (the server also rejects).

### `core/query.py` and `web/routes.py` — generalized gate iteration

`QueryEngine.get_pair()` used to iterate the hardcoded tuple
`("cz_unipolar", "cz_flattop")`. Now it walks whatever lives in
`pair["macros"]`, so a `cz_v3` (or any other name) renders automatically.
The route helper `_build_pair_sections()` does the same and uses
`_humanize_gate_name()` to title-case unknown names.

`_build_qubit_sections()` keeps its static `_QUBIT_PROPERTY_MAP` (so the
familiar XY-Drive / Readout / Flux layout is unchanged) and then sweeps
each channel for any operation **not** in `_QUBIT_KNOWN_OPS`. Every such
operation gets its own section labelled `<CHANNEL> · <op_name>` with one
row per field. The `__class__` key is hidden from the property grid.

## Naming-collision rule

Reject by default. The form blocks submission client-side and the server
returns 409 if the user bypasses the client check. There is no "replace"
toggle in v1 — collisions are rare enough that asking the user to pick a
different name is acceptable, and forces them to think about whether
they actually want to overwrite.

## Live-file conflict caveat

The Add Gate dialog renders a warning: **"Live file may be overwritten by
experiments — review the JSON above before committing."** This matches the
broader working-copy story documented in
[28_conflict_safe_io.md](28_conflict_safe_io.md):

- The app edits a working copy under `instance/working_state/`.
- Calibration scripts (chevron, etc.) write the *live* `state.json`.
- A UI-created gate persists in the working copy; only **Apply to live**
  writes it back to disk where a running script can see it.
- If you Apply to live while a script is recording state updates, the
  script's next save may overwrite your gate. Today the only mitigation
  is "don't apply while a calibration is running."

A future iteration could surface this conflict in the pending tray.

## Phase 3a — ParametricCZGate (shipped)

A new `ParametricCZGate` class lives in `quam_builder` next to `CZGate`
and is exported from
`quam_builder.architecture.superconducting.custom_gates`. Schema
mirrors `CZGate` (same `flux_pulse_qubit`, `coupler_flux_pulse`,
`phase_shift_*`, spectator fields) and adds **`modulation_frequency: float`**
(Hz). The runtime `apply()` wraps the moving-qubit `play` with
`update_frequency(z, modulation_frequency)` before and
`update_frequency(z, 0)` after, so the OPX synthesizes an AC sideband
on the z line for the gate duration.

### How the three pieces connect

1. **state-manager UI** — `_GATE_TYPES["cz_parametric"]` in `web/routes.py`
   exposes the form fields (`amplitude`, `length`, `modulation_frequency`,
   `coupler_amplitude`, two phase shifts). `_build_gate_template()` emits
   the canonical JSON shape with the fully-qualified
   `__class__: "quam_builder...ParametricCZGate"` string.
2. **state.json** — the macro persists as:
   ```jsonc
   "cz_param_v1": {
     "__class__": "quam_builder...ParametricCZGate",
     "fidelity": {},
     "flux_pulse_qubit": { "amplitude": 0.04, "length": 120 },
     "modulation_frequency": 3.0e8,
     "coupler_flux_pulse": { "amplitude": 0.01 },
     "phase_shift_control": 0.0,
     "phase_shift_target": 0.0
   }
   ```
3. **run_build.py** — when the wizard regenerates from spec, the new
   `gate_type: "cz_parametric"` field in `populate.pairs.<id>` dispatches
   to `ParametricCZGate(flux_pulse_qubit=..., modulation_frequency=...)`.
   On installs without the upgraded `quam_builder` the import fails
   gracefully and the generator falls back to `cz_unipolar` (warning
   printed to stderr).

### Dependency caveat

The state-manager UI writes the parametric macro to `state.json` regardless
of which `quam_builder` version is installed in the *generator* env — the
UI is just dictionary editing. But the resulting state.json will only
**load** correctly into a QUAM whose `quam_builder` exports
`ParametricCZGate`. Until that lands in a released version of
`quam-builder` on PyPI, this is feature-flagged by environment rather
than by app config: if the team's calibration env doesn't have the new
class, parametric gates will fail to deserialize there.

## Other gate types — still blocked

iSWAP, SWAP, and parametric-iSWAP need their own macro classes in
`quam_builder`. The work is purely additive (mirror `ParametricCZGate`'s
shape, register in `_GATE_TYPES`, extend `_build_gate_template`), but
each one needs an upstream PR first. Same TWPA precedent in
[27_config_generator.md](27_config_generator.md).

## Tests

`tests/test_modifier.py::TestCreateSubtree` covers happy-path creation,
key-collision rejection, missing-parent rejection, deep-copy isolation,
and that the new subtree is editable via `set_value` afterwards.

`tests/test_modifier.py::TestUndoCreate` covers undo / discard / batch
rollback, including the "create, then edit, then undo" sequence.

`tests/test_modifier.py::TestCreateSubtreeSearchIndex` confirms every
leaf inside the new subtree is registered with the search index and
removed on undo.

`tests/test_web.py::TestAddGateFlow` (Phase 1) and
`tests/test_web.py::TestAddPulseFlow` (Phase 2) cover the full HTTP
surface: form rendering, cancel restore, successful creation for each
type, channel / name / type validation (400), collision rejection
(409), unknown qubit / pair (404), single change-log entry per creation,
and undo-deletes-the-creation.

## Verification

Run the dev server in the `qm_mng` conda env on a free port (kill stale
Flask processes first per the standing project conventions), open a pair
detail page, click **+ Add gate**, pick a name and type, watch the JSON
preview, submit, confirm the new section appears with the values you
entered. Verify in `instance/working_state/state.json` that the schema
matches the fixture in `tests/test_query.py`'s `cz_flattop` / `cz_unipolar`
shape byte-for-byte.
