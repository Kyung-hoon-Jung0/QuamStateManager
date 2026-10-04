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
- **`/hub/status` and `Hub.status()`** say `building` (n/N), `ready`, or `degraded` (a data folder
  cannot be read, or a run could not be ingested after its retries; the ledger holds everything
  else). `Hub.require_ready()` raises `hub_sync.Building`, a `ramcache.Warming`, while
  `building`, so no surface can present a partial ledger as complete.

## Which folders belong to a chip (one function)

`hub_sync.roots_for_chip` is the one rule. `routes._hub_roots_for(ctx)` feeds it from what SM
already knows. Strongest source first, existing directories only, deduplicated by `fs_key`:

| # | Source | Where it comes from |
|---|---|---|
| 1 | `declared` | the chip's own `extras.data_folder`, bridged and existence-checked by `_adopt_extras_data_folders` |
| 2 | `project_storage` | the chip's project scope's folder under the qualibrate storage location: `<location>/<project>`, or the location itself when its path already names the project (`hub_sync.project_run_root`) |
| 3 | `project_roots` | folders recorded for that project in `instance/project_dataset_roots.json`, except another project's subfolder of a shared location |
| 4 | `decided_same` | a Datasets (workspace) root the user has declared to be this chip's data: a `"same"` entry in `chip_decisions.json` that names the root (`<chip>::root:<fs_key>`), or names its data-folder label when exactly one workspace root carries that label |

**Decision: never every workspace root.** One workspace often holds several chips' data. Syncing
a foreign folder would put every one of its runs into this chip's timeline. A run inside a
synced folder whose identity disagrees is **kept and flagged `CHIP_UNCERTAIN`, never dropped**
(the S3 rule). When the open chip **declares a name**, its own identity (name, fingerprint, qubit
names) is seeded into `meta.chip_identity` when the ledger has none yet. A nameless chip's
fingerprint changes as the chip does (a qubit added, a controller moved), so its ledger takes the
identity from its own archive, as the offline builder does. Two names decide; one name on two
chips with no qubit in common is two chips (`hub_build.identity_disagrees`). An existing ledger
keeps its stored identity.

`/qualibrate/open` registers the chip's folders again after it pins the project: when several
projects share one `state_path`, the activation before the pin cannot know which one it is.

## Placement by instant, local repair

### Invariants

All four are pinned in `tests/test_hub_sync.py`. `hub_sync.verify()` checks I1, I2 and I4 on any
ledger, plus that every run has a location:

- **I1:** `ord` order equals the canonical order `(t_ord, root folder_key, run_id, experiment,
  rel_path, eid)`. `t_ord` is the order instant: a run's `t_utc_us`, and an SM event's own
  instant raised to its causal floor ("SM events are placed by time too"). An SM event has an
  empty root key, so it sorts before a run of the same microsecond.
- **I2:** a run's rows = S2 `diff(state_at(previous good event), its saved state)`. An SM event's rows
  are what SM wrote and never change.
- **I3:** an SM event with no anchor replays from its predecessor. Whenever that predecessor would
  change, the SM event's exact state is **anchored first** (`HubStore.ensure_anchor`).
- **I4:** a checkpoint holds the state of its event (an error event's checkpoint holds the previous
  good event's state). `state_at` never needs an archive file.

### Inserting a run

`HubStore.neighbors(key)` finds the events just before and after a new key. It uses the new
`events_by_order_time(t_ord, ord)` index; ties at the same microsecond are compared in Python.

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
the same `neighbors`/`alloc_ord`, raised to a **causal floor** when the clocks disagree with what
happened (`HubStore.causal_t_ord`): never before an SM write earlier in the journal
(`sm_events.jpos`), and never before the run whose content (`events.chash`, the working copy's
`content_hash`) is the write's `base_hash`, when that run sits later than the write's clock
(within `CAUSAL_WINDOW_US` = 1 h, and only when no earlier event already explains the base).
The mirror rule places a run before an SM write based on it (`causal_ceiling`). Its "chained" test (is the base the predecessor's post-state)
uses the **placed** predecessor. When it lands before a run the sync already ingested, that run is
re-diffed against the SM event's exact state (`after_sm_placed`). This covers two windows, or a
projector behind the sync.

A DERIVED SM event (S4: no bytes in RAM) replays from the event **it is placed after**, computed
inside `append_sm`'s own transaction (`derive=`, `hub._derive_from`), not from the last one
projected and not before the placement (a run inserted in between would be missed).

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
  one. Any run, newest or not, whose pair is missing or torn while its files changed less than
  `FRESH_S` (120 s) ago also waits: a late copy caught mid-copy. When an error event later gains
  its pair, the outcome is `completed`, not `rewritten`.
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
| run watcher tick on a registered folder | light: new day folders + the newest 2, the deferred runs, a stat check of the newest 64 runs (by run id) | 8-12 ms with nothing new |
| periodic, every 30 s | stat the newest days and the next 200 older day folders, round robin (a late copy into an OLD day moves no watched directory); a chunk of 2,000 runs of the stat sweep; re-read deferred runs and runs whose retry is due | bounded per look (review round) |
| within ~300 s | every ingested run stat-checked once (the chunks above add up to the full sweep) | ≈ 1 s per 10k runs in total |

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

**Budget.** A slice is 0.25 s of work, and at least one item, whatever the budget; it ends after
any item as soon as a user request is in flight. Before each slice the projector waits (≤ 0.5 s)
for in-flight user requests to finish (the `RunIngest` foreground rule). Only the open chip is
watched and swept; another registered chip keeps its RAM but costs nothing until it is opened. A TESTING app syncs inline on the request thread only when
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
    `_derive_from`, `_retry_sync_later`, `reserve`/`unreserve`, and the module `kick_sync`.

  It also touches S4 code at four places. A cherry-pick of S4's fixes to `project` and
  `_project_line` will meet the fourth; it is one line and easy to re-apply:
  - `kick`'s inline branch calls `_run_locked`;
  - `_loop` takes `_SyncTask`s, a periodic wake and an idle `release`;
  - `import contextmanager`;
  - **`_project_line`** passes `jpos=` and `derive=` to `append_sm` (review round).

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

## Review round

An adversarial review of `e9fbfb24` returned executed refutations: six P1, ten P2, ten P3 and
three name findings. S4's own review fixes (`afb46ab5` on `feat/hub-record`) were cherry-picked
first, then each finding was reproduced, fixed and kept as a pin in `tests/test_hub_sync.py`
(classes `TestReviewSync`, `TestReviewRoots`, `TestReviewIdentity`, `TestReviewCausal`,
`TestReviewS4AndStore`).

### The S4 cherry-pick (`d7a505e2`)

Three places conflicted. Each keeps both behaviours:

- **`hub.py`.** S4 removed `_LOCKS_GUARD`, which S5's `_writer_for` used. The ledger writer locks
  now have their own `_WRITERS_GUARD`. S4's journal lock, `{"landed": id}` lines, per-line error
  capture, offset rescan, CLI writer wait and `project()` are as S4 wrote them. S5's `kick_sync`,
  `_SyncTask`, writer lock and one connection per burst are kept.
- **`hub_store.append_sm`.** S4 relabels a failed or unconfirmed projection in place (`replace`),
  and reused the old row's `ord`. S5 places every SM event by its instant. Merged: the old row is
  removed, and the line takes its place by instant again. S4's `ord < ord_` bounds on the chained,
  since-anchor and revert queries became S5's placement bound. A relabelled line is always
  anchored.
- **The rest** (`xlock.py`, the per-chip lock in `working_copy.apply_to_live`, the content-decided
  diff in prepare, CLI writer registration) applied without conflict. The run sync takes the
  ledger writer lock and never the live-write lock.

`tests/test_hub_*.py` on the merge alone, before any review fix: 191 passed, 1 failed. The failure is
`test_a_burst_shares_one_connection_and_releases_it_when_idle`, the flake the review reported (P2
below: every chip ever opened was swept); it is S5's, not the merge's.

### Findings, fixes, pins

| Finding | Fix | Pins |
|---|---|---|
| **P1-1** Under a storage location shared by several projects, a chip synced every project's runs | `hub_sync.project_run_root`: `<location>/<project>`. Another project's recorded subfolder is never adopted. `/qualibrate/open` registers the folders again after the pin | `test_opening_a_project_syncs_its_own_runs_and_no_other_chips`, `test_a_project_known_only_after_the_pin_is_synced`, `test_project_run_root_and_sibling_folders` |
| **P1-2** A run whose ingestion raised was never retried (its day was marked listed), and status said `ready` | `RootState.failed` holds `[retry_at, attempts, error]`. A run is re-queued when due (30 s, at most 5 times), and its day is not marked listed while a retry is pending. Status is `building` while a retry is due and `degraded` once retries run out | `test_a_run_whose_ingestion_raised_once_is_retried`, `test_a_day_whose_run_failed_is_listed_again`, `test_a_run_that_keeps_failing_is_reported_not_ready` |
| **P1-3** Status said `ready` while a moved root was being listed, or while the last found run was in hand | `in_hand` counts the item a slice holds; a slice clears `ready` before it empties an inbox | `test_status_never_says_ready_while_a_moved_root_is_being_listed`, `..._while_the_last_run_is_being_ingested`, `test_status_is_building_while_a_periodic_listing_ingests_what_it_found` |
| **P1-4** A DERIVED SM write was computed before its placement transaction, so a run inserted in between was missed | `append_sm(derive=)` computes it inside the transaction, from the event it is placed after (`hub._derive_from`) | `test_a_derived_write_is_computed_inside_its_transaction`, `test_a_write_after_a_backward_clock_step_derives_from_the_write_before` |
| **P1-5** SM writes were ordered by wall clock: a PC whose clock is ahead, or a clock stepped back, put a write before its own base | A causal floor (`causal_t_ord`): never before an earlier journal line (`sm_events.jpos`), and never before the run whose content is the write's base (`events.chash`). Its mirror, `causal_ceiling`, applies to runs. `events.t_ord` is the order instant. All three columns are additive | `test_a_run_whose_clock_is_ahead_is_still_the_base_of_the_write[run_first, sm_first]`, `test_an_undo_with_an_earlier_clock_still_undoes` |
| **P1-6** A slice that raised stopped the catch-up for good | `_retry_sync_later` re-kicks with backoff (1, 2, 4 … 60 s). `periodic()` rescues a chip that has work but no queued slice | `test_a_slice_that_raised_is_retried`, `test_periodic_rescues_a_chip_with_work_and_no_slice_queued` |
| **P2** A late copy caught mid-copy became a final error event | A missing or torn pair whose files changed less than `FRESH_S` (120 s) ago waits. An error event that gains its pair is `completed`, not `rewritten` | `test_a_late_copy_caught_mid_copy_is_not_a_final_error`, `test_an_error_event_that_gains_its_state_is_completed_not_rewritten`, `test_a_run_in_flight_in_the_same_second_as_the_previous_save_waits` |
| **P2** `CHIP_UNCERTAIN` on the chip's own history | Live identity is seeded only from a named chip; a nameless one takes it from its own archive. Two chips with one name and no qubit in common are two chips | `test_the_chips_own_history_is_not_flagged_foreign[qubit_added_since, network_changed_since]`, `test_two_chips_that_share_a_chip_name_are_told_apart`, `test_a_named_chip_that_gained_a_qubit_is_still_itself` |
| **P2** A "same" decision adopted every root with that label | It must name the root (`<chip>::root:<fs_key>`), or its label must belong to exactly one root | `test_a_decided_same_root_must_be_named` |
| **P2** An unreachable data folder made every run look deleted | An unlistable root skips the gone-check and makes status `degraded` | `test_an_unreachable_data_folder_is_not_every_run_deleted` |
| **P2** Two windows: `SOURCE_GONE` set on a stale eid | The eid is read from `locations` inside the flagging transaction | `test_two_windows_split_and_flag_the_same_event`, `test_two_windows_moving_and_flagging_agree` |
| **P2** Copies: a node move followed by a restart made the run two events, a rewrite of one copy changed what the other copy replays, and `verify` read one location | One event per (instant, run, bytes), as a build from the files gives. A copy that is rewritten or re-timed leaves the shared event; the others keep theirs. `verify` replays every location | `test_a_move_keeps_the_other_locations`, `test_a_move_of_one_copy_is_the_same_ledger_after_a_restart`, `test_every_location_of_a_rewritten_copied_run_replays_its_own_bytes`, `test_verify_checks_every_location` |
| **P2** Every chip ever opened was watched and swept (and the burst pin flaked) | `open_chip(exclusive=True)`; only the active chip is watched, listed and swept | `test_only_the_open_chip_is_swept` |
| **P2** Deep tree: every periodic look stat'ed every day folder, and the 300 s sweep came as one burst | Round robin: 200 older days and 2,000 runs per look. A slice ends after any item once a request is in flight | `test_a_periodic_listing_stats_a_bounded_chunk_of_days`, `test_a_slice_ends_after_one_item_when_a_request_waits` |
| **P2** S4: a landed write recorded as "outcome unknown" | `afb46ab5`'s late landed line already corrects the final outcome. `Hub.record` now reserves the line id before the append, so a projection never reads it as abandoned while it is being written | `test_a_line_read_before_it_is_registered_is_not_unconfirmed` |
| **P3** The newest 64 were chosen by string order, where `#10` sorts before `#9` | Run id order (`_rel_order`) | `test_a_light_tick_stat_checks_the_newest_runs` |
| **P3** A repaired node got a new eid and `REWRITTEN` | A move keeps the eid. `REWRITTEN` only when the bytes changed | `test_repairing_a_corrupt_node_keeps_the_event_and_is_not_a_rewrite` |
| **P3** `OVERLAPS_SM_WRITE` on a stateless run depended on landing order | Set for error runs too | `test_overlap_flag_of_a_stateless_run_does_not_depend_on_landing_order` |
| **P3** A deferred run voted for the offset on every re-read | The vote rides on the candidate and counts once, at ingestion | `test_a_deferred_run_is_voted_once` |
| **P3** An error event's `base_hash` went stale after an insertion | `refresh_error_bases` | `test_an_error_events_base_hash_follows_a_late_insertion_like_the_offline_build` |
| **P3** A folder vanishing before `_resolve` wedged the sync | A per-candidate `OSError` is a vanished folder | `test_a_folder_that_vanishes_before_it_is_resolved_does_not_wedge_the_sync` |
| **P3** `HubStore()` wrote `meta` on every open (a reader took the write lock) | Schema statements are skipped when current. `meta` is written only when a key is missing | `test_opening_a_ledger_takes_no_write_lock` |
| **P3** The Datasets watcher could wake for a root the hub had seen first | Owner roots keep their own baselines (`_xsigs`). A root added to Datasets is baselined when added | `test_a_root_the_hub_saw_first_is_baselined_for_the_datasets_page` |
| **P3** The share-delete read was unpinned | Pinned | `test_run_files_are_read_through_a_share_delete_handle` |
| **Names** docs/275 named a conda env after a customer; docs/270 named a customer archive and listed #6/#7 as open | "the test env"; the archive is now generic; #6, #7 (and #5, #9) are marked with what fixed them | — |

**Found during this round, not by the review:**

- **Every copy of a run deleted, yet no `SOURCE_GONE`.** The gone-check counted the other copies'
  location rows, including copies already deleted. This was present since `e9fbfb24`. It was found
  while writing the copies sentence above. Now only copies still holding their saved state count
  (`_live_copies`), on the sweep, the re-read and the split paths. Pins:
  - `test_a_run_whose_every_copy_is_deleted_is_gone`
  - `test_every_copy_deleted_before_one_sweep_is_gone`
  - `test_a_copy_that_lost_its_state_while_another_holds_it_is_not_gone`
  - `test_a_copy_reread_without_its_state_is_not_gone_while_another_holds_it`
  - `test_a_live_copy_is_one_whose_watermark_still_has_its_state`
  - `test_rewriting_the_last_copy_on_disk_is_a_rewrite_not_a_split`
- **Copies whose `node.json` files name different instants.** The review-round draft moved the
  shared event with every location. After the moved copy was deleted, the event kept an instant
  that no file on disk names. The fuzz found this once a copy operation was added (seed 1013).
  Now each copy sits at its own instant, as a build from the files gives. A copy that moves to an
  instant the ledger already holds joins that event, and an event no folder names is removed.
  Pin: `test_copies_that_move_to_one_instant_are_one_event_again`.
- **A move that renumbered the ledger failed for good.** A run being re-placed holds the rank
  `-eid`. `renumber()`'s first step negates every rank (`-ord - 1`), and SQLite checks `UNIQUE`
  row by row, so rank `eid - 1` collided with it. The move raised `IntegrityError` on every retry,
  and the run stayed at its old instant. This was introduced by this round's "a move keeps the
  eid". It was found by the fuzz's small-`ORD_EPS` mode (seed 5002). Now `renumber` leaves negative
  ranks alone and moves every other rank below all of them first. Pin:
  `test_a_move_that_renumbers_the_ledger_lands`.

### Changes outside `hub_sync.py` in this round

- **`hub_store.py`:**
  - additive columns: `events.t_ord` (a trigger defaults it to `t_utc_us`; index
    `events_by_order_time`), `events.chash` (index `events_by_chash`) and `sm_events.jpos`;
  - `causal_t_ord`, `causal_ceiling` and `refresh_error_bases`;
  - `renumber` leaves a run being re-placed (negative rank) alone;
  - `append_sm(replace=, jpos=, derive=)`;
  - opening a current ledger runs no schema statement, and writes `meta` only for a missing key.
- **`hub.py`:**
  - `_WRITERS_GUARD`;
  - `_Projector.reserve`/`unreserve` (`outcome_of` says "wait" for a reserved line);
  - `_derive_from` replaces `_placed_base`;
  - `_retry_sync_later`;
  - `_sync_one` passes `should_yield`.
- **`hub_build.py`:** `_chip_identity` carries the qubit names; `identity_disagrees` adds the
  one-name, no-common-qubit rule; `parse_state(want_pair=)`.
- **`run_watch.py`:** an owner's roots keep their own baselines (`_xsigs`), apart from the
  Datasets baselines (`_sigs`).
- **`routes.py`:**
  - `_hub_roots_for`: `project_run_root`, the sibling exclusion, and a "same" decision that names
    its root;
  - `_hub_live_identity` seeds named chips only;
  - `_hub_sync_open` unwatches the other chips;
  - `/qualibrate/open` registers the folders again after the pin.

### Red on the base

The review pins were run against `e9fbfb24` with the final test file: **42 of 44 fail**.

- About 35 fail on their assertion.
- The rest fail because the API they drive does not exist there (`project_run_root`, `periodic`,
  new `ChipSync` arguments). Their review repros were red on the merged base.
- The two that pass are guards against over-correction, not bugs:
  - a named chip that gained a qubit is still itself;
  - the share-delete read (it was unpinned, not broken).

The copies pins added later are red on the code before their fix (mutation-checked, below).

### Measured

The machine is this PC. Other sessions kept it at about 65% CPU (16 cores) throughout, so read
every timing as indicative.

- **"Same rig"** means one SM process on port 5139, serving a synthetic chip. Its declared data
  folder is a deep synthetic tree: 1,500 day folders × 13 runs = 19,500 runs. A second chip has 3
  runs.
- **"Before"** is `e9fbfb24` on that same rig and ledger, run right after the final code, where it
  was measured. Otherwise it is the review's own number.

| What | Before | After |
|---|---|---|
| `status` says `ready` while a run the watcher found is not in the ledger (20 ms polling) | review: 5/5 runs, 0.24-1.3 s early; same rig: 3/3 runs | **0 samples in 5/5 runs**; each landed 0.74-0.95 s after its copy |
| landing page during the open's full sweep of 19,500 runs | review: p95 316 ms; same rig: p50 23 / p95 557 / max 812 ms | **p50 23 / p95 43 ms**, max 689 ms (the first render after start); re-opens p95 46-50 ms |
| idle CPU, two chips opened, 90 s | review: 5.6-8.2 %; same rig: 3.7 % of one core | 3.3 % of one core (0.2 % of the machine) |
| deep tree, cold catch-up in one process (`perf/deep_cli.py`) | review: 748.8 s, peak RSS 95.0 MiB, first ingest after 32.4 s | 469.8 s, peak RSS 97.1 MiB (76.8 while listing), first ingest after 42.7 s |
| light tick, nothing new | 68.7 ms | 89.8 ms |
| periodic look, nothing new | listing 319 / 204 ms, **plus a 7.5 / 6.2 s full sweep every 300 s** | 792 / 833 ms: 200 older days and 2,000 runs stat'ed, in 0.25 s slices |
| full sweep, nothing changed (chip open) | 7.5 / 6.2 s | 5.7 / 6.3 s |
| opening a ledger while a writer holds it for 3 s | 3,065-3,088 ms | **3.1-4.1 ms** |
| opens in 10 s beside a writer and a checkpointer | 34 | 5,252 (0 errors either way) |
| the burst pin | review: 6/10 failing | 10/10 alone; green in every full-file run |

- **The periodic look costs more per look and less per 300 s.** Before, the stat work in 300 s was
  10 listings plus one full sweep, about 9.5 s. Most of that came as one 6-7 s burst. Now it is 10
  looks of about 0.8 s, about 8.1 s, each split into 0.25 s slices that end early for a request.
- **The light tick and first-ingest numbers are within this PC's run-to-run noise.** Their code
  path did not change, apart from sorting the newest runs by run id.

The review's executed repros, run on the merged base and on the final code:

| Repro | Before | After |
|---|---|---|
| `t_stall` (one slice raises) | `building`, 0 events, forever | `ready`, 20 events |
| `t_wedge` (a folder vanishes before `_resolve`) | every later slice raises; 3 runs in, 3 stuck | all runs in; read queue empty |
| `t_stale_eid` (two windows, move then delete) | `SOURCE_GONE` on the stale eid; the live one unflagged | the live eid flagged |
| `t_record_race` (S4: a line read while being written) | `unconfirmed` | `landed` |
| `t4_placed_base` (a DERIVED write vs an insertion) | computed before the placement transaction (its pin is red on the base) | the insertion waits for the transaction; 0 problems |
| `t4_stress`, seeds 1-3 (two processes) | — | 0 problems, 80 SM writes each |

### Fuzz, mutations, regression

**Fuzz** (`fuzz.py`, a seeded property fuzz carried over from the review). Each seed takes 100
random steps against two data folders, with SM writes (pair and DERIVED) interleaved:

- a run appended, late, at a tied instant, or reusing another folder's run id;
- a stateless, no-clock or corrupt-node variant;
- a rewrite, a re-time, a delete, a restore or a restart;
- this round, a byte-identical copy into the other folder.

After every step it checks I1, I2 and I4, and these per-folder rules:

- every folder on disk replays its own bytes at its own node instant;
- an event is `SOURCE_GONE` exactly when no copy holds it;
- every run has a location.

The one-folder mode without SM ends by comparing row for row with S3's offline build of the
archive at rest.

There are five modes: full looks, light ticks, one folder, a checkpoint every 3 events, and
`ORD_EPS` = 0.6, which forces renumbering. On the final code and harness: **300/300 seeds green** (100 full, 60 light, 60 one-folder, 40
checkpoint, 40 small `ORD_EPS`; 61 min). The seeds exercised at least 3,255 SM writes,
641 moves, 762 rewrites, 397 gone folders, 244 copies joined to an event, 110 copies split off
and 323 renumbers.

The fuzz found two product defects, both fixed above:

- copies at different instants (seed 1013);
- a renumber during a move (seed 5002 and others in the `ORD_EPS` mode).

Two harness rules were corrected; neither is a product defect:

- The offline comparison now ages the archive first. S5 waits `FRESH_S` on a fresh stateless run
  that S3 commits at once (seeds 3003, 3030).
- "Newest of its folder" is judged on the ledger's instants. A no-clock run is placed by its folder
  name and the folder's offset hint, which can differ from the fuzz's own intent by the PC's UTC
  offset (seed 4023).

The fuzz also gained the copy operation and the every-copy `SOURCE_GONE` rule. With the
pre-fix gone-check planted, 20 seeds of the full mode did not reach that defect. That case
is held by the pins, not the fuzz.

**Mutations** (`docs/275_hub_sync_review_mutations.json`): **54/54 RED** on the final code. Each
is one source edit, its claimed pins run, and the bytes are restored after. A collection error
never counts as RED. The sweep history:

- The first sweep over the review pins left 11 survivors:
  - eight were pin gaps, now closed;
  - two were equivalent mutations, re-aimed (the undo ordering was reverted to S4's, and the
    watcher mutation now sits in `set_roots`);
  - one was the S4 race, whose final outcome `afb46ab5` already corrects, so the pin now holds the
    transient.
- The second sweep left 2 survivors, both pin gaps, now sharpened:
  - the derive at activation could already name the project;
  - the Datasets baseline was taken at the next look, not when the root was added.
- Eight mutations cover the three defects found in this round.

**Tests** (the test env, `--timeout=900`, run one file list at a time):

- **Every `test_hub_*` file**, plus `test_run_watch`, `test_run_ingest`, `test_project_scope`,
  `test_multi_instance` and `test_scheduler_scope`, on the final code: **400 passed, 0 failed**.
- **The 199 files** that touch the hub, run ingestion, the run watcher or chip activation, with
  `NODE_PATH` at an installed jsdom: **5,687 passed, 51 skipped, 2 failed** (77 min, under load).
  - The run was before the renumber fix, which changes only `HubStore.renumber`; the 400 above
    cover it.
  - Both failures pass alone and with their own file, on this code and on `e9fbfb24`. Neither
    file nor the code it drives changed since S4's base:
    - `test_chip_status_layout::test_chip_jump_selfcheck` is a jsdom timing check;
    - `test_overnight_run::...the_summary_lists_the_night...` reads a failure text that
      `agent_overnight` takes from one of two fields, depending on timing.
  - Both are order or timing flakes, not this round's.
- The burst pin: 10/10 alone.

### Not fixed, adapted, or only documented

- **Two chips that share a `chip_name` still share one ledger directory.** The chip-identity ladder
  in `history.py` (name > fingerprint) decides the directory, and S5 does not change it. The sync
  now flags the other chip's runs `CHIP_UNCERTAIN`, with honest rows. The review's repro was
  adapted to assert that.
- **A "same" decision recorded by label only**, when two workspace roots share that label, is
  evidence for neither root. The review's repro expected one to be chosen; a decision must name its
  root.
- **RAM before the first ingest.** A cold catch-up lists every day folder before it ingests. The
  listing holds one entry per run folder: peak RSS 76.8 MiB (from 32.7 MiB at start) on the 19,500-run tree, first ingest after
  42.7 s. Documented, not changed.
- **Rewrites within one mtime tick.** The watermark is size + mtime of three files. The review's
  equal-mtime repro never produced an equal mtime on NTFS (100 ns ticks), so nothing was changed.
  A same-size rewrite inside one tick would be missed until the next change.

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
   - While a root is known to have moved, a found run is in hand or a failed run's retry is due,
     `status()` says `building`. "Ready" otherwise means "as of the last look", not "as of now".
4. **Cost on a network share.** The 30 s periodic look stats up to 200 older day folders plus the
   newest ones; docs/155 measured about 0.7 s for 390 of them on an SMB share. It runs in the
   background, but on a share it is not free. The light tick itself stays at two day folders.
5. **Request latency during a cold catch-up** roughly triples at the median: 11 → 36 ms on the
   landing page, p95 89 ms. It is GIL-shared CPU. The slices yield to requests between items, not
   within one parse.
6. **The archive offset hint is fixed when a run is ingested.** If the folder's majority offset
   changes later, runs with naive times are not re-placed. None of the measured archives has naive
   times.
7. **Copies.** One event holds every copy of a run whose bytes agree. A rewrite of one copy splits
   that copy off into its own event; the others keep theirs. A node-only move of one copy moves the
   shared event with every location, so the event's instant is that of the copy moved last.
   `verify` replays every location. The event is `SOURCE_GONE` only when no copy still holds its
   saved state. A from-scratch build of copies whose `node.json` files name different instants
   gives one event per instant (the S3 run identity includes the instant). There, an incremental
   ledger and a rebuilt one differ.
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
