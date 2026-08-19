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
