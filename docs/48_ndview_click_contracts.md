# 48 — N-D Data Viewer (ndview) + Click Contracts (interactive-plot-v2)

Branch: `feat/interactive-plot-v2` (worktree). Replaces the legacy per-figure HDF5
summary pipeline on the dataset detail page with two layers:

1. **ndview** — a crash-free, generic N-D data viewer (the "Data" tab): every
   variable of every `ds_*.h5` file is browsable without per-experiment code.
2. **Click contracts** — the Interactive tab's figures stage the **node-update
   value** (what the calibration node itself would have written to state.json),
   not the raw clicked coordinate.

Both feed the same audited write path: `_openPlotApplyPopup` → `/field/peek` →
`/field/edit[-batch]` → change_log (+`group_id` for atomic multi-field undo) →
working copy → explicit apply-to-live. **No new write path was added.**

## ndview (core/ndview.py + web/static/ndview.js + plot-theme.js)

- **Axis truth**: real HDF5 files carry `DIMENSION_LIST` object refs only (0 of
  ~5,000 surveyed files had `_ARRAY_DIMENSIONS`) — h5py derefs them directly.
  netCDF placeholder scales ("This is a netCDF dimension…") are detected and
  treated as synthetic. The legacy pipeline's length-equality axis guesses are
  gone.
- **Dim classification** is name-based (dim order is inconsistent between
  sibling vars in one file): entity (`qubit`, `qubit_pair`), categorical
  (≤4-long `control_axis`/`qst_basis` → overlay curves), sweep, synthetic.
- **Decimation**: peak-preserving min/max **index** selection for 1-D (kept
  indices ship, so click-snap maps back to true coordinates; dip survival is
  tested), block-mean coarsening for heatmaps. Budgets are element- **and
  byte-aware**: a cube whose JSON exceeds ~4 MB is rebuilt with proportionally
  tighter budgets; no cube ships >~4–5 MB.
- **Cache**: server LRU keyed `(path, mtime, var)` stores **serialized UTF-8
  bytes** (warm hit ≈2 ms server-side; the per-request `uid/which/click` block
  is byte-spliced around the cached cube). Bounded by 24 entries **and** 64 MB
  total. Client cube cache is uid-scoped with a mount-generation token so a
  pending fetch from a previous run can never render into (or poison the cache
  of) the next run.
- **Never-crash contract**: `/dataset/<uid>/ndview[/data]` always returns
  HTTP 200 with classified fallbacks (`holds text data`, unreadable, …). This
  is enforced by corpus-invariant tests over the real archives (~5,000 files,
  41k+ variables, zero exceptions allowed).
- **Renderer convention**: ndview calls `window._plotlyRender(divId, data,
  layout, config)` **positionally** and attaches Plotly click handlers **inside
  the render promise** (a fresh div has no `.on` until Plotly resolves). The
  jsdom selfcheck loads the *real* `app.js` renderer, so a calling-convention
  mismatch fails the suite (this exact P0 shipped once because the old harness
  stubbed the renderer).

### ndview click candidates (core/click_targets.py)

Ranked write-target suggestions for a clicked point. Two tiers:
- node-tier (recognized node families) and coord-tier (absolute-frequency
  coordinate names) — both gated to **absolute axes only** (`_RELATIVE` regex:
  detuning/shift/delta/prefactor axes never produce candidates; a wrong
  suggestion is worse than none).
- Every candidate carries a positive **`dim` binding** (the dim-name pattern it
  reads from); the client resolves that name against the rendered view's x/y
  and takes the coordinate from the matching axis — never by row index, and
  there is deliberately **no fallback** when no axis matches.
- Values are peeked fresh at click time (`/field/peek?dot_path=…`), never baked
  into the payload.

## Click contracts (core/interactive_plots/contracts.py + recipes/)

The single source for "what does this node write when it accepts a fit". Each
recipe bakes the node's own update formula into the figure's `clickable` block
at build time: per-target affine `{path, axis, scale, offset}` plus named
client transforms (`dbm_to_amp`, `ceil4` = ceil(t/4)·4(+const), `wrap01` =
(a·clicked+b) mod 1). Each target carries a `provenance` block (formula +
frozen inputs) rendered in the apply popup.

Hard-won semantic rules (each pinned by a round-trip golden — click at the
node's fit optimum through the baked affine must equal the node's own
`patches[].value` on real archive runs):

- **Staleness trap**: the run's quam_state snapshot is saved *after*
  `update_state` — when `node.json` has `patches`, `patches[].old` is the
  pre-image; when patches are null (declined), the snapshot *is* the run-time
  value. `pre_update_value()` implements patches-first with **qubit-scoped**
  suffixes (a bare `/resonator/f_01` once matched the wrong qubit's patch);
  `old=None` ⇒ view-only (never risk double-apply).
- **RF anchor from the dataset**: `rf_at_run = full_freq[q][0] − detuning[0]` —
  live-drift-proof, independent of the snapshot.
- **Increment vs assign is per-node**: 05b/06-f01/23-flux/24-zz are `+=`
  (offset = pre-update target − anchor); 03/08/02w/15a/15b are absolute
  assigns; 09 joint increments, 09 independent *assigns the delta*; 35a is the
  mod-wrap `(pre + clicked) % 1`.
- **Operation routing**: `run_operation()` reads `run.parameters` **top-level
  first** — DatasetStore flattens `parameters.model` up (nested-only reads are
  a recurring bug class; `_zz`'s artificial-detuning subtraction fell to it
  once).
- **Relative axes are view-only**: DRAG `alpha_prefactor` (unrecoverable when
  `alpha_setpoint` is used), exploratory SNZ 38/39 twins (`update_state ==
  pass`), ramsey/T1 ns-vs-seconds targets (dropped — ×1e9 trap).
- **Registry matching is two-tier**: raw prefix first (zero regression), then a
  normalizer that strips `1Q_/2Q_`, `cz_<N>` graph prefixes, numeric indexes
  (incl. `38_2_`), benign suffixes. **Never gate a recipe on
  `name.startswith("1Q_…")`** — standalone-launched runs don't carry the
  prefix; gate on `_normalize_node_name`.

### Safety gates on the apply path

- The interactive popup passes the run's chip-identity token; a mismatched
  loaded chip ⇒ 409 + explicit confirm (pair names like `qA2-qA1` recur across
  chips — this gate is what stops cross-chip staging).
- Pointer aliases (`operations.x180 = "#./x180_DragCosine"`) are
  through-resolved server-side (`_resolve_edit_path`); `/field/edit` and
  `/field/edit-batch` share the same editability guard.
- Non-blocking amber warnings in the popup for |amplitude| > 1 (OPX full scale)
  and |offset| > 0.5 V (flux DC range) — warn, never block (researcher-trust
  philosophy).
- Unknown client transform types are **skipped**, never degraded to identity.

## Performance (measured on 9p /mnt/d — the deployment FS)

- `build_interactive_figure` has a thread-safe **bundle-input LRU** (4 runs,
  mtime-fingerprint keyed over the exact files read): warm tile ≈20 ms (was
  200–320 ms; tab-open on a 12-tile readout run 1.85 s → ~0.2 s). Files are
  write-once after a run completes, so mtime keying is sound.
- `pre_update_value` reads node.json from `bundle.node_meta` (was: one disk
  read per target).
- Cube warm hits serve cached bytes (~2 ms); `coord_full` is gated at 20k
  points (a 400k-point coord nothing read was the old 6.5 MB payload's bulk).

## Tests

- `tests/test_ndview.py` — synthetic + corpus invariants (auto-skip without the
  archives); `tests/test_interactive_cache.py` — cache correctness/bounds/
  thread-hammer; `tests/test_click_contracts.py` + `tests/test_cz_contracts.py`
  — round-trip goldens on pinned real runs (assert `clickable is not None`
  outright where the archive data is known-good — a contract regression must
  fail, not skip) + synthetic fixtures for node types with zero archive runs
  (39_2 SNZ).
- `tests/ndview_selfcheck.cjs` — jsdom, loads the **real app.js** with a Plotly
  stub (run with `NODE_PATH=<repo>/node_modules node tests/ndview_selfcheck.cjs`).
