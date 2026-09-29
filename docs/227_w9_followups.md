# docs/227 — The w9 wave: sliced Chip Status panels, a virtual "All" pulse view, structural pulse edits on one page, and the lab worker warmed at chip open

2026-09-28/29, branch `integ/w9` (base `91c8aae` = origin/main after w8, four
merges, no conflicts), worktree `D:\work\sm-w7`. Sources: `out_w8_uxpolish`,
`out_w8_pulsesall`, `out_w8_pulsegate`, `out_w8_labwarm` (each branch's own
implementer report; "by whom" names the implementer's own measurement, an
independent reviewer where one ran, or the integrator). The labwarm /
project-env branch has its own record, **docs/228**; §5 here only names it.

## 1. What was merged

| branch | merge | branch head | what |
|---|---|---|---|
| `w9/uxpolish` | `e5de3f4` | `65a55d6` | Chip Status builds its 2Q RB and metric panels in slices from the jump's target outward; one focus-ring rule for every `[role=group]`; a run opened from OUTSIDE the run list opens fresh |
| `w9/labwarm` | `beab317` | `42c86fb` | the lab-code worker is prewarmed at chip open, idles out after 60 min; "Preparing…/Checking…" texts; each QUAlibrate project remembers its Python env (`core/project_env.py`, landing env picker, sidebar env badge) — docs/228 |
| `w9/pulsesall` | `fb61459` | `2aba961` | the per-page "All" view of a library of ≥ 400 rows is VIRTUAL (`pulses-vt.js`, `RowMemo`, `/pulses/vids` · `/pulses/vrows` · `/pulses/sparks`); edits patch the rows they touched |
| `w9/pulsegate` | `8a01472` | `24c3a71` | pulses are added, removed and renamed on the Pulses page only (`core/pulse_structure.py`); `/api/pulse/delete-together`; tree guidance + `/pulses/goto`; the pair Add-gate form no longer offers the flux CZ variants |

All four were cut from `91c8aae` and merged pairwise clean. Shared files:
`routes.py` (labwarm, pulsesall, pulsegate), `app.js` (uxpolish, pulsesall,
pulsegate), `pulses.js` (labwarm, pulsesall, pulsegate), `pulse_index.py`
(pulsesall, pulsegate), `style.css` (all four), `_pulse_detail.html` and
`base.html` (two each) — every pair auto-merged in disjoint regions.

## 2. The seams the integrator checked (merged tree)

1. **One thumbnail mechanism, not two.** pulsesall's lazy `POST
   /pulses/sparks` and the paged rows both render through the same
   `pulse_index.sparkline(...)` and hand every lab-class miss to the same
   `_warm_lab_sparks(store, paths)` (one in-flight set, `_lab_spark_inflight`),
   which is the batch labwarm's yielding `lab_waveform.draw` serves — labwarm's
   "chunked pre-draw" is that batch yielding to a user's check, not a second
   cache.
2. **The structural guard and the virtual view.** pulsegate guards the write
   doors (`/field/create|delete|edit|edit-batch`, `/qubit|pair/<n>/edit`, `cli
   set`, `/pair/<n>/gate`); pulsesall's `/pulses/vids` (GET), `/pulses/vrows`
   and `/pulses/sparks` (POST) only read rows — nothing to guard.
3. **Delete-together inside the virtual All view.** `POST
   /api/pulse/delete-together` runs `_field_edit_batch_impl(payload,
   pulse_door=True)`, so its answer carries the batch door's `pulses-changed`
   / `pulses-rows-changed` triggers; `pulses-vt.js` re-lists on
   `pulses-changed` (`/pulses/vids` → `/pulses/vrows`), patches named rows on
   `pulses-rows-changed`, and a `gone` answer re-lists — the deleted rows
   leave the virtual table the same way they leave the paged one.
4. **app.js / pulses.js.** uxpolish 5 + pulsesall 6 + pulsegate 11 hunks in
   `app.js`; labwarm 2 + pulsesall 2 + pulsegate 4 in `pulses.js`; all
   disjoint, `node --check` clean, the branches' selfchecks green on the
   merged tree.

Structural: parse / duplicate-def / duplicate-route (394 routes) / `node
--check` clean after every merge.

## 3. Chip Status: a jump builds its target first (`w9/uxpolish`)

**What.** Traced on big30x: a tile/tab click task was 0.7-1.3 s —
`build2QRBPanels` making all 96 panels (0.23-0.33 s) and one forced layout of
~6,600 cells (0.53-0.79 s) inside the click. One slice builder (`_sliceStart`)
now serves the 2Q RB section and the metric panels: on a chip over 300 cells a
section goes in as headings plus empty `display:contents` slots, the press
builds the target panel and the one under it, the rest arrives in slices of up
to 4 panels per frame, building outwards from the target (a reader wheeling up
right after a jump finds panels there). Also: one `:focus-visible` rule for
every `[role=group]` (no whole-group ring after a mouse press); a run opened
from outside the run list (Chip Status trend, a History "Data" link) opens
fresh — Full View at the top.

**Measured (implementer, real Chrome, interleaved).** big30x tile click:
long task 309-739 → < 50-92 ms (2Q targets), 349-728 → < 50-75 ms (metric
targets); input-to-paint 320-768 → 48-112 ms; landed 319-816 → 47-132 ms;
exact landing 21/21 both. Six wheel ticks after a jump: long tasks 17-26
(50-508 ms) → 0-3 (52-67 ms). krs5 unchanged within noise (every panel built
in one go). F5 exact place `chip_place.cjs`: 12/12 both. Focus rings: mouse
ring on the groups 7/9 → 0/9, Tab ring 8/8 kept. Outside-fresh journey 15/15
vs base 10/15.

**Pins.** `tests/chip_rb_slices_selfcheck.cjs` (S1-S19, 54 assertions, a
30-qubit lattice with 20 RB + 15 metric panels in virtual time), driven by
`test_chip_status.py::test_a_jump_builds_its_own_2q_panel_first`;
`tests/test_group_focus_ring.py`; `ds_scroll_anchor_selfcheck` 118/118;
journey `ds_outside_fresh.cjs`. 37 mutations: 36 red, 1 green explained
(an unreachable `if (o.spec)` guard, kept).

**Final-QA P1 fix (wheel up right after a jump).** The empty slots were 0 px,
so on big30x a wheel UP within ~1 s of a tile jump skipped every unbuilt panel
above the target and landed on Trends / the 2Q section; as the slots filled the
target ended 77-165k px below. Now every slot holds a placeholder
(`.topo-rb-ph[data-rb-ph=<panel key>]`) as tall as a measured panel of its kind
(tile size + Show Meta Info + cell count), sized in the frame after the press;
a builder with no panel yet (the lazy 2Q section above a metrics jump) builds
its ONE panel nearest the target in that frame to measure it, and a lazy
section builds from its end (nearest the target). Each slice keeps the
reader's place (`_holdPlace`: a slot wholly above the reader's line gives its
height change back to the scroll offset -- only when Chrome's own anchoring,
which it applies synchronously in the same layout, moved nothing). Once the
reader has moved the pane (wheel / key / touch / press, `readerInput`), the
builder with the unbuilt panel nearest the view goes first, outwards from that
panel. A place record taken over a placeholder names its panel. Measured in
real Chrome on big30x (`chip_place.cjs wheel`, 3 reps x ro_ge / gate1q / T1 /
T2 Ramsey / IRB x up / down / none): 45/45, each ending in the same panel as
base 91c8aae (15/15); the unfixed integ JS 0/5 (Trends / 2Q). F5 exact place
16/16. Click task (interleaved, 2 blocks x 5 tiles): 50-87 ms vs integ
51-128 ms. 5Q: 8/8. Pins: `chip_rb_slices_selfcheck` S18 (the sample), S20
(15 wheel cases + wrong estimates + the view-first order + Chrome's anchoring
left alone), S21 (F5 over a placeholder); the model gained placeholders and an
opt-in emulation of Chrome's anchoring. 10 mutations, all red (two were green first -- the view-first order inside one section and the "leave Chrome's anchoring alone" rule -- and got the pins that now catch them).

**Open.** `.calc-sec > summary + *` in style.css is a universal sibling rule
(a mid-host insertion 524 → 207 ms without it) — an app-wide candidate.
TopbarHeight's MutationObserver and `syncSidebarNavActive`'s
`scrollIntoView` still force ~13 ms layouts inside a click. The chart pump
still draws ~115 charts in 0.3-0.9 s batches after the slices. Frames run
40-80 ms during the 1-3 s background build. `chip_jump_selfcheck` D2 is
wall-clock and flakes under heavy load (base 1/6, new 0/6).

## 4. Pulses: the "All" view of a big library is virtual (`w9/pulsesall`)

**What.** Profiled at `91c8aae` on big30x (All = 8,779 rows, 148k elements):
every forced layout walked the whole table (one commit: 6.7 s
`getBoundingClientRect` + 8 s layout + 2.6 s Split.js); the server rendered
All in 7-11 s (400 lab thumbnails at ~8 ms + 8.8k Jinja rows), 14 MB, on
every structural change. Now a library of ≥ 400 rows (`routes._PULSES_VT_MIN`)
ships its rows once as data (`[path, digest, text-html]`, digest = blake2b of
the text), `pulses-vt.js` renders only the rows near the viewport between two
spacer rows, keeps the first visible row in place, and draws thumbnails lazily
for the rows on screen. `core/pulse_index.RowMemo` re-renders only the rows a
cold rebuild changed (validated by row identity or a type-strict signature of
exactly the keys `_pulse_row.html` reads, pinned against the template). A
value change patches its rows through `/pulse/row` (`X-Pulse-Ver` /
`X-Pulse-Stamp`); anything else re-lists digests (`/pulses/vids`) and fetches
only the rows whose digest moved (`/pulses/vrows`).

**Measured (implementer, real Chrome, interleaved A,B,B,A, big30x All).**
Open to first rows 47,885 → 1,088 ms (final 668); row click → pulse open
7,304 → 379 (223); field commit → row repainted 21,399 → 1,117 (608); Ctrl+Z
→ row repainted 3,336 → 95 (47); search per key 1,556 → 35 ms; reload with
`?per_page=0&pulse=` 63,401 → 1,242 (774); duplicate → row in table 53,030 →
736; delete → row gone 57,632 → 2,325 (1,328); DOM 8,779 rows / 147,974
elements → 22 rows / 3,783. The 50-row view is unchanged; a 173-row library
(krs5) stays below the floor, byte-identical.

**Pins.** `tests/test_pulses_virtual.py` (16) and
`tests/pulses_vt_selfcheck.cjs` (sections A-O, 60+ assertions); 36 mutations
red. Reviewer + implementer found and fixed three P2 races (out-of-order
text, sparks adopting text, an endless sparks loop for a gone path), a
type-loose memo carry (`100 == 100.0`), a stamp not moving on an
invalidate-hint rebuild.

**Open.** The inspector re-render after a commit is a 300-650 ms task on
big30x (`buildViewBar` builds an `<option>` for every pulse — shared with the
50-row view). Structural ops on big30x are server-bound (1-2.6 s). Ctrl+F and
printing see only the rendered rows. Initial All payload 13.7 MB.

## 5. Pulses: structural pulse changes on the Pulses page only (`w9/pulsegate`)

**What.** `core/pulse_structure.py`: a write is a structural pulse edit when
it changes WHICH pulse objects exist — "a pulse" is exactly what the Pulses
page lists (`places_of == pulse_index.list_pulses`, exact on 6 real chips:
big30x 8,779, KRS_5Q 158, big20 1,040, KRS_CZ 665, CR_state 288, AS_10TQ9TC
479 rows) plus every key of an `operations` dict. Not structural: anything
inside a pulse, a pair gate slot holding a POINTER (a re-link), deleting or
emptying a non-pulse object. Structural: create/delete of a pulse or
`operations` entry, a new non-pulse object that BRINGS pulses ("carries"), a
whole-object edit that adds/removes/renames/replaces a pulse (class swap,
pulse → alias). The guarded doors answer 409 `error_kind=pulse_structure`
with a link to the Pulses page (`/pulses/goto` resolves a path to its row);
the tree shows guidance instead of ✕ on a pulse; `/api/pulse/delete-together`
is the one batch door for a lab refusal's offer (verified paths only). The
pair page's Add gate no longer creates flux CZ gates (their templates wrote
the flux pulses inline — the layout the Pulses page retired 2026-09-27); it
points to + New pulse → Gaussian CZ. Undo/redo, sync, restore and dataset
loads are unaffected by design.

**Measured (implementer, real Chrome, interleaved, N=6).** big30x explorer
open ttfb 78/125 → 86/82 ms (payload +389 B of 9.6 MB), DCL 474/453 →
381/371; a value edit inside a pulse 8/10.5 → 9.5/9.5 ms server; a 30-row
batch 102.5/208.5 → 15/11.5 ms; tree → pulse open 382/406 ms (no link on
base). In process: the batch pre-check for 2,000 leaf rows ~37 ms,
`structural_change` of a whole qubit 0.6-1.9 ms, `places_of` of the whole
chip 47-77 ms (never on a request path).

**Pins.** `tests/test_pulse_structure.py` (99), `tests/pulse_gate_tree_selfcheck.cjs`
(G1-G7), journeys `pulse_gate.cjs` + `pair_add_gate_pulses.cjs` +
`pulses_delete_together.cjs`; updated `test_lab_doors`,
`test_pulse_delete_together`, `test_field_crud_routes`,
`test_web::TestAddGateFlow`, `test_cr_surfaces`, `test_pulses_routes`. Two
sweeps: 40 → 32 red + 4 equivalent + 3 pinned + 1 anchor; 56 → 56 red after
pinning the 3 survivors. Refute review (opus) + own: delete+recreate channel
bypass, batch rows judged against pre-batch state, a pointer row steering a
later row, recursion depth ~490 failing OPEN → iterative walk + fail-closed,
class swap via JSON edit, HX-Location snapshotting the whole tree into
htmx history — all fixed.

**Open (user decisions flagged).** `/pair/<n>/gate-inspector/switch-moving`
still deletes+creates the gate's CZ flux ops (a move between the pair's two
qubits, one Ctrl+Z) — left allowed. The pair page's Add gate no longer
creates flux CZ gates (done, flagged). Per-value takes from a version / the
diff workbench that would CREATE an alias op or an inline slot are refused
with a toast (only take-live is verified). Duplicating a qubit/channel through
the tree is refused by the "carries" rule (consistent, new). Pre-existing: a
top-level `operations` dict crashes `pulse_index._discover`.

## 6. The lab worker and the project env (`w9/labwarm`) — see docs/228

The worker starts at chip open (only for a chip with a lab class or gate,
never in tests unless a pin asks), idles out after 60 min, is retired on an
env switch; "Preparing…" while it imports, "Checking…" while it checks; each
QUAlibrate project remembers its env (`core/project_env.py`, landing picker,
sidebar badge). Measured (implementer, loaded machine, interleaved): first
lab check on big30x sent when `/bulk` finished 10.3-18.0 s → 1.1-1.9 s; krs5
edit at +60 s 12.1 s → 0.48 s; open-to-worker-ready median 17.6-18.4 s under
load. Pins: `test_lab_prewarm.py`, `test_project_env.py`,
`lab_check_selfcheck` 42, `landing_env_selfcheck` 17; 44 mutations red. Open:
one env's worker at a time (alternating projects on different envs restarts
it); the worker's private commit ~1.2 GB from open to idle-out.

## 7. Integration checks (integrator)

Targeted batch (28 files: the four branches' own tests plus pulses / lab /
explorer / undo / project neighbours) and the branches' selfchecks on the
merged tree; the full suite as three parallel shards in detached worktrees at
`8a01472` (`D:\work\sm_qa_rigs\_findings\w9_shard{1,2,3}.txt`); results and
the classification against `91c8aae` are in `out_integ_w9.json` beside this
document's sources. Not pushed by the integrator (final QA runs first).
