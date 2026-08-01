# 67 — Raw Data tab reads NetCDF-classic natively (r13)

## The problem

Since 2026-07-29 every IQCC run's `ds_raw.h5` / `ds_fit.h5` is **not HDF5**:
the new runner env lost `netCDF4`/`h5netcdf`, so xarray's `to_netcdf` fell back
to its scipy engine, which writes **NetCDF-classic (CDF-2, magic `CDF\x02`)**
bytes under the same misleading `.h5` names. h5py refuses those with
"file signature not found", so the Raw Data tab answered
*"Cannot open the data file: …"* for every recent run — while runs before the
flip are genuine HDF5 (`\x89HDF`). Both formats coexist in one workspace
forever, so the reader must sniff **per file**.

Verified ground truth on the real archive (2026-08-01):

- 413/413 post-flip files are exactly CDF-2; 631 pre-flip files are HDF5.
- NetCDF3 carries **native per-variable dimension names** (`var.dimensions`) —
  no `DIMENSION_LIST` dereferencing needed, simpler than the h5py path.
- Data is stored **big-endian** (`>f8`, `>i4`); NaN is stored directly
  (`_FillValue` present but redundant).
- String coords (qubit names) are 2-D char matrices `(qubit, string3)` of
  `|S1` with `_Encoding: utf-8` — rows must be re-joined.
- **Mixed dim order inside one file is real**: in
  `#419_09_qubit_spectroscopy_vs_flux`, `I/Q/IQ_abs` are
  `(qubit, detuning, flux_bias)` while `phase` is
  `(qubit, flux_bias, detuning)`.
- Aux coordinates ride the `coordinates` attr
  (`b"attenuated_current current full_freq"`), same as the h5netcdf files.

The Interactive tab's reader (`interactive_plots/h5reader.py`) gained a
NetCDF3 fallback in v0.8.4; docs/48 recorded the ndview gap as *"deliberate —
the lab-side fix is pip install netCDF4"*. r13 reverses that decision on user
demand: **SM reads what the lab writes.**

## Design — one reader adapter, pipeline untouched

`core/ndview.py`'s h5py surface was exactly two functions —
`probe_file` and `_build_cube_uncached` — plus five helpers. Everything else
(dim classification, default view, peak-preserving decimation, byte budget,
serialization, bytes-LRU) is pure numpy and format-agnostic. So the fix is a
**reader adapter** at the file boundary:

```
_open_reader(path)         # context manager; caller ALREADY holds _h5_lock_for
 ├─ head == b"CDF"  → _NcReader   (scipy.io.netcdf_file, mmap=False)
 └─ anything else   → _H5Reader   (h5py.File — garbage keeps today's
                                    canonical OSError; HDF5 userblock files,
                                    whose signature sits at offset 512/1024,
                                    are never misrouted)
```

Adapter surface (both classes): `keys() / get(name) / dim_meta(h) / read(h) /
read_coord(name) / var_attr(h, a) / coord_attr(name, a) /
is_dim_coord(key, h) / is_placeholder(h) / root_attrs()`. Handles expose
`.shape/.ndim/.dtype`.

**`_H5Reader` delegates verbatim** to the pre-existing module helpers
(`_dim_names_for`, `_read_coord`, `_attr`, `_is_dimension_scale`,
`_is_placeholder_scale`) so HDF5 behavior is provably unchanged; its `get`
folds the five scattered `isinstance(ds, h5py.Dataset)` checks into one place.

**`_NcReader` specifics** (each one a hard-won pitfall):

- **No locking inside the adapter** — `_h5_lock_for` is a plain non-reentrant
  `threading.Lock` already held by both call sites; re-acquiring deadlocks.
- **`mmap=False` is an EAGER whole-file read** in scipy (arrays are owned
  copies; nothing dangles after close). Consequence: the per-variable
  `_MAX_RAW_ELEMENTS` guard fires only after RAM is spent, so the constructor
  adds an `st_size > _MAX_RAW_ELEMENTS * 8` pre-guard that raises `OSError`
  (→ the existing "Cannot open the data file" classification).
- scipy's `netcdf_variable` has **no `.ndim`/`.dtype`/`.size`** — the
  `_NcVar` handle computes them from `.data`. Attributes are read via
  `._attributes.get()` ONLY: scipy injects NC attrs into the instance
  `__dict__` *after* `data`, so a file attr literally named `data` would
  clobber the field under `getattr`.
- `read()` byteswaps big-endian to native and ends with `arr[()]` — h5py's
  `ds[()]` unwraps 0-d datasets to a numpy **scalar**, and without the same
  unwrap a scalar cube ships `"28.5"` as a string.
- `read_coord()` returns **np.ndarray** (the cube consumes `.shape[0]`,
  `.dtype`, object indexing): 2-D `|S1` char matrices are row-joined to a
  1-D object array of strings; the 1M-element guard mirrors `_read_coord`.
- A dimension **without** a same-named variable never appears in `keys()` —
  semantically identical to h5py's placeholder scales (`has_coord=False` →
  classified `synthetic`), so `is_placeholder` is constant `False`.
- dtype strings normalize to native (`float64`, not `>f8`) on the NC side
  ONLY — genuinely big-endian HDF5 datasets ship `>f8` today and the
  byte-identical-rebuild + corpus tests pin that.
- scipy raises `TypeError`/`ValueError` (not `OSError`) on truncated/corrupt
  CDF bodies — the existing generic classification branch absorbs them
  ("Unreadable data file (…)").

Behavioral mapping that falls out for the real files: `qubit`/`detuning`
(dim-coord vars) stay hidden from the card list; the joined qubit names feed
the entity selector; `full_freq`/`current`/`attenuated_current` surface as
`is_coord_var=True` cards after the data vars; per-variable dim order is
preserved exactly (the mixed-order `phase` case).

### UI

Variable cards now show the **dimension names visibly**
(`.ndv-var-dims`, was tooltip-only) — the card list is the "inspect keys +
structure first, then plot" surface the users asked for, and the
all-info-visible principle applies.

### Replot / fit-audit / autofit-replay runners

`generator/run_interactive_replot.py`, `run_fit_audit.py`,
`run_autofit_replay.py` opened datasets with
`xr.open_dataset(p)` → fallback `engine="h5netcdf"` — for CDF bytes in a QM
env without scipy the autodetect fails and h5netcdf can *never* read NetCDF3.
The fallback now sniffs the 3-byte magic and picks `engine="scipy"` for CDF
files (autodetect stays first — envs with netCDF4 keep reading CDF through
it). Inline per file — the runner scripts are standalone by design.

## Verification

- `tests/test_ndview_netcdf.py` (15 tests): synthetic CDF fixtures mirroring
  the real shapes — probe card semantics, entity join, int coords + units,
  NaN→null JSON, IQ partner + lockstep decimation on CDF, per-variable mixed
  dim order, synthetic dim classification, 0-d scalar parity, oversized
  pre-guard, corrupt trio (garbage keeps the canonical h5py message /
  truncated CDF / empty), cache warm-hit + deterministic rebuild, and an
  HDF5-still-h5py regression.
- Full real-corpus sweep (local, both formats): **1,044 files
  (631 HDF5 + 413 CDF), 3,705 cubes, 0 probe failures**, entity-off-axes and
  JSON-safety invariants hold everywhere.
- Interactive skeleton families verified on real runs via the registry:
  08 (1D, incl. a failed-fit run — no NaN garbage, overlay honestly absent),
  08b (2D vs power), 09 (2D vs flux — heatmap `z (11,21) == y×x`), and an
  HDF5-era 06 resonator-vs-flux regression. Recipes were already
  dim-order-aware (`qslice` + per-var `dim_order`).
- Existing guards stay green: `test_ndview.py`, `test_interactive_cache.py`
  (byte-identical rebuild), `test_h5reader_netcdf.py`, `ndview_selfcheck.cjs`.

## Boundaries

- The dead legacy `/h5` routes (`get_h5_summary`/`get_h5_plot_data`, behind
  `TODO(remove-legacy-h5)`) stay h5py-only.
- `dataset._resolve_fit_ref` (`./file.h5#key` refs in data.json) stays
  h5py-only — the v2 nodes write fit_results inline; extend it only if a lab
  actually emits such refs in NetCDF form.
- CDF-5 (`CDF\x05`) does not occur in the archive; scipy cannot read it — it
  would classify as "Unreadable data file", which is the honest answer.

---

## Amendment (2026-08-01, r13 ⑧): axis titles carry the dim name when file metadata lies

A user asked why qubit spectroscopy's Raw Data x-axis said "readout
frequency". SM was displaying exactly what the file declares: the lab's own
v2 node (`08_qubit_spectroscopy.py`, sweep-axes block) copy-pasted
`attrs={"long_name": "readout frequency"}` from the resonator-spec nodes onto
the qubit-DRIVE detuning axis — the on-disk metadata is wrong, and only the
lab can fix the node. SM-side hardening: `ndview.js`'s `axisTitle` now shows
the raw dim NAME alongside any long_name that differs meaningfully after
normalization — `readout frequency (detuning) [Hz]` — while cosmetic-only
differences (`flux bias` vs `flux_bias`) stay single. Pinned behaviorally in
`ndview_selfcheck.cjs` §8 and as source pins in `test_web.py`.
