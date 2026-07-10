# Task 5: `core/query.py` -- Done

## What was built

`QueryEngine` -- a high-level query interface that sits on top of `QuamStore` and transforms the deeply nested JSON into flat, researcher-friendly dictionaries. It resolves pointers automatically, extracts human-readable property names, and provides filtering, comparison tables, wiring maps, and topology graphs.

## Methods

### `get_qubit(name) -> dict`

Returns a flat dict with ~30 resolved properties for one qubit:

| Key | Source | Example |
|-----|--------|---------|
| `f_01` | `qubits.{name}.f_01` | `6255526125.489` |
| `anharmonicity` | `qubits.{name}.anharmonicity` | `258250000.0` |
| `T1`, `T2ramsey`, `T2echo` | direct | `8834`, `1.42e-6`, `None` |
| `chi` | direct | `-5200000.0` |
| `x180_amplitude`, `x180_length`, `x180_alpha` | `xy.operations.x180_DragCosine.*` | `0.115`, `40`, `-1.75` |
| `x90_amplitude` | `xy.operations.x90_DragCosine.amplitude` | `0.057` |
| `readout_frequency` | `resonator.f_01` | `7639750000.0` |
| `readout_amplitude`, `readout_length`, `readout_threshold` | `resonator.operations.readout.*` | `0.042`, `1000`, `-0.00014` |
| `confusion_matrix` | `resonator.confusion_matrix` | `[[0.91, 0.09], [0.12, 0.88]]` |
| `z_joint_offset`, `z_flux_point` | `z.*` | `0.081`, `"joint"` |
| `gate_fidelity_avg`, `gate_fidelity_x180`, `gate_fidelity_x90` | `gate_fidelity.*` | `0.991`, `0.986`, `0.986` |
| `xy_RF_frequency` | `xy.RF_frequency` | `6255526125.489` |
| `freq_vs_flux_01_quad_term`, `phi0_current`, `phi0_voltage` | direct | `-1.12e11`, `4.85`, `0.097` |

All pointer values (`#/...`, `#../...`) are resolved to their concrete values. Self-refs (`#./`) are returned as-is.

### `get_pair(name) -> dict`

Returns a flat dict with ~20 properties per qubit pair, including both CZ gate types (unipolar and flattop) when present:

- `qubit_control`, `qubit_target`, `moving_qubit`
- `cz_unipolar_amplitude`, `cz_unipolar_length`, `cz_unipolar_phase_shift_*`
- `cz_flattop_amplitude`, `cz_flattop_flat_length`, `cz_flattop_smoothing_length`
- `cz_flattop_bell_fidelity`, `cz_flattop_standard_rb`, `cz_flattop_interleaved_rb`
- `coupler_decouple_offset`, `coupler_interaction_offset`
- `detuning`, `confusion`, `mutual_flux_bias`

### `get_port_for(qubit, channel) -> dict`

Resolves the port reference for a qubit's `"xy"`, `"rr"`, or `"z"` channel. Follows the `#/wiring/...` pointer to the actual port object.

### `list_qubits(filter_expr=None) -> list[dict]`

Returns all qubits as flat dicts, optionally filtered by a safe expression like `"f_01 > 6e9 and T2ramsey > 1e-6"`. The filter uses `ast.parse()` with a strict whitelist -- never `eval()`.

### `summary_table(properties) -> list[dict]`

Returns a list of dicts (one per qubit) with only the requested property columns, plus `"id"`. Used for comparison tables.

### `get_wiring_map() -> list[dict]`

Returns a list of dicts mapping each qubit to its XY, RR, and Z port assignments.

### `get_topology() -> dict`

Returns `{"nodes": [...], "edges": [...]}` for rendering the Chip Topology dashboard (HTML/SVG cards, heatmap grids, per-metric panels, pair summary).

**Node fields** (one per qubit):
- Identity: `id`, `chain`, `grid_location`
- Coherence: `T1`, `T2ramsey`, `T2echo`
- Frequencies: `f_01`, `f_12`, `anharmonicity`, `chi`, `readout_frequency`
- Fidelity: `gate_fidelity_avg`, `gate_fidelity_x180`, `gate_fidelity_x90`, `assignment_fidelity`, `ro_fidelity_g` (confusion_matrix[0][0]), `ro_fidelity_e` (confusion_matrix[1][1])
- Calibration: `x180_amplitude`, `x180_length`, `x180_alpha`, `x90_amplitude`, `saturation_amplitude`, `readout_amplitude`, `readout_length`, `readout_threshold`
- Flux: `z_flux_point`
- Ports: `xy_port`, `rr_port`, `z_port`

**Edge fields** (one per qubit pair):
- Identity: `pair_id`, `source`, `target`, `has_cz`
- Best fidelity: `cz_fidelity`, `best_gate` (name of gate with highest Bell fidelity)
- All gate fidelities: `gate_fidelities` -- list of `{gate, metric, Fidelity, Purity, ...}` dicts found by searching all macros for fidelity-like data
- Confusion matrix: `confusion_size` (N for NxN matrix), `confusion_diag` (diagonal values), `confusion_offdiag` (off-diagonal values for crosstalk analysis)

**Helper functions** (module-level):
- `_assignment_fidelity(confusion_matrix)` -- average of 2x2 diagonal
- `_cm_diag(confusion_matrix, idx)` -- single diagonal element
- `_extract_pair_gate_fidelities(macros)` -- searches all gate macros for fidelity metrics
- `_extract_pair_confusion_offdiag(confusion)` -- extracts off-diagonal values from NxN confusion matrix

### `get_instrument_wiring() -> dict`

Returns the instrument-level wiring configuration extracted from the wiring tree. Maps instrument controllers to their connected channels and port assignments, useful for debugging hardware connectivity.

## Safe filter evaluator

The `_eval_filter` function parses expressions using `ast.parse()` in `"eval"` mode and walks the AST with a strict whitelist:

- **Allowed**: comparisons (`>`, `<`, `>=`, `<=`, `==`, `!=`), boolean ops (`and`, `or`, `not`), numeric/string constants, property names from the qubit dict
- **Blocked**: function calls, attribute access, imports, subscripts, assignments -- anything not in the whitelist raises `ValueError` and the filter returns `True` (pass-through)
- `None` values always fail comparisons (safe default)

## Files

| File | Lines | Purpose |
|------|-------|---------|
| `quam_state_manager/core/query.py` | 806 | `QueryEngine` with all 7 methods + safe filter evaluator + topology helpers |
| `tests/test_query.py` | 350 | 49 tests (all passing) |

## Real data validation

- **17-qubit example dataset**: all 17 qubits loaded; a sample qubit's `f_01`, `anharmonicity`, `x180_amplitude`, `readout_amplitude`, and `gate_fidelity_avg` all round-trip exactly
- **Pair qA1-A2**: control=qA2, target=qA1; `cz_flattop_amplitude` and `coupler_decouple_offset` resolve correctly
- **Topology**: 16+ nodes across chains A/B/C, 20+ edges
- **Filtering**: `"f_01 > 6e9"` correctly selects only high-frequency qubits
- **3-qubit dataset**: 3 qubits, 2+ pairs, wiring map, topology all work

## How downstream modules will use this

- **`web/routes.py`**: `/qubit/<name>` calls `engine.get_qubit()`, `/pairs` calls `engine.get_pair()`, `/table` calls `engine.summary_table()`, `/wiring` calls `engine.get_wiring_map()` + `engine.get_topology()`
- **`differ.py`**: `multi_compare()` will use `get_qubit()` to extract specific properties across multiple stores for trend charts
- **`cli.py`**: `show qA1` calls `get_qubit()`, `table f_01 T2ramsey` calls `summary_table()`, `wiring` calls `get_wiring_map()`

## Project status

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.

## Notes for the next developer

- The property names in `get_qubit()` and `get_pair()` are the canonical keys used throughout the app (UI column names, filter expressions, CSV exports, trend chart axes). If you add a new property, add it here first.

- `get_qubit()` explicitly lists every extracted property rather than doing a generic walk. This is intentional: it produces clean, predictable keys (e.g. `"readout_amplitude"` instead of `"resonator.operations.readout.amplitude"`) and makes the code easy to audit. The downside is that new QUAM fields need manual addition.

- Pointer references in `qubit_control` and `qubit_target` (e.g. `"#/qubits/qA2"`) are extracted to just the qubit name (`"qA2"`) by splitting on `/`. This is simpler than full pointer resolution for these fields and produces the right result.

- The filter evaluator is deliberately strict. Unknown properties and syntax errors cause the filter to pass (return `True`) rather than raise -- this prevents the UI from breaking when a user types a partial expression mid-keystroke.
