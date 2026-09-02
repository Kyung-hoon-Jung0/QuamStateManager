# 155 — "It is not only Datasets": where the other menus' time actually goes

**2026-09-01, user-directed follow-up to docs/154.** Users report that Overview,
Live State Edit and Param History slow down too, not just Datasets — and that it
gets worse over months. docs/154 fixed two walks on the `/datasets` path; this is
the measurement of what is left, on the other surfaces.

## 1. Method: count syscalls, not milliseconds

The customer's workspace is an SMB share (`\\nasdaq.snu.ac.kr\QDL`, mapped `Z:`).
Every `stat`/`scandir`/`open` there is a network round-trip, so a route doing 800
stats costs ~8 ms on this development NVMe and **~1.4 s** on their box. Wall clock
measured locally cannot see the problem at all; the operation count is the same
number on both machines, so that is what was measured.

SM was served under waitress (16 threads, production mode) with `os.stat`,
`os.scandir`, `os.listdir`, `os.path.exists` and `open` wrapped by a per-request
counter that buckets each call by which filesystem the path is on (share / live
chip / SM's own instance dir). For attribution the wrapper also records the
innermost `quam_state_manager` frame that made each share call — exact, not
sampled, which is the half py-spy cannot give.

Fixture: the real 13-qubit / 16-pair chip
(`SNU-QDL-Qualibrate/quam_state_9Q_Temp`, 371 KB state.json) against a synthetic
archive built to the customer's shape — 5,874 runs, **390 date dirs**, shallow
layout. 390 date dirs is roughly 13 months of daily measurement, which is the
"over months" axis the reports describe.

The `est. on NAS` column is an extrapolation, not a measurement: the measured
20.2 s of docs/154's py-spy profile, over the 11,420 ops that same walk makes on
this shape, gives **1.8 ms per share op**. The NAS was down while this was
written (§6).

## 2. What each surface costs, per request

| surface (what the user calls it) | route | share ops | est. on NAS |
|---|---|---|---|
| **Chip Status / Overview** | `/topology` | **7,831** | **~14 s** |
| Datasets | `/datasets` | 1,564 | ~2.8 s |
| Trends | `/trends/data` | 1,564 | ~2.8 s |
| **Param History**, first open | `/param-history/alignment` | 1,180 | ~2.1 s |
| **Live State Edit** — 🕘 on a value | `/field/history` | 789 | ~1.4 s |
| **Live State Edit** — 🕘 on a column | `/bulk/column-history` | 786 | ~1.4 s |
| *every page*, the new-run poll | `/datasets/poll` | 782 | ~1.4 s |
| *every page*, the live-wake long poll | `/datasets/wait` | 782 | ~1.4 s |
| Param History (grid) | `/param-history` | 17 | ~0.03 s |
| **Live State Edit (the page itself)** | `/bulk` | **0** | 0 |
| Json Tree, Pulses, Diagnostics, Instrument, Qubits/Pairs/Flux/Couplers/Resonators | | 0 | 0 |

Two things fall out of that table immediately.

**Overview is the worst surface in the app, by 5×.** `/topology` *is* the Chip
Status page — the menu users named first.

**Live State Edit's own render touches the share zero times.** Its 112 ms is
local CPU on a 2.5 MB grid (docs/141's territory). What makes that page *feel*
slow on a big archive is not the page: it is the two background polls every open
tab runs, and the two 🕘 history popovers, which are the things a user clicks
constantly while editing. Naming the page was right; blaming the grid would have
been wrong.

## 3. One root cause under all of them

Attribution of `/topology`'s 7,831 share calls, by frame:

```
  3900  /core/dataset.py:922  _current_mtime      (entry.is_dir())
  3900  /core/dataset.py:925  _current_mtime      (entry.stat())
    10  /core/dataset.py:916  _current_mtime      (folder_path.stat())
    10  /core/dataset.py:921  _current_mtime      (iterdir)
    10  /core/rb_gate_fidelity.py:74 from_run_folder
     1  /core/history.py:2034 list_snapshots
```

`DatasetStore._current_mtime` answers *"has anything new landed?"* by stat'ing
**every date dir under the root**, two syscalls each (`is_dir()` then `stat()`).
One call is therefore `2 x (date dirs)` share round-trips — 782 at 390 date dirs
— and it is deliberately not memoized (docs/105 #8: a TTL memo broke the pinned
write-then-poll contract).

`/topology` calls it **ten times per render**, once per 2Q edge. That is
docs/138's derived per-gate RB fidelity: `derive_for_edges` calls
`_rb_run_folder(load_id)` per edge, and each call re-enters
`_active_dataset_stores(fast=True)` -> `_get_or_create_store(cand)` ->
`rescan_if_stale()` -> a full staleness sweep of the archive. Ten edges, ten
sweeps, for ten run-folder lookups that between them read ten files.

## 4. Why it gets worse every month — measured, not argued

The cost is linear in **date dirs**, and an archive gains one per day of
measurement. Holding everything else fixed and adding date dirs only:

| date dirs | `/topology` share ops | `/datasets/poll` |
|---|---|---|
| 90 | 1,830 | 182 |
| 390 | **7,830** | **782** |

Exactly `edges x dates x 2`. Run count is not the axis (the archive kept its
5,574 runs across both rows) — **days are**. A lab that measures daily adds ~7 s
to every Chip Status render per year on this share, forever, and nothing in the
app ever prunes or amortizes it.

## 5. Fixes, in value-per-risk order

**F1 — `/topology`: one staleness sweep per render, not one per edge.
SHIPPED.** `_topology_with_derived_rb` resolves `_active_dataset_stores` once
and hands `derive_for_edges` a resolver that reuses it; `_rb_run_folder` gained
an optional `stores=` and is byte-identical without it. The resolution is LAZY
— a chip with no Standard-RB `load_id` resolved nothing before and must keep
paying nothing, so an eager sweep would have been a regression for those chips
dressed as a fix.

**F2 — halve `_current_mtime`. SHIPPED.** `entry.is_dir()` on a `Path` from
`iterdir()` is a syscall; the same answer comes free from `os.scandir`'s
`DirEntry`, which carries the file-type bits from the directory listing. The
name test also moved in FRONT of it, so a non-date entry (a README, an export
folder) now costs nothing at all. `os.stat` is kept for the **mtime** — see
§5b. 2 ops per date dir -> 1, for every caller, at no semantic cost.

**F2' — `_workspace_token` had F2's shape too. SHIPPED.** The other walker on
these routes made the same two calls per entry, and on the alignment path a
third: docs/139's own-root exclusion called `Path.resolve()` on EVERY entry at
both levels. `os.scandir` makes `is_dir()` free the same way, and the exclusion
now resolves the ROOT once — every entry it tests is a direct child of a
directory the walk already holds, so for anything that is not a symlink the
resolved path is `parent_resolved / name` and the predicate is string work. A
symlink, or a parent that would not resolve, falls back to the original.

**F4 — the background polls stopped sweeping to answer questions they do not
ask. SHIPPED.** These two run forever on every open tab, and they were the
whole reason a page nobody is looking at slows down the page somebody is.
`_active_dataset_stores` gained `rescan=False`:

- `/datasets/changes-since` (every **5 s**) rescanned every store in the
  lookup and then rescanned it again inside `changes_since` — two full sweeps
  of every date dir per call. Worse, the first one carried **no deadline**, so
  docs/105 #4's budget was being spent before the bounded sweep it belongs to
  was ever consulted. The lookup no longer rescans; the deadline-bearing sweep
  is the only one left.
- `/datasets/wait` wants the folder **paths** and nothing else — the watcher
  takes its own signature on its own thread. It built and rescanned a run
  table that nothing read.

With `rescan=False` the `run_count` filter is skipped too: a count we
deliberately did not refresh must not be used to drop a folder for being
empty, which would drop exactly the folder about to receive its first run.
`/datasets/wait` therefore now watches an empty data folder, which it never
did before.

**F3' — the two walkers stopped walking the same tree twice. SHIPPED.**
`HistoryManager._workspace_token` stats every date directory to answer *"did
the workspace change?"*; `DatasetStore._current_mtime` stats every date
directory to answer *"did this store's root change?"*. Those are the SAME
directories — a data folder is a workspace root in the shallow layout, and the
chip directory the token descends into in the deep one (a run entry's
`folder_path.parent.parent` is the root in one and the chip dir in the other,
which is exactly what the token stats) — so a `/datasets` render walked the
whole archive twice, in two functions, for two answers.

`core/dir_sample.py` is the one walk. It merges the I/O, not the questions:
each walker still computes its own fingerprint from it, and a pin holds the
two answers apart. Two costs are kept separate because the callers want
different subsets — the LISTING (names + type bits + the symlink bit) rides
one `os.scandir`, while an mtime needs an `os.stat` per entry and is taken
lazily, memoized per entry. The store asks about date directories only, the
token about every child directory, and the second caller in a request pays for
none of them again.

**The scope is a request, and that is the whole argument.** docs/105 #8
rejected a *TTL* memo because a run written milliseconds before a poll must be
seen by THAT poll; a request-scoped cache keeps that exactly, because every
poll is its own request and takes its own sample. `before_request` opens the
scope and `teardown_request` closes it (it runs even when the view raised),
and `begin()` overwrites whatever a missed teardown left, so a leaked scope
cannot outlive one request on a reused worker thread. Outside a request — the
scheduler worker, autofit, the CLI — no scope is ever opened, nothing is
cached, and both walkers behave precisely as they did before the module
existed. All four properties are pinned.

### 5a. Measured (same fixture, 390 date dirs)

| route | before | F1+F2 | +F2' | +F4 | +F3' | total |
|---|---|---|---|---|---|---|
| `/topology` (Chip Status / Overview) | 7,831 | 403 | 403 | 403 | **403** | **-95%** |
| `/datasets` | 1,564 | 1,174 | 784 | 784 | **392** | **-75%** |
| `/trends/data` | 1,564 | 1,174 | 784 | 784 | **392** | **-75%** |
| `/datasets/changes-since` (every **5 s**) | 1,568 | 784 | 784 | 392 | **392** | **-75%** |
| `/param-history/alignment` | 1,180 | 1,180 | 401 | 401 | **401** | **-66%** |
| `/datasets/poll` (every 60 s) | 782 | 392 | 392 | 392 | **392** | **-50%** |
| `/field/history` (the 🕘 popover) | 789 | 399 | 399 | 399 | **399** | **-49%** |
| `/datasets/wait` (the long poll) | 782 | 392 | 392 | **0** | **0** | **-100%** |

(The `/datasets/wait` row is measured in the steady state. F6 in §6 records
what it cost on the request right after a run landed — 392, not 0 — and closes
that too.)

Every surface users named is now between 0 and 403 share operations — about
0.7 s on the share where the worst of them was ~14 s, and where all of them
grew by one date directory per day of measurement.

**What a tab costs while nobody touches it**, at 1.8 ms per share op — the
number that decides whether four open tabs saturate the pool:

| | before | after |
|---|---|---|
| `/datasets/changes-since`, every 5 s | 16.9 s/min | **8.5 s/min** |
| `/datasets/wait`, continuous | 1.7 s/min | **0** |
| `/datasets/poll`, every 60 s | 0.7 s/min | 0.7 s/min |
| **per open tab** | **19.3 s/min** | **9.2 s/min** |

`/topology`'s attribution afterwards shows exactly ONE `_current_mtime` sweep
where it was ten; the alignment path's per-entry `resolve()` storm is gone;
and `/datasets`' attribution is now a single `dir_sample` listing — 390
mtimes, one stat, one scandir — serving both walkers.

Pinned by `tests/test_share_io_cost.py` (30 asserts) plus the docs/154 pins in
`tests/test_workspace_walk_depth.py`, all counting syscalls and never a clock.
**26 mutations, 26 caught** across the five fixes — including the three
docs/154 mutations re-verified against each new code shape, and two whose only
job is to prove a pin is not vacuous.

**A pin can measure the wrong moment, too.** The first pin for "the symlink
bit rides the listing" read `smp.children` inside the spy — which measures a
tuple unpack, not the listing — and the mutation swapping the free
`DirEntry.is_symlink()` for `os.path.islink` (an `lstat` per entry, exactly
the cost F2' removed) passed it untouched. The pin measures `sample()` itself
now, as an invariant across sizes, and the spy counts `lstat` as well: a
syscall the spy does not hook is a syscall the pins cannot hold down.

**A pin can go blind without failing.** docs/154's spy hooked `Path.stat` and
`Path.iterdir`; F2' moved the walk onto `os.scandir` + `os.stat` and every one
of those pins started asserting against an empty set — they had stopped
watching the walk and would have passed a mutation that deleted it. The spy is
hooked at the `os` layer now, which both spellings reach and neither can
bypass. A spy that sees only one spelling of a syscall pins the spelling, not
the cost.

Regression: 858 passed across the dataset/history/RB/poll/web suites. Every
failure is pre-existing (§7): the docs/87 tmp-path case class, and
`test_poll_stability` flakes that reproduce at the same rate with that file run
entirely alone (1 in 3) and pass 10/10 in isolation with and without these
changes. One of them was measured at 20/20 passing WITH F2' and 17/20 without.

### 5b. The one thing that could not be pinned, and why that is the finding

`DirEntry.stat()` is the obvious way to make the mtime free too, and docs/141
§4ac already forbids it. The natural semantic pin — write a run inside a date
dir with nobody touching the dir, assert the fingerprint moved — was written,
and it **passed under exactly that mutation**. Measured cause, 20 trials per
gap on this NTFS volume:

| gap after `mkdir` | `DirEntry.stat()` saw it | `os.stat()` saw it |
|---|---|---|
| 0.00 s | 1/20 | 6/20 |
| 0.02 s | 0/20 | 5/20 |
| 0.50 s | 5/20 | 8/20 |
| 1.20 s | 5/20 | 16/20 |

NTFS updates the parent's recorded timestamps for a child lazily, so within
about a second of a `mkdir` **neither** call reliably reports the change.
`os.stat` is markedly better and still not deterministic. A pin built on that
is a coin toss, which is worse than no pin — so the guard is the COST pin
(`de.stat()` costs zero syscalls, so the slope pin reds), verified by mutation,
with the reasoning recorded beside it in the test file.

Worth separating from the fix: this also says SM's own new-run detection on
Windows has an inherent sub-second latency that no amount of polling removes.
It has never mattered — a real run folder takes far longer to write than the
window measured here, and `run_watch` (docs/141 §4p) polls at 0.5 s anyway —
but it is the reason a write-then-poll test in this project always bumps the
mtime explicitly rather than trusting the filesystem to have noticed.

## 6. The backlog after F1 + F2 + F2' + F3' + F4 — every item measured, none guessed

Attribution of what is LEFT, on the shipped code at 390 date dirs. Frame counts
are exact, not sampled.

```
/datasets  ·  /trends/data        392 ops     <- was 1,564
   390  dir_sample  mtime()            (one os.stat per date dir, ONCE,
     1  dir_sample  _read                shared by both walkers)
     1  dir_sample  _list

/param-history/alignment          401 ops     <- was 1,180
/topology                         403 ops     <- was 7,831
/datasets/poll (60 s)             392 ops     <- was 782
/datasets/changes-since (5 s)     392 ops     <- was 1,568
/datasets/wait (continuous)         0 ops     <- was 782
```

What is left is one `os.stat` per date dir, once, for both walkers together.
There is no duplicated I/O left on these paths — only F5's single sweep, which
is the shape of the question rather than a defect in the answer.

**F3 — a request-scoped fingerprint memo. Answered by F3', and the record of
how it was nearly dropped is worth keeping.** F3 was deferred on the reasoning
that F1 had removed the repetition — measured on `/datasets` and `/topology`,
where that was true. It was false on `/datasets/changes-since`, the route that
runs every five seconds on every tab, which had not been measured when the
call was made and was doing two sweeps per request. F4 removed that pair
outright; F3' then took the request-scoped cache to the place the duplication
actually lived — one directory listing, shared by two walkers — rather than
memoizing a fingerprint. The lesson is smaller than the fix: a deferral is
only as good as the routes it was measured on.

**F4' — could a poll read the watcher's tick instead of sweeping at all?**
F4 was originally framed that way, and reading `run_watch.signature()` refuted
the framing: the watcher looks at the ROOT and the NEWEST date dir only, while
`_current_mtime` looks at every date dir. Gating a poll on it would silently
stop noticing a change in an OLDER date dir — a backfilled import, a figure
landing in yesterday's run, a folder deleted — and the polls are documented as
the watcher's safety net, so the net would be gating on the thing it protects.
What shipped as F4 instead removes only sweeps that were duplicated or unused
and changes no staleness question. A real version of this needs a periodic full
check to keep the net, and should be judged against the ~9 s/min a tab costs
now rather than the ~19 it did.

**F5 — the O(dates) sweep is unbounded by construction.** Even at one syscall
per date dir, a five-year archive is ~1,800 round-trips per poll. Nothing in
the app ever caps, tiers or ages this. Any real answer is F4 or an OS change
notification (`ReadDirectoryChangesW`), not a smaller constant.

**F6 — `/load` costs 804 share ops. SHIPPED — and it was never a cost of
reading the chip.** The 804 was measured before F1–F4; on the shipped code it
was 421, and attribution put 390 of those in ONE chain that has nothing to do
with the chip being opened:

```
  _maybe_data_folder_suggest        (routes.py — every chip activation)
    -> _data_folder_candidates
      -> _dataset_candidate_folders(fast=True)
        -> HistoryManager._workspace_token
          -> dir_sample.mtime   x 390   (one os.stat per date dir)
```

`_dataset_candidate_folders(fast=True)` exists precisely to skip that walk
(docs/142 measured it at ~1.2 s on a 9p workspace and took it off the per-run
click). It validates its cache on `ws.version` instead. But on a MISS it fell
through to the token anyway — and **that token can never produce a hit there**:
the token slot is validated on `ws.version` too, and a fast miss has already
failed that test, so the comparison below it is guaranteed false. It was
computed only to be STORED, priming a cache for some future slow caller, at
exactly the cost the flag exists to avoid.

**Which made it a recurring cost, not a cold-start constant.** The scanner
bumps `ws.version` on every rescan that finds something new, so the walk was
re-paid every time a run landed — and paid by whichever fast caller arrived
first afterwards. Measured both ways at 390 date dirs, on the same fixture:

| after ONE new run lands | pre-F6 | post-F6 |
|---|---|---|
| `POST /load` first | **406** | **14** |
| `/datasets/wait` first | **392** | **0** |
| `POST /load`, cold (first in the process) | **421** | **29** |

That is also an honest correction to §5a. **`/datasets/wait`'s "0 ops" was
true only while nothing changed** — and the long poll re-arms exactly when
something changes, so on a lab that measures all day it paid a full sweep on
the request right after every run. It is 0 now in both states.

The fix is four lines of control flow: a fast caller never computes the token,
rebuilds from the in-memory tree (which is all the candidate list is ever
derived from — `ws.root_folders` + `ws.all_entries`; the token never enters the
result), and stores its result with `token = None`. A slow caller reads an
untokened slot as a miss and recomputes, so `fast=False` keeps its staleness
contract byte for byte. Every route's steady-state cost is unchanged, measured
against the pre-F6 tree across the whole §2 table — this fix moves the cold
start and the first request after a change, and nothing else.

**Audited and left:** `HistoryManager.scan_workspace_alignment` also computes
`_workspace_token`, and that one stays. It is not the same shape — there the
token is the cache's ONLY validator (no `ws.version` equivalent exists for a
scan keyed on the loaded chip's fingerprint), and it guards a scan that reads
two JSON files per run rather than a set built in memory.

**The pin lesson, which is the reason this took a rewrite:**
`test_fast_candidates_rebuild_on_workspace_version_bump` (docs's own
pre-delivery audit) asserted the right property — *a new candidate dir must
become visible to fast callers once the scanner finds it* — by **counting
`_workspace_token` calls**. That is a proxy for "did it rebuild", and F6
removed the thing being counted, so the pin went red while the property it
names stood untouched. It asserts the property now: a root the scanner has
discovered appears, one appended behind the scanner's back does not. Same
lesson as docs/141 §4af — *a pin can measure the wrong moment.*

**And one of the new pins was flaky before it was right.** The two that drive
real code and compare two archive sizes were counting syscalls with the file's
process-wide spy, so anything SM does on a daemon thread — the docs/142 listing
hydration, a deferred index rebuild, `run_watch` — landed in whichever side's
counter happened to be open. Measured at about one failure in three when the
file runs beside its neighbours, and green in isolation, which is the worst
shape a pin can have: docs/141 §4ae showed a single flaky test turning a whole
mutation sweep into coin tosses reported as verdicts. `_fs_spy` grew an
`own_thread=True` option (default off, so every pin written before it counts
exactly what it counted), and both syscall-counting pins use it. 6 consecutive
three-file runs, one pre-existing failure each and no others.

Pinned by `TestFastCandidatesNeverWalkTheArchive` in
`tests/test_share_io_cost.py` (7 tests: two cost slopes that must not scale
with date dirs, the direct "no token on the fast path" statement, and four
that hold the cache's promises — discovery after a bump, the cache hit while
the version holds, the slow caller's token validation, and an untokened slot
never satisfying a slow caller). **5 mutations, 5 caught**, one per property:
restoring the fall-through, dropping the fast store, letting a slow caller
accept an untokened slot, ignoring `ws.version` on the fast path, and ignoring
the token on the slow one.

**F8 — jsdom, and this environment's baseline. DONE** (§8): `npm install`
run, 101/101 selfchecks executing, and the full-suite baseline established at
35 failed / 6,911 passed with every failure reproduced on the pre-campaign
tree. What remains open here is smaller: the 35 are worth triaging into
"stale pin" and "genuine OS-behaviour difference", which nobody has done.

**F7 — tests write into the developer's real `instance/` dir. SHIPPED.**
Recorded as an observation during the F1–F4 work and measured properly
afterwards. `create_app()` with no `instance_path` falls back to
`default_instance_path()`, which is `None` in a repo checkout so Flask derives
`<repo>/instance` — right for the app, and wrong for the ~22 test call sites
that build an app that way.

**Measured, on six of those files alone: 36 files disturbed.** 33 stray
working copies created, and three REWRITTEN — `last_session.json`,
`workspace_roots.json` (the developer's configured workspace roots, replaced
by a test's tmp paths) and docs/139's `history/_fingerprints.json`. Nothing
failed loudly and nothing reached a commit; it just quietly replaced state a
person was relying on.

**A census made the scale plain.** Every one of the 96 working copies in that
directory pointed at a `pytest-of-*` folder that no longer exists, from pytest
runs numbered 78 to 595 — months of them — and all 105 cached workspace
listings were rooted in a tmp dir too. Not *some* litter: the directory was
litter, with nothing of the developer's own left in either place.

Two halves:

- **Stop making more.** `tests/conftest.py::_isolate_instance_dir` (autouse)
  redirects the default into pytest's own tmp tree, lazily, so only a test
  that actually builds such an app pays for a directory.
  `create_app(testing=True)` takes an earlier branch — `tempfile.mkdtemp` —
  which never consults the default and had left **327 `quam_test_instance_*`
  directories in %TEMP%**; that one call is redirected by its own prefix into
  the basetemp pytest garbage-collects, with no change to the production
  branch. Opt out with `@pytest.mark.real_instance_path`, for the handful of
  tests that assert on the instance-path POLICY itself. Re-measured after:
  **36 → 0.**

- **Clear what the un-isolated years left.** `_purge_test_leftovers` already
  dropped leaked history dirs and tmp paths out of `workspace_roots.json` /
  `last_session.json`; it now also drops working copies and cached listings,
  under the same "$TEMP only" rule the function already documents. A working
  copy needs **two** signals — under `$TEMP` *and* gone from disk — because a
  working copy can hold unapplied edits and a real chip folder can be absent
  for innocent reasons; what a real chip never is, is under the system
  tempdir. A cached listing needs only the first: it is a pure cache, rebuilt
  by the ordinary staleness path (docs/142). On this machine that clears 96
  copies and 105 listings and keeps **zero** of either, which is the census
  above restated as an outcome.

Pinned by `tests/test_instance_isolation.py` (13 tests). **4 of 4 mutations
caught** on the fixture, **6 of 6** on the purge.

**Three drafts of one pin, each corrected by a mutation rather than by
re-reading it.** The pin for "a real chip that is merely missing is never
deleted" first used an unreachable UNC share — and passed with the `$TEMP`
rule deleted, because on Windows `Path.exists()` against a dead UNC host
RAISES, so the copy was kept by a different guard and never reached the rule
at all. Draft two moved to a `.invalid` host, which answers False politely —
and stopped testing the OSError guard, whose own mutation then went green.
Draft three separates them: a plain absent path pins the `$TEMP` rule, and a
simulated `OSError` on a path **under** `$TEMP` pins the conservative branch
(the `$TEMP` test comes first, so a share path never reaches it — an
unreachable share is kept by the `$TEMP` rule, not by the error handler). The
assertions never moved across those drafts; only the mutations distinguished
them. Same lesson as F6 above and docs/141 §4af, arrived at from the other
direction: there, a pin measured the wrong moment; here, a pin passed for the
wrong reason.

**Deliberately not done:** nothing deletes a working copy that is not under
`$TEMP`, whatever its state. A chip on a disconnected share is indistinguishable
from a deleted one, and the copy may hold edits nobody has applied — so the
directory can still grow for a user who renames chips, and that is the right
trade.

## 7. Does this help anybody but the customer? — the A/B

Everything above was measured on one archive shape at one size, on a machine
whose workspace is a NAS share, and every "on the share" figure is arithmetic.
That leaves the question that actually matters for shipping: **does a lab with
a small archive on a local disk get anything?** So the pre-campaign commit
(`c247338`) was checked out into a worktree and the same routes were driven
against three archive sizes, in-process, counting syscalls against the
workspace directory. Local NVMe, no share involved.

| | **30 runs** / 10 dates | | **300 runs** / 60 dates | | **1,440 runs** / 180 dates | |
|---|---|---|---|---|---|---|
| route | before | after | before | after | before | after |
| `/topology` (Overview) | 220 / 16.7 ms | **12 / 6.9** | 1,220 / 71.2 | **62 / 10.3** | 3,620 / 247 | **182 / 18.0** |
| `/param-history/alignment` | 132 / 19.4 | **13 / 6.3** | 1,142 / 133 | **63 / 11.0** | 5,042 / **627** | **183 / 22.8** |
| `/datasets` | 114 / 5.5 | **12 / 1.5** | 904 / 51.2 | **62 / 6.6** | 3,784 / 219 | **182 / 21.9** |
| `/trends/data` | 114 / 5.5 | **12 / 0.8** | 904 / 43.2 | **62 / 3.4** | 3,784 / 224 | **182 / 14.2** |
| `/datasets/changes-since` (5 s) | 44 / 2.0 | **12 / 1.0** | 244 / 12.3 | **62 / 3.6** | 724 / 39.5 | **182 / 12.8** |
| `/datasets/wait` (continuous) | 22 / 1.5 | **0 / 0.4** | 122 / 6.8 | **0 / 0.3** | 362 / 26.7 | **0 / 0.2** |
| `/field/history` | 23 / 6.8 | 13 / 6.7 | 123 / 18.1 | 63 / 18.3 | 363 / 65.1 | 183 / 60.8 |

(ops / milliseconds, best of three after warming.)

**The answer is yes, and it is stronger than the share arithmetic suggested.**

- At **30 runs** — a lab two weeks old — operation counts already fall ~90% and
  a Chip Status render goes 16.7 → 6.9 ms. Real, free, and not something a
  person would notice.
- At **300 runs** — a few months — Param History goes **133 → 11 ms** and Chip
  Status **71 → 10 ms**. That is the point where it stops being a rounding
  error on a local disk.
- At **1,440 runs** — about a year of daily measurement — Param History goes
  **627 → 23 ms (27x)** and Chip Status **247 → 18 ms**. Plainly perceptible,
  with no network anywhere.
- **No route got worse at any size.**

`/field/history` is the honest exception: its share ops halve but its wall
time does not move (65 → 61 ms), because that route is dominated by snapshot
and SQLite reads rather than by the directory walk. It is not a beneficiary of
this campaign, and saying otherwise from the op count alone would be wrong.

Two things this A/B corrected:

1. **A defect in the measurement itself.** Driving `/datasets/changes-since`
   with `ts=0` returns every row in the archive, and under `TESTING` the
   docs/132 EXP-ingest then runs INLINE for each one — file I/O a real poll
   never pays. The first run of this table read 3,604 ops at the mid size for
   that route and showed almost no improvement; driven the way the browser
   drives it (the previous response's cursor) it reads 724 → 182.
2. **docs/154's finding, confirmed at a second size.** The pre-campaign
   `/param-history/alignment` costs 5,042 ops at 180 date dirs — that is per
   RUN, not per date, which is exactly the O(runs) token this campaign opened
   with. Worth stating plainly: **that half of the benefit only reaches labs
   whose workspace root is the SHALLOW layout** (`<root>/<date>/<run>`, which
   is what qualibrate's `storage.location` is). A deep-layout root already
   stopped at the date level and sees only the other fixes.

## 8. The client side, and this environment's real baseline

Everything above is server-side. The client half was unverifiable here until
now, because the `.cjs` selfchecks need jsdom and this machine did not have
it — so 78 DOM-level pins were reporting as failures-or-skips rather than
running (the state CLAUDE.md warns about, and docs/120 records once hid four
genuinely failing selfchecks).

`npm install` was run. Results:

- **`npm run selfcheck`: 101 passed, 0 failed, 0 skipped, of 101.** The whole
  client suite actually executes.
- The nine pytest drivers that had been failing (`test_pane_state`,
  `test_pulses_commit`, `test_undo_nav`, `test_kb_polish`, `test_grid_editing`,
  `test_ds_flow`, `test_figure_lightbox`, `test_interactive_theme`,
  `test_apply_ux`) — **21 passed**. Those failures were jsdom's absence, not
  regressions.
- And structurally: `git diff c247338..HEAD -- '*.js' '*.cjs' '*.html' '*.css'`
  is **empty**. This campaign touched four Python files, two test files and two
  docs. A client-side regression was impossible by construction; the selfchecks
  confirm the harness that would have caught one now runs.

**Baseline for this environment (`QM_Qualibrate`, Windows 11, jsdom
installed), full suite minus `TestWaitForServer`: 35 failed, 6,911 passed, 280
skipped, ~21 min.** All 35 were re-run against the pre-campaign worktree: 33
fail there identically. The remaining two are flakes, measured 15x on each
tree — `test_state_only_run_waits_for_wiring` 0/15 on both (it only fails
inside a larger run, an ordering effect) and
`test_native_path_anchors_on_the_config_dir` 2/15 on both. **Zero regressions
from the campaign.**

The 35 fall into the classes this project already documents: the docs/87
OS-behaviour set (tmp-path case identity, WSL kernel checks, Windows
file-locking timing), the `test_scanner` staleness-timing group, and the stale
pins CLAUDE.md already records for `test_auto_apply` and `test_auto_sync`.
Nothing in the client half.

## 10. F8' — the 36 failures, triaged one at a time

§8 established that the campaign caused none of the failures (33 of 35 fail
identically on the pre-campaign worktree). It did not establish **why** each one
fails. So every failing test was run ALONE, three times, and classified by what
happened — never by what its name suggests.

```
total                     36
fails alone every time    32   deterministic: an OS difference or a stale pin
flaky alone (1 of 3)       2   test_safe_io::reader_survives_concurrent_os_replace
                               test_qualibrate_location::native_path_anchors_on_the_config_dir
PASSES alone (0 of 3)      2   test_poll_stability x2 -- ordering / shared state,
                               they only fail inside a larger run
```

The 32 deterministic ones are not one class. Six of them turned out to be
**defects in this repository**, not OS behaviour — and one of the six is in the
product, not in a test.

### 10a. The product defect: the tray's hover contradicts Auto-Sync

`432c764` ("the How-this-works banner becomes the sync badge's hover", a docs/132
follow-up) folded a two-branch banner into one unconditional `title` string. The
branch it dropped was this one, whose own comment said what it was for:

```jinja
{# docs/117: while auto-apply is ON this sentence would be FALSE -- the
   whole point of the mode is that leaving a field writes the chip. #}
{% if auto_apply %} ... {% else %}
<b>How this works:</b> edits stay in your <b>working state</b> (a private copy)
until you press <b>Apply to live</b> -- reversible with <b>Revert last apply</b>.
```

So with Auto-Sync ON the badge's hover told the user their edits stay private
until they press Apply (false — that mode exists precisely so they do not have
to), and pointed at a **Revert last apply** button that is not the one rendered
underneath: the button correctly reads *Revert this session*, because under an
armed session the anchor IS the session. Two of the three `test_auto_apply`
failures were this, failing honestly for eleven days. The branch is restored,
in the docs/120 vocabulary.

Worth stating plainly: **the tests were right and the code was wrong**, which is
the opposite of what "35 known failures" invites one to assume. That is the
argument for triaging a stale-looking list instead of rounding it off.

### 10b. Two pins that measured something other than their subject

| pin | what it actually measured | now |
|---|---|---|
| `test_auto_apply` ×2 (label), `test_auto_sync` | the string "Auto-apply", which docs/120 item 8 renamed to **Auto-Sync** on the user's own instruction | assert the current name |
| `test_no_new_poller_was_added` | that `/state/drift` sits within **4,000 characters** of `/auto-sync/pull` in app.js. It is 4,696 now — the pin expired because the file grew | assert **structurally**: no `setInterval(`/`fetch(`/`XMLHttpRequest`/`function poll` between the two, which is what "no new poller" means |

### 10c. Three fixtures that could not run on Windows at all

None of these three is an OS *behaviour* difference — each is a POSIX assumption
baked into a fixture, so the rule under test was never exercised here.

- **`test_capabilities_routes` ×2.** Both posted `output_path` as a bare POSIX
  literal. `WindowsPath` with no drive is **not absolute**, so `_ingest_abs_path`
  answered 400 and the route returned *before* the capability gate — the gate
  under test never ran. (`test_build_proceeds_after_ack`, which uses `tmp_path`,
  passes; that contrast is the whole diagnosis.) Now `tmp_path`.
- **`test_label_html_is_escaped`.** It built a directory literally named
  `<script>alert(1)</script>`. Windows forbids `< >` in a filename, so the
  fixture died with WinError 123. Payload is now `a&b'c` (legal everywhere, same
  autoescape). **Fixing it exposed a second defect in the pin itself**: the
  assertion was *absence of the raw payload*, which also passes on a page that
  renders no label at all — and that is what was happening, because a raw `&`
  in the path split the query string so the source was never read. The payload
  is percent-encoded now and the assertion is that the **escaped** form is
  PRESENT, which cannot pass vacuously.
- **`test_scan_file_always_fresh_even_on_mtime_size_collision`.** The helper
  wrote with `write_text` (which translates newlines to CRLF on Windows) and the
  test then rewrote with `write_bytes` (which does not), so a `_pad_to(.., 400)`
  source was 403 bytes and the size collision the test needs never occurred:
  `assert 400 == 403`. The helper writes bytes now.

### 10d. What is left, and what it is

Twenty-six deterministic failures remain, and they are the classes this project
already documents — tmp-path case identity (`test_state_coherence` ×4,
`test_web` ×3, `test_chip_identity` ×2, `test_predelivery_audit_fixes`,
`test_sidebar_root_row`), the WSL kernel probe, Windows file locking, and the
**`test_scanner` staleness group (9, plus a tenth that needs two directories
differing only in case)**.

One correction to how that last group has been described here and in CLAUDE.md:
it is called a *timing* group, but all ten fail **3 of 3 alone**. Deterministic
failure is not what a timing flake looks like. **§10e measured it** — it is
neither a timing flake nor a staleness defect.

**Baseline after this section: 29 failed** (35 minus the six fixed), same suite
and same exclusion as §8.

## 10e. Fifteen failures were one character

The `test_scanner` staleness group looked like the most alarming thing in §10d:
if `_is_root_stale` really answered "stale" with nothing changed on disk, every
5-second poll would rescan the whole archive — which is precisely the complaint
this whole document is about. So it was measured before it was believed.

**On a real archive on disk, `_is_root_stale` is correct.** Twelve runs across
two chips and two date dirs, probed three times with nothing touched: `False`
every time, 0 of 7 spine dirs differing. Rebuilding the failing test's exact
shape with the test's own helpers, outside pytest: `False`, 0 differing. The
product was never the problem.

Inside pytest the same code raises `KeyError` on the root's own spine entry, and
`_is_root_stale` returns `True` for exactly one documented reason —
`spine is None` means "never probed, one rescan seeds it". The root was
registered, just not under the name the test used:

```
str(root)      C:\...\Temp\pytest-of-measurement\pytest-933\...\ws
root.resolve() C:\...\Temp\pytest-of-Measurement\pytest-933\...\ws
registered     C:\...\Temp\pytest-of-Measurement\pytest-933\...\ws
```

Windows is case-insensitive but case-**preserving**. pytest builds its base temp
dir from `getpass.getuser()`, which returns `measurement` here, while the
directory that exists on disk is `pytest-of-Measurement` — created once, long
ago, with a capital M, and reused ever since because `mkdir` on an existing
case-variant simply succeeds. `Path.resolve()` returns what is on disk. SM
canonicalizes every root and chip path it registers **on purpose**, so that two
spellings of one folder can never become two entries; the test then looks its
own path up under the spelling it was handed. Miss.

Proof, not inference: re-running five of those files with `--basetemp` pointed
at a correctly-cased directory turned 13 of their 18 failures green with no code
change (18 -> 5). The fix is one fixture in
`tests/conftest.py` that hands tests the canonical spelling
(`os.path.realpath`), a no-op wherever the two already agree — POSIX, and any
Windows box whose account name is lowercase.

**Fifteen tests, six files, all green** (22 failures -> 7): the nine
`test_scanner` staleness tests, `test_chip_identity` ×2, `test_predelivery_audit_fixes`,
`test_sidebar_root_row`, and `test_web` ×2
(`test_hx_vals_escapes_backslashes`, `test_session_handles_missing_folder`).

What that costs to have not known: these sixteen have been carried for months as
an "OS-behaviour class the product has to live with" (docs/87's list, quoted
forward into CLAUDE.md and into §8 of this document). They are not OS behaviour
and not a class. They are one machine's temp-directory casing, and the reason
nobody found it is that nobody ran one of them alone and asked what the KeyError
was actually saying. §10a is the same lesson from the other direction: a red
test that has been red a long time stops being read.

**Not fixed by this, and separate**: `test_state_coherence` ×4 (the cache key is
canonical now and the entry is genuinely absent — a real question, unanswered),
`test_web` ×2 (a concurrency race, a dataset payload),
`test_add_root_dedups_same_inode_spellings` (it needs two directories differing
only in case, which this filesystem cannot hold — arguably an honest Windows
skip), the two WSL probes, `test_safe_io` ×2, and `test_poll_stability` ×2
(order-dependent — they pass alone).

## 10f. The customer installed it — and named the one number we lacked

**2026-09-02, first real deployment of this campaign.** Chip Status, Live State
Edit and data loading are "상당히 빨라졌" — the surfaces §2 predicted and §5
fixed. Two remain slow, and one of them came with the fact this document has
been missing since §1: **their chip has 4,003 versions.**

§9 says the customer's real archive size was unknown and that 390 date dirs was
a model. This is the first measured number off their box, and it is about a
different store than §2 profiled — the snapshot history, not the run archive.

Reported: the landing at `127.0.0.1` takes **>10 s**, and pressing the project
button takes **~20 s**, after which the Versions chip reads 4,003.

### The measurement

A synthetic 4,003-snapshot store, syscalls counted per call and attributed to
the innermost SM frame (§1's method):

```
                                  ops      est. on their share (@1.8 ms/op)
COLD  (no manifest)             12,013     ~21.6 s
WARM  (manifest, cold process)   8,009     ~14.4 s
```

The reported ~20 s for the project press is the COLD figure almost exactly.
docs/143's manifest is doing its job — it removes the 4,004 `meta.json`
**opens** — but it never addressed the stats, and the stats are most of the
cost:

```
history.py:2137   child.is_dir()      4,004 stats
history.py:2141   meta_path.stat()    4,003 stats   <- the manifest's freshness check
```

### The fix, and why it was already written down

`hist_dir.iterdir()` yields `Path` objects, so `child.is_dir()` is **one stat
per snapshot for an answer the directory enumeration already carried**.
`os.scandir` hands it over free. This project already knows this —
`scanner._spine_of` carries the comment verbatim ("scandir, not iterdir +
is_dir(): the enumeration already carries each child's kind", docs/126 r3
profile) — it had simply never been applied here.

```
COLD   12,013 -> 8,010 ops    ~21.6 s -> ~14.4 s
WARM    8,009 -> 4,005 ops    ~14.4 s ->  ~7.2 s
```

Same 4,003 snapshots out, so this is a pure cost removal with no semantic
change; 237 history/aging/versions tests pass unchanged. Pinned by
`test_the_scan_costs_one_stat_per_snapshot_not_two`, which counts stats under
the chip dir and allows exactly one per snapshot (the freshness check) plus the
single `hist_dir` guard — mutation-verified by restoring the per-child
`is_dir()`.

### What is still there, and the trade it would cost

The remaining 4,003 stats are the manifest's per-entry freshness check, and
they are load-bearing: an in-place label/pin edit rewrites `meta.json` without
touching any directory mtime, which is what
`test_in_place_meta_edit_is_picked_up` protects.

They **could** go. After the scandir change the enumeration already yields the
complete child-name set for one op, so a manifest whose key set matches could
be trusted outright — 4,005 ops -> ~5, i.e. ~7.2 s -> effectively nothing. The
price is that an in-place `meta.json` edit made by something other than SM
would be invisible until the name set moved. That reverses a deliberate
docs/143 decision, so it is written down here rather than taken unilaterally.

### The landing is NOT explained by this

`GET /` is deliberately cheap (docs/63: the project cards are a lazy fragment
so the landing "never pays the TOML/doctor I/O"). Candidates for the >10 s,
**none of them measured**: `tray_status()`; up to six
`(Path(p) / "state.json").exists()` probes over the resume path and recent
chips, which on an unreachable or slow share can each block for seconds;
`_chip_display_name` per recent, which reads state.json; and first-request app
startup. **This section does not claim any of them.** The customer can split it
in one step with no code: the browser's Network tab shows `GET /` and
`GET /landing/projects` separately — whichever holds the seconds names the
half, and the second one would mean the qualibrate listing + doctor rather than
anything in this document.

## 10g. The sidecar earns its keep: a warm scan is 3 file operations, flat

§10f halved the snapshot scan and recorded the rest as a decision for the
user rather than a change to make unilaterally. The user took it.

The remaining 4,003 stats were the sidecar's per-entry freshness check. After
§10f's `scandir` the enumeration already yields the complete child-directory
name set for **one** syscall, so if that set is exactly the set the sidecar was
built from, the only thing that can have changed since is an in-place
`meta.json` rewrite. There are exactly two of those in the codebase —
`annotate_snapshot` (label / pin / note) and the run-id enrichment — and both
now refresh their sidecar entry through `_manifest_update_entry`. The other
three writers create or move directories, so the name set moves and the gate
catches them for free.

```
                                 before 10f    after 10f    after 10g
COLD  (no sidecar)                  12,014        8,011        8,011
WARM  (sidecar, cold process)        8,010        4,006            3
est. on the customer's share         ~14.4 s      ~7.2 s     ~0.005 s
```

Counted with one instrument across all three revisions — every `os.stat`,
`os.scandir`/`os.listdir` and every `open`, including the ones `pathlib`
routes through `io.open`. Each column is a fitted formula, not one run: at
n=200 and n=1,000 a warm scan is `2N+4` before 10f, `N+3` after it, and a
flat **3** after 10g (one stat of the chip dir, one read of the sidecar, one
scandir), so the per-snapshot term is gone rather than merely small. The
numbers first published here were one op lower in every warm cell: the first
harness wrapped `builtins.open`, which `Path.read_text` does not go through,
so it never saw the sidecar's own read. The conclusion is unchanged and the
correction is recorded rather than quietly swapped in.

**The trade, kept explicit.** A `meta.json` edited by something other than SM
— a person with a text editor — is invisible until a directory is added or
removed. docs/143 stat'ed every entry to catch exactly that; it was costing
4,003 syscalls a scan to defend SM's own cache of SM's own files against hand
editing. The reversal is pinned in both directions:
`test_an_in_place_label_edit_through_sm_is_picked_up` (SM's own edit must
survive) and `test_an_out_of_band_meta_edit_is_the_accepted_trade`, which
asserts the *limitation* so that reversing it later has to be a decision rather
than a drift — and also that it self-heals the moment the name set moves.

**One upgrade cost, once.** The sidecar version is bumped to v2, not for its
shape but because a v1 file was written by a build whose in-place writers did
not refresh it. So the first project open after upgrading still pays the cold
scan (~14 s on their share); every one after that is ~0.

### Two things the tests caught that reasoning did not

**A capture is momentarily meta-less, and memoising that is fatal.** The first
draft also recorded the dirs that had *no* `meta.json` so the gate could
exclude them. A capture creates its directory *before* writing `meta.json`, and
the writer lists priors mid-capture — so that scan memoised the half-written
snapshot as one to ignore, permanently. 40 tests went red inside a minute, and
the log line naming a snapshot dir without a `meta.json` said exactly what had
happened. The gate is plain equality now: a meta-less dir just costs the full
scan, every time, which is honest and self-healing.

**The addition-only case had no pin.** Mutating the gate from equality to a
subset test (`every entry I know still exists`) passed the whole suite. The
existing add-and-remove test could not catch it — its removal breaks the
subset and sends it down the slow path anyway. A snapshot that is only ADDED is
the case that matters, because it is the newest one, the one the user is
looking for. It has its own pin now.

That mutation is nonetheless a genuine **no-op**, proven rather than assumed:
the fast path looks each name up individually and bails to the full scan on the
first unknown, so a subset gate cannot actually serve a short list. The
equality gate stays as the earlier, clearer short-circuit; the per-name lookup
is what enforces it. A pin cannot fail on a mutation that does not mutate
(docs/141 §4ad established the same distinction), so the rule is guarded by
the pin above, which fails on the behaviour rather than the spelling.

## 9. What this does not show

- **The NAS was unreachable throughout** (the same network fault that ended the
  docs/154 session). Every "est. on NAS" figure is op-count x 1.8 ms, and the
  1.8 ms itself is derived from one earlier profile. The op counts are measured;
  the seconds are arithmetic.
- **The customer's real date-dir count is unknown.** 390 is a modelled
  13-months-of-daily-runs, chosen to make the scaling visible, not read off their
  archive. Their absolute numbers could be smaller or considerably larger.
- Client-side RENDER COST (docs/141's subject) is still a separate axis and is
  what dominates `/bulk` locally. §8 verifies client CORRECTNESS, not speed.
- Surfaces reachable only by clicking through a run (figures, h5 reads) were not
  swept.
- The measuring harness itself (`scratchpad/serve_instrumented.py`) wraps `open`
  and `os.stat` process-wide; it changes no SM code, but its own overhead is
  inside every millisecond figure above. Op counts are unaffected.
