# 275: The ledger keeps itself current

S5 of the state-tracking hub, on `feat/hub-sync` (base: S4 `e38602f9`).
Design source: `D:\work\study\2026-10-03_state-tracking-hub\DESIGN.md` §3.1-3.4 and §3.7 S5,
read on 2026-10-04, with [269](269_hub_rules.md), [270](270_hub_ledger_builder.md) (and its
review) and [271](271_every_sm_write_is_recorded.md).

Binding user decisions applied here:

- Run by run: a run's change = its saved `quam_state` vs the ledger state just before it.
- SM writes are exact facts (S4). They are never re-diffed.
- No fit-vs-human split. No copied-state detection.
- Several data folders of one chip merge into one timeline by instant. A run's order key is
  its `run_instant` (`core/run_time.py`, docs/256/262).

## What changed for the user

S3 built a ledger offline; S4 recorded SM's own writes into it. Runs reached it only through
the offline CLI. Now:

- **Opening a chip catches its ledger up.** Every data folder registered to the chip is listed in
  the background. Every run folder the ledger does not hold is ingested. Runs made while SM was
  closed land with no page visit.
- **A new run lands within one watcher tick** (0.5 s poll + one slice), off the request thread.
- **A run that arrives late is inserted at its place**, not appended: a second folder, a late
  copy, or a run that finished while SM wrote.
- **A run folder rewritten after ingestion is re-diffed in place.** It is found by a stat
  watermark, never by re-reading every run.
- **`/hub/status` and `Hub.status()`** say `building` (n/N) or `ready`. `Hub.require_ready()` raises
  `hub_sync.Building`, a `ramcache.Warming`, so no surface can present a partial ledger as
  complete.

## Which folders belong to a chip (one function)

`hub_sync.roots_for_chip` is the one rule. `routes._hub_roots_for(ctx)` feeds it from what SM
already knows. Strongest source first, existing directories only, deduplicated by `fs_key`:

| # | Source | Where it comes from |
|---|---|---|
| 1 | `declared` | the chip's own `extras.data_folder`, bridged and existence-checked by `_adopt_extras_data_folders` |
| 2 | `project_storage` | the qualibrate project storage location of the chip's project scope (`qualibrate_config.project_storage`) |
| 3 | `project_roots` | folders recorded for that project in `instance/project_dataset_roots.json` |
| 4 | `decided_same` | a Datasets (workspace) root the user has declared to be this chip's data: a `"same"` entry in `chip_decisions.json` for (chip, the root's data-folder label) |

**Decision: never every workspace root.** One workspace often holds several chips' data. Syncing
a foreign folder would put every one of its runs into this chip's timeline. A run inside a
synced folder whose identity disagrees is **kept and flagged `CHIP_UNCERTAIN`, never dropped**
(the S3 rule). The chip identity it is compared with is now the **open chip's own** (extras name +
fingerprint), seeded into `meta.chip_identity` when the ledger has none yet. Before, it was
whichever run the folder happened to start with. An existing ledger keeps its stored identity.

## Placement by instant, local repair

### Invariants

All four are pinned in `tests/test_hub_sync.py`. `hub_sync.verify()` checks I1, I2 and I4 on any
ledger, plus that every run has a location:

- **I1:** `ord` order equals the canonical order `(t_utc_us, root folder_key, run_id, experiment,
  rel_path, eid)`. An SM event has an empty root key, so it sorts before a run of the same
  microsecond.
- **I2:** a run's rows = S2 `diff(state_at(previous good event), its saved state)`. An SM event's rows
  are what SM wrote and never change.
- **I3:** an SM event with no anchor replays from its predecessor. Whenever that predecessor would
  change, the SM event's exact state is **anchored first** (`HubStore.ensure_anchor`).
- **I4:** a checkpoint holds the state of its event (an error event's checkpoint holds the previous
  good event's state). `state_at` never needs an archive file.

### Inserting a run

`HubStore.neighbors(key)` finds the events just before and after a new key. It uses the new
`events_by_time` index; ties at the same microsecond are compared in Python.

`alloc_ord` takes the midpoint of the two neighbours' ranks. An append keeps S3's integral ranks.
When floating point runs out of room (gap < `ORD_EPS`), `renumber()` rewrites every rank to
1..N in the same order (two steps, so the UNIQUE rank never collides).

In one `BEGIN IMMEDIATE` transaction:

1. The location is checked again. Another SM window may have ingested it: outcome `present`.
2. The run identity is checked again. A copy of a run already held: outcome `location`.
3. The run's rows are the diff against the previous good event's state (flat from a 3-entry LRU,
   else `state_at`). A byte-identical successor of a run is a zero-change event with no parse,
   as in S3.
4. The successor is prepared **before** the insert. An un-anchored SM successor is anchored (I3).
   A run successor's own state is read, because it does not change.
5. The event, location, watermark and rows are inserted.
6. The run successor is re-diffed against the new run (I2). Its `base_hash` is updated, and its
   `proven` is recomputed from its own `node.json` patches.
7. Error-event checkpoints between the two hold the new state (I4).
8. A checkpoint is written when the stretch between start points reaches the interval.
9. Revert facts are refreshed for the run, its successor and every later equal state.
10. `OVERLAPS_SM_WRITE` is set.

Nothing else is touched: the successor's rows change, every other event's rows stay
byte-identical (pinned).

### A run inserted before an SM event: the decision

The SM event is a fact. **Its rows and its state never change.** Before the run goes in, an
un-anchored SM successor is anchored with its exact state (I3). After that, nothing inserted
before it can move it. The run after the SM event is not touched either: it was diffed against
the SM event's exact state, and that state did not change. Only run rows are diffs.

### SM events are placed by time too

`append_sm` (S4) no longer takes `MAX(ord)+1`. It places the event by its own UTC instant with
the same `neighbors`/`alloc_ord`. Its "chained" test (is the base the predecessor's post-state)
uses the **placed** predecessor. When it lands before a run the sync already ingested, that run is
re-diffed against the SM event's exact state (`after_sm_placed`). This covers two windows, or a
projector behind the sync.

A DERIVED SM event (S4: no bytes in RAM) replays from the event **before its instant**
(`hub._placed_base`), not from the last one projected (pinned with a long-list edit, where the two
differ).

### Flags

S5 adds four bits. `schema_version` stays 1, because the tables are additive:

| Bit | Name | Set when |
|---|---|---|
| 64 | `OVERLAPS_SM_WRITE` | `run_start < an SM write's instant < the run's own instant`. The run's save (the whole machine, at its end) went over that write (DESIGN 2.11). Set whichever lands first. |
| 128 | `REWRITTEN` | the run folder's saved pair was rewritten after ingestion. The event's rows are its current bytes. |
| 256 | `SOURCE_GONE` | the run folder, or its saved state, is gone. The event, its rows and its replay stay. It is cleared when the folder comes back with the same bytes. |
| 512 | `NODE_UNREADABLE` | `node.json` could not be read. The saved pair is still the run's state, and the instant comes from the folder clock. |

## In-flight, rewritten, deleted, corrupt, foreign

- **In flight.** The newest run of a folder whose pair is missing, unreadable or **torn** (it does
  not parse), or whose `node.json` is unreadable, waits in `deferred`. It is re-read on every
  light tick of its folder, and by the projector's periodic wake (≤ ~30 s). That second path
  exists because a state written *inside an existing* `quam_state` folder moves no directory
  the watcher looks at. The run lands once complete. A later run in the same folder proves it
  final, and it then becomes an error event (S3 semantics). A torn parse of the newest run rolls
  its transaction back (`Deferred`) instead of committing an error event; S3 would have committed
  one.
- **Rewritten.** `run_files(root_id, rel_path, sig, rewritten_us)` holds `size:mtime_ns` of state,
  wiring and node.json for every ingested folder: three stats, no read. A changed watermark is
  re-read:
  - same bytes → the watermark and metadata are updated, nothing is re-diffed;
  - same instant → the event is re-diffed **in place** (same eid, `REWRITTEN`, `rewritten_us`),
    its successor is re-diffed, and its checkpoint is refreshed;
  - a node.json that now names another instant → the event **moves** (removed with its successor
    repaired, then inserted at its new place);
  - a pair that is mid-write → no new watermark, so the next sweep looks again.

  **A rewrite after an SM write** keeps that SM event untouched: it is anchored, its rows are
  facts. The correction is applied at the run, flagged `REWRITTEN` and dated by `rewritten_us`.
  This was the S4 hand-over item.
- **Deleted.** `SOURCE_GONE`. Rows and replay stay, and a later run diffs against the ledger state.
  A folder deleted between listing and ingestion is not an event.
- **Corrupt `node.json`.** This is the docs/270 review's P2 #6, fixed for **both** builders through
  the shared `hub_build.read_node`/`event_fields`/`parse_state`: the run keeps its saved state,
  flagged `NODE_UNREADABLE`. When the node is repaired, the watermark moves and the metadata lands.
- **Foreign chip.** Kept, flagged `CHIP_UNCERTAIN`, with its own honest rows.
- Run files are read through `safe_io.open_shared` (FILE_SHARE_DELETE), the docs/270 review's
  P2 #7.

## When the sync looks

| Trigger | What it does | Cost (measured below) |
|---|---|---|
| chip open (`_activate_quam`, both the cached and the cold path) | full: list every day folder, stat-sweep every ingested run | 0.84 s / 4,109 runs, 1.2 s / 10,000 |
| run watcher tick on a registered folder | light: new day folders + the newest 2, the deferred runs, a stat check of the newest 64 runs | 8-12 ms with nothing new |
| periodic, every 30 s | stat every day folder (a late copy into an OLD day moves no watched directory); re-read deferred runs | one stat per day folder |
| periodic, every 300 s | the full stat sweep (rewrites of old runs) | ≈ 1 s per 10k runs |

The run watcher (`core/run_watch.py`) gained `watch(owner, roots)`. Roots watched for another
owner wake only listeners registered with `all_roots=True` (the hub). The dataset tick and the
existing listeners (arrivals, `RunIngest`) see exactly what they saw before (pinned). Chip open
starts the watcher. Before, only a `/datasets/wait` page started it.

**`watch()` takes no signature on the caller's thread.** A first draft did, as `set_roots` does.
The caller is the chip-open request, and on a first open SM's own background workspace parse
holds the interpreter. Each of the ~650 `os.stat` calls of the newest day then waited behind it:
2.2 s inside the request, against 20 ms for the same call alone. The watcher's own next look now
takes the baseline, and it **announces** an owner's new root to the all-roots listeners instead of
swallowing it. That is one extra light tick, and it means a run landing between the open's
listing and the baseline is never lost. The hub hook (`_hub_sync_open`) now costs 35 ms on a first
open, measured in step (one run measured 241 ms while the background parse was busiest), and
20-60 ms on a re-open while a catch-up runs. The catch-up is queued last, after the watcher is
registered. Like every slice, the first one waits for in-flight requests (at most 0.5 s; a
longer bound was tried and dropped, because it delayed every tick while a user was clicking).

## One writer per ledger

S5 runs every slice on the **hub's projector thread**: the same queue that projects SM journal
lines. Run-sync slices are queued as `_SyncTask`s, and at most one per chip waits at a time.
Both writers take the chip's **ledger writer lock** (`hub._writer_for`, an RLock). The sync takes
it through `Hub.ledger()`, and projections through `_run_locked`. So a projection and a run
insertion never write one ledger at the same time in one process (pinned).

Across processes (two SM windows), every mutation is one `BEGIN IMMEDIATE` transaction that
re-reads its neighbours, location and identity inside it. The other window's work is seen, never
duplicated. The run sync never touches the live chip and never takes the live-write lock.

**One connection per burst.** `Hub.ledger(keep=True)` keeps one `HubStore` open while the
projector works, and closes it when the queue runs dry (`Hub.release`). An inline caller (tests,
the CLI) closes it on exit, so a ledger file can be deleted and rebuilt. A 12-run sync forced into one-item
slices opened the ledger once (pinned). S4's `project()` still opens its own connection per call.
That is unchanged here, to keep S4's code as the S4 fixes expect it.

**Budget.** A slice is 0.5 s of work, and at least one item, whatever the budget. Before each
slice the projector waits (≤ 0.5 s) for in-flight user requests to finish (the `RunIngest`
foreground rule). A TESTING app syncs inline on the request thread only when
`HUB_SYNC_ON_OPEN` is set, so no unrelated test that opens a chip with a real declared data
folder ingests it.

**RAM.** Only metadata stays in RAM: per root, the ingested `rel_path → [eid, watermark]`, the day
folders' mtimes and the deferred runs. While a run is ingested, its own pair is in RAM, plus
an LRU of 3 flats.

## Measured

The machine is this PC: Windows 11, the test env. Every number is from a **copy** in the
scratchpad. The 4,121-run archive's run folders were copied (only `node.json` + `quam_state/`,
4.0 GiB, real byte copies, no links). The newest 12 runs were held aside to be added later. The
10k synthetic is generated from one ~300 KB saved pair, with 1-5 numeric leaves moved per run,
5% byte-identical repeats and 0.3% stateless runs. Other agents' processes ran on the PC during
some measurements, so read the timings as indicative. The S3/S5 pair below was measured back to
back on the same input. The open and sweep costs were measured before `file_sig` dropped from
four syscalls to three, so they are slightly pessimistic.

### Cold catch-up (CLI `python -m quam_state_manager.core.hub_sync`, one process, RSS polled every 100 ms)

Final code, the two builders back to back on identical input. By then the copy held 4,131 runs:
the 12 held runs and the rig's fault and late runs had been added.

| Input | Events | Error events | Rows | Ledger MiB | Seconds | Peak RSS MiB |
|---|---:|---:|---:|---:|---:|---:|
| archive copy (1.6 MB states), **S5** | 4,131 | 9 | 19,173 | 7.97 | 189.1 | 67.9 |
| same copy, S3 offline builder | 4,131 | 9 | 19,173 | — | 152.2 | 69.7 |
| synthetic (300 KB states), S5 | 10,000 | 28 | 32,821 | 11.22 | 258.3 | 74.9 |

- **S5 and S3 agree row for row**: 0 event mismatches (time, hashes, flags, status, error) and all
  19,173 change rows equal, `proven` included.
- The first comparison, on 4,109 runs, found one difference: `base_hash` on the 9 error events,
  which S3 sets to the head's hash. S5 now does the same, and this is pinned.
- **S5 costs about 9 ms per run more than S3** (+24%). It pays for its per-run
  placement queries, the stat watermark and one `BEGIN IMMEDIATE` transaction per run, which is
  what lets two windows and a crash stay consistent.
- docs/270's 80 s for the same archive was measured on the original folders on another day. On
  this copy, today, S3 took 152-163 s. Compare the columns, not the documents.

### Inside SM (rig on port 5139, production mode, waitress)

The rig was a chip copy whose `extras.data_folder` is the archive copy, plus a sandboxed home and
an empty qualibrate config.

| Step | Result |
|---|---|
| open the chip (`POST /load` only) | catch-up starts in the background |
| catch-up progress before the kill | 1,539 runs in 32.9 s |
| request latency during catch-up | `/hub/status` p50 16.7 / p95 61.3 ms; landing page p50 24.6 / p95 62.3 ms |
| **SM killed (`taskkill /F`) mid catch-up**, 1,567 events | `verify`: 0 problems |
| restart, open → ready | 2,549 runs in 92.5 s; landing page p50 36.0 / p95 88.5 ms during, 10.7 ms idle |
| 3 runs added **while SM was closed**, then start + open | all 3 in the ledger **2.0 s** after the open, no other page visited |
| runs added **while SM was open** | **0.66 s** and **0.42 s** to land (one watcher tick + one slice) |
| the same two checks again **on the final code** (after the watcher and hook fixes) | while open: **0.56 s** and **0.36 s**; while closed: in the ledger **0.72 s** after the open returned |
| a gap run (between two ingested runs of the newest day) | 0.69 s, placed between its neighbours |
| final `verify` over the rig ledger | 4,118 of 4,127 events (every run with a saved state) replayed equal to their own pair, every checkpoint correct: **0 problems** |

### Incremental, on the copied ledgers (one process; ms)

| | archive copy, 1.6 MB states | synthetic, 300 KB states |
|---|---|---|
| open: full listing + stat sweep | 840 (4,109 runs) | 1,199 (10,000 runs) |
| full sweep, nothing changed | 759 | 1,084 |
| light tick, nothing new | 12.0 | 7.8 |
| one new run per tick | 248, 271, 163, 147, 160 | 118, 35, 37, 34, 34 |
| late run into the middle (insert + successor re-diff) | ≈ 370 | — |
| rewrite of an old run (re-diff in place + successor) | 125; full sweep incl. it 585 | — |

- DESIGN estimated 20-60 ms per new run. That holds for a 300 KB chip: 34-37 ms steady.
- A 1.6 MB chip costs 150-270 ms: parse, flatten and diff of the whole saved pair.
- The first tick in a process also replays the head from its checkpoint (the 118 ms and 248 ms).
- `shape_and_arrays` no longer re-encodes a long array whose content-addressed blob the ledger
  already holds. Both builders benefit, and the bytes stored are unchanged.

## Fault injection

One case per channel S5 watches. Each case checks the outcome, not "no crash". Pins are in
`tests/test_hub_sync.py`; the rig cases ran with two real SM processes on one chip.

| Channel | Pin | Real-SM rig outcome |
|---|---|---|
| a run inserted out of order | `test_a_late_run_is_inserted_and_only_its_successor_is_rediffed` (+ two folders, same instant, rank exhaustion) | gap run placed between its neighbours in 0.69 s; late copy into an old day landed in 30.2 s (periodic listing) |
| a rewritten run folder | `test_a_rewritten_run_is_rediffed_in_place_with_its_successor`, `..._after_an_sm_write_keeps_the_sm_event`, `..._node_names_another_instant_moves` | `REWRITTEN` 1.1 s after reopening the chip, 16 rows, same eid |
| an in-flight run that completes later | `test_an_inflight_newest_run_waits_then_lands_when_complete`, `..._deferred_run_is_looked_at_again_without_a_watcher_tick` | not in the ledger while stateless; landed 27.8 s after its state was written (periodic deferred check); one event across two windows |
| a run from a foreign chip | `test_a_run_of_another_chip_is_kept_and_flagged` | `CHIP_UNCERTAIN`, kept with its own 2 rows |
| a deleted run folder | `test_a_deleted_run_folder_keeps_its_event_rows_and_replay`, `test_a_folder_deleted_before_it_is_ingested_is_not_an_event` | `SOURCE_GONE`; the event and its rows were kept |
| a corrupt node.json | `test_a_corrupt_node_json_keeps_the_saved_state` | deferred while newest; then `NODE_UNREADABLE`, state kept, quality `archive_offset` |
| two SM windows on one chip | `test_two_windows_on_one_chip_ingest_each_run_once` (2 processes), `test_a_run_another_window_ingested_is_skipped` | 0 duplicate runs; both windows `ready` |
| SM killed mid-tick | `test_a_crash_mid_insertion_leaves_the_ledger_as_it_was`, `test_killed_mid_catch_up_is_consistent_on_restart` (a real process kill) | `taskkill /F` at 1,567 events: `verify` 0 problems; resume completed |

## Tests and mutations

- `tests/test_hub_sync.py`: **41 pins**, generic synthetic run folders.
- **Mutation sweep: 58/58 RED**, each an assertion failure, never a collection error. Every
  source file was restored byte-for-byte after each mutation. Machine-readable results are in
  [275_hub_sync_mutations.json](275_hub_sync_mutations.json).
- **39 of 41 pins are targeted.** The two that are not are the process-level checks (two real
  processes, a real kill). They test SQLite's transaction atomicity across processes, which no
  deterministic source edit can break reliably. Their in-process halves are targeted:
  `txn_commits_on_error` and `no_present_check`.
- **The first sweep found 5 survivors.** All five were pin gaps, and all are now pinned:
  - `pending` only matters after the first catch-up;
  - a folder deleted between listing and ingestion;
  - two runs of the same instant;
  - the offline builder's half of the corrupt-node change;
  - an inline sync's handle close.
- **Two harness defects were fixed before the sweep was trusted.** Multi-line anchors missed
  because the worktree checks out CRLF. And a heredoc ate the harness's backslashes (the known
  trap on this machine).
- **Three defects were found while writing pins and measuring, and fixed:**
  - a torn newest run was committed as a final error event;
  - `_is_newest` scanned the whole queue per run (O(N²) on a cold build);
  - the chip-open hook baselined the watcher on the request thread (1.9-3.5 s on a first open,
    see "When the sync looks").

Regression run (the test env, `--timeout=900`):

- **The 199 test files** that touch the hub, run ingestion, the run watcher or chip activation
  (`/load`, `_activate_quam`, `/workspace/select`, `/qualibrate/open`), found by grep:
  **5,516 passed, 129 skipped, 7 failed** (53 min).
  - Six failures are `*_selfcheck` tests failing on `jsdom not installed`: this worktree has no
    `node_modules`. With `NODE_PATH` pointed read-only at an installed copy, all six pass.
  - The seventh, `test_calc_window::test_never_touches_a_chip`, reads `routes.py` through
    `inspect.getsource`. I edited `routes.py` while the run was in progress, so the loaded module
    and the file on disk disagreed. It passes on its own.
- **On the final code** (the last watcher, hook and bound edits): every `test_hub_*` file,
  `test_run_watch`, `test_run_ingest`, the watcher, newrun, poll, project-time, state-history and
  state-coherence files, `test_calc_window` and all of `test_web`: **942 passed, 19 skipped,
  0 failed**.

**One existing test was edited:**
`tests/test_hub_build.py::test_state_pair_changed_during_read_is_rejected`. It injected a
concurrent writer by patching `Path.read_bytes`. Run files are now read through the share-delete
handle, so the injection hooks `hub_build._read_shared` instead. What it asserts is unchanged.

## Changes outside the new module

`core/hub_sync.py` is new: the roots rule, the per-chip sync, the ledger mutations, status,
`verify`, and a CLI. Existing files changed as follows:

- **`core/hub_store.py`** (S3/S4):
  - additive `events_by_time` index and `run_files` table;
  - the S5 placement methods;
  - `append_sm` places by instant and repairs its successor;
  - `_pid` asks the file on a cache miss (another window's path);
  - `check_same_thread`;
  - `checkpoint_interval=None` adopts the ledger's own;
  - `meta.ledger_id`;
  - S5 flags;
  - the long-array blob skip.

  S3's offline `append` is unchanged.
- **`core/hub.py`** (S4): **kept to separate additions**, for the S4 fix cherry-pick:
  - `_writer_for`;
  - a docs/275 block at the end of `Hub` (`writer`, `ledger`, `release`, `status`,
    `require_ready`);
  - `_run_locked`, `kick_sync`, `_sync_inline`, `_sync_one`, `_periodic`, `_SyncTask`,
    `_placed_base`, and the module `kick_sync`.

  It also touches S4 code at four places. A cherry-pick of S4's fixes to `project` and
  `_project_line` will meet the fourth; it is one line and easy to re-apply:
  - `kick`'s inline branch calls `_run_locked`;
  - `_loop` takes `_SyncTask`s, a periodic wake and an idle `release`;
  - `import contextmanager`;
  - **one line in `_project_line`**: `prev = _placed_base(store, line)`.

  `project()` itself is byte-identical to S4's.
- **`core/hub_build.py`** (S3): shared `read_node`, `run_of`, `resolve_instant`, `parse_state`,
  `identity_disagrees` and `event_fields`; `_read_shared`. Behaviour changes, both applied to both
  builders:
  - an unreadable `node.json` keeps the saved state, flagged;
  - a newest run with an unreadable node is deferred.

  The offline builder still refuses a late run; its message now points at the sync.
- **`core/run_watch.py`**: `watch(owner, roots)` and `add_listener(all_roots=)`; an owner's
  root is baselined by the watcher thread and announced on its first look. The dataset tick, the
  dataset roots' baselines and the existing listeners are unchanged.
- **`web/routes.py`**:
  - `_hub_roots_for`, `_hub_live_identity` and `_hub_sync_open`, called from **both** activation
    paths;
  - `GET /hub/status`.

## Residual risks

1. **Rows-only folding across a drifted SM write.** When something outside SM changed the live chip
   between a run and an SM write, the SM write's exact state (its anchor) holds that outside value.
   Its rows are only SM's entries, so the change appears as no row anywhere. `state_at` is exact
   everywhere (verified). A surface that folds rows alone, without checkpoints or anchors, is wrong
   for that path after such an event. This is S4's model, unchanged here: SM rows are facts, run
   rows are diffs against the ledger state. A drift row on the SM event would need the previous
   live document at write time.
2. **S3-built ledgers have no watermarks.** An offline-built run gets its first watermark when the
   sync first sees it, without re-reading. A rewrite that happened between the offline build and
   that moment is not detected.
3. **Detection latency.**
   - Old days: a late copy lands within ≤ 30 s + the projector wake, and a rewrite of an old run
     at the next 300 s sweep or chip open.
   - An in-flight run whose state is written inside an existing `quam_state` folder lands at the
     periodic deferred check (≤ ~30 s; 27.8 s measured).
   - While a root is known to have moved, `status()` says `building`. "Ready" otherwise means "as
     of the last look", not "as of now".
4. **Cost on a network share.** The 30 s periodic listing stats every day folder; docs/155 measured
   about 0.7 s for 390 of them on an SMB share. It runs in the background, but on a share it is not
   free. The light tick itself stays at two day folders.
5. **Request latency during a cold catch-up** roughly triples at the median: 11 → 36 ms on the
   landing page, p95 89 ms. It is GIL-shared CPU. The slices yield to requests between items, not
   within one parse.
6. **The archive offset hint is fixed when a run is ingested.** If the folder's majority offset
   changes later, runs with naive times are not re-placed. None of the measured archives has naive
   times.
7. **Moving or deleting an event with several locations.** A copy's rewrite re-diffs the shared
   event. A move takes all of that event's locations with it.
8. **A foreign run stays in the chain.** It is flagged and its rows are honest, but both its rows and
   the next run's rows are large.
9. **The projector's own periodic calls are not pinned.** That covers `_loop` calling `_periodic`
   when idle and when busy. `hub_sync.periodic()` itself is pinned, and the idle path ran in the
   real-SM rig: the late copy landed in 30.2 s and the in-flight run in 27.8 s.
10. **S4 hand-over #9 is only partly done.** One connection per burst covers the sync. `project()`
    still opens a store per call, which is S4's code under review.
11. **An anchor made by S5 is the merged document, not the written bytes.** `ensure_anchor` stores
    `{"s": merged doc, "w": {}}`, marked `state_ref = "anchored:<hash>"`. `state_at` is exact. A future
    "restore the exact bytes" surface must use S4's own pair anchors (`state_ref = "pair:<hash>"`),
    not these.

Real browser: S5 adds no page, only `/hub/status` (JSON). It was checked with real HTTP against
real SM processes, not in Chrome.
