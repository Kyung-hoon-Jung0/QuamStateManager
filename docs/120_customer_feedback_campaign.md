# 120 — Customer feedback campaign: 11 items

*2026-08-16. A customer round of feedback arrived as 11 items (9 numbered, 2
raised mid-discussion). Each was worked through with the user until intent and
design decision were explicit; this file is the binding record, and each large
workstream gets its own implementation doc (docs/121+). Same shape as the
docs/110 daily-flow campaign.*

## What the customer is actually asking for

The through-line is that the daily loop costs too much: typing the same search
terms over and over, two chip maps where one would do, per-qubit trends when
the question is chip-wide, a sync model that asks permission the user already
wants to grant standing, and no way to get back to a known state quickly.

## Decisions the user made (binding)

| # | Item | Decision |
|---|---|---|
| 1 | Standalone folder | Indicate it; editing was never blocked |
| 2 | QDAC generation | Done — docs/119 |
| 3 | Search syntax panel | `?` is enough; make it toggle |
| 4 | Live-Edit chips | Chip-DERIVED sections + validated leaf keywords; AND/OR toggle leftmost, default **AND** |
| 5/9 | Chip Status Trends | **Any numeric parameter**, conditional on the overhead investigation (§ below) |
| 6 | Anharmonicity | Investigated — no bug |
| 7 | FSP amps | Editable before commit |
| 8 | Auto-Sync | Ticking "auto replace" **is** the consent ⇒ live wins silently; unticked + local edits ⇒ banner, never silent |
| 10 | Version id + history | Advanced UX over the existing State History |
| 11 | One chip map | Keep the hero, absorb the cards, C/T/M + frequency chevrons |
| — | Order | Quick wins first, then big |

## The verification target

The real customer chip, measured — this is what every item is checked against:

- 20 qubits, 30 pairs, **all** with `grid_location` → the strict grid gate
  passes, so it renders in `physical` layout mode.
- **Mixed architecture**: 11 × `QdacBiasedFixedFrequencyTransmon`
  (`QdacBiasLine`) + 9 × `FluxTunableTransmon` (`FluxLine`), plus a top-level
  `state["qdac"]`. Exactly the chip item 2 was built for.
- Every pair carries `qubit_control` / `qubit_target` **and an explicit
  `moving_qubit`** ∈ `{"control","target"}` (15/15 split), one-hop pointers.
- All 30 pairs: both `f_01` numeric, |Δ| 93–280 MHz.
- **433 run folders, each carrying its own `quam_state/{state,wiring}.json`**.
- `T1` / `T2ramsey` / `T2echo` / `gate_fidelity` are **null** chip-wide (early
  bring-up) — which is why the curated-11 metric tier would render a nearly
  empty Trends page, and the single strongest argument for item 5/9's scope.

## The item 5/9 overhead investigation — the user's condition, answered

The user approved "any numeric parameter" *conditionally*: **investigate the
performance, speed and stability overhead, report, then decide.** Measured by
running the real `core/leaf_index.py` against all **433 real snapshots** into
an in-memory SQLite with the production schema:

| measurement | result |
|---|---|
| numeric leaves per snapshot | **2,892** (+1,675 non-numeric kinds) |
| distinct indexed paths | **3,887** |
| change points, 433 snapshots | **24,851** (first snap 3,742, **median 7** after) |
| **whole index size** | **1.21 MB** |
| ingest | ~20–28 ms/snapshot |
| **all-20-qubits, one metric, overlay query** | **0.32 – 0.92 ms** |
| the same query, batched `IN(...)` | 0.93 ms — **no gain** |
| path typeahead (`search_paths`) | 0.6 – 4.8 ms |
| full rebuild (out-of-order penalty) | ~10 s |

A caution raised during planning was **wrong and is corrected here**: that
`leaf_index.series()` answers one path at a time, so a 20-qubit overlay means
20 queries. Measured, those 20 queries cost **0.92 ms in total** and batching
them gains nothing.

**Coverage is the real argument.** The index holds 516 `amplitude` paths,
1,616 `operations`, 840 `macros`, 488 `resonator`, 169 `opx_output` — and
**588 paths have more than one change point**, i.e. 588 genuinely trendable
parameters. The movers are exactly what this lab retunes:
`qubits.q2.xy.operations.x180_DragCosine.amplitude` (198 change points),
`ports.mw_outputs.con1.1.3.full_scale_power_dbm` (198),
`…resonator.operations.readout.integration_weights_angle` (202).

**Verdict: proceed with any-numeric-parameter**, curated 11 as the default
preselection so the page is useful on open. Two disclosed risks, both
pre-existing properties of `leaf_index` rather than things Trends introduces:
the ~10 s rebuild on out-of-order arrival (docs/83: "rebuilt, not repaired") —
so Trends reads, never ingests, and never triggers it on a render; and the
index covering SM's snapshots, so a chip SM has never snapshotted must say so
honestly rather than draw an empty axis.

---

## Wave 1 — shipped

### Item 6 — anharmonicity: investigated, no bug

> *"in the qubit spec vs power code, how is anharmonicity calculated now? Is it
> 2 × (difference between GE and EF/2 freq)? Investigate; if it's right then
> it's okay, maybe a user is confused."*

`core/interactive_plots/recipes/qubit_spec_vs_power.py` implements exactly that
identity. The 2-photon g→f/2 line sits at `f_01 − α/2`, so a click assigns
`anharmonicity = 2·(f_01_fit − clicked)`, encoded as the affine
`{"scale": -2e9, "offset": 2.0 * ge}` and anchored to **this run's** fitted GE
frequency. It is offered only when the run's analysis carries the ef fit
(`_EF_VARS`), so an older analysis gets an *unavailable* tile rather than a
wrong one.

A bug of this shape did exist and **is already fixed**: the tier-2 node-name
matcher used to route a standalone `28_qubit_spectroscopy_e_to_f` into the GE
recipe, making the E→F peak clickable into `f_01` — wrong by the anharmonicity,
~200–300 MHz (`recipes/qubit_spectroscopy.py`). **No action**; reopen only
against a specific run.

### Item 3 — the syntax help stops covering the list

> *"whenever a user types something in the dataset search box the syntax always
> shows up, so the user needs to scroll down to see the folder list — NO GOOD
> UX. I think just having a ? symbol inside the box is already enough. Small
> bug: clicking ? pops it up, but clicking again does not close it."*

Two independent defects, both present in **both** parallel implementations (the
Datasets page's id-based handler and the generic class/data-attribute one the
sidebar filter uses):

1. the panel opened **itself** on the first focus of its input per browser
   session (`quam_dataset_search_help_shown` / `quam_search_help_shown`);
2. the `?` was **open-only** — `hidden = false` with no else — so the control a
   user opened the panel with could not put it away. Only the `×` could.

Why it reads as *covering the folder list* rather than a mild annoyance: the
Datasets panel floats (`position:absolute`), but the sidebar's copy is
deliberately `position:static`, because a narrow `overflow-y:auto` sidebar
would clip an absolute popover. Static means **inline**, so opening it pushes
the whole experiment tree down. One keystroke buried the list and the obvious
way to undo that did nothing.

Now nothing auto-opens either panel and `?` negates `hidden` in both. The `×`,
the click-to-paste examples and the Datasets dead-click guard are unchanged.
`test_web.py` had pinned the *old* spec (asserting the sessionStorage flag was
present), so that test is **updated**, not worked around.

Pinned by `tests/test_search_help.py` + `tests/search_help_selfcheck.cjs`,
verified to fail on the pre-fix revision.

### Item 1 — say when the folder is standalone

> *"SM works as project centric now. But sometimes a user wants to modify by
> just opening an arbitrary state file, and also wants to handle the FULL state
> file in SM. I think it's enough to just indicate in SM that a user is now
> modifying not a project but a specific folder."*

Editing an unaffiliated folder always worked (docs/63: standalone loads keep
working, "displayed as-is"). The gap was that SM said nothing:
`_qualibrate_tray_badge` returned `None` whenever there was neither an active
project nor a derived scope, so an empty slot meant **both** "you are editing a
bare folder" and "this badge does not apply yet".

Boundaries, each pinned: gated on a qualibrate config existing (without one
"project" is not a concept this user has, so that case stays byte-identical);
archives excluded (the status badge already names them); it may sit **beside**
the ⚗ badge, because qualibrate having an active project while the open chip
belongs to none is exactly the case worth naming — and it explains that badge's
amber; and it is **not a warning**, so the warn/danger colours keep their
docs/55 meanings. Its own CSS class, never the sidebar tree's
`.status-standalone` (that belongs to the run-status family).

Pinned by `tests/test_project_scope.py::TestStandaloneBadge`.

### Item 7 — the FSP compensation amplitudes are editable

> *"When you change FSP the option to change the related amps comes up
> together — that itself is really good. The problem is the amps can't be
> modified by the user at all: it's accept, or discard and update only the FSP.
> Users want to be able to modify the amps a little and then update."*

Each compensated amplitude is now an input. The change lands in **one place**:
all five call sites (Explorer, both grids, All-values, and the plot-apply
popup's per-row and Apply-All paths) build their `comp` resend through
`window._fspCompUpdates`, so teaching that helper to read an override covers
every one of them with no edits to any resend site.

`a.new` keeps the computed value forever and the override rides alongside as
`a.userNew` — that is what gives the per-row reset a target, and what makes an
un-edited plan serialise byte-identically (pinned).

The consequences of an editable field, each handled: the **DAC clip warning is
recomputed from the live inputs** (it is a claim about what will be *written*,
not about the proposal that arrived with the 409), as are the per-row mark and
the docs/76 Δ; a **non-numeric cell disables the apply** rather than silently
falling back; **blank means "use the computed value"**; and departing from
`P = FSP + 20·log10|amp|` is **stated out loud**, since that identity is the
whole point of the compensation.

The server is untouched — `fsp_ack=comp` has always accepted whatever
amplitudes the batch carries and never re-derived them.

Pinned by `tests/fsp_edit_selfcheck.cjs` + `TestEditableAmpsWiring`, verified
to fail on the pre-change `app.js`.

---

## The dormant-pin outage (found while setting this campaign up)

Not a customer item, but the campaign depends on it and the user directed it be
fixed first.

`jsdom` was **not installed on this machine at all**, so all 59 `.cjs`
selfchecks were taking their driver's `pytest.skip("jsdom not installed")`
branch. Roughly fifteen DOM-level pins had been reporting green without ever
executing. Installing jsdom turned four of them red immediately — and they fail
identically against the pre-campaign `app.js`, so they were genuinely broken,
not broken by this work.

One root cause, shared by all four. The harnesses run the scripts with
`window.eval` and hand Node **individual** globals (`global.window`,
`global.document`, …) rather than using jsdom's own script realm. `CSS` was
never on that list, so:

```
typeof window.CSS         -> "object"
window.eval("typeof CSS") -> "undefined"
```

which makes the shipped idiom `(window.CSS && CSS.escape) ? CSS.escape(s) : s`
**throw ReferenceError instead of taking either branch** — the guard tests
`window.CSS` but calls the bare global. In a browser both exist, so the product
code is correct; only the harness made them disagree.

The damage was invisible because of *where* it landed. In
`LiveEditUndo._input` the throw was swallowed by `try { … } catch { return
null; }`, so every cell lookup silently missed, every restore "found nothing to
do", and `tryUndo()` returned false with no error anywhere — two assertions
failing for a reason no assertion could name. In `dataset-virtual.js`'s
`_kbHighlight` it threw outward and took the first j/k keypress with it.

Fixed in the **harness**, not the product: bridge `CSS` like every other
global. Recovered and now executing: `state_sync` 46 assertions, `ctrlz` 28,
`undo_nav` 20, `ds_flow` 17.

A `package.json` now declares `jsdom` + `canvas` so this cannot recur silently,
and CLAUDE.md records both the skip-vs-fail trap and the harness convention:
**a guard that tests `window.X` while calling bare `X` throws instead of
degrading, so a new harness must bridge every global its code reads bare.**

---

## Waves 2 and 3 — planned

| wave | item | branch |
|---|---|---|
| 2 | 4 — Live-Edit parameter chips | `feat/live-edit-chips` |
| 2 | 10 — version id + history in the top bar | `feat/version-history-topbar` |
| 3 | 11 — one chip map | `feat/one-chip-map` |
| 3 | 5/9 — Chip Status Trends | `feat/chip-trends` |
| 3 | 8 — bidirectional Auto-Sync | `feat/auto-sync` |

Their design decisions are recorded in the campaign plan and will be expanded
into docs/121+ as each lands. Two are called out as needing an audit even if
their stream looks clean:

- **item 11** relocates `openQubitMore` / `_sharedQubitPopup` out of
  `buildTopology` before deleting the card diagram. The failure mode is
  **silent** — `bindHover` guards on a null `_sharedQubitPopup`, so the hover
  popup would simply stop opening, with no error and no failing test.
- **item 8** splits arm-ability per direction. The existing gate refuses when
  `live_diverged`, which is correct for push and backwards for pull.
