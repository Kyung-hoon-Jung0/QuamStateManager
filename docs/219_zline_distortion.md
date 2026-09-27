# docs/219 — Z-line distortion: the exponential and FIR filters drawn together, judged per QOP model

2026-09-26/27, branch `w7/zline` (4 commits), merged into `integ/w7` as `7ff16b2`
(branch head `9848d9f`, no merge conflict; see docs/224).

## 1. What shipped

A new page, **Live State Edit > Z-line distortion** (`/zline`), in the sidebar next to Pulses.

- **Lines:** every qubit z line and every pair coupler. The z channel's `opx_output` is followed
  through its pointer chain to the LF analog-output port.
- **Step figure:** four curves on a log time axis: ideal, exponential only, FIR only, and both
  filters together. Since `6d93e23` all four are shown by default (the two component curves dotted).
- **Pulse figure:** the line's own flux pulses, ideal vs through both filters, with an operation selector.
- **Model selector:** QOP >= 3.5 sum form (default) or the QOP <= 3.4 cascade. The table carries a
  per-model verdict column, "QOP >= 3.5 / <= 3.4" (ok / UNSTABLE).
- **Honest failure:** each of these is a block note and no curve: an exponential set that is unstable
  or improper under the selected model, tau <= 0, tau in seconds, too many IIR stages, FIR taps
  outside (-2, 2), all-zero or non-numeric taps, `feedback_filter` set, high_pass together with
  dc_gain, a dangling pointer, an MW port, a tuple port, or any unexpected model exception. One bad
  port is one note on its own line, never a 500 for the page.

## 2. What was measured

KRS 5Q rig copy, line q1 (5 exponentials + 48 taps, FIR sum 0.9997), sum model. Implementer-measured
on rig w7-zline, then re-measured exactly by the independent verifier on rig w7v-zline:

| quantity | value |
|---|---|
| first sample | 0.5682 |
| peak | 1.1539 at 2.0 ns |
| at 1 ms | 0.99979 |
| DC limit | 0.99972 |
| const pulse | ideal 0.4000 V -> output 0.4616 V (x1.154) |
| note shown | sum \|FIR taps\| = 1.151, above QM's recommended 1 |
| cascade (verifier only) | first 0.5664, peak 1.1517 |

- **Physics, independent verifier re-derivation** (imports nothing from `zline_filters`: mpmath
  polyroots at 50 digits, zpk2ss, bilinear, dlsim for the sum model; a hand-derived first-order
  bilinear per stage for the cascade; `np.convolve` for the FIR). Max |diff| against served
  `/zline/data` over 2e6 samples: q1 sum 5.4e-8, q1 cascade 1.1e-7, q3 sum 7.0e-8, q3 cascade 5.9e-11.
- **Server time (verifier, re-verification at 6d93e23):** `/zline/data` warm 9-16 ms; `/zline` HX
  table on big30x 23 ms warm. First verification: GET `/zline` 0.019 s, `/zline/data` cascade 0.010 s
  warm (cold first 0.23 s), median of 5.
- **Interactions, real Chrome (verifier, medians, ms):**

| | open | row click | 15-row burst | Enter | model -> cascade / sum | op switch | reload | back |
|---|---|---|---|---|---|---|---|---|
| big30x | 353 | 265.5 | 444 | 67 | 219 / 218 | 76 | 348 | 486 |
| krs5 | 447 | 392 | 90 | 115 | 313 / 313 | 81 | 426 | 527 |

  There is no before number: `/zline` is 404 on the base. Neighbours before vs after on big30x:
  Pulses ttfb 22 vs 19 ms, settle 608 vs 594 ms; Live Edit `/bulk` ttfb 6508 vs 5775 ms, settle
  43030 vs 33836 ms (the verifier reports no regression).

## 3. Model and provenance

`core/zline_filters.py` is pure (numpy + scipy). Its docstring tags every rule `[paper: ...]` or
`[derived]`; its quotes are pinned verbatim by `TestQuotesVerbatim` (one exception, section 7).

**Carries a verbatim quote** (QM docs `Guides/output_filter.md`, `opx1000_fems.md`, or qualang_tools):
- Rate and size: "The filters always operate at 2 GSa/s, and each FIR tap is 0.5 ns."; "one FIR filter
  with 48 taps, and 6 IIR filters"; "Feedforward taps are limited to the range (-2,2)."; "time
  constants ranging from 1 ns to 1 second".
- The distortion's step response s(t) = (A_dc + sum_n A_n*exp(-t/tau_n)) * u(t), default A_dc = 1
  (both quoted in the docstring as the docs write them).
- high_pass: usable "only when the `exponential_dc_gain` is not set", and quoted as equivalent to
  setting `exponential_dc_gain` to 0 and adding (1, tau_hp) to the exponential list.
- Cascade (QOP <= 3.4): "The filters are cascaded sequentially, such that the output of one filter is
  the input of the next one."; its stage has no dc_gain field ("To configure the IIR filters, set the
  filter parameters in the `exponential` and `high-pass` fields."); the leaky high-pass: "Note that a
  decay of 0.5 seconds is automatically added to the high-pass filter." (`HP_AUTO_DECAY_NS = 5e8`),
  built in the docs' leaky form with A_dc = tau_hp / 0.5 s.
- Clipping: "If the output is outside the allowed range, it is clipped to the nearest allowed value
  according to its sign."; output ranges `direct` +-0.5 V / `amplified` +-2.5 V and `pulse`-mode
  upsampling by doubling from `opx1000_fems.md`.
- The sum |taps| < 1 info note (added in `9848d9f`): tagged `[paper: output_filter.md:248]` with the
  sentence recommending that the absolute gain of the feedforward taps be below 1.

**`[derived]`** (checked numerically in the tests):
- H(s) = A_dc + sum A_n*s/(s + 1/tau_n) as s times the Laplace transform of the quoted step response
  (`_distortion_rational`); the sum model corrects it as one filter.
- Merging equal taus (A1 + A2 on one tau is one term); the cascade keeps both stages.
- The correction C = 1/H: zeros at -1/tau_i, poles at H's zeros (np.roots + Newton polish), gain
  1/(A_dc + sum A_i). The code cites as evidence a study note (`2026-09-01_iir-filter-and-flux-waveform`
  section 3.2): QOP compiler warnings on a real 20-port chip matched these roots to 10 significant digits.
- Bilinear (Tustin) discretization at 2 GSa/s. The docstring states the firmware discretization is not
  published, so the page draws a model, never a bit-exact replay.
- IIR/FIR order does not matter (LTI); the step horizon from the correction's slowest pole;
  dc_limit = sum(FIR) / A_dc of the model drawn; the seconds-like tau threshold, decimation and pulse
  length budget.
- Cross-checks named in `2f5128e`: qualang_tools' own correction taps (< 1e-9, single exponential and
  cascade), a hand-derived closed-form step, and an independently built line model on the real q1 set
  (< 1e-6) (`TestAgainstQualangTools`, `TestClosedForm`; implementer-measured, not re-measured later).

**What the verifier found on the quotes** (at `6d93e23`): 27 of 29 quoted strings are verbatim in
`documentation-website` (output_filter.md, opx1000_fems.md) or the installed qualang_tools; the 2
misses are not citations (a rhetorical question and a regex artifact). On relevance, the QOP 3.4
high-pass quotes (output_filter.md:201 and :112-113) do support the leaky-stage construction, though
the construction combines two passages. The first verifier also checked the model semantics against
output_filter.md: the step response is the distortion, the correction is 1/H, QOP <= 3.4 has no
dc_gain and a 0.5 s high-pass decay. It reported the ff_gain note untagged; `9848d9f` added the tag.

## 4. Mechanism (commits)

- `2f5128e` — the page, `core/zline_filters.py`, `/zline` + `/zline/data`, `zline.js`; responses in a
  content-keyed `KeyedMemo`.
- `772c66b` — the implementer's own refute-lens round (commit subject: "verifier round 2"), 5 defects:
  a repeated tau divided by zero in the root polish (a 500 for every line) -> equal taus merged in the
  sum form; one port's exception took the page down -> a block note; the cascade applied
  `exponential_dc_gain`, lacked the 0.5 s decay, and dc_limit/horizon described another model; GET
  `/zline` re-parsed every port -> parse memo on the port's canonical JSON, notes returned as copies;
  a failed load left stale figures.
- `6d93e23` — the first independent verification's defects (section 5).
- `9848d9f` — the re-verification's P3s: the step memo slot is now `(line, "step", model)`; the
  ff_gain note carries its source.

## 5. Found by the independent verification and fixed

First verification (after `772c66b`; commit `6d93e23` calls it "verifier round 3"), verdict DEFECTS,
fixed in `6d93e23`, re-verified PASS at `6d93e23`:

- **P1_memo_key_high_pass** — `PortFilter.key()` omitted high_pass, so `high_pass_filter=h` and
  `exponential_dc_gain=0` + `[[1,h]]` shared a key and `/zline/data` served a stale cascade after an
  edit between the two: final 999.998 / dc_limit 500000 / horizon 1e6 instead of the cold 0.99983 /
  1.0 / 16000 (KRS q3). Fix: `key()` is `dataclasses.astuple(self)`. Re-verified: high-pass form ->
  explicit form -> high-pass -> explicit served 999.998 / 0.99983 / 999.998 / 0.99983, each step
  byte-equal to a cold `zf.step_response`.
- **P1_stability_per_model** — the stability gate always used the sum model, so `[[-0.6,10],[-0.6,1000]]`
  (stable as a cascade) drew under neither model. Fix: `model_stability(pf, model)`; `/zline/data`
  blocks only the failing model and names the one that draws. Re-verified: cascade draws
  (final 1.000496), sum blocked with an `other_model_draws` note.
- **P2_cascade_stage_error** — `[[-1.2,10],[0.5,1000]]` gave an anonymous `ValueError` twice. Fix: one
  block note naming the stage ("stage 1 of 2 (A = -1.2, tau = 10 ns) ... UNSTABLE"), a step + pulse
  error is one note. Re-verified, growth time 2 ns checked by hand.
- **P3_four_curves** — "exponential only" and "FIR only" were `legendonly`. Fix: all four visible by
  default. Re-verified in real Chrome on big30x and krs5: 4 traces, all `visible = true`.

Re-verification P3s (at `6d93e23`):
- The step memo slot held one model, so every Model flip recomputed 2e6 samples: median 338 ms
  alternating vs 16 ms for one model (big30x, verifier). Fixed in `9848d9f`.
- The ff_gain note had no provenance tag. Fixed in `9848d9f`.
- Report wording only: "first sample 6.03 = 1/(1-0.6)^2" is not an identity (1/0.4^2 = 6.25); 6.03 is
  the product of the two bilinear first samples, 2.412 x 2.499. The code is right; no change needed.

`9848d9f` itself was not re-verified by an independent run in the sources.

## 6. Pins

- `tests/test_zline_filters.py`: `TestAgainstQualangTools`, `TestClosedForm`, `TestFaultInjection`,
  `TestResolve`, `TestQuotesVerbatim`, `TestRepeatedTau`, `TestModelDrawnIsModelReported`,
  `TestKeyIsTheWholeModel`, `TestStabilityIsPerModel`.
- `tests/test_zline_page.py`: `TestPage`, `TestData`, `TestNeverStale`,
  `TestOneBadPortNeverTakesThePageDown` (incl. `test_model_flips_reuse_both_step_curves`, `9848d9f`),
  `TestTableParseMemo`, `TestRound3PerModel`.
- Real-Chrome journey `tests/browser/journeys/zline.cjs` (Z1-Z5); no `.cjs` selfcheck for `zline.js`.
- Counts: 94 passed after `772c66b`, 107 passed after `6d93e23` (zline + bundles + sidebar_tools files;
  implementer, and the verifiers matched both). Full cqt suite: 9948 passed / 251 skipped / 1 failed
  (a flake, `test_apply_ux_selfcheck_passes`, passes in isolation) at the first verification; 9962
  passed / 251 skipped / 0 failed at `6d93e23` (re-verifier, 1:23:33). Journey 18/18 -> 27/27.
- Mutations: round 2 9/9 red (implementer); round 3 8/8 red after rewriting the two route tests to one
  model per pass (a model switch always missed the one-token slot and hid the stale key). The
  re-verifier's 4 mutations all went red. `9848d9f`: "shared slot -> red" (commit message).

## 7. Open / not done

- The state does not say which QOP the lab runs; the page defaults to the sum form.
- A model of the documented filter, not a firmware replay; no hardware comparison (scope trace or
  cryoscope) yet.
- Coupler rows are covered only by unit tests: neither rig chip wires a coupler `opx_output`.
- Z4 (lower row reveals its figures) is weak on a 5-line chip; the first verifier proved it on a
  20-line big20 copy (step figure top 521 px of 950).
- SM's beforeunload guard can hang a headless journey on a rig with staged edits; `zline.cjs` accepts
  and counts the dialogs, the shared `cdp.cjs` does not.
- The `9848d9f` ff_gain quote is verbatim at output_filter.md:248 (checked while writing this doc),
  but it sits in a plain `#` comment after `[paper: output_filter.md:248]`, a form the inline pattern of
  `TestQuotesVerbatim` does not collect, so it is not mechanically pinned.
- After a chip switch with `/zline` open, a same-named row draws the new chip's (correct) data under
  the old table until reload, as on other SM pages (verifier observation).
