"""Interactive-figure axis parity + click-contract axis coherence (docs/78 P1).

Two invariants that the existing goldens CANNOT catch:

1. **Axis convention.** The lab's own ``plotting.py`` is not uniform: for a
   *power* sweep it puts frequency on x, but for a *flux* sweep it puts FLUX on
   x and frequency on y (measured 2026-08-06 across all 9 x180-chain families,
   and confirmed against the archived PNGs). SM applied "frequency is always x"
   universally, so every flux family rendered transposed — the physicist's flux
   arch showed up rotated 90°.

2. **Contract coherence.** ``tests/test_click_contracts.py`` pins the VALUE math
   (``staged == scale*clicked + offset``) and never looks at a target's ``axis``
   field. A figure could therefore be transposed while its contracts still read
   the old axis, and every golden would stay green while a click wrote the
   frequency into the flux field (docs/78 §4.3 / R1 — the highest-risk edit in
   the plan). This module pins the missing half: each target's ``axis`` must
   name the layout axis that actually carries that quantity.

Synthetic bundles only — no archive, no env.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core.interactive_plots.recipes.base import Bundle

# The lab's convention, per family (docs/78 §4.1). "what is on x" / "what is on y".
LAB_AXES = {
    "1Q_05_resonator_spectroscopy_vs_power": ("freq", "power"),
    "1Q_06_resonator_spectroscopy_vs_flux": ("flux", "freq"),
    "1Q_07_resonator_spectroscopy_vs_coupler_flux": ("flux", "freq"),
    "1Q_08b_qubit_spectroscopy_vs_power": ("freq", "power"),
    "1Q_09_qubit_spectroscopy_vs_flux": ("flux", "freq"),
    "1Q_10_qubit_spectroscopy_vs_coupler_flux": ("flux", "freq"),
    "1Q_11_power_rabi": ("amp", "pulses"),
}

# Substrings that identify a quantity in an axis title.
_TITLE_MARKS = {
    "freq": ("frequency", "detuning"),
    "flux": ("flux",),
    "power": ("power",),
    "amp": ("amplitude",),
    "pulses": ("pulses",),
}

# Substrings that identify the quantity a click TARGET writes.
_PATH_MARKS = {
    "flux": ("_offset", ".z."),
    "freq": ("f_01", "rf_frequency", "frequency"),
    "power": ("full_scale_power", "readout.amplitude"),
    "amp": ("operations.", ".amplitude"),
}

# Quantities a single axis may legitimately write. A dBm axis writes an
# AMPLITUDE (the node stores amplitude, not dBm — the `dbm_to_amp` /
# `dbm_gridfs` transforms), so "the target's quantity must equal the axis's" is
# only true up to this equivalence. Without it the invariant would go red on
# correct code the moment the fixture grows a realistic snapshot.
_AXIS_WRITES = {
    "flux": {"flux"},
    "freq": {"freq"},
    "power": {"power", "amp"},
    "amp": {"amp", "power"},
    "pulses": {"pulses"},
}


def _quantity_of_title(title: str) -> str | None:
    t = (title or "").lower()
    for q, marks in _TITLE_MARKS.items():
        if any(m in t for m in marks):
            return q
    return None


def _quantity_of_path(path: str) -> str | None:
    p = (path or "").lower()
    # flux first: `qubits.{q}.z.joint_offset` also contains no freq marker, but
    # `resonator.f_01` must never be read as flux
    for q in ("flux", "freq", "power", "amp"):
        if any(m in p for m in _PATH_MARKS[q]):
            return q
    return None


def _axis_title(layout: dict, axis: str) -> str:
    t = (layout.get(f"{axis}axis") or {}).get("title")
    return (t.get("text") if isinstance(t, dict) else t) or ""


# ---------------------------------------------------------------------------
# synthetic bundles
# ---------------------------------------------------------------------------

_NF, _NY = 7, 5
_DET = np.linspace(-5e6, 5e6, _NF)
_FLUX = np.linspace(-0.2, 0.2, _NY)
_POWER = np.linspace(-40, -10, _NY)


def _cell(i_second: int, i_det: int) -> float:
    """An IDENTITY-valued cube cell: the rendered value tells you exactly which
    (second-axis, detuning) sample it came from. Random noise would let a
    double transpose cancel in SHAPE while scrambling the mapping."""
    return float(i_second * 1000 + i_det)


def _raw_2d(second_name: str, second_vals, *, dims_first_is_second: bool):
    """A 2-D |IQ| cube in the loader's own shape (``vars`` + ``dim_order``).

    ``dims_first_is_second`` mirrors the archives' two real orderings (some
    store (second, detuning), others (detuning, second)) — docs/78 §4.2. Both
    are exercised for every family, so no orientation guard has a dead half.
    """
    base = np.array([[_cell(i, j) for j in range(_NF)]
                     for i in range(len(second_vals))], dtype=float)
    dims = ["qubit", second_name, "detuning"] if dims_first_is_second \
        else ["qubit", "detuning", second_name]
    arr = base[None, :, :] if dims_first_is_second else base.T[None, :, :]
    return {
        "vars": {"IQ_abs": arr},
        "dim_order": {"IQ_abs": dims},
        "coords": {"detuning": _DET, second_name: second_vals,
                   "qubit": np.array(["qA1"], dtype=object)},
        "attrs": {}, "root_attrs": {}, "qubits": ["qA1"],
    }


def _fit(**scalars):
    return {"vars": {k: np.array([v]) for k, v in scalars.items()},
            "dim_order": {k: ["qubit"] for k in scalars},
            "coords": {"qubit": np.array(["qA1"], dtype=object)},
            "attrs": {}, "root_attrs": {}, "qubits": ["qA1"]}


def _quam_state(flux_point="joint"):
    # Realistic enough that the POWER families produce a clickable: without an
    # opx_output full-scale, resonator_2d._amp_conversion returns None and
    # 1Q_05's contract test skips — silently losing the freq-on-x control the
    # whole convention asymmetry rests on.
    return {"qubits": {"qA1": {
        "f_01": 5.0e9,
        "xy": {"RF_frequency": 5.0e9,
               "opx_output": {"full_scale_power_dbm": -10.0},
               "operations": {"saturation": {"amplitude": 0.1},
                              "x180": {"amplitude": 0.2}}},
        "resonator": {"f_01": 7.0e9, "RF_frequency": 7.0e9,
                      "opx_output": {"full_scale_power_dbm": -10.0},
                      "operations": {"readout": {"amplitude": 0.05}}},
        "z": {"flux_point": flux_point, "joint_offset": 0.05,
              "independent_offset": 0.05},
    }}}


def _bundle(name, raw, fit, *, full_freq=True):
    if full_freq:
        raw = dict(raw)
        raw["vars"] = dict(raw["vars"])
        raw["dim_order"] = dict(raw["dim_order"])
        raw["vars"]["full_freq"] = (7.0e9 + _DET)[None, :]
        raw["dim_order"]["full_freq"] = ["qubit", "detuning"]
    return Bundle(run=None,
                  node_meta={"metadata": {"name": name},
                             "data": {"parameters": {"model": {}}}},
                  raw=raw, fit=fit,
                  raw_vars=set(raw["vars"]), fit_vars=set(fit["vars"]),
                  raw_coords=set(raw["coords"]), fit_coords=set(fit["coords"]),
                  qubit_names=["qA1"], quam_state=_quam_state())


def _rabi_raw():
    """power_rabi's own shape: (qubit, nb_of_pulses, amp_prefactor) + full_amp."""
    npulse = np.array([1.0, 3.0, 5.0])
    pref = np.linspace(0.8, 1.2, _NF)
    z = np.random.default_rng(1).normal(size=(1, len(npulse), _NF))
    return {
        "vars": {"I": z, "full_amp": (pref * 0.2)[None, :]},
        "dim_order": {"I": ["qubit", "nb_of_pulses", "amp_prefactor"],
                      "full_amp": ["qubit", "amp_prefactor"]},
        "coords": {"nb_of_pulses": npulse, "amp_prefactor": pref,
                   "qubit": np.array(["qA1"], dtype=object)},
        "attrs": {}, "root_attrs": {}, "qubits": ["qA1"],
    }


def _built(name, key="amplitude::qA1", *, second_first=None):
    """Build the primary 2-D figure for a family through the real registry.

    ``second_first`` picks the cube's dim order; ``None`` uses the order the
    real archives of that family happen to ship. Tests parametrize BOTH so a
    transpose guard can never have a dead branch.
    """
    from quam_state_manager.core.interactive_plots import registry

    if "power_rabi" in name:
        raw, full_freq = _rabi_raw(), False
    else:
        if "flux" in name:
            second, vals = "flux_bias", _FLUX
        else:
            second, vals = "power", _POWER
        order = ("06_" in name or "07_" in name) if second_first is None \
            else second_first
        raw = _raw_2d(second, vals, dims_first_is_second=order)
        full_freq = True
    fit = _fit(idle_offset=0.01, frequency_shift=1.0e6, resonator_frequency=7.0e9,
               qubit_frequency=5.0e9, success=1.0, num_crossings=1.0,
               opt_amp=0.2, opt_amp_prefactor=1.0)
    recipe = registry._resolve(name)
    bundle = _bundle(name, raw, fit, full_freq=full_freq)
    return recipe, recipe.build(bundle, key)


# ---------------------------------------------------------------------------
# 1 — the figure's axes must follow the LAB's convention
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(LAB_AXES))
def test_layout_axes_match_the_lab_convention(name):
    want_x, want_y = LAB_AXES[name]
    _, spec = _built(name)
    # The synthetic bundle is well-formed by construction, so an unavailable
    # figure is a DEFECT, not an inapplicable case. Skipping here would let a
    # broken orientation guard hide behind an honest "unavailable" degrade.
    assert spec is not None and spec.available and spec.figure is not None, (
        f"{name}: the recipe refused a well-formed synthetic bundle "
        f"({getattr(spec, 'reason', None)!r})")
    layout = spec.figure["layout"]
    got_x = _quantity_of_title(_axis_title(layout, "x"))
    got_y = _quantity_of_title(_axis_title(layout, "y"))
    assert (got_x, got_y) == (want_x, want_y), (
        f"{name}: SM draws x={got_x!r}/y={got_y!r} "
        f"({_axis_title(layout, 'x')!r} / {_axis_title(layout, 'y')!r}) but the "
        f"lab's plotting.py draws x={want_x}/y={want_y} — a transposed figure "
        f"is not the signature the physicist (or the judge) was taught")


# ---------------------------------------------------------------------------
# 2 — every click target must read the axis that carries its quantity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("second_first", [True, False],
                         ids=["cube=(second,det)", "cube=(det,second)"])
@pytest.mark.parametrize("name", sorted(LAB_AXES))
def test_rendered_cell_maps_to_the_right_sample(name, second_first):
    """The rendered z[j][i] must be the sample at (that y, that x).

    Shape alone is blind: a wrong dim-order guard plus the deliberate transpose
    cancel in shape while scrambling the mapping, so the map renders MIRRORED
    with no error and a click reads the wrong cell. With an identity-valued
    cube the cell value names its own source sample, and BOTH archive dim
    orders are exercised so no orientation branch stays dead.
    """
    if "power_rabi" in name:
        pytest.skip("power_rabi ships its own cube shape")
    _, spec = _built(name, second_first=second_first)
    # The synthetic bundle is well-formed by construction, so an unavailable
    # figure is a DEFECT, not an inapplicable case. Skipping here would let a
    # broken orientation guard hide behind an honest "unavailable" degrade.
    assert spec is not None and spec.available and spec.figure is not None, (
        f"{name}: the recipe refused a well-formed synthetic bundle "
        f"({getattr(spec, 'reason', None)!r})")
    heat = next((t for t in spec.figure["data"] if t.get("type") == "heatmap"), None)
    if heat is None:
        pytest.skip(f"{name}: not a heatmap figure")
    layout = spec.figure["layout"]
    z = np.asarray(heat["z"], dtype=float)
    xs, ys = np.asarray(heat["x"], dtype=float), np.asarray(heat["y"], dtype=float)
    assert z.shape == (ys.size, xs.size)

    second_vals = _FLUX if "flux" in name else _POWER
    x_is_second = _quantity_of_title(_axis_title(layout, "x")) in ("flux", "power")

    # The cube encodes its two indices with VERY different weights
    # (second index x1000, detuning index x1), so the per-step change along each
    # rendered axis identifies which index that axis walks. This is invariant to
    # any positive rescaling the recipe applies (several convert V -> mV), which
    # an exact-value comparison is not.
    step_x = abs(z[0][1] - z[0][0])
    step_y = abs(z[1][0] - z[0][0])
    assert step_x > 0 and step_y > 0, "identity cube must vary along both axes"
    x_walks_second = step_x > step_y
    assert x_walks_second == x_is_second, (
        f"{name} [{second_first=}]: the x axis is labelled "
        f"{_axis_title(layout, 'x')!r} but the data walks the "
        f"{'second-sweep' if x_walks_second else 'detuning'} index along x "
        f"(step_x={step_x}, step_y={step_y}) — the map is transposed relative "
        f"to its labels, so every click reads the wrong sample")
    # monotonic in both directions ⇒ no mirroring
    assert np.all(np.diff(z[0]) > 0) and np.all(np.diff(z[:, 0]) > 0), (
        f"{name} [{second_first=}]: the rendered map is mirrored along an axis")
    # sanity: the axis labelled with the swept quantity really carries its coord
    ref = xs if x_is_second else ys
    assert np.allclose(sorted(ref), sorted(second_vals))


@pytest.mark.parametrize("name", sorted(LAB_AXES))
def test_heatmap_data_matches_its_axis_labels(name):
    """The DATA must move with the labels.

    Swapping only the axis titles (or only the heatmap arguments) leaves a
    figure that is internally inconsistent but still renders: the colours sit
    on the wrong axes while the labels claim otherwise, and every click lands
    somewhere else entirely. Neither the title check above nor the click
    goldens would notice, so assert the x/y arrays against the coordinate each
    title names, and z against ``[len(y)][len(x)]``.
    """
    _, spec = _built(name)
    # The synthetic bundle is well-formed by construction, so an unavailable
    # figure is a DEFECT, not an inapplicable case. Skipping here would let a
    # broken orientation guard hide behind an honest "unavailable" degrade.
    assert spec is not None and spec.available and spec.figure is not None, (
        f"{name}: the recipe refused a well-formed synthetic bundle "
        f"({getattr(spec, 'reason', None)!r})")
    heat = next((t for t in spec.figure["data"] if t.get("type") == "heatmap"), None)
    if heat is None:
        pytest.skip(f"{name}: not a heatmap figure")
    layout = spec.figure["layout"]
    xs = np.asarray(heat["x"], dtype=float)
    ys = np.asarray(heat["y"], dtype=float)
    z = np.asarray(heat["z"], dtype=float)

    known = {"flux": _FLUX, "power": _POWER}
    for axis, arr in (("x", xs), ("y", ys)):
        want = _quantity_of_title(_axis_title(layout, axis))
        ref = known.get(want)
        if ref is None:
            continue                          # freq/amp/pulses: range-checked below
        assert arr.size == len(ref) and np.allclose(sorted(arr), sorted(ref)), (
            f"{name}: the {axis} axis is labelled {want!r} but does not carry "
            f"the {want} coordinate — labels and data disagree")
    # frequency, wherever it is, must read as GHz (the recipes divide by 1e9)
    for axis, arr in (("x", xs), ("y", ys)):
        if _quantity_of_title(_axis_title(layout, axis)) == "freq" and arr.size:
            assert 0.1 < float(np.nanmedian(arr)) < 100.0, (
                f"{name}: the {axis} axis is labelled a frequency but its "
                f"values are not GHz-scaled")
    assert z.shape == (ys.size, xs.size), (
        f"{name}: heatmap z must be [y][x] = [{ys.size}][{xs.size}], got "
        f"{z.shape} — a transposed cube renders a mirrored map")


@pytest.mark.parametrize("name", sorted(LAB_AXES))
def test_click_targets_read_the_axis_carrying_their_quantity(name):
    _, spec = _built(name)
    # The synthetic bundle is well-formed by construction, so an unavailable
    # figure is a DEFECT, not an inapplicable case. Skipping here would let a
    # broken orientation guard hide behind an honest "unavailable" degrade.
    assert spec is not None and spec.available and spec.figure is not None, (
        f"{name}: the recipe refused a well-formed synthetic bundle "
        f"({getattr(spec, 'reason', None)!r})")
    clickable = spec.clickable
    if not clickable or not clickable.get("targets"):
        pytest.skip(f"{name}: no clickable targets (deliberate for some families)")
    layout = spec.figure["layout"]
    on_axis = {"x": _quantity_of_title(_axis_title(layout, "x")),
               "y": _quantity_of_title(_axis_title(layout, "y"))}
    default_axis = clickable.get("axis")
    for t in clickable["targets"]:
        want = _quantity_of_path(t.get("path", ""))
        if want is None:
            continue                      # nothing to assert about this path
        axis = t.get("axis", default_axis)
        # A 2-D figure has TWO axes, so a target that names neither is not
        # "not applicable" — the client falls back to x, silently reading the
        # wrong quantity. Skipping it here would leave the highest-consequence
        # leg unpinned, which is exactly the docs/78 §4.3 hazard.
        assert axis in ("x", "y"), (
            f"{name}: target {t['path']} carries no usable axis ({axis!r}) on a "
            f"2-D figure — the client would default it to x")
        allowed = _AXIS_WRITES.get(on_axis[axis], {on_axis[axis]})
        assert want in allowed, (
            f"{name}: target {t['path']} writes a {want} value but reads "
            f"axis={axis!r}, which carries {on_axis[axis]!r} (may write "
            f"{sorted(allowed)}). Transposing a figure without swapping its "
            f"contract makes a click write the wrong field (docs/78 §4.3).")


# ---------------------------------------------------------------------------
# 3 — every scoped family must actually have a recipe
# ---------------------------------------------------------------------------

def test_every_scoped_family_has_a_recipe():
    from quam_state_manager.core.interactive_plots import registry
    from quam_state_manager.core.interactive_plots.recipes import fallback

    missing = [n for n in LAB_AXES if registry._resolve(n) is fallback]
    assert not missing, (
        f"no Interactive recipe for {missing} — the tab shows only static PNGs "
        f"and offers no click-to-apply for those nodes")


# ---------------------------------------------------------------------------
# 4 — real-archive tier: the rendered axes must carry the run's OWN coordinates
# ---------------------------------------------------------------------------
# Placeholder root (repo scrub doctrine) — skips unless pointed at real data.
_ARCHIVE = Path("<data-root>/archive")

_REAL_CASES = [
    ("*_06_resonator_spectroscopy_vs_flux_*", "flux_bias", "full_freq"),
    ("*_07_resonator_spectroscopy_vs_coupler_flux_*", "flux_bias", "full_freq"),
    ("*_09_qubit_spectroscopy_vs_flux_*", "flux_bias", "full_freq"),
    ("*_10_qubit_spectroscopy_vs_coupler_flux_*", "flux_bias", "full_freq"),
]


@pytest.mark.skipif(not _ARCHIVE.is_dir(), reason="real archive not available")
@pytest.mark.parametrize("pattern,x_coord,y_var", _REAL_CASES)
def test_real_run_axes_carry_the_expected_coordinates(pattern, x_coord, y_var):
    """A transpose regression is invisible to the value-math goldens, but the
    rendered x/y RANGES cannot lie: on a flux family, x must span the run's own
    ``flux_bias`` coord and y its ``full_freq`` (docs/78 §4.2 / R1)."""
    from quam_state_manager.core.interactive_plots import registry

    # rglob, not a fixed two-level glob: real archives also nest as
    # <root>/<chip>/<date>/#run (docs/78 §13.5), and a depth-pinned pattern
    # would make this tier silently unreachable even when repointed at data.
    folders = sorted(_ARCHIVE.rglob(pattern))
    folders = [f for f in folders if f.is_dir() and (f / "ds_raw.h5").is_file()]
    if not folders:
        pytest.skip(f"no run matching {pattern}")

    class _Run:
        folder_path = str(folders[-1])
        experiment_name = folders[-1].name
        qubits: list = []
        fit_results: dict = {}

    run = _Run()
    menu = [m for m in registry.list_interactive_figures(run)
            if not m["static"] and m.get("available")]
    if not menu:
        pytest.skip("no interactive figure for this run")
    built = registry.build_interactive_figure(run, menu[0]["key"])
    assert built, "a listed-available figure must build"
    heat = next((t for t in built["data"] if t.get("type") == "heatmap"), None)
    assert heat is not None, "flux families render a heatmap"

    from quam_state_manager.core.interactive_plots import h5reader
    raw = h5reader.load_dataset(run, "ds_raw")
    flux = np.asarray(raw["coords"][x_coord], dtype=float)
    xs = np.asarray(heat["x"], dtype=float)
    ys = np.asarray(heat["y"], dtype=float)
    assert xs.size == flux.size and np.allclose(sorted(xs), sorted(flux)), (
        "x must carry the run's flux coord — a transposed figure puts "
        "frequency here and every click writes the wrong field")
    # y is the absolute RF axis in GHz
    assert ys.size and 0.1 < float(np.nanmedian(ys)) < 100.0, (
        f"y must be the RF frequency axis in GHz, got median "
        f"{float(np.nanmedian(ys))}")
    z = np.asarray(heat["z"], dtype=float)
    assert z.shape == (ys.size, xs.size), \
        f"heatmap z must be [y][x] = [{ys.size}][{xs.size}], got {z.shape}"
