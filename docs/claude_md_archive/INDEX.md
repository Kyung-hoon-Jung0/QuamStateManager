# CLAUDE.md archive

Every paragraph of the pre-2026-11-14 root CLAUDE.md that is not a bare heading, moved here verbatim (one file per paragraph). The slim CLAUDE.md points here. Read a file on demand; the full doc, when one exists, is in the last column.

| Block | Section | Title | Bytes | File | Full doc |
|---|---|---|---|---|---|
| 2 | CLAUDE.md | > **Note on this file's history**: this file previously accumulated long forensi | 444 | [002_note-on-this-file-s-history-this-file-pr.md](002_note-on-this-file-s-history-this-file-pr.md) | none |
| 4 | Project Overview | QUAM State Manager | 384 | [004_quam-state-manager.md](004_quam-state-manager.md) | none |
| 6 | Commands | Run tests | 2388 | [006_run-tests.md](006_run-tests.md) | none |
| 7 | Commands | The `.cjs` selfchecks need node + jsdom — and they SKIP, loudly-looking-green, without the | 813 | [007_the-cjs-selfchecks-need-node-jsdom-and-t.md](007_the-cjs-selfchecks-need-node-jsdom-and-t.md) | none |
| 8 | Commands | Run web dev server | 300 | [008_run-web-dev-server.md](008_run-web-dev-server.md) | none |
| 9 | Commands | Run CLI: | 174 | [009_run-cli.md](009_run-cli.md) | none |
| 11 | Commands | Build standalone executable: | 248 | [011_build-standalone-executable.md](011_build-standalone-executable.md) | none |
| 14 | Core Pipeline | ``` | 833 | [014_block.md](014_block.md) | none |
| 16 | QUAM Pointer System | QUAM files avoid value duplication via three pointer types: | 184 | [016_quam-files-avoid-value-duplication-via-t.md](016_quam-files-avoid-value-duplication-via-t.md) | none |
| 17 | QUAM Pointer System | `pointer_resolver.py` resolves these **on-read, never in-place**. The cache is * | 363 | [017_pointer-resolver-py-resolves-these-on-re.md](017_pointer-resolver-py-resolves-these-on-re.md) | none |
| 19 | QuamStore (Thread-Safe) | `core/loader.py` holds merged state+wiring dicts, change log, search index, and | 339 | [019_core-loader-py-holds-merged-state-wiring.md](019_core-loader-py-holds-merged-state-wiring.md) | none |
| 21 | Working-Copy / Live-File Safety | Current sync, configuration and measurement contracts (docs/311, 1.7.0). | 2594 | [021_docs311.md](021_docs311.md) | ../311_walk_campaign_sync_contracts.md |
| 22 | Working-Copy / Live-File Safety | Experiment programs read and write the same `state.json` + `wiring.json` the app | 788 | [022_experiment-programs-read-and-write-the-s.md](022_experiment-programs-read-and-write-the-s.md) | none |
| 23 | Working-Copy / Live-File Safety | State-roundtrip invariant (docs/65): | 516 | [023_docs65.md](023_docs65.md) | ../65_state_roundtrip.md |
| 24 | Working-Copy / Live-File Safety | SM never swaps what you are looking at (docs/87). | 845 | [024_docs87.md](024_docs87.md) | ../87_always_ask_on_drift.md |
| 25 | Working-Copy / Live-File Safety | A field the entity has not got yet (docs/88). | 445 | [025_docs88.md](025_docs88.md) | ../88_missing_leaf_edit.md |
| 26 | Working-Copy / Live-File Safety | The third choice when live drifted (docs/86). | 929 | [026_docs86.md](026_docs86.md) | ../86_overwrite_live.md |
| 27 | Working-Copy / Live-File Safety | The **load path is content-hash-aware**: each sync point records `synced_live_ha | 563 | [027_the-load-path-is-content-hash-aware-each.md](027_the-load-path-is-content-hash-aware-each.md) | none |
| 29 | Scanner + LRU Cache + Sidebar Tree v2 | `core/scanner.py` discovers experiment folders lazily. `QuamStore` instances are | 209 | [029_core-scanner-py-discovers-experiment-fol.md](029_core-scanner-py-discovers-experiment-fol.md) | none |
| 30 | Scanner + LRU Cache + Sidebar Tree v2 | **RAM + lazy loading at 5,000-run scale (docs/142, 2026-08-31 — customer: 5,000+ | 2925 | [030_docs142.md](030_docs142.md) | ../142_lazy_scale.md |
| 31 | Scanner + LRU Cache + Sidebar Tree v2 | The aging audit (docs/143, 2026-08-31 — months of accumulation, judged then fixed). | 2590 | [031_docs143.md](031_docs143.md) | ../143_aging_audit.md |
| 32 | Scanner + LRU Cache + Sidebar Tree v2 | Sidebar tree v2 (r13, docs/68): | 635 | [032_docs68.md](032_docs68.md) | ../68_sidebar_tree_v2.md |
| 34 | Web UI (HTMX) | Flask routes return either full pages or partial `_*.html` template fragments fo | 459 | [034_flask-routes-return-either-full-pages-or.md](034_flask-routes-return-either-full-pages-or.md) | none |
| 35 | Web UI (HTMX) | The UI kit is the one design source (docs/308). | 719 | [035_docs308.md](035_docs308.md) | ../308_ui_kit.md |
| 36 | Web UI (HTMX) | Clicking a Plotly data point in a dataset figure opens a confirmation popup (one | 214 | [036_docs36.md](036_docs36.md) | ../36_plot_click_confirm_popup.md |
| 37 | Web UI (HTMX) | Δ everywhere (docs/76). | 676 | [037_docs76.md](037_docs76.md) | ../76_value_delta.md |
| 38 | Web UI (HTMX) | The qubit/pair inspector panes carry a sticky in-panel search bar (`filterDetail | 181 | [038_the-qubit-pair-inspector-panes-carry-a-s.md](038_the-qubit-pair-inspector-panes-carry-a-s.md) | none |
| 39 | Web UI (HTMX) | The diff workbench is the front door (docs/84). | 585 | [039_docs84.md](039_docs84.md) | ../84_diff_workbench.md |
| 40 | Web UI (HTMX) | Diff workbench: side-by-side figures, a named direction, a top-anchored tab (docs/147, 202 | 932 | [040_docs147.md](040_docs147.md) | ../147_diff_figures_direction.md |
| 41 | Web UI (HTMX) | One search grammar (docs/96). | 552 | [041_docs96.md](041_docs96.md) | ../96_search_grammar.md |
| 42 | Web UI (HTMX) | **A typo finds the key; a range beats a list; the Agent says how it is wired (do | 6323 | [042_docs175.md](042_docs175.md) | ../175_search_ladder_and_agent_strip.md |
| 43 | Web UI (HTMX) | Comparison also lives in the **Compare hub** (`/compare-hub`) — one URL-canonica | 454 | [043_comparison-also-lives-in-the-compare-hub.md](043_comparison-also-lives-in-the-compare-hub.md) | none |
| 44 | Web UI (HTMX) | What actually leaves the instrument (docs/109). | 547 | [044_docs109.md](044_docs109.md) | ../109_physical_units.md |
| 45 | Web UI (HTMX) | A tab keeps its state; the grid works like a spreadsheet; failures explain themselves (doc | 1171 | [045_docs110.md](045_docs110.md) | ../110_daily_flow_plan.md |
| 46 | Web UI (HTMX) | Pull/push never resets the view (docs/144, 2026-08-31 — customer principle: "sync 버튼은 SM의  | 2453 | [046_docs144.md](046_docs144.md) | ../144_view_never_resets.md |
| 47 | Web UI (HTMX) | Json Tree inline edit: the literal, and the overlap (docs/145, 2026-08-31). | 1021 | [047_docs145.md](047_docs145.md) | ../145_tree_edit_literal.md |
| 48 | Web UI (HTMX) | **The loader covers the wait; the red boxes follow the tray (docs/146, 2026-09-0 | 1552 | [048_docs146.md](048_docs146.md) | ../146_loader_and_markers.md |
| 49 | Web UI (HTMX) | Live State Edit shows everything (docs/85). | 750 | [049_docs85.md](049_docs85.md) | ../85_livegrid_show_all.md |
| 50 | Web UI (HTMX) | Settings + Calculator are sidebar tools (docs/89). | 704 | [050_docs89.md](050_docs89.md) | ../89_sidebar_tools.md |
| 51 | Web UI (HTMX) | The Calculator as its own browser window (docs/156, 2026-09-03 — user feedback: "SM 창 내부에서 | 1862 | [051_docs156.md](051_docs156.md) | ../156_calc_window.md |
| 52 | Web UI (HTMX) | **The sidebar's tick box is a real control, and Compare makes room (docs/161, 20 | 1102 | [052_docs161.md](052_docs161.md) | ../161_sidebar_select_ux.md |
| 53 | Web UI (HTMX) | **Ctrl+Z after Apply-to-live writes the chip (docs/160, 2026-09-03 — customer, C | 6439 | [053_docs160.md](053_docs160.md) | ../160_undo_writes_live.md |
| 54 | Web UI (HTMX) | **Ctrl+Z on a LIST cell shows on screen; the 🕘 reaches it (docs/159, 2026-09-03 | 1600 | [054_docs159.md](054_docs159.md) | ../159_undo_list_cells.md |
| 55 | Web UI (HTMX) | **Param History: None means none, a filter change swaps only the results (docs/1 | 1244 | [055_docs158.md](055_docs158.md) | ../158_param_history_none.md |
| 56 | Web UI (HTMX) | **The experiment list: larger rows, the run number as a badge (docs/157, 2026-09 | 1136 | [056_docs157.md](056_docs157.md) | ../157_sidebar_run_rows.md |
| 57 | Web UI (HTMX) | Overview tiles drag-reorder (docs/153, 2026-09-01 — docs/150 v2 item 1). | 538 | [057_docs153.md](057_docs153.md) | ../153_overview_drag.md |
| 58 | Web UI (HTMX) | The visible door: Overview settings button, global + per-tile statistic (docs/152, 2026-09 | 633 | [058_docs152.md](058_docs152.md) | ../152_overview_settings.md |
| 59 | Web UI (HTMX) | Hovering an Overview tile lists every qubit/pair with its value (docs/151, 2026-09-01). | 606 | [059_docs151.md](059_docs151.md) | ../151_overview_hover.md |
| 60 | Web UI (HTMX) | Overview panels are customizable, formulas untouched (docs/150, 2026-09-01). | 1149 | [060_docs150.md](060_docs150.md) | ../150_overview_custom.md |
| 61 | Web UI (HTMX) | The config picker gets the explorer, files become pickable, the dialog learns the keyboard | 1405 | [061_docs149.md](061_docs149.md) | ../149_config_picker_browser.md |
| 62 | Web UI (HTMX) | Chip Status: Fidelity splits in three, GEF stops vanishing (docs/148, 2026-09-01). | 2004 | [062_docs148.md](062_docs148.md) | ../148_fidelity_split.md |
| 63 | Web UI (HTMX) | Chip topology as a picture (docs/92, plan docs/91). | 776 | [063_docs92.md](063_docs92.md) | ../92_topology_as_picture.md |
| 64 | Web UI (HTMX) | The schema-drift healing chain + the silent grid cap (docs/94). | 601 | [064_docs94.md](064_docs94.md) | ../94_gridcap_and_schema_heal.md |
| 65 | Web UI (HTMX) | Every pulse class, generated and loadable (docs/98). | 493 | [065_docs98.md](065_docs98.md) | ../98_gen_ss_allpulse.md |
| 66 | Web UI (HTMX) | Component-map feedback r1 (docs/93). | 684 | [066_docs93.md](066_docs93.md) | ../93_component_map_feedback_plan.md |
| 67 | Web UI (HTMX) | SM 1.0: the execution trio is hidden, the agent gets a door (docs/172, 2026-09-06 — custom | 1884 | [067_docs172.md](067_docs172.md) | ../172_agent_cockpit.md |
| 68 | Web UI (HTMX) | SM 1.0 part 2 is IN PROGRESS on `feat/agent-cockpit` (docs/173, 2026-09-06 — read §11 befo | 9070 | [068_docs173.md](068_docs173.md) | ../173_sm1_final_plan_ux.md |
| 69 | Web UI (HTMX) | Sidebar IA (FB-09, docs/FB-09_sidebar_ia.md; supersedes docs/69): | 739 | [069_docsFB-09.md](069_docsFB-09.md) | ../FB-09_sidebar_ia.md |
| 70 | Web UI (HTMX) | UI state persisted in `localStorage`: `quam_font_size`, `quam_sidebar_collapsed` | 1289 | [070_docs135.md](070_docs135.md) | ../135_profile_cases_closure.md |
| 71 | Web UI (HTMX) | Modal focus traps (`trapFocus` in app.js) are leak-proof at the source: re-trapp | 413 | [071_modal-focus-traps-trapfocus-in-app-js-ar.md](071_modal-focus-traps-trapfocus-in-app-js-ar.md) | none |
| 72 | Web UI (HTMX) | Customer feedback rounds 2–3 (docs/126, 2026-08-19) — real-browser verified against the PJ | 1905 | [072_docs126.md](072_docs126.md) | ../126_customer_feedback_r2.md |
| 73 | Web UI (HTMX) | **The Datasets tree collapses with the arrow keys, and the printout holds every | 3524 | [073_docs188.md](073_docs188.md) | ../188_report_everything.md |
| 74 | Web UI (HTMX) | Versions panel: per-row Diff + differences-only N-way Compare (docs/128, 2026-08-21). | 1948 | [074_docs128.md](074_docs128.md) | ../128_version_diff_and_nway_compare.md |
| 75 | Web UI (HTMX) | Versions answer WHO/WHY, not just WHEN (docs/132, 2026-08-22). | 3173 | [075_docs132.md](075_docs132.md) | ../132_readout_1d.md |
| 76 | Web UI (HTMX) | QDAC + LF-FEM on ONE qubit, through the LAB's own builder (docs/137, 2026-08-24). | 4512 | [076_docs137.md](076_docs137.md) | ../137_closure_executed.md |
| 77 | Web UI (HTMX) | A 2Q RB number is per-Clifford or per-gate (docs/138, 2026-08-24). | 3652 | [077_docs138.md](077_docs138.md) | ../138_c2_executed_name_scrub.md |
| 78 | Web UI (HTMX) | Perf diagnosis + the Param History fix (docs/139, 2026-08-25). | 3201 | [078_docs139.md](078_docs139.md) | ../139_chain_replay_q1_q2.md |
| 79 | Web UI (HTMX) | The night session: apply cost, the Config Manual, search speed, the undo trail (docs/141,  | 45103 | [079_docs141.md](079_docs141.md) | ../141_night_session_manual_speed.md |
| 80 | Web UI (HTMX) | The v1.1.0 delivery walk: the ledger is the one history (docs/301, 2026-10-08). | 1467 | [080_docs301.md](080_docs301.md) | ../301_delivery_walk_v1_1_0.md |
| 82 | Multi-Context Registry | `app.config["contexts"]` maps names → context dicts with a `type` field (current | 269 | [082_app-config-contexts-maps-names-context-d.md](082_app-config-contexts-maps-names-context-d.md) | none |
| 84 | Param History | `core/history.py` (`HistoryManager`, ~2,300 lines) keeps timestamped snapshots o | 503 | [084_core-history-py-historymanager-2-300-lin.md](084_core-history-py-historymanager-2-300-lin.md) | none |
| 85 | Param History | One history store (S10, docs/302, v1.2.0). | 1200 | [085_docs302.md](085_docs302.md) | ../302_s10_one_history_store.md |
| 86 | Param History | Production contracts (v1.3.0, docs/303). | 1932 | [086_docs303.md](086_docs303.md) | ../303_v130_production_campaign.md |
| 87 | Param History | Pre-deployment verification (v1.3.0, docs/304). | 1186 | [087_docs304.md](087_docs304.md) | ../304_v130_predeploy_verification.md |
| 88 | Param History | Generic Interactive and map tools (v1.4.0, docs/305). | 1533 | [088_docs305.md](088_docs305.md) | ../305_generic_interactive_map_tools.md |
| 89 | Param History | Chip identity is a ladder | 636 | [089_docs20.md](089_docs20.md) | ../20_param_history.md |
| 90 | Param History | Every numeric parameter, as change points (docs/83). | 634 | [090_docs83.md](090_docs83.md) | ../83_all_parameters_history.md |
| 91 | Param History | Column History + LiveEditUndo | 486 | [091_docs20.md](091_docs20.md) | ../20_param_history.md |
| 92 | Param History | **Dataset "Apply to chip" (docs/108, amended by docs/126 ⑤ — read that entry abo | 564 | [092_docs108.md](092_docs108.md) | ../108_dataset_apply_to_chip.md |
| 93 | Param History | Ctrl+Z crosses the save boundary; Ctrl+Shift+Z is redo (docs/107). | 897 | [093_docs107.md](093_docs107.md) | ../107_undo_journal.md |
| 94 | Param History | Every editable value carries a 🕘 value-history popover — `GET /field/history?pat | 335 | [094_every-editable-value-carries-a-value-his.md](094_every-editable-value-carries-a-value-his.md) | none |
| 95 | Param History | The auto-incremental backfill on page open is single-attempt per chip per browse | 229 | [095_the-auto-incremental-backfill-on-page-op.md](095_the-auto-incremental-backfill-on-page-op.md) | none |
| 97 | State History | `/state-history` (sidebar; Param History nests under it since r15/docs/69) is a | 804 | [097_docs69.md](097_docs69.md) | ../69_sidebar_ia.md |
| 99 | Typed Editing + State↔Env Validation | Every leaf of state.json (and wiring.json) is dynamically editable, type-safely. | 285 | [099_every-leaf-of-state-json-and-wiring-json.md](099_every-leaf-of-state-json-and-wiring-json.md) | none |
| 100 | Typed Editing + State↔Env Validation | Type system | 526 | [100_type-system.md](100_type-system.md) | none |
| 101 | Typed Editing + State↔Env Validation | Env schema probe | 196 | [101_env-schema-probe.md](101_env-schema-probe.md) | none |
| 102 | Typed Editing + State↔Env Validation | Type visibility (r14, docs/56): | 745 | [102_docs56.md](102_docs56.md) | ../56_autofit_scheduler.md |
| 103 | Typed Editing + State↔Env Validation | Env baselines + user verdicts (docs/79): | 575 | [103_docs79.md](103_docs79.md) | ../79_env_type_baseline.md |
| 104 | Typed Editing + State↔Env Validation | Band-edge headroom is SM's own guideline, not QM's (docs/81): | 293 | [104_docs81.md](104_docs81.md) | ../81_extras_free_form.md |
| 105 | Typed Editing + State↔Env Validation | Validation | 407 | [105_validation.md](105_validation.md) | none |
| 107 | QUAlibrate Projects | Projects | 924 | [107_docs55.md](107_docs55.md) | ../55_qualibrate_projects.md |
| 108 | QUAlibrate Projects | **Sync-Qualibrate may probe its port (docs/206, 2026-09-25 — customer: "Nothing | 530 | [108_docs206.md](108_docs206.md) | ../206_workbench_csp.md |
| 109 | QUAlibrate Projects | Project-centric shell (docs/63). | 586 | [109_docs63.md](109_docs63.md) | ../63_project_centric_reorg.md |
| 110 | QUAlibrate Projects | A project opens only with a chosen env (docs/229, customer 2026-09-30). | 1011 | [110_docs229.md](110_docs229.md) | ../229_cust_0930_alias_ops_project_env.md |
| 111 | QUAlibrate Projects | Customer feedback 2026-09-30, second batch (docs/230–233). | 1021 | [111_docs230.md](111_docs230.md) | ../230_live_edit_pair_wrap_cap.md |
| 112 | QUAlibrate Projects | Multi-select, keyboard and tree (docs/234–235; free-edit D-29..D-35). | 1469 | [112_docs234.md](112_docs234.md) | ../234_235_keyboard_select_and_tree_multi_edit.md |
| 113 | QUAlibrate Projects | The sidebar chip rows fold (docs/236). | 467 | [113_docs236.md](113_docs236.md) | ../236_sidebar_chip_rows_fold.md |
| 114 | QUAlibrate Projects | A Reload in the top bar (docs/237). | 636 | [114_docs237.md](114_docs237.md) | ../237_topbar_reload.md |
| 116 | Autofit (one-button automatic fitting scheduler) | Autofit | 1422 | [116_docs69.md](116_docs69.md) | ../69_sidebar_ia.md |
| 117 | Autofit (one-button automatic fitting scheduler) | Runner + AI calibration agent (docs/78 — read §17 before trusting any earlier section). | 1808 | [117_docs78.md](117_docs78.md) | ../78_runner_agent.md |
| 118 | Autofit (one-button automatic fitting scheduler) | **The judge on the user's own Claude Code login (docs/169, 2026-09-05 — customer | 1731 | [118_docs169.md](118_docs169.md) | ../169_claude_code_judge.md |
| 119 | Autofit (one-button automatic fitting scheduler) | Auto-calibration verified against the CQT corpus (docs/127, 2026-08-21 — 0.9.9 groundwork) | 1383 | [119_docs127.md](119_docs127.md) | ../127_autofit_cqt_corpus.md |
| 120 | Autofit (one-button automatic fitting scheduler) | The red team, and what it cost the campaign that invited it (docs/123–125). | 1402 | [120_docs123.md](120_docs123.md) | ../123_red_team_brief.md |
| 121 | Autofit (one-button automatic fitting scheduler) | Four reports, and the fix was never the first guess (docs/122, extended in Phase 2). | 1580 | [121_docs122.md](121_docs122.md) | ../122_four_reports_phase0.md |
| 122 | Autofit (one-button automatic fitting scheduler) | A press means what the presser could see; a lie in a variable is a lie everywhere (docs/12 | 1177 | [122_docs120.md](122_docs120.md) | ../120_customer_feedback_campaign.md |
| 123 | Autofit (one-button automatic fitting scheduler) | A push that finds the chip moved MERGES, instead of turning itself off (docs/187, 2026-09- | 8326 | [123_docs187.md](123_docs187.md) | ../187_autosync_merge.md |
| 124 | Autofit (one-button automatic fitting scheduler) | A run opened from a chart closes with its own × (docs/204, 2026-09-23 — customer: Trends 클 | 727 | [124_docs204.md](124_docs204.md) | ../204_trends_run_close.md |
| 125 | Autofit (one-button automatic fitting scheduler) | **A commit never shakes the screen (docs/203, 2026-09-23 — customer: "live state | 1859 | [125_docs203.md](125_docs203.md) | ../203_topbar_twerk.md |
| 126 | Autofit (one-button automatic fitting scheduler) | **Auto-apply — the covenant amended (docs/117; amended again by docs/160 above, | 945 | [126_docs117.md](126_docs117.md) | ../117_auto_apply.md |
| 127 | Autofit (one-button automatic fitting scheduler) | The QA journeys campaign: 330 findings, two fix waves, a reverify (docs/205, 2026-09-25). | 865 | [127_docs205.md](127_docs205.md) | ../205_qa_journeys_campaign.md |
| 128 | Autofit (one-button automatic fitting scheduler) | Chip Status' derived-RB fidelity stopped sweeping the archive (docs/207, 2026-09-25). | 835 | [128_docs207.md](128_docs207.md) | ../207_chip_status_rb_cache.md |
| 129 | Autofit (one-button automatic fitting scheduler) | Trends plotted T1/T2 but nothing for IRB (docs/208, 2026-09-25). | 965 | [129_docs208.md](129_docs208.md) | ../208_trends_irb.md |
| 130 | Autofit (one-button automatic fitting scheduler) | Twenty sync correctness fixes, then one status control (docs/209, 2026-09-25 — user decisi | 1515 | [130_docs209.md](130_docs209.md) | ../209_sync_fixes_and_one_control.md |
| 131 | Autofit (one-button automatic fitting scheduler) | Datasets > Trends served from RAM, not re-rendered DOM (docs/210, 2026-09-25/26). | 1354 | [131_docs210.md](131_docs210.md) | ../210_ram_p0_p3.md |
| 132 | Autofit (one-button automatic fitting scheduler) | The big-state QA rig: at ~12x the bytes, live writes cost 7-18x and Live Edit 37-86x (docs | 866 | [132_docs211.md](132_docs211.md) | ../211_big_state_qa_rig.md |
| 133 | Autofit (one-button automatic fitting scheduler) | The live write path in RAM: the same bytes, written far sooner (docs/212, 2026-09-27). | 1001 | [133_docs212.md](133_docs212.md) | ../212_live_write_in_ram.md |
| 134 | Autofit (one-button automatic fitting scheduler) | Live Edit grids and the Json Tree patch themselves from a change feed (docs/213, 2026-09-2 | 1023 | [134_docs213.md](134_docs213.md) | ../213_live_edit_json_tree_in_ram.md |
| 135 | Autofit (one-button automatic fitting scheduler) | Chip Status › Trends from RAM: one table per history token, a toggle is a patch (docs/214, | 849 | [135_docs214.md](135_docs214.md) | ../214_chip_status_trends_from_ram.md |
| 136 | Autofit (one-button automatic fitting scheduler) | A new run is absorbed by the tick; Param History reads from RAM (docs/215, 2026-09-27). | 1021 | [136_docs215.md](136_docs215.md) | ../215_datasets_new_run_param_history.md |
| 137 | Autofit (one-button automatic fitting scheduler) | Re-opening a chip is a byte digest, not a re-parse (docs/216, 2026-09-27). | 906 | [137_docs216.md](137_docs216.md) | ../216_cold_open_chip_switch.md |
| 138 | Autofit (one-button automatic fitting scheduler) | **Pulses: a value commit recomputes only the rows that can see it; a hand-added | 1079 | [138_docs217.md](138_docs217.md) | ../217_pulses_in_ram_shape_discovery.md |
| 139 | Autofit (one-button automatic fitting scheduler) | **A lab's own pulse and gate classes check every write, behind one "+ New pulse" | 1140 | [139_docs218.md](139_docs218.md) | ../218_lab_pulse_classes_and_new_pulse.md |
| 140 | Autofit (one-button automatic fitting scheduler) | Z-line distortion: both LF-port filters drawn together, judged per QOP model (docs/219, 20 | 1065 | [140_docs219.md](140_docs219.md) | ../219_zline_distortion.md |
| 141 | Autofit (one-button automatic fitting scheduler) | Chip Status says when each metric was measured; Overview tiles jump to their panel (docs/2 | 848 | [141_docs220.md](141_docs220.md) | ../220_chip_status_meta_info.md |
| 142 | Autofit (one-button automatic fitting scheduler) | The Datasets run detail keeps the reader's place exactly across run switches (docs/221, 20 | 910 | [142_docs221.md](142_docs221.md) | ../221_datasets_scroll_exactness.md |
| 143 | Autofit (one-button automatic fitting scheduler) | **Small UI queue: "Live edit - Json Tree view", no bare arrow on Chip Status tit | 921 | [143_docs222.md](143_docs222.md) | ../222_small_ui_queue.md |
| 144 | Autofit (one-button automatic fitting scheduler) | Clear the agent conversation (docs/297). | 1220 | [144_docs297.md](144_docs297.md) | ../297_agent_clear_conversation.md |
| 145 | Autofit (one-button automatic fitting scheduler) | Agents menu QA: a refused approve takes its save back; the toast lift is a body class (doc | 999 | [145_docs223.md](145_docs223.md) | ../223_agents_menu_qa.md |
| 146 | Autofit (one-button automatic fitting scheduler) | The w7 integration: three duplicated mechanisms became one (docs/224, 2026-09-27). | 1053 | [146_docs224.md](146_docs224.md) | ../224_w7_integration.md |
| 147 | Autofit (one-button automatic fitting scheduler) | **The w8 follow-ups: an exact place after F5, a bounded grid window, "Delete tog | 1659 | [147_docs225.md](147_docs225.md) | ../225_w8_followups.md |
| 148 | Autofit (one-button automatic fitting scheduler) | **The w9 wave: sliced Chip Status panels, a virtual "All" pulse view, structural | 2198 | [148_docs227.md](148_docs227.md) | ../227_w9_followups.md |
| 150 | FSP → Amplitude Compensation | Editing a port's `full_scale_power_dbm` keeps pulse powers constant only if the | 1278 | [150_editing-a-port-s-full-scale-power-dbm-ke.md](150_editing-a-port-s-full-scale-power-dbm-ke.md) | none |
| 152 | Dataset Support | `core/dataset.py` (DatasetStore) + `core/experiment_data.py` load experiment run | 354 | [152_core-dataset-py-datasetstore-core-experi.md](152_core-dataset-py-datasetstore-core-experi.md) | none |
| 153 | Dataset Support | Multiple windows on one machine (docs/80). | 1043 | [153_docs80.md](153_docs80.md) | ../80_multi_instance.md |
| 154 | Dataset Support | Polling under a concurrent writer (docs/80). | 1308 | [154_docs80.md](154_docs80.md) | ../80_multi_instance.md |
| 155 | Dataset Support | The run table's cold build lands on nobody's click, and the "N new" chip refreshes (docs/1 | 1480 | [155_docs170.md](155_docs170.md) | ../170_datasets_refresh_and_new_chip.md |
| 156 | Dataset Support | The run table survives a restart (docs/171, 2026-09-06). | 931 | [156_docs171.md](156_docs171.md) | ../171_dataset_store_cache.md |
| 157 | Dataset Support | Tier-2 scale batch (docs/105, docs/104 items #1–#11). | 894 | [157_docs105.md](157_docs105.md) | ../105_tier2_scale.md |
| 159 | Interactive Data (ndview + click contracts) | The dataset detail page's Data tab is `core/ndview.py` + `web/static/ndview.js`: | 480 | [159_docs67.md](159_docs67.md) | ../67_raw_netcdf_ndview.md |
| 160 | Interactive Data (ndview + click contracts) | A shot index is not an axis (docs/82). | 602 | [160_docs82.md](160_docs82.md) | ../82_repeat_axes.md |
| 161 | Interactive Data (ndview + click contracts) | The Interactive tab now uses the replot path (`core/interactive_plots/replot.py` | 795 | [161_the-interactive-tab-now-uses-the-replot-.md](161_the-interactive-tab-now-uses-the-replot-.md) | none |
| 162 | Interactive Data (ndview + click contracts) | The Interactive panel's four instabilities (docs/118). | 844 | [162_docs118.md](162_docs118.md) | ../118_audit_two_chips.md |
| 164 | Pulses Page | Pulses | 723 | [164_pulses.md](164_pulses.md) | none |
| 165 | Pulses Page | **The chip map is the Pulses page's row filter; an alias row follows a two-hop c | 4293 | [165_docs166.md](165_docs166.md) | ../166_pulses_map_and_row_hover.md |
| 166 | Pulses Page | **A pulse whose CLASS the lab wrote still plots (docs/189, 2026-09-16 — customer | 3763 | [166_docs189.md](166_docs189.md) | ../189_lab_pulse_class_plots.md |
| 167 | Pulses Page | Inline-commit stability (docs/75). | 431 | [167_docs75.md](167_docs75.md) | ../75_inline_commit_stability.md |
| 169 | Generate Config + Config Viewer | `core/config_generator.py` drives the **Generate Config** wizard: discovers cond | 409 | [169_core-config-generator-py-drives-the-gene.md](169_core-config-generator-py-drives-the-gene.md) | none |
| 170 | Generate Config + Config Viewer | After `machine.save()`, a post-save fix-up rewrites each readout MW input port's | 209 | [170_after-machine-save-a-post-save-fix-up-re.md](170_after-machine-save-a-post-save-fix-up-re.md) | none |
| 171 | Generate Config + Config Viewer | The Populate step is absolute-first: RF frequencies are the primary input, LO au | 377 | [171_the-populate-step-is-absolute-first-rf-f.md](171_the-populate-step-is-absolute-first-rf-f.md) | none |
| 172 | Generate Config + Config Viewer | The r3 feedback batch (`docs/53_generate_feedback_r3.md`) added: as-you-type inl | 289 | [172_docs53.md](172_docs53.md) | ../53_generate_feedback_r3.md |
| 173 | Generate Config + Config Viewer | Auto-allocate is the default; the Wiring step delivers its diagram on its own (docs/134, 2 | 2963 | [173_docs134.md](173_docs134.md) | ../134_adjudication40_profile_cases.md |
| 174 | Generate Config + Config Viewer | The rack was cropped, not truncated (docs/135, 2026-08-23). | 4801 | [174_docs135.md](174_docs135.md) | ../135_profile_cases_closure.md |
| 175 | Generate Config + Config Viewer | QDAC-II is a component, not an add-on (docs/136, 2026-08-23). | 16928 | [175_docs136.md](175_docs136.md) | ../136_abc_retag_two_tier.md |
| 176 | Generate Config + Config Viewer | QDAC-II bias (docs/119). | 810 | [176_docs119.md](176_docs119.md) | ../119_qdac2_bias_wizard.md |
| 177 | Generate Config + Config Viewer | The chip's root class, and three things the review would not say (docs/176, 2026-09-11). | 3351 | [177_docs176.md](177_docs176.md) | ../176_generate_root_class.md |
| 178 | Generate Config + Config Viewer | `core/config_view.py` is the **Config Viewer**: shows the QM config dict + per-p | 279 | [178_docs27.md](178_docs27.md) | ../27_config_generator.md |
| 180 | Re-generate Config | Re-generate config | 601 | [180_re-generate-config.md](180_re-generate-config.md) | none |
| 181 | Re-generate Config | A renamed qubit is the same qubit (docs/295): | 434 | [181_docs295.md](181_docs295.md) | ../295_regen_rename_keeps_the_qubit.md |
| 182 | Re-generate Config | A renamed qubit keeps its history (docs/296): | 891 | [182_docs296.md](182_docs296.md) | ../296_rename_keeps_the_history.md |
| 183 | Re-generate Config | Entity collections are never resurrected. TWPAs build natively via `Connectivity | 473 | [183_entity-collections-are-never-resurrected.md](183_entity-collections-are-never-resurrected.md) | none |
| 184 | Re-generate Config | A pair reference is a POINTER, and it can be TWO hops (docs/118). | 503 | [184_docs118.md](184_docs118.md) | ../118_audit_two_chips.md |
| 185 | Re-generate Config | A rebuild said what it lost, never what replaced it (docs/202, 2026-09-18). | 4430 | [185_docs202.md](185_docs202.md) | ../202_class_substitution.md |
| 186 | Re-generate Config | Adaptive reconstruction + populate-protect (r16, docs/72) | 456 | [186_docs72.md](186_docs72.md) | ../72_regen_adaptive_fidelity.md |
| 188 | Environment Capability Validation | A build only works if the selected env exposes the exact functions the chip need | 739 | [188_a-build-only-works-if-the-selected-env-e.md](188_a-build-only-works-if-the-selected-env-e.md) | none |
| 190 | Type Coercion Philosophy | `modifier.py` casts new values to the original field's type but **never validate | 199 | [190_modifier-py-casts-new-values-to-the-orig.md](190_modifier-py-casts-new-values-to-the-orig.md) | none |
| 192 | Key Files | / File / Purpose / | 13307 | [192_file-purpose.md](192_file-purpose.md) | none |
| 194 | Tests | Tests use `tmp_path` synthetic fixtures. Some tests additionally probe real data | 768 | [194_tests-use-tmp-path-synthetic-fixtures-so.md](194_tests-use-tmp-path-synthetic-fixtures-so.md) | none |
| 196 | Dependencies | Core: Flask, Jinja2, Typer, Rich. Desktop: pywebview. Data: h5py (HDF5 reading), | 491 | [196_core-flask-jinja2-typer-rich-desktop-pyw.md](196_core-flask-jinja2-typer-rich-desktop-pyw.md) | none |
