<!-- Moved verbatim from root CLAUDE.md (section: Key Files, block 192) on 2026-11-14. -->

| File | Purpose |
|------|---------|
| `quam_state_manager/core/safe_io.py` | Conflict-safe live-file I/O — share-delete reads, `ReplaceFileW` writes (with an `os.replace` fallback on WinError 50/1 — WSL's P9 server doesn't implement ReplaceFileW), mtime-bracketed pair read, atomic JSON writes |
| `quam_state_manager/core/working_copy.py` | Per-chip working copy; sync/apply between it and the live files |
| `quam_state_manager/core/pointer_resolver.py` | Core innovation — resolves QUAM JSON pointers; cache lives per `QuamStore` |
| `quam_state_manager/core/loader.py` | QuamStore: loads via safe_io, merges, validates state+wiring; owns the per-instance pointer cache |
| `quam_state_manager/core/scanner.py` | Workspace discovery (safe_io-routed), LRU cache |
| `quam_state_manager/core/search_index.py` | <1ms keystroke search (prefix map + trigram) |
| `quam_state_manager/core/query.py` | Flattens nested JSON into qubit/pair property dicts |
| `quam_state_manager/core/modifier.py` | Edits with undo + two-phase rollback; clears the store's pointer cache on mutate |
| `quam_state_manager/core/saver.py` | Atomic writes (delegates to safe_io), `.bak` rotation, CSV/MD export |
| `quam_state_manager/core/pulse_catalog.py` | Pulse-class registry (params/defaults/inferred-length math) — single source of truth for Pulses page + add-pulse forms |
| `quam_state_manager/core/key_manual.py` | The Config Manual model (docs/141): one row per (class, field) from the env classes' own docstrings + the QM docs map, labelled by source; `node_keys()` = what THIS place can carry (set / unset / undeclared) |
| `quam_state_manager/core/key_manual_docs.py` | Curated key documentation transcribed from the OFFICIAL QM docs (page#anchor + verbatim line per entry; a test asserts every anchor is a real heading) — nothing invented, undescribed keys absent |
| `quam_state_manager/web/static/manual.js` | The movable Config Manual window (calculator pattern) + the house search box + the `?`/F1 entry points |
| `quam_state_manager/web/static/undo-trail.js` | The Undo trail panel: every Ctrl+Z / Ctrl+Shift+Z answered with path, from → to, and ONE "go to field" button that is the only thing that navigates |
| `quam_state_manager/core/qdac_lf_recipe.py` | Emits the LAB-idiom combined generator for a bias-tee chip (`build_quam_qdac_lf.py` + `generate_qdac_LF_combined.py`) — the lab's own builder picks ONE class per qubit id and silently overwrites a FluxLine with the QDAC bias; only emitted when a spec declares a bias-tee qubit (docs/137) |
| `quam_state_manager/core/qdac.py` | The ONE place SM decides what a QDAC-II bias line is — `bias_mode` (`opx`/`qdac`/`bias_tee`), `bias_line_of` (returns the FIELD NAME too, since it is not always `z`), the two-hop `trigger_ref`, `ext_groups` (the real shared cabling + its conflict case). Structural detection, so a lab's own bias-tee class reads correctly with no env change (docs/136) |
| `quam_state_manager/core/bulk_virt.py` | Server-side column virtualization for the Live-Edit qubit grid (docs/141 §4n) — the planner that decides which columns render COLD, mirroring the client's own estimate; its width metrics are pinned against `bulk-edit.js`, and §4ac states the two configurations where the mirror is not conservative |
| `quam_state_manager/web/static/grid-virt.js` | The ONE column-virtualization core shared by the Live-Edit qubit and pair grids (docs/141 §4ad) — cold/remote sets, the value map, width freeze, the look-ahead scroll pass, `ensureTd` hydration. It carries **no default for a grid's own DOM fact** (`rowAttr`) and keys per-instance state by `styleId`, because both grids scroll inside one `#table-pane` |
| `quam_state_manager/core/run_watch.py` | Stat-based watcher for new experiment runs (docs/141 §4p) behind `GET /datasets/wait`; run mtimes via `os.stat`, never `DirEntry.stat()` (a Windows cache read that never sees a write inside the directory — §4ac) |
| `quam_state_manager/web/static/float-panel.js` | The ONE drag core for the three tool windows (Calculator / Settings / Config Manual) and the ONE place their selectors are named (`TOOLS_SEL`) — docs/141 §4u, §4ac |
| `quam_state_manager/web/static/live-wake.js` | The single long-poll to `/datasets/wait` that wakes the run polls; backs off on a `saturated` answer (docs/141 §4p, §4ac) |
| `quam_state_manager/web/static/diff-panes.js` | The pane view's client: baseline switch from the server's pairwise equality matrix, the Keys-tree collapse (docs/141 §4z/§4ab/§4ac) |
| `quam_state_manager/core/pair_columns.py` | Live State Edit pair grid — derives editable columns from the chip's real pair leaves, lab-flexible. Cells resolve through the same `_build_bulk_cell` pipeline as qubits; UI in the isolated `web/static/pair-edit.js` |
| `quam_state_manager/core/waveform_synth.py` | In-process numpy+scipy waveform synthesis (live preview; golden-pinned to the customer env's quam) |
| `quam_state_manager/core/pulse_index.py` | Pulse enumeration + reverse-pointer (used_by) index + duplicate/rename pointer rewriting |
| `quam_state_manager/generator/run_waveform_golden.py` | Env subprocess: dumps quam ground-truth waveforms → `tests/golden/waveform_golden.json` |
| `quam_state_manager/core/differ.py` | 2-way diffs, float tolerance, N-way trends |
| `quam_state_manager/core/history.py` | Param History: snapshots + SQLite trend index, chip fingerprints, alignment scan, migrations |
| `quam_state_manager/core/qualibrate_config.py` | READ-ONLY `~/.qualibrate` reader: env-var + merge fidelity, Win→WSL path dialect, projects listing, doctor lint, tray badge status |
| `quam_state_manager/core/dataset.py` | DatasetStore: discovers + indexes experiment runs; per-file h5py lock |
| `quam_state_manager/core/ndview.py` | Data-tab N-D cube builder — DIMENSION_LIST axis truth, decimation, byte-budget, bytes LRU |
| `quam_state_manager/core/click_targets.py` | ndview click→write-target candidates (absolute-axis-gated, dim-name-bound) |
| `quam_state_manager/core/interactive_plots/contracts.py` | Click-contract math — patches-first pre-update anchors, RF-at-run, increment/assign/wrap semantics |
| `quam_state_manager/core/interactive_plots/registry.py` | Recipe dispatch (two-tier name matcher) + bundle-input LRU |
| `quam_state_manager/core/experiment_data.py` | Loads node.json + data.json, extracts metadata/fit results |
| `quam_state_manager/core/config_generator.py` | Generate-Config wizard: env discovery, probe, subprocess runner |
| `quam_state_manager/core/config_view.py` | Config Viewer: cached `generate_config()` preview + waveform plots |
| `quam_state_manager/core/regenerate.py` | Re-generate orchestration: reconstruct → subprocess build → value merge → recipe emit + exact-spec sidecar |
| `quam_state_manager/core/regen_spec.py` | Invert a chip → build spec (pinned wiring, populate via pointer chains); exact-spec sidecar read/write |
| `quam_state_manager/core/regen_merge.py` | Value-preserving merge (tier-1 carry / tier-2 graft), pair-id reconciliation, redundant-op prune, TWPA wiring+ports carry |
| `quam_state_manager/core/regen_script.py` | Emit the single-file editable Python build recipe (generate + populate combined) |
| `quam_state_manager/core/capabilities.py` | Env-capability model: `REGISTRY` + `required_capabilities(spec)` + `assess` (blocker/degrade/ok buckets) |
| `quam_state_manager/generator/probe_capabilities.py` | In-env detection catalog (`CATALOG`/`CATALOG_IDS`) + `detect()` → capability manifest |
| `quam_state_manager/generator/run_build.py` | Standalone QM-stack subprocess script (allocate / build modes) |
| `quam_state_manager/generator/run_generate_config.py` | Standalone QM-stack subprocess script (config preview) |
| `quam_state_manager/web/routes.py` | ~90 Flask/HTMX routes |
| `quam_state_manager/web/static/app.js` | ~5,100 lines of vanilla JS for UI polish |
| `quam_state_manager/web/static/generate.js` | ~3,000 lines for the Generate-Config wizard frontend |
| `quam_state_manager/cli.py` | 10 Typer + Rich CLI commands (`--version`, `--json` for scripting) |
| `quam_state_manager/main.py` | pywebview desktop launcher |
| `build/quam-manager.spec` | PyInstaller onedir bundle config |
| `docs/00_overview.md` | Architecture deep-dive — start here |
| `docs/28_conflict_safe_io.md` | Working-copy + armored I/O (read this before touching file paths) |
| `docs/32_red_team_phase_2.md` | Phase 2 red-team findings + which are fixed |
| `docs/53_qop37_alignment.md` | Modern-stack (quam 0.6.0 / quam_builder 0.4.0) ground truth: class moves, `_PULSE_HOMES`, padding_length rename, ParametricCZGate gating — read before touching pulse-class or build-script imports |
| `docs/129_res_power_pilot.md` | The res-spec-vs-power domain-knowledge pilot: 95 real runs / 172 targets on two chips, the case taxonomy, and the expert review that turned it into `knowledge/v1` |
| `docs/133_qubit_spec_joint.md` | The hardest family, and the first read as a PAIR: `08_qubit_spectroscopy` and `08b_..._vs_power` are one workflow, so a 1-D value is only identifiable against the anharmonicity the power run reports. The two-photon trap (§1, §3 — why the obvious statistic says 6 and the figures say 50), the multi-photon ladder, onset-as-a-stretch, `P = FSP + 20*log10|amp|` (reading the amplitude alone gets the SIGN wrong), and §6 recency-versus-agreement with its circularity caveat |
| `docs/132_readout_1d.md` | The 1-D readout family across SIX labs and two node generations: the Fano trap (the tallest excursion is not the resonance), the resonator sign as physics, power-broadening judged against the node's own `target_peak_width`, and the round where the benchmark started arguing back (blind agreement 7/12, two answer keys challenged by their own auditor) |
| `docs/131_four_more_families.md` | Manuals + benchmark for qubit-spec, qubit-vs-flux, res-vs-flux and res-vs-coupler-flux across FIVE labs; **§2 lists ten ways the obvious statistic was wrong** and §5 the measured result (51/73) with its two structural limits |
| `docs/130_replay_benchmark.md` | The future-blind replay benchmark — exemplar images, 51 per-run answer keys, and the measured result (46/50 correct, 1 wrong frequency); **read §5 before quoting any number, and §6 for what it does NOT show** |
| `docs/78_runner_agent.md` | Runner + AI calibration agent — the one-button bring-up loop. **Read §17 (audit) before trusting any earlier section**: it lists what shipped, what is still open, and which earlier claims were false |
| `quam_state_manager/core/autofit/envmatrix.py` | (env × analysis-tree revision × run generation) compatibility probe — the triple a replay verdict is only valid within |
| `quam_state_manager/core/autofit/sourceroot.py` | Read-only `git archive` materialization of a pinned analysis revision (never a checkout — the customer tree is untouchable) |
| `quam_state_manager/core/autofit/figure_gen.py` | Regenerate a run's figure through the LAB's own plotting module, with a wrong-fit injection seam |
| `quam_state_manager/core/autofit/judge_pack.py` | Vision-judge family knowledge: versioned JSON pack + the Clause-B lint enforced at LOAD (a bad exemplar is dropped, never taught) |
| `quam_state_manager/core/autofit/mapshapes.py` | One raw-map reader for every spectroscopy family: cube reading (axis order differs BY FAMILY), background removal (a moving background finds a ridge a per-slice one cannot — 18% vs 100% of slices on a real arch), look-elsewhere-corrected significance, ridge tracking, shape analysis (docs/131) |
| `quam_state_manager/core/autofit/mapcases.py` | Measured shape -> SEMANTIC signal + cross-family flags; the pack's `signal_map` turns a signal into that manual's case id, which is the seam a vision judge slots into |
| `quam_state_manager/core/autofit/replaybench.py` | The generalized future-blind loop for the shape-read families + its scoring |
| `quam_state_manager/core/autofit/knowledge.py` | Domain-knowledge packs: versioned per-family case manuals loaded through the Clause-B lint (an absolute-scale rule is DROPPED, never taught), additive-only lab overlays, `manual_hash` for the verdict context (docs/129) |
| `quam_state_manager/core/autofit/chainwalk.py` | The chain conductor (docs/140): one target's runs -> time-ordered family streams -> shipped walkers -> cross-family recency conclusions with provenance + the parking edge (a pre-parking qubit frequency is stale, never vouched) |
| `quam_state_manager/core/autofit/pathreplay.py` | Future-blind replay: `Session.reveal(k)` RAISES past k, so cheating is a crash not a better score; raw-map reader -> case -> the pack's bounded knob moves -> adopt/retune/abstain, scored against per-run answer keys (docs/130) |
| `quam_state_manager/generator/render_knowledge_exemplars.py` | Re-renders the manual's exemplars from raw with normalised, UNLABELLED axes — confidentiality and Clause B are the same fix, since a picture with no numbers cannot teach an absolute scale |
| `quam_state_manager/core/autofit/verification.py` | The context a verdict is only valid inside — ONE shape stamped by `fit_audit`, `figure_gen` and the engine; `comparable()` is what stops two gate revisions being read as one |
