# v1.3.0 display fixes: validation

Worktree: `D:/work/sm-v130-cx1`; branch: `feat/v130-cx1`.

The 11 interrupted files contained only NUL bytes. Their committed contents were recovered before applying the fixes. No readable partial changes survived the restart.

| Item | Change | Pin | Mutation result |
| --- | --- | --- | --- |
| A6 | Escaped writer labels gain break opportunities after underscores, hyphens and spaces; the column disables forced word breaks. | `test_writer_breaks_only_at_separators_and_escapes_html`; CSSOM assertions in `v130_display_selfcheck.cjs`. | Both rendering and CSS mutations failed by assertion. |
| A7 | Both grids format min/max with `ValueDelta.formatValue`, which uses the existing compact magnitude rule. Editable values keep every digit. | `grid_stats_selfcheck.cjs`, including `5.50925e-06`; Python/JS value parity. | Replacing either grid's formatter with its old grouping formatter failed its assertions. |
| A8 | A flagged first-state card says: "The chip's first recorded state: 5,352 values. They are its starting values, not changes, so they are not listed here." Its badge says "starting values". Later uncertain events keep their existing explanation. | `test_first_state_card_is_consistent`, rendering the card macro through a Flask test client. | Restoring the later-event sentence for the first event failed the exact-text assertion. |
| A12 | Diff rows distinguish stored `null` from `(absent)` by the change operation. This covers history details, version vs current, legacy diff and the two-version workbench list. Python has an explicit absence sentinel; JS uses `undefined`. | Three parametrized diff renders; `test_two_recorded_versions_distinguish_null_from_absence` through the real ledger routes; `test_js_value_display_matches_python_including_null_and_absence`. | Python/JS null and absence mutations plus the diff render mutations failed by assertion. |
| A13 | The idle note has a readable local-time fallback and carries its timestamp through `ts_local` for the display zone and offset. The search found no other raw-ISO interpolation in value-history notes. | `test_idle_note_has_a_localizable_time_and_plain_text_fallback`; the rendered note is localized under Node to `2026-09-15 17:22:35 (UTC+9)`. | Reverting either the plain-text formatting or the timestamp markup failed by assertion. |
| A15 | The backup chip uses a 1px solid border in its text color. Both drawer charts let Plotly expand the y-axis margin. | CSSOM and recorded Plotly layouts in `v130_display_selfcheck.cjs`. | Reverting the border or either chart's automargin failed by assertion. |

No browser rig, server, Chrome session, screenshots or visual measurements were made. The coordinator checks the screens. The full suite was not run.

## Existing tests changed

- `tests/grid_stats_selfcheck.cjs`: loads the actual shared formatter; its header expectations now use the compact representation. The one-line `v1.3.0: raw header numbers -> compact values` comment explains the change. Cell, undo, Escape, Reset and refusal assertions remain.
- `tests/test_value_delta.py`: adds value-display/null/absence parity and description pins, with a one-line `v1.3.0: blank null -> null and (absent)` comment. Existing assertions are unchanged.
- `tests/value_delta_parity.cjs`: adds a value-display mode with a one-line `v1.3.0: blank null -> explicit null/absence` comment. Existing computation and percent modes are unchanged.

- `tests/test_field_history.py`: its run-name assertion ignores only `<wbr>` markers and keeps the same visible-name expectation, with a one-line `v1.3.0` explanation.
- `tests/test_hub_drawer.py`: the strict writer-label parser accepts `<wbr>` and strips those markers from visible text. Existing provenance and value assertions remain unchanged; the two helper changes carry one-line `v1.3.0` explanations.

## Mutation checks

Every mutation was applied alone, run against its pin, and restored in a `finally` block. All 23 negative runs failed on assertions rather than import, syntax or environment errors.

| Mutation | Result |
| --- | --- |
| A6 separators | Assertion failure; restored. |
| A6 whole words | Assertion failure; restored. |
| A7 qubit header | Assertion failure; restored. |
| A7 pair header | Assertion failure; restored. |
| A8 first-state words | Assertion failure; restored. |
| A12 Python null | Assertion failure; restored. |
| A12 Python absence | Assertion failure; restored. |
| A12 JS null | Assertion failure; restored. |
| A12 JS absence | Assertion failure; restored. |
| A12 history added | Assertion failure; restored. |
| A12 history removed | Assertion failure; restored. |
| A12 version added | Assertion failure; restored. |
| A12 version removed | Assertion failure; restored. |
| A12 legacy diff | Assertion failure; restored. |
| A12 two versions | Assertion failure; restored. |
| A13 plain note | Assertion failure; restored. |
| A13 rendered note | Assertion failure; restored. |
| A15 chip border | Assertion failure; restored. |
| A15 value chart | Assertion failure; restored. |
| A15 parameter chart | Assertion failure; restored. |
| A8 first-state badge | Assertion failure; restored. |
| A12 absence in old description | Assertion failure; restored. |
| A12 absence in new description | Assertion failure; restored. |

## Test results

Initial focused verification: `tests/test_v130_display.py`, `tests/test_grid_stats.py`, `tests/test_value_delta.py`: 85 passed, 1 skipped. This includes the real two-version route pin.

Initial affected-test run (82 files): 2608 passed, 22 skipped, 27 failures in old writer-label parsers. The new `<wbr>` markup caused those failures; the parser and visible-name checks were updated to preserve their existing expectations. All 27 failures were resolved. The companion rerun (30 files, including all four affected files and the focused pins) passed: 638 passed, 13 skipped in 382.06 s.

Commands use `PYTHONUTF8=1`, `NODE_PATH=D:/work/statemanager/node_modules`, `conda run -n cqt python -m pytest`, `-q -p no:cacheprovider --timeout=900 --timeout-method=thread`. The RAM gate passed before heavy runs. One pytest process ran at a time.

The affected test files are:

- `tests/test_agent_api_p3.py`
- `tests/test_agent_guardrails.py`
- `tests/test_agent_pill.py`
- `tests/test_aging.py`
- `tests/test_alignment_job.py`
- `tests/test_all_values_route.py`
- `tests/test_bulk_edit.py`
- `tests/test_bulk_pairs_picker.py`
- `tests/test_bulk_virt_server.py`
- `tests/test_calibration_age_ledger.py`
- `tests/test_calibration_log_big_day.py`
- `tests/test_calibration_log_hub.py`
- `tests/test_chip_apply_guard.py`
- `tests/test_chip_metric_meta.py`
- `tests/test_column_history.py`
- `tests/test_diff_panes.py`
- `tests/test_diff_tree_client.py`
- `tests/test_diff_workbench.py`
- `tests/test_display_timezone.py`
- `tests/test_escape_versions_panel.py`
- `tests/test_field_history.py`
- `tests/test_field_history_show_all.py`
- `tests/test_fingerprint_sidecar.py`
- `tests/test_flat_table_sync.py`
- `tests/test_grid_stats.py`
- `tests/test_history.py`
- `tests/test_history_drawer.py`
- `tests/test_history_index_lock.py`
- `tests/test_history_revert.py`
- `tests/test_history_source_folder.py`
- `tests/test_hub_chip_status.py`
- `tests/test_hub_drawer.py`
- `tests/test_hub_incremental.py`
- `tests/test_hub_link_folder.py`
- `tests/test_hub_mode_switch.py`
- `tests/test_hub_no_roots_sync.py`
- `tests/test_hub_unreadable_is_not_preparing.py`
- `tests/test_hub_versions.py`
- `tests/test_hub_witness.py`
- `tests/test_journal_day_url.py`
- `tests/test_journal_page.py`
- `tests/test_journal_search.py`
- `tests/test_misc_ui.py`
- `tests/test_natural_order_rest.py`
- `tests/test_no_names_in_code.py`
- `tests/test_one_run_instant.py`
- `tests/test_param_history_changes.py`
- `tests/test_param_history_filter_ux.py`
- `tests/test_param_history_none.py`
- `tests/test_param_history_ram.py`
- `tests/test_predelivery_audit_fixes.py`
- `tests/test_preflight_diagnostics.py`
- `tests/test_rename_datasets.py`
- `tests/test_rename_history.py`
- `tests/test_rename_review.py`
- `tests/test_run_instant_callers.py`
- `tests/test_s10_walk_drawer_footer.py`
- `tests/test_s10_walk_history_lists.py`
- `tests/test_s10_walk_journal_unreadable.py`
- `tests/test_s10_walk_left_out_notes.py`
- `tests/test_s10_walk_r4_text.py`
- `tests/test_s10_walk_r4_ui.py`
- `tests/test_s10_walk_report_log_speed.py`
- `tests/test_s10_walk_round3_archived.py`
- `tests/test_s10_walk_round3_lists.py`
- `tests/test_s10_walk_round4.py`
- `tests/test_scheduler.py`
- `tests/test_sidebar_compare.py`
- `tests/test_state_history.py`
- `tests/test_state_versions.py`
- `tests/test_story.py`
- `tests/test_time_display.py`
- `tests/test_trends_irb.py`
- `tests/test_trends_provenance.py`
- `tests/test_trends_run_attr.py`
- `tests/test_ui_readability.py`
- `tests/test_undo_live.py`
- `tests/test_v130_display.py`
- `tests/test_value_delta.py`
- `tests/test_vh_equality_and_removal.py`
- `tests/test_web.py`
- `tests/test_zone_attribute_times.py`

Additional companion test files:

- `tests/test_band_live_verdict.py`
- `tests/test_bulk_arithmetic.py`
- `tests/test_bulk_dirty_columns.py`
- `tests/test_bulk_dyncols.py`
- `tests/test_bulk_header_compact.py`
- `tests/test_bulk_markup.py`
- `tests/test_bulk_virt.py`
- `tests/test_compare_sources.py`
- `tests/test_cust_0930_ui.py`
- `tests/test_diff_cache.py`
- `tests/test_diff_three_way.py`
- `tests/test_grid_editing.py`
- `tests/test_pair_bulk_edit.py`
- `tests/test_pair_columns.py`
- `tests/test_pair_detuning_unit.py`
- `tests/test_pair_gates_seed.py`
- `tests/test_pairs_bell_column.py`
- `tests/test_pairs_declared_not_built.py`
- `tests/test_rename_datasets_table.py`
- `tests/test_rename_lineage.py`
- `tests/test_saver.py`
- `tests/test_tree_multi.py`

Final parser-import and focused verification includes `tests/test_hub_folder_view.py` and `tests/test_s10_walk_observed_dedupe.py`, as well as the changed Python tests, the grid driver and the name guard. Result: 185 passed, 1 skipped in 116.36 s. All display pins and the name guard passed. There are no remaining test failures in the final affected-file reruns.

The final check launch initially hit a locked conda activation temporary file. No pytest cases ran in that launch. It was retried with TEMP/TMP inside this worktree.

Skipped checks use unavailable real/large fixtures, OS-specific assumptions, or the existing tray fixture that did not stage its edit. No new display pin skips.

Direct Node selfchecks after the final Python run:

- `tests/grid_stats_selfcheck.cjs: exit 0; all checks passed (25 assertions)`
- `tests/v130_display_selfcheck.cjs: exit 0; all checks passed`
- `tests/fh_chart_selfcheck.cjs: exit 0; all checks passed`
- `tests/field_history_show_all_selfcheck.cjs: exit 0; field_history_show_all_selfcheck: all 15 checks passed`
- `tests/version_diff_selfcheck.cjs: exit 0; ok - JT-19: a further Escape leaves it closed (no toggle back open)`
- `tests/ph_drawer_scroll_selfcheck.cjs: exit 0; all 3 checks passed`
- `tests/ui_readability_selfcheck.cjs: exit 0; ui_readability_selfcheck: all checks passed`
- `tests/bulk_arith_selfcheck.cjs: exit 0; all checks passed (121 assertions)`

The Python file coverage is 106 distinct test files. The full suite and browser screenshots remain for the coordinator.
