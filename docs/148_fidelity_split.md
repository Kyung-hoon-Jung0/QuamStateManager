# docs/148 — Chip Status: the Fidelity menu splits in three, and GEF stops vanishing (2026-09-01, customer)

Two asks in one round: *"Fidelity에 2Q, 1Q, readout이 다 몰려있어서 readout을
보려니 너무 힘들다 — 2Q Fid. / 1Q Fid. / Read. Fid. 셋으로 세분화"*, and
*"readout fidelity에 왜 GEF는 없는거니?"*.

## The split

One `Fidelity` section (docs/141 4o had absorbed the old Gate tab into it)
becomes three sections and three menu entries — in the left sidebar
(`#chip-status-subnav`) and the in-page jump bar alike: **2Q Fid.** (the RB
panels, keeping the historical `data-topo-section="fidelity"` anchor),
**1Q Fid.** (`#sec-fidelity-1q`), **Read. Fid.** (`#sec-readout`, GE + GEF +
per-state |g⟩/|e⟩). `buildMetricPanels` routes the `fid1q`/`fidro` groups to
their own hosts; the shared metrics container keeps coherence/frequency/
calibration. Plumbing kept honest across the machinery: TAB_SPEC gains the
three views; `gate` AND the retired `fidelity` survive as aliases (old
links, remembered localStorage views, `?view=` deep links — normalised at
BOTH the setChipStatusView door and the docs/141-4ac deep-link guard);
the jumpGuard BELOW list and the lazy IntersectionObserver learn the new
sections (`fid1q`/`fidro` → the metrics build).

## GEF (and any absent fidelity metric) renders honestly

`buildMetricPanels` skipped any panel whose aggregate count was 0 — on a
chip whose runs haven't written `gef_confusion_matrix` yet, the GEF panel
silently did not exist, which read as a missing feature (the exact customer
question; the docs/94 silent-cap rule again). Inside the two dedicated
fidelity sections an absent metric now renders an honest empty line — the
panel title + *"no values on this chip yet — fills from
`gef_confusion_matrix` once a run writes it"*. The shared metrics container
keeps the old skip (an absent coherence panel is not a question anyone
asked). CDP-verified on a dataless chip: three sections render, the readout
host holds 4 honest-empty panels, GEF names its source leaf.

## Fallout fixed while running the wider pins (docs/142 debts)

Three `test_web.py` sidebar pins had been red since the docs/142 lazy-group
round (its batch never ran test_web): ① the lazy `<details>` attributes had
moved the pinned `open` flag mid-tag — the lazy attrs now render on ONE line
and a non-lazy tag is byte-identical to pre-142; ② **small trees no longer
lazy-load at all** (`_LAZY_GROUP_MIN_ENTRIES = 200`, whole-tree count):
lazy groups exist for 5,000-run archives, and on a 5-run chip the eager
render is strictly better (`test_lazy_scale` pins both sides, patching the
floor to 0 for its lazy fixtures); ③ the tree-HTML memo pin now settles the
listing-first hydration before comparing (the hydration version bump
legitimately invalidates the memo). The two `/param-history` alignment
banner pins moved to the docs/142 lazy fragment they now live in.
Remaining `test_web` reds — `TestPhase4QuamCacheConcurrency` +
`TestDatasetSelectionFix` — fail identically at pre-142 `c4d09cf`
(pre-existing, recorded, un-adjudicated).

Pinned by: `test_web.py` submenu pin (10 links, split order, the three
labels, no `view=fidelity` link), `chip_density_selfcheck.cjs` (TAB_SPEC
views, fid1q/fidro→metrics build, the honest-empty GUARD-feeds-push regex,
BELOW list, gate+fidelity→fidelity2q alias), `test_lazy_scale.py`
(lazy floor both ways). Mutations red: honest-empty removed (caught only
after strengthening the pin to span guard+push — a string-only source pin
survived the `if (false)` mutation, lesson recorded), sections never build,
plus the docs/147 four.
