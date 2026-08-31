# 137 — Closure executed: the NaN that was never a value, the read that was of something else, and C2

**Date:** 2026-08-24 · **Branch:** `feat/knowledge-pilot` ·
**Builds on:** docs/135 (closure rules as data), docs/136 (the A/B/C retag and
its measurement). This is step ④ of the approved plan, with the rabi/ramsey/zz
packs deferred by user direction. Benchmark trajectory this round:
**53/73 → 60/73 → 61/73**, every step diffed per target.

## 1. A NaN is not a value (53 → 60)

The docs/136 diagnosis said 13 of 20 wrong outcomes took their first value at
a B-tagged run. Dumping those walks showed WHAT was being taken:
`adopted={'qubit_frequency': 'nan'}` — the reader vouched the SHAPE, the
node's fit had failed, the record carried NaN, and NaN passes
`isinstance(v, (int, float))`. A NaN adoption made the session "resolved"
with a non-answer, which scored as over-adoption six times and wrong-value
twice. Both adoption sites now require `math.isfinite` — this is not a tuning
against the keys but the walk's own doctrine ("numbers come from the node's
own fitter or not at all") — and the benchmark moved 53→60: over-adoption
9→3, res-vs-flux 15→20/22, B-direction agreement 64/92→73/92, premature
first adoption 27→16.

## 2. The session-end closure pass (60 → 61)

Three mechanisms, and the honest story is that the FIRST design was wrong:

* **The consensus-overturn clause was removed after it broke a real
  target.** Draft one re-vouched the largest independently-agreeing group
  over a lone final reading. That silently re-introduced
  agreement-over-recency — the exact choice docs/133 §6 decided the other
  way, caveat on record — and flipped a target whose lone final reading was
  the RIGHT line (an 882 MHz swing). Removed, not patched around.
* **Identity split (unconditional, like the NaN guard):** a read is OF
  something, and `measure_qubit` names it. The coupler-flux node sweeps the
  resonator of whichever pair member it measures; a `control` read is a
  different physical resonator from a `target` read, and the recency ratchet
  was overwriting one with the other (the real session this fixes is GOOD
  precisely because the two identities corroborate the SWEEP value — the
  frequency is what must not mix). The vouched value is the majority
  identity's most recent read. Node semantics, not a band. This is the
  60→61: one wrong-value became a match, zero other flips.
* **CL-CLUSTER's contested clause, executed:** when a session's readings
  disagree beyond tolerance and NO group carries independent confirmation
  (same-setting repeats are one observation), the session does not vouch a
  value; the direction is the rule's own text. Gated on a consulted pack
  actually shipping CL-CLUSTER — without it the walk is byte-identical
  (knowledge stays strictly optional). **Measured honestly: this clause is
  dormant on the current benchmark** — the one target it was aimed at
  turned out to have two independently-agreeing noise-floor locks, which no
  amount of session arithmetic can catch (a reader-level limit, recorded).

Sweep fields are deliberately out of closure scope: the flat-map case
refuses sweep reads by design, so the walk's sweep evidence is
systematically thinner than the archive's, and a contested-drop there would
punish that honesty.

## 3. What remains wrong, named

12 of 73: 6 missed (the reader refuses runs the keys trust — docs/131's
structural reader-vs-author disagreements; tuning the reader against these
keys would be overfitting the benchmark), 3 over-adopted (incl. the
noise-floor-agreement case above and a key whose own ideal path adopts while
its termination says unresolved), 3 wrong values (incl. the
five-clean-reads-of-the-wrong-line identity case that needs the
qubit-spec-vs-power partner, i.e. the C2 rule below, executed).

## 4. C2 — cross-family closure as data

`knowledge/v1/_cross/closure.json` (loader `knowledge.load_cross`, same
Clause-B lint + profile-gate validation; a rule naming fewer than two
families is dropped — a one-family rule is a C1 rule in the wrong file;
`cross_hash` joins the verdict context). Four rules shipped:

| id | seam | status |
|---|---|---|
| X-JOINT-QSPEC | qubit-spec × qubit-spec-vs-power | **already executed** — the docs/133 joint replay + two-photon guard; the rule documents the executed mechanism |
| X-TWO-DIP-POWER | res-spec × res-vs-power | data only — a contested two-dip identity closes through the punch-out, with the profile field naming the other dip |
| X-PARKING-AGREE | res-vs-flux × qubit-vs-flux | data only — the two flux maps see one sweet spot; disagreement beyond the map's own feature width re-measures, never averages |
| X-COUPLER-DECISION | res-vs-coupler × qubit-vs-coupler | data only, profile-gated (`weak_flat_normal`) — the resonator map verifies, the qubit map decides |

`cross_rules_for(doc, family, answers)` gives the active rules per family
under the chip's answers (unanswered gate ⇒ silence, as everywhere).

## 5. Pins

`TestClosureWalk` (NaN never adopts; a minority-identity read never
overwrites the majority; contested never vouches when CL-CLUSTER ships;
without the rule the ratchet is byte-identical) — the synthetic sessions
give each run its OWN map with the fit at that map's apex, because a fit
disagreeing with its map is refused by the reader before closure logic runs.
`TestCrossClosure` (shipped rules load clean; profile gate + family filter;
lint drops absolute-scale and one-family rules). The docs/131 and docs/136
benchmark floors all still hold with margin; per the no-overfit doctrine
they were NOT raised to chase the new numbers.
