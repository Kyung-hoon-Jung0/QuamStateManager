# 78 — Runner + AI calibration agent (PLAN, final)

> Status: **PLAN ONLY — nothing implemented.**
> Worktree `.claude/worktrees/runner-agent`, branch `feat/runner-agent`,
> based on `origin/main @ 8e5fa99`. Design discussion + investigation: 2026-08-05.
> Lineage: docs/40 (Scheduler chassis) · docs/47 (LLM doctrine + Phase-0
> measurements) · docs/50 (fit-audit) · docs/56 (Autofit v1/v2) ·
> docs/48 (ndview + click contracts) · docs/52 (env capability probing).
>
> **Written to survive context compaction and model swaps.** Every decision
> carries its rationale, every fact carries how it was verified, every phase
> carries pros / cons / caveats. A fresh context should read §1–§5 before
> touching code. §12 is the fast index.

---

## 0. Placeholders (this file is tracked → no customer identifiers)

| placeholder | meaning |
|---|---|
| `<node-tree>` | the **source-of-truth** calibration tree: `calibrations/1Q_calibrations/*.py` + `calibration_utils/<util>/{analysis,plotting,parameters}.py` |
| `<env-new>` | customer conda env — quam **0.6.0**, numpy 1.25.2, xarray 2025.6.1. Also the canonical pytest env |
| `<env-old>` | customer conda env — quam **0.5.0a3**, numpy 1.25.2, xarray 2025.6.1 |
| `<archive-9q>` | 9-qubit example archive (2026-03/04). **Only source of coupler-flux runs** |
| `<archive-A>` | primary customer archive (2026-05) — volume source for 1-D families |
| `<archive-C>` | third archive (2026-06) |

Real paths / env names live in `CLAUDE.local.md` (gitignored decoder).
**Never** write a real customer name, host, or path into any tracked file.

---

## 1. Goal

### 1.1 User intent (verbatim, 2026-08-05)

> 1. User presses a button.
> 2. Several experiments run.
> 3. Looking at the accumulated experiments, the final state is updated.
> 4. (Or, after each experiment) its fit is recorded into state and the next run
>    is based on that. Then another run.
>
> …and we introduce an **AI agent** into this.

**(3) and (4) are not alternatives.** (4) is structurally required — the next
node's subprocess loads the LIVE `state.json`, so deferring the write starves
the chain (docs/56 §7b-A proved this). (3) is a **consolidated
cross-experiment review layer** on top, and it does not exist today in any form
(every verdict is per-(step, target) in isolation). Phase P6 builds it.

### 1.2 PoC goal (binding, user-set)

> Run until the AI agent, **looking at the figure itself**, accepts it as a
> **"correct experimental signature"** — for a clear **Qubit Spectroscopy** and
> a clear **Rabi oscillation** (2-D `11_power_rabi`) — reached through the whole
> chain that makes an **x180 gate** meaningful.

Why this is a good goal: it sits exactly on the one thing docs/47 measured as
**solid** (signature recognition is scale-free and transfers across chips —
Clause A); it side-steps the numeric-precision argument; and it side-steps the
"what T1 counts as done?" problem, for which SM has no data model (§10.1).

### 1.3 Termination rule (was missing; now explicit)

> A (step, target) is **done** only when the deterministic gates **pass** AND the
> vision judge **accepts** the signature.

- gates fail + judge accepts ⇒ **not done** (D-2: an LLM accept never overrides
  a deterministic fail).
- gates pass + judge rejects ⇒ **adapt and re-measure** (this is the loop).
- Both must be recorded in the ledger with their reasons.

---

## 2. Scope — the 9 families

| # | node (`<node-tree>/calibrations/1Q_calibrations/`) | util | runs `<archive-A>`+ | runs `<archive-9q>` |
|---|---|---|---|---|
| 1 | `03_resonator_spectroscopy_single.py` | `resonator_spectroscopy` | 93 | 16 |
| 2 | `05_resonator_spectroscopy_vs_power.py` | `resonator_spectroscopy_vs_power` | 59 | 1 |
| 3 | `06_resonator_spectroscopy_vs_flux.py` | `resonator_spectroscopy_vs_flux` | 17 | 17 |
| 4 | `07_resonator_spectroscopy_vs_coupler_flux.py` | `resonator_spectroscopy_vs_coupler_flux` | **0** | **11** |
| 5 | `08_qubit_spectroscopy.py` | `qubit_spectroscopy` | 236 | 48 |
| 6 | `08b_qubit_spectroscopy_vs_power.py` | `qubit_spectroscopy_vs_amplitude` | 49 | 0 |
| 7 | `09_qubit_spectroscopy_vs_flux.py` | `qubit_spectroscopy_vs_flux` | 68 | 26 |
| 8 | `10_qubit_spectroscopy_vs_coupler_flux.py` | `qubit_spectroscopy_vs_coupler` | **0** | **12** |
| 9 | `11_power_rabi.py` | `power_rabi` | 156 | 116 |

≈ **925 real runs**; every family has ≥ 11. **No hardware before P9.**

Why the flux variants are in (user, 2026-08-05): on a tunable chip the operating
point must be established before `f_01` is even *defined*. Without them the
qubit-spec number is "a frequency at an unknown flux".

**Out of scope:** the `…_vs_power_iq` node-name variant (absent from
`<node-tree>`; user decision).

---

## 3. Design decisions (D-1 … D-14)

### D-1 — Verification tiers for a calibration number

Old doctrine (docs/47): *"the LLM never emits a calibration number."*
User half-disagreed: let the AI produce it, then **verify by re-fitting /
re-plotting with our own modules**.

Refined, not accepted-or-rejected. Key realization: docs/47's "generic re-fit
misses by 2.5–17.9 MHz" measured **our own re-implementation**
(`core/interactive_plots/models.py`, 7 closed-form fits). It does **not** apply
to `core/fit_audit.py` + `generator/run_fit_audit.py`, which **import and call
the lab's own `calibration_utils/<util>` module inside the customer env** —
node-faithful by construction.

| tier | verifier | precision | the AI's number is used as… |
|---|---|---|---|
| **A** | the node's own `analysis.py`, via replay | identical to the node | a **seed / discrete choice** (which peak, which branch). State receives the **fitter's** output |
| **B** | the sweep grid is finer than the required precision | grid-exact | **directly writable** — reading it off the plot is an exact operation |
| **C** | only our closed-form models exist | ±MHz | **never auto-written**; human-gated proposal only |

**Binding restatement:**
> Not "the AI may not emit numbers", but
> **"a number the AI emitted does not reach `state.json` before passing a
> verifier of tier A or B."**

Membership is **computed, not guessed**: A = a `calibration_utils` analysis
module exists (all 9 have one); B = `sweep step : required precision` computed
from the corpus (P0d).

*Pros:* adopts the user's idea; provable; reuses existing machinery.
*Cons:* tier A costs one subprocess per verification (seconds).
**Caveat (sharpest in this document):** a tier-C family with a confident AI
number and a pretty regenerated plot is the most dangerous configuration in the
design — *a precise, auditable, WRONG number wearing a trust badge* (docs/47's
own words). Tier C stays human-gated no matter how good the model looks.

### D-2 — Vision is the primary authority; deterministic gates are the safety floor

User: *"vision이 주역이 되는 설계라는 걸 명시하자 — 정확해 내가 원하는 게 이거야."*

- Deterministic gates (`gates.py` G1–G5) keep their veto: an LLM `accept` can
  **never** override a deterministic `fail`.
- But the **"is this a correct / clear signature?"** judgment is the LLM's, and
  it is what terminates the loop (§1.3).
- Consequence: **7 of 9 families are 2-D**, where there is no deterministic
  localizer at all (G3 degrades to signal-presence). This scope is *deliberately*
  the hard case, which raises the stakes on D-7 and D-8.

### D-3 — Action space classified by "can the result lie to us?", not "is it a number?"

The safety story rests on the judge SEEING the consequence. So the axis that
matters is whether a wrong choice yields an **obviously bad figure**
(self-revealing; cost = one run) or a **plausible-looking invalid measurement**
(deceptive; the error propagates).

| class | parameters | agent's power |
|---|---|---|
| **A — self-revealing** | `frequency_span_in_mhz`, `frequency_step_in_mhz`, `num_shots`, `num_flux_points`, `min/max_flux_offset_in_v`, **drive power / amplitude factor**, target selection, next-node selection | **picks real numbers**, inside code-owned bounds |
| **B — deceptive** | `reset_type`, `use_state_discrimination`, `multiplexed` | **proposes only**; code checks the precondition and may refuse |
| **frozen** | `line_attenuation_in_db`, `input_line_impedance_in_ohm` | never touched — facts about the wiring; changing them silently rescales every power |
| **reserved** | `simulate`, `timeout`, `load_data_id`, targets keys | already blocked by the scheduler (docs/40 Phase 2b) |

`num_shots = 3` is a number and is perfectly safe (wrong ⇒ visibly noisy).
`use_state_discrimination = True` without calibrated IQ blobs is a boolean and
is dangerous (clean-looking populations that are garbage).

**User decision:** drive power is **class A**, conditional on satisfying
`core/spec_constraints.py`.

*Caveat:* class-B preconditions are stated in prose inside the recorded schema
(`reset_type`: *"Must be implemented as a method of Quam.qubit"*). The prose is
the specification; enforcement must be re-expressed as code.

### D-4 — The agent returns a typed object against a generated reduced schema

Never free text. Per step, generate a **reduced JSON Schema** from the node's own
recorded `data.parameters.schema`: class-B keys **removed**, class-A keys
annotated with bounds (D-5). The agent must return an object valid against it, so
validation is mechanical and we never have to trust the agent.

This is `auditor.py`'s existing trick in reverse: today the verdict schema has
**no numeric field at all**, making a number structurally impossible. Here we
**explicitly open** exactly the fields we allow.

### D-5 — Bounds are code-owned, from three sources

The recorded schema has types, defaults and prose but **no min/max** (§4.4):

1. **Hardware reach** — `core/spec_constraints.py` (bands, ±400 MHz IF window…).
2. **The corpus** ← best source. From ~925 real runs, compute the range this lab
   has actually used per parameter per family. A bound *derived from data*.
3. **Budget** — total runs / wall clock (D-8).

*Caveat:* do **not** derive bounds from schema defaults — observed values leave
them far behind (`num_shots = 3` vs default 100; flux ±2.5 V vs default ±0.5 V).

### D-6 — L3 division of labour: code owns preconditions, the agent owns priorities

- **Code owns preconditions** — what must precede what. These are **facts**
  (no discrimination without calibrated blobs; no qubit spec without readout).
- **Agent owns priorities** — given what we now know, what is most valuable next.

If the agent invents ordering it will eventually emit a physically impossible
sequence. This split is what makes L3 safe.

### D-7 — The judge must be calibrated in BOTH directions before a plan runs

| judge fault | effect | existing defence |
|---|---|---|
| **lenient** (false-accept) | stops on garbage; bad calibration written | deterministic gates keep their veto (D-2) ✅ |
| **stingy** (false-reject) | **never terminates**; burns the night; every retry looks individually justified | **none today** ❌ |

docs/47 measured a local VLM blanket-rejecting **57/57** perfect figures.

**Pre-flight (user-approved):** before a plan starts, feed the judge N known-good
archived figures; require **accept ≥ 90 %**, else refuse to start.

**Gap found while planning (user agreed):** that pre-flight measures only
*stinginess*. The binding risk per docs/47 is **false-accept**, which it does not
measure at all. Both halves are required:

| direction | measurement | material |
|---|---|---|
| stinginess | known-good figures → **accept ≥ 90 %** | archive runs |
| **leniency** | **manufactured wrong-fit figures → reject rate** | take a good run, move the stored fit onto a sidelobe / wrong peak / adjacent branch, **regenerate the figure with the lab's own `plotting.py`** |

The second only became feasible once we confirmed we can call the lab's plotting
module ourselves (§4.3). docs/47 asked for exactly this ("manufacture the
wrong-fit set — don't wait for it") and could not build it.

*Caveats:* the bar is **per family**, and families 4/8 have only 11–12 runs — a
"90 %" on 11 samples is nearly meaningless, so report the sample count beside
every rate and never present a thin family as measured (docs/47's
accuracy-ledger discipline: *a thin family must read as visibly thin*).

### D-8 — Stop-loss has three tiers; a counter is not a stop-loss

A budget says *how much you spent*; a stop-loss says *you are not learning / you
are making it worse*. Only the first exists today.

**Tier 1 — budget.** Per-target retries · plan step cap · **wall clock** · LLM
calls. **Scoped per target**, plan level a ceiling only: a hopeless q3 must not
eat the whole night.

**Tier 2 — no progress (absent today).** Two independent signals:
- **(a) deterministic metric trend — free.** `gates.py` already extracts
  `contrast`, `r2`, `fwhm`, spectral presence every attempt. If none improves
  over K rungs we are not learning. No LLM needed.
- **(b) pairwise vision comparison.** Show the judge the previous and current
  figure: **`better | worse | same`**. Comparative judgment is far more reliable
  than absolute confidence (docs/47 forbids self-reported confidence as a gate,
  rightly), and the output stays a discrete verdict with no number.

Both flat ⇒ stop. This is also the only thing that catches **oscillation**
(widen → too coarse → refine → out of window → widen …), where every individual
step is justified and a counter never fires.

**Tier 3 — harm detection.** State drift beyond the G4 band; seeds accumulating
unconsumed; drive power near the `spec_constraints` ceiling; **the same target
escalating upstream twice** (⇒ the problem is not where we think it is).

**On stop (user decision):** **revert this target** to its last known-good state,
**continue to the next target**, collect into the morning report. Never hand a
human a half-adapted chip (seeds written, power ramped) — failure must fail
cleanly. Halt the whole plan only for a common cause (hardware/env failure).

**The most valuable stop is a question, not an exhaustion:** *"I narrowed it to
two hypotheses and cannot separate them — which of these two figures?"* The
dressed/bare branch of family 2 is exactly this shape and docs/47 calls it the
one genuinely hard family. Design it as a **normal terminal state**.

### D-9 — Notifications (user decision: webhook **and** browser)

None exist today (§4.7). Build both: **webhook** (`urllib`, stdlib; one URL in
`instance/`; reaches a phone) and the **browser Notification API** (free when a
tab is open).

Send **only when judgment is needed**, never per step: ask-a-human · target
reverted+abandoned · plan halted · plan complete.

Verified: autofit already **survives a closed browser** — `realbackend._wait`
calls `scheduler.touch_ui()` every poll, so the 90 s heartbeat pause never fires
on an autofit plan. The SM **process** must stay up (v1 limitation, docs/40 §8).

### D-10 — Judge model: Sonnet (user decision), re-checked by measurement

`instance/autofit_ai.json` → `provider: "anthropic"`, `model: "claude-sonnet-5"`.
**No code change** — the model is a config string (`auditor._DEFAULTS`).

Honest caveat: docs/47 measured **Opus** (0/136 false-accept) and **Haiku**
(~17 %, all in the hard 2-D families). **Sonnet was never measured**, and 7 of 9
families are 2-D. This is the one unmeasured assumption in the design — and D-7's
two-sided calibration is exactly the experiment that settles it.

Option kept open: **tiered models** — Sonnet routine, Opus-class only for the
residual ambiguity.

### D-11 — The judge is fed the LAB's figure, not SM's re-render

Because we can call `plotting.py` ourselves, the judge sees the figure the
physicist sees. This deletes an entire class of fidelity risk — and §4.2 proves
that risk is currently live. SM's Interactive tab remains the **human's**
click-to-apply surface, and must still be fixed (D-12) because humans use it.

**Two hard requirements** (both agreed):
1. **Per-target panel.** Several plotting modules emit one panel per qubit/pair
   in a single sheet. The judge must receive **only the target's panel**, or it
   judges the wrong qubit. This is correctness, not polish.
2. **Regeneration fidelity.** For same-generation runs, the regenerated figure
   must match the archived PNG. If regeneration diverges, the judge is looking at
   something the physicist never saw. Pinned by a test (P0e).

### D-12 — Interactive figure parity is IN SCOPE (user decision)

Three transposes + one missing recipe, **and the click contracts must move with
them** (§4.3). See P1.

### D-13 — Verification is **env-driven**: the customer's env is ground truth

User: *"우리 SM은 고객의 env에 따라서 검증하게끔 해야돼 — 나중에 다른 환경도
선택할 수 있기 때문."*

Measured env × archive matrix:

| env | quam | `<archive-9q>` (2026-03) | `<archive-A>` (2026-05) |
|---|---|---|---|
| `<env-new>` | 0.6.0 | ✗ `duration_control`→`duration_qubit` | ✗ unknown `grid_location`/`isolation` |
| `<env-old>` | 0.5.0a3 | ✓ (only the `qubit_pairs` shim gap) | ✓ **bit-identical** replay |

So **an archive is replayable — with the right env.** Consequences:

1. The verification env is a **first-class, user-selectable input**, never
   hard-coded. `fit_audit.audit_run(node_name, folder, env, source_root)` already
   takes it; the surrounding UX and the autofit path must too.
2. SM must **probe env × archive compatibility** and say plainly which env can
   verify which run, instead of surfacing a raw quam traceback.
3. Every verdict records **env + `lib_versions` + `gate_hash`**
   (`run_fit_audit` already emits all three). A verdict without its env is not
   reproducible.
4. **Rejected alternative:** a JSON-backed namespace shim bypassing `Quam.load`
   to make replay env-independent — see §9.

**Distinguish two different things** (easy to conflate, and the plan depends on
the distinction):
- **env compatibility** = can `Quam.load` read this run's `quam_state`?
- **analysis generation match** = was this run produced by the same
  `calibration_utils` code we are replaying with?

They are independent. `<archive-A>` matches `<node-tree>`'s analysis
(proven bit-identical, §4.5a). `<archive-9q>` does **not** (§4.5b) — and no
choice of env fixes that, because the drift is in the analysis code, not quam.

### D-14 — Update targets are **run-derived**, never hardcoded (NEW, from review)

Verified by reading the nodes' own `update_state`:

| node | writes |
|---|---|
| `06`, `07`, `09`, `10` (flux) | `qubit.z.independent_offset` **or** `qubit.z.joint_offset` — **routed by `qubit.z.flux_point`** — plus `z.min_offset`, `z.phi0_voltage`, `z.phi0_current` |
| `11_power_rabi` | `q.xy.operations[node.parameters.operation].amplitude` — **the operation name is a run parameter** (observed `x180_DragCosine`, and `x90_DragCosine` written too) |
| `03` | `resonator.f_01`, `resonator.RF_frequency` |

`families.py` currently hardcodes `qubits.{q}.xy.operations.x180.amplitude` for
`power_rabi` — **which does not exist on a chip whose operation is
`x180_DragCosine`.** Any forward-write or revert built on that path is wrong.

Therefore `UpdateSpec` resolution must read the run's own `parameters` and the
chip's own state (flux_point routing), exactly the way SM's click contracts
already do for node 06 (`resonator_2d.py` comments call it "flux_point-routed
field"). Additionally, `11_power_rabi`'s fit emits
`required_full_scale_power_dbm` / `recommended_amplitude` — so an amplitude
update may implicate **FSP**, which SM governs with a never-silent compensation
doctrine (r12, `core/mw_fem.py` + `core/autofit/power_rows.py`). That
interaction must be designed, not stumbled into.

---

## 4. Verified facts (2026-08-05; re-verify after any stack upgrade)

### 4.1 The lab's axis convention is NOT uniform

| family | lab x | lab y |
|---|---|---|
| res spec (1-D) | `full_freq_GHz` | amplitude |
| res spec **vs power** | `detuning_MHz` | `power` |
| res spec **vs flux** | **`flux_bias`** | **`freq_GHz`** |
| res spec **vs coupler flux** | **`flux_bias`** | **`freq_GHz`** |
| qubit spec (1-D) | frequency | rotated I |
| qubit spec **vs power** | `freq_GHz` | `power` |
| qubit spec **vs flux** | **`flux_bias`** | **`freq_GHz`** |
| qubit spec **vs coupler flux** | **`flux_bias`** | **`freq_GHz`** |
| power rabi (2-D) | `amp_mV` | `nb_of_pulses` |

**vs power → frequency on x. vs flux → flux on x.** Confirmed visually against
archived `figures.amplitude.png` for families 3, 7, 8.

### 4.2 SM put frequency on x universally → the four flux families are transposed

| # | family | SM recipe | SM x | SM y | verdict |
|---|---|---|---|---|---|
| 1 | res spec | `resonator` | RF frequency | Amplitude [mV] | ✅ |
| 2 | res vs power | `resonator_2d` | RF frequency | Readout power [dBm] | ✅ |
| 3 | res vs flux | `resonator_2d` | RF frequency | flux bias [V] | ❌ **transposed** |
| 4 | res vs coupler flux | `resonator_2d` | RF frequency | coupler flux bias [V] | ❌ **transposed** |
| 5 | qubit spec | `qubit_spectroscopy` | RF frequency | rotated I [mV] | ✅ |
| 6 | qubit vs power | `qubit_spec_vs_power` | RF frequency | Drive power [dBm] | ✅ |
| 7 | qubit vs flux | `qubit_spec_vs_flux` | RF frequency | flux bias Δ [V] | ❌ **transposed** |
| 8 | qubit vs coupler flux | **`fallback`** | — | — | ❌ **no recipe at all** |
| 9 | power rabi | `power_rabi` | Pulse amplitude [mV] | Number of pulses | ✅ |

Resolution verified by calling `registry._resolve()` on the real node names.
Family 8 → `fallback` = **empty menu** ⇒ only static PNGs, **no click-to-apply**.
("New recipe for node 10" = create `recipes/qubit_spec_vs_coupler_flux.py`;
`resonator_2d` already lists node 07 in `FAMILY`, nothing lists node 10.)

Four independent lines of evidence agree on the transposes: the lab's
`plotting.py`; SM's recipe `layout`; the heatmap array shapes; the archived PNGs
side by side.

**The user reported only the resonator one.** Both flux families are equally
transposed — the resonator case merely *looks* catastrophic (a nearly-flat
horizontal band becomes a vertical band) while the qubit case is an arch that
still reads as an arch after a 90° rotation. **Fixing only what was reported
would have fixed half the bug.**

Related hazard: `ds_raw` axis order differs per family **and even within one
file** — family 7 has `I/Q/IQ_abs` as `(qubit, detuning, flux_bias)` while
`phase` is `(qubit, flux_bias, detuning)`. **Bind axes by dim NAME, never by
position.**

### 4.3 Click contracts are bound to the axes — they must move together

```python
# recipes/qubit_spec_vs_flux.py
flux_delta_targets(bundle, qname, axis="y")                     # click y → flux field
freq_increment_targets(bundle, qname, axis="x", axis_scale=1e9) # click x → f_01 / RF_frequency
```
Transposing the figure without swapping these makes a click write the
**frequency into the flux field** — worse than a wrong-looking plot.
Transpose + contract swap + click round-trip golden must land in ONE commit.

### 4.4 What a run records

```
metadata.{name, run_start, run_end, status, description, data_path}
data.parameters.model    → the ACTUAL values used
data.parameters.schema   → JSON Schema: type + default + prose description (NO min/max)
data.outcomes            → {entity: "successful"|…}   entity = PAIR name for coupler nodes
data.quam                → "./quam_state"  (every run carries its own state snapshot)
patches                  → [{op, path, value, old}]  — present only when update_state fired
```
- Observed values leave defaults far behind (`num_shots = 3` vs 100; flux ±2.5 V
  vs ±0.5 V) ⇒ D-5's "never derive bounds from defaults".
- Where `patches` is absent, the state delta is still recoverable by diffing
  consecutive runs' `quam_state` snapshots (`core/differ.py`,
  `core/compare_sources.py`).
- `metadata.name` can be missing ⇒ `_derive_util` falls back to the folder
  basename and does **not** strip `#<id>_`, producing a non-identifier util and
  an import error (fix in P0b).

### 4.5 Cross-generation reality

**(a) Same generation ⇒ bit-identical.** `<archive-A>` `#95 08_qubit_spectroscopy`
via the shipped `run_fit_audit.py` in `<env-old>` against `<node-tree>`:
```
fresh   frequency = 5715387535.868236
archive frequency = 5715387535.868236     ← identical
```
The fresh fit also returns exactly `r2`, `contrast`, `fwhm` — the three metric
gates `families.py` already declares. **`<archive-A>` is analysis-generation-matched
with `<node-tree>` ⇒ it is the numerically scorable archive.**

**(b) Cross generation ⇒ values AND field names drift.** `<archive-9q>` coupler
run replayed against `<node-tree>`:

| | fresh | archived |
|---|---|---|
| resonator frequency | 7,074,653,188 | 7,074,800,000 (`sweet_spot_frequency`) |
| frequency shift | −346.8 kHz (`frequency_shift`) | −200.0 kHz (`freq_shift`) |
| idle offset | −0.0686 | **+0.0391** (opposite sign) |
| flux min | 0.0967 (`min_offset`) | −0.0476 (`flux_min`) |
| success | True | True ✅ |

⇒ **Archived numbers are NOT a valid numeric oracle for `<archive-9q>`, under any
env** (the drift is in the analysis code, not quam — D-13). Consequence:
**families 4 and 8 can never be numerically scored**, because they exist only
there. They remain fully usable for **signature judging** (the figure is
regenerated fresh by the same `plotting.py`) and for **`outcomes` labels**.

**(c) Env decides loadability** — see D-13's matrix.

### 4.6 Replay works — with one known, small gap

With `node.namespace["qubit_pairs"]` provided, the coupler analysis runs
end-to-end:
```
process_raw_dataset → OK   (I, IQ_abs, Q, phase; coords current, detuning, flux_bias, full_freq)
fit_raw_data        → OK
  q0-1: success=True, resonator_frequency=7,074,653,188 Hz, idle_offset=-0.0686, dv_phi0=0.3305
```
Today `run_fit_audit._Node` supplies only `{"qubits": …}` (`run_fit_audit.py:167`),
so families 4 and 8 die at `analysis.py:87` with `KeyError: 'qubit_pairs'`.

**Pair identity must come from the RUN's own record.** Verified the hard way:
`q0` belongs to both `q0-1` and `q0-3`, so deriving pairs from the machine gave
2 pairs for a 1-pair cube and xarray raised
`AlignmentError: conflicting dimension sizes {1, 2}`. Correct source:
`data.parameters.model["qubit_pairs"]`, cross-checked against `data.outcomes`.

### 4.7 What does NOT exist

| | status |
|---|---|
| plan-level **step cap** | **absent** — the work queue is a `deque` runtime rungs `appendleft` into, with no global bound |
| plan-level **wall clock** | **absent** (docs/56 §2e claims both — doc/code drift) |
| notifications (email / webhook / browser) | **absent entirely** |
| `fit_audit.FAMILIES` | only **2** entries |
| `families.py` coverage | **4 of our 9 families are ABSENT** (`06`, `07`, `09`, `10`); `power_rabi` is a shell (`metric_gates=[]`, presence-only check, 2 trivial adaptations, no ladder, no `verify_wide`) and its update path is **wrong for this chip** (D-14) |
| G5 history-drift gate | **dead in production** — routes build `PlanEngine` without `history_points_of` |
| cross-experiment consistency review (goal §1.1 #3) | **absent entirely** |

Present and healthy: per-step `retry_max` (0–5, default 1), LLM budget
(40 calls/plan), per-step timeout (3600 s), target-scoped `criticality`,
adaptation ladders, CAS seed restore, `review`-mode restore-at-end, and the
`writer.py` single-write-path discipline.

---

## 5. Architecture

```
                ┌────────────────── /autofit  (THE button) ──────────────────┐
   goal /       │  AGENT LOOP                                                │
   preset  ───► │   propose next (node, targets, params)                     │
                │        │  validated against the REDUCED SCHEMA (D-4)       │
                │        ▼                                                   │
                │   RUN  — scheduler chassis                        [reuse]  │
                │        ▼                                                   │
                │   INGEST — realbackend attribution                [reuse]  │
                │        ▼                                                   │
                │   VERIFY — node's own analysis via replay (D-1 A)  [extend] │
                │        ▼                                                   │
                │   GATES  — G1..G5 deterministic                   [reuse]  │
                │        ▼                                                   │
                │   JUDGE  — vision on the LAB figure (D-11)        [new]    │
                │        ▼                                                   │
                │   PROGRESS — metric trend + pairwise vision (D-8)  [new]   │
                │        ▼                                                   │
                │   DECIDE — done / adapt / revert+next / ask        [extend] │
                │        ▼                                                   │
                │   WRITE  — writer.py, the ONLY write path         [reuse]  │
                └────────────────────────────────────────────────────────────┘
                                  ▼ at plan end / checkpoint
                     CONSOLIDATED CROSS-EXPERIMENT REVIEW  (goal #3)  [new]
```

| new module | responsibility |
|---|---|
| `core/autofit/envmatrix.py` | env × archive compatibility probe + per-run env selection (D-13) |
| `core/autofit/figure_gen.py` | call the lab's `plotting.py` → per-target panel PNG (D-11); also builds the manufactured wrong-fit set (D-7) |
| `core/autofit/corpus.py` | index archives (params, outcomes, patches/quam_state, figures, generation) + observed parameter ranges (D-5) + sweep-step:precision ratios (tier B) |
| `core/autofit/action_space.py` | A/B/frozen classification, precondition checks, reduced-schema generation (D-3, D-4) |
| `core/autofit/progress.py` | deterministic metric trend + pairwise-vision comparison (D-8 tier 2) |
| `core/autofit/judge_calib.py` | two-sided judge calibration + the 90 % gate (D-7) |
| `core/autofit/consistency.py` | cross-experiment consolidated review (goal #3) |
| `core/notify.py` | webhook + browser notification (D-9) |
| `recipes/qubit_spec_vs_coupler_flux.py` | the missing Interactive recipe (D-12) |

**Extended:** `fit_audit.FAMILIES` (2 → 9) · `generator/run_fit_audit.py`
(`qubit_pairs`, util derivation) · `autofit/families.py` (**4 new families +
power_rabi rebuild + run-derived UpdateSpec**, D-14) · `autofit/engine.py`
(stop-loss, agent proposer) · `autofit/auditor.py` (signature verdict + pairwise
compare) · `recipes/resonator_2d.py` + `recipes/qubit_spec_vs_flux.py`
(transpose + contracts).

---

## 6. Phases

Ordering rule: **everything verifiable offline comes first**; each phase ends in
an independently useful state. **P1 is independent of everything** and may ship
at any time.

### P0 — Foundation: replay + figures under the customer env

**P0a `envmatrix.py`** — probe (env × archive-generation) compatibility; record
it; surface it honestly ("this run needs an env with quam < 0.6") instead of a
raw quam traceback. Stamp env + `lib_versions` + `gate_hash` on every verdict.
*Caveat:* probing spawns; cache keyed on the interpreter's **package versions**,
never mtime (the docs/52 lesson — an mtime-keyed cache goes stale after
`pip install`).

**P0b `run_fit_audit.py` hardening** — supply `qubit_pairs`; derive pair identity
from `data.parameters.model["qubit_pairs"]` cross-checked with `data.outcomes`
(§4.6); strip `#<id>_` in the `_derive_util` folder fallback (§4.4).

**P0c `fit_audit.FAMILIES` 2 → 9** — `{util, value_field, value_tol, label}` each.
Cheap; the physics lives in the lab tree.

**P0d `corpus.py`** — index all archives; per-family observed parameter ranges
(D-5); sweep-step:precision ratios (decides tier-B membership, D-1); generation
tagging (which archive is analysis-matched with `<node-tree>` — §4.5).

**P0e `figure_gen.py`** — call the lab's `plotting.py` for a (raw, fit) pair →
**per-target panel** PNG (D-11.1). **Test:** for same-generation runs the
regenerated figure matches the archived PNG (D-11.2).

**Exit criterion:** for every family, ≥ 5 real runs replay AND regenerate a
per-target figure, under an automatically chosen env.

### P1 — Interactive figure parity (D-12) — independent, shippable alone

**P1a** transpose the three flux recipes to the lab convention (x = flux,
y = frequency) **and swap the click-contract axes in the same commit** (§4.3).
**P1b** new `recipes/qubit_spec_vs_coupler_flux.py` (node 10).
**P1c** promote the parity harness (built during this investigation, currently
scratch-only) into a permanent test: per family, SM's axis convention must equal
the lab's.

*Pros:* fixes a live user-reported bug; zero coupling to the agent work.
**Caveat: highest-risk edit in the plan despite looking cosmetic** — a wrong
contract swap writes a frequency into a flux field. **Goldens first, then the
transpose.**

### P2 — Family knowledge for all 9 (the physics layer)

Was a footnote in the draft; the review showed it is a **major** piece.

**P2a** add the 4 missing families to `families.py` (`06`, `07`, `09`, `10`):
metric bands, plausibility bands + jump limits, feature checks (2-D ⇒
signal-presence only), adaptation ladders.
**P2b** rebuild `power_rabi` (today `metric_gates=[]`, no ladder, no
`verify_wide`).
**P2c** **run-derived `UpdateSpec` resolution (D-14)** — flux-point routing for
the flux families; operation-name-from-parameters for `power_rabi`; and the
`required_full_scale_power_dbm` → FSP interaction (r12 doctrine: never silent).
**P2d** wire the dead **G5** history gate (`history_points_of`) — it is also a
tier-3 harm signal for D-8.

*Caveat:* code-curated with parity tests, exactly like the existing registry
(repo doctrine: code + tests, never YAML). Every band should be justified from
the corpus, not invented.

### P3 — The judge + two-sided calibration

**P3a** prompt + exemplar pack per family. **Shape and relative geometry only** —
never absolute or fractional-of-axis position (docs/47 Clause B: a feature's
fractional position is an artefact of the sweep window the experimenter chose,
not physics). Versioned data files, editable without a code change.
*Specific item:* define what a **clear 2-D power-rabi signature** is — it is an
error-amplification map (converging fringes about the optimal amplitude), not a
sine. The PoC's success is defined on it.

**P3b** `auditor.py`: the signature verdict (the §1.3 terminator) + the pairwise
`better|worse|same` comparison (D-8 tier 2b). Keep the numeric-emission guard —
these schemas stay structurally number-free.

**P3c/P3d** two-sided calibration (D-7), reported **per family with sample counts**.

**Decision point:** does Sonnet clear both bars per family? If it fails only on
the hard 2-D families, adopt tiered models (D-10) rather than abandoning it.

### P4 — Action space and bounds (D-3, D-4, D-5)

`action_space.py`: A/B/frozen registry; class-B precondition checks; bounds from
`spec_constraints` + corpus ranges; reduced-schema generation from the run's
recorded schema.

### P5 — Stop-loss (D-8)

P5a budgets (including the **missing** plan step cap and wall clock, §4.7),
scoped per target; P5b `progress.py`; P5c harm detection; P5d
revert-and-continue.
*Caveat:* "revert this target" must **compose with** the existing CAS seed
restore and `patches[].old` revert, not duplicate them.

### P6 — The loop, the button, and the consolidated review (the user-facing deliverable)

**P6a** wire the agent into `engine.py` as the step proposer under D-4/D-6.
Define the entry point: a shipped **preset encoding the x180 chain**
(03 → 05 → 06/07 → 08 → 08b → 09/10 → 11) as the default goal, with the agent
choosing order/params inside it.
**P6b** the live board + per-target state, reusing the docs/56 §5 surface.
**P6c** **the consolidated cross-experiment review (goal §1.1 #3)** —
`consistency.py`: at plan end (and at checkpoints) reconcile everything learned
across experiments, flag mutually inconsistent results, and present a single
apply/undo surface. **This is a stated user goal with no implementation today.**

*Caveat:* P6c is where "look at the accumulated experiments and update the final
state" actually lives. Do not let it collapse into "a list of per-step results" —
the value is the cross-checking.

### P7 — Notifications (D-9)

`core/notify.py`: webhook + browser; settings in `instance/`; four events only.

### P8 — Offline end-to-end scoring

Replay a real session; give the agent the first *k* runs; ask what it would do
next; compare with what the human actually did — including the known operator
loops (docs/56 §6V cases A/B/C, which are exactly our families).

The honest metric is **not** "the agent agrees with the human" but **"the agent
reaches the same conclusion in fewer runs."** Case C is the reference: the human
burned three drive-power attempts and a day before refining the step.

### P9 — Real hardware (only when the user asks)

Pre-flight: confirm a scheduler-run node produces a dataset run with
`fit_results` **and** `patches`; confirm `realbackend._attribute` matches the real
node names (its normalized-prefix + time-window match is the most fragile seam on
first contact with hardware).

### Minimum PoC path

**P0 → P2 (families 5 + 9 only) → P3 → P4 → P5 (tier 1 + 2a) → P6a/P6b.**
That demonstrates exactly §1.2 (button → runs → vision-judged signature on qubit
spec and rabi). P1 ships in parallel; P2's other 7 families, P5c/d, P6c, P7, P8
harden it into the full product.

---

## 7. Merge protocol (repo convention — do not skip)

- One phase (or a coherent sub-phase) = one branch off `main`, e.g.
  `feat/runner-p0-foundation`, `fix/interactive-flux-axes`.
- Per branch: targeted tests → **full gate** →
  `docs/78` amended with what actually shipped → commit → merge from the main
  checkout → push.
- **Full gate is the canonical command** and must land on the documented
  Windows baseline (CLAUDE.md: ≈ 18 failures, all in the published catalogue).
  Anything beyond that list is a regression.
- Never `git add -A`; stage explicit paths. Never commit `CLAUDE.local.md`.
- P1 must additionally re-run the click round-trip goldens
  (`tests/test_click_contracts.py`, `tests/test_cz_contracts.py`).

---

## 8. Risk register

| # | risk | severity | mitigation |
|---|---|---|---|
| R1 | P1's contract swap misroutes clicks (frequency → flux field) | **high** | goldens first; transpose + contract in ONE commit; per-family round-trip test |
| R2 | D-14 wrong update path (`x180` vs `x180_DragCosine`) writes or reverts the wrong field | **high** | run-derived resolution + a test per family against real `patches` |
| R3 | Sonnet fails the 2-D families | medium | measured in P3 before anything depends on it; tiered-model fallback ready |
| R4 | Stingy judge ⇒ non-terminating loop | medium | D-7 pre-flight + D-8 tier-2 progress stop |
| R5 | Manufactured wrong-fit set too easy ⇒ false confidence | medium | physically plausible perturbations only; eyeball review |
| R6 | Families 4/8: 11–12 runs, cross-generation ⇒ **never numerically scorable** | medium | signature + `outcomes` scoring only; sample counts always shown |
| R7 | Multi-panel figures ⇒ judge reads the wrong qubit | medium | per-target panel extraction is a P0e requirement with a test |
| R8 | Env confusion ⇒ verdict attributed to the wrong stack | medium | stamp env + `lib_versions` + `gate_hash` (D-13.3) |
| R9 | FSP interaction in `power_rabi` writes power silently | medium | route through the r12 never-silent path (`power_rows.py`) |
| R10 | Agent picks a class-A parameter that wastes the night | low | corpus-derived bounds + budget |
| R11 | Stack upgrade breaks a util import | low | re-run the P0 exit criterion after any upgrade |
| R12 | SM process must stay up all night (v1 limitation) | low | documented; notification on halt |

---

## 9. What we deliberately are NOT doing

- No LLM-authored calibration number reaching state without a tier-A/B verifier
  (D-1). **Tier C stays human-gated.**
- No re-implementation of any node's analysis — we **call** the lab's module.
- **No JSON-backed namespace shim to bypass `Quam.load`.** It would make replay
  env-independent (attractive: one env for all generations, faster, no quam
  import) but it re-implements an attribute surface that can silently diverge
  from quam, and it contradicts D-13 — the customer's env *is* the ground truth.
  Reconsider only if a future archive is loadable by **no** available env.
- No scoring against archived numbers across analysis generations (§4.5b).
- No synthetic-data scoring of the vision judge (circular). Manufactured
  wrong-fits are the one exception and measure **rejection** only, never acceptance.
- No qualibrate-runner REST transport (docs/56 §7 keeps the seam).
- No change to the deterministic gates' veto over an LLM accept (D-2).

---

## 10. Open questions

1. **What defines "done" beyond the PoC?** A real "bring up this chip" needs
   target values/tolerances (T1, readout fidelity…). SM has no chip-spec data
   model. Proposal on the table: derive plausible defaults from what this lab's
   own history reached, and let the user adjust. **Not agreed.**
2. The **90 % pre-flight baseline** is a starting number; re-set it once P3
   measures the real distribution (and it is per family, §D-7 caveat).
3. **Tier-B family membership** — computed in P0d, not yet known.
4. Which env should be the **default** for a newly-discovered archive when both
   are compatible.
5. Whether P6c's consistency review should be able to **trigger re-measurement**
   on its own, or only report.

---

## 11. Appendix — commands and invariants

```bash
# node-faithful replay of one run (the verifier that makes D-1 tier A possible)
<env>/python.exe quam_state_manager/generator/run_fit_audit.py \
    --run <run-folder> --util <util> --source-root <node-tree>

# canonical full gate (expect the documented Windows baseline)
PYTHONUTF8=1 conda run -n <env-new> python -m pytest tests/ -q \
    --timeout=600 --timeout-method=thread --deselect tests/test_main.py::TestWaitForServer
```

**Invariants to keep green while implementing:**
- a (step, target) is done only when gates **pass** AND the judge **accepts** (§1.3)
- an LLM `accept` never overrides a deterministic `fail`
- `writer.py` remains the only write path
- reverts stay compare-and-swap with `coerce=False`
- the reduced schema is generated per step, never hand-written
- axes are bound by dim NAME, never by position
- update targets are resolved from the run + chip state, never hardcoded (D-14)
- every verdict records its env + `lib_versions` + `gate_hash`
- no customer identifier enters a tracked file

---

## 12. Fast index (for a fresh context)

| I need… | go to |
|---|---|
| what we're building and why | §1 |
| the 9 families and their run counts | §2 |
| every design decision + rationale | §3 (D-1…D-14) |
| what was measured, and how | §4 |
| module map | §5 |
| the work, in order | §6 |
| how to land a change | §7 |
| what can go wrong | §8 |
| what we rejected and why | §9 |
| what's still undecided | §10 |

**Three things most likely to be forgotten and most costly:**
1. **§4.3 / R1** — transposing a figure without swapping its click contract
   writes the frequency into the flux field.
2. **D-14 / R2** — `families.py` hardcodes `x180`; this chip uses
   `x180_DragCosine`, and flux writes are routed by `qubit.z.flux_point`.
3. **§4.5b / R6** — the coupler families can never be numerically scored;
   signature and `outcomes` only.

---

## 13. P0 implementation record (2026-08-06) — SHIPPED

Branch `feat/runner-p0-foundation`. Everything below was measured on the real
archives; where a plan claim turned out incomplete it is corrected here rather
than edited above, so the reasoning history stays readable.

### 13.1 What shipped

| module | role |
|---|---|
| `generator/run_quam_load_probe.py` | env-side: can THIS interpreter `Quam.load` THIS run's state? Reports the missing module explicitly |
| `core/autofit/envmatrix.py` | classified compatibility probe + `choose_context`; version+content-keyed cache |
| `core/autofit/sourceroot.py` | **new axis** — read-only `git archive` materialization of a pinned analysis-tree revision |
| `core/autofit/corpus.py` | archive index → per-family observed parameter ranges (D-5) + sweep steps (tier-B input) |
| `core/autofit/figure_gen.py` + `generator/run_figure_gen.py` | lab-faithful figure regeneration, per-target panels, wrong-fit injection seam |
| `core/fit_audit.py` | `FAMILIES` 2 → 9, unit-aware drift text, honest missing-value verdict |
| `generator/run_fit_audit.py` | pair-shaped runs, folder-name fallback, `run_params` unwrap, byte-coord `.sel` |
| `tests/test_runner_p0.py` | 60+ synthetic pins for all of the above |

### 13.2 D-13 CORRECTED — compatibility is a TRIPLE, not a pair

The plan modelled it as **env × state generation**. Measured reality adds a
third axis: **the analysis tree's own revision**.

While P0 was being built, the source-of-truth tree's working copy gained a
`quam_config` import requiring quam ≥ 0.6. From that moment the older env could
not import `quam_config` from the LIVE tree *at all* — so every pre-0.6-era
archive (including the only coupler-flux data) became unreplayable, even though
nothing about those runs had changed. A **pinned revision of the same tree**
replays them bit-identically.

> **Binding: a verification context is (env, source root, run generation).**
> Every verdict records all three (`env`, `root_kind`/`root_rev`,
> `lib_versions`, `gate_hash`).

`sourceroot.py` owns the root axis. It uses `git archive` — no checkout, no
worktree registration, no index write — so the customer tree is never modified
(pinned by a test that snapshots the tree's entries before and after).
`candidates()` returns the live root (flagged `dirty` when it differs from
HEAD) followed by pinned revisions; `envmatrix.choose_context` probes
(env × root) in preference order.

**The generation split is bidirectional** (this too was not in the plan):

| run era | quam 0.5-era env | quam 0.6-era env |
|---|---|---|
| 2026-03 / 05 | ✅ (with a pinned tree) | ✗ `duration_control`→`duration_qubit`, unknown `grid_location`/`isolation` |
| 2026-07 | ✗ `Attribute isolation is not a valid attr` | ✅ |

**No single env covers the corpus.** Env selection is mandatory, not a nicety.

### 13.3 Classification buckets (envmatrix)

`ok` · `generation_mismatch` · **`tree_incompatible`** · `no_quam` ·
`no_quam_config` · `state_unreadable` · `error`.

Two rules learned the hard way:

* **Classify on the probe's `missing_module`, never on the traceback text.**
  The traceback always renders the source line `from quam_config import Quam`,
  so text-sniffing labelled *every* import failure `no_quam_config` and made
  `no_quam` unreachable.
* A **dotted** miss inside an installed package (`quam.components._waveform_tools`)
  is `tree_incompatible` — the tree wants a newer library than the env ships.
  Calling that "no quam" would send the user to reinstall what they already have.

Only deterministic buckets are cached; transient failures stay retryable. The
cache key is `env | env-versions | state-generation | quam_config-tree-hash` —
**the interpreter itself is in the key** because `quam_config` is not a probed
package, so equal versions do not imply equal loadability (`fit_audit`'s own
long-standing doctrine, which P0 initially failed to follow).

### 13.4 D-14 EXTENDED — target vocabularies differ per family

The plan said update targets must be run-derived. Measured: **the two coupler
families report different names for the same slot.**

| family | fit_results keyed by | cube coord | pair |
|---|---|---|---|
| `07 resonator_spectroscopy_vs_coupler_flux` | **pair** (`q0-1`) | pair | `q0-1` |
| `10 qubit_spectroscopy_vs_coupler_flux` | **measured qubit** (`q3`) | qubit | `q0-3` |

So a figure request must resolve a target against **all three** vocabularies
(pair name, cube label, measured-qubit name) — assuming one is how a judge ends
up grading another qubit's panel (R7).

Additionally, node 10's plotter draws on a **pair grid** and indexes the data by
pair name, while older archives of that same node stored the measured-qubit
name. `run_figure_gen._relabel_qubit` bridges exactly that (the same rename a
newer node performs at acquisition), and is a no-op when the archive already
uses pair names.

Pair identity itself always comes from the run's own record
(`data.parameters.model["qubit_pairs"]`, cross-checked against
`data.outcomes`) — never inferred from the machine: a qubit belongs to several
pairs (`q0 ∈ q0-1` and `q0-3`), and guessing mis-sizes the cube
(`AlignmentError: conflicting dimension sizes {1, 2}`).

### 13.5 Corpus (D-5 / tier-B input) — measured

2,841 runs indexed across the archives; 638 in the 9 scoped families.

| family | runs | median sweep step | observed `num_shots` |
|---|---|---|---|
| resonator_spectroscopy | 89 | freq 100 kHz | 10 – 1000 |
| resonator_spectroscopy_vs_power | 54 | freq 100 kHz · power 0.45 dB | 50 – 200 |
| resonator_spectroscopy_vs_flux | 26 | freq 100 kHz · flux 50 mV | 3 – 100 |
| resonator_spectroscopy_vs_coupler_flux | 11 | freq 100 kHz · flux 40 mV | 3 – 1000 |
| qubit_spectroscopy | 115 | freq 250 kHz | 87 – 500 |
| qubit_spectroscopy_vs_power | 21 | freq 250 kHz · power 0.92 dB | 10 – 100 |
| qubit_spectroscopy_vs_flux | 77 | freq 500 kHz · flux 10 mV | 10 – 300 |
| qubit_spectroscopy_vs_coupler_flux | 12 | freq 1 MHz · flux 30 mV | 10 – 100 |
| power_rabi | 233 | amp prefactor 0.005 | 1 – 200 |

Confirming D-5's warning: observed values leave the schema defaults far behind
(`num_shots` 3 vs a default of 100; flux ±2.5 V vs ±0.5 V). **Bounds must come
from the corpus, never from defaults.**

The flux vocabulary is not uniform either — three shapes exist
(`min/max_flux_offset_in_v`, `min_flux/max_flux`, a centered
`flux_offset_span_in_v`), so the step rule tries each. An axis a family's rule
cannot derive is reported with `n: 0` rather than dropped: a missing row would
read as "no grid" instead of "unknown".

The archive walk is **depth-bounded recursive**, not two-level: real archives
also nest as `root/<chip>/<date>/#N` (the reason the sidebar tree v2 exists,
docs/68), and a fixed two-level walk indexes such a root to a silent zero.

### 13.6 Invariants added by the adversarial review

A 5-lens review with per-finding adversarial verification confirmed 19 defects
in the P0 diff; all are fixed and pinned. The ones that changed a *contract*:

* **A missing comparable value is `unverifiable`, never `agrees`.** With 9
  families, a cross-generation field rename (archive `sweet_spot_frequency`,
  today's fitter `resonator_frequency`) made both sides claim success with no
  number to compare — and the old fall-through returned the strongest verdict on
  evidence we did not have, silencing exactly the drift the auditor exists to
  find.
* **A wrong-fit injection that cannot be applied RAISES.** Silently skipping it
  (misspelled variable, renamed field) hands the harness a pristine figure
  labelled "manufactured wrong fit" — the D-7 leniency number would then be
  measuring nothing. Overrides are also filtered to the panel being drawn, so a
  multi-target manufacture run no longer loses every figure.
* **One parameter unwrap** (`run_fit_audit.run_params`) shared by the replay,
  figure and corpus paths; a private copy would drift and the agent's bound
  table would describe values the fitter never saw.
* Failure envelopes carry every documented key (callers must not `KeyError` on
  the failure path); figure filenames include `fit_source` (a fidelity check
  regenerates the same run both ways); byte coord labels are honoured on
  NetCDF-classic archives.

### 13.7 Exit criterion

`for every family, ≥5 real runs replay AND regenerate a per-target figure,
under an automatically chosen (env, root)` — met. Under a single fixed env the
same sweep reaches only 20/45, which is the measurement that forced §13.2.

Fidelity spot-check: the same-generation qubit-spec anchor still replays
**bit-identically** (`5715387535.868236`) through every change in this batch,
and regenerated figures were compared side-by-side against the archived PNGs.

### 13.8 Open items carried into P1+

* The live tree is **actively edited** and its `quam_config` is moving to
  quam 0.6 only (confirmed intentional). Old-generation archives therefore
  depend on pinned revisions indefinitely; `migrate_state_to_quam06.py` exists
  in the tree and is the candidate steady-state alternative (would need the
  bit-identical invariant re-proven after migration).
* `gate_hash` churns while the tree is edited — verdicts must always be read
  together with their `root_rev`/`root_kind`.

---

## 14. P1 implementation record (2026-08-06) — SHIPPED

Branch `fix/interactive-flux-axes`. The plan called this the highest-risk edit
in the whole programme (R1) despite looking cosmetic. It was.

### 14.1 What shipped

| change | why |
|---|---|
| `recipes/resonator_2d.py::_vs_flux` (06 + 07) | x = flux, y = frequency; heatmap cube transposed; idle-offset marker `hline` → `vline`; contract axes swapped |
| `recipes/qubit_spec_vs_flux.py` (09) | same transpose + contract swap; flux target moved FIRST and a top-level `axis` declared (see §14.4) |
| `recipes/qubit_spec_vs_coupler_flux.py` (**new**, 10) | the node had NO recipe: it resolved to `fallback`, i.e. an empty Interactive menu — static PNG only, no click-to-apply |
| `registry.py` | registers the new recipe |
| `tests/test_interactive_axis_parity.py` (**new**) | the invariants the existing goldens structurally cannot express |

Value math (`scale` / `offset`) was deliberately **not** touched, so every
existing click golden stays valid — they pin the arithmetic, this batch pins the
geometry.

### 14.2 The gap that made R1 real

`tests/test_click_contracts.py` asserts `staged == scale*clicked + offset` and
**never reads a target's `axis`**. So a figure could be transposed while its
contracts still read the old axis, and every golden would stay green while a
click wrote the clicked FREQUENCY into the flux field. Worse, the pre-transpose
orientation was pinned in a second file (`test_interactive_plots.py`) which was
NOT updated by the first pass — the repo briefly asserted two mutually
exclusive contracts, and a future revert of the transpose would have been
"confirmed correct" by the stale pin. Both are fixed; the stale pin now carries
a comment saying why it must move with the figures.

### 14.3 Four things must move together

A transpose is not one edit. It is four, and any one left behind is silently
wrong:

1. the **axis titles**,
2. the **heatmap cube** (plotly `z` is `[y][x]`),
3. every **overlay and shape** (a flux marker becomes a `vline`; a ridge's x/y
   swap),
4. every **click-contract `axis`**.

`tests/test_interactive_axis_parity.py` pins all four, and each is
**mutation-proven**: reverting any single one turns the suite red.

| single-point revert | caught |
|---|---|
| axis titles only | ✅ 3 failures |
| heatmap cube only (09) | ✅ 3 |
| heatmap cube only (06/07) | ✅ 6 |
| contract axis only | ✅ 1 |
| dim-order guard neutered (10) | ✅ 1 |
| an `axis` key dropped from a target | ✅ 1 |

Two properties of the test made that possible, both learned from the review:

* The synthetic cube is **identity-valued** (`cell = second*1000 + det`), not
  random. With noise, a wrong dim-order guard and the deliberate transpose
  cancel in SHAPE while scrambling the mapping — the map renders mirrored and
  nothing notices. The check is **scale-invariant** (it compares per-axis step
  sizes, not values) because several recipes legitimately rescale V → mV.
* **Both** archive dim orders are exercised for every family. Pinning one order
  left half of every orientation guard as dead code — proven by neutering both
  guards and watching the suite stay green.

### 14.4 Adjacent defects the review surfaced

* **A missing `axis` is not "not applicable".** The client defaults it to `x`,
  so a 2-D target without an axis silently reads the wrong quantity. The test
  now FAILS on it instead of skipping (it had been skipping the
  highest-consequence leg).
* **An unavailable figure is not a valid skip.** The synthetic bundle is
  well-formed by construction, so a recipe refusing it is a defect. Skipping
  let a broken orientation guard hide behind an honest degrade — the dim-order
  mutation went from CAUGHT to MISSED until this became an assertion.
* **Axis→target equivalence is not identity.** A dBm axis legitimately writes an
  amplitude (`dbm_to_amp` / `dbm_gridfs`), so the coherence rule allows
  {power, amp} on a power axis. Without that the invariant would go red on
  correct code as soon as the fixture grew a realistic snapshot.
* **The toast read the wrong axis.** The client reports the clicked value from
  `clickable.axis` (defaulting to x) and labels it with `targets[0]`'s path.
  With frequency first, node 09 announced `x=0.0137 → …f_01` — the flux number
  under the frequency's name, exactly the confusion the transpose removes.
* **Node 10 hardening**: `menu()` advertised on `IQ_abs` while `build()` needs
  `full_freq`; duplicate targets (a star coupler layout reports the same
  measured qubit for every pair) collapsed several tiles onto slice 0; the fit
  index was taken positionally from the CUBE on the one family whose fit and
  cube vocabularies are documented to diverge (§13.4); a detuning-valued ridge
  was drawn as absolute when the carrier could not be computed. All fixed —
  the ridge now draws nothing rather than claiming the qubit sits at DC.

### 14.5 Verification

* axis suite **32 passed** (8 skips, all deliberate)
* interactive + contract + P0 suites together: **174 passed**
* **real-data click round-trip** on three archived node-06/09 runs: every target
  reproduces the node's own patch, and — the part a self-consistent test cannot
  fake — each clicked value lands INSIDE the range of the axis it declares
  (flux in [−2.5, 2.5] V, frequency in [7.067, 7.077] GHz). Under the old
  mapping the flux value would have had to fall in the frequency range.
* side-by-side against the archived lab PNGs: same arch, same orientation, same
  ranges; node 10 matches its lab panel.

---

## 15. P2 implementation record (2026-08-06) — SHIPPED

Branch `feat/runner-p2-families`. P2 is the physics layer: the nine families of
the x180 chain get real gates, real update grammar, and — for the first time —
a history gate that is actually connected.

### 15.1 The method: bands are MEASURED, never invented

Every number added in this phase came from replaying real archived runs through
the lab's own analysis and splitting the results by the node's OWN verdict:

* harvest: 119 runs across the 9 families (`--per-family 14`, spread evenly over
  every root and date so no single campaign dominates), replayed via the P0
  env × pinned-revision matrix;
* for each candidate field, compare the node-ACCEPTED distribution against the
  node-REJECTED one;
* a floor is only worth having if it sits **below the accepted minimum** and
  still separates. Anything else buys false-rejects.

The verdict is the **accuracy ledger** — gates vs the node's own success flag
over 276 accepted and 115 rejected targets:

| family | accepted | FALSE REJECTS | rejected | caught |
|---|---:|---:|---:|---:|
| resonator_spectroscopy | 23 | **0** | 7 | 7 |
| resonator_spectroscopy_vs_power | 31 | **0** | 7 | 7 |
| resonator_spectroscopy_vs_flux | 40 | **0** | 42 | 25 |
| resonator_spectroscopy_vs_coupler_flux | 15 | **0** | 0 | — |
| qubit_spectroscopy | 34 | **0** | 15 | 14 |
| qubit_spectroscopy_vs_power | 45 | **0** | 2 | 0 |
| qubit_spectroscopy_vs_flux | 30 | **0** | 17 | 0 |
| qubit_spectroscopy_vs_coupler_flux | 3 | **0** | 14 | 14 |
| power_rabi | 55 | **0** | 8 | 8 |

**0 false rejects, 75/115 rejects caught deterministically.** The uncaught 40
are the honest scope of the vision round (P3) — they are not a gap to be closed
by inventing a tighter band, and §15.3 says exactly why for each family.

### 15.2 What the corpus overturned in ALREADY-SHIPPED code

Sixteen production false-rejects existed before this phase — gates written from
physical intuition that the real data contradicts:

| family | shipped band | what the corpus says | fix |
|---|---|---|---|
| `qubit_spectroscopy` | `r2 ≥ 0.75` | the node ACCEPTED r² down to **0.452**; 12 of 34 good fits would have been flagged | `peak_snr ≥ 5.0` leads (accepted [6.6, 24.3], all 15 rejects below, median 3.0); r² drops to 0.30 as a garbage backstop |
| `power_rabi` | `prefactor ∈ [0.5, 2.0]`, a HARD G4 fail | 4 of 55 accepted fits sit outside it — real bring-up prefactors run down to 0.20 | envelope widened to [0.05, 5.0]; detection moves to `multipulse_fit_quality ≥ 0.30` (accepted [0.37, 0.82], rejects median 0.12) |
| `qubit_spectroscopy_vs_flux` | (proposed) `vertex_extrapolated` check | the node's own analysis treats it as a WARNING and accepts anyway — one accepted target carries it | check dropped before it ever shipped |

The lesson generalizes: **a gate is a claim about the lab's data, so it needs the
lab's data as evidence.** The `_R2_FLOOR` that was safe for resonator
spectroscopy (accepted floor 0.82) was catastrophic one family over.

### 15.2b The jump limits — measured separately, and 37 more false alarms

The accuracy ledger covers bands, metric gates and consistency checks, but a
`max_abs_jump` needs a **pre-update anchor**, which no fit dict carries. That
anchor is sitting in every run's own `node.json`: `patches[].old` IS the
pre-update state and `patches[].value` IS the claim — so every accepted patch is
a jump the node itself was happy with. No replay needed.

| family / anchor | limit | accepted moves | largest accepted | over the limit |
|---|---:|---:|---:|---:|
| resonator_spectroscopy / frequency | 50 MHz | 122 | 30.5 MHz | 0 |
| resonator_spectroscopy_vs_power / resonator_frequency | 50 MHz | 27 | 4.5 MHz | 0 |
| qubit_spectroscopy / frequency | ~~100~~ → **200 MHz** | 114 | **139 MHz** | ~~2~~ → 0 |
| qubit_spectroscopy_vs_power / frequency | ~~100~~ → **200 MHz** | 29 | 47 MHz | 0 |
| qubit_spectroscopy_vs_flux / qubit_frequency | 500 MHz | 47 | 31 MHz | 0 |
| power_rabi / opt_amp | ~~0.25~~ → **0.8** | 1004 | **0.538** | ~~35~~ → 0 |

Two findings, and the second is the important one:

1. the old limits fired on **37 moves the nodes themselves accepted** — 3.5% of
   every power-rabi write, which in `review` autonomy means one in thirty
   perfectly good calibrations queued for a human;
2. **not one node-rejected target emits a patch at all** (the nodes skip failed
   targets), so on this corpus a jump limit has *zero measured detection value*.
   Its entire measured effect was false alarms.

That settles what a jump limit is FOR: a sanity envelope against an absurd step,
not drift detection. Real drift detection is G5's job — which is only true now
that G5 is actually wired (§15.5). `qubit_spectroscopy_vs_power` inherits node
08's wider measurement rather than its own thin 29-sample maximum, because it
writes the same physical quantity in the same bring-up regime.

Totals after the recalibration: **0 false rejects across bands, metric gates,
consistency checks AND jump limits.**

### 15.3 The four new families, and their honest coverage

| node | family | what it can catch | what it CANNOT |
|---|---|---|---|
| 06 res-vs-flux | `resonator_spectroscopy_vs_flux` | `ridge_amp_snr ≥ 2.5` (27/42), `ridge_coverage ≥ 0.55` (17 more), `ridge_r2 ≥ 0.40` | a vertex read off a noise ridge — inside every band |
| 07 res-vs-coupler-flux | `resonator_spectroscopy_vs_coupler_flux` | physical envelope + signal presence | everything else: the corpus has **no rejected side at all** (15/15 accepted), so there is nothing to calibrate a metric against. Declaring one would be guessing. |
| 09 qubit-vs-flux | `qubit_spectroscopy_vs_flux` | 500 MHz jump limit, swept-range guard, presence | the fitted vertex: **no numeric field separates** accepted from rejected — every reject is non-finite, i.e. the node's own gate is finiteness. A fabricated metric would add false-rejects without adding detection. |
| 10 qubit-vs-coupler-flux | `qubit_spectroscopy_vs_coupler_flux` | `num_crossings ≥ 1` — **perfect separation** (3/3 accepted found exactly one crossing, 14/14 rejects found zero) | — |

Both COUPLER families are **verify-only** (`updates=[]`): node 07's
`update_state` is an empty stub and node 10 writes only a bookkeeping `extras`
key. Inventing a write target from the figure's axis is precisely the D-1 trap
("the figure axis is not the state value").

### 15.4 The update grammar is run-derived (D-14), and the families disagree

Two families read the SAME fit key and write it DIFFERENTLY. Any attempt to
share one update rule corrupts one of them:

| | node 06 | node 09 |
|---|---|---|
| `flux_point == "independent"` | assign `z.independent_offset` | assign `z.independent_offset` |
| `flux_point == "joint"` | assign `z.joint_offset` | **`+=`** `z.joint_offset` |
| anything else | **else-branch** → joint | **writes nothing** (its if/elif has no else) |
| frequencies | `+= frequency_shift` | absolute assign |
| pre-condition | — | offset must lie inside the swept span |

So `UpdateSpec` gained `op` (`assign` / `add_to_current` /
`subtract_from_current` / `assign_ceil4`), `route_on` / `route_when` (with `"*"`
as an explicit else that fires only when no exact branch did), and `guard`.
`power_rabi`'s path became `…xy.operations.{operation}.amplitude` — this chip's
pulse is `x180_DragCosine`, so the old hardcoded `x180` addressed a field that
**does not exist on it**; with no `operation` in the run's parameters the row is
SKIPPED, never guessed.

`FIT_TARGET_MAP` parity is preserved by resolving the placeholder before
comparing, not by reverting to a literal.

### 15.4b Two more things the nodes do that we did not

Reading the real `node.json` patches (no replay needed — `patches[].old/value`
IS the node's own write) turned up two writes the registry was missing entirely.

**(a) The run names an ALIAS; the node writes its target.** Every chip in the
corpus carries

```
operations.x180           = "#./x180_DragCosine"     ← what the run parameter names
operations.x180_DragCosine = {amplitude: …}          ← what the node patches
```

485 real patches address `x180_DragCosine`; **zero** address `x180`. So filling
`{operation}` from the run parameters — P2c's fix — was still one hop short:
the write would have landed on a path whose value is a *pointer string*.
`families.resolve_alias_path` now follows `#/`, `#./` and `#../` aliases with
the same frame `pointer_resolver` uses, **verifying every hop by reading the
result** and refusing (no row) when it can't. A segment the reader simply cannot
answer is left alone — a non-existent path fails loudly at the transactional
write, whereas a silent rewrite would not.

Verified against three labs' real chips: the resolved paths equal the nodes' own
patch paths **exactly**.

**(b) Power Rabi writes π/2 as well.** `update_x90` is true in all 222 real
runs, and the π/2 amplitude is bit-exactly half the π amplitude in 477 of 479
patch pairs (the 2 outliers ±1.5%; 6 more pairs are stored as TEXT — the r14
class). Without that row every x90 gate would sit stale behind a freshly
calibrated x180. `UpdateSpec` gained `factor` for exactly this node-applied
ratio.

**(c) And the FSP coupling was half-wired.** `power_rows.py` already builds the
rvp node's ATOMIC update (frequency + readout amp + the SHARED port FSP +
power-preserving sibling rescales), but only the diagnose ROUTE called it — the
engine's own `_forward_rows` wrote the frequency half alone. In `full` autonomy
that is a silent partial write that de-couples the readout power calibration.
The engine now builds the coupled rows too, through a `merged_view()` on the
Writer protocol (the feedline port resolves through the wiring pointer chain, so
`state` alone would make every port lookup quietly refuse), and any refusal or
DAC-clip warning is written to the ledger rather than swallowed.

### 15.5 G5 was dead — and wrong

`gates.py` implemented the history-drift gate and `engine.py` accepted a
provider, but **`web/routes.py` never passed one**, so in production G5 never
ran. Wiring it exposed a second defect that could only exist because nothing
had ever exercised the code path: the gate compared **`val`, the loop variable
left over from the metric-gate pass** — so it measured the last metric (an r² of
0.99) against a flux-offset trend and reported a 614-σ drift on a clean run.

Fixed together:

* G5 reads `entry[fam.value_key]` explicitly;
* `families.trend_path_for` resolves the anchor the SAME way `resolve_updates`
  resolves the write — routed families take the branch that will actually fire,
  because independent and joint offsets carry genuinely different histories and
  comparing against the wrong one manufactures drift;
* with no state reader the else-branch is NOT assumed (we cannot tell "else"
  from "unknown"), and verify-only families answer `None` — no writable home,
  no trend;
* the provider is handed the whole `{target: dot_path}` map at once and answers
  from `HistoryManager.column_history` — **one snapshot pass per run**, not one
  per qubit (17 qubits × 60 snapshots of direct parsing per step would have made
  the gate a performance bug instead of a safety net);
* any failure returns empty and the gate abstains. A history hiccup must never
  manufacture a verdict.

What it buys, measured: the 06 `drift` corruption (a 0.4 V offset step) is
inside every physical band and leaves the ridge metrics intact — a documented
blind spot **without** history, a `suspect` **with** it.

### 15.6 The sim corpus grew to the whole chain

`synth.py` gained generators for 05 / 06 / 07 / 08b / 09 / 10, so all nine
families now run hardware-free through the real gates, the real writer and the
real engine. Design notes worth keeping:

* the flux arch is modelled as the **parabolic approximation** around the sweet
  spot, not the full `sqrt|cos|` — over a realistic ±0.25 V window they agree to
  <1%, while the arch would sweep the qubit 800 MHz out of any real span;
* the sim now emits the fields the shipped gates actually read (`peak_snr`,
  `dip_snr`, `multipulse_fit_quality`, `prefactor_extrapolated`,
  `pi_amp_reachable`, the ridge metrics). Omitting them left the real gates
  abstaining against sim data — the sim is supposed to be indistinguishable to
  every SM reader, and **gates are readers**;
* `peak_snr` / `dip_snr` are computed from the SAME trace the gate reads
  (prominence over the point-noise floor), not fabricated per corruption mode;
* a run must **name its own pulse** in `parameters.operation`. It didn't, and
  the moment power_rabi's path became `{operation}`-templated the sim's
  power-rabi step resolved **zero** write rows — the loop's single most
  important write silently stopped being exercised while every engine test
  stayed green (they assert on other quantities). Now pinned by a test that
  resolves the update from the run's own node.json.

### 15.7 Ledger cells that MOVED (never silently)

`tests/test_autofit_gates.py` says tightening is progress and downgrading is a
regression, so each move is stated with its evidence:

| cell | was | now | why |
|---|---|---|---|
| `08_qubit_spectroscopy` / noisy | not_pass | **pass_allowed** | the catch came from `r2 ≥ 0.75`, which the corpus proved rejects 12/34 node-accepted fits. The synthetic claim lands within fwhm/6 of truth — a GOOD value with an ugly fit. Rejecting it is a production false-reject, not a catch. |
| `11_power_rabi` / wrong_peak | fail | **not_pass** | the hard prefactor band was false-rejecting; a locked harmonic is now a suspect via `multipulse_fit_quality`, and a deterministic FAIL is reserved for physically impossible values. |
| `11_power_rabi` / noisy | pass_allowed | **not_pass** | tightened — the family now has a quality metric, and a noisy multipulse sweep lands on the corpus's reject side. |
| `11_power_rabi` / out_of_band | fail | fail | unchanged verdict, re-based corruption: "out of band" must now mean an amplitude the port cannot play (×8), because ×3 sits INSIDE the corpus-widened envelope. |

### 15.8 Verification

* `tests/test_runner_p2.py` (**new**, 43 tests): family registration + archive
  node-name resolution, the corpus-derived floors pinned as numbers, routed
  updates (06 else-branch vs 09 no-else, `+=` vs assign, span guard, opt-in
  `min_offset`, `{operation}` skip), the trend anchor (routed / no-reader /
  verify-only / `{operation}`), G5 catching what the other gates cannot and
  NOT flagging a value on its own trend, an end-to-end engine run proving the
  provider receives resolved dot-paths and its answer reaches the ledger, and
  alias following (all three pointer forms, the x90 half-row, refusal on an
  unresolvable alias), power coupling (the engine builds the coupled rows and
  ledgers the refusal; only the rvp family is coupled), and sim-fidelity checks
  (every gated field present; the run names its operation; the flux cube really
  carries a parabolic ridge whose fitted vertex is the sweet spot).
* `tests/test_autofit_gates.py`: ledger extended from 10 to 16 node × mode
  rows — **146 passed**.
* autofit + fit-audit + P0 suites together: **273 passed, 9 skipped**.
* real-archive accuracy ledger re-run after every change: **0 false rejects**.

### 15.9 Carried into P3

* the 40 uncaught rejects are the vision round's brief — with the per-family
  reasons in §15.3, not as an undifferentiated "AI looks at it";
* `qubit_spectroscopy_vs_power` (the #575 class: a self-consistent noise fit
  that a replay AGREES with) remains the canonical case that deterministic
  gates provably cannot close;
* `resonator_spectroscopy_vs_coupler_flux` has no rejected side in the corpus
  at all — its false-accept coverage is UNMEASURED, and that must be stated in
  any report the loop produces, not silently rendered as "passed".
