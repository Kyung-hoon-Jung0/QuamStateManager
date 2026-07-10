# Interactive tab — recipe coverage & the static-PNG superset

The dataset detail view has two figure tabs:

- **Figures** — the static PNGs the experiment saved (listed from `data.json`'s
  `figures` dict).
- **Interactive** — Plotly figures **reconstructed from the run's HDF5**
  (`ds_raw.h5` / `ds_fit.h5` / `ds_iq_blobs.h5`) so points are zoomable and, for
  many, click-to-edit.

## How the Interactive tab is built

Each experiment type is handled by a *recipe* in
`quam_state_manager/core/interactive_plots/recipes/`. A recipe exposes:

- `FAMILY` — a tuple of `node.json` `metadata.name` **prefixes** it handles.
- `menu(bundle) -> list[FigureSpec]` — cheap; one spec per `(<base>, qubit)`,
  with an `available` flag + `reason` derived from which HDF5 variables exist.
- `build(bundle, key) -> FigureSpec` — heavy; the Plotly `{data, layout}` for
  one `"<base>::<qubit>"` key.

`registry._resolve(name)` picks the **first** recipe whose `FAMILY` prefix
satisfies `name.startswith(prefix)`; otherwise the `fallback` recipe (empty
menu). Because resolution is by prefix and case-sensitive, **a FAMILY string
must exactly match the real `metadata.name`** — verify it against an on-disk run
(`node.json` → `metadata.name`, also embedded in the run folder name), never
guess. A historical bug shipped `…_E_to_F` while the node emits `…_ef`, sending
every e→f run to the empty fallback.

## Static-PNG superset (Interactive ⊇ Figures)

`registry.list_interactive_figures` reconciles the recipe's reconstructed
figures against the run's **saved** figures (`dataset._extract_figure_names`)
and appends a **static tile** (the original PNG, via `/dataset/<id>/fig/<name>`)
for any saved figure the recipe doesn't reproduce — *even when the matched
recipe is the empty fallback*. So a run with figures is never blank in the
Interactive tab, and the tab is always a superset of the Figures tab.

Matching is by *base* name: `_saved_base` strips the container prefix
(`figures.raw_data` → `raw_data`) and a trailing `_<qubit>` (`raw_qA1` → `raw`)
so saved keys line up with the per-qubit `<base>::<qubit>` keys recipes emit.
An *unavailable* recipe figure that has a matching saved PNG is replaced by that
PNG (more useful than a greyed stub). Recipe base names should therefore match
the experiment's saved-figure names (e.g. power Rabi uses base `amplitude`, not
`rabi`) so they dedup instead of double-rendering.

Static tiles render as `<img>` in `_dataset_interactive.html`; the lazy
Plotly-fetch path (`app.js loadDatasetInteractive`) ignores them.

### Non-reconstructable figures (`STATIC_ALLOWLIST`)

Some saved figures genuinely cannot be rebuilt from the run's own HDF5 and are
*expected* to remain static:

- `ramsey_curve`, `spectroscopy_curve` — reference curves loaded from **another**
  run via `*_run_id` (not persisted in this run's `ds_fit`).
- `fir_*` (FIR distortion diagnostics) — matplotlib-only; no backing arrays.

## Regression guard

`tests/test_interactive_coverage`-style checks live in
`tests/test_interactive_plots.py` (`test_interactive_tab_is_superset_of_figures_tab`,
real-data-gated on `<dataset-root>`). For every node type on disk it
asserts (1) every saved figure appears in the Interactive tab, and (2) figures
that appear **only** as static PNGs are limited to the allowlisted
non-reconstructable kinds. A recipe that silently drops a reconstructable figure
(the `parabola_fit` bug class) fails this test until the figure is reconstructed
or the base is consciously added to `_allowlisted_static_base`. Synthetic tests
cover `_saved_base` / `_merge_static`; `test_all_new_experiments_build` confirms
every interactive (non-static) menu key builds to strict-JSON Plotly.

## Adding a new experiment's interactive figures

1. Find the node's real `metadata.name` from an on-disk run; add its prefix to
   the right recipe's `FAMILY` (or write a new recipe + register it in
   `registry._RECIPES`).
2. In `menu()`, emit one `FigureSpec` per figure base × qubit, gating
   `available` on the HDF5 variables actually present. Use the experiment's
   saved-figure name as the base so the static fallback dedups.
3. In `build()`, reconstruct each base from `bundle.raw`/`bundle.fit` (mirror the
   experiment's plotting code; reuse `plotbuild` + `flux_common.fitted_two_panel`
   and the `iq_blobs` builders where applicable).
4. If a figure can't be rebuilt from this run's HDF5, leave it to the static
   fallback and (if a new kind) add it to the allowlist.

## Red-team fixes (this change)

A multi-agent audit of all 17 recipes against the experiment code
(`qualibration_graphs`) and on-disk runs produced:

- **`ramsey_vs_flux`** — added the missing `parabola_fit` fit figure (unfolded
  frequency + parabola + Nyquist boundaries; the reported bug).
- **`resonator`** — added `amplitude_local` (±5×FWHM peak zoom); fixed
  `detrended_phase` for *wide* variants (derive detuning from `RF_frequency`).
- **`readout_opt`** — 1Q_15b now shows `iq_blobs` / `histograms` /
  `confusion_matrix` by loading the separate `ds_iq_blobs.h5`
  (whitelisted in `dataset._H5_WHICH_WHITELIST`) and delegating to the
  `iq_blobs` builders.
- **FAMILY fixes** — `qubit_spectroscopy` `…_E_to_F`/`…_ef`; latent
  coupler/interleaved/GEF prefixes (07, 21b, 21c, 27b, 30/30a). GEF (30/30a)
  returns an empty interactive menu for now (static fallback shows its PNGs);
  the coupler resonator-vs-flux (07) figure is view-only (its click target is the
  qubit-flux knob, wrong for a coupler sweep).
- **`power_rabi`** — base renamed `rabi` → `amplitude` so it dedups against the
  saved PNG.
- **`xyz_delay`** — removed an unused import.
- **Static-PNG superset + regression guard** — the durable fix so this whole bug
  class (figure saved but missing from Interactive) can't silently recur.

Investigated and intentionally **not** changed: the `power_rabi`/15b "key
mismatch" the coverage pass flagged (false positive — handled above), and the
`drag` / `flux_ramsey` / `qubit_spectroscopy _new` items (recipes already
correct).
