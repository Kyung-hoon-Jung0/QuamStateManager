# Task 9: `cli.py` -- Done

## What was built

`cli.py` -- a full command-line interface built with **Typer** (auto-help, type hints) + **Rich** (pretty tables, panels, colored output). Provides 10 commands covering every core module.

## Commands

### `show <name> [--section] [--folder]`

Display all properties of a qubit or qubit pair.

```
quam-manager show qA1                       # all properties
quam-manager show qA1 --section readout     # just readout-related
quam-manager show qA1-A2                    # pair info + CZ params
```

Sections: `frequency`, `coherence`, `xy`, `readout`, `flux`, `fidelity`. Auto-detects qubit vs pair by checking for `-` in the name.

### `table <properties...> [--folder]`

Show a comparison table of selected properties across all qubits.

```
quam-manager table f_01 T2ramsey gate_fidelity_avg
```

### `wiring [--folder]`

Show the full port wiring map (XY, RR, Z ports for each qubit).

### `search <query> [--limit] [--category] [--folder]`

Search all values and keys. Returns dot_path, value, and relevance score.

```
quam-manager search "7639"          # find readout frequencies
quam-manager search "qA1 readout"   # multi-term search
```

### `set <dot_path> <value> [--save] [--folder]`

Set a single value by dot-path. Supports `--save` flag to persist immediately.

```
quam-manager set qubits.qA1.f_01 6.3e9
quam-manager set qubits.qA1.T1 9000 --save
```

Value parsing: `null`/`none` -> None, `true`/`false` -> bool, integers, floats, or string.

### `save [--folder] [--output]`

Save the current state to disk with automatic backup.

### `diff <path_a> <path_b> [--tolerance] [--limit]`

Compare two quam_state folders. Shows a summary panel (added/removed/modified counts) and a detailed table.

```
quam-manager diff ./state_old/ ./state_new/ --tolerance 1e-6
```

### `export <output> [--folder] [--props]`

Export qubit summary as CSV or Markdown (auto-detected by file extension).

```
quam-manager export summary.csv
quam-manager export report.md --props f_01 T2ramsey
```

### `scan <folders...> [--date] [--name] [--limit]`

Scan folder trees for quam_state directories and list all experiments with metadata.

```
quam-manager scan ./data/project_name/ --date 2026-02-19 -n 20
```

### `trend <properties...> --folder <root> [--qubits] [--limit]`

Show how properties change across experiment snapshots (N-way multi-compare via CLI).

```
quam-manager trend f_01 T2ramsey --folder ./data/ --qubits qA1,qA2 -n 5
```

## Architecture

```
cli.py
  |
  +-- app (typer.Typer)        <-- entry point
  |     +-- show               <-- QueryEngine.get_qubit / get_pair
  |     +-- table              <-- QueryEngine.summary_table
  |     +-- wiring             <-- QueryEngine.get_wiring_map
  |     +-- search             <-- SearchIndex.build + search
  |     +-- set                <-- Modifier.set_value
  |     +-- save               <-- Saver.save
  |     +-- diff               <-- Differ.diff + summary
  |     +-- export             <-- Saver.export_csv / export_markdown
  |     +-- scan               <-- Workspace.add_root + get_flat_list
  |     +-- trend              <-- Workspace + Differ.multi_compare
  |
  +-- _parse_value()           <-- CLI string -> Python type
  +-- _format_cell()           <-- Python value -> Rich-safe display string
  +-- _load_store()            <-- QuamStore with error handling
```

## Files

| File | Lines | Purpose |
|------|-------|---------|
| `quam_state_manager/cli.py` | 490 | 10 Typer commands + helpers |
| `tests/test_cli.py` | 270 | 33 tests (all passing) |

## Test coverage

| Area | Tests | Verified |
|------|-------|----------|
| `_parse_value` | 5 | null/none, bool, int, float, string |
| `show` | 6 | qubit, section filter, pair, missing qubit, invalid section, invalid folder |
| `table` | 1 | renders with properties |
| `wiring` | 1 | renders port map |
| `search` | 3 | by value, by key, no results |
| `set` | 3 | success, invalid path, with --save (round-trip verified) |
| `diff` | 2 | identical, modified |
| `export` | 3 | CSV, Markdown, bad format |
| `scan` | 2 | empty folder, synthetic |
| Real data: show | 2 | qA1 (17-qubit), pair qA1-A2 |
| Real data: table | 1 | f_01 + T2ramsey across 17 qubits |
| Real data: search | 1 | "7639" finds resonator frequency |
| Real data: wiring | 1 | full 17-qubit wiring map |
| Real data: scan | 2 | 53 experiments, date filter |

## Project status

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.

## Notes for the next developer

- **Typer 0.24.0 required**: The installed version was 0.9.0 which has a compatibility issue with Click 8.2.1 (`make_metavar()` missing `ctx` arg). Upgraded to 0.24.0 during development.

- **Unicode safety**: All display strings use ASCII-safe characters (no em dashes or special Unicode). The Windows console with Korean locale (cp949) can't encode many Unicode characters, so we use `N/A` instead of `—` and standard ASCII everywhere.

- **`_parse_value`** converts CLI string arguments to Python types. The order is: None -> bool -> int -> float -> string. This means `"42"` becomes `int(42)` and `"6.25e9"` becomes `float(6.25e9)`. The modifier's type coercion then matches it to the original field's type.

- **Section filtering** in `show` only applies to qubits, not pairs. Pair data is always shown in full since it's smaller and structured differently.

- **Error handling**: invalid folders, missing qubits, bad paths, and type mismatches all produce `exit_code=1` with a helpful red error message. Invalid filter expressions and unknown search queries return `exit_code=0` with a yellow warning.

- The CLI uses `typer.testing.CliRunner` for tests, which captures Rich output as plain text (markup stripped). Tests verify content presence rather than exact formatting.
