# docs/(w9) — The lab-code worker starts before the first lab check; each project remembers its Python env

2026-09-28, branch `w9/labwarm` (base `91c8aae` = origin/main after w8), worktree
`D:\work\sm-w9-labwarm`. **Doc number to be assigned at integration** (other w9 branches
run in parallel). Chips: krs5 (rig copy of 260907_KRS_5Q) and big30x; lab env KRISS_CZ.
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

(filled in from `lw9-krs5/ab.jsonl`, `lw9-big/ab.jsonl`)

## 4. Pins

`tests/test_lab_prewarm.py` (prewarm, retirement, idle 60 min, when it starts, status
route, delete step, activity exemption, quiet cap), `tests/lab_check_selfcheck.cjs`
10-11 (+ render-time state), `tests/test_project_env.py` (memory, landing, opening,
badge, OOB repaint, re-activation), `tests/landing_env_selfcheck.cjs` (picker, badge
refresh). Mutation sweep: 40 mutations, each turned its pin(s) red, source restored
byte-equal (hash-checked). Journey: `tests/browser/journeys/lab_prewarm.cjs` (phases
first / prep / grid / second / other / change).

## 5. Open

- The Pulses list's own sparkline warm (`_warm_lab_sparks`, docs/218) already started
  the worker when the user went to Pulses right after the open; the pre-warm defers to
  it (a check holding the env) — both A and B then pay that first batch (all visible lab
  rows drawn in one ask) before the first check.
- The worker's commit charge is ~1.2 GB private (PrivatePageCount), working set ~285
  MB — the same process the first check started before, now alive from the open until
  60 min idle.
- A Generate Config env pick does not update any project's memory (by design: it may
  be for another chip); the badge says "not the project's" until a project is opened.
