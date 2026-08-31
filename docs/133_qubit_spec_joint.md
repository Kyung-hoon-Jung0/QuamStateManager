# 133 — The hardest family, and why it can only be read in pairs

**Date:** 2026-08-21 · **Branch:** `feat/knowledge-pilot` ·
**Builds on:** docs/129–132 (pilot, benchmark, four families, readout 1-D)

Seventh and eighth families, and the first round where two node types are read
as **one session** because neither is interpretable alone:

* `08_qubit_spectroscopy` — the 1-D drive sweep (already had a manual, docs/131)
* `08b_qubit_spectroscopy_vs_power` — drive detuning × **drive power**, which
  chooses both the frequency and the power the 1-D run then measures at

563 runs of the two node types exist across **5 labs**; **16 dates carry both**,
and those 16 joint sessions hold **519 runs**. Every one of them was annotated —
**519 runs / 1150 targets**, with every figure viewed — which makes this the
largest annotation pass of the study and the only family read at full coverage
rather than by sample.

## 1. Why this pair is the hard case

The 1-D run is only as good as the power the vs-power run chose, and the
vs-power run is only as good as whether its sweep reached **below the onset**.
Above the onset three things happen at once, and all three are in this corpus:

* the line **broadens** (the node reports both `fwhm` and `intrinsic_fwhm`, and
  their ratio reaches 5.2× here);
* it **Stark-shifts**;
* the **two-photon 0→2 transition** appears half an anharmonicity below the
  fundamental and grows *faster* with drive, so at high power it can be the
  strongest feature in the sweep.

A 1-D fit that lands on that partner looks perfect — narrow, high SNR, high r².
Nothing inside that single run distinguishes it from the real line.

One map says it better than any description. On CQT `#579` q10 the response
walks down a **three-rung ladder** in equal steps of about half the reported
anharmonicity: the fundamental at the bottom of the power sweep, the two-photon
line above it, the three-photon line above that. The node reported the **middle**
rung as f_01 and the **top** rung as the two-photon — the whole ladder shifted by
one — and the 1-D run two minutes later found the bottom rung and disagreed with
it by 95 MHz.

## 2. What the node's own flags are worth here — measured

| claim | measured over 182 vs-power targets |
|---|---|
| `success` | **True on 182 of 182.** It carries no information in this family. |
| `power_warning == "sweep_too_hot"` | 91 of 182 |
| record sits at the untouched **sweep centre** | 41 of 182 |
| `fwhm / intrinsic_fwhm` | median 1.39, p90 2.50, max 5.20 |

And the headline: against a consensus truth derived independently (from 1-D
runs only, two agreeing high-quality fits required), **the node's own frequency
is right on 52 of 103 targets.** Half its answers are wrong by more than 3 MHz.
That is the honest measurement of what a closed loop would be ratcheting on.

## 3. The statistic that would have been wrong

The obvious way to measure the two-photon trap is to look for two accepted
values in one session sitting half an anharmonicity apart. Done naively over
run **pairs** it returns 405 hits and looks like a dominant failure mode.

Counted properly — distinct (session, qubit) targets, both values accepted,
both passing a strict quality bar — it returns **6 of 142**. And against placebo
bands of the same fractional width at other multiples of the anharmonicity, the
real band is **not enriched**: a quarter of the anharmonicity gives 10, 0.7× gives
8, 1.4× gives 3. On that evidence alone the trap looks like nothing.

The expert reading of the same corpus says the opposite: the two-photon line is
implicated on **107 targets over 50 distinct (session, qubit) pairs**, in all 15
sessions and all 5 labs — 71 where the fit stepped over it correctly and 33
where it bit.

Both numbers are right, and the gap between them is the point. **When the trap
bites, the session usually never records the correct value at all**, so there is
no pair for a statistic to find. The failure is invisible to any test that
compares a session's own numbers against each other. It is only visible in the
picture — which is the case for reading figures, stated as a measurement rather
than an intuition.

## 4. What the reader had to learn

* **The onset is a stretch, not a suffix.** A first version asked for the lowest
  power above which the rest of the sweep is mostly covered. That reads a
  multi-photon ladder as an empty map: on `#579` the fundamental is tracked
  cleanly over the lowest 22 of 50 drive powers and then *disappears* as the
  response moves to the next rung. Anchoring at the topmost traced slice and
  walking down while the line keeps reappearing fixed it — and turned that map
  from "nothing here" into a frequency 0.34 MHz from the 1-D truth.
* **A rival above means the tracker is on the wrong rung.** The two-photon line
  is always *below*, so a companion *above* by half the run's own anharmonicity
  identifies the tracked ridge as the partner and the companion as the answer.
  The number comes from the run, so the rule stays chip-independent.
* **The slice's own strongest feature is a rival too.** Collecting rivals only
  from the runner-up list misses the partner in exactly the slices where it is
  the brighter of the two.
* **Eight swept powers.** Sweeping the plateau gate alone against the consensus
  truth, its length is the only setting that moves the number: 2 slices is right
  on 51% of what it says (the node's own rate), 4 on 62%, 8 on **73%**, 12 falls
  back to 69% while answering far less often. Ridge depth and below-onset
  coverage were swept alongside and never bound at any setting, so they are
  measured and reported but not gated on — a gate that never fires is a
  constraint invented rather than found.
* **No projection fallback.** On the same gate-only comparison the rotated
  projection alone answers 38 right / 14 wrong; the magnitude alone 35/15;
  falling back from one to the other 41/18 — three more right answers for four
  more wrong ones. In a loop that ratchets on its own output that is the wrong
  trade, so an unreadable projection stays unread.
* **The ladder gets its own name.** A stretch at *less* drive sitting half an
  anharmonicity *above* the tracked one is the ladder seen from the wrong rung
  — the walk cannot step there itself, because the jump is far wider than its
  local search window, so the lower segment is sought separately. It fires on
  11 of 182 targets across four labs, and on one of them it moves the answer 68
  MHz onto the fundamental.

Net, with every rule in place and measured on the same 103 targets:

| | answers | right | wrong | abstains | precision |
|---|---|---|---|---|---|
| the node's own fit | 102 | 52 | 50 | 1 | **0.51** |
| the reader as shipped | 56 | 41 | 15 | 47 | **0.73** |

By signal: the stationary-stretch case answers 44 targets at 32/12, the ladder
case 12 at 9/3, and the three "cannot vouch" cases answer none of their 47.
Fewer answers, better answers — the correct direction for a loop where a wrong
value poisons every run after it and an abstention costs one repeat.

### What the guard actually catches

The annotators identify **23 1-D targets where the two-photon line was adopted**
— the fit landed on the partner and the operator kept it. The replay refuses
**23 of 23**. That is not the reader simply refusing everything: across the same
corpus its 1-D signals adopt on at most 327 of 904 target-runs, so a blanket
refusal rate of roughly two thirds would have let about eight of these through.

The refusals do not come from the cross-family guard. Twenty-two of the 23 come
from the 1-D reader seeing more than one feature in the window and declining to
choose; the guard accounts for exactly **one** of them. The guard fires twice in
the whole corpus, and its second firing is on a target the annotators do *not*
call a two-photon adoption — a refusal of a claim that turns out to sit 4.4 MHz
from the consensus value, so it is a false alarm, not a catch.

That is the honest shape of the result and it is smaller than the machinery
suggests: the cheap in-window check does nearly all the work, and the
cross-family guard is a rare backstop with a demonstrated false-positive rate of
one in two. What the two node types read together *did* buy is not the guard —
it is the ladder rule and the partner rule inside the power reader, which move
real answers by 68 and 95 MHz.

## 5. Which value, not just whether

The doctrine amendment from earlier in this campaign — the AI decides *what
value to use*, not merely whether to accept the node's — was not actually
reaching the benchmark: the reader computed a corrected frequency and `replay()`
discarded it, always adopting the record. Fixed, and deliberately **scoped**: a
measured value outranks a flag saying the record is wrong only for shapes
measured across many slices. Relaxing it for 1-D traces as well converted an
abstention into a wrong answer on the readout benchmark and gained nothing
anywhere, so it is not relaxed there. Every previously published family score
is byte-identical after the change.

## 6. Recency or agreement — how a session should end

Fourteen of the fifteen remaining wrong answers had one thing in common: the
walk adopted a value and a later run moved it. The loop keeps the **last**
value it vouched for, and a ratchet is exactly recency-following — so the
alternative worth measuring is keeping the value the largest cluster of vouched
readings **agrees** on.

Measured over the 63 joint targets: recency 46 right / 15 wrong, agreement
**54 right / 7 wrong**, and on all eight targets where the two rules differ,
agreement is right and recency is wrong. Zero counterexamples.

That looks decisive and should not be read that way. The truth those 63 targets
are scored against is itself "the largest agreeing cluster of high-quality 1-D
fits" — so comparing a clustering rule to a clustering-derived truth is partly
circular, and some of the margin is that circularity rather than physics. The
non-circular test is the hindsight answer keys, which are figure-by-figure
judgements and not a clustering rule at all.

So `agreement` shipped as an explicit option (`score(..., rule=)`) with
`recency` still the default, and the comparison was re-run against the
independently authored keys.

**On the independent keys the two rules tie: 34 of 48 either way.** The margin
on the consensus truth was the circularity, exactly as suspected. Agreement is
still the better idea on principle — a ratchet is recency-following — but the
evidence for it is one clustering rule agreeing with another, and it does not
survive contact with a truth built a different way. It stays available and
stays off by default, and this paragraph is why.

## 7. Reading the amplitude alone gets the sign wrong

One annotator found a 1-D run whose stored drive **amplitude rose by half again**
while its line came back several times **narrower** — nonsense, until the port is
read too. The vs-power node writes the digital amplitude *and* the port's
full-scale power, and physical power is `P = FSP + 20*log10|amp|`. The
full-scale power had been written down further than the amplitude was written
up, so the power at the qubit fell.

So the replay now computes the real drive power from each run's own snapshot,
following the reference chain across `state.json` → `wiring.json` → the port
entry. It is used to make the two-photon guard physically honest: a multi-photon
line only exists *above* a drive threshold, so a value measured at **less** drive
than the one already held cannot be that artifact, and refusing it would be
refusing the better of the two measurements.

## 8. The manual

**15 map cases, 11 flags, 15 cross-family cases, 18 rules** — and the joint
cases are written into *both* manuals, which is provably inert for scoring (a
case only changes a replay if its family's `signal_map` names it, and these are
named by neither) but means a judge reading either manual meets the trap.

**312 exemplars rendered, none refused** — the first family in this campaign
where every citation resolved. **No case was dropped by the Clause-B lint**
either, which is what happens when the annotators are told at the outset that
an absolute frequency gets the case deleted rather than corrected.

**Blind re-reading agreed on 68 of 80 targets exactly** (73 of 80 on the
coarser question of whether the recorded fit was right) — the strongest
agreement of any family here, against the readout family's 7/12. A power map
is simply easier to agree about than a Fano lineshape.

One piece of the manual's advice was tested rather than adopted: it tells a
reader to check the two-photon offset against the run's *fitted* anharmonicity
rather than the stored default. Over the 103 targets with a consensus truth
that scores 40 right / 15 wrong against the stored value's 41 / 15 — no
improvement, so the reader keeps the stored value. The manual advises a person;
the reader is measured. They are allowed to differ, and this is what it looks
like when they do.

Three of the flags are worth naming because they are verdicts on the node
rather than on a chip: `success` carries no information (194 targets),
`sweep_too_hot` is wrong in both directions (91), and `fringe_detected` was a
false positive **everywhere it was checked** (35).

## 9. The measured result, and what it is not

Five joint sessions, five labs, 48 targets, keys written with hindsight and then
attacked by an independent auditor. **All five audits found the key's reasoning
wrong; only two challenged its VALUES.** That distinction is recorded on each
key rather than collapsed into one "challenged" flag, because a key whose
narrative is torn apart while every number it scores against is confirmed is
still a usable scoring target — and three of these five are exactly that.

| | targets | correct |
|---|---|---|
| all | 48 | **34** |
| keys whose VALUES the auditor confirmed | 20 | 10 |
| keys whose VALUES the auditor challenged | 28 | 24 |

Per session: CQT 08-16 **17/20**, SNU 08-02 7/8, CQT 08-15 7/14, AS 08-09 3/4,
IQCC 07-22 0/2 (2/2 under the agreement rule).

Three things this does NOT show, all of them worse than the headline:

* **It is slower than the ideal path**, median **+1** run, at-or-under in only
  19 of 45. Every other family in this study came in at or under. This one
  abstains and asks for another sweep, and on a real chip that costs time.
* **319 of its proposals are unscoreable** — the bounded knob move it asked for
  matches no run the operator actually did, so the archive cannot say whether
  it would have helped.
* **The audit split is inverted again.** Keys whose values the auditor
  confirmed score 10/20; keys whose values were challenged score 24/28. The
  same inversion appeared in docs/132. Two rounds is not proof of a mechanism,
  but it is now twice that "the auditor found defects" and "this key is a good
  scoring target" behaved as different properties — which is a caution about
  reading either number as a grade.

The manual's own first open question is the sharpest statement of why this
round exists at all:

> Can a 1-D run ever convict itself of the two-photon lock from its own record
> alone? The corpus contains no example.

Nine more open questions are recorded in the pack, including one the corpus
could not settle at all: no threshold for "the sweep reached below onset" could
be derived, because the only evidence is the flat floor of the peak-height
trace and in this archive that floor is buried in noise.

## 10. Files

| path | what |
|---|---|
| `knowledge/v1/qubit_spectroscopy_vs_power/` | the new manual + exemplars |
| `knowledge/v1/qubit_spectroscopy/` | gains the shared cross-family cases |
| `core/autofit/mapshapes.py` | `PowerShape`, `shape_power`, onset-as-a-stretch, lower-rung search |
| `core/autofit/mapcases.py` | the `power_*` vocabulary, the two-photon partner rule |
| `core/autofit/replaybench.py` | per-run family resolution, the cross-family guard, `drive_power_dbm` |
| `tests/golden/calib_paths/qubit_spectroscopy_vs_power/` | joint answer keys |
