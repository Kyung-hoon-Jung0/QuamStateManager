# 82 — a shot index is not an axis

*Status: shipped 2026-08-07. Branch `feat/multi-instance-safety`.
Amends docs/48 (ndview + click contracts).*

## The report

> readout power optimization 코드에서 왜 `n_runs`가 sweep axes에 있지? …
> 다른 노드는 한번 너가 봐봐 없는 것 같은데

Opening the Data tab on a `readout_power_optimization` run plotted I against
**`n_runs`** — the shot index — instead of against `amp_prefactor`, the thing
the node sweeps.

Both halves of the question have an answer, and they are different answers.

## `n_runs` is real. It is also rare.

It is genuinely in the file, not invented by SM:

```
ds_raw.h5      I  : (state=2, qubit=2, n_runs=2000, amp_prefactor=10)
ds_iq_blobs.h5 Ig : (qubit=2, n_runs=2000)
```

A survey of the real archive (4,002 `ds_*.h5` files) says the user's intuition
about the other nodes is right. Only these carry a repetition axis at all:

| axis | files | nodes |
|---|---|---|
| `n_runs` | 1,836 | readout_power_optimization, iq_blobs, iq_blobs_gef |
| `average` | 41 | two-qubit RB |
| `repeat` | 41 | two-qubit RB |
| `shots` | 30 | two-qubit RB |
| `n` | 4 | two_qubit_confusion_matrix |

Every other node type — resonator/qubit spectroscopy, power Rabi, Ramsey,
chevron, all_xy, … — averages on the OPX (`n_avg`) and saves only the average,
so the shot axis is gone before the file is written. The nodes that keep it are
exactly the ones that **need every individual shot**: to build IQ blobs, a
confusion matrix, or a readout fidelity — a mean of those shots would erase the
very distribution being measured.

## What SM got wrong

`_classify_dim` called any dim with a numeric coordinate a `sweep`, and
`_default_view` then ordered sweeps **by size** and took the largest as x. A
shot axis is always the largest dim in the file (2,000–6,000 against a sweep of
10), so it won every time.

That is the wrong default twice over:

* **It is not a quantity.** Nothing distinguishes shot 7 from shot 6; the
  ordering is an artefact of acquisition. There is nothing to read off the axis.
* **It is the whole payload.** The cube shipped 80,000 numbers to draw a view
  worth 40 of them.

Two-qubit RB was worse: `average`(100) took x and `circuit_depth`(7) became y —
a 100×7 heatmap of what is a decay *curve*.

## The fix: average them, like the instrument would have

A new dim kind, `shot`, and one rule in `_default_view`: **a repetition axis is
averaged away whenever there is anything else to plot against.**

```
before:  x=n_runs(2000)     y=amp_prefactor(10)     shipped 80,000
after:   x=amp_prefactor    overlay=state           shipped 40, averaged over n_runs
before:  x=average(100)     y=circuit_depth(7)      (RB)
after:   x=circuit_depth    averaged over average, repeat
```

Averaging rather than slicing is the point: the mean over identical repeats is
*precisely* what the other ~47 node types already ship, so the default view of a
single-shot node now matches the default view of every other node. Shot #0
would have been one noisy trace out of two thousand.

**And it is kept when there is nothing else.** `iq_blobs`' `Ig(qubit, n_runs)`
has no other plottable dim — the per-shot scatter *is* the node — so the largest
repetition axis is promoted to x and only the extras average. In the real
archive this is the only way a shot axis ever reaches an axis: of 502 such
cubes, **none** had another plottable dim. (When the only other candidate was a
short sweep diverted to the overlay bucket, that sweep is reclaimed for x first
— a real quantity, however short, beats a shot index.)

## Where the line is drawn

Identical repeats are averaged; **distinct realizations are not**.

`sequence`, `sequence_index` and `nb_of_sequences` stay plottable: an RB random
sequence and an all_xy gate pair name *different circuits*, so index 7 is not
index 6 repeated. `frame`, `a`, `N` are ordinary sweeps and were never
candidates. The averaged set is a curated name list (`_SHOT_DIM_NAMES`) in the
same style as `_ENTITY_DIM_NAMES` — the names the real archive emits plus the
obvious spellings of the same QUA loop variable.

## Never silent

The plot shows a mean, so it says so: the controls strip renders
**`averaged over n_runs (2,000 shots)`**, beside the existing
"decimated view — peaks preserved" note. The variable card keeps listing the
file's true dims.

Mechanics: the reduction happens server-side before any budgeting (a 2,000-shot
axis is 2,000× the payload of the view it produces), the reduced dim leaves
`dims` so the client's flatten still matches, and an I/Q partner read inside the
decimation branch is averaged over the **same** axes — otherwise the pair walks
out of lockstep and |IQ| silently disappears. `nanmean` keeps a partly-failed
acquisition usable; an all-NaN repeat set ships as `null` like any other NaN.

## Pins

`tests/test_ndview_repeats.py` — the reported shape (x, the reduced dim leaving
the cube, and that the value shown **equals the mean**, not a sample), iq_blobs
keeping its axis and still decimating, RB's two repeat axes, the small-repeat
axis never becoming an overlay, distinct-realization names staying plottable,
NaN safety, and IQ lockstep with decimation firing on top of the reduction.
`tests/ndview_selfcheck.cjs` pins the disclosure note and that an averaged axis
leaves no selector chips (2,000 chips would gut the DOM).

Corpus verification: 5,805 real cubes rebuilt — 96 reduced, 502 kept a shot on
x, 0 problems (shape agreement, no reduced dim left in `dims`, entity never on
an axis, JSON parse-safe).
