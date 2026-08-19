"""Ramsey-vs-coupler-flux interactive recipe (docs/99, 1.0-prep).

All three archived generations of the family (17b / 21a / 10b — 64 real runs)
used to resolve to ``fallback`` (an EMPTY Interactive menu). Dispatch pins run
always; figure-build goldens run against the real archives when present
(placeholder roots, overridable via ``SM_REAL_ARCHIVES`` — same convention as
the other real-data suites).
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(os.environ.get("SM_REAL_ARCHIVES", "<work-root>"))
_CANDIDATES = [
    (_ROOT / "dataset" / "AS_10TQ9TC", "#*17b_ramsey_vs_coupler_flux*"),
    (_ROOT / "Novera9Q", "#*21a_ramsey_vs_coupler_flux*"),
    (_ROOT / "Novera9Q", "#*10b_ramsey_vs_coupler_flux*"),
    # date-dir layout (<root>/<date>/#N_...) — the CQT archive shape the
    # docs/124 M-12 inversion was executed on (run #490 of 2026-08-14)
    (_ROOT, "#*17b_ramsey_vs_coupler_flux*"),
    (_ROOT, "#*21a_ramsey_vs_coupler_flux*"),
    (_ROOT, "#*10b_ramsey_vs_coupler_flux*"),
]


def _find_runs():
    out = []
    for root, pat in _CANDIDATES:
        if not root.is_dir():
            continue
        hits = sorted(root.glob(f"*/{pat}"))
        if hits:
            out.append(hits[-1])          # newest of the family
    return out


class TestDispatch:
    def test_all_generations_resolve_to_the_recipe(self):
        from quam_state_manager.core.interactive_plots.registry import _resolve
        for name in ("1Q_17b_ramsey_vs_coupler_flux",
                     "17b_ramsey_vs_coupler_flux",
                     "21a_ramsey_vs_coupler_flux",
                     "10b_ramsey_vs_coupler_flux"):
            r = _resolve(name)
            assert getattr(r, "__name__", "").endswith(
                "ramsey_vs_coupler_flux"), (name, r)

    def test_plain_ramsey_families_unaffected(self):
        from quam_state_manager.core.interactive_plots.registry import _resolve
        # the coupler recipe must not swallow the qubit-flux or plain ramsey
        assert not getattr(_resolve("09_ramsey"), "__name__", "").endswith(
            "ramsey_vs_coupler_flux")
        assert not getattr(_resolve("1Q_09b_ramsey_vs_flux_calibration"),
                           "__name__", "").endswith("ramsey_vs_coupler_flux")

    def test_empty_bundle_menu_is_honest(self):
        from quam_state_manager.core.interactive_plots.recipes import (
            ramsey_vs_coupler_flux as r)
        b = SimpleNamespace(raw=None, fit=None, raw_vars=set(),
                            fit_vars=set(), node_meta={}, qubit_names=[],
                            run=SimpleNamespace(qubit_pairs=["pA"]))
        tiles = r.menu(b)
        assert len(tiles) == 2
        assert not any(t.available for t in tiles)
        assert all(t.reason for t in tiles)


def _make_bundle(run):
    from quam_state_manager.core.interactive_plots import h5reader
    from quam_state_manager.core.interactive_plots.registry import (
        Bundle, _node_name)
    _name, node_meta = _node_name(run)
    raw = h5reader.load_dataset(run, "ds_raw")
    fit = h5reader.load_dataset(run, "ds_fit")
    return Bundle(
        run=run, node_meta=node_meta,
        fit_results=getattr(run, "fit_results", {}) or {},
        raw=raw, fit=fit,
        raw_vars=set(raw["vars"]) if raw else set(),
        fit_vars=set(fit["vars"]) if fit else set(),
        raw_coords=set(raw["coords"]) if raw else set(),
        fit_coords=set(fit["coords"]) if fit else set(),
        quam_state=h5reader.load_quam_state(run),
    )


def _run_info(folder: Path):
    from quam_state_manager.core.dataset import DatasetStore
    store = DatasetStore(folder.parent.parent)
    for rid in sorted(store.runs):
        if str(store.runs[rid].folder_path) == str(folder):
            return store.runs[rid]
    return None


@pytest.mark.skipif(not _find_runs(), reason="real coupler archives absent")
class TestRealRunGoldens:
    def test_builds_on_every_generation_and_both_formats(self):
        from quam_state_manager.core.interactive_plots.recipes import (
            ramsey_vs_coupler_flux as r)
        runs = _find_runs()
        assert runs, "gate mismatch"
        for folder in runs:
            info = _run_info(folder)
            assert info is not None, folder
            b = _make_bundle(info)
            tiles = r.menu(b)
            avail = {t.key.split("::", 1)[0]: t.available for t in tiles}
            assert avail.get("fringes"), (folder.name, tiles)
            assert avail.get("freq"), (folder.name, tiles)
            for t in tiles:
                fig = r.build(b, t.key)
                assert fig.available, (folder.name, t.key, fig.reason)
                assert fig.figure and fig.figure.get("data"), (folder.name,
                                                               t.key)
                # doctrine: relative flux axis + bookkeeping-only node
                # update => view-only, never a staged write
                assert fig.clickable is None, (folder.name, t.key)
            # fringes is a heatmap whose grid matches the coords exactly,
            # oriented IDLE ON X — the lab's own figure for this family and
            # ndview's docs/122 rank. The first version of this pin froze the
            # opposite orientation in place, which is how the Interactive tile
            # shipped transposed against BOTH the Raw-Data tab and the lab's
            # static PNG beside it (docs/124 M-12: a green pin on each side,
            # no pin across them — the cross-surface assert below closes that).
            fr = r.build(b, tiles[0].key)
            hm = fr.figure["data"][0]
            n_flux = len(b.raw["coords"]["coupler_flux"])
            n_idle = len(b.raw["coords"]["idle_times"])
            assert len(hm["x"]) == n_idle and len(hm["y"]) == n_flux
            assert len(hm["z"]) == n_flux and len(hm["z"][0]) == n_idle
            # cross-surface: ndview's axis rank must agree (idle before flux
            # means idle takes x there too — the two tabs render the same run
            # the same way, which is customer report docs/122 #1's whole ask)
            from quam_state_manager.core.ndview import _AXIS_RANK
            assert _AXIS_RANK["idle_times"] < _AXIS_RANK["coupler_flux"]
