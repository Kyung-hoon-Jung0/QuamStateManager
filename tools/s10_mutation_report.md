# S10 mutation tool report

Release base: `e61a20c3`. Changes are confined to `tools/`; the package, tests, docs and vendor files are unchanged.

All 506 mutations ran sequentially in isolated copies: **496/506 red by assertion**, **10 survived**.
All anchors match once. Twenty-three mutation anchors were re-pointed; none were retired. No mutation was weakened.

| Tool | Red by assertion |
| --- | --- |
| `mutate_archived_chip_build.py` | 37/38 |
| `mutate_hub_builder.py` | 36/36 |
| `mutate_hub_c7b.py` | 9/9 |
| `mutate_hub_chip_status.py` | 56/56 |
| `mutate_hub_drawer.py` | 53/60 |
| `mutate_hub_folder_view.py` | 13/13 |
| `mutate_hub_link_folder.py` | 73/74 |
| `mutate_hub_mode_switch.py` | 64/64 |
| `mutate_hub_no_roots.py` | 14/15 |
| `mutate_hub_query.py` | 39/39 |
| `mutate_hub_versions.py` | 87/87 |
| `mutate_s10_walk_round3_archived.py` | 15/15 |

All three fault tools ran:

| Tool | Result |
| --- | --- |
| `faults_hub_chip_status.py` | 7 scenarios completed |
| `faults_hub_folder_view.py` | 8/8 PASS verdicts |
| `faults_hub_versions.py` | 9 scenarios completed; shared notes captured after repair |

Fault injection tools do not contain source mutations, so an assertion-kill tally does not apply.

## Anchor repairs

| Tool | Mutation | Old -> new, why |
| --- | --- | --- |
| `mutate_hub_chip_status.py` | `in_force_points_never_undone` | indented points -> effective row builder, bypass undo decoration. |
| `mutate_hub_chip_status.py` | `building_words_lost` | run counter text -> progress formatter, suppress building words. |
| `mutate_hub_chip_status.py` | `nan_drawn_as_a_number` | adjacent presenter -> numeric helper body, retain nonfinite leak. |
| `mutate_hub_drawer.py` | `numeric_only_like_the_leaf_index` | point comprehension -> series presenter loop, drop text and boolean history. |
| `mutate_hub_drawer.py` | `p0_3_by_run_reads_todays_holder` | fold call -> saved and kept holder selection, read today instead of then. |
| `mutate_hub_drawer.py` | `p3_nan_by_run_changed` | cell closing brace -> expanded cell dict, retain NaN inequality. |
| `mutate_hub_link_folder.py` | `versions_archive` | notes call -> listing-aware notes call, discard archive origin. |
| `mutate_hub_mode_switch.py` | `no_runs_versions_fallback` | summary assignment -> retry-nested assignment, keep the fallback mutation valid. |
| `mutate_hub_mode_switch.py` | `drawer_count_not_refreshed` | literal count -> formatted count span, remove out-of-band refresh. |
| `mutate_hub_mode_switch.py` | `ledger_disk_usage_missing` | render arguments -> snapshot-note arguments, omit disk usage. |
| `mutate_hub_mode_switch.py` | `c5_appeared_for_every_change` | whole effective prefix -> anchor prefix, claim every change appeared. |
| `mutate_hub_no_roots.py` | `failed_slice_never_cleared` | slice reset -> transient-aware reset, retain stale failure. |
| `mutate_hub_query.py` | `exact_case` | shared lookup -> series lookup only, preserve exact holder semantics. |
| `mutate_hub_query.py` | `root_holder` | shared lookup -> series lookup only, preserve exact holder semantics. |
| `mutate_hub_versions.py` | `one_timeline_page_only` | timeline loop -> retry-nested loop, stop after one page. |
| `mutate_hub_versions.py` | `summary_cached_regardless_of_the_ledger` | inline token -> retry token key, ignore ledger token. |
| `mutate_hub_versions.py` | `summary_cached_regardless_of_the_snapshots` | inline token -> retry token key, ignore snapshot list. |
| `mutate_hub_versions.py` | `cached_rows_drawn_as_they_were` | older rows -> deduplicated older rows, freeze snapshot annotations. |
| `mutate_hub_versions.py` | `panel_notes_hidden` | notes include -> retry-aware include, hide panel notes. |
| `mutate_hub_versions.py` | `history_notes_hidden` | notes include -> retry-aware include, hide timeline notes. |
| `mutate_hub_versions.py` | `history_count_calls_older_recorded` | inline wording -> count macro call, mislabel older rows as recorded. |
| `mutate_hub_versions.py` | `drawer_count_calls_older_recorded` | inline wording -> count macro call, mislabel older rows as recorded. |
| `mutate_hub_versions.py` | `kept_snapshot_not_listed` | older rows -> deduplicated older rows, drop unattached kept bookmarks. |

Other harness repairs: C7b adds its sibling import path; the folder-link tool uses the selected interpreter and an explicit `--report` instead of writing release docs; the Versions fault extractor follows the shared notes partial. Its expected wording now describes the ledger renderer and legacy rows.

The new sequential runner checks source hashes, rejects unknown tools, reports surviving mutations as failed runs, and preserves copied test sources as text artifacts after each sweep. This prevents generated fixtures from contaminating the package/tool deletion pins.

## Surviving mutations

These assigned pins stayed green (exit 0). Their mutations remain intact. This records coverage findings, not proven release defects.

| Tool | Mutation | Pin that should catch it |
| --- | --- | --- |
| `mutate_archived_chip_build.py` | `progress_not_in_snapshots` | `tests/test_archived_chip_build.py -k build_runs_in_the_background` |
| `mutate_hub_drawer.py` | `alias_read_literally` | `tests/test_hub_drawer.py::TestAliases::test_an_alias_reads_its_holder_and_says_via` |
| `mutate_hub_drawer.py` | `before_via_never_marked` | `tests/test_hub_drawer.py::TestAliases::test_a_pointer_retargeted_mid_history_is_named_and_older_rows_are_marked` |
| `mutate_hub_drawer.py` | `element_unchanged_versions_kept` | `tests/test_hub_drawer.py::TestElementsAndTrends::test_an_element_of_a_long_array_changes_only_when_it_does` |
| `mutate_hub_drawer.py` | `p0_1_no_via_row_at_a_retarget` | `tests/test_hub_drawer.py::TestReviewMore::test_p0_1_after_a_return_trends_draws_each_holders_value_in_force` |
| `mutate_hub_drawer.py` | `p1_1_newest_hop_row_decides` | `tests/test_hub_drawer.py::TestReviewRound::test_p1_1_before_via_marks_only_rows_the_alias_did_not_name` |
| `mutate_hub_drawer.py` | `p3_before_via_only_by_opacity` | `tests/test_hub_drawer.py::TestReviewRound::test_p3_column_history_says_before_via_in_text` |
| `mutate_hub_drawer.py` | `p1_2_sm_copy_same_before_imported` | `tests/test_hub_drawer.py::TestReviewObserved::test_p1_2_an_sm_writes_own_snapshots_are_not_a_second_history` |
| `mutate_hub_link_folder.py` | `retained_note` | `tests/test_hub_link_folder.py::test_unlink_keeps_events_and_shows_no_folder_even_after_label_link` |
| `mutate_hub_no_roots.py` | `schema_step_not_under_one_lock` | `tests/test_hub_no_roots_sync.py -k two_windows` |

The Versions tool also reports three pin functions never turned red by any of its existing mutations:

- `test_show_all_drawer_discloses_the_newest_points_cap`
- `test_the_report_trends_read_the_ledger`
- `test_the_value_drawer_reaches_its_older_points`

## Verification

The first mode-switch run exposed invalid indentation in `no_runs_versions_fallback`. Its retry-nested anchor was repaired and re-run: one assertion failure in `test_modes_other_than_ledger_say_why[no_runs]`. The final tally merges that repair with the other 63 assertion kills.

The generated-source audit found no other invalid Python mutation. All declared pin targets exist. The saved final mutation logs contain no runtime or collection failures. The initial invalid run is excluded from the final kill tally.

Every mutation restores its isolated source bytes; the sequential runner verified the original release files after every tool. No tests were deleted, rewritten or weakened; no production names were deleted, so there is no new deletion-name grep or static test to add. Existing deletion pins were exercised by the mutation tools and the restored baseline below.

The C0 tripwire is absent from this release. No with-tripwire run applies.

| Baseline file | Result |
| --- | --- |
| `tests/test_archived_chip_build.py` | 19 passed, 1 warning in 50.56s (exit 0) |
| `tests/test_chip_metric_meta.py` | 10 passed, 1 warning in 12.95s (exit 0) |
| `tests/test_history_drawer.py` | 12 passed, 1 warning in 9.87s (exit 0) |
| `tests/test_hub_build.py` | 25 passed, 1 warning in 1.84s (exit 0) |
| `tests/test_hub_chip_status.py` | 54 passed, 1 warning in 24.57s (exit 0) |
| `tests/test_hub_drawer.py` | 44 passed, 1 warning in 21.36s (exit 0) |
| `tests/test_hub_folder_view.py` | 32 passed, 1 warning in 12.40s (exit 0) |
| `tests/test_hub_link_folder.py` | 47 passed, 1 warning in 17.82s (exit 0) |
| `tests/test_hub_mode_switch.py` | 100 passed, 1 warning in 32.98s (exit 0) |
| `tests/test_hub_no_roots_sync.py` | 16 passed, 1 warning in 16.60s (exit 0) |
| `tests/test_hub_query.py` | 34 passed, 1 warning in 2.29s (exit 0) |
| `tests/test_hub_store.py` | 7 passed, 1 warning in 1.36s (exit 0) |
| `tests/test_hub_unreadable_is_not_preparing.py` | 6 passed, 1 warning in 4.52s (exit 0) |
| `tests/test_hub_versions.py` | 58 passed, 1 warning in 27.36s (exit 0) |
| `tests/test_param_history_changes.py` | 18 passed, 1 warning in 9.61s (exit 0) |
| `tests/test_param_history_ram.py` | 16 passed, 1 warning in 13.33s (exit 0) |
| `tests/test_project_scope.py` | 62 passed, 1 warning in 24.37s (exit 0) |
| `tests/test_s10_c5_review.py` | 14 passed, 1 warning in 11.27s (exit 0) |
| `tests/test_s10_c6_snapshot_branches_gone.py` | 1 passed, 1 warning in 1.75s (exit 0) |
| `tests/test_s10_c7_snapshot_machinery_gone.py` | 1 passed, 1 warning in 42.60s (exit 0) |
| `tests/test_s10_chip_status_deleted.py` | 1 passed, 1 warning in 14.24s (exit 0) |
| `tests/test_s10_walk_round3_archived.py` | 6 passed, 1 warning in 12.64s (exit 0) |
| `tests/test_state_versions.py` | 89 passed, 1 warning in 34.99s (exit 0) |
| `tests/test_trends_irb.py` | 13 passed, 1 warning in 20.63s (exit 0) |
| `tests/test_trends_provenance.py` | 26 passed, 1 warning in 15.46s (exit 0) |
| `tests/test_no_names_in_code.py` | 7 passed, 1 warning in 23.29s (exit 0) |

Restored baselines: **718 tests passed across 26 files**, with no failures. The name guard ran after staging all new tool artifacts.

| Direct JS selfcheck | Result |
| --- | --- |
| `tests/vh_retry_selfcheck.cjs` | 17 checks passed (exit 0) |
| `tests/hub_chip_status_selfcheck.cjs` | 28 checks passed (exit 0) |
| `tests/hub_link_folder_selfcheck.cjs` | Passed (exit 0) |

The runner repeat check used a fresh numbered copy and passed all eight folder-view fault scenarios. Unknown or empty tool selections are rejected before copying. Input verification also detects missing copied files.

The existing fixture docstring emits a SyntaxWarning for an invalid escape; the retry JS check emits font fallback notices. Both remain unchanged and all checks passed.

## Notes for coordination

The estimated 14 stale anchors undercounted this tree: 22 were missing or ambiguous, and one additional matching anchor generated invalid indentation. All 23 are repaired without retiring behavior.

The ten surviving mutations and the three Versions coverage gaps remain findings because release code and tests are frozen. No application behavior was changed.

Full per-mutation names, files, pins, assertion verdicts and exit codes are in [s10_mutation_results.json](s10_mutation_results.json). Raw local reports and logs remain under `tools/.tmp_s10_mut/`; repaired fault notes and the fallback retry are under `tools/.tmp_s10_recheck/`. Copied fixture sources are preserved as text artifacts.
