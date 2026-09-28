# docs/225 — The w8 follow-ups: an exact place after F5, a bounded grid window, "Delete together", and no request waiting on a whole-chip build

2026-09-28, branch `integ/w8` (base `be7884c` = origin/main after w7, four
merges, no conflicts), worktree `D:\work\sm-w7`. Sources: `out_w8_chipplace`,
`out_w8_gridscroll`, `out_w8_pulsedel`, `out_w8_locks` (each branch's own
implementer report; "by whom" below names the implementer's own measurement,
an independent verifier where one ran, or the integrator).

## 1. What was merged

| branch | merge | branch head | what |
|---|---|---|---|
| `w8/chipplace` | `34c1073` | `726b92a` | F-20 place record: panel + offset, written with the jump's URL, flushed on pagehide; virtual-time selfchecks; no mouse ring on the rows toolbar |
| `w8/gridscroll` | `54ecf91` | `984131d` | the pair grid keeps a bounded column window on BOTH sides; class-based 64-column rule blocks; hydrated columns park |
| `w8/pulsedel` | `08789e2` | `bfa905a` | `_lab_delete_also` shared by Pulses / Json Tree / the batch door; "Delete together with …" in place; Ctrl+Z waits for the batch answer to be handled |
| `w8/locks` | `409a172` | `c77042c` | single-flight grid build and cold `PulseIndex` with lock hand-over; the save snapshots then writes outside the lock; producer hand-over; sparklines in one held pass |

All four were cut from `be7884c` and merged pairwise clean. Shared files
(`routes.py`, `app.js`, `style.css`) were touched in disjoint regions; the
one semantic seam — pulsedel's delete doors read the `PulseIndex` inside
their own held store lock while locks' `PulseIndex._ensure` runs the cold
build through `activity.single_flight` — composes because `_ensure` returns
at once when the caller already owns the lock (its hold is not the index's
to hand over). Save byte-identity re-checked by the integrator after the
locks merge: the same chip copy with the same four edits saved through
`Saver.save` at `be7884c` and at the w8 head gives identical `state.json`
(1,654,614 B) and `wiring.json` (2,526 B) SHA-256 and the same backup set.

## 2. Chip Status: F5 lands on the panel the reader jumped to (`w8/chipplace`)

**What.** The F-20 place record F5 restores was written only by a 250 ms
debounced scroll handler that a big chip's growth kept re-arming, and it
named the tab SECTION plus a pixel offset — right only once every panel above
had its final height (the IRB heading sits 80-107k px below the 2Q Fid.
section top on big30x). Now a tile jump / tab press writes `{view, sel,
jump:true}` in the SAME `history.replaceState` as its URL; `pagehide` and
`visibilitychange(hidden)` write the place at once; while a jump is landing
the record is its TARGET; a position record names the PANEL at the 130 px
line (one `elementFromPoint` hit-test, panel scan only as fallback). The
resume selfcheck runs in virtual time (`tests/jsdom_vclock.cjs`); the rows
toolbar's `[role=group]` ring shows only on `:focus-visible`.

**Measured (implementer, real headless Chrome, interleaved with base).**
F5-lands-exact (target top within 8 px under the pane top, tab lit, URL
`?view=`): big30x 8/18 → 64/64; krs5 9/16 → 32/32 (mouse / Enter / Space /
tab × F5 after 0, 0.3, 2, 12 s, rotating targets). The resume selfcheck:
4/12 FAIL on `be7884c` under 12× parallel load → 50/50 green in virtual
time. Click-handler cost unchanged (the w8 record 0.6-1.6 ms inside a
318-741 ms task dominated by the pre-existing synchronous
`build2QRBPanels`).

**Pins.** `tests/chip_status_place_selfcheck.cjs` (54 assertions, J1-J5,
J4b, R1-R4, T1, F5 matrix), `tests/chip_status_resume_selfcheck.cjs`
(virtual time), `tests/test_chip_status.py::test_a_reload_keeps_the_readers_place`,
`tests/test_exp_list_compact.py::test_a_mouse_press_leaves_no_ring_around_the_rows_toolbar`,
journey `tests/browser/journeys/chip_place.cjs`. Every listed mutation red.

**Open.** The pagehide/visibilitychange flush forces one layout when dirty
(14-24 ms on big30x, 6-11 ms on krs5). The first tile jump on big30x is a
320-740 ms click task, almost all `build2QRBPanels` (pre-existing). Other
Pico `[role=group]` containers very likely show the same whole-group ring
after a mouse click; only the rows toolbar was changed. Most other
selfchecks still wait on the wall clock.

## 3. Live Edit: a slow scroll across the 2,389-column pair grid (`w8/gridscroll`)

**What.** Six causes found by trace: only the RIGHT side was ever collapsed;
every reveal rewrote a rule block (any stylesheet change walks all 247k+
elements, 19-50 ms, vs 3 ms for a class toggle on one column); each landing
scanned all ~160k cold tds and re-walked the table for PhysAmp / a11y marks
/ pair stats; Chrome's autofill form scan re-walks every `<input>` after each
insertion (150-230 ms); the toolbar-pin rAF ran `querySelectorAll` over the
pane on every scroll frame; the two grids changed layout in the same frame.
Now a bounded window on both sides, class-based 64-column rule blocks,
hydrated columns park, one compensating grid per frame, row heights pinned
while the tail lives.

**Measured (implementer, real headless Chrome, interleaved A/B, big30x, bulk
font scale 1.2 for both).** Slow wheel 0→40k→0 (120 px / 60 ms): right side
p50/p90/p99/max 74-77/125-142/171-191/190-221 → 57/69-78/99-103/106-111 ms,
tasks >100 ms 72-78 of 263 → 1-3 of 62-70, long-task seconds 22.6-23.1 →
3.7-4.4, wall 61-62 → 32.4-32.7 s, pair columns laid out 132 → 32; left side
p99 223-243 → 79-89 ms, tasks >100 ms 159-175 → 0. Fast wheel (800 px /
110 ms) p99/max 661/766 → 110/129 ms, 19 → 0 cold cells in view. Full range
0→848k→0: never more than 38 pair columns laid out, 0 cold cells, 0 errors
(the w7 code grew to 2,216 long tasks, max 476 ms, 655 s for the right phase
alone). krs5: DOM/sheet hashes identical to base, Enter commit 48-74 ms both.

**Pins.** `tests/liveedit_big_grid_selfcheck.cjs` (101 assertions; sections
3/8/9 rewritten for the class mechanism, new 10/11/11b/12), driven by
`tests/test_liveedit_big_grid.py`; 25 mutations red in a scripted sweep.
Targeted pytest 1025 passed / 20 skipped (37 grid/undo/pane files).

**Open.** The per-step floor is Chrome work that scales with the DOM (~2,400
td siblings per row; pane paint 15-35 ms; the autofill scan 70-80 ms after
each landing) — p99 ~100-110 ms, max ~110-140 ms under continuous wheeling;
lower needs tds out of the rows or inputs that exist only on focus. Row
pinning keeps a grown row's height until the next render. The TWPA divider
over the pair rows at the far right is identical on base.

## 4. Pulses: a lab refusal of a delete offers "Delete together with …" (`w8/pulsedel`)

**What.** A lab refusal of a delete (a gate's inline pulse, an op the gate
plays by name, the gate's required control pulse) now appears inside the
delete step (`HX-Retarget #pulse-delete-result`; before, a toast only),
lists exactly the paths that must go with the pulse and each one's role, and
offers "Delete together with N …" — one batch, one Ctrl+Z.
`routes._lab_delete_also` is the ONE function for every door (Pulses delete,
Json Tree `/field/delete`, an all-delete `/field/edit-batch` refusal): it
merges what the lab check named with a closure over the lab-watch map (ops
whose tracked field the delete cuts, by-name ops whose name-holding gate
field goes, the gate field holding a deleted op's name, and the whole gate
when the env's own dataclass schema says a going field is required). A
Ctrl+Z pressed during the check waits for the answer to be HANDLED.

**Measured (implementer, real headless Chrome, interleaved ABBA).** krs5
(n=10 medians) refusal click 57 (toast) → 44 ms (in step), refusal server
31 → 26 ms, batch 37 → 31 ms, undo 10 → 8 ms. big30x (n=6) refusal click
577 → 469 ms, batch 770 → 587 ms; server-side refusal and undo are bimodal on
both codes (the lab worker's cache: fast when cached, 0.3-0.6 s when asked).
`_lab_delete_also` 0.3 ms per call in process on big30x; the watch map 52 ms
cold, cached by stamp. Journeys: krs5 78 checks ALL OK; big30x 80 checks OK
(refusal 0.29-0.81 s warm / 10.0 s cold; Apply 4.4 s). After Apply,
`pulse_lab_check.py` in the lab's env: krs5 gates 21/0, big30x 3467/0.

**Pins.** `tests/test_pulse_delete_together.py` (15), extended
`tests/test_lab_doors.py`, `tests/pulses_delete_together_selfcheck.cjs` (30
assertions, real app.js + pulses.js), `tests/explorer_crud_selfcheck.cjs`
(C18 extended), journey `tests/browser/journeys/pulses_delete_together.cjs`.
Python 11/11 and JS 13/13 mutations red, plus the hold-timing and label
plumbing mutations.

**Open.** Without an env schema for the gate class, deleting an op held by a
REQUIRED gate field offers only that field and the batch is refused as a
whole by the gate's own `apply()`, read in place. The refusal still costs the
lab check that precedes it (warm ~0.3 s krs5, 0.3-0.8 s big30x; cold 3-10 s)
with only a disabled button while it waits. docs/218 has no section for this
change yet.

## 5. No request waits on the store lock for a whole cold build (`w8/locks`)

**What.** (1) The Live-Edit grid build runs through
`activity.single_flight(main=True)` with checkpoints in the cell loops,
column derivations and post-passes; a build the chip moved under restarts
(content token), and the change-log map is re-read with the content so an
edit landing mid-build keeps its "modified" marker. (2) The cold
`PulseIndex` ticks (`activity.checkpoint`) through a `_walk_ticked`
reverse-index walk, the shape discovery and the row loop; reads go through
`PulseIndex._ensure` → `single_flight(main=True)` per index; the cheap roads
(fresh / value-only steps) stay one short hold; the chip-open decision walks
its OWN index under `activity.yielding` and warms the context's. (3)
`saver.save` holds the lock for a SNAPSHOT (content token, the change-log
entries it stands for, `marshal.dumps` of both documents, format 2) and for
the swap; the `.bak` copies and rotation run under a per-folder save lock
(`safe_io.path_lock`), the render and `.tmp` write with the lock free, the
swap re-checks the token (edits that landed keep their log entries; a moved
chip snapshots again, 3 tries, then one hold). (4) A foreground walk hands
the lock to a background producer a request waits on; a page's own walk goes
first while other walks wait and short requests still get in. (5) The Pulses
page draws its sparklines in one held pass that hands the lock over.

**Measured (implementer, `lockwatch2/3` in process, big30x, 2+2 interleaved
base → new).** Longest wait on `store._lock` over the w7 verification
journey 1685 / 1832 → 310 / 307 ms (waits >300 ms 7-8 → 1, 0 net of GC);
first `/bulk` after open max wait 1521 / 1579 → 132 / 180 ms; `/bulk` after a
structural Take live 1685 / 1832 → 250 / 200 ms; `_decide`'s cold PulseIndex
after a structural pull 741-817 ms hold → no hold (parks); open `/pulses`
629 / 634 → 0 / 6 ms max wait; `saver.save` in the first apply after Take
live 291-400 → 21-186 ms. Real Chrome big30x two-tab PULL: B's commit
repaint median 3317 → 690 ms. Single-tab open: load+ttfb 8577 → 8508 ms
(level). Remaining >250 ms waits are the w7 lint's own hand-over slices,
GC-inflated.

**The trade-off to weigh (open).** Real Chrome big30x two-tab OPEN (Live
Edit, then Pulses in a second tab 300 ms after `/load`): the cold `/bulk`
ttfb is 1.1-2.1 s slower (median 12.9-13.3 → 14.0-15.4 s over 4 runs). The
lock-wait target is met; the page now waits for the short requests' own
critical sections served during its build (drift polls hold 0.16-0.5 s each
at open, `_working_vs_live_entries`) plus the hand-over overhead. krs5
single-tab first `/bulk` +166 ms in Chrome (318 → 484 ms, n=8) while `/load`
is ~100 ms faster, not reproducible in process. Also open: a cold grid /
PulseIndex build restarts from scratch when an edit lands at a hand-over
(bounded; plain writes could be patched via the change feed instead); undo
responses may cold-build the PulseIndex for `pulses-rows-changed` when no
index is warm (a walk now, not a hold).

**Pins.** `tests/test_store_lock_holds.py` (22, stable over repeated runs);
25 mutations red (M1-M17b: grid holds the lock whole, stale change-log map,
no restart on a moved chip, PulseIndex cold under one hold, open decision
not parking / sharing / not warming / replacing a fresher index, no
producer hand-over, per-row takes, backups under or before the snapshot,
ticked-walk prefix rule, render under the lock, swap without re-check, late
edits, …). Targeted pytest 2442 passed / 47 skipped (54 files);
`SM_RAM_VERIFY=1` over 27 files: 2 pre-existing count-based failures
identical on `be7884c`, 0 `StaleCacheError`.
`tests/test_safe_io.py::TestTwoWritersOfOneFile::test_concurrent_writes_of_one_file_all_land`
is flaky on base too (6/30; Windows `ReplaceFileW` collision, path
unchanged).

## 6. Integration checks (integrator)

Structural: parse / duplicate-def / duplicate-route / `node --check` clean
after every merge. Targeted batch (26 files: the four branches' own tests
plus grid, pulse, saver, undo, lock and sync neighbours): 721 passed / 13
skipped. Selfchecks: `chip_status_place`, `chip_status_resume`,
`liveedit_big_grid`, `pulses_delete_together`, `explorer_crud`, `ctrlz`,
`pulses_final_qa`, `loader`, `chip_jump`, `bulk_virt`, `bulk_virt_server`,
`pair_virt_server`, `undo_repaint` — all exit 0. Save byte-identity: see §1.
Full suite: run as three parallel shards in detached worktrees at the w8
head (`D:\work\sm_qa_rigs\_findings\w8_shard{1,2,3}.txt`); the result and
the classification against `be7884c` are in `out_integ_w8.json` beside this
document's sources.

## 7. Addendum (2026-09-28, second w8 wave): two more branches

| branch | branch head | what |
|---|---|---|
| `w8/dstab` | `b7a193c` | Datasets: the reader's tab survives a run that lacks it -- a tab PRESSED on that run is recorded (`_dsScroll.picked`), and a run opened into an empty or non-run pane is a FRESH open (Full View at the top); docs/221 §8 |
| `w8/pulsehint` | `82e41e7` | Pulses: "Don't see your pulse class?" under the + New pulse class list (`_pulse_class_find.html`; ok / failed-module / no-env states), OOB-synced with the env strip, one env probe per Add/Remove module, in-place class-list update that keeps a touched form |

Both cut from `4e0a9b5`, merged pairwise clean and clean against the head.

**dstab, measured (implementer, real Chrome).** Journey `ds_tab_intent.cjs`:
5Q KH rig 8/10 → 10/10 and big30x 8/10 → 10/10 (the two failures were the
pick on a tab-less run landing back on Interactive at 388 px, and a re-open
after close at 388 px); keyboard pick (focus + Enter) D = interactive@414 →
full@0; intent probe 44 → 47 of 56 (the 9 left are clamped short runs and a
pre-existing #4102 Interactive scrollTop offset, identical on base); the
dsscroll matrix stays 0 miss / back exact 47/47 with switch medians within
noise (e.g. full_1600 329 → 327 ms). Pins: `ds_scroll_anchor_selfcheck.cjs`
section K (19 assertions, 102 total), 8 mutations red in jsdom + 3 in real
Chrome. Open: the #4102 return lands 100-120 px lower in scrollTop (tiles
above are 366 px placeholders on return, pre-existing); a run opened from
another page while the collapsed inspector still holds a run counts as a
switch (design choice to confirm); docs/221 §7 still awaits confirmation.

**pulsehint, measured (implementer, real Chrome, interleaved vs 4e0a9b5).**
krs5 add-a-module-to-a-class-in-the-picker: 32.2 / 31.6 s plus an extra
"refresh (clears this form)" click → 14.9 / 16.9 s with no click; env probes
per Add module 2 → 1, per Remove 2 → 1; a touched form (op name typed) gets
the class in place at 12.8 s with the name kept; big30x New-pulse click to
class list 521 → 507 ms, strip GET 13 → 14 ms, keystrokes within noise; one
line at 1366/1600, light and dark. Also fixed on the way: typing a module
name in the strip marked the whole create form dirty (now only edits inside
`form.pulse-create-form` do). Pins: `tests/test_pulse_class_find.py` (10,
driving `tests/pulses_classfind_fragcheck.cjs` -- real htmx + app.js +
pulses.js on the real route renders), journey `pulses_classfind.cjs`; 19
mutation/pin pairs red. Open: a reload inside the ~15 s post-create re-probe
window shows a transient schema banner (pre-existing harvest-drift re-probe,
docs/218); if the selected class's own spec changes in the same probe that
brings a new module, the touched form still falls back to the "refresh"
offer.

