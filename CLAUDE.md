# CLAUDE.md

Guidance for Claude Code agents working in this repository. This file is loaded into every session, so it holds only
what applies to EVERY task. History and per-feature detail live in `docs/NNN_*.md`; the long campaign write-ups this
file used to carry (until 2026-11-14) are kept verbatim in `docs/claude_md_archive/` (see its `INDEX.md`).

## Project overview

**QUAM State Manager** (SM) is a desktop + web tool for inspecting and editing quantum machine (QUAM) state files. It
reads `state.json` + `wiring.json`, resolves QUAM JSON pointer references, and provides a CLI, a Flask/HTMX web UI and a
pywebview desktop app. It also generates fresh QUAM configurations (Generate Config wizard), re-generates existing chips
(Re-generate), previews `machine.generate_config()` (Config Viewer), keeps per-chip history, browses experiment runs, and
gives a terminal agent (Claude Code / Codex) a safe door into the chip (`/api/agent/*`, `quam_state_manager/mcp.py`).

## Commands

**Tests** (Windows conda env `qsm_official`, called directly, never via `conda run`, which hangs here; confirm env names
with `conda env list`, they churn). `PYTHONUTF8=1` is required (selfcheck drivers emit UTF-8; cp949 nulls their stdout).
The env's stdout carries a `qm - INFO - Starting session` line; filter it when parsing.
```bash
PYTHONUTF8=1 PYTHONPATH=. NODE_PATH=D:/work_laptop/statemanager-cfb/node_modules C:/ProgramData/miniconda3/envs/qsm_official/python.exe -m pytest tests/ -q --timeout=900 --timeout-method=thread
# single file:
PYTHONUTF8=1 PYTHONPATH=. NODE_PATH=D:/work_laptop/statemanager-cfb/node_modules C:/ProgramData/miniconda3/envs/qsm_official/python.exe -m pytest tests/test_pointer_resolver.py -v
```
- Baselines go stale: re-derive the pass/fail baseline in the env you actually use; never quote an old number as fact.
  The known OS-difference failure class (loopback/WSL, tmp-path case identity, Windows file-lock timing) is in
  `docs/87_always_ask_on_drift.md`. "Pre-existing failure" is a measurement on a clean HEAD, never an inference.
- Before a review rig (SM server + headless Chrome): `python C:/Users/khoon/.claude/review-kit/preflight.py --env <python> --serve <serve.py> --ports <sm>,<cdp>`.

**Dev server** (non-default port; serve from a Windows env, never WSL):
```bash
python -c "from quam_state_manager.web.app import create_app; create_app().run(debug=True, port=5050)"
```
**CLI:** `python -m quam_state_manager.cli show qA1 -f "path/to/quam_state/"` (`--version`, `--help`).
**Desktop:** `python -m quam_state_manager`.
**Build:** `pyinstaller build/quam-manager.spec` -> `dist/quam-manager/quam-manager.exe` (onedir on purpose: instant cold start).

## Architecture

```
state.json + wiring.json
    -> safe_io.read_state_wiring (FILE_SHARE_DELETE + mtime-bracketed pair read)
        -> loader.py (QuamStore: merged dicts, change log, search index, per-instance pointer cache, RLock)
            -> pointer_resolver.py (#/abs, #../parent, #./self; resolved on read, never in place)
            -> search_index.py (prefix map + trigram)    -> query.py (flatten to qubit/pair dicts)
            -> modifier.py (type-coerced edits, undo, rollback) -> saver.py (atomic writes, .bak rotation)
            -> differ.py (2-way / N-way)  -> history.py (snapshots + SQLite index)
            -> config_view.py / config_generator.py (QM-stack subprocesses)
```
- Pointer cache is per `QuamStore`; unresolvable pointers return the raw string + a warning (real data dangles).
- The app works on a per-chip **working copy** under `instance/working_state/` (`core/working_copy.py`); the live files are
  read only on an explicit sync and written only on an explicit apply (or an armed session, see Contracts).
- `QuamStore` instances are LRU-cached; experiment folders are discovered lazily (`core/scanner.py`).
- Web UI: Flask routes in `web/routes.py` return full pages or `_*.html` partials for `hx-swap`; HTMX + vanilla JS, no
  framework. Page-specific JS ships as a page bundle (`window.Bundles`), never in the core scripts.
- `app.config["contexts"]` maps names -> context dicts (`type: "quam"`); routes use `_store()`, `_engine()`, `_modifier()`.
- History: one per-chip change ledger (`core/hub_*.py`) is the read source for every "value's past" surface;
  `HistoryManager` snapshots under `instance/history/<chip>/` remain the capture store.
- Heavy QM libraries (`quam`, `quam_builder`, `qm`, `qualang_tools`) are never imported by the SM process; Generate,
  Re-generate, Config Viewer, capability probes and lab-code workers run in a user-selected conda env as subprocesses.
- `modifier.py` casts to the original field's type but never validates ranges: trust researcher input.

## Contracts that always apply

1. **Live-file I/O goes through `core/safe_io.py`** (share-delete reads, `ReplaceFileW` writes, retries). A plain
   `open()` on Windows blocks an experiment's atomic save. -> `docs/28_conflict_safe_io.md`
2. **The live-write covenant:** a direct live write happens only on an explicit Apply press, inside a user-armed
   Auto-Sync session, or as the undo/redo of a change that reached the chip through one of those.
   `/state/apply-to-live` is the one live writer. -> `docs/117_auto_apply.md`, `docs/160_undo_writes_live.md`
3. **Current sync rules** (pull/push collisions, merge on a moved chip, disarm reasons, undo vs in-flight push) are in
   `docs/311_walk_campaign_sync_contracts.md`; it supersedes older sync docs (incl. docs/187's failure budget).
4. **SM never swaps what you are looking at:** no silent pull over what the user sees from a user-facing path; robots
   (scheduler hook, autofit) may adopt. Pull/push never resets the view: only on-screen values update, in place.
   -> `docs/87_always_ask_on_drift.md`, `docs/144_view_never_resets.md`
5. **Pull and push are not symmetric:** a pull discards nothing on disk, a push discards live content; "Keep mine"
   needs a preflight naming what disappears. One direction vocabulary on every surface.
   -> `docs/86_overwrite_live.md`, `docs/97_drift_popup_ux.md`, `docs/209_sync_fixes_and_one_control.md`
6. **State roundtrip:** content loaded wholesale into the working copy has no change-log entries; `/state/sync` must
   never pull-first over it (`ctx["staged_base"]` marks staged+edited). -> `docs/65_state_roundtrip.md`
7. **A press means what the presser could see:** live-write doors compare the presser's declared `seen_changes`
   against the log and refuse unseen edits (409). -> `docs/120_customer_feedback_campaign.md`, `docs/179_unseen_gate_doors.md`
8. **Ctrl+Z crosses the save boundary and, with "Ctrl+Z writes live" ON, writes the chip;** journal steps are
   all-or-nothing with rollback. -> `docs/107_undo_journal.md`, `docs/160_undo_writes_live.md`
9. **The UI kit is the one design source:** only `ui-kit.css` tokens, `_ui.html` / `ui.js` parts; no new colours, button
   styles, dialog shells or inline styles. `tests/test_ui_contract.py` enforces it with ratchets that only go down.
   -> `docs/308_ui_kit.md`
10. **One implementation per concept:** before/after deltas via `core/value_delta.py` / `window.ValueDelta`
    (`docs/76_value_delta.md`); equality via `differ.compare_equal` (`docs/118_audit_two_chips.md`); search grammar via
    `search-query.js` / `core/search_query.py` (`docs/96_search_grammar.md`); Plotly resize via `window.PlotHost`
    (`docs/122_four_reports_phase0.md`).
11. **Honest by construction:** a missing value renders blank or an honest line naming its source leaf, never an
    invented number, a fabricated 0, or a silently dropped column. -> `docs/94_gridcap_and_schema_heal.md`,
    `docs/148_fidelity_split.md`, `docs/109_physical_units.md`
12. **FSP edits:** every FSP door commits the FSP at once; amplitudes change only on the user's Yes, in the same undo
    group. -> archive `docs/claude_md_archive/150_editing-a-port-s-full-scale-power-dbm-ke.md` (no separate doc)
13. **Production guards:** Host allowlist (403 before any route), no spawn of an interpreter SM did not discover or the
    user did not choose, every JSON body through `web/request_body.py`, htmx `historyCacheSize = 0`.
    -> `docs/303_v130_production_campaign.md`
14. **QUAlibrate config files are read-only to SM** (qualibrate's own write is non-atomic). A project opens only with
    a chosen env. -> `docs/55_qualibrate_projects.md`, `docs/229_cust_0930_alias_ops_project_env.md`
15. **The agent never emits a calibration number that reaches state unverified;** nodes run only through `run_node`
    after a person started the plan (rule 0); `apply_to_live` refuses a human's unseen edit.
    -> `docs/47_ai_analysis.md`, `docs/78_runner_agent.md`, `docs/172_agent_cockpit.md`, `docs/173_sm1_final_plan_ux.md`
16. **Every store mutation is recorded through `store_revs.note(...)`;** what counts as a waiting user is one rule,
    `activity.is_foreground(path)` over one exemption list. -> `docs/224_w7_integration.md`
17. **Evidence stays outside the repository:** mutation runners use `SM_EVIDENCE_DIR` or system temp and refuse repo
    destinations. -> `docs/311_walk_campaign_sync_contracts.md`
18. **Never save a quam machine without an explicit path, and never against the live customer state;** use a copy.

## Test and harness rules

- **`.cjs` selfchecks need node + jsdom, and SKIP (looking green) without them.** Run `npm install` (jsdom + canvas)
  and set `NODE_PATH` before trusting a clean run. `npm run selfcheck` reports `(N assertions)`, `(silent style)`
  (legit) or `ok? ... (exit 0 but NO output)` (cannot tell nothing-asserted from passing).
- **Judge a harness by its exit code, never by `tail -1`:** a hard Node crash ends with `Node.js vNN`. Likewise
  `pytest ... | tail && git commit` masks pytest's exit code.
- **jsdom realm rules:** harnesses hand Node individual globals, not a real script realm. A guard that checks
  `window.X` while the code calls bare `X` throws instead of degrading: bridge every global the code reads bare.
  A Node-realm `window.fetch =` never reaches the realm's bare `fetch`; a top-level app.js function is not a bare
  global in the Node realm. Install mocks and call in-realm via `window.eval`.
- **htmx under the CSP:** `unsafe-eval` is forbidden, so `hx-on::*` handlers and `hx-trigger` `[filter]`s are silently
  dead; use `data-after-request` + the delegated dispatcher and separate event names.
- **A pin must be able to fail:** mutation-check new pins; a GREEN under mutation usually means the fixture cannot
  reach the state (e.g. planner floors like `MIN_CELLS`/`MIN_COLD`, a hidden column, a scalar-only fixture).
  Pin the guard together with what it feeds; avoid fixed-character-window greps. -> `docs/141_night_session_manual_speed.md`
- **Do not trust a settle by wall clock** in jsdom harnesses; wait on a condition.
- Tests use `tmp_path` fixtures; real-data probes auto-skip when their path is absent. `tests/conftest.py` points
  `QUALIBRATE_CONFIG_FILE` at a nonexistent path and sets `SM_DISABLE_ENV_WARMUP=1` / `SM_DISABLE_HISTORY_VERIFY=1`.
  TOML paths in fixtures are written `as_posix()`.
- `tests/test_no_undefined_names.py` runs pyflakes over the package.
- Stress tests run real user journeys in real Chrome on a copy of a real chip (`tests/browser/journeys/`), checking
  screen, server peek, working copy and live files. -> `docs/205_qa_journeys_campaign.md`

## Where to read before touching X

| Area | Read first |
|---|---|
| Architecture overview | `docs/00_overview.md` |
| Working copy, live files, conflict-safe I/O | `docs/28_conflict_safe_io.md`, `docs/311_walk_campaign_sync_contracts.md` |
| Drift, pull/push, Keep mine / Take live | `docs/87_always_ask_on_drift.md`, `docs/86_overwrite_live.md`, `docs/97_drift_popup_ux.md`, `docs/209_sync_fixes_and_one_control.md` |
| Auto-Sync / auto-apply, merge on a moved chip | `docs/117_auto_apply.md`, `docs/187_autosync_merge.md` |
| Undo / redo, journal, list cells, undo trail | `docs/107_undo_journal.md`, `docs/160_undo_writes_live.md`, `docs/159_undo_list_cells.md`, `docs/141_night_session_manual_speed.md` |
| Unseen-edit gate, multi-window | `docs/120_customer_feedback_campaign.md`, `docs/179_unseen_gate_doors.md`, `docs/80_multi_instance.md` |
| Missing-leaf edits, typed editing, type fixes | `docs/88_missing_leaf_edit.md`, `docs/56_dynamic_typed_edit.md`, `docs/77_type_autofix.md`, `docs/79_env_type_baseline.md`, `docs/81_extras_free_form.md` |
| Sidebar tree, scale, aging, lazy loading | `docs/68_sidebar_tree_v2.md`, `docs/142_lazy_scale.md`, `docs/143_aging_audit.md`, `docs/155_why_every_menu_is_slow.md` |
| Sidebar IA, rows, folding, tools | `docs/FB-09_sidebar_ia.md`, `docs/157_sidebar_run_rows.md`, `docs/236_sidebar_chip_rows_fold.md`, `docs/89_sidebar_tools.md`, `docs/156_calc_window.md` |
| Live State Edit grids, virtualization | `docs/85_livegrid_show_all.md`, `docs/111_grid_editing.md`, `docs/141_night_session_manual_speed.md`, `docs/105_tier2_scale.md`, `docs/213_live_edit_json_tree_in_ram.md` |
| Json Tree edit, find/replace, multi-select | `docs/145_tree_edit_literal.md`, `docs/234_235_keyboard_select_and_tree_multi_edit.md` |
| Diff workbench, Compare | `docs/84_diff_workbench.md`, `docs/147_diff_figures_direction.md`, `docs/49_compare_hub_redesign.md`, `docs/128_version_diff_and_nway_compare.md` |
| Search grammar, typeahead | `docs/96_search_grammar.md`, `docs/175_search_ladder_and_agent_strip.md` |
| Chip Status, topology, Overview tiles | `docs/92_topology_as_picture.md`, `docs/148_fidelity_split.md`, `docs/150_overview_custom.md`, `docs/220_chip_status_meta_info.md`, `docs/227_w9_followups.md` |
| 2Q RB levels, derived fidelity | archive `docs/claude_md_archive/077_docs138.md`, `docs/207_chip_status_rb_cache.md` |
| Param History, ledger, Trends | `docs/20_param_history.md`, `docs/83_all_parameters_history.md`, `docs/302_s10_one_history_store.md`, `docs/301_delivery_walk_v1_1_0.md`, `docs/208_trends_irb.md`, `docs/214_chip_status_trends_from_ram.md` |
| Versions, State History | `docs/41_state_history.md`, archive blocks 074-075 (`docs/claude_md_archive/INDEX.md`), `docs/128_version_diff_and_nway_compare.md` |
| Datasets, polling, run store cache | `docs/80_multi_instance.md`, `docs/170_datasets_refresh_and_new_chip.md`, `docs/171_dataset_store_cache.md`, `docs/210_ram_p0_p3.md`, `docs/215_datasets_new_run_param_history.md`, `docs/221_datasets_scroll_exactness.md` |
| ndview, Interactive, click contracts | `docs/67_raw_netcdf_ndview.md`, `docs/82_repeat_axes.md`, `docs/48_ndview_click_contracts.md`, `docs/305_generic_interactive_map_tools.md` |
| Pulses page, lab pulse classes, Z-line | `docs/40_pulses_page.md`, `docs/189_lab_pulse_class_plots.md`, `docs/217_pulses_in_ram_shape_discovery.md`, `docs/218_lab_pulse_classes_and_new_pulse.md`, `docs/219_zline_distortion.md`, `docs/228_labwarm_project_env.md` |
| Generate Config wizard, wiring diagram | `docs/27_config_generator.md`, `docs/53_generate_feedback_r3.md`, archive blocks 173-174 |
| QDAC-II, bias tee | `docs/119_qdac2_bias_wizard.md`, archive blocks 175-176 and `docs/claude_md_archive/076_docs137.md` |
| Root class, Re-generate, renames | `docs/176_generate_root_class.md`, `docs/51_regenerate_config.md`, `docs/72_regen_adaptive_fidelity.md`, `docs/202_class_substitution.md`, `docs/295_regen_rename_keeps_the_qubit.md`, `docs/296_rename_keeps_the_history.md` |
| Env capabilities, modern stack | `docs/52_env_capabilities.md`, `docs/53_qop37_alignment.md` |
| Config Viewer | `docs/30_config_viewer.md` |
| QUAlibrate projects, envs | `docs/55_qualibrate_projects.md`, `docs/63_project_centric_reorg.md`, `docs/229_cust_0930_alias_ops_project_env.md`, `docs/206_workbench_csp.md` |
| Agent cockpit, chat, MCP, setup | `docs/172_agent_cockpit.md`, `docs/173_sm1_final_plan_ux.md`, `docs/297_agent_clear_conversation.md`, `docs/223_agents_menu_qa.md` |
| Autofit / runner / AI judge | `docs/56_autofit_scheduler.md`, `docs/78_runner_agent.md` (read its section 17 first), `docs/169_claude_code_judge.md`, `docs/127_autofit_cqt_corpus.md` |
| RAM caches, live write speed, big-state rig | `docs/211_big_state_qa_rig.md`, `docs/212_live_write_in_ram.md`, `docs/216_cold_open_chip_switch.md`, `docs/225_w8_followups.md` |
| Top bar layout, loader, reload | `docs/203_topbar_twerk.md`, `docs/146_loader_and_markers.md`, `docs/222_small_ui_queue.md`, `docs/237_topbar_reload.md` |
| Instance folder location | `docs/232_instance_folder_env_move.md` |
| Production / pre-deploy verification | `docs/303_v130_production_campaign.md`, `docs/304_v130_predeploy_verification.md` |
| Anything else | `docs/claude_md_archive/INDEX.md` (every former CLAUDE.md paragraph, verbatim) |

## Key files

| File | Purpose |
|---|---|
| `quam_state_manager/core/safe_io.py` | Conflict-safe live-file I/O (share-delete reads, `ReplaceFileW` writes with an `os.replace` fallback, pair reads) |
| `quam_state_manager/core/working_copy.py` | Per-chip working copy; sync/apply between it and the live files |
| `quam_state_manager/core/pointer_resolver.py` | Resolves QUAM JSON pointers; cache per `QuamStore` |
| `quam_state_manager/core/loader.py` | `QuamStore` |
| `quam_state_manager/core/modifier.py` / `saver.py` | Edits with undo + rollback / atomic writes, `.bak` rotation, export |
| `quam_state_manager/core/query.py` | Flattens nested JSON into qubit/pair property dicts |
| `quam_state_manager/core/history.py`, `core/hub_*.py` | Snapshots + SQLite index; the change ledger |
| `quam_state_manager/core/undo_journal.py` | Undo across the save boundary |
| `quam_state_manager/core/dataset.py` | `DatasetStore`: experiment run discovery + index |
| `quam_state_manager/core/qdac.py` | The one place SM decides what a QDAC-II bias line is |
| `quam_state_manager/core/pulse_catalog.py`, `pulse_index.py` | Pulse-class registry; pulse enumeration + reverse pointers |
| `quam_state_manager/core/config_generator.py`, `regenerate.py` | Generate / Re-generate drivers (subprocess) |
| `quam_state_manager/core/capabilities.py` | Env-capability model (blocker / degrade / ok) |
| `quam_state_manager/web/routes.py` | Flask/HTMX routes |
| `quam_state_manager/web/static/app.js`, `ui-kit.css`, `grid-virt.js` | Core client JS; the UI kit; shared grid virtualization |
| `quam_state_manager/web/agent_api.py`, `quam_state_manager/mcp.py` | Agent JSON API; stdio MCP bridge (thin client of a running window) |
| `quam_state_manager/generator/` | Scripts that run inside the user's QM env |

## Dependencies

Core: Flask, Jinja2, Typer, Rich. Desktop: pywebview. Data: h5py, numpy + scipy (waveform synthesis, bit-exact with
qualang_tools). Frontend: HTMX, Pico CSS, Split.js, Plotly.js, all bundled in `web/static/` (no CDN). The QM stack lives
only in the user-selected conda env.

## Keeping this file small

- New campaign knowledge goes to its own `docs/NNN_<topic>.md`. CLAUDE.md gets at most ONE pointer line per doc (in
  "Where to read" or as a contract line), never the write-up.
- Only a rule that applies to every task (a data-safety contract, a harness pitfall, a command) belongs here, in one or
  two lines with its doc pointer.
- Byte budget: 30,000. `tests/test_claude_md_budget.py` fails above it and fails when a named docs path does
  not exist.
