# 90 — merge audit handoff (2026-08-09)

*Not a design doc. This is the map for a reviewer auditing the branch chain
before it merges to `main`, written to be read with **no prior conversation
context**. Everything here was taken from git and from test runs, not from
memory; where a claim rests on something weaker than a passing test, it says so.*

---

## 0. The one-paragraph version

Seven stacked branches add **22 commits** on top of `origin/main` (`8e5fa99`),
touching **105 files, +14,513 / −655**. They are strictly linear — each branches
off the previous — so **merging the tip (`feat/sidebar-tools`) brings all of
it**. Twelve design docs (`docs/78`–`docs/89`) explain each change in its own
terms; this file is the index, the verification ledger, and the honest list of
what was *not* checked.

---

## 1. The chain

Base: **`origin/main` = `8e5fa99`** ("Merge branch 'feat/ui-fsp-btns-port-labels'").

> Note for whoever runs `git log main..`: the **local** `main` in this worktree
> is `cb8891b`, two commits *behind* `origin/main`. Compare against
> `origin/main` or you will see 24 commits instead of 22.

```
origin/main  8e5fa99
 └ feat/multi-instance-safety      ea13cd6   docs 78,79,80,81,82   58 files  +8,714 −271
    └ feat/param-history-all-leaves b9342dd   docs 83               12 files  +2,038 −17
       └ feat/diff-workbench        05cb797   docs 84               17 files  +1,514 −91
          └ feat/livegrid-showall   5c1a56e   docs 85               10 files  +394 −73
             └ feat/sync-keep-mine  91da68e   docs 86                9 files  +550 −8
                └ feat/always-ask-on-drift 846cf3f  docs 87,88      14 files  +610 −85
                   └ feat/sidebar-tools    29ca429  docs 89         10 files  +694 −111
```

All seven are pushed to `origin`. Nothing is stashed; the worktree is clean.

### Commits, newest first

| SHA | Subject |
|---|---|
| `29ca429` | feat(ui): Settings + Calculator become sidebar tools (docs/89) |
| `846cf3f` | feat(sync): never swap what the user is looking at (docs/87) + fix an uneditable field (docs/88) |
| `91da68e` | feat(sync): the third choice — keep mine and overwrite live (docs/86) |
| `5c1a56e` | feat(live-edit): show every property by default, and let search find the rest (docs/85) |
| `05cb797` | docs: refresh the suite baseline |
| `bee2363` | test: the front door moved — three legacy pins updated (docs/84) |
| `7d6c045` | docs(84): the diff workbench |
| `2d901fd` | feat(diff): the diff workbench — two sources, four tabs, differences only (docs/84) |
| `57aa839` | perf(ctx): every page render slept 906 ms on a sidecar that does not exist |
| `b9342dd` | docs(83): every numeric parameter's history |
| `873de18` | feat(param-history): "What changed" — the feed over every parameter (docs/83) |
| `a0e1ea2` | feat(history): index EVERY numeric parameter as change points (docs/83) |
| `ea13cd6` | fix(schema): /schema/missing-keys 500'd on the docs/79 anchor payload |
| `c8fdc57` | fix(ndview): a shot index is not an axis — average repeat dims (docs/82) |
| `d2c02f5` | fix(diagnostics): band-edge headroom is SM's own guideline (docs/81) |
| `a2d6440` | fix(types): extras is free form (docs/81) |
| `e5498f4` | fix(multi-instance): audit pass — three permanent-tax defects (docs/80) |
| `40570ac` | feat(scheduler): runner state is per-chip (docs/80 Part 4) |
| `2c5f307` | feat(multi-instance): sibling windows become visible (docs/80 Parts 1–3) |
| `9d334ff` | fix(datasets): monitoring survives a concurrent writer (docs/80 Part 0) |
| `35c9b35` | feat(types): retained env schema baselines + user verdicts (docs/79) |
| `f5a0407` | feat(types): the type-anomaly alert raises itself (docs/78) |

---

## 2. How to verify anything here

Windows conda env **`LabD_17Q`** is the canonical pytest env. `PYTHONUTF8=1` is
required (node-selfcheck drivers emit UTF-8; cp949 nulls their stdout).

> `LabD_17Q` is the **scrubbed placeholder** for the env's real name, per the
> repo-wide convention (CLAUDE.md uses it too). Copy-pasting it verbatim gives
> `EnvironmentLocationNotFound`. The real name is in the gitignored
> `CLAUDE.local.md` decoder, or just run `conda env list`.

```bash
cd D:\work\state-manager\.claude\worktrees\regen-schema-gate
PYTHONUTF8=1 conda run -n LabD_17Q python -m pytest tests/ -q \
    --timeout=900 --timeout-method=thread \
    --deselect tests/test_main.py::TestWaitForServer
```

Dev server (**must** run from this worktree, and from Windows — WSL loopback is
broken on this machine):

```bash
conda run -n LabD_17Q python -c "from quam_state_manager.web.app import create_app; create_app().run(debug=True, port=5050)"
```

`conda run` buffers all output until exit, so a run looks silent for ~10 min.
For ad-hoc scripts prefer the interpreter directly:
`C:/ProgramData/miniconda3/envs/LabD_17Q/python.exe -u script.py`.

### The expected result

**15 failed / 4,795 passed / 235 skipped / 2 deselected** on the tip
(measured 2026-08-09, 626 s).

14 of those are the **native-Windows environmental baseline** — OS behaviour
differences, all pre-existing, none caused by this chain:

```
test_capabilities_routes.py::test_build_degrade_needs_ack          (sh-script fake interpreters)
test_capabilities_routes.py::test_build_blocker_refuses            (   ″   )
test_compare_hub_routes.py::TestHubShell::test_label_html_is_escaped  (WinError 123 filename)
test_config_generator.py::TestRunningUnderWsl::test_true_on_microsoft_kernel
test_node_scan_cache.py::…::test_scan_file_always_fresh_even_on_mtime_size_collision
test_safe_io.py::test_reader_survives_concurrent_writes            (file-locking timing)
test_scanner.py::…::test_add_root_dedups_same_inode_spellings      (symlink/inode)
test_scanner.py::test_scan_dir_cap_bounds_runaway_symlink_walks    (   ″   )
test_state_coherence.py::TestEvictionNeverLosesEdits               ×2 (tmp-path case identity)
test_state_coherence.py::TestArchiveReadOnly                       ×2 (   ″   )
test_web.py::TestPhase4QuamCacheConcurrency::test_concurrent_activate_quam_keeps_one_entry
test_web.py::TestDatasetSelectionFix::test_dataset_payload_carries_active_folder
```

The 15th is `test_safe_io.py::test_reader_survives_concurrent_os_replace`, a
**genuine flake**. CLAUDE.md used to claim it "passes in isolation"; that was
measured false on 2026-08-09 (**2 pass / 1 fail over three consecutive
single-test runs**) and the claim was corrected in `846cf3f`. A lone failure of
it is never evidence of a regression — re-run before investigating.

**Anything outside that list is a real regression.**

### The baseline shrank, 18 → 14

`docs/87` removed four entries: `TestRestartCleanCopy::test_legacy_meta_
replaced_shows_banner_not_clobber` and `TestBannerSlot` ×3. They assert the
live-diverged banner appears; the old silent auto-adopt raced them on Windows
mtime semantics. Making that path deterministic fixed them. **An auditor should
treat this as a claim to re-check**, since "tests started passing" is exactly
the shape a masked failure would also have — the argument is in §5.

---

## 3. What each branch does

Read the linked doc for the real argument; these are one-paragraph orientations.

### `feat/multi-instance-safety` — docs/78–82 (the largest, 58 files)

Five independent changes that accumulated on one branch:

- **docs/78 — the type alert raises itself.** Stored-as-text numerics were
  detected automatically but the repair sat behind a button. `_arm_type_alarm`
  now fires at three content-entry choke points and `GET /type-alert` consumes
  the flag **one-shot** (not-armed → 204 is what keeps ordinary editing silent).
  The dialog it opens *is* the docs/77 repair dialog plus a header, so one click
  fixes it while the per-field proposal is still on screen.
- **docs/79 — env schema baselines + user verdicts.** The schema cache is keyed
  by interpreter and overwrites on a version change, so SM could never say
  whether a mismatch was the chip's fault or the library's. Baselines are now
  retained per **env identity**; `core/type_verdicts.py` stores the user's
  correction, scoped `env_key → "<class>.<field>"`. Safety: **dormant with no
  verdict file**, and an accept-all overlay is a provable no-op (339 golden
  fields, zero expectation changes).
- **docs/80 — multiple windows on one machine** (the biggest piece; 364-line
  doc). Process registry (`core/instances.py`), run ownership by PID
  (`ForeignRunnerError` → 409), per-chip scheduler state with a verified
  legacy migration, and polling that survives a concurrent writer.
- **docs/81 — two honesty fixes.** Band-edge margin 50 MHz → 5 MHz (QM documents
  no such rule; SM's own guideline was overstated and a lab retuned hardware
  over it), and `extras` is free form so SM forms no opinion about the type of
  what lives there.
- **docs/82 — a shot index is not an axis.** `_default_view` ordered sweeps by
  size and a repetition axis is always the biggest, so runs plotted against
  `n_runs`=2000 instead of the real sweep. Shot dims are now averaged away
  server-side, and never silently (the controls strip says so).

### `feat/param-history-all-leaves` — docs/83

The customer asked whether Param History could cover *every* numeric parameter,
not the curated eleven. Dense indexing is impossible (8,000 leaves × 111
snapshots ≈ 200 MB/chip); indexing only **transitions** is, because the median
number of numeric leaves that change between consecutive snapshots is **2–4 out
of 8,000**. `core/leaf_index.py` adds `leaf_snaps`/`leaf_paths`/`leaf_cp` inside
the chip's existing `index.sqlite` and ends up *smaller* than the eleven-property
index beside it. Surface: `/param-history/changes`, paged by snapshot not row.

### `feat/diff-workbench` — docs/84

"Compare selected" worked and nobody used it — three buttons led to three
surfaces, and the one users reached asked for a context + tolerance + mapping
before showing a row. `/diff?a=&b=&tab=&view=` shows differences IDE-style,
reusing the Explorer's own `renderJsonTree` (which gained `options.union` so
added/removed become first-class, opt-in so live diff stays byte-identical).
"Differences only" is a **pruned document**, not a filter. Also contains
`57aa839`, an unrelated find: **906 ms of a 937 ms render was `time.sleep`** —
an optional sidecar read through `safe_io`'s retry ladder, paid on every page.

### `feat/livegrid-showall` — docs/85

Customer: make Live State Edit's Properties show everything by default. The
premise (that hiding was a performance decision) was checked and is **false** —
`.bulk-col-hidden` is one CSS rule and the server emits every cell regardless.
It was also self-contradictory: r7 had already flipped *derived* columns to
default-visible, so a chip showed ~200 obscure derived leaves **and hid T1**.
Independently, the search only ever scanned *visible* columns, so a hidden
column was findable by neither name nor value. Both fixed. `headline_on` was
split out of `default_on` so the Compare hub's pair-row set stays byte-identical.

### `feat/sync-keep-mine` — docs/86

When something outside SM rewrote the live chip, the user's only options were
Sync or Close — one direction. The push already existed (the modal's
`working_dirty` branch, the conflict tray's force button, State History's
restore-live); it was missing exactly where the user has made no edits of their
own, which is the mis-run case. `↑ Keep mine — overwrite live` added to both
surfaces, last and un-primary, behind one confirm built from a new on-click
preflight, and reversible because `/state/apply-to-live` already snapshots the
pre-apply live.

### `feat/always-ask-on-drift` — docs/87 + docs/88

- **docs/87** — the other half of the same report. `reconcile_with_live`
  auto-pulled whenever the working copy was *provably clean*, which describes
  exactly the person most likely to be hurt by a mis-run. The user-facing path
  now asks. **`auto_adopt` splits actors, not behaviours**: the scheduler's
  post-node hook and the autofit engine still adopt, because autofit's gates
  judge each fit against the pre-update anchor and a stale store makes the
  *verdict* wrong.
- **docs/88** — a reported bug: typing into `lo_mode` answered `Parent at
  'qubits.qC4.resonator.opx_input.lo_mode' is str, not dict or list` for every
  value. `opx_input` is a pointer to a port that lacks `lo_mode` while siblings
  have it; resolution dead-ended and the raw path reached the modifier.

### `feat/sidebar-tools` — docs/89

Settings and Calculator moved from the far top-right into the sidebar with
labels and real SVG icons (the calculator's glyph had been `&#8757;` = U+2235
∵, the "because" sign). Two structural traps handled: the sidebar collapses to
width 0, so a topbar fallback appears only while collapsed; and the sidebar is
`overflow-y:auto`, which clips absolute children, so both popovers moved to body
level with fixed anchoring.

---

## 4. Verification ledger — what was checked, and how

This is the part worth auditing hardest. Evidence strength varies.

| Change | Strongest evidence | Grade |
|---|---|---|
| docs/83 leaf index | Replaying every change point reproduces each chip's newest `state.json` **exactly** (0 missing / 0 extra / 0 wrong) on 4 real stores; incremental ingest == full rebuild on 500 sampled paths/chip | **A — real data, property-level** |
| docs/84 diff | All four tabs on two real experiment runs; node tab surfaced the parameters that genuinely differed (`artificial_detuning_in_mhz`, `num_shots`, `amp_max/min`) | **A — real data** |
| docs/85 show-all | Column/cell/byte counts measured on 3 real chips before and after; `headline_on` counts match the pre-change visible counts exactly (42/10/34) | **A — real data** |
| docs/86 overwrite | End-to-end on a real 21-qubit chip: 42 out-of-band values → clean branch offers both → preflight says 42 → overwrite restores all 42 → `last_apply` staging brings the run's values back | **A — real data, round-trip** |
| docs/88 lo_mode | Reproduced **verbatim** on a real chip, then re-verified fixed on the same chip | **A — real data** |
| docs/82 shot axes | Corpus-invariant tests over real archives | **A — real data** |
| docs/87 always-ask | Synthetic fixtures + the four previously-failing banner tests now passing | **B — synthetic + inference** |
| docs/89 sidebar tools | jsdom against the real `app.js`/`calc.js` + server-rendered markup assertions | **B — no real browser** |
| docs/85 paint cost | jsdom timings (ratios only) + the argument that the qubit grid already ships the same cell count | **C — inference, not measured** |
| docs/78–81 | Their own test suites (see each doc's Pins section) | **not re-verified in this pass** |

### Things that were nearly wrong, and what caught them

Worth knowing because they show where the guardrails are real:

1. **docs/88, first attempt.** The fix made the edit choke points create *any*
   absent leaf. `test_accept_added_leaf_without_flag_fails` failed, whose
   comment states the invariant: *"the flag gates creation, so a generic
   bulk/plot edit can't silently create a mistyped path."* That invariant was
   kept and the fix narrowed to a **declared** create (the server marks a cell
   it rendered "not set"; only such a cell may ask). **The test was right and
   the first fix was wrong** — do not re-widen this without reading docs/88 §2.
2. **docs/85 and the Compare hub.** Flipping pair `default_on` would have
   silently ballooned the Compare hub's pair-row set, because `compare.py` read
   the same flag for a different question. Caught by reading the call sites, not
   by a test; `headline_on` exists for this and `test_pair_columns.py` now pins
   that it stays a *proper* subset.
3. **Customer-name scrub.** While preparing this handoff, real customer/chip
   names (`KRISS`, `SNU`, `arbel`, `QRS`, `Novera*`) were found in
   `docs/83,84,85,88` and `CLAUDE.md` — the repo is deliberately scrubbed so it
   can go public (see `CLAUDE.local.md`, which is the gitignored decoder). They
   were replaced with the placeholder names (`LabA`, `LabD`, `deviceB`, `LabB`,
   `ExampleChip*`). **An auditor should re-grep before any public push.**

### Explicitly NOT verified

- **No real browser was used anywhere in this chain.** There is no headless
  browser on this machine; all client-side proof is jsdom, which does **no
  layout**. Two things therefore rest on inference:
  - the 141-column pair grid's scroll/paint smoothness (docs/85);
  - the popover anchoring actually landing under the trigger, and the topbar
    fallback appearing when the sidebar is collapsed (docs/89).
  Both are ~5 minutes to check by eye at `localhost:5050`, and both are cheap to
  revert (one line in `pair_columns.py`; one commit for the sidebar).
- **docs/78–81 were not re-verified in this pass.** They were completed earlier
  and their suites pass, but the detailed reasoning behind them lives in their
  own docs, not in this reviewer's working memory.
- **No multi-window / multi-process manual test** of docs/80 beyond its test
  suite (which does spawn a real process for the ownership matrix).

---

## 5. Where an auditor should look first

Ranked by blast radius × novelty.

1. **`core/working_copy.py::reconcile_with_live` + its three callers**
   (docs/87). This is the working-copy safety core. The change is small
   (`sync_if_clean=False` on the user path, a new `out` dict) but the
   *consequence* is large: SM now shows stale content until the user pulls.
   Check specifically: that the machine callers (`routes.py` scheduler post-node
   hook, autofit `reconcile()`) really do still adopt — autofit correctness
   depends on it — and that the four banner tests pass for the right reason.
2. **`web/routes.py`** — +1,216 / −139, the largest single surface, touched by
   five of the seven branches. Highest-churn regions: `_activate_quam` /
   `_build_quam_context` / `_reconcile_cached_quam_ctx`, the `/field/edit`
   and `/field/edit-batch` choke points, and the new `/diff*`,
   `/param-history/changes`, `/state/overwrite-live/preflight` routes.
3. **`core/leaf_index.py`** (667 new lines) — writes new tables into the chip's
   **existing** `index.sqlite`. Check the version marker isolation (it must not
   be able to trigger the `param_history` pair-row upgrade) and the
   rebuild-merges property, which docs/83 names as the one place data can be
   lost.
4. **`core/scheduler.py`** (+412 / −73, docs/80) — per-chip scoping plus a
   one-shot legacy migration that copies → verifies → deletes. A migration that
   deletes is worth reading twice.
5. **`core/edit_policy.py`** (docs/88) — the resolver now returns a *different
   path* than before in one case. Confirm it cannot change the target for any
   path that previously resolved normally (`resolve_missing_leaf_path` returns
   `None` unless the parent resolves and the leaf is genuinely absent).

---

## 6. Open decisions and deferred items

Nothing here blocks a merge; all were raised and left deliberately.

1. **A run started from SM's own Experiment Runner still adopts silently.** Only
   writes from outside SM raise the banner. The user was asked directly and
   answered "아니오" (no, don't make SM-launched runs ask). docs/87 §"The one
   carve-out" states it.
2. **Hidden columns' *values* remain outside the search haystacks** (docs/85) —
   labels/keys/sections only. Matching a row on a cell the user cannot see was
   judged worse.
3. **`data` tab compares variable inventories, not array contents** (docs/84).
   A byte diff of HDF5 arrays is not meaningful.
4. **Diff comparison is exact — no tolerance presets** (docs/84). The Compare
   hub keeps those.
5. **Six smaller decisions from the docs/83–84 work** (leaf `value` stored as
   REAL, losing docs/56's int/float distinction; pointers resolving to
   non-numbers still costing a scan; diff source dropdowns offering only the
   loaded chip; the Changes tab being loaded-chip only) were reported at the
   time and left as-is.
6. **docs numbering.** This chain used 78–89 and there was a known collision
   risk with another session's docs/78; the cleanup was deferred. Worth a check
   that `docs/78_type_alert_popup.md` is the intended occupant.

---

## 7. Conventions a reviewer needs

- **Never `git stash` / `git stash pop`** in this repo. The stash stack is shared
  across worktrees and other sessions may be using it. Use a WIP commit.
- **This is a worktree** (`.claude/worktrees/regen-schema-gate`). Run everything
  from here; do not `cd` to `D:\work\state-manager`.
- **`CLAUDE.local.md` is the customer decoder** and is gitignored. Public-facing
  files (docs/, tests/, CLAUDE.md, code) must use placeholders — see §4.3.
- **`CLAUDE.md` is the living architecture index.** Every doc in this chain added
  a paragraph there; they are the fastest way to get oriented without reading
  all twelve docs.
- Real-data tests auto-skip when the archive paths are absent, which is why the
  suite reports ~235 skips.
