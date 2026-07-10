# 35: Chip Compare — Multi-Chip Chip Status

> A new top-level **Chip Compare** tab, placed directly under Chip Status in
> the sidebar. The user picks two or more `quam_state` folders (different chips,
> or the same chip at different times) and sees them side-by-side as topology
> cards plus a unified diff table of state values, experiment parameters, and
> fit results.

---

## Why this exists

Users repeatedly asked: *"I want to compare Chip Status across two different
chips, side-by-side, like the Chip Status dashboard but doubled up."*

The existing comparison stack covered only adjacent use cases:

| Existing surface | What it does | What it misses |
|---|---|---|
| `/diff` | 2-folder flat key-by-key diff | No chip-status view, only 2 folders |
| `/compare` | N-way comparison driven by **experiment checkboxes** in the sidebar workspace tree | Tied to per-experiment selection — not chip-folder-driven |
| Param History snapshot compare | Same chip across time | Single chip only |

Chip Compare fills the gap: a **chip-folder-driven** N-way comparison with the
topology view front and center.

---

## What the user sees

### Entry points

- **Sidebar nav** — a "Chip Compare" link directly under "Chip Status".
- **Workspace toolbar** — a "Chip Compare" button next to the existing
  "Compare Selected" and "Trend Tracker" buttons.
- **Command palette** — Ctrl/Cmd+K → "Chip Compare".

All three land on `/chip-compare`.

### Layout

```
┌────────────────────────────────────────────────────────────┐
│  Chip Compare                                              │
│  Compare Chip Status across 2+ quam_state folders…         │
├────────────────────────────────────────────────────────────┤
│  Selected chips  [variant-B] × [example-9q-rack] × [+ tunable_cpl]×  │
│  From workspace [▾]   Recent [▾]   📁 Browse   [Compare 3] │
├────────────────────────────────────────────────────────────┤
│  [Topology]  [Differences]                                 │
├────────────────────────────────────────────────────────────┤
│  ┌── variant-B (REF) ──┐ ┌── example-9q-rack ──┐ ┌── tunable_cpl ──┐│
│  │ Qubits  17       │ │ Qubits  9     │ │ Qubits  17     ││
│  │ Avg F   99.0%    │ │ Avg F   98.7% │ │ Avg F   99.2%  ││
│  │ Avg T1  42μs     │ │ Avg T1  38μs  │ │ Avg T1  51μs   ││
│  │ ┌──┐┌──┐┌──┐     │ │ ┌──┐┌──┐┌──┐  │ │ ┌──┐┌──┐┌──┐   ││
│  │ │qA││qA││qA│⚠    │ │ │q1││q2││q3│⚠ │ │ │qC││qC││qC│⚠ ││
│  │ └──┘└──┘└──┘     │ │ └──┘└──┘└──┘  │ │ └──┘└──┘└──┘   ││
│  └─────────────────┘ └───────────────┘ └────────────────┘ │
│  Reference chip: [variant-B ▾]                                 │
└────────────────────────────────────────────────────────────┘
```

The Topology tab renders one **chip card** per selected chip:

- Card header with the chip label; the reference chip gets a `REF` badge and
  a primary-colored border.
- Summary stats: qubit count, pair count, mean gate fidelity, mean T1.
- A qubit grid where each qubit shows its id + key metrics. Qubits that
  differ from the **reference chip** on any tracked property (`f_01`, `T1`,
  `T2ramsey`, `T2echo`, `readout_frequency`, `anharmonicity`, `readout_amplitude`,
  `readout_threshold`, `gate_fidelity_avg`, `x180_amplitude`, `z_joint_offset`)
  get a colored border and a small dot marker.
- A reference-chip selector at the bottom: changing it re-fetches the tab
  via HTMX with the new `ref` index.

The Differences tab reuses the existing `/compare` page's `_compare_diff.html`
template wholesale — it already organizes content into Metadata, Parameter
Differences, Fit Result Differences, and State Property Differences sections.
Sections without content auto-hide, so:

- Two plain `quam_state` folders (no `node.json`/`data.json`) → only the
  State Property Differences section appears.
- Two experiment-folder pairs (with `node.json`) → Metadata, Parameter
  Differences, Fit Result Differences, and State Property Differences all show.

---

## What it reuses

Chip Compare is mostly orchestration over existing pieces — no new diff or
query logic:

| Reused | Source |
|---|---|
| `Workspace.load_store(path)` (LRU-cached, accepts any `quam_state` path) | `core/scanner.py:215-230` |
| `_load_compare_stores(paths)` (graceful when `node.json` missing) | `web/routes.py:1980` |
| `_detect_workspace_chips(ws)` (populates the in-page picker) | `web/routes.py:2326` |
| `Differ.multi_diff`, `Differ.multi_compare`, `Differ.compare_parameters`, `Differ.compare_fit_results` | `core/differ.py` |
| `_compute_diff_cells(rows, ref_idx)` (per-chip diff highlight set) | `web/routes.py:2009` |
| `QueryEngine.get_topology()` (per-chip nodes + edges) | `core/query.py:332` |
| `chip_name_for(path)` (display labels) | `core/history.py:317-337` |
| `_compare_diff.html` (entire Differences tab) | `web/templates/_compare_diff.html` |
| `recentFolders` localStorage helpers, `openFolderBrowser` modal | `web/static/app.js` |

Note that this means Chip Compare benefits automatically from the Phase 3
perf work: the SQL pre-thinning, parallel scanning, fast extractor, and
O(N) fingerprint index all make multi-chip loads fast even at scale.

---

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/chip-compare` | Empty picker page |
| POST | `/chip-compare` | Accept `paths` list, render with default tab (Topology) |
| GET | `/chip-compare/topology?paths=…&ref=N` | HTMX swap — topology tab |
| GET | `/chip-compare/diff?paths=…&ref=N` | HTMX swap — unified diff tab |

All four sit in `web/routes.py`, right after the `/compare/full` route.

---

## Templates

| Template | Role |
|---|---|
| `chip_compare.html` | Full page — extends `base.html` |
| `_chip_compare.html` | Top-level partial — header, picker, tab bar, content pane |
| `_chip_compare_picker.html` | Selected-chip tags + workspace dropdown + recents dropdown + browse button + Compare submit |
| `_chip_compare_topology.html` | Side-by-side chip cards with qubit grid and ref-chip highlighting |

The Differences tab reuses `_compare_diff.html` directly with no wrapper —
the existing template already handles the "missing fit/param sections"
hiding logic via `is defined` guards in Jinja.

---

## Frontend JS (in `app.js`)

Single IIFE at the end of `app.js` exposes:

| Function | Purpose |
|---|---|
| `addChipFromSelect(selEl, kind)` | Add the selected dropdown option as a tag |
| `addChipFromInput(inputEl)` | Add the path typed/browsed into the hidden browse-target input |
| `removeChipFromCompare(btnEl)` | Remove a tag |

The source of truth for the selected list is **the live DOM** — hidden
`<input name="paths">` elements inside the form. No `sessionStorage` or
duplicated state. The Compare button's label and disabled state are
re-synced on every HTMX swap.

The browse-target hidden input lives **outside** the picker form. The
existing `selectBrowserFolder()` helper calls `target.closest("form").requestSubmit()`,
which would otherwise auto-submit the picker with a partial selection.
Keeping the hidden input outside the form makes `closest("form")` return
null and lets the picker's own `change` handler call `addChipFromInput`.

---

## Tests

`tests/test_chip_compare_routes.py` — 13 tests covering:

- Empty picker GET
- Too-few-paths warning (POST)
- HTMX vs full-page rendering of the tabbed view
- Topology tab: chip cards, REF badge, qubit-diff highlights
- Diff tab: state-property highlights, Param section appears when `node.json`
  is present, Param section hidden when it's absent

Run: `conda run -n qm_mng python -m pytest tests/test_chip_compare_routes.py -v`.

---

## Things deliberately deferred

- **Side-by-side full topology dashboard** (with the SVG connectivity graph,
  Plotly histograms, 2Q-RB grid) — would require extracting ~1300 lines of
  inline JS from `_wiring.html` into a reusable `topology.js`. High regression
  risk for v1. The compact-card view ships first; if user feedback wants the
  full dashboard duplicated per chip, that's a follow-up.
- **Chip-level sidebar tree selection** — the existing workspace tree is
  experiment-centric (`Root → Date → Experiment`). Adding a `[+ Compare]`
  link per chip would require restructuring the tree or N buttons per chip.
  The in-page picker (populated from `_detect_workspace_chips`) already
  covers the "pick chips from this workspace" flow.
- **Persisting the chip selection across sessions** — current behavior is
  per-tab, lost on reload. Users can re-pick from the workspace dropdown
  which renders the same chips. If users start asking for sticky selection,
  add a `localStorage` shadow of the form state.
