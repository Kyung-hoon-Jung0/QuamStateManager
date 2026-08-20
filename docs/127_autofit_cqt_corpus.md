# 127 — Auto-calibration verified against the CQT corpus (0.9.9 groundwork)

**Date:** 2026-08-21 · **Branch:** `feat/autofit-cqt-corpus`
**Corpus:** `D:\work\Customer_Codes\CQT\data` — 2,655 real runs (2026-08-13 →
08-19), 20-qubit QDAC-biased tunable-coupler chip, quam 0.6.0 / quam_builder
0.4.0, 54 node families, 1,113 runs carrying `patches[]`. Read-only throughout.
**Env:** conda `cqt` (the customer stack) + the customer's own
`calibration_utils` tree as the replay source root.

The question the user asked: *can the current SM auto-calibration be verified
with this data?* The answer, after executing every offline verification layer
docs/78 defines: **yes — and doing so found and fixed seven real defects.**
Everything below was measured, not reasoned; every number reproduces from the
scripts in the session tmp (`afeas/`).

## 1. What was verified (and the defects it surfaced)

### 1.1 Family dispatch + gates over the whole archive

The full 2,655-run sweep through the real `family_for` + `gates.evaluate_run`
runs in ~26 s. All nine x180-chain families dispatch, including the customer's
QDAC variants (`02e_…_qdac`, `03c_…_qdac` via prefix; `03_…_single` via alias).
**Where the node itself said failed, the gates agree 100%** (352/352 on
qubit_spectroscopy alone). ~36% of the archive (JAZZ ZZ-off 311 runs, the CZ
suite, distortion/cryoscope, RB/XEB, TOF) is outside the autofit scope — a
0.9.9 scope fact, recorded not hidden.

### 1.2 Tier-A refit (the lab's own analysis, replayed) — WORKS, after one line

`run_autofit_replay.py` used raw `_deep_find(node, "parameters")` instead of
THE one unwrap `run_params()` — so every knob fell to defaults, and a
`use_state_discrimination=True` run re-processed its state-only `ds_raw`
through `convert_IQ_to_V` and died on `KeyError: 'I'`. This is precisely the
failure mode `run_params`'s own docstring warns about; the consumer pin
(`test_runner_p0::test_every_consumer_uses_the_one_unwrap`) covered
`run_figure_gen` and `corpus` but not the replay runner. Fixed + pinned.
After the fix the refit **reproduces the stored `opt_amp` to the last digit**
(0.20066197220327142) and regenerates the figure — the (env × source-root ×
run-generation) triple is compatible.

### 1.3 The G3 reader vs this generation's recordings (three renames, one dim)

690 targets sat at `unverifiable` for mechanical reasons, split exactly:

| cause | targets | fix |
|---|---:|---|
| `use_state_discrimination=True` saves the fitted trace as `state`, no I/Q at all | 423 (power_rabi 164 · ramsey 144 · 17a 94 · echo 21) | `_VAR_EQUIVALENTS["I"] = ("state",)` |
| coupler cubes name the PAIR dim plain `qubit` (values are pair names — the same rename `_derive_pairs` documents) | 85 (10_ 50 · 07_ 35) | pair-kind coord fallback `("qubit_pair", "qubit")`; the target-membership check still guarantees identity |
| readout-freq-opt saves the \|g⟩−\|e⟩ distance `D` (the very quantity its fit maximizes), not `snr` | 44 | `_VAR_EQUIVALENTS["snr"] = ("D",)` |
| chevron renamed `state_target` → `state_moving` | 3 | equivalents row |

Each fallback is a **verified rename of the same physical trace** (checked
against the customer's own `analysis.py`), never a different quantity.
`32a/32b` (g/e/f-split cubes) stay honestly unverifiable — structure, not a
rename. Pinned by `TestTraceVarAndPairCoordEquivalents`.

### 1.4 Band recalibration — the §15.2 method on a new lab's corpus

Derivation over every `Plausibility` band: accepted/rejected fit-value
distributions, per family per key. **Every band except ramsey's two was
already at 0 false rejects on this corpus.** Ramsey:

* `freq_offset ∈ [−5, 5] MHz` fired on **26 node-accepted offsets, ~20
  confirmed good** (r² up to 0.997; q14's 39.8 MHz drift was measured, written,
  and the next run reads 60 kHz — the correction landed). Now **±50 MHz**, the
  absurdity envelope: keeps every confirmed real offset, still rejects the one
  true monster (an accepted 9.549 GHz "offset" with r²=−inf, snr=0).
* the `decay` band's **entire measured effect was 26 false alarms** — on 2,655
  runs not one node-REJECTED target carries a decay value (§15.2b's jump-limit
  finding, met again). The node accepts negative/unconstrained T2* because it
  gates on the frequency; G4 killed the whole target over a secondary write.
  Band removed; **write-honesty moved into the update guard**: T2ramsey is
  written only when decay is physical AND (when an error bar exists) error <
  value — the frequency correction always proceeds, and a robot never writes
  T2ramsey = −29 µs (24 accepted runs carry one; one has `decay_error=inf`).
  An ABSENT error field abstains, so errorless generations keep their writes.
* judging moved to the node's own numbers: `osc_amp_snr ≥ 1.0` + `r2 ≥ 0.30`
  metric gates flag 31 of 483 accepted targets and **zero of the r²≥0.5 ones**;
  the spectral floor drops to its honest job (`spectral_min=10` — accepted
  r²≥0.5 fringes bottom out at peak/median 12.2, and garbage reaches 531 via
  slow drift, so the spectrum separates nothing on this family).

After: ramsey 443 pass / 8 fail (all true catches) / 114 suspects.
Pinned by `TestRamseyCqtRecalibration`.

### 1.5 The three-zone feature check (the deep one)

The 123 node-successful qubit_spectroscopy gate-fails decomposed by a
neighbor-corroboration cross-check (does a neighboring accepted run of the
same qubit agree within ±2 MHz?): **98 of 122 corroborated** — the claims were
right and the gate was wrong. Root causes, both measured:

* the prominence-z floor of 5 is a claim about the lab's SNR, and this chip's
  qubit peaks are legitimately shallow — corroborated claims carry z down to
  **2.35**, and z does not separate right claims from wrong ones anyway
  (uncorroborated distribution nearly identical). `FeatureCheck.z_min`
  (per-family, the `spectral_min` §27 pattern) is now 2.0 for this family.
* merely lowering the floor moved the failure: max-of-N on a *flat* window
  already reads z≈3.3, so the argmax below z=5 is unreliable **both ways** —
  91 corroborated-good claims became `wrong_peak` (the argmax was a noise
  spike), and an abstain-only middle zone would have let the no-signal
  corruption through as a mere suspect (the synth ledger caught this).

The shipped design is **three zones**: below `z_min` → provably empty,
`no_signal`; above the module floor → localize as before (`ok`/`wrong_peak`);
between them → **test the claim's own region** — no look-elsewhere penalty at
a known location: smooth by the feature's own width (fwhm), read the deviation
within ±tol of the claim, **sign-agnostic** because the qubit feature in |IQ|
is a dip as often as a peak on this chip (median smoothed deviation −44σ under
the peak assumption — only the node's ROTATED projection has a guaranteed
orientation, which is also why the global argmax kept landing elsewhere).
A noise window tops out at |z_at|≈3.6 under this statistic (control p99 =
3.18), so the threshold of 5 keeps the no-signal corruption a **hard fail**,
not a judge call. The presence probe deliberately keeps the module floor — it
has no claim to test, and "feature present" at a noise maximum would send the
adaptation ladder chasing noise.

After: qubit_spectroscopy 305 pass / 16 node-successful fails — 14
`wrong_peak` on genuinely multi-structure traces (visually confirmed: q9's
history flips between 4.211 and 4.316 GHz and BOTH peaks are on the figure;
q10 carries three structures) and 2 true empty-window catches. The 14 are
correct flags: a robot writing f_01 from a two-peak trace should look twice.
Pinned by `TestThreeZoneFeatureCheck`.

### 1.6 The revert path, at archive scale

Every patch-carrying run × target (1,755 of them) driven through the real
`_sandbox_fix(mode="revert")` and verified byte-deep: each replace-op patch's
dotted path holds `patch["old"]` afterwards and NOTHING else changed. First
pass failed 295 targets: iq_blobs/readout-power patches touch **list**
elements (`confusion_matrix/0/0`) and the sandbox walker treated every dotted
segment as a dict key. Now structural (digit segment indexes a list parent —
the modifier's own rule), `_get_dotted` too, `ValueError`/`IndexError` in the
fix's failure envelope. After: **1,755/1,755 verified, 5,418 patches applied
byte-exact.** The 121 `add`-op / `old=None` patches are skipped by design
(revert only replaces). Pinned by `TestSandboxRevertWalksLists`.

### 1.7 replay_score wired end-to-end on real retry chains

105 real retry sessions → 195 decision points; the deterministic adaptation
ladder as proposer answers 178, **agreement 52%, early moves 4 (runs_saved
4)**. That number is the honest deterministic BASELINE the LLM/vision proposer
(P3c/P3d, still needs an API key) has to beat — the harness itself is now
proven against real data, which is what P8 promised.

### 1.8 numpy-2 `.ptp()` (bonus)

`ndarray.ptp()` was removed in numpy 2.x and the customer env ships 2.5.2 —
the "environmental" 23-failure baseline (16 gates + 4 synth + 3 runner_p2, all
05/08b + flux-cube) was actually a real incompatibility killing the sim
harness of two in-scope families in the customer env. `np.ptp(arr)` works on
both generations. **The cqt-env autofit baseline is now 0 failures.**

## 2. The final state of the sweep

Node-successful gate-fails across the whole archive: 67 (power_rabi 2 ·
qubit_spectroscopy 16 · ramsey 8 · rfo 10 · res_spec 12 · qs_vs_coupler 14 ·
qs_vs_flux 1 · res_vs_coupler 4). The ramsey and qubit_spectroscopy remainders
are adjudicated true catches / honest ambiguity flags (§1.4, §1.5). The
smaller families' remainders (rfo 10, res_spec 12, coupler-flux 18) are
**un-adjudicated** — the same corroboration+figure method applies and is the
natural next pass; they were not touched blind.

## 3. What archived data can NEVER verify (unchanged honesty)

* the closed loop itself (measure → update → re-measure convergence) — needs
  hardware (P9) or the sim backend (CI-covered);
* the vision judge (P3c/P3d) — needs an API key;
* whether a gate's accept would have been *followed* by a good next run — only
  `runs_saved` (§1.7) approximates this offline.

## 4. Tests

New/changed pins: `TestTraceVarAndPairCoordEquivalents` ·
`TestThreeZoneFeatureCheck` · `TestSandboxRevertWalksLists` (all
`test_runner_audit_fixes.py`) · `TestRamseyCqtRecalibration`
(`test_autofit_gates.py`) · `test_the_replay_runner_uses_it_too`
(`test_runner_p0.py`). Full battery in cqt: **445 passed, 9 skipped, 0
failed** across gates/engine/e2e/scenarios/v2loop/synth/runner_p0/p2/
fit_audit/auditor/plan_writer + the audit-fixes file.
