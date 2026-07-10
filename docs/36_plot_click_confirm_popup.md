# 36: Plot Click → Confirm Popup

> Replaces the auto-apply behavior shipped in `docs/19_plot_click_to_edit_and_2q_rb.md`
> with a per-row confirmation popup. The user reviews and edits each value before
> committing, instead of having the click silently mutate the working copy.

---

## What changed

**Before** (per `docs/19`): clicking a Plotly point in a dataset figure copied
the coordinates to the clipboard and *immediately* POSTed sequential
`/field/edit` calls — one per dot-path in `EXPERIMENT_PATH_MAP` for that
experiment — applying the values to the working copy and refreshing the
Pending Changes tray. The first applied path also auto-navigated the
Explorer tree.

**After**: clicking still copies coordinates to the clipboard and still
navigates the Explorer tree (legacy contextual behaviors are preserved).
But the auto-apply is replaced by a **confirmation popup** with one row
per affected dot-path:

```
┌──────────────────────────────────────────────────────────────────┐
│ Apply update from plot click                                  ×  │
├──────────────────────────────────────────────────────────────────┤
│ Experiment: qubit_spectroscopy_vs_flux · Qubit: qA1              │
├──────────────────────────────────────────────────────────────────┤
│ qubits.qA1.z.joint_offset    previous 0.081      new [0.083]  Apply │
│ qubits.qA1.xy.RF_frequency   previous 6.25e9     new [6.30e9] Apply │
│ qubits.qA1.f_01              previous 6.25e9     new [6.30e9] Apply │
├──────────────────────────────────────────────────────────────────┤
│                                          [Apply All]  [Cancel]   │
└──────────────────────────────────────────────────────────────────┘
```

- Each row shows the dot-path, the current value (`previous`), and an
  editable input pre-filled with the click coordinate (`new`).
- Pressing **Enter** in any input applies that row.
- Per-row **Apply** keeps the popup open and marks just that row as
  `✓ applied` (greyed-out, input becomes read-only). The user can keep
  editing the remaining rows.
- **Apply All** atomically applies every still-pending row. On any
  failure (e.g. type-coercion error), nothing is applied — the
  modifier rolls back the whole batch and the bad row gets an inline
  error.
- **Cancel / ×** dismisses without writing.
- If a click resolves to zero mappings (experiment name unknown to
  `EXPERIMENT_PATH_MAP`), the popup does *not* open — only the
  clipboard copy + toast fire (unchanged from before).

---

## How it's wired

### Two new backend routes (`quam_state_manager/web/routes.py`)

| Method | Path | Purpose |
|---|---|---|
| GET  | `/field/peek?dot_path=…&dot_path=…` | Read current values for one or more dot-paths. Informational — returns `{ok: true, values: {…}, errors: {…}}` with `null` for missing paths plus a per-path entry in `errors`. |
| POST | `/field/edit-batch` | Apply many edits atomically. Accepts either JSON `{updates: [{dot_path, value}, …]}` or form-repeated `dot_path=…&value=…`. Uses `modifier.batch_set`-style atomicity (full rollback on any failure) and returns `{ok, tray_html, results: [{dot_path, applied, error?}, …]}` so the popup can mark individual rows. |

The existing `/field/edit` (single-key, `web/routes.py:896`) is unchanged
and powers the per-row Apply button.

### Frontend (`quam_state_manager/web/static/app.js`)

Replaced the `_autoUpdateFields(mappings, pt)` call inside
`_attachPlotClickHandler` with `_showPlotApplyPopup(mappings, pt, expName, qubitName)`.
Deleted the old `_autoUpdateFields`. Added four new functions:

| Function | Role |
|---|---|
| `_showPlotApplyPopup` | Activate the state, render rows, fire a single `/field/peek` for old values |
| `applyPlotRow(row)` | Per-row Apply — POSTs `/field/edit` (unchanged endpoint) |
| `applyAllPlotRows()` | Apply All — POSTs `/field/edit-batch` (new endpoint) |
| `closePlotApplyPopup()` | Hide overlay, clear rows |

### Template (`quam_state_manager/web/templates/base.html`)

Added a single `#plot-apply-popup` overlay block after the existing
`#new-run-popup`. The CSS reuses the existing `popup-slide-in`
animation and theme tokens.

---

## Atomicity & rollback

`/field/edit-batch` mirrors the locking and rollback strategy of
`modifier.batch_set` (`core/modifier.py:147`) but reports per-path
results rather than raising on failure:

1. Take `store._lock`.
2. For each `(dot_path, value)`, call `set_value(..., _defer_hooks=True)`
   and record success / failure.
3. On any failure, invoke `modifier._rollback(applied_entries)` to
   reverse every previously-applied edit in this batch. Mark the rolled-
   back rows as `applied: false` with `error: "rolled back due to other
   failure(s) in this batch"` so the popup can render them clearly
   distinct from the originally-failing row.
4. On full success, clear the pointer cache and refresh the search
   index *once* (the same single-flush optimization `batch_set` already
   uses).

The route does not call `modifier.batch_set` directly because that
method raises on the first failure and we need per-path error
reporting to drive the popup UX. The duplication is intentional and
contained to ~30 lines.

---

## Tests (`tests/test_field_batch.py`)

11 tests covering:

- `/field/peek` happy path (single, multi), missing path graceful
  fallback, empty query, no-active-context 400.
- `/field/edit-batch` happy path (JSON and form-repeated bodies),
  atomic rollback on invalid path, response includes a fresh
  `tray_html` for the popup to swap, empty-updates 400, no-active-
  context 400.

Run: `conda run -n qm_mng python -m pytest tests/test_field_batch.py -v`.

The JS popup itself is verified manually (the repo has no headless
browser test infrastructure).

---

## What's deliberately kept from `docs/19`

- **Clipboard copy** of `x=…, y=…` on every click — some users do paste
  these into lab notebooks.
- **Explorer tree auto-navigation** to the first resolved dot-path on
  every click — provides context for what the popup is about to apply.
- **`EXPERIMENT_PATH_MAP`** (`app.js:2848`) and the dot-path templating
  in `_resolveExperimentPath` (`app.js:2862`) — unchanged.
- The Pending Changes tray refresh after apply — still happens via the
  `tray_html` field in the response.

The only thing removed is the silent multi-POST `_autoUpdateFields`
chain and its direct mutation of the working copy.
