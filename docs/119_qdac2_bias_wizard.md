# 119 — QDAC-II bias in the Generate Config wizard

*2026-08-14. Session slug `add-qdac2-config-wizard`, conda env `CQT_20Q`.
Some labs flux-bias specific qubits from an external QDAC-II DC voltage
source instead of an OPX LF-FEM port. There is no upstream
qualang_tools/quam_builder support for this — it exists only as the
customer's own `quam_config.qdac_components` module
(`QdacInstrument` / `QdacBiasLine` / `QdacBiasedFixedFrequencyTransmon`).
Verified end-to-end on a real 20-qubit chip via `CQT_20Q` (state backed up
first).*

## The shape of the problem

A QDAC-biased qubit's `z` is not a pulsed OPX flux line — it's a static DC
bias line with no `operations` dict, driven from an entirely different
instrument. Everywhere the generator currently assumed "if `qubit.z` is not
None, it can play a flux pulse" needed a second gate, and the customer's own
component classes have no upstream registration, so this is DEGRADE-only
end to end: a missing `quam_config` import, a failed trigger allocation, or
any per-qubit attach error must leave that qubit with no z/bias component
and a warning — never a crash, never a half-built qubit. Same doctrine as
the TWPA `hasattr(connectivity, "add_twpa_lines")` belt-and-suspenders
pattern (`docs/…` TWPA capability), and the same DEGRADE/BLOCKER split as
every other row in `capabilities.REGISTRY`.

## Two new capability ids, both DEGRADE

`instr.qdac` (bare-import locator on `quam_config.qdac_components` — no
attr/cls to check, present iff the module imports at all) and
`wire.qdac_trigger_line` (`Connectivity.add_wiring_spec`, the private-ish
API the trigger allocation needs). Both are requested **only** when
`spec.qdac.qubits` is non-empty — a chip with no QDAC-biased qubits never
even probes for the module. Mirrors the TWPA pattern of context-gated
requirement (`required_capabilities` adds ids by *inclusion*, not always).

## `validate_spec`: structural checks, same spirit as the twpas block

`spec.qdac` (comm type Ethernet/USB with the matching required field,
port, per-qubit channel/trigger_port/output_range/output_filter enums,
numeric dwell/slew_rate/settle_time/dc_offset) is pure JSON validation with
no QM imports — same as the existing `twpas` block. One cross-check that
doesn't exist for TWPAs: a qubit can't be both QDAC-biased (`spec.qdac.
qubits`) and carry an OPX `flux` line in `spec.lines` — its z bias comes
from one place, not two. Channel numbers must be unique per QDAC instrument
(a device-level constraint, not per-qubit).

## The trigger-wiring allocation is a second, isolated `Connectivity`

`quam_builder.create_wiring()`/`build_quam()` raise `ValueError` on any
wiring line type they don't recognize, and the QDAC-II trigger's custom
`"qt"` line type is not in their whitelist — confirmed against the
customer's own `build_quam_wiring_qdac.py`, which forks the *entire* wiring
builder rather than teach `create_wiring` about `"qt"`. So
`_allocate_qdac_triggers` builds a **second** `Connectivity`, sharing the
same `instruments` object so the port pool stays conflict-free with the
main allocation, calls `add_wiring_spec` + `allocate_wiring` on it, and
reads back `(con, slot, port)` via `read_allocation()` — the `"qt"` spec
never reaches `build_quam_wiring`/`build_quam` at all. Unlike the
customer's own trigger cabling (which shares one physical port across
qubits armed on the same ext input), the wizard gives every QDAC-biased
qubit its **own** dedicated auto-allocated digital-output port — simpler,
and the explicit "no port sharing" UI decision. Any failure here (old
qualang_tools missing the wiring-spec API, a private-API shape change)
degrades to "no trigger wiring, warning emitted" — the static `dc_offset`
bias still gets written either way.

## Attaching the bias line: reassign the class in place

`QdacBiasedFixedFrequencyTransmon` differs from the qubit's already-built
class only in the type of `z` (confirmed against the customer's own
`qdac_components.py` — no other fields differ), so `_apply_qdac` does
`qubit.__class__ = QdacBiasedFixedFrequencyTransmon; qubit.z =
QdacBiasLine(...)` **in place** rather than reconstructing a fresh instance
and moving children between parents — same object identity, same
already-built resonator/xy children, same parent/root wiring. Must run
**before** `apply_populate`/`_finalize_pair_gates` so their
`z.operations`/`z.independent_offset` guards (below) see the final
per-qubit class.

Two guards had to widen because "has a `z`" stopped meaning "has a pulsed
flux line":
- `apply_populate`'s flux-seeding loop now checks
  `hasattr(z, "independent_offset")` (a field `QdacBiasLine` doesn't have)
  instead of `z is not None`, so a QDAC-biased qubit's populate.flux
  entries are silently skipped rather than crashing on assignment.
- `_apply_pairs` and `_seed_cz_variant` now check `hasattr(moving_q.z,
  "operations")` before writing a CZ flux pulse — a QDAC-biased moving
  qubit can't play a flux pulse there at all, so that pair's CZ macro is
  skipped with a named warning instead of the whole pair failing.

## Two post-save file patches, not live `machine.*` writes

Both go through the same ATOMIC tmp+`os.replace` pattern as the existing
`_link_input_downconverters_to_outputs` fix-up, for the same underlying
reason:

- **`_inject_qdac_state`** adds the top-level `state["qdac"]` instrument
  entry. Verified against a real build that `FluxTunableQuam`/
  `FixedFrequencyQuam` (the QPU root classes this generator builds onto)
  declare **no** `qdac` dataclass field — an assigned-but-undeclared
  attribute (`machine.qdac = QdacInstrument(...)`) is silently dropped by
  `Quam.save()`. It sets without error and never reaches state.json, unlike
  `qubit.z`, which *is* a real declared field on every transmon class. So
  the instrument dict is built, construction-validated in memory, and
  patched into the saved JSON directly.
- **`_inject_qdac_trigger_wiring`** adds `wiring.qubits.<qid>.qt.
  digital_output` for every qubit whose trigger port allocated. The
  customer's own `build_quam_wiring_qdac.py` warns that `machine.wiring`'s
  `setdefault` returns detached copies, so a nested post-assignment write
  silently drops the leaf — a live write here could not be trusted to
  land, so it's a file patch like the state one.

`run_build`'s `qdac_qubits` field in the returned result reports the
qubits that were actually wired (from `qdac_pins`) when the trigger
allocation succeeded, falling back to the requested set from
`spec.qdac.qubits` so the wizard can still show *something* was attempted
even under full degrade.

## Wizard UI: TWPA-style, not a resource-pool integration

`generate.js`'s QDAC-II band lives in step 4 next to TWPAs: an instrument
address block (Ethernet IP:port or USB device) plus one row per qubit with
a checkbox and, when checked, a compact 8-field grid (channel, trigger
port, dwell, slew rate, output range/filter, settle time, DC offset) bound
directly onto `spec.qdac.qubits[qid]`. Deliberately no auto-allocated
resource pool and no step-5 topology-diagram integration — same
"simple, TWPA-style" scope as the TWPA band itself. `deriveLines()` omits
the `flux` line for a QDAC-biased qubit (mirrors the server-side "can't
have both" validation rule) so toggling the checkbox live-updates the line
list. `applyQubitIdMap` re-keys `spec.qdac.qubits` on rename, same as the
TWPA qubit-list re-keying. A stashed sidecar spec from an older session
that predates this feature gets defensively normalized on load
(`spec.qdac` absent or malformed → fresh defaults) since `freshSpec()`'s
merge only fills entirely-absent keys, not partially-shaped ones.

## Verified end-to-end on real hardware

`tests/test_generate_qdac_live_build.py` builds a two-qubit spec (q1
QDAC-biased, q2 a normal flux-tunable qubit — the mixed-architecture case
every real chip actually has) through `CQT_20Q` — quam 0.6.0 / quam_builder
0.4.0 / qualang_tools 0.23.0 with the customer's `quam_config` editable-
installed — and asserts every QDAC-related key lands with the right shape:
the top-level `state["qdac"]` instrument dict, q1's `z` as a `QdacBiasLine`
with every field round-tripped, q1's `opx_trigger_out` digital-marker
channel pointing at a real `#/wiring/qubits/q1/qt/digital_output` pointer
that resolves to an actual port, q2 keeping an ordinary flux line
untouched, and **zero** QDAC-related degrade warnings (a real attach
should need none). Per the user's explicit scoping, exact calibration
*values* don't need to match a real chip — only that every QDAC-related
*key* generates with the correct shape/type. Output always goes to a
pytest `tmp_path`, never into a real project's `quam_state` folder (this
repo's own doctrine: a generator subprocess must never write over
calibrated live data). Skips (not fails) on any machine without `CQT_20Q`.

`tests/test_generate_qdac.py` drives the wizard-side plumbing
(`generate_qdac_selfcheck.cjs`, node+jsdom) — spec.qdac normalization on
hydrate, checkbox toggle add/remove semantics, `deriveLines` omitting the
flux line for a biased qubit while keeping it for others on the same chip,
`applyQubitIdMap` re-keying on rename. Skips without node+jsdom.

## What's pinned

`tests/test_capabilities.py` (capability gating: required only when biased
qubits are declared, DEGRADE not BLOCKER, absent when unrequested),
`tests/test_config_generator.py::TestValidateSpecQdac` (12 cases — comm
type/port requirements, channel uniqueness, enum validation, the
QDAC+flux-line conflict both directions), `tests/golden/scripts_bundle_cz/
02_build_machine.py` (the emitted build-script recipe carries the same
`hasattr` guards as `run_build.py` — regenerated, not hand-edited).
