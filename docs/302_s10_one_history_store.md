# 302 — S10: every history surface reads the change ledger; the snapshot fallbacks are gone

**Date:** 2026-10-08 .. 2026-10-10 · **Trigger:** the user's "remaining work" order (S10 was deferred from
v1.1.0), then "check it from the user's side before calling it done".

## What changed for the user

Every surface that shows a value's past -- the value drawer, Column History, the agent's field history,
Chip Status Trends / metric meta / calibration age / hero sparklines, the Param History grid / cell
drawer / Changes, the printable report, Versions, State History, the History drawer and the Calibration
log -- now answers from ONE store: the per-chip change ledger (docs/275-298), read through the open
folder's own view. The old second path (Param History snapshots read directly when the ledger "could not
answer") is deleted, so two surfaces can no longer disagree because one of them silently fell back.

| Mode | When | What every surface shows |
|---|---|---|
| `ledger` | the ledger answers | its rows, with notes for what this folder's view leaves out |
| `building` / `preparing` | the ledger is catching up | a progress note; the lists show the older Param History snapshots as rows meanwhile |
| `unavailable` | no history folder, no ledger in a read-only context, or a ledger that cannot be read | one plain sentence, no rows, one manual "Try again" where a retry can change the answer; the lists still show older snapshots |

A chip with no linked data folder shows SM's own writes and the states SM saw, with a **Link a data
folder** offer. An archived chip that never had a ledger builds one in the background the first time it
is viewed -- from its runs' folders where they still exist, from its run captures where they do not, and
from the states SM saw -- so nothing the old Param History grid drew is missing.

## The steps

| Step | Commit(s) | Content |
|---|---|---|
| C0 | 3e6785fb | a tripwire at every fallback branch + an audit of which chips would reach one |
| C1 | dee66432 | every chip gets a ledger; a folderless chip imports its observed states |
| C2 | 750d3d7d | the Link-a-data-folder offer (ranked candidates, zero-match refused) |
| C1.5 | d39a5b85 | one ledger per chip identity, read per FOLDER (lanes, seams, `held_before_write`) |
| C3 | 0531f9a4, aa9b00e3 | the mode switch: no `no_runs` fallback; `unavailable` replaces `fallback` |
| C4 | 0a342a4f | value drawer / Column History / agent snapshot paths deleted |
| C5 | 908d0c19 | Chip Status / Param History snapshot paths deleted |
| C6 | 3cdd8657 | Versions / State History / History drawer snapshot branches deleted |
| C7, C7b | da58d045, f80bcbb8 | what C4-C6 left callerless, the tripwire, the reader pool |

## What the reviews and the walk found (all fixed unless listed under Residuals)

**Adversarial review of C3** (a separate reviewer, refute lens): an archived chip whose ledger held no runs
read as empty; the new Pin button erased the bookmark's label; the lists said "nothing older is shown"
above the older rows they showed; a run of another chip became this chip's newest value.

**Adversarial review of C4-C7:** C4 deleted the agent API's `import os` while `os.getpid()` stayed in use
(the overnight grant watch crashed -- caught by the full suite; `tests/test_no_undefined_names.py` now
runs pyflakes over the package, which also found `core/autofit/figure_gen.py` using `os` with no import
on main); hiding every chip-uncertain run hid a nameless chip's OWN runs after it gained a qubit -- the
rule is now "only a run whose saved state DECLARES another chip (another name, or the same name with no
qubit in common) leaves the timeline"; the archived reader (no folder view) named another chip's run as
writer; a bookmark on a run's capture vanished; one failed slice turned "building" into a half-built
answer (a locked ledger stays `building` now); the live-baseline marker overwrote a user's bookmark label.

**User-perspective walk** (real Chrome, a copy of a 21-qubit customer chip with 3,494 runs): the
Versions panel listed the user's own Apply twice ("seen by SM ... writer unknown" on top); the Calibration
log returned HTTP 500 on an unreadable ledger; list notes contradicted the rows below them; four
different counts for one history; raw reason codes ("(no_ledger)") and version ids on screen; an archived
chip's 647 snapshots unreachable; a 30-55 s first catch-up after a data folder moved, with no progress.

### P0-1 -- a run-saved value the chip never kept (predates S10, found by the walk)

The ledger read each run's saved `quam_state` as "the chip's state after this run". On the customer's
setup a run often saves its OWN fit result that never reaches the live chip; the next run starts from the
chip and saves the old value again. Measured against the lab's 1,404 timestamped live backups: 2,166 of
17,278 judgeable run-saved changes (12.5%) never reached the chip -- and every surface showed them (wrong
"last changed", a CZ run "changing" an untargeted qubit's f_01, Revert offering values the chip never
held).

`core/hub_witness.py` decides each run change by the first later event that READ THE CHIP without
re-measuring that qubit (a run not targeting it, a state SM saw, an SM write): it carries the new value ->
confirmed; it carries the old value -> not kept on the chip. Replayed by the shipped code on the real
ledger: confirmed 98.9% precise (14,183 / 165), not-kept 96.2% (1,372 / 54). Not-kept changes and the
read that restored the old value leave every value series; the drawer lists them ("Saved by run #3313 ...
not kept on the chip (#3314, which did not measure qA1, still had ...)"); "since" is where the value's
stay on the chip began (an unconfirmed excursion that returned to the same value is skipped and named).
For three parameters checked by hand against the backups, every surface now gives the true date.

## Decisions

* D1-D5 (docs per spec, approved by the user): copies share runs under linked roots; Trends / Param
  History / Calibration log per folder; unknown folders after the cut are hidden and counted; legacy roots
  unlinked until linked; one rule for single-folder breaks.
* Another chip's run leaves the timeline only when its saved state DECLARES another chip; a merely
  uncertain identity stays, shown and never named as the writer.
* A run whose targets are unknown is never a witness (conservative; moved no judged verdict).
* A transient (locked/busy) ledger error keeps `building`; any other slice error is `degraded`, named.

## Residuals (known, not fixed)

* A NAMELESS chip takes its ledger identity from the first run ingested; a shared folder whose oldest run
  belongs to another nameless chip seeds it wrong. Declaring a chip name avoids it.
* At a seam, a change masked by another chip's run keeps its run but reads "writer not proven" even when
  its own patch proves it (under-claims, never over-claims).
* Labels cannot be typed by a user anywhere (Pin only) -- unchanged from before S10.

## Verification

Full suite in six sequential chunks (`cqt`), failures diffed against main; every step's pins
mutation-checked by `tools/mutate_hub_*.py` (drawer 60, chip status 56, versions 87, mode switch 67,
folder view 13, query 39, builder 36, no roots 15, archived build 38, witness 14 -- all red by
assertion); real-browser journeys on rig copies, screenshots read. Records of each round are in the QA
rig folders outside the repository.
