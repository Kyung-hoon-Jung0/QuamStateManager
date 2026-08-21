# 130 — The future-blind replay benchmark (res-spec-vs-power pilot)

**Date:** 2026-08-21 · **Branch:** `feat/knowledge-pilot` ·
**Builds on:** docs/129 (the pilot annotations + manual v1)

Three pieces shipped together, because none of them is worth anything alone:
the manual's **exemplar images**, the golden paths refined into **per-run
answer keys**, and the **future-blind replay harness** that walks a session
run by run and is scored against those keys.

## 1. Why a benchmark at all

The observation that started this (user, 2026-08-21): a calibration chain is
sequential. Every run's parameters come from the state the previous run wrote,
so a plausibly-wrong fit adopted at step 1 propagates into everything after
it — "잘못된 단추를 잠그고 티셔츠 단추를 계속 잠그는 것". SM's existing
defences (pre-update anchors, revert, consistency checks) are all *post hoc*:
they limit and undo damage, they do not stop a wrong value from being adopted.
Whether a fit is wrong is, in the end, visible in the figure — which is exactly
where the customer expects an AI to look.

So the loop needs (a) knowledge about what the figures mean, and (b) a way to
measure whether reading them actually helps. This document is (b).

## 2. Exemplar images — re-rendered, never copied

`generator/render_knowledge_exemplars.py` rebuilds each exemplar from the run's
own `ds_raw.h5` with **normalised, unlabelled axes**. Two problems collapse
into one fix:

* **Confidentiality** — an exemplar ships to every other lab, and the lab's own
  PNG carries absolute frequencies, powers, and the chip's identity in its
  ticks.
* **Clause B** — the manual may only teach chip-independent geometry. *A
  picture with no numbers on it cannot teach an absolute scale.*

What is preserved deliberately: the labs' own orientation (frequency
rightwards, power upwards — a judge trained on one orientation misreads the
other, the docs/122 lesson), the true pixel grid (so an under-resolved window
still *looks* under-resolved), the per-row dip track, and **the record's own
markers even when they contradict the map** — that contradiction is the entire
lesson of the branch-swap and off-feature cases.

89 images across 21 cases, both pilot chips, 0 missing. Found while building
it: **run numbers collide** — one archive holds both
`#76_01_time_of_flight_…` and `#76_05_resonator_spectroscopy_vs_power_…`, so a
number-only lookup silently renders the wrong experiment. The family is now
part of the key and an ambiguous id is refused rather than guessed.

## 3. Answer keys — per run, hindsight allowed, the operator is not the target

`tests/golden/calib_paths/<family>/<chip>/<session>.json` (`smgolden/v2`):
51 (session, qubit) keys over 7 sessions and 2 chips. Each names the ideal
sequence step by step — expected case, correct decision, what the operator did
and whether they were right — plus the termination run, the final values with
their evidence and confidence, the wasted runs, and the writes that should
never have happened.

Two properties make them usable as a target:

* **The author had full hindsight; the system under test never does.** That
  asymmetry is the point of an answer key.
* **The operator is the baseline, not the ground truth.** 16 of 51 keys say
  *unresolved* — no trustworthy value existed, and adopting one is the error.
  Several ideal paths are shorter than what actually happened.

Every key was then attacked by an independent adversarial pass (kept in the
file as `adversarial_audit`) looking for laundered guesses, ideal steps a
future-blind system could not have taken, and claims contradicting the records.

## 4. The harness — future-blindness is structural

`core/autofit/pathreplay.py`. `Session.reveal(k)` returns runs `0..k`; asking
past `k` **raises**. Cheating is a crash, not a quietly better score — which
matters because every convenient shortcut here is a form of cheating (the
operator's next action, the value the chip eventually settled on, and the
docs/129 annotations were all written with hindsight).

Pipeline per run: `measure` the raw map → `classify` a case → the knowledge
pack's prescription → `decide` → adopt / retune / reconfirm / abstain. The
reader occupies the seat a vision judge will take: it returns a **case id**,
and the bounded-knob arithmetic lives in code, so swapping in a model never
lets it emit a number. An abstention never adopts.

### 4.1 What the naive reader got wrong (all measured, all fixed)

Building the reader was mostly discovering that the obvious statistic is the
wrong one:

| defect | what it did | fix |
|---|---|---|
| half-depth width from the global median | the strong frequency background counted as dip; linewidth 8 px where the dip is 3, which **merged the two branches into one line** | baseline subtracted per row (sigma-clipped polynomial), width grown contiguously from the minimum |
| adaptive depth bar for branch membership | punch-out means the bare branch is *far deeper* — the bar scaled to it and **discarded every dressed row**, reporting a textbook punch-out as stationary | fixed bar; depth asymmetry between branches is physics, not quality |
| branch centres from the extreme power rows | the lowest rows are where signal dies, so their argmin is noise — one map's dressed branch landed 8 px off the visible dip | k-means seeded from the two power *regimes*, outliers trimmed, edge-pinned rows excluded |
| saturation vs the global median | these maps brighten monotonically with power, so the ends of every healthy ramp flagged: **78 rows across the corpus** | detrended against the power trend |
| second minima anywhere | a sloped, speckled map always has one: **43 of 172 targets read as multi-feature**, against 10 by a human | a neighbour line must stand at the *same* position across rows, and the tracker must actually visit it |
| coexistence count for bistability | 6 rows of 50 made a textbook map "bistable" | count branch *hops* on a glitch-smoothed label sequence |
| flat 3σ per-row bar | a row is a maximum over `n` samples, so pure noise reaches ~2.9 on a 64-point row — a noise-only map read as a resolution problem | the bar carries the look-elsewhere term `sqrt(2 ln n) + 1` |
| "clipped" = dip near an edge | a feature that merely sits to one side is not clipped | the dip's own half-width must run off the window |
| speckle as a case | a punch-out plainly visible above a noisy bottom is still a punch-out | N1 is a flag unless it stops the read |

### 4.2 What scoring against the keys then exposed

Running the harness against the keys found four more, and each was fixed from
the key's own argument rather than by moving a threshold:

* **R-batch was in the manual and not in the code.** One 8-qubit multiplexed
  run failed 7 of 8; the loop adopted from its one "success", 23 MHz from the
  value the chip held. A batch that missed most of its targets is not evidence
  for the rest.
* **N8 blocked the whole adopt.** The flag says the *high-power rows* are
  unreliable — and the only value read there is the bare frequency, which the
  expert rule does not require at all. It now suppresses that field and lets
  the dressed value through.
* **A power can be unusable without being floor-pinned.** An optimum landing
  on a power row where no dip is traceable is read out of the noise band; it is
  refused on F1's principle.
* **Stopping dead at the first adopt.** Four calibrations were *correct at the
  run they adopted* and scored wrong because the chip moved later in the same
  session. That is the manual's own R-bias rule, so the walk now continues in
  watch mode and revises the held value when a later clean map puts the dressed
  branch several linewidths away. The two numbers — runs to the first value
  (efficiency) and the value still held at the end (correctness) — are reported
  separately, because collapsing them scores a correct early calibration as a
  wrong answer.

## 5. Results (50 keys, 2 chips; AS #8 excluded by expert decision)

| metric | value |
|---|---|
| correct outcome | **46 / 50** (31 right value + 15 correctly abstained) |
| adopted a wrong frequency | **1** |
| adopted where the key says no value existed | **1** |
| failed to adopt where the key has one | **2** |
| runs to first value vs the ideal path | median **+0**, at-or-under in 37/50 |
| runs saved vs the operator | **75** total, ahead on 18/50 |
| poisoned fields the keys name / adopted anyway | 61 / **21** |
| proposals no archived run answers | 90 (recorded unscoreable, never invented) |
| case agreement with the keys | 41% |

Read honestly:

* **The headline is the abstention count as much as the match count.** 15 of
  the 16 keys that say "no trustworthy value existed" were correctly refused.
  For a system whose failure mode is poisoning a chain, refusing is the
  behaviour that matters.
* **The poison gap is the real weakness.** SM refuses about two thirds of the
  writes the keys call poisoned; 10 of the 21 it takes are on one qubit (AS
  q6), the branch-swap-plus-intermittency case that cost the operator a full
  day. This is pinned as a floor so it cannot quietly get worse, and it is not
  tuned against — raising it by fitting these keys would be overfitting the
  benchmark.
* **Case agreement (41%) is the weakest number and the least meaningful one.**
  The keys' `expected_case` fields are largely prose written by a reader with
  the whole session in view; agreement on the *decision* (adopt vs retune) is
  what the other rows measure.
* **90 unscoreable proposals** is a hard limit of archive replay: when the loop
  asks for a measurement nobody took, the archive cannot answer. Recorded, never
  simulated.

## 6. What this does NOT show

* No model was involved anywhere in these numbers. The case reader is
  deterministic; the vision judge seat is built and empty (P3c/P3d still need
  an API key). The benchmark exists so that plugging one in produces a
  *comparison*, not an anecdote.
* Archive replay cannot close the loop. A proposal the operator never ran has
  no outcome, and no amount of scoring invents one — that needs hardware (P9).
* One family, two chips, seven sessions. The manual's chip-independence is
  evidenced (every case except spur-lock was seen on both chips, and the
  dressed-bare offset direction varies per qubit on both), not proven.

## 7. Files

| path | what |
|---|---|
| `quam_state_manager/core/autofit/pathreplay.py` | session guard, raw-map reader, classifier, decision, scoring |
| `quam_state_manager/generator/render_knowledge_exemplars.py` | stripped-axis exemplar renderer |
| `quam_state_manager/knowledge/v1/<family>/exemplars/` | 89 images + `index.json` |
| `tests/golden/calib_paths/<family>/<chip>/<session>.json` | 51 per-run answer keys (`smgolden/v2`) |
| `tests/test_pathreplay.py` | 31 pins incl. the benchmark floors |
| `tests/test_knowledge_pack.py` | 17 pins incl. exemplar/key integrity |
