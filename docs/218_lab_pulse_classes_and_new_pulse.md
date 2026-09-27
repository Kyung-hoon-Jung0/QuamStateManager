# docs/218 — The lab's own pulse and gate classes draw and check every write; one "+ New pulse" button

2026-09-26/27. Branches `w7/adaptive` (7 commits, `bd01432`..`f984798`) and `w7/pulsecreate`
(27 commits over `e74b8ce`, contains `w7/adaptive`, head `d27a372`). **Not merged into `integ/w7`
yet** (docs/224). Shape discovery of pulses (`729e2bd`, `9b61e35`) is docs/217. Chips: krs5 (rig
copy of 260907_KRS_5Q, "5Q" in the reports) and big30x; lab env KRISS_CZ (quam 0.6.0 /
quam_builder 0.4.0) unless named.

## 1. What was measured

| what | chip / setting | before | after | measured by |
|---|---|---|---|---|
| draw a lab pulse with its own `calculate_waveform()` | krs5, cold subprocess, 5 params | n/a | median 3.15 s (2.87–3.78 s); cache hit 0.01–0.02 s | independent verifier (adaptive) |
| lab own-code check, direct `lab_waveform.draw` | KRISS_CZ | cold 10.2 / 9.3 s | warm worker 0.02 s (first spawn 8.8 s) | implementer (`7e52938`) |
| same, reused worker | KRISS_CZ, shadow copy of `quam_config` on PYTHONPATH | — | cold 9.6–10.8 s, warm 0.02 s | independent verifier, round 2 |
| lab-class create | krs5 | 9.5–19.5 s (implementer) | 572 ms / 600 ms warm (journey) | independent verifier, round 2 |
| lab-class edit refusal in the create journey | krs5, W=1366 | 14.0 s (implementer, fix 1) | 467 ms | independent verifier, round 2 |
| lab gate `apply()` check | big30x | ~20–25 s, whole chip (9.5 MB) | 0.7–1.2 s in-process, pruned (0.37 MB); real Chrome 1.26 s | implementer (`eb1b702`) |
| non-lab edit `qubits.q1.f_01`, in-process, median/p90 | big30x | base `e74b8ce` 1.56/2.14 and 1.81/1.92 ms | 1.51/1.81 and 2.17/2.67 ms ("no change beyond noise") | independent verifier, round 3 |
| CZ macros whose own `apply()` passes after the journeys + Apply | krs5 / big30x | 22/23 and 3468/3469 (round 2) | 23/23 and 3469/3469 (round 3) | independent verifier |

## 2. Cause

- A lab writes its own Pulse subclass (e.g. `quam_config.two_flux_gate.SNZTwoFluxPulse`,
  `GaussianNZTwoFluxPulse`). SM keeps no copy of waveform code (docs/189 §2), so only the class can
  draw it or say whether values are valid.
- The class validates its values in its waveform code: SM wrote `GaussianNZTwoFluxPulse(flat_length=32,
  filter 40 MHz)` and `generate_config()` raised for the WHOLE chip ("half the flat length (16 ns) is
  below 6*sigma", implementer QA D8). Edits of existing lab pulses were never checked (verifier 1).
- The lab's gate checks more than one pulse: `CZGateTwoFlux.apply()` calls `assert_lines_compatible()`
  (control and target flat_length must match) while Quam.load and generate_config still pass (verifier 2).
- Two create buttons (+ Gaussian CZ..., + New pulse), the user's report (D9). An empty gate slot was
  filled inline on no channel, so `CZGate.apply()` played a name `z` did not have (D1); a coupler slot
  was offered on a coupler-less pair (D2) and a linked slot shown as empty (D3).

## 3. Mechanism

**Lab classes with no SM code change (`bd01432`, `3326bf5`, `f8af5f8`).** The env probe adds the lab's
Pulse subclasses (`lab: true`) from modules the chip imports plus modules named on the Pulses env strip
(`instance/pulse_class_modules.json`; names validated, never a blind package walk). The manifest records
`[mtime_ns, size]` per lab source file; `SCHEMA_FORMAT` 2 → 3; a probe-cache entry answers only for the
same env versions, same source stats and exactly the named module set (`3326bf5`: a superset is a miss).
`core/lab_waveform.py` runs `generator/run_pulse_waveform.py` (`-B`) → the class's own
`calculate_waveform()` for detail preview, create preview and row sparkline; KeyedMemo on env + class +
canonical field JSON, token = env signature + lab file stats re-derived on every read; a class's own error
is cached, a failure to run never; a list render never spawns ("–"). Forms are typed from the class's
dataclass schema. `f8af5f8`: one check `_lab_schema_check(store)` on the env strip, the detail and
`/pulses`; a kick that finds its single-flight key held queues a re-run (`_schema_rerun_pending`, started in
`_end_schema_flight`); a stale detail says so, polls `GET /pulse/schema-status` and re-renders once fresh
(never over an uncommitted edit); `/api/pulse/lab-waveform` draws only a class `adaptive_spec_for` vouches
for or `resolve_qclass` matches exact/env/alias (`reason: unknown-class` otherwise). `cade362`: an
unimportable class (`{importable:false, bases:[]}`) no longer vetoes a found pulse, and
`pulse_catalog.class_info_generation()` is in `PulseIndex._stamp()` so a probe landing rebuilds rows;
`f984798` (per_page URL sync, late banner shift) — both with docs/217.

**One "+ New pulse" (`55dc2d0`..`fec4158`, 9 commits).** `8f3ddee`: the first step
(`_pulse_create_choice.html`) chooses New pulse / Copy a pulse to another channel / Gaussian CZ from
cz_flattop (`gaussian_cz.py` unchanged). An empty gate slot gets an op on the channel that plays it
(moving qubit z / coupler, named `<gate>_flux_pulse_<c>_<t>` like the chip) plus a slot link, one undo
group; a linked slot is "held"; a coupler slot only when the pair has a coupler; the in-form "+ new gate"
is withdrawn (410). Copy (`pulse_index.copy_pulse_to`): alias followed, pointers into the source qubit
re-pointed to the target, sibling links kept when the target has the sibling, the rest written as values
and named. `55dc2d0`/`392e79a`: a created/deleted subtree and edits inside it are the user's own on
one-click Apply (D4). IQ on a single-output channel refused (D5); env re-probe after a class new to the
chip (D6); the form is never rebuilt over typed input (`1ae04a4`, `e598e43`; D7). `ee8e4a1`/`fec4158`: the
lab class's own code checks the typed values before the write, refusal verbatim, no env = not blocked.

**The own-code check on every editing door.** `df4abbe`: `/pulse/edit` (`_lab_edit_refusal`). `44fd9cd`:
`_lab_write_refusal` in `/field/edit` and `/field/edit-batch`; `core/lab_watch.py` follows pointer chains
up to 64 hops, cycle-safe, cached against a new `QuamStore.structure_seq` (numbers never bump it);
`web/static/lab-check.js` shows "checking with your class's own code…" from a `/field/lab-watch` probe.
`7e52938`: warm per-env worker (`run_pulse_waveform.py --serve` + `lab_waveform._Worker`), re-validated
before every request (alive, env signature, `os.environ`, first-seen lab file stats), cold fallback,
retired after 600 s idle / env switch / exit. `eb1b702`: lab GATE classes mapped with the pulses they
play; the worker gets the chip as-is and with the writes in one request and runs each gate's `apply()` in
`qm.qua.program()`; refused only on applying → failing; contents pruned to the gate's pair and qubits;
`lab_follow` drives a one-batch "Set ... too" button; `reason='class-unavailable'` never caches or blocks
(warning names the env); `_lab_hold` holds check-to-write for lab-touching writes only.
Doors (fix 3 report): refuse — `/field/edit`, `/field/edit-batch`, `/pulse/edit`, `/pair/<name>/edit`,
`/qubit/<name>/edit`, `/field/create`, `/field/delete`, `/api/pulse/create` (pulse class only); warn only
— `/discard`, `/auto-apply/revert`; not checked by design — whole-state loads, the Ctrl+Z/redo journal,
`_replay_updates`, extras writes, type-fix, diagnostics fix, quam_builder gate writes,
duplicate/copy/rename/delete (rename/delete reopened in Round 3).

## 4. Found by the independent verification and fixed

**w7/adaptive.** Verifier at `3326bf5`: P2 module named during a running probe stuck "not read yet"; P2
detail schema stale after a lab edit until the create form opened; P3 `lab-waveform` imported any
`qclass` — all fixed in `f8af5f8`, rechecked FIXED by the second verifier (strip settled 27.0 s, detail
re-rendered 14.0 s later; implementer: 50.1 s and 18.2 s). Verifier: 1032 passed, 22 skipped on the
30-file neighbour suite on both the branch and integ/w7. The second verification's new findings (found-
pulse veto → `cade362`; per_page, banner → `f984798`) are shape-discovery side, docs/217.

Time order for w7/pulsecreate: implementation → verifier 1 → fix 1 → fix 2 → verifier 2 → fix 3 →
verifier 3 → fix 4. Fix 2 closed fix 1's own open issues before verifier 2 ran; fix 3 answered verifier 2.

## Round 1 — out_verify_pulsecreate + out_fix_pulsecreate

Verifier 1 reproduced 649 passed, 2 skipped; create journey krs5 W=1366 ALL OK (34 ok), big30x W=1600
SLOW=3 ALL OK; `indep_check.py` (KRISS_CZ): every created/copied pulse loads, configs and plays; 23/23 CZ
macros apply on krs5. Defects and fixes:
- major — copy wrote a qubit / channel / gate macro into `<channel>.operations`; after Apply Quam.load
  failed chip-wide (UI race on big30x wrote `qa_racebad` of class XYDriveMW) → `df4abbe`: source must be a
  pulse-index row, and `copy_pulse_to` refuses a `__class__` `is_pulse_class()` rejects.
- major — an existing lab pulse edit was unchecked (`flat_length=4` → 200, then generate_config fails
  chip-wide) → `df4abbe`: `/pulse/edit` asks the class (edited pulse, followed-link target, one-hop readers).
- minor — copy kept links into another pair's gate and the source's `id` → `df4abbe` names kept links,
  drops the id. minor — no moving_qubit='target' pin (`mq = ctl`: 0 red) →
  `test_the_flux_slot_pulse_lands_on_the_target_when_it_moves`. minor — Ctrl+Z right after a delete
  refused once as "made in another window" → `fa8c83e`: UndoQueue holds a press while this window's own
  write is in flight (cap 20 s). Fixer found: `plotDiv.on is not a function` on big30x → `31fb6e9`.

Fix 1, implementer-measured (krs5): the 3 bad copies → 400; `flat_length=4` → 400 in 11.8 s, `=40` → 200
in 10.9 s, catalog edit 0.17 s no spawn; journey lab refusal 14.0 s, commit 13.6 s, first Ctrl+Z after
delete 0.57 s. big30x: refusal 21 s, commit 36.6 s (machine loaded; cached refusal < 2 s server-side).
Tests 938 passed, 4 skipped, 1 failed (intended; `test_an_edit_is_a_new_key_never_the_old_curve`
updated), final subset 240 passed, 2 skipped. 13 mutations, each red.

## Round 2 — out_verify_pulsecreate2 + out_fix_pulsecreate2

Fix 2 (`44fd9cd`, `7e52938`, `584b3c5`), implementer-measured: big30x lab_watch map 81 ms (404 lab pulses,
1504 chain paths), `affected()` ~1.0 us, probe 3.7 ms non-pulse / 23.6 ms lab; big30x HTTP lab edit first
8.8 s then 47 / 30 / 34 ms; non-pulse edits no measurable difference (median 3.5/3.1 → 4.3/2.6 ms).
Real Chrome krs5 `lab_field_edit.cjs` 14/14: grid `flat_length=9` badge 0.75 s, refused 11.7 s cold, tray
0; 80 written 0.63 s warm; Json Tree refusal 8.4 s; pointer-chain write refused through the link, 82
landed in 0.39 s. Tests 1579 passed, 24 skipped, 0 failed; 17 mutations red (one JS pin vacuous at first).

Verifier 2: all 5 round-1 defects fixed (`flat_length=4` → 400 in 0.03 s; Ctrl+Z after delete 475 ms
krs5, 17 s big30x). Journeys ALL OK (40 ok): krs5 catalog creates 555–653 ms, lab refusal 467 ms, commit
398 ms; big30x qa_snz2 14.9 s (cold), qa_mf 3.4 s, refusal 391 ms, commit 2.05 s. Worker: a lab edit
respawns it and the new refusal is served; 4 concurrent draws → one spawn (~9.9 s). Edits: at most
~0.5–1 ms extra. Tests 279 passed, 2 skipped. Defects (fixed in Round 3 by `eb1b702`, `ca54893`, `ad79a28`):
- M1 major — `/pair/<n>/edit` and `/qubit/<n>/edit` unchecked (pair inspector wrote `flat_length=9`).
- M2 major — an env that cannot import the class refused every lab edit as "Your pulse class refused this
  value" (big30x with the CQT_20Q env).
- M3 major — the lab gate was never asked: the journey's own 82 vs 74 broke `q2-3.cz_GNZ` (krs5) and
  `q1-6.cz_GNZ` (big30x) at `assert_lines_compatible`.
- M4 major — `/field/create` wrote `padding=-3` and a whole lab pulse with flat_length 3 unchecked.
- minor a check-then-write race (both 200, stored state class-invalid) → `_lab_hold`; b `/discard` of one
  coupled half → warns; c re-link to a container/dangling pointer → refused; d LabWatch container branch
  unpinned (`if False:` → 21 passed) → `TestLabWatchContainerChain`.

## Round 3 — out_verify_pulsecreate3 + out_fix_pulsecreate3

Fix 3, implementer-measured: gate check warm krs5 0.6–0.8 s in-process / 637 ms Chrome, big30x 0.7–1.2 s
/ 1.26 s; pulse-only warm krs5 17–89 ms, big30x 23–55 ms; cold first ~5–7 s (~11.8 s big30x); Set both
krs5 1.4–1.6 s grid / 1.7–2.2 s chain, big30x 1.7 s / 3.0–3.1 s. After Apply: `pulse_lab_check.py` gates_ok
23 / gates_bad 0 (krs5), 3469 / 0 (big30x). Journeys: `pulses_create_qa.cjs` ALL OK (42 ok) on both,
`lab_field_edit.cjs` 20/20 on both. Tests 419 passed, 2 skipped (`test_lab_doors.py` 27 new); 20 server + 4
selfcheck mutations red (the class-unavailable pin was GREEN until a pulse-only pin was added). Found in
Chrome: the refusal badge ran off-window on a low big30x cell → flips above / pinned inside (`ad79a28`).

Verifier 3: M1, M3, M4 fixed by repro; M2 by the pin only (no foreign env re-run). Race: second parallel
batch refused; discard warns (74/96/20 left invalid by design); re-link → 400. Shared `extras.cz_flat=82`
→ 400, good batch 78+78 → 200. Pruned contents: krs5 5 gates, big30x 86 gates apply (0 artefacts, max
0.72 MB, big30x ~0.6 s per pair). Set both 0.22 s, one /undo both back, stale window 409. big30x: lab doors
1.2–1.7 s, Set both 0.98 s, Apply 9.2 s, 602 plays ok, 3469/3469 macros; krs5 48 plays, 23/23. Tests 419
passed, 2 skipped; 2/2 mutations red. Defects, open for Round 4:
- major — an op a lab gate plays BY NAME (`qubits.q2.z.operations.cz_GNZ_flux_pulse_q2_q3`) is not on the
  gate's route: `/field/delete`, `/api/pulse/rename`, `/api/pulse/delete` → 200, then `apply()` raises
  `KeyError: Operation 'cz_GNZ_flux_pulse_q2_q3' not found in channel 'q2.z'`.
- minor — a pair `qubit_control`/`qubit_target` re-link is not gate-checked; minor — `/api/pulse/create`
  into an empty lab-gate slot is not gate-checked (82 vs 74 → 200); minor — a gate already failing hides a
  later breakage with no note; minor — pruning cut (0 artefacts on the real chips).

## Round 4 — out_fix_pulsecreate4 + out_verify_pulsecreate4

Fix 4 (`d27a372`, implementer-measured, krs5 in-process): the verifier-3 major. An op a lab gate plays BY
NAME is now on the gate's route — `lab_watch._gate_pulses` finds the name each inline or pointed pulse is
played by (its `id`, followed through a pointer, else its key — quam_builder's `get_pulse_name` rule) and
looks it up on every `operations` dict the gate can play on (the owner qubits' channels and the pair's own
channels, pointers followed); every rename / delete / create door asks the gate. Verifier-3 scripts re-run:
V1 by-name `/field/delete` → 400 (gate `KeyError`), V2 `qubit_control` → `#/qubits/q4` → 400, V3
`/api/pulse/rename` → 400, V4 `/api/pulse/delete` → 409 naming the slot's `id`, then `force=1` → 400; the
full-state `apply()` stays clean after each; V5 a mismatch edit on a clash-broken gate → 200 with a "could
not be checked" note. Tests 427 passed, 2 skipped (8 new pins in `test_lab_doors`); 16 mutations red;
selfchecks lab_check / ctrlz / pulses_create / pulses_commit exit 0.

Verifier 4 (independent; tempdir chip copies, rigs 5261/9561 krs5 and 5262/9562 big30x, torn down after):
all five round-3 defects confirmed fixed with round 3's own scripts. Verdict DEFECTS — five minor:
1. a lab check that cannot RUN (the env's python missing, a worker timeout at 90 s or crash) let the write
   through with no word at all;
2. an empty required gate slot on a pair switched the gate check off for every lab gate on that pair, and
   the later "gate slot" fill said nothing when the chip went from not loading to a broken lab gate;
3. deleting a lab gate, or its inline target pulse, left the by-name mirror ops' linked fields dangling and
   `generate_config()` then failed for the WHOLE chip;
4. perf — every ordinary edit read the env settings file from disk before checking whether anything
   lab-related was touched;
5. (admitted open issue, confirmed) spectator pulses (`spectator_qubits_control`) are not on the route; no
   real chip has such a gate.
The machine was out of commit memory (other agents' rigs); two MemoryErrors were re-run clean and are not
SM defects. A first `lab_field_edit` launch with `LFE_COLD=1` may have killed other agents' warm workers
once (they re-spawn on demand); re-run without it.

## Round 5 — out_fix_pulsecreate5

Fix 5 (`7640213`, `11f6b22`, `643be06`; krs5 in-process, big30x rig), per verifier-4 defect:
1. `_lab_write_refusal` gathers every pulse / gate / config question that could not be asked (no env
   selected, the env's python gone, a run-failed record, an exception, a worker timeout) and emits ONE note
   per error — "Your lab code could not be run (<error>): this edit was NOT checked against your class X
   (<path>), your gate Y (<path>)[, your chip's generate_config()]"; a timed-out warm worker falls back to
   a cold run; the note survives a probe that answers after the body was read (`11f6b22`);
2. an unloadable macro is dropped from BOTH sides of the before/after comparison, and a slot fill asks the
   pair's other lab gates — D1 breaking `cz_GNZ` (82 vs 74) while the pair has an empty slot → 400 (was
   200 "could not be checked"); D2 a valid fill → 200, 22/22 macros; D3 → 400 (was 200);
3. deletes reach a config question: a delete that breaks `generate_config()` is refused (gate or its inline
   pulse), one that still generates but orphans a link warns and names the orphans; the Json Tree offers
   "Delete together with N ops" and the batch delete rows ride along;
4. the env settings are read only once something lab-related is touched;
5. `chain_under` with prefixes puts a delete above a lab field's chain end on the route.
Tests 533 passed, 2 skipped (16 files, 7 m 58 s); `test_lab_doors` 50 (15 new pins, 2 adjusted to the new
rule); 18/18 server mutations red (scratch copy, baseline green first, source restored byte-equal). Measured:
the delete config question costs two loads and two `generate_config()` calls — ~1.4 s warm on krs5, ~7 s
warm / 21–26 s cold on big30x — paid only by deletes that reach a lab chain, gate or name-holder; G1 a
gate-breaking edit with the env path gone → 200 + the not-run warning; `pulse_lab_check` big30x 3469/3470
gates `apply()`, config ok; krs5 `indep_check` 0 bad, twice. Open after round 5: a lab gate can no longer
be deleted ALONE while its by-name mirror ops link into it (by design; the Pulses-page delete of its inline
pulse is refused with the "delete them together" text); `lab_field_edit`'s "checking badge shown" step is a
timing race on a warm worker (passes on a cold one, big30x 20/20); big30x `indep_check` not completed
(~3.6 h of QUA serialisation at that load); a Json-Tree edit that goes through with a note shows it twice
(the tree's toast and the amber cell badge); the pruned config question sets aside only channel ops.
The code's `docs/217` / `docs/218` references (`643be06`) resolve to docs/217 and this document, both
written on `w7/docs` and merged with `w7/pulsecreate` in integration round 3 (docs/224).

## 5. Pins

`tests/test_pulse_adaptive.py` (31 → 38 at `f8af5f8`, + `TestTheWarmWorker` 7), `tests/test_pulse_locations.py`
(`test_unknown_bases_never_veto`, `test_probe_landing_refreshes_cached_rows`), `tests/test_pulses_routes.py`
(`TestALabClassEditIsCheckedByItsOwnCode`, `test_the_flux_slot_pulse_lands_on_the_target_when_it_moves`),
`tests/test_lab_doors.py` (`TestAnEnvThatCannotImportTheClassNeverBlocks`, `TestTheTreePlusIsADoorToo`,
`TestLabWatchContainerChain`, `test_a_gate_already_broken_never_blocks_an_edit`,
`test_two_coupled_edits_in_flight_cannot_both_land`), `tests/test_sync_conflict.py`,
`tests/test_live_sync_server.py`, `tests/test_lab_check.py` → `tests/lab_check_selfcheck.cjs`,
`tests/pulses_create_selfcheck.cjs` (P13–P19), `tests/pulses_urlsync_selfcheck.cjs`,
`tests/ctrlz_selfcheck.cjs`, `tests/ndview_selfcheck.cjs`. Journeys `tests/browser/journeys/`
`pulses_adaptive.cjs`, `pulses_adaptive_stale.cjs`, `pulses_create_qa.cjs`, `lab_field_edit.cjs`; lab-env
checker `pulse_lab_check.py` (runs each gate's `apply()` since `ca54893`).

## 6. Open / not done

- Not merged; at merge any held-open route it adds goes into `activity.LONG_POLL_PATHS/EXEMPT_PREFIXES`
  and `test_the_held_routes_are_one_list` (docs/224). 58 `docs/2xx` placeholders remain in code and tests.
- No report ran the full cqt suite on `w7/pulsecreate` (subsets only).
- The first lab edit after a server start or 10 min idle pays one cold spawn (9–12 s krs5); no pre-warm
  (it would spawn with no user action, docs/135).
- `/pulse/edit` names the field that must follow but has no one-press Set both; `_replay_updates` is not
  re-checked against gates; inspector forms re-submit a refused value on focusout (second toast, predates).
- Carried from adaptive: a red "unrecognized pulse class" label (older overlay layer) while the schema is
  pending; a lab row thumbnail stays "–" until drawn once; the stale detail stops polling after 90 × 2 s.
- The component map grows 88 → 123 px ~200 ms after `/pulses` loads (rows move 35 px, pre-existing).
- A pair CR/ZZ channel create is not checkable (neither chip has one).
