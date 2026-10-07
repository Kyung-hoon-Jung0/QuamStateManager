# 300 — The Interactive tab draws the node's own fit, or says why it does not

**Date:** 2026-10-08 · **Trigger:** v1.1.0 delivery check. The Datasets › Interactive tab drew
fit curves that contradicted the data and the run's own saved figure. A Ramsey fit grew about
2.9× over 30 µs where the lab figure decays, and a qubit-spectroscopy fit was drawn as a dip
where the data and the lab figure show a peak. A wrong curve shown as "the fit" is worse than
no curve.

## The rule now

An overlay is the curve the node fitted, evaluated from what the run saved, or it is not drawn.

1. **Model.** Each family uses the model the node actually used, quoted verbatim in the
   docstring with file and line (`models.py`, the recipes). Where a quote comes from a lab's
   `calibration_utils`, only the generic relative path is named. 45 quotes were checked
   mechanically against the source copies on this machine; there were 0 misses.
2. **Per-run proof.** Where the run stores a number the node computed *from its own curve*,
   SM recomputes it from its reconstruction on the plotted data. `fitcheck.py` holds the
   tolerances. On every archived run the two agree to rounding; a wrong convention misses by
   orders of magnitude.

   | stored number | family |
   |---|---|
   | `fit_r2` (min over detuning signs) | Ramsey |
   | `t2_star = 1e-9·mean(1/decay)` | Ramsey |
   | `osc_amp_snr` | power Rabi |
   | `rms_error` | long-distortion fits |
   | `fitted_curve` | 2Q RB |

   If the recomputed number disagrees, the curve is **withheld** with an in-plot note.
3. **The node's verdict.** Where the node's own figure skips a rejected fit (2Q RB, CZ
   phase), SM does too and says so.
4. **Honest absence.** If the parameters needed for the curve were never saved, there is no
   curve. Instead there is a note and whatever *is* exact (markers, bands, a stored curve).
5. **Runaway fits** (Ramsey decay rate ≤ 0, RB `p > 1`, flux fits that blow up) are still
   the node's curves, so they are drawn. The y axis is held to the data, the node figures'
   own rule, and a note says so.

## Per family: what was wrong, the evidence, what is drawn now

| family | was | evidence | now |
|---|---|---|---|
| **Ramsey** (`ramsey.py`) | `exp(+decay·t)`: grows | Library `oscillation_decay_exp` is `a * np.exp(-t * decay) * …` (qualibration_libs `analysis/models.py:102`, byte-identical in all 12 lab envs). Stored `fit_r2` reproduced exactly on **933/933** qubit-runs. `t2_star` equals `1e-9·mean(1/decay)` on **817/818**. Median R² over 2971 decaying branches: 0.965 with `-t`, −0.83 with `+t`. | `exp(-t·decay)`. Stored `fit_curve` drawn as-is for a mixed-envelope fit. Fit rows paired with data by detuning **sign**, not position. Per-run `fit_r2`/`t2_star` gate. Runaway branch: axis held to the data. |
| **Qubit spectroscopy, `popt` generation** | drawn as a **dip** over the whole sweep, background referenced to the sweep mean | Node model `lorentzian_peak_linbg`, `amp ≥ 0`, `fc = float(np.mean(f))` over the fit window (analysis.py:69-70). The node figure draws only `|det − f0| ≤ fit_window_half_hz` (plotting.py:185-189). Peak-in-window reproduces the stored `r2` within 0.02 on 94% of 608 qubit-runs; the dip version's median R² is −4.1. | Peak, evaluated on and drawn over the node's window; fallback window `max(4·FWHM, 10 MHz)` is the node figure's own. If the data in the window is a dip, the curve is withheld (never flipped). |
| **Qubit spectroscopy, peak-finder generation** (`base_line`/`position`/`width`/`amplitude`) | baseline **array** + Lorentzian | No least-squares fit exists. The legacy figure draws `lorentzian_peak(ds.detuning, amplitude, position, width/2, base_line.mean())` (legacy plotting.py:69-75). | Exactly that curve, named `fit (peak estimate)`. Withheld if the library's own peak/dip rule (`feature_detection.py:85-88`) says dip (8 of 197 archived qubit-runs). |
| **Power Rabi** (`power_rabi.py`) | **sine**: a quarter period off | `fit_oscillation` fits `oscillation` = `a * np.cos(2 * np.pi * f * t + phi) + offset` (`models.py:67`, `fitting.py:310`). Stored `osc_amp_snr` reproduced exactly with cos on **307/307**. Median R² over 903 1-D fits: 0.93 cos vs −0.93 sin. | Cosine; per-run SNR gate. A single-pulse sweep (`nb_of_pulses` of length 1) is drawn as the node fits it, a 1-D trace with its fit, instead of a one-row heatmap. Uses the node's own `isel(nb_of_pulses=0, drop=True)`, analysis.py:523. |
| **Resonator spectroscopy** (`resonator.py`) | Lorentzian `popt` redrawn over the sweep: median R² −1.7 (single), −67 (wide) | The background is referenced to `fc = f.mean()` of an **unsaved** fit window (analysis.py:63). Current generations also overwrite `popt[0] = refined` (line 379) and may have won with a quadratic background whose third coefficient is dropped from `popt`. The node's own figure: "The Lorentzian is NOT drawn - it fits \|S\| alone" (plotting.py:411). | No Lorentzian curve. The f₀ marker and FWHM band (exact) and a note. Where the node saved a circle fit (`ds_port_fit.h5`), its stored model is drawn as stored: solid in the fitted window, dashed extension, `[node: failed]` when it failed (median R² 0.99 on 42 archived panels). |
| **1Q RB** (`rb.py`) | plotted the 12-number **parameter row** `fit_data` (a, offset, decay + covariance) against depth: median R² −19 / −26 | `fit_decay_exp` returns parameters labelled on `fit_vals` (`fitting.py:89-104`). The newer generation stores `param = p, A, B` with `power_law` (`fitting_tools.py:67-68`). | Parameters picked by label; `decay_exp` (`models.py:118`, note the `+t` sign) or the power law. The newer `data_mean` layout now gets a tile. Runaway held. Median R² 1.00. |
| **2Q RB** (`two_qubit_rb.py`) | SM **re-fitted** A and B by least squares around the stored α | Newer generation stores `fit_amplitude`/`fit_alpha`/`fit_offset`; these reproduce the stored `fitted_curve` exactly (max diff 0.0 over 357 panels). Older generation stores only α and the fidelity; SM's refit matched the node on 87/119 panels and not on 32. Node figure draws only `if success:` (plotting.py:245), linear axis by default (`rb_plot_log_x: bool = False`). | Stored A·αᵐ+B only, gated on the stored curve; nothing when the node rejected the fit. Alpha-only runs: no curve, a note, and the tile no longer replaces the node's saved figure (which shows the curve beside it). Axis follows the run's `rb_plot_log_x`. |
| **CZ conditional phase** (`cz_phase.py`) | drew fits the node **rejected** | Node figure: `if hasattr(fit_result, "success") and fit_result.success and …` (plotting.py:106). | Skip + note when `success` is saved and false. |
| **Long-distortion via Ramsey** (`flux_ramsey.py`) | finite-pulse model on runs of the **legacy** sequential fitter | Legacy results carry `optimized_fractions` and fitted the plain step model. Stored `rms_error` reproduced 152/152 (legacy, plain) and 97/97 (global, finite-pulse with `t_pulse_ns = …flux_settle_time_in_ns…`, analysis.py:353). | Model by fitter generation, RMS gate. No longer unavailable when the settle time is missing (the fitter then used the plain model). |
| Long-distortion via qubit spectroscopy, short distortion | correct | `term = amp * np.exp(-t / tau)` (analysis.py:595). RMS reproduced 231/233 (2 flat traces store 0). | Unchanged model + RMS gate + runaway hold. |

## Before / after (harness over every archive, newest 5 runs per type)

*v1* is the coordinator's `fit_overlay_check.py`. It scores dashed `fit…` traces against a
same-length trace, so it cannot score a fit drawn on a sub-window (now qubit spectroscopy) or
solid fits (flux, RB). *v2* (scratch tool) builds runs exactly as the app does (fit_results +
parameters), scores every `fit…`/`circle fit` trace on the points it covers, compares phase in
2π units modulo one turn, and counts withheld/absent overlays as notes. Flux counts double in
v2-after because both panels of the two-panel figure now carry the name `fit`.

| experiment type | v1 before | v1 after | v2 before | v2 after |
|---|---|---|---|---|
| `02_resonator_spectroscopy_wide_pyloop` | -67.54 (n=50, <0.5: 100%) | — | -67.54 (n=50, <0.5: 100%) | no curve (50 notes) |
| `02_resonator_spectroscopy_wide_pyloop_new` | -0.64 (n=22, <0.5: 100%) | — | -0.64 (n=22, <0.5: 100%) | no curve (22 notes) |
| `03_resonator_spectroscopy_single` | -1.70 (n=248, <0.5: 84%) | — | -1.75 (n=260, <0.5: 85%) | circle fit 0.99 (n=42, <0.5: 0%), 260 notes |
| `08_qubit_spectroscopy` | -2.92 (n=58, <0.5: 98%) | -2.14 (n=12, <0.5: 75%) | -2.88 (n=63, <0.5: 98%) | 0.77 (n=63, <0.5: 30%) |
| `11_power_rabi` | — | 0.80 (n=18, <0.5: 44%) | — | 0.80 (n=18, <0.5: 44%) |
| `12_ramsey` | -1.84 (n=242, <0.5: 69%) | 0.97 (n=242, <0.5: 0%) | -1.87 (n=252, <0.5: 71%) | 0.97 (n=252, <0.5: 0%) |
| `19_qubit_flux_short_distortion` | — | — | 0.93 (n=1) | 0.93 (n=2) |
| `19a_qubit_flux_long_distortion_qubitspec` | — | — | 0.38 (n=27, <0.5: 59%) | 0.38 (n=54, <0.5: 59%) |
| `19b_qubit_flux_long_distortion_ramsey` | — | — | 0.65 (n=9, <0.5: 33%) | 0.65 (n=18, <0.5: 33%) |
| `1Q_03_resonator_spectroscopy_single` | -0.92 (n=14, <0.5: 86%) | — | -0.92 (n=14, <0.5: 86%) | no curve (14 notes) |
| `1Q_03_resonator_spectroscopy_single_new` | -0.68 (n=60, <0.5: 77%) | — | -0.68 (n=60, <0.5: 77%) | no curve (60 notes) |
| `1Q_03_resonator_spectroscopy_single_v2` | -0.26 (n=10, <0.5: 100%) | — | -0.26 (n=10, <0.5: 100%) | no curve (10 notes) |
| `1Q_03_resonator_spectroscopy_single_v3` | 0.93 (n=2) | — | 0.93 (n=2) | no curve (2 notes) |
| `1Q_03_resonator_spectroscopy_wide_python_loop` | -52.64 (n=38, <0.5: 100%) | — | -52.64 (n=38, <0.5: 100%) | no curve (38 notes) |
| `1Q_08_qubit_spectroscopy` (peak finder) | -0.10 (n=5, <0.5: 100%) | 0.01 (n=4) | -0.10 (n=5) | 0.01 (n=4), 1 note |
| `1Q_08_qubit_spectroscopy_new` | -2.21 (n=7, <0.5: 100%) | — | -2.21 (n=7) | 0.88 (n=7, <0.5: 14%) |
| `1Q_11_power_rabi` | — | 0.86 (n=4) | — | 0.86 (n=4, <0.5: 0%) |
| `1Q_12_ramsey` | 0.96 (n=18) | 0.98 (n=18) | 0.96 (n=18) | 0.98 (n=18, <0.5: 0%) |
| `1Q_19a_qubit_flux_long_distortion_qubitspec` | — | — | 0.98 (n=5) | 0.98 (n=10) |
| `1Q_19b_qubit_flux_long_distortion_ramsey` | — | — | 0.99 (n=5) | 0.99 (n=10) |
| `1Q_20_qubit_flux_short_distortion` | — | — | 0.40 (n=5) | 0.40 (n=10) |
| `1Q_27_single_qubit_randomized_benchmarking` | — | — | -19.39 (n=6, <0.5: 100%) | 1.00 (n=6, <0.5: 0%) |
| `1Q_28_Qubit_Spectroscopy_E_to_F` (peak finder) | -5.09 (n=3) | -5.07 (n=3) | -5.09 (n=3) | -5.07 (n=3) |
| `1Q_28_Qubit_Spectroscopy_ef` (peak finder) | -0.89 (n=5) | -0.89 (n=5) | -0.89 (n=5) | -0.89 (n=5) |
| `1Q_28_qubit_spectroscopy_e_to_f` (peak finder) | -5.04 (n=1) | -5.02 (n=1) | -5.04 (n=1) | -5.02 (n=1) |
| `1Q_29_power_rabi_ef` | -0.63 (n=2) | 0.11 (n=2) | -0.63 (n=2) | 0.11 (n=2) |
| `20_cz_conditional_phase` | 0.88 (n=5) | 0.88 (n=5) | 0.88 (n=5) | 0.88 (n=5) |
| `20_qubit_flux_short_distortion` | — | — | 0.81 (n=15, <0.5: 47%) | 0.81 (n=30, <0.5: 47%) |
| `21b_coupler_flux_long_distortion_qubitspec` | — | — | 0.04 (n=2) | 0.04 (n=4) |
| `21c_coupler_flux_long_distortion_ramsey` | — | — | 0.07 (n=16, <0.5: 69%) | 0.11 (n=32, <0.5: 69%) |
| `22_two_qubit_standard_rb` (α only) | — | — | 0.99 (n=5) — SM's refit | no curve (5 notes) |
| `27_single_qubit_randomized_benchmarking` | — | — | -25.74 (n=18, <0.5: 100%) | 1.00 (n=96, <0.5: 8%) |
| `27b_single_qubit_randomized_benchmarking_interleaved` | — | — | — (no tile) | 0.99 (n=5) |
| `28_Qubit_Spectroscopy_E_to_F` (peak finder) | -0.85 (n=12) | -0.72 (n=11) | -0.85 (n=12) | -0.72 (n=11), 1 note |
| `28_qubit_spectroscopy_e_to_f` | -3.07 (n=22, <0.5: 100%) | 0.90 (n=2) | -3.05 (n=26, <0.5: 100%) | 0.72 (n=26, <0.5: 31%) |
| `29_power_rabi_ef` | -0.96 (n=14, <0.5: 100%) | 0.97 (n=14, <0.5: 7%) | -0.96 (n=14) | 0.97 (n=14, <0.5: 7%) |
| `2Q_20_cz_conditional_phase` | 0.98 (n=5) | 0.98 (n=5) | 0.98 (n=5) | 0.98 (n=5) |
| `2Q_37_two_qubit_standard_rb` (α only) | — | — | 0.99 (n=5) — SM's refit | no curve (5 notes) |
| `2Q_37b_two_qubit_interleaved_cz_rb` (α only) | — | — | 0.98 (n=5) — SM's refit | no curve (5 notes) |
| `32_cz_conditional_phase` | 0.98 (n=14) | 0.98 (n=14) | 0.98 (n=14) | 0.98 (n=14) |
| `32a_cz_conditional_phase` | 0.78 (n=5) | 0.88 (n=4) | 0.98 (n=5, <0.5: 20%) | 0.98 (n=4, <0.5: 0%), 1 note (rejected) |
| `37_two_qubit_standard_rb` (α only) | — | — | 0.98 (n=11) — SM's refit | no curve (11 notes) |
| `37a_two_qubit_standard_rb` | — | — | 0.98 (n=14) | 0.99 (n=11), 4 notes (rejected) |
| `37b_two_qubit_interleaved_cz_rb` | — | — | 0.99 (n=18) | 0.99 (n=8), 10 notes (rejected / α only) |

The α-only 2Q RB rows scored well *before* because SM's least-squares refit is, by
construction, close to the data. It was SM's fit, not the node's, and on 32 archived panels it
was not the node's curve at all.

## Every remaining low score, with its reason

None of these is an SM defect.

- **Qubit spectroscopy, `popt` (19 rows < 0.5).** SM's R² matches the node's own stored `r2`
  within 0.1 on **19/19**. The node's fit is that poor, mostly `success = 0`, or a frequency
  "success" (peak found) with a poor lineshape.
- **Peak-finder generation (all rows of `1Q_08`, `*_E_to_F`, `1Q_28_*`).** The legacy
  overlay is a peak *estimate* drawn on the mean of an asymmetric-least-squares baseline that
  hugs the lower envelope. Its tails sit below the data, so R² over the sweep is negative by
  construction. Side-by-side renders are identical to the lab figures (#590, #540).
- **Power Rabi (11 rows).** `success = 0` and stored `osc_amp_snr` < 2 (noise fits), or
  saturated / flat state data where the node's own figure shows the same flat curve (#1564,
  #431).
- **Long distortion (19a/19b/21b/21c/20).** The stored RMS error is reproduced (see the
  table above), so the curve is the node's; these are poor fits on noisy traces. One older
  short-distortion fit blows up to 1e13; its axis is now held to the data.
- **1Q RB (8 rows).** Qubits with flat data (R² meaningless) and one `p > 1` runaway
  (`success = 0`, axis now held).
- **CZ (#191, #489).** Genuinely poor node fits: noise across the 0/1 wrap, and an
  un-unwrapped wrap. The lab figures show the same.

## What stays undrawn, and why

- **Resonator Lorentzian curve.** The fit window its background is referenced to was never
  saved, newer nodes overwrite `popt[0]`, and the quadratic coefficient is dropped. The f₀
  marker, FWHM band and (when saved) the circle fit are exact.
- **2Q RB without a stored amplitude/offset.** Only α was saved. The node's saved figure
  stays visible beside the tile.
- **Fits the node rejected** (2Q RB, CZ phase).
- **Any reconstruction that fails its per-run check.** No archived run trips one; the
  mutation sweep shows they fire.
- A qubit-spectroscopy peak curve over data that is a dip, and a legacy peak estimate where
  the library's own rule says dip.

## Click contracts

A fit-overlay change must not move a staged value. Every figure of every changed family was
dumped (newest 5 runs per type per archive) before and after: **0 click contracts changed
across 1,914 clickable figures**. The intended changes were:

- the resonator f₀ marker (a shape);
- single-pulse Rabi drawn 1-D instead of as a one-row heatmap (22 figures; same click
  contract);
- RB tile keys: alpha-only `fig_<pair>` → `survival::<pair>`, plus new `data_mean` 1Q RB
  tiles.

## Pins

`tests/test_fit_overlay_agreement.py` (36 tests) runs on `tests/fixtures/fit_overlays/`: 19
single-target slices of real runs, renamed `q0` / `q0-q1`, with root attributes and free-text
results stripped (1.3 MB). Each fixture renders exactly like its source run, which was checked
trace by trace.

- **Agreement floors per family**, set where the node accepted its fit.
- **Honest-absence cases.**
- **`TestTheCheckBites`:** growing, inverted and quarter-period-shifted curves fail the
  agreement check, and the three stored-number gates fire on the wrong model.
- **Synthetic edges:** sign-order pairing, the mixed-envelope stored curve, a dip under peak
  parameters, a window-referenced background, a 2Q stored-curve mismatch, a circle fit for
  another qubit, runaway holds.

`tests/test_interactive_plots.py`: the model unit tests now pin the corrected conventions,
and the 2Q RB test pins the honest behaviour.

**Mutation sweep: 25/25 red.** Each fix was reverted one at a time. The window-referenced
background mutation was green against the archive fixtures (a narrow sweep centred on the line hides it), so
a synthetic pin was added; it is now red.

**Browser.** `tests/browser/journeys/fit_overlays.cjs` uses real mouse clicks on the
Interactive tab and reads the live Plotly figures, then Full View and a reload. It ran over 14
runs, one or more of every changed family, against a copy of the needed run folders, with 0 JS
errors and every page whole after reload. The screenshots were compared with each run's own
saved figure.

## Not done here

- An e→f run whose node found no peak (`popt` NaN) shows no curve and no note; the lab figure
  shows none either.
- The Interactive keep-alive pool purges far-offscreen tiles of long runs; this is
  pre-existing.
- The 1Q RB y label reads `P(survival)` for a `1 − I` signal; this is pre-existing.
- Not checked in light theme. Notes carry no colour of their own and use the theme's text
  colour.
