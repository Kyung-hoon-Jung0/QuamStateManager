# docs/262: one run instant everywhere, and the Param History re-key (v4)

2026-10-04, step S1 of the state-tracking hub (`D:\work\study\2026-10-03_state-tracking-hub\DESIGN.md`
§2 item 1, §3.3, §3.7 S1). Branch `feat/time-s1`, cut from `integ/batch4` (`0b46c043`).
docs/256 added the primitive (`timefmt.run_instant`, `archive_offset_hint`,
`run_witnesses`) and changed no caller. This step switches the callers and
re-keys stored history.

## What was wrong (measured)

A run was turned into a time in at least four incompatible ways:

| Reader | What it read | Zone it assumed |
|---|---|---|
| `history._entry_timestamp` (the Param History key) | `created_at` cut to `HH:MM:SS` (offset and fraction dropped) + the date | this server's zone |
| `routes._run_ts_stamp`, `routes._exp_entry_for_run` (runs tier, live ingest) | the folder name's date + `HHMMSS` | this server's zone |
| `trend_index.run_instant_ms` (Datasets Trends axis) | the folder digits | encoded as UTC |
| `dataset-virtual.js` "When" column + its sort | `date + 'T' + time` | the browser's zone |

The real Novera9Q archive has `created_at` at -04:00 for all 2,082 runs (docs/256). On this
+09:00 machine its Param History keys were 13 h early. The Trends axis was 4 h off its
real instant. The When column was 13 h off. Backfill and live ingest read different inputs:
backfill read `created_at` digits and live ingest read folder digits. They agreed only
because, on these archives, the two sets of digits are equal (docs/256: 0 s difference on
7,736 runs). A read-only sweep (an Explore agent, 2026-10-03) found about 25 more places of
the same kind. They are listed below as switched or deliberately not switched.

## The one reading

`core/run_time.py` (new) is the I/O edge of `timefmt`. It is the only module that reads a
`node.json` or walks an archive root for an offset. `timefmt` stays pure.

- `resolve(created_at, run_end, folder, *, offset_hint=..., read_node=True) -> (utc_us, quality)`
  - The caller's own evidence comes first. An aware `created_at` or `run_end` decides alone,
    with no file read and no hint. This is the case for every run of the three real
    archives.
  - Next, the run folder's own `node.json`. This covers a scanner stub, whose `created_at`
    is the folder clock (naive), and a standalone entry.
  - Last, `timefmt.run_instant` with the archive's offset. Without one it falls back to the
    machine zone (`assumed_local`).
  - The evidence order is docs/256's product decision.
- `root_offset_hint(root)` is `timefmt.archive_offset_hint` over every run's `node.json`
  under `<root>/<YYYY-MM-DD>/<run>`.
  - It is read only when a run's own evidence is naive.
  - The result is cached on the date dirs' names and mtimes.
  - A `DatasetStore` that has parsed every run of the root publishes its own vote
    (`publish_root_hint`), so the history paths and the Datasets table use the same hint.
  - Sort keys on a request path (`_entry_recency_key`, `_run_age_key`) pass
    `scan_root=False`. They use a published or cached vote, or none, and never walk an
    archive on a share inside a request. Only a scanner stub's naive clock ever reaches
    that branch.
- `instant_of(run)` returns the store-resolved `RunInfo.instant_us`, or `resolve()` when
  there is none. `iso_instant(text)` applies the same rules to one ISO field.
- `snapshot_key(utc_us, run_id)` returns `YYYYMMDD_HHMMSS_NNN`: the UTC second of the
  instant (floor), with NNN = run id mod 1000. This is the format every existing key
  already has.
  - [derived] For a run recorded in the machine's own zone, the old reading gave the same
    UTC second. Stored +09:00 history on this +09:00 machine is therefore byte-identical
    (checked below).
- `timefmt.node_times(created_at, run_end)` (the node.json shape) and `timefmt.utc_stamp`
  (floor to the second, integer arithmetic) are pure helpers.

`RunInfo` now carries `created_at`, `instant_us` and `instant_q`.
`DatasetStore._settle_instants` dates every run against the archive's own vote. When the
vote changes, every naive run is re-dated as a replaced RunInfo, counted in
`generation`/`exp_gen`, so the trend index and the other RAM consumers see the change.
`_STORE_CACHE_V` goes from 2 to 3: a v2 cache row has no instant. The cost is one cold scan
per archive after the upgrade.

## Callers switched

| File:line (this tree) | Before | Now |
|---|---|---|
| `core/history.py:6906` `_entry_timestamp` | `created_at` digits in the server zone | `run_time.resolve` → `snapshot_key`. The no-evidence-at-all fallback (`_undated_entry_stamp`, :6938) is the old midnight/epoch rule. |
| `core/history.py:6706, 6743` backfill order | `(date_str, run_id, created_at string)` | the key itself (instant order), so two archives in different zones interleave correctly and the leaf index sees them in order |
| `core/history.py:6865` `_run_key_slot` (+ `_index_run_slots` :6801, `_same_run_holder` :6825) | a taken stamp always meant "already ingested" | a stamp held by another run is a **collision** (see below) |
| `web/routes.py:33021` `_exp_entry_for_run` | fabricated a naive ISO from the folder digits | hands over the run's own `created_at` / `run_end`, the same input the backfill has |
| `web/routes.py:10431` `_run_ts_stamp` (runs tier, exp-ingest queue key) | folder digits in the server zone | `snapshot_key(instant_of(run))` |
| `web/routes.py:10453` `_run_recency` (new), used at :33209 (drift-scan newest 5), :33974 (`/datasets/poll` latest + count), :38675 (`_latest_run_info`, scheduler attribution), :39323 (autofit `rescan_and_list_runs`) | `(date, time)` folder strings compared across folders | the instant |
| `web/routes.py:32765` merged Datasets list order | `(date, time, id)` | `(t, date, time, id)` |
| `web/routes.py:33961` `/datasets/poll` `since_t` | `since_date`/`since_time` only | also accepts the acknowledged run's instant (`since_t`, ms); the payload carries `t`. The old pair stays accepted. |
| `web/routes.py:26478` `_run_age_key` (diff column order) | `(date, time, run_id)` | instant (store value → `resolve` → a bare `date`/`time` read in the machine zone) |
| `web/routes.py:29353` `_entry_recency_key` | folder digit strings | instant |
| `core/trend_index.py:218` `run_instant_ms` | folder digits encoded as UTC | `RunInfo.instant_us // 1000` |
| `core/trend_index.py:411` merged `_rows` order | `(date, time, run_id)` | `(t, run_id)`, undated first |
| `core/dataset.py:769, 1145, 265, 1892` | — | instant resolved at parse, settled per scan, compact row `t`/`tq`, `get_run` carries it |
| `core/scanner.py:1679` standalone entry | naive `fromtimestamp(mtime)` (read as local by the key, as UTC by `to_utc`) | aware (`.astimezone()`), same digits |
| `static/dataset-virtual.js:533, 543, 562` When column, its title and sort, the M/D of old runs | `date+time` in the browser zone | `t` in the viewer's zone (`SnapTime`), title = `SnapTime.display` + "acquisition PC clock <date time>" |
| `static/dataset-virtual.js:1815` delta poll "is this arrival new" | folder digit strings | `t` |
| `static/app.js:18264, 18271, 18288, 18348` Trends axis, hover title, zone-change redraw, caption | raw ms (Plotly draws UTC); caption "as its folder names it" | `SnapTime.axisValue(t)`; caption "x = when each run saved its state, in <zone>" |
| `static/app.js:19772` new-run poll stamp | `"date time"` string | `t` when sent |
| `templates/_datasets.html:50` tz-note title | "SM does not convert time zones" | names the When column as the exception |

Switched by a helper agent (its report is summarised; every site has a pin in
`tests/test_run_instant_callers.py`):

| File:line | Now |
|---|---|
| `core/story.py:129-193` | `row_instant` / `run_epoch` / `start_epoch` / `with_instants` (the old `_epoch` is gone) |
| `core/story.py:635` `build_day` | journal attach window from `run_start` (offset kept), else the instant |
| `web/journal_routes.py:187` `_run_when` | the true start instant, filed by the journal's own server-local convention |
| `core/agent_runs.py:1071` `_attribute`; `web/agent_api.py:804, 1038, 1638`; `web/chat_api.py:323` | the run's instant against `time.time()` / hook stamps / session epochs |
| `core/autofit/realbackend.py:281` `_parse_ts` | `iso_instant`: a naive `run_start` is read in the machine zone, no longer as UTC |
| `core/value_writer.py:128` `_parse_iso` | `iso_instant`, quality `offset` only. This is provenance, so a naive `run_end` stays `None`. |

## Not switched (named, not hidden)

- **Display of the acquisition PC's wall clock**, as docs/244 decided:
  - the Date/Time columns and the date tabs;
  - the run detail header (labelled "acquisition-PC local");
  - the Calibration log card times;
  - the strings of `_entry_snapshot_parts` and `compare_sources` labels;
  - Parameter Differences titles and the CLI listing.
- **Day buckets by folder date**: the dataset date filter, and the Calibration log's day.
  `story.build_day` selects runs by folder date, but journal lines and undo units by
  server-local day. For a -04:00 archive on a +09:00 machine, a run after 11:00 lab time
  has its claim and journal lines on the next day's page. Which day a run belongs to is a
  product decision.
- **A naive `run_start`** in story/journal is read in the machine zone, not in the archive's
  offset.
- **Single-archive offline tools**: `autofit/chainwalk.order_key`, `pathreplay`. Their
  ordering is within one archive, where the folder clock is monotonic except across a DST
  fall-back hour.
- **Sidebar ordering** (`scanner`: run id, then the raw `created_at` string).
- **`run_witnesses` and the 30-minute "ask once per project"** (docs/256 S1 handoff, with the
  coordinator's note on when a witness is a witness). Not wired in this step: no caller
  passes mtime or first-seen yet, and nothing persists the ask.

## The re-key migration (v4)

`core/history_rekey.py`, run at startup after v1–v3 (`web/app.py`).

- **What moves.** A snapshot dir is moved when all of these hold:
  - its `meta.json` is an experiment row (`kind == "exp"` or `trigger == "experiment"`);
  - it names its run folder;
  - that run's `node.json` dates it with evidence (quality `offset` or `archive_offset`);
  - its key differs from that instant's `snapshot_key`.

  Nothing else is touched. Two cases keep their key and are listed in the journal:
  - a run folder that cannot be read now (`unresolved`);
  - a run dated only by assuming this machine's zone (`kept_assumed_local`). The old key
    rested on the same kind of assumption, so moving it would trade one assumption for
    another.
- **Plan.**
  - `target = snapshot_key(instant, run_id)`.
  - A target held by ANY snapshot dir or index row (`param_history` or `leaf_snaps`) is a
    **collision**. The snapshot then takes the first free `target + "01"`, `"02"`, …
    (`run_time.collision_key`). That is five digits, so it never equals an ingest key
    (3 digits) or a capture stamp (4 digits, `_ts_stamp`), and it sorts right after its
    base. `_HIST_TS_RE` accepts it.
  - A planned target is never handed out twice, and old names are never reused within a
    plan. So there are no rename chains, and two snapshots are never merged.
- **Apply (per chip dir).** The journal is written **before** anything moves. It lives at
  `<instance>/history_rekey_v4/<chip>.json`, and holds every move with the original
  `meta.json` bytes (base64), their sha1, and atime/mtime. Then, in order:
  1. `os.rename(old, new)` for each move;
  2. rewrite `meta.json` with the new `timestamp` and `rekeyed_from`;
  3. relabel the index rows with the docs/200 machinery (`HistoryManager._relabel_index_rows`);
  4. in one transaction, `_cp_invalidate` and `leaf_index.mark_dirty`. A 13 h move can
     change the ORDER of snapshots, and both change-point tables depend on order;
  5. delete the snapshot manifest (the next listing rebuilds it from the dirs);
  6. rebuild the leaf index (`leaf_index.rebuild`), synchronously and only for chips that
     moved.
- **Crash safety.** Every step checks before it acts:
  - rename only when old exists and new does not;
  - rewrite only when the bytes differ;
  - a relabel of rows already moved is a no-op.

  A journal left `applying` or `reverting` is resumed on the next start, whatever the flag
  says. A chip whose re-key stops leaves the flag unwritten (status `partial`), and the
  next start retries it.
- **Idempotent.** The plan maps a snapshot that is already at its instant's key (or one of
  its collision keys, `run_time.is_key_of`) to itself. A second run moves nothing and
  writes nothing.
- **Gate.** `<instance>/migrated_v4.flag` (`migrated` / `reverted`). The step is deferred,
  with no flag written, while another SM process is registered on the instance
  (`instances.peers`) or another process holds `history/.rekey_v4.lock`. Planning opens
  the index read-only, with `immutable=1` when there is no `-wal`, so a plan that moves
  nothing creates no sidecar file either.
- **Revert.** `revert_history_rekey_v4(instance)` walks every journal backwards:
  - it renames each dir back;
  - a `meta.json` nothing edited since gets its original bytes and mtime back;
  - an edited one (a label, a pin) keeps the edit and only its stamp goes back (reported as
    `edited`);
  - it relabels, rebuilds the leaf index, and sets the flag to `reverted`.

  A restart then does not re-apply; `migrate_history_rekey_v4(force=True)` does.
- **Live ingest after the step** uses the same `_run_key_slot`. A re-ingest of a run stored
  under a collision key finds it there.
  - Run identity is run id + experiment name + the last two components of the run folder
    (`<date>/<#N_name_HHMMSS>`), never the full path. [derived] The same run reached
    through two spellings of its root is one run.
  - Side fix: a snapshot with no `param_history` rows (no tracked property) used to be
    rewritten and then deleted by the dedup branch on every re-ingest. It is now found by
    its dir.

## Checks

All runs used copies under the scratchpad. `D:\work\statemanager\instance` and
`D:\work\Customer_Codes` were only read.

1. **Real instance history (+09:00 data on this +09:00 machine).**
   - Copy of `instance/history`: 4 chip dirs (`CQT_20Q`, `KRISS_CZ`, `demo_verify_128`,
     `superconducting`), 22 snapshots, 15 of them `exp` (KRISS_CZ, from two archives).
   - Migration: `migrated`, **0 moves**, 0 unresolved, 0 kept.
   - After the migration, **every one of the 82 files is sha256-identical to the original**,
     `index.sqlite` included, and the 27-dir set is identical.
   - The only new files are `<instance>/migrated_v4.flag` and nothing else.
2. **-04:00 fixture.**
   - Built from 58 real Novera runs (the last 10 of 2026-04-02, all 38 of 2026-04-03, the
     first 10 of 2026-04-04, copied to the scratchpad). They were ingested by the **base
     commit's own code** (`git archive 0b46c043`) on this machine: 20 snapshots (38 runs
     were content duplicates) and 2,921 leaf change points.
   - Migration with this tree:
     - **20/20 moved by exactly +46,800 s (13 h)**, all with quality `offset`;
     - the dir set is exactly the old set relabelled;
     - files changed: only the 20 moved `meta.json` and the manifest;
     - `param_history` is equal after the relabel;
     - **the leaf change-point multiset is equal after the relabel (2,921 = 2,921)** and
       equal ignoring ts; the leaf index is not dirty after;
     - every moved key equals the key the new ingest code gives that run (0 mismatches).
   - A second run (`force=True`): 0 moves, and the history is identical.
3. **Revert of (2).** 20 reverted, 0 edited.
   - **Every non-index file (62) is sha256-identical to the pre-migration copy**, the
     manifest and every `meta.json` mtime included.
   - The index is logically identical: `param_history` rows, `leaf_snaps`, the leaf
     change-point multiset, the dirty flag. Its bytes differ, as any SQLite rewrite's do.
4. **Backfill vs live ingest of the same run give the same key.** Pinned:
   - `test_the_three_paths_give_one_key`: backfill entry, live entry and runs tier, for
     every fixture run;
   - `test_ingesting_the_same_run_both_ways_stores_it_once`, in both orders;
   - `test_the_live_entry_carries_the_runs_own_created_at`.
5. **Real headless Chrome over CDP.**
   - Setup: rig `D:\work\sm_qa_rigs\agent\rTime1` (made by `make_rig.py`, port 5121, CDP
     9441, sandboxed `USERPROFILE`/`HOME`). Its `srv.bat` points at this worktree. A copy of
     the 58 Novera runs is added as a second Datasets folder beside the rig's +09:00 data.
     The viewer is Asia/Seoul.
   - Journey: **15/15 checks pass, 0 console errors.**
     - Runs #1172 (2026-04-02 21:20:42 -04:00), #1181, #1182 and #1194 show When `4/3`
       (the viewer's day; the folder says 04-02 for the first two).
     - Their titles read `2026-04-03 10:20:42 (UTC+9) · acquisition PC clock 2026-04-02 21:20:42`
       and the equivalents.
     - Sorting by When keeps the instant order across the -04:00 midnight.
     - Open → close (×) → away to `/qubits` → Back → reload: the table and its When
       column are intact.
   - Screenshots (read): `scratchpad\time1\browser\01_datasets_when.png` … `05_reload.png`,
     and `06_when_vs_acquisition_clock.png`. The last shows Date `04-02` / Time `21:20:42`
     beside When `4/3`, plus a probe printing each When title verbatim.
   - Datasets › Trends on the same archive (`07_trends_axis.png`):
     - `/trends/series` sends #1182 at `1775189268000` (= 04:07:48Z);
     - the chart's x is `2026-04-03T13:07:48`;
     - the ticks read 13:30 … 15:00 (Apr 3, 2026);
     - the caption reads "x = when each run saved its state, in Asia/Seoul.";
     - 0 errors.

## Pins

- `tests/test_one_run_instant.py`: **31 tests**. They cover:
  - the key reads the instant (6);
  - backfill = live = runs tier (4);
  - collisions (3): two runs in one second with one suffix are both kept; a snapshot with
    no `param_history` rows is never rewritten; a snapshot the index has not seen (its
    rows deleted) is never overwritten or deleted. That last case was a real data-loss
    path: the old logic deletes the dir, and the mutation that restores it turns red with
    the dir gone;
  - the migration (13): 13 h move, same-zone byte identity, second run writes nothing,
    collision, byte-exact revert, a kept label edit, the journal before the first move,
    killed apply and killed revert resuming, an unreadable run folder, assumed-local kept,
    deferral while a peer window is open;
  - Datasets/Trends/recency (4);
  - the poll (1);
  - the jsdom client selfcheck, which runs from this file.
- `tests/run_instant_client_selfcheck.cjs`: **17 assertions**, against the real
  `dataset-virtual.js` + `SnapTime` and the real Trends shell. It runs under `TZ=Asia/Seoul`.
  - When text/title/sort by `t`;
  - an old run's M/D in the zone chosen in Settings;
  - a delta's arrival judged new by instant;
  - the Trends x in the viewer zone, and its caption.
- `tests/test_run_instant_callers.py`: **21 tests** (helper agent). They cover story,
  journal_routes, agent_runs, agent_api, chat_api, realbackend and value_writer, with the
  server zone forced to +09:00.

## Mutations

Each mutation was applied to its own copy of the tree under the scratchpad, never to the
worktree, and run against `tests/test_one_run_instant.py` (plus `tests/test_trend_index.py`
where named). The final sweep ran on the final tree, **32/32 RED**, all assertion
failures. The counts are failed / passed out of the 30 or 31 tests in the file.

| Mutation | Result |
|---|---|
| drop the offset (`timefmt._run_iso` keeps the first 19 chars) | RED 25 |
| naive-as-local (`resolve` ignores the archive hint) | RED 2 |
| naive-as-local in the dataset store (vote never re-dates) | RED 1 |
| folder-as-UTC (`resolve` reads the folder clock as `+00:00`) | RED 3 |
| folder-as-UTC on the Trends axis (old `run_instant_ms`) | RED 4 (with trend_index) |
| trend merge by folder digits | RED 2 |
| live entry back to the folder digits | RED 1 |
| `_run_ts_stamp` back to the folder digits, server zone | RED 1 |
| `_entry_timestamp` skips the run's own node.json | RED 1 |
| **collision merge at ingest** (any taken key = already ingested) | RED 1 |
| a stamp the index does not know is free (the dir ignored) | RED 1 |
| **skip the relabel** | RED 2 |
| **non-idempotent second run** (`is_key_of` removed) | RED 4 |
| **collision merge in the migration** (occupied check removed) | RED 1 |
| journal not written before the moves | RED 2 |
| no resume of a pending journal | RED 2 |
| revert without the byte restore | RED 3 |
| revert drops an edit | RED 1 |
| assumed-local runs moved | RED 1 |
| plan opens the index `mode=ro` (creates sidecars) | RED 2 |
| no deferral while a peer window is open | RED 1 |
| `_run_recency` by folder digits | RED 2 |
| `_run_age_key` ignores the instant | RED 1 |
| `_entry_recency_key` by digits | RED 1 |
| `/datasets/poll` ignores `since_t` | RED 1 |
| compact row without `t` | RED 1 |
| JS: When back to `date+time` in the browser zone | RED 1 |
| JS: When title without the instant | RED 1 |
| JS: old run's day in the browser zone | RED 1 |
| JS: delta "new" by folder digits | RED 1 |
| JS: Trends caption back to "as its folder names it" | RED 1 |
| JS: Trends x back to raw ms | RED 1 |

Seven mutations were GREEN in earlier sweeps, and each one pointed at a real gap in the
pins:

- "live entry back to folder digits" and "skip node.json" were masked by the
  sibling-offset fallback;
- "assumed-local moved" had no fixture;
- "age key" passed on a run-id tie-break;
- "a stamp the index does not know is free" had no fixture once the leaf row named the
  run;
- two runs produced no pytest output, a harness hiccup; both were RED on rerun.

The pins were strengthened as follows, and every one of the seven then went RED:

- a single-run root with `node.json` gone;
- +09:00 siblings around a -04:00 run;
- a UTC-server-ingested naive history;
- a larger run id on the newer-by-digits run;
- an index with the snapshot's rows deleted.

Helper agent's sweep on its own file: 13/13 RED (listed in its report: each of the seven
sites back to the folder-digit/server-zone reading, the adapter dropping instants,
realbackend naive-as-UTC, and value_writer accepting naive / dropping the offset).

## Existing tests edited (each one encodes the removed reading)

- `tests/test_trend_index.py::TestPayload::test_runs_carry_the_real_instant_and_a_uid`
  expected `calendar.timegm(folder digits)`, which is folder-as-UTC, exactly the reading
  this step removes. It now expects the folder clock read in the machine zone (these
  fixture runs carry no `created_at`, so the quality is `assumed_local`, also asserted).
- `tests/test_trend_index.py::TestStaleness::test_a_folder_change_and_a_same_chip_merge`
  expected the merged order to equal the folders' `(date, time, run_id)` strings. It now
  expects instant order (undated first). The fixture's `hhmmss = f"{rid%24:02d}{rid%60:02d}00"`
  produces hours like `30:30:00`, which no clock dates.
- `tests/trends_view_selfcheck.cjs` A: `x === runs[i][1]` (raw ms, drawn as UTC digits)
  now reads `x === SnapTime.axisValue(runs[i][1])`. The concrete zone is pinned in
  `run_instant_client_selfcheck.cjs`.

`tests/test_customer_scroll_and_order.py` (two `_run_age_key` pins with bare
`date`/`time` dicts) was **not** edited. The code keeps reading such a row's wall clock
(machine zone), and those pins pass.

## Tests run

Every `tests/test_*.py` mentioning history, snapshot, run_ts, `_entry_timestamp`,
trend_index, run_instant, timefmt or dataset, plus trends_view, sync_badge, run_watch,
pane_state, time_display, story/journal/agent_runs/value_writer and misc_ui: 225 files,
env `cqt`, 8 parallel shards.

- Result: **5,880 passed, 159 skipped, 2 failed**. Neither failure is from this step:
  - `test_autosync_merge.py::…::test_the_shipped_client_behaves` is a jsdom selfcheck.
    `node tests/autosync_merge_selfcheck.cjs` passes on this branch AND on the base
    (29/29). docs/258 records it as a load flake.
  - `test_web.py::TestWorkspace::test_sidebar_filter_multi_token_with_date` also fails on
    the base `0b46c043` (a `git archive` of it, run in isolation in the scratchpad).
    `test_web.py::TestWorkspace` passes on this branch when run alone.
- A first full run caught three time-reading pins and one text-scan pin:
  - the two `test_trend_index.py` pins and the trends_view x pin, edited as above;
  - `live_wake_selfcheck`'s text scan of `pollForNewRuns` (3,000 characters). The new
    stamp logic moved into two helpers outside the function; nothing in the test was
    edited.
- Files touched after the full run were re-run: the history/dataset/trend/ingest/story
  files, the pins, the dataset JS selfchecks (7, all green) and `test_web.py::TestWorkspace`.

## Open items

1. **Day of a run** (Calibration log, date tabs, date filter): folder date vs viewer day vs
   server day. This needs a product decision. The agent found the concrete mismatch
   described under "Not switched".
2. **`run_witnesses` and the once-per-project 30-minute ask**: wiring and persistence are
   still to do. Pass `first_seen` only for runs SM saw arrive live, and treat an mtime gap
   on a copied archive as a copy.
3. **Index-only experiment rows** (a pruned snapshot whose `param_history` rows survive)
   keep their old key. The plan counts none on the real instance. Auto-prune starts only
   above 100,000 snapshots.
4. **Two runs of two archives with the same run id, experiment, date dir and HHMMSS, in
   the same UTC second**, are taken as one run at ingest (identity without the root). The
   content-hash dedup still keeps distinct states apart only when the first one's state
   differs.
5. **A revert after new ingests** can find a run's old (wrong) key taken by a new snapshot.
   The revert then stops (`both exist`) and its journal stays `reverting`. Not reachable
   without a manual revert.
6. The leaf rebuild after a re-key runs synchronously at startup, and only for chips that
   moved. A 1,590-snapshot chip took about 70 s to rebuild in docs/208.
