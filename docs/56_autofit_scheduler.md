# 56 — Autofit: the one-button automatic fitting scheduler

> Status: **v1 BUILT** on `feat/autofit-scheduler` (off `main` @ 7d953f8) —
> core (`core/autofit/`: synth, families, gates, auditor, plan, writer,
> engine, sim/real backends), web surface (`/autofit` page + status poll +
> guards + sidebar badge), and the full verification ladder of docs/56 §6:
> reader-compat goldens, the per-family corruption accuracy ledger, engine
> state-machine + RealBackend protocol tests, E2E over the genuine
> QuamStore/WorkingCopy/Saver write path, and the one-button flow through the
> Flask client (sim backend). All §7b review amendments implemented.
> Lineage: docs/40 (Scheduler — the execution spine), docs/47 (AI fit-review — the
> evidence base + LLM doctrine), docs/50 (fit-audit — verdict machinery), the
> outcome-rule chaining engine (`scheduler.py:782–918`), and an 8-agent recon
> synthesis over all of them (2026-07-17).

## 0. The ask (verbatim intent)

> The user presses **one button** → a sequence of experiments runs (their way,
> maximally flexible parameters) → each result is **fitted**, an **LLM corrects
> wrong fittings**, and the **state updates automatically**. Focus: **1Q and CZ**.

Today every piece exists in isolation and the user does the glue by hand: queue
assembly is manual, failed nodes pause the queue with no retry, fits land in
state unreviewed (or reviewed one apply-popup at a time the next morning), and
the fit-audit is a slow post-hoc backlog sweep. Autofit is that glue, made
trustworthy.

## 1. The reframe that makes "LLM corrects bad fits" safe

docs/47 measured (real archive, manufactured wrong-fit sets) that an LLM reading
a figure must **never emit a calibration number** (pixel precision is ~250× the
kHz budget; generic SM-side re-fits miss by MHz unless they replicate the node's
exact conditioned channel). The calibration numbers must always come from the
**node's own fitter**. So in Autofit, "the LLM corrects a bad fit" decomposes
into three honest mechanisms, in priority order:

1. **Detect** — deterministic gates first (they beat every cheap LLM on 1-D
   families: 0% false-accept at $0 — docs/47 Phase-0), an LLM verdict only on
   the residual ambiguity, and only when enabled (BYO key).
2. **Revert** — a rejected fit's state write is undone **deterministically**:
   the node's own `patches[].old` (or the pre-run working-copy snapshot) is the
   restore value. The LLM never sources a number, not even for reverts.
3. **Re-measure** — the corrective *number* comes from re-running the node with
   **adapted parameters** (wider span, more averaging, re-centered window —
   deterministic per-family adaptation rules, selected by the failure mode the
   gates/LLM diagnosed). The node's own fitter then produces the new number.

This is exactly what a careful physicist does: they don't hand-edit a frequency
off a bad plot — they re-take the scan around the right feature.

## 2. Architecture

```
                 ┌──────────────────────── /autofit (one button) ────────────────────────┐
                 │ Plan (preset/custom) → per step:                                       │
                 │   RUN (scheduler chassis / sim backend)                                │
                 │   → INGEST (result_ref attribution, dataset store)                     │
                 │   → GATE  (deterministic tiers: outcome, metrics, feature x-check,     │
                 │            plausibility band, history drift)                           │
                 │   → AUDIT (LLM verdict on suspects only — accept/reject/abstain,       │
                 │            failure mode; NO numbers)                                   │
                 │   → DECIDE (keep | revert | retry(adapted params) | defer)             │
                 │   → WRITE  (audited path: batch_set→save→apply_to_live, group_id,      │
                 │            ledger provenance)  /  REVERT (patches.old)                 │
                 │   → next step (criticality: a failed critical step halts dependents)   │
                 └────────────────────────────────────────────────────────────────────────┘
core/autofit/
  plan.py        Plan/Step models + JSON schema + shipped presets (1Q bringup, CZ tuneup)
  families.py    per-family knowledge: name matching, metric gates, plausibility bands,
                 update ops (assign/subtract/ceil4/half), feature x-check spec,
                 adaptation rules keyed by failure mode
  gates.py       deterministic verdict pipeline over (run, family, chip state, history)
  auditor.py     LLM layer: providers (anthropic / openai-compatible / fake / off),
                 vision+table bundle, verdict contract, side-file persistence
  writer.py      staged-write orchestrator + deterministic revert (in-process, locked)
  engine.py      the plan state machine (worker thread), scheduler-chassis driver,
                 retry budgets, persistence, pause/abort, ledger
  synth.py       synthetic run generator (9 node families, ground truth + corruption)
  simbackend.py  SimBackend: emits synth runs into a dataset root + applies node-style
                 patches to a sim chip — the whole loop runs hardware-free
web: /autofit page (+ routes + autofit.js + _autofit*.html), sidebar item, lock/badge
docs/56 (this file)   tests/test_autofit_*.py
```

### 2a. Engine ↔ scheduler integration (decided)

The engine **drives the existing scheduler chassis** — it never re-implements
spawn/kill/refresh. Per step it: appends one queue item (tagged
`autofit: {plan_run_id, step_id, attempt}`), calls `scheduler.start()`, and
waits for the item's terminal status (the worker naturally idles on an empty
queue between steps; `start()` re-spawns per step, which is cheap). Evaluation
runs AFTER the post-node refresh hook attributed `result_ref` (reconcile +
rescan already done). The UI mutator lock stays continuous across the whole
plan: `_scheduler_lock_guard` additionally checks `autofit.is_active()` (the
between-steps gap would otherwise let an edit slip in and trip the next
preflight). The engine's own writes are in-process (never HTTP), so the guard
doesn't block the writer itself.

Preflight: the full 8-gate scheduler preflight runs once at plan start
(`force` honored, same contract as `/scheduler/start`); each step re-uses the
scheduler's own per-item fail-closed re-classification.

### 2b. Backends

- **real** — the scheduler chassis above (subprocess `run_experiment.py`,
  temp-copy `custom_param` injection, kill-tree). Chosen over qualibrate-runner
  REST for v1: full lifecycle control + param injection; REST has one-global-run
  semantics and no injection seam (docs/55 §2 keeps REST as a later tier).
- **sim** — `SimBackend` bypasses the chassis: `synth.py` emits a run folder
  (indistinguishable to `dataset.py`/`ndview.py`/`contracts.py` readers) into a
  dataset root and applies node-style patches to the sim chip's live files.
  Ground truth + seeded corruption modes make false-accept/false-reject
  **measurable in CI**. The evaluate→decide→write path is byte-identical in
  both backends — that is the point.

### 2c. The deterministic gate pipeline (`gates.py`)

Per (step, target) in order; first hard failure short-circuits to a verdict:

| gate | source | catches |
|---|---|---|
| G0 item status | scheduler result | crashed/timeout/cancelled runs |
| G1 node outcome | `data.outcomes[q]` + `fit_results[q].success` | the node's own gate said failed |
| G2 metric bands | family registry (r², fwhm, snr, error-bar ratios) | low-quality "successes" |
| G3 feature x-check | raw h5 (family-specific localizer: 1-D argmax/argmin vs claimed value within tolerance; docs/47 POC winner) | wrong-peak / sidelobe locks |
| G4 plausibility band | family registry physical bands (e.g. T1 ∈ (0, 1 ms), amp ∈ (0, 1.5]) + relative-jump vs current state (e.g. |Δf01| > 50 MHz suspicious) | absurd values with good r² |
| G5 history drift | Param History trend (optional, when index has ≥3 points) | slow corruption / step changes |

Outcomes: `pass` (Tier-A confidence), `suspect(failure_mode)` (→ LLM if
enabled, else policy default), `fail(failure_mode)` (hard). Failure modes:
`no_signal | wrong_peak | noisy | out_of_band | drifted | node_failed | unverifiable`.
G3 is family-gated: only families whose stored value is provably the argmax bin
(A1 evidence) get the x-check; `resonator_*` families (rotated-S21 channel)
skip it (docs/47 §resonator DEFER) and rely on G2/G4.

### 2d. The LLM auditor (`auditor.py`)

- **Contract (docs/47, binding):** input = figure PNG (+ compact numeric
  context), output = `{verdict: accept|reject|abstain, failure_mode, reason}`.
  No numeric field exists in the schema. `failure_mode` selects the adaptation
  rule — qualitative, not quantitative.
- **Providers:** `anthropic` (Messages API, vision), `openai_compat`
  (`/v1/chat/completions`, urllib — covers Ollama/gateways), `fake`
  (deterministic, for tests), `off`. Config in `instance/autofit_ai.json`
  (key, base URL, model, max calls per plan). stdlib urllib only — no new deps.
- **Scope:** suspects only (G-pipeline residual). Never a pre-filter on
  success; never an authority over G3's deterministic rejection (an LLM accept
  cannot override a deterministic fail — one ack never collapses two gates).
- **Budget:** per-plan max-calls cap; over-budget suspects → policy default
  (`defer`). All verdicts → ledger side-file, never data.json.

### 2e. Decision policy + retry (`engine.py`)

```
gate/audit verdict → decision per (step, target):
  pass / accept        → KEEP  (node already wrote via its own patches)  or STAGE+APPLY
                         when the node didn't write (patches null) and the family maps
                         a writable target (fit_targets-derived registry)
  reject|fail          → REVERT (patches.old via audited write path) + RETRY with
                         adapted params (failure-mode-keyed rule) while budget lasts
  retry exhausted      → REVERT (state stays pre-step) + DEFER to review queue
  abstain/unverifiable → policy knob: defer (default) | keep | revert
```

- Retry budget: per-(step,target) `retry_max` (default 1), per-plan step cap,
  wall-clock cap. Retries re-run **only the failed targets** (scheduler
  `targets` splicing). Provenance `autofit_attempt: n` — distinct from the
  outcome-rule `inserted_by` (whose depth-2 cap stays untouched).
- Step `criticality`: `hard` (failure halts the plan for affected targets —
  e.g. qubit spec failed ⇒ skip rabi/ramsey for that qubit), `soft` (record
  and continue). Target-scoped: qA1 failing never blocks qA2's chain.
- **Autonomy knob** (per plan run): `full` = auto-apply to live (the one-button
  promise; every write still gated by G-pipeline + LLM and reverted on reject),
  ~~`staged` = write working copy only~~ (SUPERSEDED by §7b-A — dropped),
  `review` = nothing written; report only. Default for shipped presets:
  `full` for sim/dry contexts (~~staged-first-run~~ superseded by §7b-A —
  `review` is the cautious mode now)
  plan (the UI remembers the user's last explicit choice per chip).

### 2f. The writer (`writer.py`) — the only write path

In-process equivalent of `/field/edit-batch` + `/state/apply-to-live`, under
the same locks and gates (build lock → `store._lock`; chip-identity token
captured at plan start; edit-policy respected; one `group_id` per (step,
target) so a human Ctrl+Z reverts an autofit write like any other). Sequence:
`modifier.batch_set` → `saver.save()` → (`autonomy=full`) →
`working_copy.apply_to_live` with StaleLiveError → one pull+re-stage retry →
else defer. History snapshot (`check_and_snapshot(..., "save")`) after each
apply, so Param History trends include autofit points and any step is
time-machine-revertible. Reverts write `patches[].old` (or the pre-run
snapshot value when patches are null) through this same path.

### 2g. Provenance ledger

`instance/autofit/runs/<plan_run_id>/ledger.jsonl` — append-only events:
`plan_started, step_started, run_attributed, gate_verdict, llm_verdict,
decision, write_applied {group_id, paths, old→new}, revert_applied, retry
_scheduled, step_done, plan_done`. The report page renders exclusively from
the ledger; the LLM's `reason` strings live here (audit trail), never in state.

## 3. Plans (`plan.py`)

```jsonc
{
  "name": "1Q bringup", "version": 1,
  "targets": {"kind": "qubits", "names": ["qA1","qA2"]},   // or "all_active"
  "autonomy": "review",
  "steps": [
    {"id": "res_spec",  "node": "02_resonator_spectroscopy_wide.py", "criticality": "hard",
     "params": {"num_shots": 400}, "retry_max": 1, "gates": "default"},
    {"id": "qubit_spec","node": "08_qubit_spectroscopy.py", "criticality": "hard",
     "params": {"frequency_span_in_mhz": 100}, "retry_max": 2},
    ...
  ]
}
```

- Steps reference node files relative to the scheduler's calibrations folder;
  params are validated against the scheduler's scanned schemas (same modal
  forms — reuse, not reinvent). Per-step params + per-plan targets = the
  "maximally flexible" requirement, with presets so the button stays one click.
- Shipped presets — **1Q bringup**: resonator spec (wide→single) → qubit spec →
  power rabi → ramsey → readout freq/power opt → IQ blobs → T1 → echo.
  **CZ tuneup**: chevron → conditional phase (error-amp variant) → phase
  compensation (run-and-verify only: its update is read-then-fold, excluded
  from auto-write per fit_targets.py:19–39) → optional interleaved-RB verify.
  Presets ship as data (`core/autofit/presets/*.json`), user plans persist in
  `instance/autofit_plans.json`.

## 4. Family registry (`families.py`)

One code-curated registry (repo doctrine: code + parity tests, no YAML). Keyed
by normalized node name (the `_normalize_node_name` matcher — same as recipes /
fit_targets). Each entry: metric gates (G2 bands), plausibility bands +
relative-jump limits (G4), feature x-check spec (G3: which h5 var/axis, peak or
dip, tolerance in FWHM units), writable targets (**reuses `FIT_TARGET_MAP`
entries verbatim where they exist** — parity test pins the subset relation) +
autofit-only extensions with `op` semantics (`assign | subtract |
assign_ceil4 | assign_half_to`) for ramsey (`f_01 -= freq_offset`), T1/echo
(`T1/T2echo ←`), chevron (`amplitude ← cz_amp`, `length ← ceil4(cz_len)`),
and adaptation rules: `wrong_peak → recenter span ×0.5 on G3 feature axis
bin` (window math from swept axis, not from any LLM output), `no_signal →
widen span ×2 + shots ×2`, `noisy → shots ×2`, `out_of_band → widen span ×2`.

v1 registry families (all 9 have synth generators): resonator_spectroscopy
(single + wide), qubit_spectroscopy, power_rabi, ramsey, T1, echo (T2),
readout_frequency_optimization, iq_blobs, cz_chevron, cz_conditional_phase.

## 5. Web UI

Sidebar **Autofit** (below Scheduler). One page, four zones:

1. **Plan bar** — preset dropdown + target picker (chip roster) + autonomy knob
   + **▶ Run plan** (THE button) / Pause / Abort. Advanced: step editor
   (add/remove/reorder steps, per-step params via the scheduler's schema
   modal, retry/criticality/gates).
2. **Live board** — step × target matrix; cell states: pending / running /
   pass ✓ / corrected ↻ (reverted+retried, shows attempt) / reverted ⤺ /
   deferred ? / halted ✕. Row click → run deep-link (Datasets) + verdict chain.
3. **Review queue** — deferred/reverted cells with the run figure, gate + LLM
   verdicts (reason strings), and the audited apply affordances (the existing
   apply-popup path) for human resolution.
4. **Report** — per-plan-run summary from the ledger: params before → after
   (with group_ids → one-click undo), timing, retries, LLM budget used.

Poll-driven (`/autofit/status`, the scheduler badge pattern); the top-bar badge
shows `Autofit: step k/N · <node> · <target-progress>` while active.

## 6. Verification plan (dummy-data-first — the user's explicit ask)

1. **synth golden tests** — every family: generator → readers
   (`dataset._parse_run_folder`, `experiment_data`, ndview build, contracts
   pre_update_value) parse it indistinguishably from real runs (shape-pinned
   against the real-run field lists captured 2026-07-17, e.g. qubit-spec
   ds_raw {I,Q,IQ_abs,phase,detuning,full_freq,qubit}).
2. **gate false-accept/false-reject ledger** — per family: N clean + N
   corrupted (wrong_peak, no_signal, noisy, out_of_band, drift) synthetic runs
   → G-pipeline must reject every corruption class at 0 false-accepts on 1-D
   families; publish per-family counts in the test (the docs/47 accuracy-ledger
   pattern, CI-enforced).
3. **engine state machine** — SimBackend plan runs: happy path (all pass ⇒
   state equals ground truth within tolerance), bad-fit path (corrupted step ⇒
   revert + retry with adapted params ⇒ converges on attempt 2), exhaustion
   (defer + pre-step state intact), criticality (hard failure halts only the
   affected target's chain), abort/pause mid-plan, autonomy review vs full.
4. **LLM auditor** — fake provider: contract round-trip, budget cap, no-number
   schema enforcement, deterministic-fail-overrides-LLM-accept invariant.
5. **routes/UI smoke** + lock coverage (autofit active ⇒ mutators 409) +
   ledger/report rendering; full suite green.

## 6R. Real-archive replay validation (2026-07-18)

The offline tier the sim corpus can't provide: 12 saved runs / 45 (run,qubit)
verdicts from a real lab archive (LabA + its CR campaign), families
`resonator_spectroscopy_vs_power` + `qubit_spectroscopy_vs_power` (the two
docs/47 "hard" 2-D families), replayed hardware-free through
`core/autofit/replay.py` + `generator/run_autofit_replay.py` (node-faithful
re-fit + re-plot in the lab's own QM env; the archive is never written — all
fixes land in per-run sandboxes).

Results (full matrix + figures in the local, uncommitted report):
- **G1**: 9/9 node-declared failures hard-failed; 3 of them *recover* under
  today's analysis (the old gate discarded valid data — the docs/50 class).
- **Clean chains**: 20/20 pass|agrees — zero false alarms.
- **Mined suspects (3)**: one caught by the refit oracle as `reject`
  (stored-success data today's own gate refuses; vision confirms the claim
  sits on noise ~8.5 MHz off a clearly visible dip), one caught as `drift`
  (+3.2 MHz punch-through pick, and it was PATCHED into state — the revert
  demo), and one **refit-blind** (a self-consistent noise fit the replay
  AGREES with — only the figure exposes the empty window; the canonical
  proof that the vision auditor is mandatory, exactly as designed in §1).
- **Vision adjudication also cleared a mined false-positive** (a genuine
  qubit move between sessions, not a bad fit) — jump-mining alone
  over-flags; judge-only vision resolves both directions.
- Marginal-SNR rvp runs surface as tightened-gate `reject`s (review-queue
  material, values never clobbered) — the dressed/bare branch ambiguity
  docs/47 predicted.

Pinned by `tests/test_autofit_replay_real.py` (auto-skips off the
workstation): the clean anchor passes+agrees; the refit-blind bad anchor is
documented as vision-mandatory. Next tier (the user's ultimate ask): the GUI
before/after report — load a bad run, show stored vs refit figure + state
old→new, then apply through the audited writer.

## 7. Explicit non-goals (v1)

- No LLM-emitted numbers anywhere (incl. reverts, window math) — doctrinal.
- No qualibrate-runner REST transport (later tier; seam kept in backends).
- No detached-process survival of an SM restart (daemon thread + headless
  heartbeat, orphan-reconcile marks interrupted — same as scheduler).
- No re-fit engine in v1 (fit_audit's node-faithful replay stays post-hoc;
  wiring it as an inline G-gate for its 2 families is a v2 candidate).
- No graphs inside plans (steps are nodes; qualibrate graphs keep their own
  runner + the existing dry-run refusal).

## 7b. Binding amendments — adversarial design review (2026-07-17, 4 lenses ×
## adversarial verification; every surviving finding confirmed against code)

**A. Autonomy semantics rewritten (CRITICAL — the staged fork).** Nodes write
the LIVE state themselves (`record_state_updates` → the run's patches); a
"working-copy-only" staged mode is unachievable without either lying (live got
touched) or breaking the calibration chain (step N+1's subprocess loads live
`state.json`, so a live-revert + working-copy-stage starves it of step N's
result, and `_reconcile_cached_quam_ctx` freezes on `live_diverged` the moment
the working copy dirties — routes.py:651–660). v1 therefore ships TWO honest
modes and drops `staged`:
  - `full` (the one-button default): nodes apply as they naturally do; the
    engine evaluates after each step and **reverts rejected patches on LIVE**
    (writer → apply_to_live), so the chain always runs on gate-approved
    values. A **pre-plan pinned history snapshot** + per-step group_ids give
    one-click plan-wide or per-step undo afterwards.
  - `review`: same execution (the chain still needs each step's values), but
    at plan END the engine restores the pre-plan state (from the pinned
    snapshot, through the audited path) and hands the user a report whose
    per-step values are one-click appliable. "The chip ends where it started."
  Gate anchors (G4 jumps, G5) always read measurement-time values via the
  patches-first rule, never the frozen working copy.

**B. Engine⇄worker synchronization (3 confirmed races).**
  1. *Lost wakeup*: the worker EXITS on an empty queue (scheduler.py:1359–1364
     `break`; liveness cleared later in `finally` :1415–1424) — `start()`
     during that gap no-ops (:1498–1500) and the enqueued step strands. Engine
     watchdog: after enqueue+start, if the item is still `queued` and
     `is_running()` is False past a short deadline → `start()` again
     (idempotent under `_QLOCK`).
  2. *Attribution race*: terminal status is persisted BEFORE the refresh hook
     runs (:1378–1391 vs :1399–1404), and the hook is registered only by the
     `/scheduler/start` route (routes.py:12822–12825) — an in-process plan may
     run with NO hook. Resolution: the engine does not depend on the hook at
     all — after terminal status it performs its own post-item ingest
     **synchronously** (reconcile-by-path + dataset rescan + name-matched
     attribution, same primitives the hook uses); a still-registered web hook
     doing the same work first is harmless (attribution is monotonic).
  3. *Two masters*: between steps `run.status` is `idle`, so every
     `/scheduler/*` mutator (start-with-settings-persist, queue ops, preset
     load, pause/cancel) is open. While `autofit.is_active()`: 409 the
     scheduler mutator routes too (start/settings/queue/preset/pause/cancel),
     same guard that already 409s field editors.

**C. Revert correctness.** Reverts restore `patches[].old` with
`coerce=False` (exact-typed restoration — the default coerce would cast an
old string/pointer through the new value's type); only `op:"replace"` patches
with a usable `old` are auto-revertible — `add`/`remove` patches defer to the
review queue. Three-way guard: a patch is reverted only while the live value
still equals the patch's `value` (someone/something else changed it since ⇒
defer, never clobber). Engine-completed queue items are removed after ingest
(the ledger is the record; the scheduler queue stays clean).

**D. Preset portability.** Presets pin **family keys**, not filenames; at plan
build the engine resolves each step against the scanned calibrations folder
via `_normalize_node_name` matching (the same drift-proofing the recipes/
fit_targets registries use), and the UI shows a step→file resolution table
(ambiguous/missing ⇒ dropdown pick or skip) before Run.

**E. Physics corrections.** 1Q preset reordered to the proven graph chain
(readout opt + IQ blobs BEFORE ramsey/T1/echo — state discrimination needs
thresholds); CZ preset gains the coarse conditional-phase step before the
error-amp variant. The spectroscopy `wrong_peak` adaptation drops the
unimplementable recenter (sweep centers are pinned to state) in favor of
`span ×2 + step ×0.5 + operation_amplitude_factor ×0.5` (kills power-broadened
/ two-photon ghosts); G3 argmax localization is honest for qubit-spec +
readout-freq-opt, coarse-screen-only for resonator (wide tol), and
signal-presence-only (spectral) for oscillation/decay/2-D families — richer
per-family localizers (ramsey FFT, decay 1/e-crossing, chevron contrast
ridge) are the documented v2 ledger-tightening path.

**F. Morning-after surfacing.** A finished plan persists its report; the
sidebar Autofit item carries a "review N" count while unreviewed
defers/reverts exist, so the overnight story doesn't end at a badge that
vanished with the run.

## 7c. Implementation audit round (2026-07-17, 3 lenses × adversarial verify)

15 confirmed findings, all fixed in-tree except one documented deferral:

- **StaleLiveError retry was a no-op** (the ctx reconcile latches
  `live_diverged` and re-raises forever): the writer's retry is now a genuine
  pull + re-stage — `sync_from_live → store.reload → replay the caller's own
  rows (reverts re-win their CAS against the fresh content or refuse) → save →
  apply` (writer.py `_apply_live_with_one_retry`).
- **Shared-path patches** (e.g. a port `full_scale_power_dbm` alongside qubit
  patches — real-corpus confirmed): recorded pre-plan for the review restore;
  surfaced in the review queue when any target was rejected (they are never
  target-attributable, so never auto-reverted).
- **Engine claim race** (`is_running` false between registry claim and thread
  start): a `_starting` flag holds the claim atomically.
- **Abort leaked a queued chassis item** the user's next Scheduler ▶ would
  run with the plan's overrides: removed on the abort path too.
- **`staged` autonomy fully removed** (legacy plans map to `review`); default
  autonomy is `review`.
- **Ctx captured, never re-resolved**: the real backend's reconcile binds the
  ctx object at plan start (a mid-plan /load can't displace it).
- **Start serialization**: `/autofit/start` holds a per-instance lock across
  guards + world prep + engine claim (closes the sim-world rmtree TOCTOU and
  the scheduler-exclusion window during real-backend prep).
- **Sim plans never lock the chip** (`locks_chip` = running ∧ ¬sim feeds the
  guard + the `scheduler-active` body class); the guard is method-gated (GETs
  on /scheduler/settings and the presets list stay readable), autofit-first
  (no alternating 409 reason mid-plan), with a distinct `autofitLocked`
  toast; the scheduler badge is suppressed while the autofit badge is
  authoritative; the report pane re-keys on `plan_run_id` (no stale report
  across runs); review-mode restore surfaces non-restorable add-op keys.
- **Deferred (documented)**: G5 history-drift points are not wired into the
  route-built engines yet (`history_points_of=None`) — G4's jump checks still
  produce `drifted` verdicts; wiring Param-History trends into G5 is the v1.1
  ledger-tightening item.

## 8. Key decisions record (from the 12 recon open questions)

| # | decision |
|---|---|
| 1 | subprocess chassis; backend seam for sim (and future REST) |
| 2 | audit inline in the engine thread between steps (serial, lock held) |
| 3 | autonomy knob full/review (§7b-A dropped staged); full = the one-button promise, gate-guarded + revertible + pre-plan snapshot; review = restore-at-end |
| 4 | autofit-owned family registry with op vocabulary; FIT_TARGET_MAP reused + parity-pinned, never mutated |
| 5 | correction = revert + adaptive re-measure; LLM contributes failure_mode only |
| 6 | own retry budget (per-target retry_max + plan caps), provenance distinct from outcome-rule inserts |
| 7 | SM-native plan JSON + shipped presets; steps = nodes only |
| 8 | StaleLiveError → one pull+re-stage retry → defer; engine is sole writer while lock held |
| 9 | code-curated registry + parity tests (no YAML) |
| 10 | side-ledger JSONL per plan run; ChangeEntry untouched |
| 11 | synthetic corpus IS the Phase-0 harness for deterministic gates; real-LLM measurement ships as an optional scripted tool |
| 12 | daemon thread + headless heartbeat; restart ⇒ interrupted + resumable plan state |
