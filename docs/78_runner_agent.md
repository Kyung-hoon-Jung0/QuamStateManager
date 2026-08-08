# 78 — Runner + AI calibration agent (PLAN, final)

> Status (2026-08-07): **P0–P8 SHIPPED except P3c/P3d (needs an API key) and P9 (needs hardware).**
> Records: §13 (P0) · §14 (P1) · §15 (P2) · §16 (P3a/P3b) · **§17 (audit —
> read this before trusting any earlier section)** · §18 (two-stage looking +
> the stage-1 pilot) · §19 (P4–P8). A stack of branches off
> `origin/main @ 8e5fa99` on `feat/runner-p3-judge`; not merged to main.
> **The loop closes** (§19.1): a target is done only when the gates pass AND
> the judge signs off on its own panel — or, with no judge configured, on the
> gates alone and STAMPED as not vision-verified.
> **§17 B3 is CLOSED** (§21.1 — every verdict now stamps its verification
> context, and the cross-run review refuses to compare across contexts), and so
> is §17.6 (§21.2 — `power_rabi`'s wide check, where the corpus refuted the
> obvious generalization). §22 records a six-way audit of every remaining
> un-measured constant and the first real two-sided run of the signature ask:
> **leniency 0/12, and the stinginess bar is not measurable against a scalar
> success flag.** Still open: the items listed in §22.4, and P9.
> Design discussion + investigation: 2026-08-05.
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
| **reserved** | `simulate`, `timeout`, `load_data_id`, targets keys | ⚠️ **FALSE as written — see §17.6.** Only `simulate` + the targets keys are blocked (`node_inject.RESERVED_OVERRIDE_KEYS`). **`load_data_id` is NOT**, and it is the dangerous one: it makes a node replay archived data instead of measuring. Blocking it is P4's first item. |

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

*(As surveyed 2026-08-05, before P0–P3. The ✅ rows were closed by the phases
named; the ❌ rows are still true today — re-verified 2026-08-07, §17.)*

| | status |
|---|---|
| plan-level **step cap** | ❌ **still absent** — the work queue is a `deque` runtime rungs `appendleft` into, with no global bound |
| plan-level **wall clock** | ❌ **still absent** (docs/56 §2e claims both — doc/code drift) |
| notifications (email / webhook / browser) | ❌ **still absent entirely** |
| `fit_audit.FAMILIES` | ✅ closed by P0c — 9 entries |
| `families.py` coverage | ✅ closed by P2a/P2b/P2c — all 9 registered, `power_rabi` gated on `multipulse_fit_quality` with a ladder, update path run-derived |
| G5 history-drift gate | ✅ closed by P2d — `routes.py` passes `history_points_of` |
| cross-experiment consistency review (goal §1.1 #3) | ❌ **still absent entirely** (P6c) |
| per-target figure for the judge (D-11.1) | ❌ **still absent** — the whole multi-target sheet is sent per target (§17 B2) |
| the §1.3 terminator in the loop | ❌ **implemented, no caller** (§17 B1) |

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

---

## 16. P3a/P3b implementation record (2026-08-07) — SHIPPED

Branch `feat/runner-p3-judge`. P3a is what the judge KNOWS; P3b is what it may
SAY. The judge's calibration (P3c/P3d) is measured, not designed, and needs a
real provider — see §16.6.

### 16.1 The pack is authored from real figures, per family

`quam_state_manager/core/autofit/judge_packs/v1/<family>.json` — nine entries,
each written by reading the **real archived PNGs** of runs the node itself
accepted and (where any exist) rejected, across two chips' archives. Each entry
carries `axes` · `correct_signature` (3–7 bullets) · `failure_appearance` per
mode · `abstain_when` · `localizer` · `notes`.

Versioned data, not code: a lab edits a JSON file. `judge_pack.prompt_block()`
renders one family's entry into the prompt; an unknown family renders **nothing**
and the judge leans on abstain — honest, because we have taught it nothing about
that node.

### 16.2 Clause B is enforced at LOAD, not just reviewed

docs/47 Clause B: a feature's position inside the sweep window is an artefact of
the window the experimenter chose, not physics. An exemplar that says "the peak
sits near the middle of the window" doesn't merely fail to help — it transfers a
falsehood to the next chip, whose window is centred elsewhere.

So `lint_entry` runs at load and violating strings are **dropped and logged**,
never handed to the judge. A hand-edited pack can degrade the judge's knowledge;
it cannot teach it a window-dependent lie.

The lint refuses four shapes: a quantity with a physical unit, a
position-in-window or fractional-of-axis claim, a path/run-id, and — added after
the critic pass — **a feature sized against the swept window**.

That last one is the finding worth keeping. Five violations in the first-draft
pack were written entirely in words, with no digit anywhere:

> "the band is many times **narrower than the swept frequency range**"
> "excursions that are a small **fraction of the plotted frequency window**"
> "a few pixels wide and far **narrower than the swept frequency span**"

No number/unit check can see any of these, and each one means the identical
physics, zoomed in, scores differently. Sizing against the **feature** is the
correct form and must pass — "a fraction of the notch's own width", "narrower
than the visible hump" — so the rule keys on the window noun, not on "fraction".
`part of the sweep` is also allowed: that is a COVERAGE statement, and coverage
is itself one of the deterministic gates.

Shipped pack: **0 violations**, pinned by a test.

### 16.3 What the cross-family critic caught that a per-family author could not

Nine independent authors produce nine locally-sensible entries that contradict
each other. The findings, all fixed:

| # | fault | why it matters |
|---|---|---|
| D1 | one family declared **dark = resonance** as fact; two siblings say polarity must be read off the panel | a colour-inverted panel would read as broken |
| D2 | **extremum at the sweep edge**: a failure in one family, an abstain in two others | identical geometry, three verdicts — and the failure version scored position-in-window |
| D3 | qubit spectroscopy hard-coded the peak as pointing **up**, against three siblings that call the sign a readout convention | a sign-flipped panel reads as suspicious |
| D4 | the three-band drive structure asserted unconditionally in one vs_power family, abstained on in the other | a partial drive range routes to reject in one and abstain in the other |
| E1 | power-rabi bullets 1–6 written unscoped, only bullet 7 marked "map variant" | a judge shown the 2-D map looks for a single sine, which a correct error-amplification map does not contain |
| E2 | the map's convergence identified by "widest fringe spacing" | not the discriminator — the convergence is *which fringe stays vertical as pulse count grows* |
| F1 | a family with no rejected runs opened all four failure descriptions with "**Observed:**" | reads as witnessed; it was inferred from update-less panels inside accepted runs |
| F2 | a bullet demanding contrast "several times the cell-to-cell scatter" | would reject that family's ONLY known-good exemplar, described in its own notes as the floor of acceptability |

F2 is the same disease as P2's shipped bands: **a criterion written from
intuition that the lab's own accepted data contradicts.** It is worth stating
that the judge's exemplars are exactly as falsifiable as the gates' numbers.

### 16.4 Power Rabi: what the archive actually ships

docs/78 P3a asks for the 2-D error-amplification signature. The archived figure
is usually **per-qubit 1-D** (drive amplitude vs readout, one panel per qubit),
with the pulse-count map as a variant. The entry therefore carries **both**, and
says which bullets apply to which — the sine bullets are explicitly scoped to
the 1-D layout, and the map is judged solely by fringe convergence: one fringe
vertical at the same amplitude from the lowest pulse-count row to the highest,
the fringes either side bending toward it and crowding as pulse count grows.

### 16.5 P3b — two more asks, both structurally number-free

| ask | schema | unavailable/unparseable ⇒ |
|---|---|---|
| `signature` (the §1.3 terminator) | `{signature: clear\|unclear\|absent, failure_mode, reason}` | **`unclear`** |
| `compare` (D-8 tier 2b) | `{comparison: better\|worse\|same, reason}` | **`same`** |

Design points:

* the signature verdict is a **separate field** from the trust verdict. "The fit
  is consistent with the data" and "this experiment worked" are different
  questions; a loop that conflated them could terminate on a self-consistent fit
  of noise;
* the signature bundle carries **no fit numbers at all** — handing over the
  claimed value invites reasoning backwards from it;
* the defaults are the safe ones and this is load-bearing: an unavailable judge
  that answered `clear` would let the loop terminate because nobody looked, and
  one that answered `better`/`worse` would respectively keep a hopeless target
  running or trip the stop-loss on a good run. Budget exhaustion answers the
  same way;
* comparison images are ordered **previous, then current**, and a dropped image
  shortens the list rather than silently shifting the pair — a re-order inverts
  the verdict;
* the numeric-emission guard is now **one implementation** (`_numeric_emission`)
  shared by all three asks, rather than three copies free to drift.

### 16.6 Not done, and why

**P3c/P3d — the two-sided calibration (D-7) — is unmeasured.** There is no API
key in this environment, so no accept-rate on known-good figures and no
reject-rate on manufactured wrong-fit figures exists. Until it does, the D-7
pre-flight gate cannot be evaluated and the decision point ("does Sonnet clear
both bars per family?") cannot be reached. Everything that must hold *before* a
model is called is shipped and pinned; the numbers are not, and nothing in the
code pretends otherwise.

Note for whoever runs it: the wrong-fit set is buildable today — P0's
`figure_gen` regenerates a run's figure through the lab's own plotting module
with an injected wrong fit, which is exactly what docs/47 asked for and could
not build.

### 16.7 Packaging

The pack is DATA inside the package, and left out of the wheel it fails
**silently**: `load_pack()` returns `{}` and the judge rules on figures it was
taught nothing about, with no error anywhere. Added to `MANIFEST.in` and
`package-data`, and pinned by a test.

### 16.8 Verification

* `tests/test_runner_p3.py` (**new**, 56 tests): the Clause-B lint in both
  directions (violations caught / relative-geometry and coverage language
  preserved), the shipped pack clean, every scoped family present, the 2-D map
  families declaring no localizer, power-rabi carrying the map signature with
  the sine bullets scoped, a hand-edited violation dropped rather than taught,
  both new schemas incl. every unusable-reply path, the safe defaults, budget
  exhaustion, image order, the shared numeric guard, packaging, and the
  identifier-shape leak scan.
* autofit + P2 suites re-run green after the auditor changes (200 passed).

---

## 17. Audit of the plan against the shipped code (2026-08-07)

Six independent auditors read docs/78 §1–§16 against the code at `8ceee44` and
a consolidator re-verified every claim at its cited line. **This section
overrides any earlier section it contradicts.** What it found splits three ways:
defects (fixed here), plan staleness (fixed here), and *phase-ownership gaps* —
work nobody owned, which is the part a plan is actually for.

### 17.1 The document lied about its own status

The header read **"PLAN ONLY — nothing implemented"** while §13–§16 recorded
four shipped phases, and §12 routes a fresh context to §1 — straight past the
records. A design document whose first line is false is worse than no document:
it is the one line that gets believed. Fixed, and the header now names what
does NOT work as well as what does.

Same class, §4.7 ("What does NOT exist"): four of seven rows had been closed by
P0/P2 and still read as gaps. Now marked ✅/❌ with the closing phase, and the
two new ❌ rows below added.

### 17.2 Defects found and fixed in this pass

**D1 — G5's trend anchor was frame-correct only for `assign` families.**
`ramsey` writes `f_01 -= freq_offset`: the FIELD holds ~5 GHz while the FIT KEY
is a ~MHz offset. `trend_path_for` returned the written path, and G5 then
compared the offset against the f_01 series — reproduced on a clean synthetic
run as *"freq_offset=2.998e+06 is 449,605 robust-σ off its own history (median
5.002e+09)"*. Ramsey is step 6 of the shipped `1q_bringup` preset and
`_autofit_start_real` supplies the provider, so this was live.

This is **§15.5's defect one layer up**: that fix made G5 read the right
*value*, and it still read the wrong *series*. `trend_path_for` now returns
`None` unless the anchoring write is a plain `assign` with no factor — an
offset or a scaled write has no honest history to compare against, so the gate
abstains rather than invent one. The lesson generalizes: **fixing a symptom in
the consumer does not fix the contract.**

**D2 — the sim could not run six of the nine families.**
`simbackend.FAMILY_TO_NODE` never gained P2's six, so a family-keyed step for
any of them returned `status="skipped"` while the plan still reported `done`.
`synth.GENERATORS` had all nine — it was pure wiring, missed because the P2
tests drive the generators directly. The demo path that is supposed to exercise
the whole x180 chain hardware-free could not touch the families P2 was built
for. Fixed.

**D3 — three families could be flagged `wrong_peak` with no rung to answer it.**
G2 emits `wrong_peak` for *every* consistency-check hit, and `power_rabi`
declares three. With no matching adaptation `can_retry` is False, so the target
**defers instead of re-measuring** — and §15.7 re-based `power_rabi/wrong_peak`
from fail to suspect *on the assumption that a rung existed*. Added, each from
the node's own knobs: power rabi halves the prefactor window and step about the
parked amplitude (a harmonic outside the tightened window cannot be locked);
the two resonator families take their existing signal / refine ladders.
`chevron_11_02` has the same shape and is deliberately left — it is outside the
nine-family scope and we have no corpus evidence for a CZ rung.

**D4 — a blank `model` silently ran the model D-10 rejected.**
`_DEFAULTS["model"] = ""` and the anthropic call fell back to Haiku — which
docs/47 measured at ~17% false-accept, concentrated in the hard 2-D families,
which are 7 of our 9. D-10's "no code change needed" was true only if the
operator also typed a model name. The fallback is now `DEFAULT_ANTHROPIC_MODEL
= "claude-sonnet-5"`, i.e. the recorded decision holds by default.

### 17.3 Open defects — NOT fixed here, and why

**B1 — the §1.3 terminator has no caller.** `Auditor.signature()`,
`build_signature_bundle`, `parse_signature` and `SignatureVerdict` are reachable
only from tests. The engine consults the auditor in exactly two places, both on
the *failure* side (`suspect` → judge, node-failed → presence); `_decide`'s
success branch closes a target with no vision judgment at all. A 7-step × 2-target
sim chain finishes `status: done, llm_calls: 0`.

Not fixed here because it is a **policy** decision, not a wiring one: with no
provider configured the judge answers `unclear` by design, so switching the
termination rule on today would stop every target from ever completing. The rule
needs its unavailable-judge branch decided (gates-only termination with an
honest ledger note, versus refusing to run at all), and that belongs to the
phase that owns the loop. **§17.5 gives it an owner.**

**B2 — the judge is shown the whole multi-target sheet, once per target.**
`_first_figure(run)` globs `figures.*.png` and returns the first, inside the
per-target loop. D-11.1 calls per-target panels *"correctness, not polish"* and
R7's stated mitigation was "a P0e requirement with a test" — neither the
extraction nor the test exists, and P0's record does not list it as outstanding.
`figure_gen.generate(..., targets=[...])` produces exactly the right artifact
and has **zero consumers** outside its own module.

This is now the **top blocker for P3c**: calibrating a judge on whole sheets
measures the wrong thing, and a 90% bar cleared that way means nothing. In the
sim it is worse than a wrong panel — `synth.py` plots every target on one axes,
so there is no panel to extract; the sim's plotting must go per-target too.

**B3 — a verdict does not record the context that produced it.** §13.2 is
binding: *"a verification context is (env, source root, run generation); every
verdict records all three."* `fit_audit.audit_run` returns `gate_hash` and
`lib_versions` but no `env` and no `root_kind`/`root_rev`; `audit_run_cached`
puts them in the cache KEY and hands the payload back unlabelled. So two
verdicts from different analysis revisions are indistinguishable downstream —
which is precisely what D-13 was written to prevent. `figure_gen` does carry all
four, so the two paths disagree.

### 17.4 Plan gaps — work nobody owned

| gap | now owned by |
|---|---|
| wiring the §1.3 terminator, incl. the unavailable-judge branch | **P6a** (was: nobody; P3b owned only the *ask*) |
| per-target panel extraction + its test (D-11.2), and per-target sim plotting | **P3c-0**, a prerequisite of calibration (was: P0e, silently unbuilt) |
| stamping `env` / `root_kind` / `root_rev` on every verdict | **P3c-0** (was: asserted as done in §13.2) |
| judge-pack maintenance: who re-authors an entry when a lab's plotting changes | **P6d** (new) |
| re-measuring the accuracy ledger when a lab's nodes change | **P6d** (new) |
| the two families with NO node-rejected runs in the corpus | tracked as **R11** below |

**R11 (new risk).** `resonator_spectroscopy_vs_coupler_flux` has zero rejected
runs in the corpus and `qubit_spectroscopy_vs_coupler_flux` has three accepted
targets. Their false-accept coverage is **unmeasured, not good**, and any report
that renders them as "passed" without the sample count is misleading. The
pack entries say so in their own notes; the calibration report must too.

### 17.5 The shortest honest path to the one-button loop

1. **P3c-0 (new, blocks everything downstream)** — per-target figures: teach
   `synth.py` to plot per target, wire `figure_gen`'s per-target extraction into
   the engine, add the D-11.2 fidelity test, and stamp env/root on verdicts.
   Without this, every judgment and every calibration number is about the wrong
   picture.
2. **P3c/P3d** — the two-sided calibration, per family with sample counts,
   thin families visibly thin (R11). Needs the operator's key.
3. **P6a** — the terminator: gates PASS **and** signature `clear`, with the
   unavailable-judge branch decided and recorded in the ledger either way.
4. **P5a tier 1** — the plan step cap and wall clock (§4.7's two oldest ❌
   rows); without them a non-terminating loop has nothing to stop it, and step 3
   is exactly what makes non-termination possible.
5. Then P4 (action space), P5b–d, P6b/P6c, P7, P8.

Steps 1 and 4 are the ones a demo will skip and a night run will not survive.

### 17.6 Also corrected in the plan text

* **D-3's "reserved keys" row was wrong.** It claimed `simulate`, `timeout`,
  `load_data_id` and the targets keys are "already blocked by the scheduler".
  `node_inject.RESERVED_OVERRIDE_KEYS` is `("simulate", "qubits",
  "qubit_pairs", "targets")` — **`load_data_id` is not blocked**, and it is the
  one that matters: a node given `load_data_id` replays archived data instead of
  measuring, so an agent could "calibrate" a chip without touching hardware.
  P4's action space is planned on top of that false assertion; D-3 now says so,
  and blocking it is P4's first item.
* §2's "out of scope: the `…_vs_power_iq` variant" is stale — `fit_audit` and
  `families` both alias it, deliberately, or every such run silently drops from
  the backlog.
* P5b no longer claims the pairwise-vision comparator as future work: D-8 tier
  2b shipped in P3b; only the deterministic metric-trend half (tier 2a) remains.
* P2b's "no `verify_wide`" for `power_rabi` is still true and stays open.

---

## 18. Two-stage looking, and testing the judge without a key (2026-08-07)

Two user decisions, both of which changed the design for the better.

### 18.1 The multi-panel problem, solved by changing the question

§17 B2: a real chip's figure is ONE sheet of N panels, and the judge was being
handed that sheet once per target and asked "is qubit 5 good?". D-11.1 calls
that a correctness bug, and the obvious fix — always send a single-panel
figure — turns out to be **unaffordable**: 17 qubits × one step = 17 judge
calls against a plan budget of 40, so five steps would exhaust it before the
chain finished. The obvious fix was never going to survive a real chip.

**The user's design instead: look twice, and change the question the first
time.**

1. **Stage 1 — triage.** Show the whole sheet ONCE and ask *"which panels want
   a closer look?"* That is a well-posed question about that picture — unlike
   "is qubit 5 good?", which is not. Cost: one call, whatever N is.
2. **Stage 2 — dedicated look.** Re-plot the named panels ALONE through the
   lab's own plotting module (`figure_gen.generate(..., targets=[...])`, built
   in P0 and until now with zero callers) and ask the per-target signature
   question on a picture that contains only that target.

The second stage is what P0's figure machinery was for; the first stage is what
makes the whole thing fit in the budget.

### 18.2 The rule that keeps it honest

Stage 1's silence is where this design can quietly go wrong: if a panel the
overview glossed over is then declared **done** on the strength of that glance,
D-11.1 is back wearing a different hat. So:

> **The set that gets a dedicated look = (targets the deterministic gates
> flagged) ∪ (targets triage flagged).** Union — never either alone.

Because the two fail **differently**. The gates miss the self-consistent noise
fit (the archived #575 class: a fit that agrees with itself on garbage, which a
node-faithful replay also agrees with). The eye misses a 3% amplitude error that
no picture shows. Neither is a superset of the other, so letting one filter the
other loses exactly the cases the other was there to catch.

Consequences, all pinned in `tests/test_runner_p3.py::TestTwoStageLooking`:

* triage `all_fine` **never** overrides a gate suspect — that target still gets
  its own look;
* an unusable or unavailable triage answers `unreadable`, which escalates
  **every** target: a router that fails must widen the net, never narrow it;
* triage may only name targets that exist on this chip (a hallucinated name is
  dropped, not looked up);
* triage is a **router, not a verdict** — nothing terminates or fails on it, so
  its worst case is a wasted call. The prompt therefore biases it toward naming:
  *"a named panel only costs one closer look, while a missed one is never
  looked at again."*
* a target in neither set terminates on gates + overview, and the ledger must
  stamp it **overview only** so no report implies it got a dedicated look.

### 18.3 Testing the judge before the key exists

The user's second decision: run the judge as **fresh-context Sonnet subagents**
rather than waiting for an API key. Each call gets exactly the payload the API
would receive — the system prompt plus the request context rendered by
`build_triage_bundle` — and one real archived figure, with an explicit
instruction not to read the run's `node.json`, `data.json` or any stored fit.
The answer comes from the picture and the pack alone.

This is not a simulation of the judge; it is the judge, minus the HTTP call. It
measures **Sonnet with our prompt and our pack**, which is the part we wrote and
the part most likely to be wrong. When the key lands, the same case set re-runs
against the real API and the numbers should reproduce; if they don't, the
difference is the transport, not the design.

**Ground truth costs nothing:** each archived run records its own per-target
outcomes, so a sheet where every target succeeded should triage `all_fine`, and
one with failures should be `some_suspect` naming those targets.

**The two sides are scored differently, and conflating them would be
dishonest:**

| side | what a wrong answer costs | metric |
|---|---|---|
| good sheet | one wasted per-target look — triage is a router, so this is **not** a false reject | escalation cost (extra panels requested per sheet) |
| bad sheet | the failed panel is **never looked at again** | recall over the failed targets |

Sample counts are reported beside every rate. A rate without its count is not a
measurement (docs/47's accuracy-ledger discipline, and R11's reason).

### 18.4 Deferred, deliberately (user decision)

§17's other two open items are **parked, not forgotten**:

* stamping `env` / `root_kind` / `root_rev` on every verdict (§17 B3);
* blocking `load_data_id` on the agent path (§17.6) — the key that makes a node
  replay archived data instead of measuring. Nothing sets it today; the risk
  arrives with P4, which is where it must be fixed.

Both keep their §17 owners. Neither is a prerequisite for the two-stage work.

### 18.5 Stage-1 pilot result (2026-08-07) — 16 real figures, real Sonnet

16 fresh-context Sonnet judges, each handed the byte-identical
`build_triage_bundle` payload plus one real archived sheet, forbidden from
reading the run's `node.json` / `data.json` / stored fits. Ground truth = the
node's own per-target outcomes. Cases spread over two families, three chips and
five months.

| family | good sheets | clean | extra looks/sheet | bad sheets | flagged | panel recall |
|---|---:|---:|---:|---:|---:|---:|
| qubit_spectroscopy | 4 | 50% (2/4) | 1.25 | 4 | 100% | **100%** (8/8) |
| power_rabi | 4 | 75% (3/4) | 1.25 | 4 | 100% | **89%** (8/9) |

**The side that matters passes.** Every sheet containing a node-failed target
was flagged, and 16 of 17 failed panels were named. The one miss (`qC5`, c14) is
not categorical — the same judge named `qC5` correctly in c07.

**The escalations look like signal, not noise.** On c00 — nine qubits, all
node-accepted — Sonnet flagged q4, q5, q7, q8. Two neighbouring runs on the SAME
chip the SAME day are in the bad set: c04 (node failed q7) and c05 (node failed
q4, q7). The "false" escalations land on exactly the qubits that chip was
struggling with, which that particular run's threshold happened to pass. Read as
marginal panels being noticed, not invented.

This is why the router framing earns its keep: **none of the above is a false
reject.** c00 costs four extra per-panel looks, which the signature ask then
adjudicates on single-panel pictures. At 1.25 extra calls per clean sheet, a
9-qubit chip stays far inside the 40-call plan budget — the affordability
argument for two-stage looking survives contact with data.

**Caveats, on the record:**

* n = 16, two families. A pilot. Every rate is printed with its count.
* Ground truth is the node's own verdict, and a node can fail a target for
  reasons the picture cannot show (a fit that failed to converge on data that
  looks fine). A "miss" is therefore sometimes "not visible", not "not seen".
* This measures **Sonnet + our prompt + our pack** — the part we authored and
  the part most likely to be wrong. The same case set re-runs against the real
  API when a key exists; a difference then is transport, not design.

**Method note (a mistake worth keeping).** The first attempt was scored against
hand-transcribed figure paths and pointed at the wrong runs; it surfaced only
because one path happened not to exist and the judge said so. Had all sixteen
wrong paths existed, confident numbers would have been computed against
mismatched ground truth. The case file is now the single source for both the
run arguments and the scorer, so the two cannot disagree — the same discipline
§15 applies to bands, applied to the experiment itself.

---

## 19. P4–P8 in one pass (2026-08-07) — the loop closes

User decisions taken as given: judge-unavailable ⇒ gates-only **stamped**;
default autonomy `review`; P6b minimal; P8 harness + small pilot.

### 19.1 The §1.3 terminator is now WIRED (closes §17 B1)

`_evaluate` gained the round that was missing: for every target the gates
PASSED, the judge is asked whether the picture carries a correct signature, and
**a refusal turns the pass into a fail**. That is the loop; until now the plan
described it and nothing enforced it.

Who gets asked follows §18's two-stage rule: one triage call for the sheet, then
a dedicated look for the **union** of (gates flagged) and (triage flagged).
Measured in the sim on 3 qubits × 2 steps: **3 LLM calls**, one target refused,
that target dropped from the downstream step. Naive per-target would have been
six calls and climbing — the affordability argument, demonstrated rather than
argued.

Three states the ledger now distinguishes, because a report that cannot tell
them apart implies a check that never happened:

| `vision` | meaning |
|---|---|
| `clear` | a dedicated look, signed off |
| `overview_only` | nobody flagged it; it terminated on gates + the sheet |
| `unavailable` | no judge configured — the approved policy, stamped |

Plus `panel_kind` (`panel` / `sheet`) so "judged on its own picture" is never
assumed. The sim now renders per-target panels; before, everything was drawn on
one axes and there was nothing to extract.

**A single-target run is a special case and was initially wrong.** Triage only
runs on multi-target sheets, so a 1-qubit run had no overview *and* no dedicated
look — nobody looked at all. Now a one-target run always gets its look: the
sheet already IS that panel, and the whole cost is one call.

### 19.2 P4 — the action space, and the key that was never blocked

`action_space.py` classifies by **"can a wrong choice lie to us?"**, not by
number-ness (D-3). `num_shots = 3` is a number and is safe — wrong ⇒ visibly
noisy. `use_state_discrimination = True` without calibrated blobs is a boolean
and is dangerous — clean-looking populations that are garbage.

* **class A** picks real numbers inside code-owned bounds; **class B** may only
  propose, and code checks the precondition; **frozen** is never touched;
  **reserved** includes **`load_data_id`** — §17.6's finding, now enforced at
  the agent's own write path in `realbackend`, where every drop is logged. A
  human may still replay archived data; the agent may not.
* an **unclassified** key is `unknown`, not class A. A parameter nobody
  classified is one nobody thought about, and the deceptive ones look harmless.
* **bounds are data-derived** (D-5): hardware reach from `spec_constraints`,
  widened by what this lab has actually run — never from schema defaults, which
  observed values leave far behind. The corpus can widen a soft floor; it can
  never widen a physical ceiling.
* an out-of-bound proposal is **rejected, not clamped** — clamping hands the
  loop a number nobody chose and hides that the agent asked for the impossible.
* a class-B precondition that cannot be CHECKED refuses. An unverifiable
  precondition is not a satisfied one.

### 19.3 P5 — a counter is not a stop-loss

`stoploss.py`, three tiers, one entry point ordered harm → budget → no-progress.

* **Tier 1** finally has the plan **step cap** and **wall clock** §4.7 listed as
  absent from the day the plan was written. They matter precisely because the
  work queue accepts runtime-inserted rungs: "steps remaining" is not a bound.
  Unset means unlimited (an unset clock is not a zero clock); `max_steps: 0` is
  rejected as the typo it is rather than silently doing nothing.
* **Tier 2** needs BOTH signals flat — the metric trend (free: the gates already
  compute `peak_snr`/`r2`/`contrast` every attempt) and the pairwise vision
  comparison. Either alone would stop runs that are genuinely improving on the
  other axis. A metric wobbling inside ±5% is not progress, and this is the only
  thing that catches **oscillation**, where every individual step is justified
  and a counter never fires.
* **Tier 3** is harm: seeds written and never consumed, drive at the ceiling,
  and the same target escalating upstream twice — which means the problem is
  not where we think it is.

### 19.4 P6c — the review that only exists across runs

Every gate so far judges ONE run against itself. The failure that survives all
of them is the pair that is each internally consistent and mutually impossible:
node 06 puts a qubit's sweet spot at one bias, node 09 puts it elsewhere, both
with clean fits and convincing figures. No per-run gate sees it; neither does
the judge.

**What to compare is corpus-derived.** Harvesting the archives showed which
quantities ≥2 families actually claim. The list is then **curated**, because the
same key name is not the same quantity — `frequency_shift` appears in four
families, but 06's is a qubit-flux response and 07's is a coupler-flux one, so
comparing them would manufacture disagreement. `optimal_power` is excluded for
the same reason: 05 and 08b optimise different lines.

**The tolerance is a physical scale the runs themselves report** — a linewidth
for frequencies, the flux step for offsets — never a constant typed into the
module. A hardcoded Hz is the Clause-B mistake in numeric form: right for the
chip it was written on, wrong for the next. A broad resonance tolerates a wider
disagreement than a sharp one, and the same two numbers correctly produce two
different verdicts.

The review **reports only**. Deciding which of two contradictory results to keep
is not something a consistency check has the standing to do — it fires
`needs_human`, which D-8 calls a normal terminal state, not an exhaustion.

### 19.5 P7 — four events, best-effort

`notify.py`: `plan_done` · `target_halted` · `plan_stopped` · `needs_human`.
A notifier that fires on everything gets muted, and a muted notifier reads as
coverage while delivering nothing. Webhook + a persisted browser queue (a closed
laptop must not lose the night). A dead webhook never raises, and a notifier
that explodes cannot kill a plan.

### 19.6 P8 — the harness, and why agreement is the wrong metric

`replay_score.py` carves decision points out of a real session — the first *k*
runs, what the operator did next, and how many runs they still needed — and
scores proposals against them.

**The metric is "reaches the same conclusion in fewer runs", not agreement.**
The reference case is docs/56 §6V case C: the operator burned three drive-power
attempts and a day before refining the step. Agreeing with that is not success.
So the harness scores an agent that skips the dead end as **faster** even though
it agrees with the human *less*, and a knob the operator never touched is
**not** counted as disagreement — it may be the shortcut, and punishing it would
suppress exactly the behaviour the experiment exists to find. Without measured
outcomes the report says so instead of substituting agreement.

It never calls a model: the caller supplies proposals, which is what lets the
same case set run against a subagent today and the real API later and be
compared.

### 19.7 P9 — not done, and not doable here

Real hardware. There is no fridge on this machine, and its pre-flight requires
observing a scheduler-run node produce a dataset with `fit_results` **and**
`patches`. Everything upstream is offline-verified in the sim; P9 begins when
someone points this at an instrument.

### 19.8 Still open

* **P3c/P3d** — the signature calibration needs a key. §18.5's pilot measured
  stage 1 only; the ≥90% bar belongs to the signature ask.
* §17 B3 — verdicts still do not record `env`/`root_rev` (user deferred).
* `power_rabi` still has no `verify_wide` (§17.6).
* P6b is minimal by decision: the board carries `vision`/`panel`, and no new
  screen was built — browser verification is not something this session could do
  honestly.

---

## 20. Empirical validation of P6c and P8 (2026-08-07)

User: *"우리는 데이터가 꽤 풍부하게 있잖아 — 최대한 실증해보자."* Both were
built from physical reasoning and neither had been measured. Measuring them
changed one substantially and stopped the other.

### 20.1 P6c: a 37.5% false-contradiction rate

First measurement: pair every cross-checked family within a session on the same
qubit, keep only fits the NODE accepted, and run the review.

> **80 comparisons → 30 contradictions → 37.5% false alarm.**

An alarm that fires on more than a third of good pairs is not a loose alarm; it
is noise with an authoritative voice. The design used `2.0 × linewidth`
everywhere — physically well-reasoned (a Lorentzian's frequency uncertainty
scales with its own linewidth) and simply wrong as a number.

**This is the P2 lesson for the third time.** A threshold written from physical
intuition is a *hypothesis* until the lab's data has answered it, no matter how
sound the reasoning behind it.

### 20.2 What the breakdown showed

Splitting by family pair, restricting to ADJACENT runs (≤5 run ids apart — the
same chip state), and keeping only fits that pass OUR gates rather than the
node's looser bar:

| pair | n | p50 | p90 | p99 | usable factor |
|---|---:|---:|---:|---:|---:|
| 03–05 resonator | 47 | 0.108 | 0.368 | **4.6** | 7 |
| 03–06 resonator | 86 | 0.072 | 0.791 | **13.2** | 20 |
| 08–08b qubit | 84 | 0.120 | 0.561 | **57.1** | 86 |
| 08–09 qubit | 62 | 0.288 | 46.7 | **97** | 146 |
| 06f–09f flux | **3** | — | — | — | — |

Three conclusions, none of which were guessable:

1. **The resonator check survives** at 20 linewidths, and that is still a real
   check: neighbouring resonators sit ~50 linewidths apart, so it catches "this
   node fitted the WRONG resonator" — the failure each run hides on its own.
2. **The qubit checks do not.** Usable only at 86–146 linewidths (340–580 MHz on
   a 4 MHz line), which is *wider than the spacing to a neighbouring qubit
   line*. A check that cannot catch the error it exists for is not a loose
   check — it is not a check. Dropped. (08-vs-09 additionally compares the
   frequency at the CURRENT bias against the frequency at the SWEET SPOT, which
   are different quantities by construction: that is what node 09 is for.)
3. **The flux check — the one this module was designed around** — has exactly
   **three** gate-passing pairs in the entire corpus. Three samples cannot
   calibrate a threshold. Shipping one anyway would be precisely the invented
   number the module refuses everywhere else. Dropped, with a test that exists
   to keep the gap visible (`test_the_flux_sweet_spot_case_is_NOT_covered…`,
   which says to delete it when the data arrives).

Also learned: the ORDER of investigation mattered. The first hypothesis — "these
are different physical quantities, so of course they differ" — was wrong;
03-vs-06 (current bias vs sweet spot) has the TIGHTEST median of all (0.072).
The second — "my pairing spans chip retunes" — was also wrong; the tail survives
at run-id adjacency. What actually removed it was applying OUR gates: much of
the tail was fits the node waved through and ours reject.

**Result: 0 false contradictions in 133 gate-passing adjacent pairs**, and the
dropped pairs do exceed the threshold (08–09 in 7 cases), so the drop decision
is itself data-confirmed.

### 20.3 P6c is now much smaller than designed, and that is the honest outcome

One quantity, three sources, one factor. It answers exactly one question — *do
two nodes agree about which resonator this is?* — and answers it with a number
the lab's own data chose. Everything else waits for evidence.

### 20.4 P8: the offline metric, implemented

`runs_saved()` closes the gap named in §19.6. Re-measuring a chip from an
archive is impossible, so "did the agent's proposal work?" is unanswerable —
but this is not:

> the agent proposed at step *k* what the operator only reached at *k+n*
> ⇒ **n runs saved**

Pure archive arithmetic: no re-measurement, no hardware, no key. Matching is by
DECISION, not by number (within 25%): demanding equality would score a correct
call as a miss because the agent said 78 where the operator typed 80. A proposal
the operator never made returns `None` — unscoreable, not wrong, because the
archive genuinely cannot say whether it was better or nonsense.

### 20.5 P8: and then the measurement said stop

Before scoring an agent, measure what it is competing against. Across five
archives and five families:

| | |
|---|---|
| recovery sequences (a target that failed, then later passed) | **14** |
| decision points in all of them | **19** |
| targets that ever needed a retry | 12–15% |
| runs still needed from the first failure | **median 1**, p90 3, max 3 |
| sequences needing more than one run | **3 of 14 (21%)** |
| parameter changes observed between failures | **1** (a `num_shots`) |

**The corpus does not contain the experiment P8 was designed to run.** These
operators almost never tune-and-retry; 79% of recoveries take a single run and
essentially nothing is adjusted in between. docs/56 §6V case C — three
drive-power attempts and a day lost before densifying the grid — is a
*documented incident*, not the typical pattern in what we hold.

So the harness ships tested and unused. Running a scoring campaign on 19 points
with one observed knob change would produce a number with no statistical
meaning, which is worse than no number: it would be quoted. What would make P8
real, in order of cost: (a) the specific archives where operators genuinely
iterated, (b) sessions captured from here on with the loop running, (c) the sim
— where recovery sequences can be manufactured, though then the agent is being
scored against our own adaptation ladders rather than against a human, which is
a different experiment and should not be reported as this one.

### 20.6 What this session's measurements have now overturned

| what | written from | what the data said |
|---|---|---|
| `r2 ≥ 0.75` (qubit spec) | intuition | rejects 12/34 fits the node accepted |
| prefactor ∈ [0.5, 2.0] | intuition | rejects 4/55 accepted |
| jump limits | intuition | 37 accepted moves exceeded them |
| P6c `2.0 × linewidth` | physical reasoning | 37.5% false contradictions |
| P8's premise | a documented incident | 19 decision points exist, total |

Five for five. The pattern is not that the reasoning was careless — the flux
tolerance argument in §19.4 is still *correct physics*. It is that a threshold
is a claim about a particular lab's data, and the only thing that settles a
claim about data is the data.

---

## 21. B3, §17.6, and the panel machinery exercised (2026-08-07)

Everything reachable without a device. Two open §17 items closed, and the
blocker §17 called "the top blocker for P3c" turned out to be a missing caller
rather than a missing capability.

### 21.1 B3 — a verdict now records the context it is only valid inside

§13.2 was binding and unimplemented: *"a verification context is (env, source
root, run generation); every verdict records all three."* Three paths produced
verdicts and each answered differently — `fit_audit.audit_run` put env and root
in its cache KEY and handed the payload back unlabelled, `figure_gen` carried
all four axes, the engine's gate verdicts carried none.

`core/autofit/verification.py` is the one shape all three stamp. The design
point worth keeping: **two analyses are stamped differently on purpose.**

* `lab_replay` — the lab's own analysis re-run in a customer env. Identity =
  (env, `lib_versions`, root + revision, `gate_hash` over the analysis bytes).
* `sm_gates` — SM's deterministic gates, computed in-process. No interpreter is
  spawned, so naming one would be a fiction. Identity = `analysis_rev`, a
  content hash of `families.py` + `gates.py`.

Collapsing them into one shape would have been the same class of error the
stamp exists to prevent. And `analysis_rev` is not hypothetical bookkeeping: it
is the axis that actually moved — sixteen shipped bands were overturned by
measurement in one session (§15.2b) and five more thresholds in the next
(§20.6). Every verdict written before those edits means something different
from one written after.

**The stamp has a consumer, which is the point.** `consistency.reconcile` — the
only place in the system that reasons ACROSS runs, and therefore the only place
D-13 can bite — now refuses to compare values obtained under different
contexts, and records the refusal in `skipped`. A disagreement between a value
read by one gate revision and one read by another is not a contradiction about
the chip; it is a category error, and reporting it as physics is how a review
loses its authority. Passing no `contexts` keeps the previous behaviour
byte-identically, so the pin from §20.2 still holds.

Fixed on the way: `figure_gen`'s no-compatible-env path built its context from a
`source_root` that had already been rebound to `None`, and so reported "the
env's installed analysis" for an analysis that never ran. A confident blank is
exactly what the stamp is supposed to make impossible.

### 21.2 §17.6 — power_rabi's wide check, and the shape the corpus refused

The family shipped with no `verify_wide`. The obvious fix was the shape the four
spectroscopy families use — multiply the swept span by four — and 230 archived
runs (899 accepted prefactors) refuse it:

| mode | runs | window | pulses |
|---|---:|---|---:|
| survey | 122 | **[0.001, 1.99]** in 103 of them, step 0.005 | 1 |
| error-amplified | 108 | median width **0.3** | 20–160 |

Pulse count and window width are anti-correlated because they are physically
coupled: N pulses alias unless the range stays near 1/N of a Rabi period about
1.0. So scaling the narrow window by four is wrong **twice**: it reaches only
[0.6, 1.6] — short of the 0.0024–2.366 that accepted optima actually span — and
it keeps the pulse count that makes that range fold. It would not survey; it
would alias.

The honest wide check here is a **mode switch to the lab's own survey**, which
is also the one measurement that can unmask a locked harmonic: a full
single-pulse Rabi curve shows the whole oscillation. `verify_wide` gained a
`survey_params` form for absolute mode switches; `num_shots` is deliberately not
pinned, so whatever averaging the ladder climbed to is kept.

**This is the sixth threshold this line of work has had overturned by
measurement, and the first where the refuted thing was a SHAPE rather than a
number.** "Generalize the mechanism that already works" is the same species of
claim as "0.75 is a reasonable r²" — plausible, and answerable only by data.

### 21.3 §17 B2 was a missing caller, not a missing capability

§17 called per-target panel extraction "the top blocker for P3c" and recorded
that `figure_gen.generate(..., targets=[...])` had **zero consumers** — which
means it had never been run end to end. Running it is what settles it.

On a real 9-qubit archived run, requesting `targets=["q0","q4","q2"]` produced
three genuine single-target panels through the lab's own plotting module in
40 s. Inspected: `q0` carries a Lorentzian fit with its parameter box; `q4` —
which the node itself failed — is pure noise watermarked NO FIT. That is exactly
the artifact D-11.1 asks for.

Getting there exercised D-13's designed answer for the first time on real data.
The live analysis tree could serve **neither** env: the 0.5-era env cannot
import its `quam_config` (needs quam ≥ 0.6), and the 0.6 env cannot load a
2026-05 run's `quam_state`. `sourceroot.candidates(..., revs="auto")` walked the
revisions of the analysis-defining paths, materialized each read-only via
`git archive`, and the **third** pinned revision loaded cleanly. Ten candidate
roots, four failed (env × root) probes, then a compatible pair — and the
resulting figure carries that pair in its context stamp.

Worth stating plainly: without the pinned-revision walk, **every archive older
than the tree's current HEAD is unreplayable**, and P3c would have had no case
set at all. The amendment that added the third axis to the verification triple
is what makes the calibration possible.

---

## 22. The constant audit, and the calibration that corrected its own labels (2026-08-07)

Two campaigns, both device-free. The first applied §20.6's method to every
remaining un-measured constant in `core/autofit/`; the second ran the shipped
signature ask against real per-target panels, now that §21.3 showed they can be
made. Each measurement was re-run by an independent agent told to REFUTE it, and
only what survived that pass is reported as fact.

### 22.1 Six audits, each adversarially verified

The pattern held: the constants written from intuition did not survive, and —
more usefully — **three of the six audits found that the constant did not matter
because the code around it was inert or broken.** Measuring a threshold is how
we discovered nobody was reading it.

**Confirmed, and structural:**

* **Stop-loss tiers 2 and 3 have no caller.** The engine constructs
  `stoploss.Budget` and never calls `should_stop`, `no_progress`, `metric_trend`
  or `harm`. Every constant in that module is currently inert — including the
  ones this audit was convened to measure. Worse, two of them are *unreachable
  by construction*: the ladders index `rungs[min(count, len-1)]` under
  `retry_max <= 2`, so the seed rung (index 2) and the escalate rung (index 3)
  never fire, which makes `unconsumed_seeds >= 3` and `upstream_escalations >= 2`
  dead conditions. D-8 said "a counter is not a stop-loss"; the honest amendment
  is that a stop-loss nobody calls is not a stop-loss either.
* **`PROGRESS_KEYS` covers almost nothing.** Four of the nine families emit none
  of the eight metrics, ever (284 entries); two more emit them in 2-3% of runs.
  Tier 2a is therefore blind on most of the chain, and would read "no metric
  improved" as evidence rather than as absence of evidence. Those families must
  be declared metric-blind, not silently treated as flat.
* **`action_space.sanitize` contradicted its own policy.** `classify` documents
  that an unclassified key is one nobody has thought about, and
  `reduced_schema`/`validate_proposal` both refuse one — but `sanitize`, the
  function on the real backend path, passed it straight through. Two halves of
  one policy disagreeing means the stricter half was decorative. **Fixed.**
* **`gates._read_target_trace` was h5py-only.** G3 is the raw-data cross-check —
  the one gate that can distinguish a fit which missed the feature from one
  which found it — and it opened `ds_raw.h5` with raw h5py. Runs from envs that
  write NetCDF-classic under that name (732 targets in the corpus) answered
  "unreadable": not a degraded check, **no check, silently**. **Fixed** by
  routing through the ndview reader adapter. (The same bug bit this session's
  own scratch script, which is how confident one should be that a second copy
  of a format sniff drifts.)
* **The judge could take the plan down.** The provider call path caught network
  and value errors but not the payload-SHAPE errors an unexpected response
  raises. A judge that cannot answer must fail to its safe default, never
  upward. **Fixed** at both call sites, all four asks.
* **`metric_trend`'s `best` was not a running maximum** — it advanced only on
  values that cleared the noise floor, so it reported the last value that
  happened to jump. **Fixed**, with the noise floor left where it is (the fix
  must not turn noise into learning).

**Confirmed, and the constant is wrong — but not changed here:**

* **`replay_score`'s `rel_tol = 0.25` erases 20.2% of real operator changes**
  (n=644) and sits *on* an operator step mode rather than in a gap: a 1.333x
  step lands at exactly 0.25 and the comparison is `<=`, so 78 canonical steps
  are declared "the same decision". The evidence supports 0.05-0.10. Not changed
  yet because the same verifier found a defect **no constant fixes**: `_close`
  applies a *relative* tolerance to *log-unit* (dBm) keys, where the same
  physical 10 dB step scores 0.125, 0.25 or 0.50 depending only on the
  reference. That needs an absolute-delta branch, and the two belong in one edit.
* **`action_space`'s corpus bounds are a zero-slack envelope.** The branch only
  ever widens to `[min_observed, max_observed]`, so it is *vacuous on its own
  training set* (0 rejections in 636 runs) and rejects real usage the moment it
  meets an archive it was not built from — 2.0% to 22.6% depending on how the
  held-out set is drawn, and the verifier declined to stake any single headline.
  Two shape defects underneath it: sweep-EDGE knobs are bounded from both sides
  when the danger is one-sided, and **69 of 101 corpus bound edges land exactly
  on a recorded schema default** — the docstring promises bounds are "never
  taken from the schema's defaults", and the corpus path violates that in
  outcome for a knob nobody varied.
* **Several gate bands do not survive the full corpus.** The strongest single
  finding is a **unit defect** in the T1 family (a `x1e-9` that reaches the band,
  the relative-jump limit and the `UpdateSpec` write, proven against the node's
  own patches). Alongside it: the spectral-presence floor rejects 44 of 77
  accepted node-09 fits, `_ERROR_RATIO_MAX` does not discriminate on ramsey
  (57.3%), `_FEATURE_Z_MIN` rejects 40.9% of accepted node-08 targets, and a
  `qubit_pair`/`qubit` coord mismatch has kept two families from ever running a
  feature check at all. **The "0 false rejects over 276/115" ledger does not
  survive extension to the full corpus.**

  *Nothing here is re-tuned in this commit*, on the verifier's own condition:
  the error-amplification, e->f and vs-flux-calibration populations must be split
  out of their host families first, because that contamination is upstream of
  every per-band number in the report. Re-deriving a band from a mixed
  population would repeat §20.1's mistake with better manners.

**Confirmed about the judge pack:** the Clause-B lint has ~0 recall on
prose-form violations (it catches units and explicit window fractions, not
"near the centre"), so `lint_dropped == []` is silence, not a clean bill of
health; `notes` is linted but never rendered (19 kB of maintainer caveats in a
dead field); and a whole `axes` description is blanked on a single token hit.
Eight of nine pack entries were authored from fewer than ten figures.

**Refuted, and worth recording as method:** several headline numbers in the
first-pass reports did not survive — a slack table that was internally
impossible, "half the hits are floor rejections" (31%), a claimed density
minimum that finer sampling erased, and a 26-run count that was 8. The verify
pass earned its cost: roughly a fifth of the quantitative claims were wrong in
detail while the directions held.

### 22.2 P3c — the calibration corrected its own labels twice

64 per-target panels across five families, regenerated through the lab's own
plotting (§21.3), each judged by a single call using the SHIPPED
`_SIGNATURE_SYSTEM` and the shipped v1 pack — one panel per call, which is the
shipped contract.

**The first labelling was wrong, and D-13 is why.** Labels came from the
archived `node.json` outcome; pictures were drawn with `fit_source="fresh"`,
i.e. the CURRENT analysis revision. Those are two different verification
contexts. Verified on a real panel: a target marked `successful` in the archive
draws NO FIT over pure noise under the fresh analysis — the judge called it
absent and was **right**. Re-labelling with `fit_audit.audit_run` (same env,
same pinned root, same analysis as the figure) **flipped 18 of 64 labels — 28%
of the case set.** The stamp introduced in §21.1 is what made the error
diagnosable rather than mysterious.

Against labels that share the pictures' context:

| | n | result |
|---|---:|---|
| **leniency** (bad panel -> judge says clear) | 12 | **0 (0%)** |
| **stinginess** (good panel -> judge says clear) | 52 | 39 (75%) |

The leniency result is the one that protects the loop: under §1.3 a "clear" is
the last word before a target is declared done, and **no bad panel got one.**

**The 90% stinginess bar, as written, cannot be measured this way** — and that
is the second label correction. Inspecting the 13 disagreements shows the judge
naming real defects that the label cannot see, because the label is a *scalar*
success flag and the judge is asked about a *picture*:

* on the 2-D power-sweep family, the node's own per-row centre chain rails onto
  a parasitic tone for several rows while the ridge itself is unmistakable — the
  scalar frequency is fine, the picture is not;
* on a resonator panel the judge called unclear, **the lab's own plotting had
  already titled it "(freq OK, shape poor)"** — its analysis carries a separate
  `success_shape` verdict and renders three title states from it.

That is the judge agreeing with the lab's picture-level verdict and disagreeing
with its scalar one, which is exactly what §1.3 asks for (gates AND judge, not
judge ~ gates). Only 4 of the 64 panels carry an archived `success_shape`, so
the correlation is a **lead at n=4**, corroborated by direct inspection of two
panels — not a measurement. Recording it as more would repeat the error this
section is about.

**So D-7's stinginess bar needs a picture-level label, and the project does not
have one.** Options, in order of cost: hand-label a set; or derive one from the
labs' own quality flags where they exist (they do, and they are not exposed
through `fit_audit` today).

### 22.3 P3d — the seam runs, and the deception has to be visible

`figure_gen`'s `override_fit` had also never been exercised. It works: nine
manufactured wrong-fit panels were produced for `power_rabi` by halving the pi
amplitude and its prefactor **together** (halving both is what makes the lie
self-consistent, which is what makes it deceptive).

They were not judged, because looking at them showed the deception **is not
rendered**: that family's primary figure is a raw chevron map with no fit
overlay, so changing the claimed number changes nothing in the picture. Two
consequences, both worth stating plainly:

1. **A wrong-fit injection can only test a judge where the family's figure
   actually draws the overridden quantity.** D-7's leniency calibration
   therefore has a per-family precondition the plan never recorded.
2. More importantly: for that family's error-amplification variant, **the vision
   judge cannot catch a wrong pi amplitude at all** — the figure it is shown does
   not display the claim. That is consistent with D-1 (the number is the gates'
   job), but it means "the judge signed off" carries less information there than
   elsewhere, and a report must not let it read the same.

The spectroscopy half of the manufactured set was not built: the fresh analysis
names its centre `f0` where the archive stores `position` (§4.5b drift again),
and the override plumbing hit a dtype refusal after the schema probe was added.
Fixable; not fixed.

### 22.4 What is now open, in the order the evidence supports

1. ~~the T1 unit defect~~ — **done, §22.5.** What remains is to split the
   contaminated populations out of their host families and then re-derive the
   *bands* §22.1 flags.
2. Wire tiers 2 and 3 of the stop-loss to a caller, and declare the
   metric-blind families rather than letting a blind check read as a passed one.
3. `rel_tol` + the dB-key absolute-delta branch, in one edit.
4. Re-shape the corpus bounds (slack, one-sided edges, no default-derived edge).
5. A picture-level label source for D-7's stinginess bar.
6. Harden the Clause-B lint against prose, and surface or drop `notes`.

### 22.5 The T1 unit defect — and the sim that was validating it

§22.4 ranked this first because it is a bug, not a calibration. Confirming it
independently took four measurements, and the third and fourth are the
interesting ones.

1. **The chips store seconds.** `qubits.*.T1` / `T2ramsey` / `T2echo` across 399
   archived snapshots: n = 8,379 / 7,354 / 7,980, p50 ≈ 3e-5. The shipped band
   `[0.5e-6, 1e-3]` is *correct* for those values.
2. **The node's fit reports nanoseconds.** `t1` ≈ 3e4. So the band accepted
   **0 of 6** accepted fits, and the `UpdateSpec` would have written ~30,000
   SECONDS into a field holding 30 microseconds — a 1e9 error, straight to the
   live chip.
3. **Six fits from three runs is not a convention.** They all came from ONE
   node variant in ONE archive, which is exactly the sample-population trap that
   §20.1 and §22.1 keep catching. The corpus turns out to hold **two** T1 node
   versions; measuring the other gives **n = 141 across 27 runs, also
   nanoseconds**. n = 147 over two node versions and two archives — now it is a
   convention.
4. **The sim was emitting seconds**, which is why nothing had ever failed.
   `synth.py` produced `t1` in the same units the band expected, so the ledger's
   T1 rows agreed with a gate that rejects every real T1 fit. **A simulator
   built to match the code rather than the instrument validates the bug.** The
   sim now emits nanoseconds for the fit and keeps seconds for the patch —
   which is what the real node does.

The fix is deliberately *not* a scale bolted onto the band or onto the write.
Both of those exist, and the defect's real cause is that **there were two
readers**: `gates` read `entry[pl.key]` for the band while `families` read
`fit_entry[spec.fit_key]` for the write, and nothing made them agree about
units. So the fix is **one reader** — `families.fit_value(fam, entry, key)`,
through which the band, the jump limit, the G5 history anchor and the write all
now pass. A test pins the property directly: what the band judged is exactly
what gets written.

The scale is scoped by measurement, not by family shape: ramsey's `decay`
(n=635) and echo's `T2_echo` (n=143) already report seconds and are **not**
scaled — scaling them would have created the same defect in the other
direction. A test pins that too, and pins that no other family acquires a scale
by accident.

---

## 23. Does the fit automation actually work? Replayed on the two vs_power families (2026-08-08)

No device was available, so the check was run against the archive — which is
better than a smoke test, because each archived run carries the answer key:
`patches[].old` is the state before and `patches[].value` is what the
instrument's own node decided. `current_value_of` returns the PRE-update state
(patch `old` first), since a run's `quam_state` snapshot is POST-update whenever
patches exist and reading it would hand the automation the answer.

The engine has **two** write paths and they had to be checked separately: when
the node produced its own patches the engine KEEPS them and only the gate
verdict decides the chip's fate; when the node wrote nothing, the engine
computes the write itself from the family's `UpdateSpec`s.

### 23.1 The gates, over 252 real targets

| family | node accepted | ours pass / suspect / fail | node rejected | ours fail |
|---|---:|---|---:|---:|
| qubit spectroscopy vs power | 87 | **87 / 0 / 0** | 3 | **3** |
| resonator spectroscopy vs power | 136 | 79 / 57 / 0 | 26 | **26** |

**Zero false rejects and zero false accepts.** Every one of the 29 targets the
node itself rejected is caught; nothing the node accepted is thrown away.

The 57 suspects are not rejections — they route to the judge — and they have one
cause: the family's own consistency check firing on runs where *"the node
produced no power split (target full-scale / amplitude absent) — its own
analysis declined this fit"*. That is a real signal, but 42% of accepted targets
escalating is a cost worth naming, and it is the first candidate for the
population split §22.4 already calls for.

### 23.2 The forward write, and a coverage gap the replay found

Parity on the paths the forward path computes: **189 match, 2 mismatch.** Both
mismatches are the same target in one run, where the node emitted a *no-op*
patch (old == value) while our fit-derived value moved 3.2 MHz — and our gates
had already marked that target `suspect`, so the loop would never have written
it. The system disagreeing with a node that declined its own fit is the system
working.

The real finding is coverage. Node 08b writes **six** fields per target and the
forward path computed **two**; node 05 writes three (plus the coupled power
rows) and it computed two. Measured against every archived write:

| path | in the fit? | action |
|---|---|---|
| `qubits.{q}.anharmonicity` | `anharmonicity_fitted`, **36/36** | **added** |
| `qubits.{q}.resonator.frequency_bare` | `bare_resonator_frequency`, **21/21** | **added** |
| `qubits.{q}.resonator.operations.readout.amplitude` | — | already built by `power_rows` |
| `qubits.{q}.xy.operations.saturation.amplitude` | `optimal_amplitude`, only **10/38** | declared gap |
| `…x180_DragCosine.amplitude` | nothing matches, **0/23** | declared gap |
| `…x90_DragCosine.amplitude` | nothing matches, **0/23** | declared gap |

The two additions are the node's own numbers, verified on every archived write.
The three that are left are **not** closed by inference: no fit key reports
them, so writing them would mean reverse-engineering the node's formula, and
D-14 says run-derived or skipped, never guessed.

But skipping them silently is the other failure. The forward path only runs when
the node wrote nothing, so writing our subset leaves the rest stale — the "quiet
partial" r12 forbids, and the same hazard `power_rows` already guards for the
resonator family. So `Family.forward_gaps` declares each missing path **with the
measurement that made it a gap**, and the engine ledgers `forward_partial` and
files a review-queue entry naming the fields it left alone, resolved for that
target. The calibrated frequency is still written — it is worth having — but no
report can now imply the write was complete.

Parity after the additions: **189 / 2**, up from 132 / 2.

---

## 24. Working the §22.4 list (2026-08-08)

Four of the six, in the order the evidence supported. Two are left, with the
reason.

### 24.1 Stop-loss tiers 2 and 3 now have a caller — after two bugs were removed

The audit's finding was that `should_stop` / `no_progress` / `metric_trend` /
`harm` had **no caller at all**. Wiring them naively introduces two defects,
and both had to be fixed first — which is the whole reason this was ranked
after the measurement rather than before it.

1. **Metric blindness is not flatness.** Four of the nine families emit none of
   the eight progress metrics, ever. `metric_trend` now reports `present`, and
   `no_progress` returns None when nothing is present — otherwise tier 2a would
   stop every metric-blind family after three attempts, reading absence of
   evidence as evidence. (2b may still speak alone there, but only to say the
   picture is *degrading*.)
2. **Tier 2 must not pre-empt an untried escalation.** "We are not learning" is
   a claim about what we have *tried*; a cross-node re-calibration nobody has
   attempted is not one of those things — and it is precisely the fix for the
   case the escalate rung exists for (a qubit invisible because the READOUT is
   mis-centred, where no same-node knob can help). `should_stop` gained
   `allow_no_progress`, and the engine clears it while the mode's ladder still
   holds an escalate rung. Tiers 1 and 3 still apply: a budget is a fact and
   harm is harm, whatever remains untried.

   This was not foreseen — the LOOP_STUDY case-A scenario test failed the
   moment the wiring landed, which is the test doing its job.

On a stop the target is deferred and the plan continues (D-8: never a
half-adapted chip, never a lost night), and `target_stopped` carries the tier
and reason. Escalations are counted per target, which is tier 3's input.

### 24.2 `rel_tol`, and the defect no constant fixes

0.25 → **0.075**. It erased 20.2% of 644 real class-A parameter changes and sat
*on* an operator step mode rather than in a gap: 1.333× lands at exactly 0.25
and the comparison is `<=`, so 78 canonical steps (1.2×, 1.25×, 1.333×) were
all declared "the same decision". 0.075 keeps the docstring's own 78-vs-80 case
with more than 3× headroom.

Shipped in the same edit, because it is the same bug: a **relative** tolerance
on **log-unit** keys is reference-arbitrary — the same physical 10 dB step
scores 0.125 at −80→−70 dBm, 0.25 at −40→−30 and 0.50 at +10→+20. No value of
`rel_tol` makes that consistent. dB-valued keys now take an absolute 1 dB
tolerance, and a test pins that the same step is judged identically wherever it
sits.

### 24.3 The corpus bounds are a sample, not a limit

Three shape defects, each measured (§22.1), each fixed:

* **slack** — an observed range is stretched ×3 before it binds. A zero-slack
  `[min, max]` is vacuous on its own training data (0 rejections in 636 runs)
  and rejects 2–22% of held-out usage.
* **one-sided edges** — a sweep edge is bounded only on its dangerous side.
  `min_power_dbm = −40` was refused for being *above* the observed −50, and
  `num_flux_points = 41` for being *below* 101; starting a sweep higher or
  coarser is strictly safer, and bounding it is a category error.
* **no default-derived edge** — a knob nobody varied has a one-point "range",
  and that point is the schema default, so enforcing it enforces the default —
  the one source the docstring promises never to use (69 of 101 corpus edges
  landed exactly on a recorded default). `bounds_for` now takes
  `schema_defaults` and drops a degenerate range that merely echoes one.

### 24.4 The Clause-B lint gets recall — as a WARN tier, and the split is a measurement

The audit was right that the lint is silent rather than clean: it catches units
and explicit window-fractions and almost nothing written in words, which is the
form an author reaches for. Three prose rules were added — word-form position
claims, unqualified size adjectives, and counts of periodic features.

They are **not** drop rules. Run against the shipped v1 pack they flag ten
strings, and reading those ten shows most are false positives: *"one fringe runs
vertical"* is a shape statement, *"instead of a narrow band"* is a contrast, and
*"several periods across the window is a legitimate signature"* exists precisely
to PREVENT a Clause-B misjudgement. P3c measured the judge's weak side to be
**stinginess** (0/12 leniency, 75% stinginess), so thinning the pack on a
regex's guess would worsen the measured weakness to fix a hypothetical one.

So they warn: logged at load, exposed as `lint_warnings`, never removed. And
the docstring's implicit claim is corrected — `lint_dropped == []` means nothing
was *dropped*, which is not the same as clean, and reading it that way is what
let the word-form violations ship.

### 24.5 Left open, with the reason

* **Re-deriving the bands** (§22.4 item 1's remainder) still waits on splitting
  the error-amplification / e→f / vs-flux-calibration populations out of their
  host families. That contamination is upstream of every per-band number, and
  re-deriving from a mixed population would repeat §20.1's mistake politely.
  §23.1 adds a first target: 42% of accepted `resonator_spectroscopy_vs_power`
  targets escalate on one consistency check, which is the same smell.
* **A picture-level label for D-7's stinginess bar** needs the labs' own
  quality flags (`success_shape` and its siblings) surfaced through
  `fit_audit`, which today reports only the scalar `success`. Four of 64 panels
  carry the flag in the archive — enough to see the mechanism, not enough to
  calibrate on.

---

## 25. The chain test: 03 → 05 → 06, walked the way a human walked it (2026-08-09)

§23 replayed runs one at a time. A chain is different, because each step writes
the state the NEXT step is measured under — it is the only place compounding
error can appear. Fourteen real 03 → 05 → 06 resonator chains exist in the
archives; the replay used the tightest: three consecutive run ids on one chip,
same day, nine shared qubits, with the first two steps carrying the operator's
own patches as the answer key.

The replay starts at the state before step 1 (reconstructed from
`patches[].old`) and walks the same three runs, taking the engine's decision at
each: gates pass → keep the node's own write (or forward-write when the node
wrote nothing); suspect → defer with the write kept and flagged; fail → revert.

**Validity is stated, not assumed.** Run k+1's raw data was taken under the
OPERATOR's state after run k. Replaying k+1 is only legitimate while our state
still matches theirs, so the script reports where it stops matching instead of
carrying on quietly.

### 25.1 Result

| step | node | gates | decision | state vs the operator |
|---|---|---|---|---|
| 1 · resonator spectroscopy | 9/9 successful | 9 pass | keep node write | **identical** |
| 2 · vs power | 9/9 successful | 9 pass | keep node write | **identical** |
| 3 · vs flux | 4 successful, 5 failed | 4 pass, 5 fail | forward-write ×4, revert ×5 | **diverged: 12 fields** |

Two steps in, across nine qubits and six watched fields each, the automation's
state is **field-for-field identical** to what the physicist produced — and the
five targets node 06 failed were caught and reverted, none of them wrongly.

### 25.2 Why step 3 diverged, established by content and not by inference

Node 06 reported no write. The first reading — "the operator declined to apply
this run" — could not be trusted, because **2,151 of 3,459 archived runs carry
no `patches` key at all**, so an absent write record is not evidence of an
absent write. (An early pass of this analysis also mis-read an aggregated key
listing as showing the key present; both readings had to be thrown out.)

The runs' own snapshots settle it. Snapshots are post-run, which the control
confirms: #521 → #522 differ by exactly 33 leaves, which is exactly the 33
patches #522 recorded. And #522 → #523 differ by **0 of 10,304 leaves**. Node
06 ran, produced four good fits, and wrote nothing.

The engine's forward path then wrote 12 fields across those four qubits. Those
writes are *faithful* — measured against every archived 06 run that DID write,
the node writes `z.joint_offset`, `resonator.f_01` and `resonator.RF_frequency`,
which is what our `UpdateSpec`s produce. Nothing was written that the node would
not have written. The divergence is not about **what**; it is about **whether**.

### 25.3 What that means, and what changed

The forward path exists because some runners do not self-apply, and there it is
the only way a calibration reaches the chip. But node 06 self-applies in 12 of
27 archived runs, so on this chain the automation was simply **more aggressive
than this operator was**. Both are defensible policies. A report that cannot
tell them apart is not.

So the write is now disclosed: `write_applied` carries
`node_reported_no_write=True` plus the paths, and the plan state counts
`forward_writes`. No behaviour change — a loop whose backend never self-applies
is unaffected — but "the node had nothing to say and we acted anyway" is on the
record instead of looking identical to "we carried the node's own write".

**This is the finding a per-run replay could not produce.** §23 checked 252
targets and every write matched; the chain asked a question §23 never posed —
*would the chip end up where the physicist left it?* — and the answer was yes
for two steps out of three, for a reason worth knowing.
