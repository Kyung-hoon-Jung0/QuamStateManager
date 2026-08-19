# docs/126 — Customer feedback round 2 (PJ_10082026 + topology/UX batch)

The second customer-feedback batch, received 2026-08-19. Scope confirmed with
the user before any code: ① the SUPER CRITICAL pair-grid search gap, ② the
Topology rework (metric patches drive the map), ③ Live-Edit patch bundle,
④ Json Tree bundle, ⑤ the apply-to-chip gate revision (user-directed covenant
amendment), ⑥ small UX items, ⑦ CosineBipolarPulse support + the Gaussian CZ
macro workflow. Reference chip: `D:\work\Customer_Codes\PJ_10082026\quam_state`
(READ-ONLY — a 20Q QDAC-biased tunable-coupler chip, 30 pairs).

## ① Pair grid: coupler/CR channel port-chain expansion (SUPER CRITICAL)

**Report.** "Typing `exponential` in Live Edit finds `exponential_filter`, but
the pair's flux line never shows up — the coupler side is unfindable. The Json
Tree finds it fine."

**Root cause.** `pair_columns._walk_pair` walked only the pair subtree's own
leaves; a channel's `opx_output` wiring POINTER stayed a raw pointer-string
column and the resolved port's leaves were never derived — unlike the qubit
grid, whose `qubit_columns._IO_KEYS` expansion has surfaced port leaves since
docs/94. On most chips that asymmetry was invisible (the same port is reachable
from some qubit's z), but this customer's chip is QDAC-biased (docs/119): 11 of
20 qubits have no OPX z at all and the COUPLER is the only entity wired to an
OPX flux port (`ports.analog_outputs.con1.5.*`). So the port filter leaves
(`exponential_filter`, `high_pass_filter`, `exponential_dc_gain`, …) existed on
NO grid, and the search — which docs/85 made whole-chip over the columns the
grid HAS — had nothing to find. Reproduced before the fix: qubit grid 1 column
(`dyn__z_opx_output_exponential_filter`, the 9 OPX-flux qubits), pair grid 0
port columns of 103.

**Fix.** `pair_columns` mirrors the qubit grid's expansion: `_IO_KEYS`
(`opx_output`/`opx_input`) pointers are not columns; `_port_leaves` resolves
the chain (`resolve_field_target` → port dict) and emits the port's scalar +
list leaves as `qubit_pairs.{pair}.<chan>.<io>.<leaf>` alias columns (labels
`out · <leaf>`), landing in the owning component's band. Everything downstream
already worked: cells build through the same `_build_bulk_cell` /
`_list_pair_cell` pipeline, list leaves open the shared ✎ JSON editor
(`BulkEdit.openJsonCell` has handled pair ▦ badges since r6 item 4), and the
pair search haystack is `label + key + section`, which now contains the leaf
names. Generic by construction — CR/ZZ channels' ports expand identically on
CR chips.

**Semantics kept:** all-null port leaves drop (same rule as every derived
column), nested port dicts (multi-DUC `upconverters`) never become columns,
a dangling wiring pointer yields no columns and no crash, and a mixed column
(7 pairs list-valued, 23 null on the real chip) renders per-pair — list rows
▦/✎, null rows scalar input — exactly as the qubit grid always has.

**Verified.** On the real chip: 9 port columns derived (111 total), path_map
addresses the alias with per-pair kind, and a full `/bulk` render carries all
9 in the pair-grid DOM (81 `coupler.opx_output.exponential_filter` cell
references). `test_pair_columns.py` (27) + `test_bulk_edit.py` (59) green;
`bulk_search_selfcheck.cjs` + `bulk_dyncols_selfcheck.cjs` green.

**Pinned by** `tests/test_pair_columns.py::TestCouplerPortChain` (port leaves
become columns; the pointer itself is not one; all-null dropped + nested dict
skipped; per-pair kind in path_map; dangling pointer degrades).

## ⑦a CosineBipolarPulse — catalog first-class + bit-exact preview

**Report (screenshot).** The PJ_10082026 chip's `cz_bipolar.flux_pulse_qubit`
rendered "Unrecognized pulse class `quam_builder…CosineBipolarPulse` — fields
are shown raw; the synthesized preview is unavailable."

**Why it was unrecognized.** quam_builder 0.4.0's `CosineBipolarPulse` is NOT
a renaming of the deprecated `_CosineBipolarPulse`/`SmoothedCosineBipolarPulse`
(different fields — explicit total `length`, no smoothing/padding — and a
different edge law: the non-flat remainder splits into rise/switch/fall
THIRDS). At docs/53 time it was deliberately left to the env-roster overlay —
the `env_creatable_specs` docstring literally used it as the example — and this
chip has no probed env attached, so nothing recognized 98 instances of it. Even
with a roster, `waveform_synth` had no transcription, so no preview either way.

**Fix.** Promoted to the static catalog (creatable, Flux/Bipolar; `qclass` IS
the quam_builder arch path — the class has no quam-era home, and docs/98
forbids writing a home no stack can import; `chip_qclass`'s majority-prefix
branch can never emit a legacy path for it since `_BY_QCLASS` holds only the
arch home) + `waveform_synth._cosine_bipolar` transcribing the env formula
exactly (halfcos rise → +flat → cosine switch → −flat → −halfcos fall; thirds
split with extra 1 → switch, extra 2 → rise+fall; even-flat and flat≤length
raises mirrored).

**Golden strategy.** The committed `waveform_golden.json` was generated on
quam 0.5.0a3 (LabC), which predates the class — regenerating it wholesale in a
modern env would break the deprecated-class cases in the other direction. So
generation-scoped goldens: `run_waveform_golden.py` gained
`--only-keys/--skip-keys/--out-name`, the new cases live in
`tests/golden/waveform_golden_qb04.json` (generated from the cqt env — quam
0.6.0 / quam_builder 0.4.0), the test fixture merges both files, and the
LabC live-regeneration test passes `--skip-keys CosineBipolarPulse`. Parity is
bit-exact (rtol 1e-9) over 7 cases including the customer chip's exact shape
(length 124 / flat 100 → 8/8/8 edges) and both raise paths.

**Ripples, all pinned.** The overlay/create tests that used CosineBipolarPulse
as *the* roster-only example now ride a synthetic `LabWigglePulse` (cloned
record at a lab home), and the promotion itself is pinned beside them:
`resolve_qclass` answers `exact` with no overlay, `env_creatable_specs(modern
roster) == {}`, the create page lists the class as a regular catalog entry
(env-verified, not env_only), and the two catalog-shape pins carry the one
deliberate qclass exception.

**Verified.** On the real chip: the screenshot's own pulse
(`qubit_pairs.q1-2.macros.cz_bipolar.flux_pulse_qubit`) resolves `exact` and
synthesizes 124 net-zero samples. 457 pulse-suite tests + 154 golden tests +
`pulses_create/commit` selfchecks green.
