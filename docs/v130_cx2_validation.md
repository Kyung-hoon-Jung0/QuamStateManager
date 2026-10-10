# v1.3.0 A14 and A17 validation

Worktree: `sm-v130-cx2`, branch `feat/v130-cx2`.

A14 counts local calendar dates. Chip Status reuses `ChipStatus.metaInfo.when`,
which uses `SnapTime` and the page's project zone. The export passes its
project zone through the existing `_report_tz` helper; Python compares the
recorded instant and the report clock in that zone. Labels are `today`,
`yesterday`, or `N days ago`, including ages over 30 days.

The pins in `tests/test_v130_age_label.py` use 11 fixed clocks: previous evening
22:00 to next day 03:00, same day 01:00 to 23:00, 35 days, UTC, UTC+9, UTC-7,
and both DST transitions. Python report counts, Markdown/HTML words and CSV
numbers agree with real Chip Status tiles under Node/jsdom. A Flask export
pin uses a project zone different from the PC zone. The existing local-clock
fallback test passes unchanged. A14 mutations: **6/6 failed by assertion**.

A17 uses one `version_label` macro in all three listings. It offers an inline
input only for snapshot annotations, sends only `label` through the existing
route, and redraws the same listing with its current pagination. Enter saves,
Escape or Cancel resets and returns focus, empty clears, and both the input
and route enforce 80 characters. Labels remain HTML escaped.

Pins cover set/clear with pinned and unpinned snapshots in all three listings,
notes and pins retained, missing labels retained on Pin, 80/81 characters,
no control and HTTP 409 for a ledger version without a snapshot, and actual
Flask-rendered markup driven under Node for Enter/Escape/clear/cancel/focus.
A17 mutations: **18/18 failed by assertion**. Every mutation restored its
source file byte for byte; the unstaged code diff was empty afterward.

Final primary checks: **133 passed** across four files. Related checks:
**2785 passed, 3 failed, 0 errors,
20 skipped** across 77 files. The JSON records every
file's result. The related batch reruns the 26 new pins after the mutations.
Together these batches cover 80 distinct test files.

The one changed existing test is
`test_hub_mode_switch::test_plain_copies_of_one_state_carry_one_pin`:
one annotation URL used to mean one Pin; the row now has a Pin button and a
Label form using that door. It asserts one of each for the one retained
snapshot, with the required one-line v1.3.0 explanation. Its Label-removal
mutation failed by assertion. The complete modified file reran: **100 passed**.
After restoring all source files, the final pins and name guard reran:
**35 passed**.

Two unrelated field-history assertions remain failing on both the changed
code and the worktree's original `6feff55e` code:

- `test_archived_chip_build::test_the_build_reads_and_never_writes_the_live_folder`
  asserts a contiguous `saved in #7 scan` substring in raw HTML; the text is
  present after stripping markup.
- `test_hub_other_chip::test_a_nameless_chip_that_gained_a_qubit_keeps_its_own_runs`
  asserts old contiguous field-history wording `#32 (chip uncertain)`.

Both failed at the same assertions when all nine modified existing production
files were temporarily replaced with their original HEAD bytes in this same
worktree. Those files were restored byte for byte afterward. These two tests
were left unchanged because they do not pin A14 or A17. The first regression
batch's annotation-URL failure is resolved by the precise test update above;
the other two failures are recorded for the coordinator. The 20 skips and
their reasons are recorded in the checks JSON.

Commands used `PYTHONUTF8=1`, `NODE_PATH=D:/work/statemanager/node_modules`,
`conda run -n cqt python -m pytest`, `-q -p no:cacheprovider --timeout=900
--timeout-method=thread`. Node drivers run `v130_calendar_age_selfcheck.cjs`
and `v130_version_label_selfcheck.cjs` with generated fixed-clock/render
fixtures. Node syntax checks also passed. RAM was checked before every heavy
batch and mutation run; all readings allowed the step. Only one pytest
process ran at a time.

Only the existing test listed above changed. No browser rig, Chrome, screenshots, application
build or full suite ran. Browser appearance and the full suite belong to the
coordinator, as instructed. No screenshot paths exist for either item.

Results: [checks](v130_cx2_checks.json), [mutations](v130_cx2_mutations.json).

Test files run:

```text
tests/test_actor_plumbing.py
tests/test_adopt_stamp.py
tests/test_agent_panel.py
tests/test_aging.py
tests/test_archive.py
tests/test_archived_chip_build.py
tests/test_auto_sync.py
tests/test_autosync_merge.py
tests/test_autosync_stage_honesty.py
tests/test_bundles.py
tests/test_calibration_age_ledger.py
tests/test_chip_metric_meta.py
tests/test_chip_report_v2.py
tests/test_chip_status.py
tests/test_chip_status_history_ledger.py
tests/test_chip_status_layout.py
tests/test_column_history.py
tests/test_compare_hub_routes.py
tests/test_dataset_apply_to_chip.py
tests/test_diagnostics_ui.py
tests/test_display_timezone.py
tests/test_escape_versions_panel.py
tests/test_fq_sync_p3.py
tests/test_history.py
tests/test_history_drawer.py
tests/test_history_revert.py
tests/test_history_source_folder.py
tests/test_hub_building_fast.py
tests/test_hub_folder_view.py
tests/test_hub_link_folder.py
tests/test_hub_mode_switch.py
tests/test_hub_no_roots_sync.py
tests/test_hub_other_chip.py
tests/test_hub_record.py
tests/test_hub_versions.py
tests/test_identity_extras_on_load.py
tests/test_instance_move.py
tests/test_journal_page.py
tests/test_leaf_index.py
tests/test_live_drift.py
tests/test_live_patch.py
tests/test_live_sync_server.py
tests/test_no_names_in_code.py
tests/test_one_run_instant.py
tests/test_overwrite_live.py
tests/test_param_history_ram.py
tests/test_poll_stability.py
tests/test_predelivery_audit_fixes.py
tests/test_project_scope.py
tests/test_project_time.py
tests/test_project_time_without_projects.py
tests/test_qualibrate_routes.py
tests/test_rename_history.py
tests/test_rename_lineage.py
tests/test_report_card.py
tests/test_route_param_hardening.py
tests/test_s10_c6_snapshot_branches_gone.py
tests/test_s10_c7_snapshot_machinery_gone.py
tests/test_s10_walk_history_lists.py
tests/test_s10_walk_left_out_notes.py
tests/test_s10_walk_observed_dedupe.py
tests/test_s10_walk_r4_ui.py
tests/test_s10_walk_round3_archived.py
tests/test_s10_walk_round3_lists.py
tests/test_s10_walk_round4.py
tests/test_scheduler_queue.py
tests/test_spec_thresholds.py
tests/test_state_history.py
tests/test_state_roundtrip.py
tests/test_state_versions.py
tests/test_sync_audit_fixes.py
tests/test_sync_one_control.py
tests/test_time_display.py
tests/test_topology_hero.py
tests/test_trends_provenance.py
tests/test_undo_after_version_load.py
tests/test_undo_live.py
tests/test_v130_age_label.py
tests/test_web.py
tests/test_zone_attribute_times.py
```
