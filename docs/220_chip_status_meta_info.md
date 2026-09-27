# docs/220 — Chip Status: each metric says when it was measured; Overview tiles jump to their panel

2026-09-26/27, branch `w7/csmeta` (4 commits), merged into `integ/w7` as `9dfc0f2` (branch head
`afabe15`). Queue items 4 (panel metadata) and 10 (tile jumps).

## 1. What shipped

**Item 4 — metric metadata.** Every per-metric panel (1Q gate fidelity, readout GE/GEF, T1/T2,
frequencies, amplitudes, 2Q RB per gate) tells you when each value was last measured.
- Hover card: last measured / changed time, the run that wrote it (e.g. "run #142 · 11 Rabi"), the
  snapshot id, the lab's own `updated_at`, and for 2Q RB the node's `*_load_id`.
- A per-panel **Show Meta Info** checkbox right of S/M/L puts a date·run tag in every tile and a
  summary line under the panel's stats. Saved per panel in localStorage `quam_chip_meta_panels`.
- Data: `GET /topology/metric-meta` folds the docs/83 change-point index through the pure
  `core/metric_meta.py` (panel key -> the state leaves it reads, pointers followed, a readout fidelity
  counts its whole confusion matrix). No server cache. The page fetches lazily (first hover, or a panel
  built with its toggle on) and re-asks after 15 s or while the index is rebuilding (`updating`).

**Item 10 — tile jumps.** Overview tiles with a panel jump to it with a smooth scroll (T1 to the T1
panel; SRB/IRB to new `data-rb-heading` sub-headings), through the sub-nav's own path and JumpGuard
(`sel:<tab>:<selector>`, `baseView`). Click and Enter/Space share `_ovJump`; the ⋮ menu keeps its own
click. Calibration Age and 2Q Gate Length stay inert. The jump map `OV_JUMP` is keyed by tile id.

**Honesty rules (final form, after two verifier rounds):**
- "Unchanged since history began" (a `≤date` tag) only when every leaf's single change row sits at the
  OLDEST snapshot the index holds (`oldest`, read on the same connection).
- A single row later than that is `appeared`: "First measured" (a run wrote it) or "First recorded"
  (no run), a plain date tag.
- Every entry carries `matches_current` (every leaf the panel reads, incl. a whole matrix or a 2Q RB
  block, vs its newest history value); a number history never held says "not in history".
- "Measured" only when a run wrote the value; run-less changes are counted "recorded without a run";
  a run number is never made up; the tile tag is an absolute date, never an age.
- On a truncated index, what cannot be vouched for is `{incomplete: true}`: tile "not indexed", hover
  "Not dated: this chip's change-point index is incomplete...", panel "N not dated (index incomplete)".

## 2. What was measured

Real headless Chrome at 1600x950. krs5 = KRS_5Q copy (15, later 16 snapshots); big30x with 200 snapshots.

| check | before the fix | after the fix |
|---|---|---|
| Enter jump, t2ramsey tile, fresh page (krs5) | 95 px, 5/5 (verifier, at `c98c44c`) | 67 px, 5/5 per mode click / Enter / Space (re-verifier, at `4acadf4`) |
| panels whose Show Meta Info is visible | krs5 9/19, big30x 9/109 (re-verifier, at `4acadf4`) | 19/19 and 109/109 (implementer, at `afabe15`) |
| raw-snapshot oracle mismatches, big30x | 125/330 (re-verifier, at `4acadf4`) | 0/330: 255 dated, 75 not dated (implementer, at `afabe15`) |
| raw-snapshot oracle mismatches, krs5 | 0/55 (re-verifier, at `4acadf4`) | 0/55, and 0/55 after journey M4 (implementer, at `afabe15`) |

Independent verifier, at `4acadf4`, medians (ms):

| | first hover card | warm hover card | toggle on / off | S/M/L | jump click / Enter / Space |
|---|---|---|---|---|---|
| krs5 | 144 [107-166] n5 | 32 n95 | 39 / 29 | 36 (meta off), 30 (meta on) | 41 / 34-42 / 36-46 |
| big30x | null (>8 s) 5/5 | 69 n545 (max 1288) | 80 / 26 | 94 full run, 60 meta on, 90 density-only | see D3 / 48 / 177 |

Matched before (integ/w7) vs after, S/M/L density-only n21: krs5 27 vs 28 ms, big30x 91 vs 90 ms;
big30x page-open `/state/live-diff` 9054 vs 8836 ms (meta on). The verifier reports no regression.
`metric-meta` cost on big30x at `afabe15` (implementer): warm 0.4-0.9 s (median ~0.6), cold first
call after a server start 3.1 s, of which the 2 snapshot parses are ~0.5 s.

## 3. Mechanism (commits)

- `b3b4b9e` — `core/metric_meta.py`, the route, `ChipStatus.metaInfo` in `chip-status.js`, the tile
  jumps, the selfcheck and the journey.
- `c98c44c` — the journey's M4 cold recompute passes the lab stamp too, as the page does.
- `4acadf4` — first verifier round: `leaf_field_series_many(origin=)` reads MIN(ts) of `leaf_snaps` on
  the same connection; `newest_change(series, paths, oldest=, current=)`; `current_values` feeds
  `matches_current`; the jump key is marked `_csJumpKey` and the guard's `cancel(ev)` ignores it; the
  panel line counts run-less changes separately.
- `afabe15` — second verifier round: the hide rule is keyed on
  `.topo-section[data-density-panel]:not(.topo-meta-on)` (the panel itself); `metric_meta.verify_truncated`
  plus `origin.truncated` / `ts_list` from `history.leaf_field_series_many`, `incomplete_index` on the route.

## 4. Found by the independent verification and fixed

**Round 1 (verifier at `c98c44c`, verdict DEFECTS), fixed in `4acadf4`:**
- **P1-a first/appeared** — the leaf index writes no row for a null leaf, so a value written later had
  one row and read "Unchanged since history began". Repro: q1 `confusion_matrix` None in the first 11
  snapshots, route `{ts 20260907_123829_142, first: true}`; after an outside write + Take live the T1
  panel line read "5 unchanged since history began (of 5) · history: 16 snapshots". The randomized
  oracle pin had the same flaw built in (it dropped leading None). After: `{first: false, appeared:
  true, run 142}`.
- **P1-b subtree matches_current** — subtree metrics never compared the value on screen: q1 readout
  shows 92.25% from a matrix no snapshot held (history 52.6% or None), yet borrowed `≤09-07 21:38 · #142`.
- **P3 keyboard jump** — Enter landed 95 px vs a click's 67. The verifier's hint (focus scrolling) was
  not the cause: the Enter keydown bubbled to the pane and the JumpGuard read it as the user taking over.

Re-verification of `4acadf4` found P1-a fixed on krs5 (55/55 against a raw-snapshot oracle, through
SM edits + undo, outside write + Take live, and a chip switch and back), P1-b fixed in the data but
invisible in the UI (D1), P3 fixed. **Round 2 (verdict FAIL), fixed in `afabe15`:**
- **D1 (P1)** — Show Meta Info invisible on every panel nested in another `.topo-section` (all 2Q RB,
  1Q gate fidelity, the three readout GE panels): the hide rule matched the wrapper sections
  `#sec-fidelity`, `#sec-fidelity-1q`, `#sec-readout`. Journey M5b and the selfcheck read `textContent`
  only, so they were vacuous.
- **D2 (P2)** — big30x's change-point index is truncated (`leaf_meta truncated=1`; caps 50,000 rows per
  snapshot, 200,000 leaves walked); the route published cap artifacts as facts. Example: q19 T1
  "First measured ... run #1001" although the oldest snapshot already holds the value; q1-q3 "No change
  of this value is on record" although the files show changes. After: each lone late row is checked
  against the snapshot just before it (at most 2 snapshots per request, cached per snapshot); the rest
  is `incomplete`.
- **D3 (P2)** — the verifier: on big30x after F5 restores a deep scroll, a mouse click on the ro_ge tile
  landed 1188 px off on the wrong tab (4/4). The implementer's CDP trace: no code defect; the harness's
  scripted `scrollTop=0` is not user intent, the F5 restore guard kept re-landing, and the click hit the
  1Q panel under the pointer, not the tile. With a real wheel back to the tiles: 68 px on the Read. Fid.
  tab, 3/3. Pinned as journey M7a (wheel + click) and M7b (wheel + Enter).
- **D4 (P3)** — two mutations survived (`_num_eq` at 1e-2 tolerance; `appeared` ignoring "lone row is
  the newest"); both now caught.
- **D5 (P3), documented, not changed** — first metadata on big30x arrives ~9 s after open (verifier:
  metric-meta 9007/9815/9310/11476/8638 ms, suggesting a wait on live-diff's lock). Implementer: not a
  lock; the process is saturated after a page open (two live-diffs at 15.3 s and 20.1 s, `/state/drift`
  21 s, `/qualibrate/subnav` 27.8 s; GET `/static/style.css` took 4.3 s during one live-diff). Pre-existing.

No independent verifier run on `afabe15` is in the sources; its numbers are implementer-measured.

## 5. Pins

- `tests/test_chip_metric_meta.py`: `TestPaths`, `TestFold` (incl. `test_a_near_miss_value_is_not_history`,
  `test_appeared_only_when_the_lone_row_is_the_newest_change`), `TestTruncated`,
  `test_a_value_null_in_the_first_snapshot_is_never_since_history_began`,
  `test_an_edited_matrix_cell_is_not_in_history`, `test_a_random_event_sequence_never_serves_a_stale_answer`
  (oracle over every snapshot file, null -> number -> null events, Take live each step),
  `test_a_truncated_index_never_dates_what_it_cannot_vouch_for`, `test_the_page_selfcheck`.
- `tests/chip_meta_info_selfcheck.cjs` (28 -> 35 -> 41 assertions): T5c-T5f, T5i, O3d-O3e, D5c, and D9
  (loads the shipped `style.css` into jsdom and checks the computed display of a nested RB panel).
- Journey `tests/browser/journeys/chip_status_meta.cjs` M1-M7: 26/26 (`b3b4b9e`), 32/32 (`4acadf4`),
  krs5 38/38 at `afabe15`; big30x 37/38 (M1b's regex rejected the new "Not dated" card, the card was
  correct; regex widened afterwards, not re-run). M6a checks laid-out height, 19/19 krs5, 109/109 big30x.
- Test runs: `test_chip_metric_meta.py` 8 -> 12 passed; the 17 files referencing `chip-status.js` 517
  passed (implementer, verifier twice); 641 passed / 1 skipped at `4acadf4`; 137 passed / 1 skipped
  (6-file set) and 217 passed (7 files touching metric-meta or `leaf_field_series_many`) at `afabe15`.
- Mutations: `b3b4b9e` 10/10 red (implementer); `4acadf4` 8/8 red (implementer); the re-verifier's 4
  mutations caught 2 and missed 2 (D4); `afabe15` lists 11 mutation entries, all red (implementer).

## 6. Integration

`history.leaf_field_series_many` conflicted with w7/trends: csmeta's `origin` fill (oldest, truncated,
ts_list) kept, the series read stays trends' shared `leaf_series_on`; `chip-status.js` keeps csmeta's
jump attributes and toggle under smallui's no-bare-arrow title rule. See docs/224.

## 7. Open / not done

- Metadata knows only what snapshots captured: after Take live, every value history never held reads
  "First recorded" at that snapshot; on chips whose snapshots missed a field, many cells show a recent date.
- On big30x 75/330 entries (all panels of q1-q3 and others) are honestly "not indexed"; dating them needs
  an untruncated leaf index (WALK_CAP 200k / SNAP_ROW_CAP 50k) or a background full scan (~47 s).
- `matches_current` for subtree metrics is as of the last fetch (re-asked after 15 s).
- The F5 restore guard re-lands until a wheel, key, touch or press; a scripted scroll does not cancel it.
- The "recorded without a run" line (`4acadf4`) was not re-checked in real Chrome at that commit.
- `OV_JUMP` is hard-coded: a new built-in tile stays inert until added.
- An edit in another window updates neither the value nor its tag until reload (same on integ/w7).
