# 131 — Four more families, five labs, one shared reader

**Date:** 2026-08-21 · **Branch:** `feat/knowledge-pilot` ·
**Builds on:** docs/129 (the res-vs-power pilot) and docs/130 (the benchmark)

The pilot covered one family on two chips. This extends the same three
artifacts — manual, exemplar images, future-blind benchmark — to
**08 qubit spectroscopy**, **09 qubit spectroscopy vs flux** (and its QDAC twin
03c), **06 resonator spectroscopy vs flux** (and 02e), and
**07 resonator spectroscopy vs coupler flux**, using **five labs' archives**:
CQT, AS_10TQ9TC, IQCC_QOP37, SNU_1Q and KRISS_CR.

## 1. Corpus

754 runs exist across the four families and five labs. 208 were annotated,
stratified rather than sampled at random: every lab, contiguous retry chains
so operator intent is readable, and deliberate coverage of failures (94 of the
208 runs contain at least one failed target). 26 agents viewed every figure;
a blind re-classification pass agreed **8/10, 8/10, 8/10 and 5/5**.

| family | runs / targets | labs |
|---|---|---|
| qubit_spectroscopy | 71 / 217 | 5 |
| qubit_spectroscopy_vs_flux | 57 / 165 | 5 |
| resonator_spectroscopy_vs_flux | 46 / 150 | 4 |
| resonator_spectroscopy_vs_coupler_flux | 34 / 34 | 2 |

The resulting manuals are far richer than the drafts they started from: 15–18
map cases and 22–32 orthogonal flags each, plus 16–18 cross-cutting rules.
All five packs (including the pilot's) pass the Clause-B lint with **zero
drops** — no case teaches an absolute scale.

## 2. One reader for four families

`core/autofit/mapshapes.py` + `core/autofit/mapcases.py`. Every one of these
experiments is the same measurement — sweep a frequency, find the feature —
differing only in what the second axis is and what shape the feature's
position traces along it. So the reading splits: family-independent
primitives measure, and a per-family manual names.

The interesting part is how much of the obvious approach was wrong, every
correction measured against the real corpus:

| defect | what it did | fix |
|---|---|---|
| per-slice polynomial background | cannot separate a broad band that TRACES an arch from the map's own transmission shape — a textbook flux arch tracked in **18%** of slices | subtract, at each frequency, the median ACROSS the sweep; the same arch tracks at **100%** |
| one background | a flat, unresponsive line and an empty window looked identical | keep BOTH residuals: a feature only the static background can see did not move, which is the flat case stated rather than guessed |
| independent per-slice significance | a whole-trace bar carries a look-elsewhere penalty the ridge does not, so weak-but-coherent ridges vanished | anchor on the strongest slice and walk with the bar corrected for the neighbourhood actually searched |
| raw sign changes as "turns" | a single clean arch read as multi-period on **105 of 149** real maps | count only turns the ridge travels more than its own width to make, on a smoothed track, with flat apexes handled (a rounded arch has equal samples at its top and a strict product test finds no turn there at all) |
| depth alone for a second line | **126 of 217** 1-D targets read as multi-feature, against a handful a person sees | a rival line is a resolved feature: require comparable WIDTH, not just height |
| extreme-row branch estimates | the lowest-signal rows voted on where the feature is | cluster from the two power/flux regimes, trim outliers, exclude edge-pinned rows |
| flat 3σ per-row bar | pure noise reaches ~2.9 on a 400-point trace | the bar carries `sqrt(2 ln n)` |
| assuming a dip | the readout rotation decides the sign, and it differs between labs AND between qubits in one run | measure it from the strongest slice |
| assuming an axis order | the qubit-flux cubes are (qubit, freq, flux) and the resonator-flux cubes are (qubit, flux, freq) | resolve by size against the named coordinate arrays |
| assuming an orientation | the punch-out figures put frequency on x; **every flux family puts flux on x** | a per-family table, checked against the labs' own figures |

## 3. The seam: signals, not case names

The reader returns a **semantic signal** (`curve_arch_vertex_inside`,
`line_multi_feature`, …) and each pack's `signal_map` turns that into ITS case
id and prescription. Two consequences worth the indirection:

* a manual can be revised — cases renamed, split, merged, prescriptions
  rewritten after a lab disagrees — without touching the code that reads
  pixels, and the reader can improve without renaming anything a human wrote;
* it is exactly the seam a vision judge occupies: a judge returns a case id,
  which is what `signal_map` produces, and the numbers stay on the code side.

Where a distinction cannot be made from the map alone, the map is recorded and
the conservative reading wins. A flat coupler map cannot say by itself whether
the flux window was too narrow or the coupler genuinely does not move that
resonator; it maps to the window-limited case, whose prescription is "widen
and look again", because claiming the physics from a window that may be too
small is the more expensive mistake.

## 4. Exemplar images

**617 images across the five families**, all re-rendered from raw with
normalised, unlabelled axes, both map shapes handled (2-D pcolormesh with the
tracked ridge; 1-D trace as a line), each family in its labs' own orientation,
0 missing. Palette-quantised to 64 colours: visually identical at the same
pixel size and **3.4× smaller** (32 MB → 13 MB), which matters because these
ship inside the app.

Two data defects surfaced while rendering, both refused rather than guessed:
run numbers collide across date directories (so the node name is part of the
key), and five exemplars named the wrong lab for a QDAC-node run (corrected
against the archives, recorded in the pack).

## 5. Benchmark results

12 hindsight-authored sessions across the four families and five labs, 73
(session, target) keys, scored future-blind.

| metric | value |
|---|---|
| correct outcome | **51 / 73** (38 right value + 13 correctly abstained) |
| adopted a wrong frequency | 8 |
| adopted where the key says no value existed | 12 |
| failed to adopt where the key has one | 2 |
| runs to first value vs the ideal path | median **+0**, at-or-under in 64/73 |
| runs saved vs the operator | **215** total, ahead on 43/73 |
| per family (correct/scoreable) | qubit-spec 19/26 · qubit-flux 10/14 · res-coupler 7/11 · res-flux 15/22 |

Read honestly:

* **This is markedly weaker than the punch-out family's 46/50**, and it should
  be: one shared reader across four families, four of which it had never seen
  a week ago. It is a floor to improve from, not a result to quote as
  capability.
* **Over-adoption is the weakness, and it is the dangerous direction.** 12 of
  73 adopt a value where the key says nothing trustworthy existed. Some of
  those keys refuse for reasons no single map can carry ("this belongs
  upstream — the readout rotation is wrong"), but not all, and the gap is
  pinned as a floor so it cannot quietly grow.
* **Two structural limits of session replay showed up**, both worth naming
  rather than scoring around. A session where the operator deliberately
  re-tunes a qubit mid-way has two right answers depending on when you ask:
  the system held a value that was correct when adopted and scored 800 MHz
  wrong against a post-re-tune key. And **219 proposals no archived run
  answers** — when the loop asks for a measurement nobody took, the archive
  cannot reply. Recorded, never simulated.
* No model was involved in any of these numbers.

Two real defects were found by scoring and fixed from the argument rather
than the number: the batch-failure rule fired on a PAIR where one qubit failed
(it was derived from an eight-qubit run that failed seven — "most failed"
needs a denominator), and a flat map was refused whole when only its SWEEP
answer is uninformative — the frequency is measured perfectly well, and
refusing it threw away 8 of 11 coupler frequencies the keys call adoptable.

A third was found by a discrepancy between two harnesses reading the same
archive: scoring must replay **exactly the runs the key was authored from**.
Judging the system on runs the key's author never examined is neither fair nor
sound — it can adopt from a run the key has no opinion about, and that was
worth 12 points. Each key now records its own run set.

## 6. Files

| path | what |
|---|---|
| `core/autofit/mapshapes.py` | cube reading, feature location, ridge tracking, shape analysis |
| `core/autofit/mapcases.py` | measured shape → semantic signal + cross-family flags |
| `core/autofit/replaybench.py` | the generalized future-blind loop and its scoring |
| `knowledge/v1/<family>/` | four more manuals (cases.md + cases.json + exemplars) |
| `tests/golden/calib_paths/<family>/<lab>/<date>.json` | 73 per-run answer keys, each with the run set it was authored from |
| `tests/test_mapshapes.py` | 18 pins on the reader and the packs |
| `tests/test_replaybench.py` | 22 pins incl. the benchmark floors |
