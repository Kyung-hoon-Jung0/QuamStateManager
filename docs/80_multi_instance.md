# 80 — Two windows, one machine: polling stability and instance safety

*Status: Part 0 shipped 2026-08-05. Branch `feat/multi-instance-safety`.
Parts 1–3 follow on the same branch.*

## The question

> "Can users open several ports on one laptop and work on several projects at
> once?"

Verified empirically before answering, by running two real Flask servers on two
ports against one shared instance directory (the shipped default: every launch
resolves the same per-user data dir).

**Yes for separate projects.** Ports never collide — the desktop launcher takes
a fresh OS-assigned port per window (`main.find_free_port`) and there is no
single-instance guard. Working copies are keyed by a hash of the live folder
path, so two projects get `projA-76774235` / `projB-91e420fa` and their edits,
saves and applies never touch each other. History is per chip. The history
SQLite index is already opened `journal_mode=WAL` with `timeout=10.0`, so
multi-process access there was handled all along.

**But the instance directory is shared**, and four things live in it that
assume a single process. This document covers the first, which is also the one
that bites users who are *not* running two windows at all.

---

## Part 0 — monitoring must not stall, lose, or die

A data folder is written by something that is not us: a qualibrate node
mid-run, or a second State Manager window. That writer is not atomic at the
folder level — it makes a directory, writes `node.json`, then `data.json`, then
figures. **Every poll that lands inside that sequence sees a run that is real
but unfinished**, and that is the normal steady state of the newest run, not an
exception.

Three defects turned that normal state into failure. All three predate
multi-instance use; a second window only raises the hit rate.

### 0-1 A half-written file cost 0.9 s of sleep, per file, per scan

`safe_io.read_json` retries `_READ_ATTEMPTS=4` times with `_READ_BACKOFF_S`
growing 0.15/0.30/0.45 — **0.9 s of pure sleep** before giving up. That ladder
is correct for a live `state.json`, where a failed read really can be a read
that landed inside an experiment's atomic replace and retrying really does
recover it. It is wrong for a bulk scan, where the same failure means "the
writer has not finished yet" and the only useful response is to look again
later.

(A *missing* file was already cheap — the scan sites guard with `.exists()`.
The ladder fired on a file that was **present and truncated**, and on the
`exists()`→`open()` race.)

New `safe_io.scan_json(path) -> dict | None`: share-delete open, **one
attempt, no sleep, no exception**. `read_json` gained an `attempts` override
for the same reason. The three scan sites in `dataset.py` use it. The live
paths are untouched.

### 0-2 An incomplete parse could freeze

Marking the run incomplete is not enough on its own: `DatasetStore` re-parses a
folder only when its fingerprint moves, and — critically — the B27 date-dir
short-circuit skips `iterdir` entirely on a date dir whose mtime is unchanged.
Writing a file *inside* a run folder moves that folder's mtime, **not its date
dir's**. So a run whose writer completed it without otherwise disturbing the
tree could stay frozen with whatever partial metadata we happened to catch.

`RunInfo.incomplete` is set when a file was present but unreadable. Such a
folder gets a sentinel fingerprint `_INCOMPLETE_FP` (a shape `_stat_fp` can
never return, so the run-level cache always re-parses) and its path joins
`DatasetStore._incomplete_paths`, which defeats the date-dir short-circuit
until the run is whole. The path stays in `_folder_fp`, so vanish-detection
still covers a run that is deleted while incomplete.

The row is still published while incomplete — id, experiment name, date and
time come from the folder name and are already correct. Hiding it would be a
worse lie than showing it half-filled.

### 0-3 The poll could stop forever, silently

**Server.** `/datasets/changes-since` looped over folders with no `try/except`.
One raising store 500'd the whole poll; the client catches the error, never
advances its cursor, and the table simply stops updating with nothing on
screen to say so. Now each folder is isolated: a failure contributes no rows
and reports `now == ts`, holding its own cursor exactly like the existing D8
rescan-failure path, while healthy folders keep flowing. A wall-clock budget
(`_POLL_BUDGET_S = 6.0`) stops the walk rather than the response; skipped
folders hold their cursors and the answer carries `partial: true` +
`skipped: n`. The route cannot return anything but 200.

**Client.** `dataset-virtual.js` had the in-flight guard (right: a slow server
must not stack requests) but **no fetch timeout**. One request that never
settled left `pollInFlight` true and killed polling until the page was
reloaded. Monitoring that fails closed and says nothing is worse than
monitoring that is late. Now: an `AbortController` armed at 10 s (matching the
sidebar tree poller), the flag cleared in a real finally, a watchdog that
force-clears a flag which outlived any possible request, exponential backoff on
consecutive failures (cap 300 s) cleared by the first success, and a prompt
catch-up poll when the server reports `partial`.

A non-2xx or malformed body now counts as a **failure**, not as an empty
delta — otherwise the cursor advanced past a window nobody had scanned.

### 0-4 The one file we write inside a data folder

Sharing a data folder is legitimate: the same chip, two experiment lines, two
windows. The only thing SM writes there is `quashboard_tags.json`, and
`_save_tags` dumped the whole in-memory dict under an in-process lock — so the
second window's save silently deleted the first window's tags and notes.

`_save_tags(touched)` now re-reads the file and applies only the entry that
changed, dropping the conflict unit from *the whole file* to *one run*, and
adopts the merged result so the other window's entries appear here immediately.
`touched=None` keeps the whole-file write for the legacy-bookmark migration,
which rewrites every key by design.

---

## Pins

`tests/test_poll_stability.py` builds **real directories and real files**,
including genuinely truncated JSON, and runs a background writer thread against
a live poll loop. Synthetic dicts would not exercise what broke.

* the scan performs **zero** retry sleeps with 40 mid-write runs present —
  asserted by spying on `safe_io.time.sleep`, not by wall clock, because the
  parse pass fans out over a thread pool and a timing threshold passed by luck
  on a many-core machine
* the live `read_json` ladder is still there (the live path must keep retrying)
* a partial run heals, including when only the run folder's mtime moved
* a run deleted while incomplete vanishes cleanly
* 50 runs at once all arrive; runs landing *during* the poll loop are never
  lost (the cursor may re-offer rows, never skip them)
* one failing folder → `partial`, cursor held, other folders keep flowing, and
  it heals with no reload
* the budget leaves folders un-scanned rather than un-answered
* an unresolvable workspace and a vanished folder both return 200
* two `DatasetStore`s over one folder (standing in for two windows, since their
  tag locks are per-instance exactly as the real cross-process case) do not
  clobber each other's tags or notes; deletion still deletes; a corrupt file
  does not lose the new write; the favorite migration still rewrites wholesale

`tests/dataset_poll_selfcheck.cjs` loads the **real** `dataset-virtual.js`
under jsdom and drives its own poll path via `init()` + a `visibilitychange`
dispatch — the same thing the browser does when a backgrounded tab returns —
so no test-only API is added to the module.

Both were checked against the pre-fix code: 15 of 27 Python cases and 6 of 20
client checks fail there. A pin that passes on the broken code pins nothing.

---

---

## Part 1 — knowing the other window exists

`core/instances.py`: a registry of live State Manager processes and what each
holds.

**One file per process** (`instance/instances/<pid>.json`), not one shared
document. A shared file would need a read-modify-write from every process —
precisely the lost-update pattern this whole document exists to close, and two
windows would delete each other's entries. Per-process files have no write
contention at all: each process writes only its own, reading is a glob,
cleanup is an unlink.

**Liveness by PID probe, not heartbeat.** A heartbeat means a timer, and the
docs/78 constraint holds: no new background pollers. A probe is exact at the
moment it matters and costs one syscall. Its failure modes are bounded: a
reused PID reads as alive (a warning that need not have been shown), a probe
that errors reads as dead (no worse than having no registry). `pid_alive` now
lives here with `scheduler._pid_alive` as an alias, so the orphan reconciler
and the registry can never disagree about who is alive.

Written from choke points that already exist: `create_app` (register), the
first request (the port — chosen outside `create_app`, so the request is the
only place that knows it for sure), both `_activate_quam` branches (the chip),
`atexit` + the window-close path (deregister, because `os._exit` skips atexit).
Every write is best-effort: bookkeeping must never stop the app.

## Part 2 — run ownership

The queue is a file; `is_running` is an in-memory registry. A second process
therefore sees "the file says running" and "no worker of mine", which is
**indistinguishable from a crashed worker** — and it acted on that. Reproduced
on unmodified code: a `/scheduler/status` poll from window 2 flipped window 1's
live run to `idle` and marked its in-flight item `failed`; pressing Start
spawned a **second worker over the same queue**; and closing window 2 wrote
`cancelled` over a run it did not own.

`run.owner_pid` (+ `owner_port`, so the warning can name the window by what the
user sees in their address bar) makes the distinction recordable. Claimed by
`start`, released at every terminal transition.

| | live foreign owner | dead owner / no owner |
|---|---|---|
| poll (`_reconcile_orphaned`) | **file untouched**, message names the window | reconciles exactly as before |
| `start()` | `ForeignRunnerError` → 409 `scheduler_foreign_owner` | allowed |
| `cancel()` | **file untouched** | cancels as before |

Queue mutators needed no change: the route guard already 409s while
`is_active`, and a foreign-owned run is still `running`.

**The no-owner row is the load-bearing one.** A queue written before this
existed — or by a genuinely crashed process — must behave byte-identically to
before, because that is the case the orphan-reconcile mechanism was built for.
It does.

This is the one place multi-window use is *refused* rather than reported,
because it is the one case no warning can make safe: two workers on one queue
means two processes driving one OPX.

## Part 3 — the banner

Only the **same state path** in two live windows gets a banner, and it is a
banner, not a gate. A cross-process file lock was considered and rejected: it
would trap a user behind a crashed window, a worse failure than the one it
prevents.

A shared **data folder** gets nothing. Two experiment lines on one chip out of
one data folder is a real workflow; the only loss it used to cause was the
whole-file tags write, fixed at the source in Part 0-4. Warning about a normal
workflow would only teach users to ignore the strip that carries the warning
that matters.

`GET /instances/banner` into a `#multi-instance-slot` in `base.html`, refreshed
from events the app already fires (`load`, `stateRestored`, `liveDriftChanged`)
— no new poller. Dismissal is for the current view only: which windows are open
is a live fact, so a persisted "don't show again" would end up asserting
something no longer true.

## What is NOT closed

Two windows still cannot run **two different chips** at once: the Experiment
Runner's settings (`scheduler.json` holds one `quam_state_path`, one env, one
calibrations folder) and its queue are per *instance directory*, i.e. one per
machine. That is a pre-existing limit of the storage layout, not of PIDs and
not of QM — `qm` supports multiple clients fine. Scoping the runner per chip
(`instance/scheduler/<scope>/…`) would lift it; `_sched_inst()` is very nearly
the single choke point that would need to change (9 of 11 call sites go through
it). Deliberately left out of this branch so the safety work could land on its
own.

Also unchanged: no editing merge between windows, no live cross-window sync,
and `last_session.json` stays last-write-wins (it only tints the landing's
Resume highlight).

## Pins for Parts 1–3

`tests/test_multi_instance.py` — the ownership matrix runs against a **real
live foreign process** (a spawned python that sleeps), not a mocked pid check,
because a probe that is wrong about liveness is exactly the failure this
feature would have. Plus registry round-trip, dead-entry self-cleaning, corrupt
entries dropped rather than raised, a read-only instance dir never raising,
conflict classification (same chip / different chip / shared data folder), the
409 and the status-poll surface, and the banner (present, absent, dead peer,
never blocking an edit, wired into every page).
