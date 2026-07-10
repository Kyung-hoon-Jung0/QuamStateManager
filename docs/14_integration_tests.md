# 14 -- Integration Tests & Finalization

## What was done

### 1. Package rename: `quam_manager` -> `quam_state_manager`

The entire codebase was renamed to avoid naming conflicts with the upstream `quam` library:

- Renamed directory: `quam_manager/` -> `quam_state_manager/`
- Updated all Python imports across 12 source files, 12 test files, and the PyInstaller spec
- Updated all 14 documentation files
- Verified zero remaining references to the old name

### 2. End-to-end integration tests (`tests/test_integration.py`)

Created 22 integration tests that chain multiple modules together, exercising the full data pipeline:

| Test class | Count | What it covers |
|-----------|-------|----------------|
| `TestSyntheticPipeline` | 7 | Load -> search -> modify -> save -> diff on synthetic data. Also: undo, pointer resolution, search-after-modify, CSV export, wiring/topology, summary table |
| `TestRealPipeline` | 7 | Same pipeline on the 17-qubit real dataset. Also: saved state reloads correctly, pointers survive save roundtrip, CSV+Markdown export, search <5ms, full topology |
| `TestWorkspacePipeline` | 2 | Workspace scanning + multi-state trend compare across real experiment folders, LRU cache verification |
| `TestWebIntegration` | 6 | Flask end-to-end: load -> search -> detail, edit -> undo, CSV export, diff, table, wiring |

### Key pipeline flows tested

```
1. Load -> Search -> Modify -> Save -> Diff -> Verify
   QuamStore -> SearchIndex.build -> Modifier.set_value -> Saver.save -> Differ.diff -> assert changes match

2. Pointer Resolution Roundtrip
   QuamStore(original) -> resolve_pointer -> Saver.save -> QuamStore(saved) -> resolve_pointer -> assert equal

3. Search After Modify
   QuamStore -> SearchIndex -> store.search_index = idx -> Modifier.set_value -> idx.search -> assert updated

4. Workspace Multi-Compare
   Workspace.add_root -> load_store (x3) -> Differ.multi_compare -> assert trend data correct

5. Web Layer End-to-End
   Flask client -> POST /load -> GET /search -> GET /qubit/detail -> POST /edit -> POST /undo
```

## Final test counts

| File | Tests |
|------|-------|
| `test_pointer_resolver.py` | 21 |
| `test_loader.py` | 40 |
| `test_scanner.py` | 50 |
| `test_search_index.py` | 55 |
| `test_query.py` | 49 |
| `test_modifier.py` | 50 |
| `test_saver.py` | 37 |
| `test_differ.py` | 48 |
| `test_cli.py` | 33 |
| `test_web.py` | 195 |
| `test_main.py` | 14 |
| `test_experiment_data.py` | 11 |
| **`test_integration.py`** | **22** |
| **Total** | **625** |

## Project status

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.

**All planned TODO items (1-14) are complete.** The QUAM State Manager is a fully functional desktop application with:

- 13 Python modules (core engine, CLI, web dashboard, desktop launcher)
- 625 tests across 13 test files
- 15 documentation files (`00_overview.md` through `14_integration_tests.md`)
- PyInstaller packaging for single-folder `.exe` distribution
- Bundled offline assets (HTMX, Pico CSS, Plotly) -- no internet required
- Package name: `quam_state_manager` (avoids conflict with upstream `quam`)
