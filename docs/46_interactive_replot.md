# 46 — Interactive Replot (Strategy B: reproduce figures from the experiment's own code)

## Problem

The **Interactive** tab on a dataset run reconstructs each experiment's analysis
figures as Plotly so they can be zoomed / hovered / clicked-to-apply. Until now
this was done by **hand-written recipes** (`core/interactive_plots/recipes/*.py`)
— one per experiment family, each re-implementing that experiment's
`plotting.py` in Plotly.

Two problems with recipes-only:

1. **Coverage gaps.** A new or customer-specific experiment with no recipe falls
   back to static PNGs. Example: `05b_resonator_spectroscopy_vs_power_iq` (the
   LabB customer's IQ-circles variant) had **no** interactive recipe — the
   Interactive tab showed only saved PNGs. The test-suite warning still lists it
   among "node types with no interactive recipe".
2. **Maintenance drift.** When the customer edits their `analysis.py` /
   `plotting.py`, the hand-ported recipe silently goes stale. Keeping recipes in
   lock-step with evolving lab code is unbounded work.

## Strategy B — run the real plotting code

Instead of re-implementing each figure, **re-run the experiment's own
`plotting.py`** against the saved datasets, in the user-selected QM env, and turn
the resulting matplotlib Figures into Plotly:

```
quam_state/{state,wiring}.json  ──▶ machine ──▶ qubits        (quam)
ds_raw.h5 / ds_fit.h5           ──▶ xarray datasets
calibration_utils.<util>.plot_* ──▶ matplotlib Figures        (the lab's real code)
iplot_extract.extract_figure    ──▶ structured JSON  (iplot/v1)
replot.mpljson_to_plotly        ──▶ Plotly {data, layout}     (in-process, dep-free)
```

Because the figures **are** the lab's own output, they match exactly, and when
the analysis changes the reproduction tracks it automatically — **no
per-experiment code**. A new `plot_*` function in any `calibration_utils`
package appears with zero changes here (args are bound *by parameter name*).

This is **additive**: recipes stay. Replot is an **opt-in** button on the
Interactive tab (it costs a subprocess + a selected env), so it never slows the
default view.

## Why not mpld3 / mpl→plotly-in-process

- `mpld3` is unmaintained and not installed in the customer env (and risky on
  matplotlib 3.11).
- `plotly.tools.mpl_to_plotly` is lossy on complex figures and would pull plotly
  into the subprocess.
- A small **structured extractor** (`iplot_extract.py`) reading artists back off
  the Axes is dependency-free (numpy+matplotlib already in the QM env), yields
  **native Plotly** (zoom/pan/hover/click), and keeps the State-Manager process
  free of the QM stack — the JSON contract crosses the process boundary.

## Pieces

| File | Role | Runs in |
|------|------|---------|
| `generator/iplot_extract.py` | generic mpl `Figure` → `iplot/v1` JSON (Line2D, scatter, QuadMesh→heatmap, twin-Y, log scale, colorbar-strip detection, annotations) | QM env subprocess |
| `generator/run_interactive_replot.py` | import util, reconstruct qubits, open datasets, discover+call every `plot_*` (args bound by name), extract | QM env subprocess |
| `core/interactive_plots/replot.py` | driver (spawn selected env, cache by data fingerprint) + converter (`iplot/v1` → Plotly) | State-Manager process |
| `web/routes.py` `/dataset/<uid>/replot[/plot]` | menu partial + per-figure Plotly JSON | State-Manager process |
| `web/templates/_dataset_replot.html` | tile menu; tiles carry `data-endpoint="replot/plot"` | — |
| `web/static/app.js` `loadDatasetReplot` | opt-in load; reuses the recipe tab's observer/render/clickable machinery (only `data-endpoint` added) | browser |

The subprocess pattern, env discovery and selection (`get_selected_env`) are the
**same** ones the Generate-Config wizard and Scheduler already use; the env that
has the customer code editable-installed needs no `--source-root` (it imports
`calibration_utils` / `quam_config` directly). `--source-root` is an optional
override.

## Caching & staleness

`replot_run` caches the `iplot/v1` result per run folder, keyed on a fingerprint
of `node.json` + `ds_raw.h5` + `ds_fit.h5` (mtime+size). Re-acquiring the
experiment invalidates it automatically. Editing the **analysis code** (which
lives in the env install, not the run folder) is *not* auto-detected — the user
clicks **↻ Regenerate**, mirroring the Config Viewer's explicit-regenerate model.

## Verified (2026-06-26, LabB `05b` real run, LabB env)

- Subprocess imports the customer util, loads `FluxTunableQuam` from `quam_state`,
  opens both datasets, runs **all 7 `plot_*` functions, 0 errors**.
- Extracted JSON → converter → Plotly rendered via the bundled `plotly.min.js`
  (headless screenshot) reproduces all figures faithfully — incl. the IQ-circles
  concentric plot, the log-Y quality-factor plot with its twin-axis contrast, and
  the dual-axis raw-data heatmaps. Visual match to the saved PNGs.
- `2432 passed, 98 skipped, 0 fail`; new `tests/test_interactive_replot.py`
  (17 converter/driver/route tests in-env + 2 extractor tests in the QM env).

> Dev note: a full click-through *inside the running app* from WSL is blocked
> only by the WSL↔Windows path namespace for the spawned Windows interpreter
> (an artifact of the dev host — production runs Windows-native, where the
> Generate/Scheduler subprocesses already work). Every seam is independently
> proven (subprocess JSON with Windows paths; converter render; route + template
> + JS wiring).

## Red-team hardening (4 parallel audits, all fixed)

- **`--source-root` was `required=True`** in the runner but the driver never
  passes it (the selected env imports `calibration_utils` directly) → argparse
  rejected *every* in-app run. Now optional.
- **Subprocess storm.** The menu call + N lazy tile calls hit `replot_run`
  concurrently; with no guard each spawned its own multi-second QM subprocess.
  Added a `threading.Lock` + per-key in-flight `Event` so they **coalesce onto
  one** subprocess; `_CACHE` reads/writes/eviction are now locked
  (`pop(..., None)`, no `KeyError`). Transient timeouts/errors are **not** cached
  (stay retryable); only figure-bearing results cache. Covered by a 6-thread
  spawn-count test.
- **`_derive_util` missed graph names** (`1Q_…`/`2Q_…`, uppercase Q) → wrong
  module → import failure. Now strips the graph prefix too; the runner also
  **rejects any non-identifier util name** before `import_module` (no dotted
  traversal).
- **Converter correctness.** (a) twinx left/right Y was assigned by axes
  *iteration order* and could swap labels → now keyed on the extracted
  `y_side`/`x_side`; (b) `twiny` dual-X (the `raw_data_with_fit` RF-freq/detuning
  pair) was split into two stacked panels showing the heatmap twice → now folds
  into **one panel with a secondary top X-axis**; (c) the QubitGrid R×C layout was
  collapsed to a vertical stack → now seats panels by the extracted
  `grid{row,col,nrows,ncols}` with per-cell titles.
- **Payload + JSON safety.** Heatmaps downsample to ≤400×400 and lines decimate
  to ≤6000 pts server-side; the runner emits with `allow_nan=False` (non-finite
  → `null`, never an invalid `NaN` token that breaks `JSON.parse`); one colorbar
  per figure (overlaid twiny heatmaps no longer stack two).
- **Pinned/split columns.** The replot container/button selectors use a
  suffix/contains match so they survive the `pinned-` id prefix (the prefix match
  left the pinned column's button dead), mirroring `loadDatasetInteractive`.
- **`/replot/plot` gate.** A direct tile hit now capability-checks before
  spawning, so an unreproducible run / missing env can't kick off a subprocess.

Known remaining limitations (documented, low-risk): per-point scatter sizes/
colours collapse to one value; irregular (curvilinear) `pcolormesh` meshes assume
monotonic axes; `LineCollection`-as-data isn't extracted (only colorbars use it
here); surfaced error lines may contain absolute paths (acceptable for a
single-user desktop tool).

## Click → apply (decoupled semantic layer — Phase 2)

Rendering (this doc) and "click a point → write a chip parameter" are **separate
concerns**. The recipe layer hand-authors a `clickable` map per figure (axis →
chip dot-path); replot figures faithfully *draw* the data but carry no such
semantic mapping, because `plotting.py` doesn't encode one.

Plan: a small, optional **apply map** keyed by `(util, figure_key)` →
`{fit_results field → chip dot-path}`, reusing `core/fit_targets.py` (the
Dataset "Apply fitted value" path). The converter already passes a `clickable`
field through to the frontend, and `_fetchInteractiveFig` already attaches the
click handler when present — so Phase 2 is additive and does not touch the
rendering pipeline. Note the "apply" capability already exists independently via
the **Results** tab; Phase 2 just brings it onto the reproduced figures.

## Extending to other experiments

Nothing per-experiment is required for *rendering*: any run whose node name
derives a `calibration_utils.<util>` exposing `plot_*` functions works. Coverage
is bounded only by (a) the extractor's artist support (extend `iplot_extract` for
new artist types) and (b) the optional Phase-2 apply map.
