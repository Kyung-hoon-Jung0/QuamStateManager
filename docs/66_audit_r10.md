# 66 — Pre-merge adversarial audit r10: findings → dispositions

Date: 2026-07-31 · Branch: `fix/audit-r10` (stacked on `feat/extras-data-folder`)
Scope: `main...dev/integration-0730` (the 9-branch merge payload, 42 files,
+6,999) audited by four role agents (state machinery/concurrency ·
history/identity/data · frontend contracts/security · test gaps/cross-branch),
every finding hand-verified before fixing. Two were confirmed by runnable
repros before any fix existed.

## Fixed (each with a pinning test)

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| F-A | HIGH (repro) | `_RUN_VALUE_CACHE` cached the chip-RELATIVE include verdict under a chip-independent key — after a chip switch the cell 🕘 popover showed the OTHER chip's values with Use buttons and suppressed the loaded chip's own runs; warm cache also bypassed the examined-window cap | run caches now hold only chip-independent immutable facts (`_RUN_IDENT_CACHE` net, new `_RUN_CHIP_CACHE` name+entity sets, `_RUN_VALUE_CACHE` extraction results); the verdict is re-derived per call; `examined` counts uniformly warm or cold; one shared `_trim_run_caches` (column series included). Pin: `TestRunCacheChipIndependence` |
| F-B | HIGH (repro) | Revert-last-apply's `_snaps[0]` fallback: `check_and_snapshot` dedups against EVERY known hash, so after an A-B-A revert cycle the newest snapshot holds the WRONG content — the second revert resurrected the value the user had just reverted | new `HistoryManager.snapshot_ts_for_current_content` (content-hash match over snapshot metas); the fallback trusts only a hash-matched snapshot. Pin: `test_second_revert_cycle_targets_true_pre_apply` |
| F-C | HIGH | staged+edited mixed state: a /save or apply-conflict fills the re-apply stash with only the EDITS, flipping the stash-based discriminator — every pull-flavored resolution then silently destroyed the staged base (and the conflict tray offered that as its primary button) | explicit `ctx["staged_base"]` marker set by the stage routes (in-lock, with `_clear_reapply` moved in-lock too — F-M), cleared on apply-success and wholesale pull; the sync guard and both `staged_conflict` computations key on `staged_base OR no-stash`. Pins: `test_staged_base_survives_stash`, `test_staged_then_edited_conflict_stays_honest` |
| F-F | HIGH (repro) | Identity-ladder tier 1 refused its own name on routine fingerprint drift (add/remove qubit or pair, host/cluster move — the token changes) → a NAMED chip's history permanently forked into path/`_alt_` dirs, silently | on token mismatch the claim is judged by ALIGNMENT against the claimed dir's newest sample: ALIGNED/RENAMED, or networks-differ-but-labels-identical (host move: name+labels = two witnesses), or unverifiable-dir ⇒ accept + refresh the stored token; only different-network AND different-labels still refuses. Pins: `TestLadderDriftHeal` (add-qubit, host-move, impostor-refusal) |
| F-H | HIGH | LiveEditUndo had no boundary discipline: `restored\|\|gone` made every pop "succeed" (stale entries ate Ctrl+Z app-wide, blocking the server tier), nothing cleared the stack at save/apply/pull/stage (Ctrl+Z after Apply resurrected pre-apply values as dirty edits), and entries staying live after their edit was STAGED half-undid cell text while the tray still staged the value | `tryUndo` restores only present+un-staged cells (`data-orig == next` ⇒ staged ⇒ skip; fully-stale entries drop silently and the loop continues to the server tier); new `clear()` wired to `stateRestored`, `doStateSync` success, and both hx Apply buttons. Pins: state_sync_selfcheck §2b |
| F-U | LOW→fixed | Ctrl+Z mid-typing in a dirty cell with an empty stack fell through to the server tier and deleted a staged group | the global handler restores a dirty focused cell to `data-orig` and stops; a clean cell still reaches the server tier. Pin: §2b |
| F-I | HIGH | the tray's Revert-last-apply 409 confirm rendered `_sh_confirm.html` whose force button targets `#state-history-detail` — absent outside that page ⇒ htmx targetError, dead button | the tray button posts `?from=tray`; the stage gate then emits `target="#status-bar"` (and threads `from=tray` through the force URL). Pin: `test_stage_confirm_from_tray_targets_status_bar` |
| F-D | MED | a failed pre-apply capture kept the PREVIOUS apply's `last_apply` — the tray offered a two-applies-deep revert labeled as one | `ctx.pop("last_apply")` when no trustworthy target exists. Pin: `test_no_trustworthy_target_drops_last_apply` |
| F-E | MED | both new pre-apply capture blocks snapshotted `_active_path()` instead of the captured ctx (the TOCTOU the same functions defend five lines up) | `ctx["path"]` + `_scope_for(ctx["path"], ctx)` in both |
| F-G | MED | `_uid_for_run_ref` first-match over SORTED roots picked the SHALLOW root in nested layouts (`<ws-root>/<chip>/<date>/#run`) → dead Data-link uids | `_uid_roots` sorts deepest-first. Pin: `TestUidDeepestRoot` |
| F-J | MED | the tray ↶ stayed `display:none` after nearly every staging path (`_swapPendingTray` fires no htmx event; `_tray_oob` fires only `oobAfterSwap`) | `_swapPendingTray` calls `_updateTrayBtn`; the module also listens on `htmx:oobAfterSwap` |
| F-K | MED | `/chip-name/set` + `/chip-data-folder/set` missing from `_SCHEDULER_MUTATOR_ENDPOINTS` — chip mutation allowed mid-plan (un-reviewed extras could ride autofit's next apply; a mid-plan name flip re-routes snapshot attribution) | both added. Pin: `test_extras_editors_in_scheduler_mutator_set` |
| F-L | MED | `stateRestored` soft-refresh × the grids' dirty-cell confirm: a stage with typed-but-uncommitted text triggered veto prompts (decline = the stale-grid bug returns) | time-boxed `window._stateRestoredRefresh` flag; both grid guards let that one programmatic refresh through (typed text belongs to the replaced state) |
| F-M | LOW-MED | `_clear_reapply` ran after the wc lock at both stage sites (a concurrent sync could read dirty+stale-stash) | moved inside the locked block |
| F-N | LOW-MED | every live activation paid the candidates sweep (dataset-store scans + TOML) even for fully-configured chips | gate: candidates computed only when a banner/datalist will consume them (unnamed / dangling / named-without-declared) |
| F-O | LOW | `chip_name_prompts.json` writers were lock-free read-modify-write (lost declines) | module lock around all three writers |
| F-Q | LOW | "run #None" rendered in the popover/chips for runs with an experiment name but no parsable id | both templates guard on `run_id is not none` |
| F-R | LOW | the one `\|safe` sink (`row.svg`) interpolates the on-disk trigger string into SVG class names | allowlist (`save/manual/auto/experiment/restore`) at render |
| F-P | LOW | `project_storage()["source"]` vocabulary drifted from `list_projects()` while the docstring claimed same-shape | docstring honesty (path fields match; source documented as config/default) |
| F-X | pins | unpinned hunks: the `/state/apply-to-live` staged_conflict variant, `GET /chip-name/banner` | covered by `test_staged_then_edited_conflict_stays_honest` (the apply path re-conflicts through the same helper) + `test_chip_name_banner_route` |

## Deferred (documented, accepted for this merge)

- **F-S**: `_adopt_extras_data_folders` runs under the build lock on the slow
  activation path (first-activation `add_root` walk; recurring `is_dir` stat
  on dangling cross-machine values — seconds against a dead SMB target).
  Latency only, never corruption. Follow-up: hoist the hooks out of the lock
  + memoize the dangling stat.
- **F-T**: stacked traps — one Escape can close two layers (palette over the
  ColumnHistory modal). Pre-existing class on main; cosmetic-to-annoying.
- **F-Y**: core-path applies (autofit's writer) don't update `last_apply`, so
  after a plan the tray's revert spans the whole plan. Bounded by the
  review-first stage flow (the diff is shown before any live write).
  Follow-up: hide the button when live content no longer matches the
  post-apply hash.
- **Force-token hardening** (role 1 #7): `force=1`/`force_cross=1` carry no
  content-hash echo — a long-open confirm can discard content staged after
  the prompt. Inherent to every force flag in the codebase; follow-up as a
  cross-cutting change.
- **Sample-None tier-1 adoption leniency** (role 2 #4): kept — the
  name-definitive doctrine wins when a dir is unverifiable; noted as a
  residual risk with hand-edit recovery.
- **Backfill report counter overwrite** for two labels resolving to one dir
  (role 2 #5): counters only; ingestion correct.
- Legacy unpinned hunks from earlier merged batches (param-history expand
  `chip_key`, compare_sources `hist:` alias, backfill dual-key JS,
  `reattributed_v3` notice): pre-existing gaps, tracked here.

## Verification

History family 216 · state/scheduler 205 · web/sync 530 pytest green;
state_sync selfcheck 22 (incl. the new §2b boundary pins), ctrlz / cellbtn /
tab-focus (28) / theme-guard selfchecks green.
