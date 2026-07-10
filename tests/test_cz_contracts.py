"""2Q/CZ click contracts — round-trip goldens on the REAL archive + synthetic
(dummy-data) fixtures for nodes with no archived runs (39_2 SNZ cond-phase).

Golden principle: build the RECIPE's clickable from the real run, simulate a
click at the node's own fit optimum, and assert the staged value equals the
node.json patch value (what the node actually wrote)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

_example_lab3 = Path("<dataset-root>/example_lab3")
_LabA_CR = Path("<dataset-root>/example_lab_cr")
_LabC = Path("<install-root>")


def _clickable(folder: Path, key_prefix: str = ""):
    from quam_state_manager.core.dataset import DatasetStore
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    store = DatasetStore(folder.parent.parent)
    run = next((r for r in store.runs.values()
                if str(r.folder_path) == str(folder)), None)
    if run is None:
        return None
    menu = list_interactive_figures(run)
    for m in menu:
        if m["available"] and not m.get("static") and m["key"].startswith(key_prefix):
            fig = build_interactive_figure(run, m["key"])
            if fig and fig.get("clickable"):
                return fig["clickable"]
    return None


def _patches(folder: Path) -> list:
    try:
        return json.loads((folder / "node.json").read_text()).get("patches") or []
    except Exception:
        return []


def _patch_val(patches, suffix):
    for p in patches:
        if str(p.get("path", "")).endswith(suffix):
            return float(p["value"]), (float(p["old"]) if p.get("old") is not None
                                       else None)
    return None, None


def _stage(t, clicked):
    tr = t.get("transform")
    if tr and tr.get("type") == "wrap01":
        return (tr["a"] * clicked + tr["b"]) % 1.0
    if tr and tr.get("type") == "ceil4":
        return math.ceil(clicked / 4) * 4 + tr.get("add", 0)
    return t.get("scale", 1) * clicked + t.get("offset", 0)


@pytest.mark.skipif(not _example_lab3.is_dir(), reason="example_lab3 archive absent")
class TestCZGoldens:

    def test_chevron_five_field_click(self):
        """#104: click (cz_len, cz_amp) reproduces all 5 written fields."""
        folder = _example_lab3 / "2026-06-29" / "#104_31_chevron_11_02_061246"
        if not folder.is_dir():
            pytest.skip("golden missing")
        clk = _clickable(folder)
        assert clk is not None, "pinned golden must produce a clickable"
        patches = _patches(folder)
        amp_new, _ = _patch_val(patches, "cz_unipolar/flux_pulse_qubit/amplitude")
        assert amp_new is not None
        with h5py.File(folder / "ds_fit.h5") as f:
            cz_len = float(np.asarray(f["cz_len"]).ravel()[0])
            cz_amp = float(np.asarray(f["cz_amp"]).ravel()[0])
        # amplitudes: clicked y = cz_amp
        amp_ts = [t for t in clk["targets"] if t["path"].endswith(".amplitude")]
        assert len(amp_ts) == 2
        for t in amp_ts:
            assert _stage(t, cz_amp) == pytest.approx(amp_new, rel=1e-12)
        # lengths: clicked x = cz_len → ceil/4*4 (+20 flattop envelope)
        len_ts = [t for t in clk["targets"] if t["path"].endswith((".length", ".flat_length"))]
        assert len(len_ts) == 3
        expect = math.ceil(cz_len / 4) * 4
        got = sorted(_stage(t, cz_len) for t in len_ts)
        assert got == sorted([expect, expect, expect + 20])

    @pytest.mark.parametrize("run_dir,macro", [
        ("#105_32_cz_conditional_phase_061755", "cz_flattop"),
        ("#106_33_cz_conditional_phase_error_amp_062114", "cz_unipolar"),
    ])
    def test_conditional_phase_amp_click(self, run_dir, macro):
        folder = _example_lab3 / "2026-06-29" / run_dir
        if not folder.is_dir():
            pytest.skip("golden missing")
        clk = _clickable(folder)
        assert clk is not None, "pinned golden must produce a clickable"
        new, _ = _patch_val(_patches(folder), f"{macro}/flux_pulse_qubit/amplitude")
        assert new is not None
        with h5py.File(folder / "ds_fit.h5") as f:
            opt = float(np.asarray(f["optimal_amplitude"]).ravel()[0])
        t = clk["targets"][0]
        assert macro in t["path"], f"operation routing: {t['path']}"
        assert _stage(t, opt) == pytest.approx(new, rel=1e-12)

    def test_jazz2n_amp_click(self):
        """#115 (33c): staged clicked-optimum == patch value (absolute axis)."""
        hits = sorted(_example_lab3.rglob("#115_33c_JAZZ2_N*"))
        if not hits:
            pytest.skip("golden missing")
        folder = hits[0]
        clk = _clickable(folder)
        assert clk is not None, "pinned golden must produce a clickable"
        new, _ = _patch_val(_patches(folder), "flux_pulse_qubit/amplitude")
        with h5py.File(folder / "ds_fit.h5") as f:
            opt = float(np.asarray(f["optimal_amplitude"]).ravel()[0])
        t = clk["targets"][0]
        assert _stage(t, opt) == pytest.approx(new, rel=1e-9)

    def test_39b_two_field_click(self):
        """#132 (39b, quadratic_2d off-grid optimum): amp + t_phi_eff patches."""
        hits = sorted(_example_lab3.rglob("#132_39b_JAZZ2_N_SNZ*"))
        if not hits:
            pytest.skip("golden missing")
        folder = hits[0]
        clk = _clickable(folder)
        assert clk is not None, "pinned golden must produce a clickable"
        patches = _patches(folder)
        amp_new, _ = _patch_val(patches, "cz_SNZ/flux_pulse_qubit/amplitude")
        tpe_new, _ = _patch_val(patches, "cz_SNZ/flux_pulse_qubit/t_phi_eff")
        with h5py.File(folder / "ds_fit.h5") as f:
            opt_a = float(np.asarray(f["optimal_amplitude"]).ravel()[0])
            opt_t = float(np.asarray(f["optimal_t_phi_eff"]).ravel()[0])
        amp_t = next(t for t in clk["targets"] if t["path"].endswith(".amplitude"))
        tpe_t = next(t for t in clk["targets"] if t["path"].endswith(".t_phi_eff"))
        assert _stage(amp_t, opt_a) == pytest.approx(amp_new, rel=1e-9)
        assert _stage(tpe_t, opt_t) == pytest.approx(tpe_new, rel=1e-9)

    def test_35a_wrap01_click(self):
        """#118 (35a): (pre + clicked frame) % 1 reproduces both phase patches."""
        hits = sorted(_example_lab3.rglob("#118_35a_cz_phase_compensation_error_amp*"))
        if not hits:
            pytest.skip("golden missing")
        folder = hits[0]
        clk = _clickable(folder)
        assert clk is not None, "pinned golden must produce a clickable"
        patches = _patches(folder)
        with h5py.File(folder / "ds_fit.h5") as f:
            fc = float(np.asarray(f["fitted_control_phase"]).ravel()[0])
            ft = float(np.asarray(f["fitted_target_phase"]).ravel()[0])
        for role, fitted in (("control", fc), ("target", ft)):
            new, old = _patch_val(patches, f"phase_shift_{role}")
            if new is None:
                continue
            t = next(x for x in clk["targets"]
                     if x["path"].endswith(f"phase_shift_{role}"))
            assert t["transform"]["type"] == "wrap01"
            assert t["transform"]["b"] == pytest.approx(old, rel=1e-12), \
                "wrap01 pre must be the patches-first pre-update value"
            assert _stage(t, fitted) == pytest.approx(new, abs=1e-9)


@pytest.mark.skipif(not _LabC.is_dir(), reason="LabC archive absent")
class TestCZGoldensLabC:

    def test_20d_leakage_amp_click(self):
        """#1283 (20d PALEA): optimal_amplitude == coupler amp patch."""
        hits = sorted((_LabC / "dataset").rglob("#1283_cz_20d_cz_leakage_amplification_palea*"))
        if not hits:
            pytest.skip("golden missing")
        folder = hits[0]
        clk = _clickable(folder)
        assert clk is not None, "pinned golden must produce a clickable"
        new, _ = _patch_val(_patches(folder), "coupler_flux_pulse/amplitude")
        with h5py.File(folder / "ds_fit.h5") as f:
            opt = float(np.asarray(f["optimal_amplitude"]).ravel()[0])
        t = clk["targets"][0]
        assert "coupler_flux_pulse" in t["path"]
        assert _stage(t, opt) == pytest.approx(new, rel=1e-9)

    def test_21_old_schema_menu_never_advertises_broken_tile(self):
        """P2-B regression: 2026-03-03 #10076–#10080 (21_cz_phase_compensation)
        store I_control/Q_control instead of state_control/state_target. The
        menu used to gate raw_and_fit on fitted_control alone, advertising a
        tile whose build then KeyError'd (registry-caught → missing figure).
        Now: every advertised interactive tile must actually build."""
        root = _LabC / "2026-03-03"
        folders = sorted(root.glob("#100??_21_cz_phase_compensation_*"))
        if not folders:
            pytest.skip("old-schema 21 runs absent")
        from quam_state_manager.core.dataset import DatasetStore
        from quam_state_manager.core.interactive_plots import (
            build_interactive_figure, list_interactive_figures)
        store = DatasetStore(_LabC)
        by_path = {str(r.folder_path): r for r in store.runs.values()}
        checked = 0
        for folder in folders:
            run = by_path.get(str(folder))
            if run is None:
                continue
            for m in list_interactive_figures(run):
                if m.get("static"):
                    continue
                if m["available"]:
                    fig = build_interactive_figure(run, m["key"])
                    assert fig is not None, \
                        f"{folder.name}: {m['key']} advertised but failed to build"
                else:
                    assert "state_control" in (m["reason"] or ""), \
                        f"{folder.name}: unexpected unavailability: {m['reason']}"
            checked += 1
        assert checked >= 5, f"only {checked} old-schema runs indexed"

    def test_zz_detuning_subtraction_hq100(self):
        """#100 (1Q_24 zz_off_jazz, artificial_detuning_in_mhz=2.0): the plotted
        curve must be |J_eff − 2.0 MHz|, not raw J_eff (P1-A regression: the
        recipe read the NESTED parameters["model"] which DatasetStore flattens
        away, so the baked detuning was always None and the user clicked the
        visual minimum of the WRONG curve into decouple_offset)."""
        folder = _LabC / "dataset" / "2026-06-03" / "#100_1Q_24_zz_off_jazz_142436"
        if not folder.is_dir():
            pytest.skip("golden missing")
        from quam_state_manager.core.dataset import DatasetStore
        from quam_state_manager.core.interactive_plots import (
            build_interactive_figure, list_interactive_figures)
        store = DatasetStore(folder.parent.parent)
        run = next(r for r in store.runs.values()
                   if str(r.folder_path) == str(folder))
        # the flattening the fix must honor: top-level key, NO nested model
        assert (run.parameters or {}).get("artificial_detuning_in_mhz") == 2.0
        assert not isinstance((run.parameters or {}).get("model"), dict)
        key = next(m["key"] for m in list_interactive_figures(run)
                   if m["available"] and not m.get("static"))
        fig = build_interactive_figure(run, key)
        assert fig is not None
        got = np.asarray([np.nan if v is None else v
                          for v in fig["data"][0]["y"]], dtype=float)
        with h5py.File(folder / "ds_fit.h5") as f:
            jeff = np.asarray(f["jeff_smooth"], dtype=float).reshape(-1)
        assert got.shape == jeff.shape
        mask = np.isfinite(jeff)
        assert mask.any()
        assert np.allclose(got[mask], np.abs(jeff - 2.0)[mask], rtol=1e-9), \
            "y must be |J_eff − detuning|, detuning read from the FLATTENED params"
        assert fig["layout"]["yaxis"]["title"]["text"] == "|J_eff − detuning| [MHz]"

    def test_zz_off_jazz_increment(self):
        """An exact-increment zz run: pre + optimal == patch value."""
        cands = sorted(_LabC.rglob("#*zz_off_jazz*"))
        checked = 0
        for folder in cands:
            patches = _patches(folder)
            new, old = _patch_val(patches, "coupler/decouple_offset")
            if new is None or old is None or not (folder / "ds_fit.h5").exists():
                continue
            try:
                with h5py.File(folder / "ds_fit.h5") as f:
                    if "optimal_amplitude" not in f:
                        continue
                    opt = float(np.asarray(f["optimal_amplitude"]).ravel()[0])
            except OSError:
                continue
            if abs((old + opt) - new) > 1e-9:
                continue   # user hand-edited the suggestion (16/24 runs do)
            clk = _clickable(folder)
            if clk is None:
                continue
            t = clk["targets"][0]
            assert t.get("offset") == pytest.approx(old, rel=1e-12), \
                "baked offset must be the PRE-update decouple_offset"
            assert _stage(t, opt) == pytest.approx(new, rel=1e-9)
            checked += 1
            if checked >= 2:
                break
        if checked == 0:
            pytest.skip("no exact-increment zz golden reachable")


# ──────────────────────────────────────────────────────────────────────────
# DUMMY-DATA fixtures — nodes with NO archived runs (39_2) + transform math
# ──────────────────────────────────────────────────────────────────────────

def _write_pair_h5(path: Path, name: str, arrays: dict, coords: dict):
    with h5py.File(path, "w") as f:
        scales = {}
        for cname, values in coords.items():
            d = f.create_dataset(cname, data=np.asarray(values))
            d.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            scales[cname] = d
        for vname, (dims, data) in arrays.items():
            ds = f.create_dataset(vname, data=np.asarray(data))
            for ax, dim in enumerate(dims):
                if dim in scales:
                    ds.dims[ax].attach_scale(scales[dim])


def _make_dummy_run(tmp_path: Path, node_name: str, *, raw, fit, coords_raw,
                    coords_fit, params_model=None, quam_state=None) -> Path:
    folder = tmp_path / "2026-07-03" / f"#900_{node_name}_120000"
    folder.mkdir(parents=True)
    (folder / "node.json").write_text(json.dumps({
        "id": 900, "parents": [], "created_at": "2026-07-03T12:00:00",
        "metadata": {"name": node_name, "status": "successful",
                     "run_start": "2026-07-03T12:00:00",
                     "run_end": "2026-07-03T12:00:30"},
        "data": {"parameters": {"model": dict({"qubit_pairs": ["qA2-qA1"]},
                                              **(params_model or {}))},
                 "outcomes": {}},
        "patches": None,
    }))
    (folder / "data.json").write_text(json.dumps({"fit_results": {}}))
    _write_pair_h5(folder / "ds_raw.h5", node_name, raw, coords_raw)
    _write_pair_h5(folder / "ds_fit.h5", node_name, fit, coords_fit)
    if quam_state:
        qs = folder / "quam_state"
        qs.mkdir()
        (qs / "state.json").write_text(json.dumps(quam_state))
        (qs / "wiring.json").write_text(json.dumps({"network": {}}))
    return folder


class TestDummy392Snz:
    """39_2 SNZ conditional phase has ZERO archived runs — verify the recipe +
    3-field click contract on a synthetic run shaped per the node anatomy."""

    def test_dummy_39_2_clickable(self, tmp_path):
        amps = np.linspace(0.19, 0.22, 7)
        tpes = np.linspace(0.0, 2.0, 5)
        pd = np.random.default_rng(3).uniform(0.3, 0.7, size=(1, 7, 5))
        folder = _make_dummy_run(
            tmp_path, "39_2_snz_conditional_phase",
            raw={"f_state_control": (("qubit_pair", "amplitude", "t_phi_eff"),
                 np.random.default_rng(1).uniform(0, 0.1, size=(1, 7, 5)))},
            fit={"phase_diff": (("qubit_pair", "amplitude", "t_phi_eff"), pd),
                 "amp_full": (("qubit_pair", "amplitude"),
                              amps.reshape(1, -1) * 1.0),
                 "optimal_amplitude": (("qubit_pair",), [0.205]),
                 "optimal_t_phi_eff": (("qubit_pair",), [1.0])},
            coords_raw={"qubit_pair": [b"qA2-qA1"], "amplitude": amps,
                        "t_phi_eff": tpes},
            coords_fit={"qubit_pair": [b"qA2-qA1"], "amplitude": amps,
                        "t_phi_eff": tpes},
            params_model={"operation": "cz_SNZ"},
            quam_state={"qubits": {}, "qubit_pairs": {"qA2-qA1": {"macros": {
                "cz_SNZ": {"flux_pulse_qubit": {"amplitude": 0.2,
                                                "t_phi_eff": 0.8,
                                                "flat_length": 48}}}}}})
        clk = _clickable(folder)
        assert clk is not None, "39_2 dummy must produce a clickable"
        paths = {t["path"] for t in clk["targets"]}
        assert any(p.endswith(".amplitude") for p in paths)
        assert any(p.endswith(".t_phi_eff") for p in paths)
        # flat_length: constant target from the snapshot (scale 0)
        flat = next((t for t in clk["targets"]
                     if t["path"].endswith(".flat_length")), None)
        assert flat is not None and flat["scale"] == 0.0 and flat["offset"] == 48.0
        # simulate a click at (0.21 V, 1.2 ns)
        amp_t = next(t for t in clk["targets"] if t["path"].endswith(".amplitude"))
        tpe_t = next(t for t in clk["targets"] if t["path"].endswith(".t_phi_eff"))
        assert _stage(amp_t, 0.21) == pytest.approx(0.21)
        assert _stage(tpe_t, 1.2) == pytest.approx(1.2)
        assert _stage(flat, 999.0) == 48.0   # click coords never affect it

    def test_dummy_exploratory_39_is_view_only(self, tmp_path):
        """The non-_2 twin never writes state → no clickable."""
        amps = np.linspace(0.19, 0.22, 5)
        tpes = np.linspace(0.0, 2.0, 4)
        folder = _make_dummy_run(
            tmp_path, "39_snz_conditional_phase",
            raw={"f_state_control": (("qubit_pair", "amplitude", "t_phi_eff"),
                 np.zeros((1, 5, 4)))},
            fit={"phase_diff": (("qubit_pair", "amplitude", "t_phi_eff"),
                 np.full((1, 5, 4), 0.5)),
                 "amp_full": (("qubit_pair", "amplitude"), amps.reshape(1, -1))},
            coords_raw={"qubit_pair": [b"qA2-qA1"], "amplitude": amps,
                        "t_phi_eff": tpes},
            coords_fit={"qubit_pair": [b"qA2-qA1"], "amplitude": amps,
                        "t_phi_eff": tpes})
        from quam_state_manager.core.dataset import DatasetStore
        from quam_state_manager.core.interactive_plots import (
            build_interactive_figure, list_interactive_figures)
        store = DatasetStore(folder.parent.parent)
        run = next(iter(store.runs.values()))
        menu = list_interactive_figures(run)
        for m in menu:
            if m["available"] and not m.get("static"):
                fig = build_interactive_figure(run, m["key"])
                assert (fig or {}).get("clickable") is None, \
                    "exploratory 39 must stay view-only"


class TestDummyZzDetuning:
    """P1-A regression: the zz recipe must read artificial_detuning_in_mhz from
    the FLATTENED top-level parameters (DatasetStore folds parameters.model up;
    the nested dict never survives) and subtract it from J_eff."""

    def test_zz_flattened_detuning_is_subtracted(self, tmp_path):
        amps = np.linspace(-0.02, 0.02, 9)
        jeff = np.abs(np.linspace(-3.0, 5.0, 9)) + 2.0        # MHz, all finite
        folder = _make_dummy_run(
            tmp_path, "1Q_24_zz_off_jazz",
            raw={"state_target": (("qubit_pair", "amp"),
                                  np.zeros((1, 9)))},
            fit={"jeff_smooth": (("qubit_pair", "amp"), jeff.reshape(1, -1)),
                 "optimal_amplitude": (("qubit_pair",), [0.005])},
            coords_raw={"qubit_pair": [b"qA2-qA1"], "amp": amps},
            coords_fit={"qubit_pair": [b"qA2-qA1"], "amp": amps},
            params_model={"artificial_detuning_in_mhz": 2.0},
            quam_state={"qubits": {}, "qubit_pairs": {"qA2-qA1": {
                "coupler": {"decouple_offset": 0.1}}}})
        from quam_state_manager.core.dataset import DatasetStore
        from quam_state_manager.core.interactive_plots import (
            build_interactive_figure, list_interactive_figures)
        store = DatasetStore(folder.parent.parent)
        run = next(iter(store.runs.values()))
        # DatasetStore flattening: top-level key present, nested model absent
        assert (run.parameters or {}).get("artificial_detuning_in_mhz") == 2.0
        assert not isinstance((run.parameters or {}).get("model"), dict)
        key = next(m["key"] for m in list_interactive_figures(run)
                   if m["available"] and not m.get("static"))
        fig = build_interactive_figure(run, key)
        assert fig is not None
        got = np.asarray(fig["data"][0]["y"], dtype=float)
        assert np.allclose(got, np.abs(jeff - 2.0), rtol=1e-12), \
            "baked detuning must be 2.0 (flattened read), not None"
        assert not np.allclose(got, jeff), "raw J_eff plotted — detuning lost"
        assert fig["layout"]["yaxis"]["title"]["text"] == "|J_eff − detuning| [MHz]"
        # the '+=' contract still rides the fixed curve: offset = pre-update
        clk = fig["clickable"]
        assert clk is not None
        assert clk["targets"][0]["offset"] == pytest.approx(0.1)


class TestTransformMath:
    """The two new client transforms, mirrored server-side by _stage."""

    def test_wrap01(self):
        assert _stage({"transform": {"type": "wrap01", "a": 1.0, "b": 0.9}},
                      0.2) == pytest.approx(0.1)
        assert _stage({"transform": {"type": "wrap01", "a": 1.0, "b": 0.5}},
                      -0.6) == pytest.approx(0.9)

    def test_ceil4(self):
        assert _stage({"transform": {"type": "ceil4"}}, 45.0) == 48
        assert _stage({"transform": {"type": "ceil4", "add": 20}}, 48.0) == 68
