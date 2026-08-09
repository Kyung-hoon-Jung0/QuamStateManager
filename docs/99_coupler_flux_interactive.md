# 99 — Coupler-flux experiments in the Interactive tab

*2026-08-10, 1.0-prep. The report was "Interactive still misses a lot —
res spec vs coupler flux and other basics — and we now have plenty of
tunable-coupler data." The diagnosis came in two halves: most of the coupler
suite ALREADY works (and was verified live on the customer's real
tunable-coupler runs), and one whole family — the ramsey-vs-coupler-flux trio
— genuinely resolved to an empty menu. That family now has a recipe.*

## Verified already working (live, on the real chip's 2026-08 runs)

| Family | Recipe | Verified |
|---|---|---|
| `07_resonator_spectroscopy_vs_coupler_flux` | `resonator_2d` (view-only by doctrine — the node's own update is a stub) | menu + 66 KB heatmap payload, absolute flux axis, idle-offset line |
| `10_qubit_spectroscopy_vs_coupler_flux` | dedicated recipe (view-only — node writes bookkeeping extras only) | dispatch |
| `18a_coupler_zero_point(_coarse)` | `cz_2d_maps` (assign contracts: `qp.detuning`, CZ coupler amp) | dispatch |
| `23/24_zz_off_jazz` | `cz_2d_maps` — **the** `decouple_offset` `+=` increment contract, patches-aware pre-update anchor | menu + map + clickable `Shift coupler decouple offset` on run `#169` |
| `21b/21c` long distortion | `flux_qubitspec` / `flux_ramsey` | dispatch |

The NetCDF-classic reader (docs/67) already covers the newest chip's
`ds_*.h5` files, so none of the above depended on file format. Anyone seeing
these as "unimplemented" on a running server is on a pre-0.9.5 build — the
recipes shipped with the docs/78 P0/P2–P8 merge.

## New: `ramsey_vs_coupler_flux` (17b / 21a / 10b)

64 archived runs across three node generations (incl. the tunable-coupler
chip's newest session) resolved to `fallback` — an empty Interactive menu.
The registry's tier-2 normalized match correctly refuses `_vs_coupler_flux`
as a benign suffix of `ramsey` (it IS a different experiment), so this needed
a real recipe, not a matcher tweak.

Two tiles per pair, mirroring the lab's own two figures:

- **Ramsey fringes vs coupler flux** — raw `state` heatmap over
  (coupler flux × idle time), flux on x (docs/78 §4.1 convention), dims
  oriented by NAME (the cube ships either order across generations).
- **Qubit frequency vs coupler flux** — `ds_fit.qubit_frequency` per flux,
  with 17b's branch-resolution context (`…_above`/`…_below` dotted) and its
  detected crossings marked; unit auto-selects MHz/Hz from the data.

**View-only, deliberately**: the flux axis is a RELATIVE pulse amplitude on
top of `coupler.decouple_offset` (relative axes are always view-only,
docs/48), and the node's own `update_state` writes only bookkeeping keys —
the real `decouple_offset` calibration lives in the JAZZ recipe's increment
contract. Also fixed: `resonator_2d`'s menu tile said "Resonator vs flux"
for the coupler variant (the build already knew better).

## Pinned

`tests/test_ramsey_vs_coupler_flux.py` — dispatch for all generation
spellings; plain-ramsey families unaffected; honest empty-bundle menu; and a
real-archive golden (skip-if-absent, `SM_REAL_ARCHIVES` override) that builds
BOTH tiles on all three generations across BOTH file formats
(netCDF-classic + HDF5), pins the heatmap grid against the coords, and pins
`clickable is None`. Interactive regression suites green (78 passed).
Screenshot: `D:\work\sm-screenshots\2026-08-10_1.0-prep\s5_ramsey_vs_coupler_flux.png`.

## Still fallback (known, low-priority)

`22_coupler_flux_short_distortion` (2 runs) and `18/18b_xy_coupler_delay`
(51 runs, scalar-delay scan) — inventoried with their write targets in the
1.0-prep archaeology; neither is a 2-D map a recipe would transform, and
their node-side updates (filter extend / delay increment) have no natural
click semantics. Left to demand.
