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

## Still to come on this branch

* **Part 1** — a process registry (`instance/instances/<pid>.json`, one file
  per process so there is no shared read-modify-write; liveness by PID probe,
  no new poller).
* **Part 2** — scheduler state scoped per chip
  (`instance/scheduler/<scope>/…`) plus `run.owner_pid`. Two windows driving
  two different chips is something QM supports and only our storage layout
  prevented; the same change makes a *second runner on the same chip* refuse
  instead of silently double-driving one OPX.
* **Part 3** — warn only when two windows hold the **same state path**. A
  shared data folder is not a conflict, and 0-4 removed the loss that made it
  look like one.
