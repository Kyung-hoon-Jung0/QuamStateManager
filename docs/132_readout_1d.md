# 132 — The 1-D readout family, and where a benchmark starts to argue back

**Date:** 2026-08-21 · **Branch:** `feat/knowledge-pilot` ·
**Builds on:** docs/129 (pilot), docs/130 (benchmark), docs/131 (four families)

Sixth family: **03_resonator_spectroscopy_single** (older spelling
**02_resonator_spectroscopy**, and one lab's `_wide_pyloop` variant) — the 1-D
sweep of the readout drive across the readout resonator. 213 runs exist across
**six labs** (a new one, IQCC_gilboa, joins here) and **two node generations**;
75 runs / 256 targets were annotated with every amplitude figure viewed.

## 1. What makes this family different

Three things, and all three bit:

* **The tallest excursion is routinely not the resonance.** The lineshape is
  Fano-asymmetric — a companion peak sits immediately beside the notch and is
  frequently taller — on a background that is strongly structured rather than
  flat. Any rule that reaches for the largest deviation lands on the companion.
* **Four figures per run**, not one: amplitude, phase, detrended phase and the
  IQ circle. A genuine resonance shows in all of them — a notch, one dispersive
  phase turn through it, and a circle closing toward the origin. This
  cross-check exists in no other family in the study, and the annotators were
  told to use it whenever the amplitude panel was ambiguous.
* **Two node generations.** The newer reports `dip_snr`, `ambiguous`,
  `candidates` and a SEPARATE `success_shape` verdict — so a run can honestly
  be "frequency OK, shape poor" — and carries an escalation ladder in its
  parameters. The older reports only frequency/fwhm/r²/success. A rule written
  against one generation must not assume the other's fields.

The resulting manual is the largest so far: **38 map cases, 31 flags, 20
rules** over six labs.

## 2. Reader work this family forced

* **The resonator sign is physics, not convention.** A readout resonator in
  |I+iQ| is a transmission notch, always. Letting the orientation probe choose
  put the reader on the companion peak — one lab's entire 1-D readout set read
  as "peak". Families whose value is a rotated projection keep measuring their
  sign, because there it genuinely is a convention and it differs between labs
  and between qubits inside one run. The companion check is scoped the same
  way: on a rotated projection a nearby opposite excursion is ordinary
  background, and treating it as a Fano partner made clean qubit lines
  unnameable.
* **One lab spells the frequency axis `RF_frequency`.** Without it, 96 of 96 of
  that lab's targets read as unreadable rather than as data.
* **Power broadening, judged against the node's own yardstick.** These nodes
  declare `target_peak_width` — the linewidth they are trying to reach — so a
  fit far wider than that is broadened and its centre is worth
  correspondingly less. On one real session every qubit came back 5–16× the
  target at full drive and one of those centres sat 30 MHz from where two
  low-drive sweeps agreed. No constant is invented; a run that declares no
  target asserts nothing. This alone took qubit-spectroscopy from 19/26 to
  **21/26** (wrong values 3→1, over-adoption 3→0).

## 3. The Clause-B lint refused something

One freshly authored case named an absolute linewidth and was **dropped at
load** rather than shipped. That is the first time the lint has refused
anything in this study, and it is worth saying plainly: a lint that has never
refused anything is not evidence that nothing needed refusing. The test now
asserts the lint is still catching something, rather than asserting every pack
is clean.

## 4. Results — and the part where the benchmark argues back

29 targets over five sessions and five labs: **21/29 correct** (19 right value
+ 2 correctly abstained), runs-to-first-value **median −1** versus the ideal
path (at-or-under in 27/29), 98 runs saved versus the operator.

But two numbers in this round should stop anyone quoting the headline:

* **Blind re-classification agreed on only 7/12** — the weakest of any family.
  Every disagreement is the clean-notch / Fano-asymmetric boundary, which is a
  real judgement call about one lineshape rather than a reading error. The
  pack says so in its own `blind_verification.note`, and the test floors this
  family separately instead of averaging the difference away.
* **The adversarial pass challenged two of the five keys hard** — one was
  called "not sound" with seventeen defects, including a wrong claim about how
  the node selects among candidate dips (it takes maximum prominence, not the
  candidate nearest the sweep centre) and arithmetic that was simply wrong.
  Each key now carries `audit_challenged` and its full audit, and the score is
  reported split by it.

And the split does not say what one would expect: the **challenged** keys score
12/14 while the unchallenged score 9/15. That is not evidence the challenged
keys are fine — it is evidence that "the auditor found defects" and "the key is
a good scoring target" are different properties, and that a five-session sample
is too small to separate them. Reported, not explained away.

The 0/5 session is worth naming too. On AS_10TQ9TC every map is readable and
every fit sits on the notch, and the reader adopted all five; the key marks all
five unresolved because the whole session runs on a stale-seed ratchet — the
sweep centres equal a frequency the chip no longer has, so each run confirms
the previous run's error. That is a **session-level** judgement no single map
can carry, and it is the clearest statement yet of what the per-run reader
structurally cannot see.

## 5. Exemplars

**221 images rendered, 9 refused** — the refusals are exemplar citations whose
run does not exist for this family or whose target is not in that cube. Refused
rather than guessed, and recorded in the index.

## 6. Files

| path | what |
|---|---|
| `knowledge/v1/resonator_spectroscopy/` | the manual (38 cases, 31 flags, 20 rules) + 221 exemplars |
| `tests/golden/calib_paths/resonator_spectroscopy/` | 5 answer keys, each with its run set and its audit verdict |
| `core/autofit/mapcases.py` | family sign policy, Fano companion, power-broadening flag |
| `tests/test_mapshapes.py` | reader + pack pins, incl. the per-family blind-agreement floor |
