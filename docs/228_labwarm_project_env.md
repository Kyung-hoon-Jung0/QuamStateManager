# docs/228 — The lab-code worker starts before the first lab check; each project remembers its Python env

2026-09-28, branch `w9/labwarm` (base `91c8aae` = origin/main after w8), worktree
`D:\work\sm-w9-labwarm`. Numbered docs/228 at integration (integ/w9, docs/227 is the combined w9 record). Chips: krs5 (rig copy of 260907_KRS_5Q) and big30x; lab env KRISS_CZ.
Everything measured by the implementer on a machine at 60-85 % CPU from other agents'
work (Windows `% Processor Time`, sampled before each run): absolute seconds are
inflated; the A/B pairs were interleaved sample by sample on the same rig, same port,
same instance, so the comparison holds.

## 1. What was asked (user, 2026-09-28)

1. Pre-warm the per-env lab worker (`core/lab_waveform`) so the first lab check / lab
   draw does not pay the worker's spawn + imports: when a chip is opened IF its state
   carries a lab class the pulse/gate check would ask AND an env is already selected;
   when the user selects an env for such a chip; after an env switch (old one retired).
   Yield to foreground requests, never block a page, import only what the chip-open
   class probe imports, never for chips without lab classes.
2. Idle retirement 10 min -> 60 min (kill on env change, lab-file change, exit kept).
3. "Preparing your lab code… (first check after start)" while a check waits on a
   worker still starting; on the Pulses delete step "Checking with your lab code…"
   beside the disabled button.
4. (scope change, same day) An env picker on the project-first landing (Generate
   Config's discovery), remembered per project; a synced project opens with its env
   already selected and nothing is re-discovered or re-probed; a project never synced
   is offered the env used most recently by any project ("suggested — confirm"); the
   project's env IS the one selected env; the UI says which env is active; the
   pre-warm starts the moment such a project opens.

## 2. What was built

**Pre-warm** (`core/lab_waveform.prewarm`, `routes._maybe_prewarm_lab_worker`).
Every activation (`_activate_quam`, both paths; never an archive; never in the suite
unless a pin asks) starts a daemon thread that waits for a quiet server
(`activity.busy` + `bg_gate`, capped at 3 s — what follows is a subprocess, no GIL,
no lock), reads the selected env, builds the chip's lab map (`lab_watch.watch_for`, the
map every edit door uses, cached by structure) and, only if it names a LAB pulse class
or a LAB gate, starts the env's worker with one `@import:` request per class (the lab
classes + the chip's root; the worker also imports `quam.components.pulses.Pulse` and
`qm.qua.program`, the two imports a check makes). Nothing is built, drawn, loaded or
cached. The pre-warm holds the env lock while importing, so a check that arrives
meanwhile waits for THAT worker (one process, never a second cold one); a check that
already holds the lock starts the worker itself and the pre-warm stands down; a
pre-warm whose imports time out answers the waiting check at once (never 2 × 90 s); a
chip switched to meanwhile gets its classes imported by the same worker.

**Env select** (`_apply_selected_env`, shared by `/generate/select-env` and the
project env): the old env's worker is retired at once (`retire_except`) and the open
chip's is started for the new env. **Idle**: `WARM_IDLE_S = 3600`.

**UI**: `/field/lab-watch` names the worker state (`ready|starting|cold|no-env`);
`GET /api/lab/worker-status` (exempt from foreground accounting); lab-check.js shows
"Preparing your lab code… (first check after start)" on the edit badge and on the
Pulses page's lab indicators (field commit, create of a lab class, the delete step)
until the status says ready, then the ordinary line; the detail renders the state at
render time into the indicator (`data-lab-state`) so the first word is right even when
the status poll answers late. The delete step's "Checking with your lab code…" is
rendered only when the delete is asked of the lab (`_lab_delete_asks`).

**Project env** (`core/project_env.py`, `instance/project_envs.json`, keyed by the
project NAME like `project_dataset_roots.json`): card env rows on the landing
(remembered ✓ / suggested — confirm + Confirm / none + Choose…), ONE inline non-modal
picker (`landing-env.js`: `/generate/envs` + `/generate/probe` per env, "Discovering
environments…", per-row "checking…", Use enabled only after the probe says the QM
stack is there, a typed interpreter/folder, Rescan) — nothing fetched until
Change/Choose is pressed. `/qualibrate/project-env` remembers; for the open project it
selects at once. `/qualibrate/open` selects the project's env (remembered, else the
suggestion) before activation, only when it differs; any other open of a synced
project's chip (State Load, Resume, workspace pick, switching back) selects its
remembered env; re-activating the chip that is already active does not. Sidebar env
badge beside the ⚗ project badge (neutral = the project's own, amber = suggested / not
the project's / none, red = interpreter gone); a sync repaints every card whose
suggestion it moved and the badge out of band; a `/generate/select-env` elsewhere
re-fetches the badge (`GET /sidebar/folder-badges`).

## 3. Measured (implementer, real headless Chrome over CDP, interleaved A/B)

A = base `91c8aae`, B = `w9/labwarm`; every sample on a FRESH server (no worker).
Flow: `POST /load` → `/qubits` → `/bulk` (Live Edit) → at +N s after the open, the
FIRST lab check: an edit the lab's gate refuses (`q1-2 cz_SNZ flux_pulse_qubit.flat_length`
78 → 82) or a delete it refuses (the gate's inline pulse). Nothing written (peeked
after every sample). See §3 table in the branch report `out_w8_labwarm.json` for the
per-sample lines; summary filled below.

First lab check after the open, ms (each number one fresh-server sample; A and B
interleaved; every check refused by the lab's gate, nothing written):

| chip | check @ | A (base) | B (labwarm) |
|---|---|---|---|
| krs5 | edit +5 s | 25983, 25566, 14835 | 15289, 10247, 12964 |
| krs5 | delete +5 s | 17896, 16055, 19344 | 27882, 10532, 19896 |
| krs5 | edit +15 s | 26710, 16255, 20801 | 12487, 2313, 8650 |
| krs5 | delete +15 s | 22803, 15123, 19729 | 9666, 1889, 11243 |
| krs5 | edit +60 s | 12103 | **483** |
| krs5 | delete +60 s | 16478 | **1302** |
| big30x | edit (sent when `/bulk` finished, 32-45 s) | 12176, 10284, 11384, 11309, 18021 | **1939, 1132, 1921, 1582, 1467** |
| big30x | delete (same) | 12323 | **4010** |
| big30x | edit / delete +15 s (sent at ~44 s) | 25304 / 14273 | **1909 / 2172** |
| big30x | edit / delete +60 s | 11497 / 13320 | **2002 / 1421** |
| big30x, Pulses page first | delete / edit +15 s | 23541 / 26020 | **6911 / 2821** |

- Open → worker ready (B, polled): krs5 median 18.4 s (15.0-32.6 s), big30x median
  17.6 s (13.6-24.5 s) — the import alone, on a machine at 60-85 % CPU (docs/218
  measured the same spawn at 8.8 s unloaded). At +5 s the worker is still importing,
  so B's first check there is the rest of the import plus the check (still faster in
  5 of 6 krs5 samples); once ready the remaining cost is the check itself (krs5 edit
  0.48 s, delete 1.3 s — the delete asks `generate_config()` twice; big30x 1.1-2.0 s
  edit, 1.4-4.0 s delete, the pruned gate `apply()` / config question on a 30-qubit chip).
- Page loads, medians A → B: krs5 `/load` 461 → 463 ms, `/qubits` 503 → 475, `/bulk`
  2234 → 2100 (14 + 14 samples); big30x `/load` 1029 → 1187, `/qubits` 1511 → 1527,
  `/bulk` 38.7 → 39.3 s (10 + 10). The big30x `/load` gap was re-measured in process
  (Flask test client, fresh process per sample, 6 + 6 interleaved): A median 2195 ms,
  B 2070 ms — no slowdown.
- The extra process: working set 284-290 MB, private commit ~1.21 GB (Win32
  `PrivatePageCount`) — the same worker the first check started before; now alive from
  the open until 60 min idle.
- Pulses page first, big30x journey: the delete's first check 43.8 s → 12.4 s after the
  sparkline warm was chunked (worker's first answer 71 s → 26 s after the open).

## 4. Pins

`tests/test_lab_prewarm.py` (prewarm, retirement, idle 60 min, when it starts, status
route, delete step, activity exemption, quiet cap), `tests/lab_check_selfcheck.cjs`
10-11 (+ render-time state), `tests/test_project_env.py` (memory, landing, opening,
badge, OOB repaint, re-activation), `tests/landing_env_selfcheck.cjs` (picker, badge
refresh). Mutation sweep: 44 mutations (M1-M44), each turned its pin(s) red, source restored
byte-equal (hash-checked). Journey: `tests/browser/journeys/lab_prewarm.cjs` (phases
first / prep / grid / second / other / change).

Refute-lens review (an independent critic agent over the diff): one P1 — an env
selected from an ordinary chip open killed the old env's worker on the request thread
(`Popen.wait(timeout=5)`) — fixed (`retire_except(background=True)`, pinned, mutation
red); two P2 noted as design tradeoffs (below).

## 5. Open
- Only ONE env's worker is kept (the user's rule: retire on env change). A session that
  alternates between two projects on DIFFERENT envs restarts a worker on every switch
  (in the background, ~15-30 s under load) — the 60-min idle does not help that pattern.
- The Pulses list's own sparkline warm (`_warm_lab_sparks`, docs/218) already started
  the worker when the user went to Pulses right after the open; the pre-warm defers to
  it (a check holding the env) — both A and B then pay that first batch (all visible lab
  rows) before the first check. Fixed in-branch: that warm now asks in chunks of 4 and
  yields to a waiting check between chunks; the first chunk still carries the imports.
- The worker's commit charge is ~1.2 GB private (PrivatePageCount), working set ~285
  MB — the same process the first check started before, now alive from the open until
  60 min idle.
- A Generate Config env pick does not update any project's memory (by design: it may
  be for another chip); the badge says "not the project's" until a project is opened.
