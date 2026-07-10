# 47 — AI Fit-Review Co-pilot (design)

> **Status: DESIGN / not built.** This document is the conclusion of a two-stage,
> 28-agent brainstorm + adversarial audit run against the **real** dataset archive
> (`LabA`, `example_lab2`, `example_lab3` folders). It records *what* to build, *why the
> obvious version is wrong*, and the *go/no-go* measurement that must precede any
> code. No routes, templates, or modules described here exist yet. The numbered
> empirical claims in the Appendix were measured on real runs and are the load-
> bearing evidence — re-verify them before relying on any one of them.

## Phase-0 empirical results (2026-06-30) — what the POC actually found

A manufactured-marker POC was run on real data (4 families × correct/subtle-wrong/
gross-wrong, rendered from h5 with a controlled fit marker; opaque ids; the binding
metric = false-accept on wrong markers). Harnesses in `.tmp_poc/`. Headline findings,
which **refine the design below**:

- **A frontier vision model works; cheap/local ones do not (as vision).** Claude
  Opus 4.8 on the figure: true false-accept **0/136** (excluding one verified
  bad-node-fit run), and it even *caught* node-fit errors. Claude Haiku: discriminates
  but ~17% false-accept (all in the hard 2-D families). Local `qwen2.5vl:7b`:
  **blanket-rejected all 57 figures** (incl. perfect fits) — zero discrimination.
- **Representation, not capability, was the wall for the weak model.** Swapping the
  image for a **numeric candidate table** made `qwen2.5vl:7b` recover discrimination
  on clean 1-D (accepted 5/5 correct, rejected gross) — but it still blanket-rejects
  2-D tables, and Haiku on tables makes **arithmetic errors** (read 10.4 MHz as
  1.04 MHz → false-accept). So the LLM must never own the precise numeric comparison.
- **Deterministic code beats every cheap LLM on faithful-channel families.** A
  code localizer (schema-faithful channel via `h5reader` + `scipy.find_peaks`) plus
  an exact `|fit − feature| < tol` arithmetic gate resolves `qubit_spec` and
  `qubit_vs_power` at **0% false-accept, accepting all correct, with no LLM and $0** —
  even the noisy 2-D `qubit_vs_power`. Invoking the LLM there only *adds* errors.
- **`resonator` must DEFER:** the node fits a rotated complex-S21 channel, so a naive
  `argmin(IQ_abs)` is **14.4 MHz** off — the data-derived feature is wrong unless the
  node's exact channel is reproduced. **`res_vs_power` is the one genuinely hard
  family** (dressed-vs-bare branch, ~3–5 MHz apart, both real dips): code heuristic
  ~37% FA, Haiku 25–33%, qwen useless — only Opus-vision handles it.

**Refined architecture (supersedes "an AI judges every figure"):**

| Tier | Who | Scope | Cost |
|------|-----|-------|------|
| 1 | **deterministic CODE** (schema-faithful localizer + arithmetic gate + a scalar-SNR/`NO_FEATURE` gate, since LLMs never abstain — 0/218) | the clear majority; 0% false-accept | $0 |
| 2 | **frontier-vision model** (Opus-class, BYO key) | ONLY the genuine-ambiguity residual (e.g. `res_vs_power` branch) | low volume, ~$0.03/call |
| 3 | **DEFER to human** | non-reproducible channels (`resonator`) + low-SNR garbage | — |

The cheap/local LLM is **not** recommended as an authority (code wins the easy cases,
frontier wins the hard ones). The applied value stays the node's own number. The
per-family schema-faithful localizer is real but bounded engineering (the existing
~28 `core/interactive_plots/recipes/` plugin pattern). Hard precondition for every
scheme: reproduce the node's conditioned signal channel, or DEFER.

## Problem

Researchers run many calibration nodes (often overnight: a full `1Q` / `2Q`
sweep), and the automated fit in each node **is not always right** — it locks onto
a sidelobe, picks the wrong peak, or reports `success=True` on a fit that a human
eye would reject. Today the only remedy is a human opening each run's figure,
eyeballing whether the fit is trustworthy, and — if so — clicking the fitted value
into the QUAM state via the Datasets **Results** tab (`fit_targets.py` →
apply-popup → `/field/edit-batch`).

The ask, from many users: **let an AI do that eyeball step.** Point it at a few
selected runs, have it judge each fit against the figure, and update the state with
the good values — because reading a figure "is not hard," and each experiment can
carry a tailored base prompt + a few reference figures encoding the domain
knowledge.

This is feasible — but **only if framed correctly**, and the naïve framing
("autonomous AI reads the figure and writes the calibration number") is actively
dangerous. The rest of this doc is the correct framing and the evidence for it.

## The one hard sub-problem, and the reframe that dissolves it

The whole feature reduces to a single hard question:

> How do you turn the AI's qualitative visual judgment into a **precise,
> calibration-grade number** to write into `state.json`?

f_01 needs ~kHz precision on a GHz axis (≈1e-6 relative); a vision model reading
pixels is hopeless at that. The instinct is to make the model *more precise* — an
iterative "draw a crosshair, re-judge, converge" loop, or a re-fit driven by the
model's coordinate guess.

**The reframe (and the central decision of this design):**

> The model must **never emit a calibration number.** The precise number already
> exists — it is the experiment's **own fitted value**. The AI's only durable job
> is a **trust verdict** (`accept` / `reject` / `abstain`) on whether that number
> is believable, by visually cross-checking the figure. The AI contributes **zero
> numeric error** because it never touches a magnitude.

This is not a compromise; the data below shows it is the *only* version that is
both accurate and buildable. It also happens to be the **lowest-overhead** version
(it reuses the entire existing write path) and the most **backend-agnostic** one
(the model's job shrinks to signature recognition, which even a weak local model
does acceptably — see [Backend](#backend--provider-agnostic)).

## What the data says (why the obvious versions fail)

Three "obvious" mechanisms for extracting a precise number were each killed by
measurement on real runs. Full numbers in the [Appendix](#appendix--empirical-measurements).

### 1. The node's stored frequency *is already* `argmax` of the swept array

On real LabA qubit-spectroscopy runs the node's stored `f_01` is **byte-identical**
to `full_freq[argmax(signal)]` — 0.0 Hz apart (Appendix A1). So any pipeline that
"snaps a crosshair to the nearest data sample" converges toward a number the node
**already hands you for free**. The snap insight is correct; its output is the node
value.

### 2. A generic State-Manager-side re-fit misses by **MHz**

Re-running `scipy.curve_fit` with a `models.py` Lorentzian on the raw `ds_raw.h5`
arrays — the "math delivers precision" idea — was measured **off by 2.5–17.9 MHz**
against a ~100 Hz budget on a real resonator run, and **64.6 kHz** off on a qubit-
spec run (worse than just taking the bin). Reason: the node fits a *conditioned
signal* (rotated quadrature / complex S21) with an absolute-Hz parameterization
that `models.py` does not encode. **To match the node you must re-implement the
node's analysis, per family and per schema** — the exact unbounded maintenance the
re-fit was meant to avoid. (Appendix A2.)

> Corollary: "math delivers precision" is true **only when the math reproduces the
> node's pipeline.** Otherwise it delivers a precise, auditable, *wrong* number
> wearing a trust badge — the worst possible failure.

### 3. The render-feedback servo loop is mathematically redundant **and** fails

Once a crosshair snaps to an array index, the precise value is `x_array[i]` — an
integer pick the model never refines. You **cannot iterate a vision model below your
sampling resolution**, and the sweep step on real data is **100–250 kHz** =
100–250× the kHz budget. No number of iterations crosses that floor. Worse, the loop
only *applies* to clean 1-D peaks (where it is redundant) and **not** to the hard
2-D targets (chevron, IQ-blobs, RB) where disambiguation would actually matter.
**Verdict: drop the iterative servo.** The one salvageable idea — *snap the
overlay to the data array, never the pixel guess* — survives, but **as a static
human-facing confirmation overlay**, not a precision engine.

### 4. The coverage cliff

Of the 11 writable families in `FIT_TARGET_MAP`, only a handful are clean
closed-form 1-D peaks, and `models.py` ships only ≈5 closed-form models
(`lorentzian_dip_linbg`, `lorentzian_peak_linbg`, `sin_osc`, `osc_decay`,
`multiexp_decay`). The families that dominate **real nightly volume** —
flux-distortion ramsey, `iq_blobs`, `chevron`, RB, CR, `cz` — have **no 1-D
re-fit** at all (Appendix A3). Even `power_rabi`, nominally a sine, is a **2-D
error-amplification scan** in real data with no stored sine coefficients
(Appendix A4). So an independent re-derived number is *structurally impossible*
for roughly half the writable surface and most of the volume.

**Net:** judge-only (precision = the node's own value) is not merely the safe
baseline — for most of the writable surface it is the **only honest mechanism**.

## Architecture

```
user selects a few runs / a small run_id range   (NOT all-folders; scoping = the user)
        │
        ▼
 for each (run, qubit, fit_key) in resolve_fit_targets(run):      ← curated, finite, unit-validated
        │     candidate set; AI never invents a dot-path or a value
        ▼
 build vision bundle:  run PNG (or iplot-reconstructed figure)
                     + per-FAMILY base prompt
                     + 2–5 reference exemplars (shape + edge cases)
                     + cross-check context: node's claimed value, success, r2   ← context, NOT a filter
        │
        ▼
 AI returns a VERDICT object  { verdict: accept|reject|abstain, signature_ok,
                                claimed_value_consistent, reason, rough_location? }
        │     ── there is NO field for a replacement number; the schema forbids it ──
        ▼
 accept → stage the row into the EXISTING apply-popup with value = node's own
          resolve_fit_targets value, verbatim   (ai_verdict=accept + reason as a label)
 reject/abstain → NO Apply affordance; surfaced as a flag in the morning review
          queue with the reason; recorded to a side-file for the golden corpus
        │
        ▼
 human reviews over coffee → clicks Apply → /field/edit-batch → WORKING COPY
        │     (atomic, rollback, cross-chip guard, archive-write-blocked — all existing)
        ▼
 human clicks Apply-to-live → dual-staleness gate → state.json
```

The AI route is **read-only**: it returns JSON and never reaches `modifier` /
`saver`. "No code path exists from the AI to a write" is an acceptance criterion,
not a config toggle.

### The verdict contract (the AI never emits a number)

The model is asked for a **discrete judgment**, the thing vision is good at, and is
*structurally prevented* from emitting a magnitude:

```jsonc
{
  "verdict": "accept" | "reject" | "abstain",
  "signature_ok": true,                    // is this the right shape for the family?
  "claimed_value_consistent": true,        // does the node's claimed feature sit where the figure shows it?
  "reason": "single clean Lorentzian dip; node peak coincides with the figure minimum",
  "rough_location": "dip ~30% into the detuning range, left of centre"   // ADVISORY/AUDIT ONLY — never converted to a number, never written
}
```

`rough_location` exists only for the human's audit trail and (later) for the
golden corpus; it is never parsed into a value. `success` / `r2` are fed **as
cross-check context, never as a pre-filter** — a `success=True` run is still shown
to the AI so a confidently-wrong fit can be caught. That is the whole point of the
feature.

## Precision sources and the default-DENY 4-tier ladder

Every family sits at exactly one tier. **Default placement is the bottom tier;**
a family is promoted only when it carries committed evidence that clears the higher
tier's bar (see [Proving accuracy](#proving-the-non-negotiable-accuracy)).

| Tier | Precision source | Mechanism | Valid for |
|------|------------------|-----------|-----------|
| **1 — auto-propose** | node's own fit value (verbatim) | AI `accept` → stage node value into apply-popup | families whose false-accept rate clears the bar on a *manufactured* wrong-fit set |
| **2 — click-then-refit** | scipy seeded by a human click | AI flags wrong-peak → human clicks the true peak → a re-fit that **reproduces the node's model** delivers the sub-step number | the ≈3–4 clean closed-form 1-D **frequency** targets *only*, and only after golden-gating |
| **3 — click-terminal** | human click = array sample | AI region-boxes → human clicks the exact sample/cell | families whose **sweep step < budget** (amplitudes, thresholds, heatmap cells) — proven per-family by the sweep-step:budget ratio |
| **4 — copy-only (default)** | node value, **shown but not appliable** | explicit "unverified" badge, no Apply affordance | everything without published evidence; all non-refittable 2-D / cluster / RB targets |

Two consequences worth stating plainly:

- **Tier 2 is a narrow, deferred, golden-gated nicety**, not the engine. On the
  clean frequency families the node value already equals the argmax bin
  (Appendix A1), so a re-fit buys almost nothing; and where it *would* help
  (wrong-peak), it must replicate the node's exact signal/model or it injects MHz
  (Appendix A2). Ship Tier 1 + Tier 3/4 first; treat Tier 2 as Phase 3.
- A frequency family that *fails* Tier 1 cannot simply fall to "human click,"
  because a click on a 250 kHz grid is itself 250× over the f_01 budget. Its only
  honest fallback is **Tier 2** (click localizes the peak, scipy delivers the
  sub-step number) — or Tier 4 if Tier 2 isn't available for that family.

## Scope: which families get what

Grounded in `FIT_TARGET_MAP` (11 writable prefixes) and the audit's family analysis:

| Family (prefix) | Writable target | Geometry | Honest tier ceiling |
|---|---|---|---|
| `1Q_03_resonator_spectroscopy` | `resonator.f_01` | 1-D Lorentzian dip | T1 accept; T2 refit candidate |
| `1Q_05_..._vs_power` | `resonator.f_01` | 1-D dip, **regime-morphing** (punch-through) | T1 with regime-keyed exemplars |
| `1Q_08_qubit_spectroscopy` | `f_01` (+ iw_angle, x180_amp, sat_amp) | 1-D Lorentzian peak | T1 accept; T2 refit candidate (f_01 only) |
| `1Q_09_..._vs_flux` | `f_01` | **2-D ridge apex** | T1 judge-only (no localize) |
| `1Q_11_power_rabi` | `x180.amplitude` | **2-D error-amplification** | T1 judge-only (node `opt_amp` is the number) |
| `1Q_13_drag_calibration` | `x180.alpha` | scalar from algorithm | T1 judge-only |
| `1Q_15a_readout_frequency_optimization` | `resonator.f_01` | 1-D optimum | T1 accept; T2 refit candidate |
| `1Q_15b_readout_power_optimization` | `readout.amplitude`, iw_angle | optimization curve | T3 (amp step < budget) |
| `1Q_16_iq_blobs` | `integration_weights_angle` | **2-D cluster rotation** | T1 judge-only / T3 click |
| `1Q_30a_gef_readout_power_optimization` | `readout_GEF.amplitude` | 3-cluster optimization | T1 judge-only / T3 |
| `2Q_20b_cz_conditional_phase_error_amp` | `macros.{op}.flux_pulse_qubit.amplitude` | error-amp | T1 judge-only / T3 |

**Autonomous calibration-grade *numbers* are promised only for the ≈3–4 closed-form
1-D frequency targets (03, 08, 15a, conditionally 05).** Everything else is
**judge-only**: the AI gates the *node's own number* behind a mandatory human Apply.
Do not let the framing "AI updates the state" leak onto families where no
independent number can be produced or proven.

## Proving the non-negotiable accuracy

Accuracy is the user's non-negotiable. The audit found the naïve methodology
commits three provability sins; the design fixes each.

### Two numbers per family, never one

"Accuracy" silently conflates two different quantities with incompatible proofs:

- **(A) applied-number error** `|applied − truth|` — in judge-only this is **0 by
  construction** (the applied value *is* the node's float; a unit test asserts
  `staged == resolve_fit_targets(run)[q][k]["value"]` byte-for-byte). Trivially met.
- **(B) false-ACCEPT rate** `P(accept | the fit is actually wrong)` — the **real**
  risk, the **same for all designs**, and the **binding** constraint. There is **no
  in-repo oracle** for it.

The hard bar is on **(B)**. (A) being trivially zero must never be reported as
"accuracy verified" — that is exactly how a precise-but-wrong fit sails through.

### Manufacture the wrong-fit set — don't wait for it

A label set built from human-*confirmed* runs is dominated by **good** fits, so a
false-accept rate computed from it is statistically vacuous *precisely where it
matters*. The wrong-fit subset must be **constructed**:

1. **Synthetic corruption** — take known-good runs, programmatically perturb the
   node's stored fit onto a sidelobe / second peak / wrong detuning-sign, and
   require the gate to **reject** them. A deterministic, CI-able false-accept test
   with as many positives as you want.
2. **Mined re-calibration jumps** — scan param-/state-history for an `f_01` that
   was later re-calibrated with a *large* jump (the chip didn't physically move
   that much → the earlier fit was probably wrong). Real wrong-fit examples with a
   later "truth."

### The frequency oracle is not a human click

Human-truth for a frequency via a Plotly click = an array sample = **quantized to
the 250 kHz sweep grid** — 250× coarser than the ±1 kHz budget it is meant to
certify. So for **frequency** families the oracle must be a **second independent
scipy fit** (the human only adjudicates *which peak* — a discrete choice the grid
resolves fine). For **amplitude/threshold** families the click **is** valid,
because their sweep step is finer than the budget. Publish the per-family
**sweep-step : budget ratio** as the precondition deciding which proof method even
applies.

### Two CI artifacts, never conflated

- **Regression gate** — `np.allclose(refit, recorded_refit, family_rtol)`, cloning
  the `tests/golden/waveform_golden.json` pattern (`run_waveform_golden.py` +
  `test_waveform_golden.py`). Blocks scipy/quam drift. Cheap, every commit. Proves
  **reproducibility, not correctness.**
- **Accuracy ledger** — the published per-family **(A) value-error distribution +
  (B) false-accept rate + sample count + confidence interval**, recomputed only
  when labels change. A thin family must *read* as visibly thin.

> **Achievability verdict.** "Non-negotiable accuracy" is achievable **only** as
> *"the applied number is the node's (or a node-faithful re-fit's) scipy float,
> never the model's pixel guess, behind a published, CI-gated false-accept rate."*
> It is **not** achievable as *"the AI reads a calibration number off a figure,"*
> for any family, ever.

## Domain knowledge: base prompts + the exemplar base_DB

Per-**physics-family** base prompts + a small **base_DB of 2–5 reference figures**
(typical + edge cases) per family, ground the verdict by comparison. Both are
keyed by the **normalized** experiment name (strip the leading `1Q_`/`2Q_`/`NN_`
index + case-fold) so the ~88 real, drifting node names collapse into ~12–15
families, **co-keyed to `FIT_TARGET_MAP`** so the writable-target set and the
judging prompt cannot drift apart. Prompts + exemplars are versioned data files
(editable by a calibration engineer without a code change / wheel rebuild), not
model weights.

The representativeness thesis ("read the signature without absolute axis values,
then judge roughly where the feature sits, so exemplars generalize across chips")
**partly holds**, and the design must respect the boundary the audit drew:

- ✅ **Clause A** — *signature recognition is scale-free and transfers across
  chips.* Solid. This underwrites the **accept** path.
- ⚠️ **Clause B** — *exemplars therefore generalize for localization* — does **not**
  follow. A feature's *fractional* position is an artifact of the sweep window the
  experimenter chose, not physics. **Fix:** exemplars teach **shape + relative
  geometry only** ("the true peak is the tallest narrow feature; a sidelobe is
  <½ height and >2 linewidths away"), never absolute or fractional-of-axis
  position; localization is expressed **relative to the visible feature**, then
  data-snapped.
- ⚠️ **Regime-shifting families** (res-spec-vs-power punch-through, power-broadened
  spec, beating Rabi) need **edge-case exemplars** or a coarse `run.parameters`
  regime-key — itself an admission against pure cross-chip transfer.
- ❌ **Fails** on the 2-D / cluster families (`1Q_09`, `1Q_16`, `1Q_30a`) — no
  single axis the feature "sits on." Those are **signature-trust-only**.
- 📉 The thesis is **strongest where the node fit already works** (clean single
  peak) and **weakest where the co-pilot is most needed** (multi-peak wrong-peak).
  Prove generalization with a **chip-held-out** golden split, never a run-held-out
  one.

## Backend — provider-agnostic

One **OpenAI-compatible `/v1/chat/completions`** seam (stdlib `urllib`, **no new
pip dependency**, no bundle growth) covers Claude vision (header swap), any cloud
gateway, and a local Ollama / llama.cpp VLM — selected/persisted in
`instance/ai_analysis.json`, mirroring `config_generator.get/set_selected_env`. The
model server is **external** (the user runs `ollama serve`), exactly like the
Generate-Config conda env is BYO; never bundle a model.

**Why this reframe helps the local-model case.** Because the AI never produces a
number, the local-vs-cloud question collapses to *"is the model's coarse trust
**verdict** good enough?"* — i.e. signature recognition (Clause A), the thing a
weak local VLM does acceptably. The go/no-go is a **measured false-accept rate**,
not a guess. Position the local-CPU path as **async / overnight-batch**
("run 1Q/2Q all night, hand the runs to the AI, review over coffee"), never a hot
path; mind host CPU contention with acquisition/fitting (core-pin / separate box /
GPU).

## Safety / write path (all reused, nothing new)

| Guarantee | Mechanism (existing) |
|---|---|
| AI cannot invent a path | `resolve_fit_targets` is the *only* candidate source; AI sees a finite curated set |
| AI cannot emit a number | verdict schema has no magnitude field |
| Atomic, all-or-nothing write | `modifier.batch_set` + `_rollback` via `/field/edit-batch` |
| No write to live | writes land in the **working copy**; live needs explicit Apply-to-live + **dual staleness gate** |
| No cross-chip mis-apply | `expect_chip` / `force_chip` on the batch payload |
| No archive corruption | `_archive_write_blocked` on dataset-origin runs; verdicts go to a **side-file**, never `data.json` |
| Range backstop | a deterministic per-`fit_key` sanity-range check (a *safety floor on the write*, **not** a triage filter) rejects an out-of-range value even on an `accept` |
| Human is the final gate | mandatory Apply click; default action on `reject`/`abstain` is **skip** |

## What we are explicitly NOT building

- ❌ **The iterative render→re-judge→converge servo.** Mathematically redundant
  after snap; error floor is one sweep step (100–250 kHz); highest compute of any
  option; doesn't even apply to the hard 2-D targets. *Keep only* the static
  "overlay the node/refit value on the figure so a human rejects an obvious miss in
  one glance."
- ❌ **A generic State-Manager-side re-fit as a general precision engine.** Misses
  by MHz unless it reproduces the node's pipeline per family/schema. Allowed *only*
  as a narrow, golden-gated, node-faithful Tier-2 overlay for the ≈3–4 frequency
  targets, with a hard invariant `|refit − node_f0| < node_fwhm` else **abstain**.
- ❌ **`success`-flag pre-filtering of which runs the AI sees.** Defeats the
  feature's reason to exist (catching `success=True`-but-wrong fits). Scoping is
  done by the **user's run selection**; `success` is cross-check context only.
- ❌ **Auto-apply / "confidence > X" gating.** The API exposes no calibrated
  confidence; a self-reported `confidence` is generated text. No auto-write path
  exists in code.
- ❌ **An all-folders scan.** The AI runs only on the user's selected handful.

## MVP and phasing

- **Phase 0 — go/no-go probe (do this before any feature code).** Build the
  false-accept measurement harness: take real clean runs from a couple of 1-D
  families, **manufacture** a wrong-fit set (synthetic sidelobe/wrong-peak
  corruption), run the **verdict** prompt (Claude vision *and*, if available, a
  local Ollama VLM) with the exemplar pack, and **measure false-accept /
  false-reject.** This single number decides whether the feature — and whether a
  local model — is worth building. It directly answers "does the exemplar-grounded
  weak model clear the bar?"
- **Phase 1 — judge-only MVP (Tier 1 + Tier 4).** Default-OFF settings toggle;
  a few clean 1-D families; AI `accept` stages the **node's own value** into the
  existing apply-popup; `reject`/`abstain` → flagged morning queue; verdicts to a
  side-file; **no re-fit, no render loop, no auto-write.** Async/overnight over a
  user-selected handful of runs.
- **Phase 2 — Tier 3 (AI region-box → human click).** For amplitude/threshold/2-D
  targets where the click is array-exact and the sweep step beats the budget;
  reuse the plot-click-confirm popup + interactive Plotly.
- **Phase 3 — golden corpus + Tier 2 (optional).** Active-learning capture of
  human corrections; published per-family false-accept ledger; node-faithful re-fit
  for the ≈3–4 frequency targets, behind the golden gate and the
  `|refit − node_f0| < node_fwhm` invariant.

## Reuse map

| Need | Existing component |
|---|---|
| Candidate enumeration (path + scaled value) | `core/fit_targets.py` `resolve_fit_targets` / `FIT_TARGET_MAP` |
| Atomic write into working copy | `web/routes.py` `/field/edit-batch` → `core/modifier.py` `batch_set` / `_rollback` |
| Human confirm gesture | the plot-click confirm popup (docs/36) + apply-popup |
| Figure bytes (vision input) | static PNG via `DatasetStore.get_figure_path` (path-safe) |
| Interactive figure + click-to-value | Interactive Replot (docs/46) + recipe `clickable` specs |
| Raw arrays for any re-fit | `core/interactive_plots/h5reader.py` `load_dataset` + `models.py` |
| Backend settings persistence | `config_generator` `instance/*.json` pattern |
| Golden / accuracy harness | `tests/golden/waveform_golden.json` + `generator/run_waveform_golden.py` + `tests/test_waveform_golden.py` |
| Live-file safety, working copy, staleness | docs/28 (safe_io / working_copy) — unchanged |

## Appendix — empirical measurements

Measured by the brainstorm audit agents on the real dataset archive. Treat as
evidence to re-verify, not as committed test fixtures.

- **A1 — node freq = argmax bin.** `LabA/2026-05-16/#95_1Q_08_qubit_spectroscopy_233303`:
  detuning `n=400`, span 99.75 MHz, **step ≈ 250 kHz**; the node `f_01`
  equals `full_freq[argmax]` to **0.0 Hz**.
  A second node's freq matches its argmax bin to **0.0 Hz** too. (So "snap to the
  array" returns the node value.)
- **A2 — generic re-fit misses by MHz.** `example_lab2/2026-06-10/#1_1Q_03_resonator_spectroscopy_single`:
  node `popt[0]` (= `fit_results.frequency`, `r2 = 0.968`). Generic
  `curve_fit` Lorentzian on raw `IQ_abs` vs detuning: dip
  (**−17.9 MHz**), peak (**+2.57 MHz**, stderr **127 kHz**) — vs a
  ~100 Hz budget. On qubit-spec `#95` a naïve Lorentzian-from-argmax landed
  **64.6 kHz** off (worse than the bin). Resonator `1Q_03`: 5/6 qubits on-grid
  (step 100 kHz), the 6th 38.7 kHz off.
- **A3 — coverage cliff (969-run histogram).** Top families by volume:
  `flux_long_distortion_ramsey` (72), `ramsey_vs_flux_calibration` (67),
  `flux_distortion_qubitspec` (44), `iq_blobs` (72), `chevron` (39),
  `two_qubit_standard_rb` (23), `CR_time_rabi_QST` (22), `cz` variants (60+).
  **None** are single-Lorentzian/single-cosine. `models.py` ships ≈5 closed-form 1-D
  models.
- **A4 — power_rabi is 2-D.** `example_lab3/2026-06-28/#29_11_power_rabi`: `ds_raw`
  is 2-D (`nb_of_pulses=10` × `amp_prefactor=80`); `ds_fit` has `opt_amp_prefactor`,
  `opt_amp`, `success` — **no `popt`, no sine coefficients**. An x-window hint is
  meaningless; the node's `opt_amp` is the number.
- **A5 — schema fragmentation.** At least **three** distinct `1Q_03` `ds_fit`
  schemas on disk (old `position/width/amplitude/base_line` on detuning;
  `f0/fwhm/r2/popt` in **absolute Hz**; a third popt-over-detuning). A re-fit must
  detect schema + absolute-vs-detuning (differ by ~7 GHz) or it injects gross error.
- **A6 — missing raw.** 8/969 runs have no `ds_raw.h5`; `h5reader` skips vars
  >50M elements (single-shot IQ-blob / large 2-D). Missing/oversized raw → **abstain
  with a reason**, never a silent drop.

## Related

- docs/36 — plot-click confirm popup (the human gesture reused)
- docs/40 — Pulses page (`waveform_synth` golden pattern, the accuracy-harness model)
- docs/46 — Interactive Replot (figure reconstruction + click-to-value)
- docs/28 — conflict-safe I/O, working copy, staleness gates (the write safety floor)
- `core/fit_targets.py` — the curated writable-target map this feature is constrained to
