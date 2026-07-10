"""Click contracts — the clicked-coordinate → node-update-value math.

ROUND-TRIP GOLDENS against the REAL archive (auto-skip without it): simulate a
click at the fit's own optimum and assert the baked affine coefficients
reproduce what the calibration node itself computed/wrote. This is the whole
point of the feature — the value the user updates, extracted from the graph.

Also pins the ndview candidate gate: a RELATIVE axis (detuning/prefactor) must
never offer raw-value staging into absolute fields (the ~2e6-into-f_01 bug).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

_LabA = Path("<dataset-root>/example_lab")
_LabC_ARCHIVE = Path("<install-root>/dataset")
_LabC_TOP = Path("<install-root>")


def _find_run(roots_and_globs):
    for root, pattern in roots_and_globs:
        if not root.is_dir():
            continue
        hits = sorted(root.rglob(pattern))
        for h in hits:
            if (h / "ds_raw.h5").exists() or (h / "ds_fit.h5").exists():
                return h
    return None


def _make_bundle(run):
    """Build a recipe Bundle exactly as registry.build_interactive_figure does."""
    from quam_state_manager.core.interactive_plots import h5reader
    from quam_state_manager.core.interactive_plots.registry import Bundle, _node_name
    name, node_meta = _node_name(run)
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


def _bundle_for(folder: Path):
    """Build a recipe Bundle for a bare run folder (no DatasetStore needed)."""
    from quam_state_manager.core.dataset import DatasetStore
    store = DatasetStore(folder.parent.parent)   # <root>/<date>/#run
    for rid in sorted(store.runs):
        if str(store.runs[rid].folder_path) == str(folder):
            return store.runs[rid]
    return None


# ──────────────────────────────────────────────────────────────────────────
# ndview candidate gate (always runs)
# ──────────────────────────────────────────────────────────────────────────

class TestRelativeAxisGate:
    def test_detuning_axis_offers_no_absolute_freq_target(self):
        from quam_state_manager.core.click_targets import candidates_for
        assert candidates_for("03_resonator_spectroscopy", "detuning", None,
                              "qubit") == []
        assert candidates_for("unknown", "detuning", None, "qubit") == []

    def test_absolute_axis_offers_targets(self):
        from quam_state_manager.core.click_targets import candidates_for
        paths = [c["path"] for c in
                 candidates_for("03_resonator_spectroscopy", "full_freq",
                                None, "qubit")]
        assert "qubits.{q}.resonator.f_01" in paths

    def test_prefactor_axis_gated(self):
        from quam_state_manager.core.click_targets import candidates_for
        assert candidates_for("11_power_rabi", "amp_prefactor", None,
                              "qubit") == []


# ──────────────────────────────────────────────────────────────────────────
# Round-trip goldens (real archive)
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not (_LabA.is_dir() or _LabC_TOP.is_dir()),
                    reason="real data archive not present")
class TestRoundTripGoldens:

    def test_05b_freq_increment_reproduces_node_update(self):
        """05b: fit says frequency_shift; the node does f_01 += shift.
        Clicking the fitted absolute frequency (RF_run + shift) through the
        baked affine must land exactly on f_01_frozen + shift."""
        folder = _find_run([
            (_LabC_ARCHIVE, "#*resonator_spectroscopy_vs_power_iq*"),
            (_LabC_TOP, "#*resonator_spectroscopy_vs_power_iq*"),
        ])
        if folder is None:
            pytest.skip("no 05b run in the archive")
        run = _bundle_for(folder)
        if run is None:
            pytest.skip("run not indexed")
        from quam_state_manager.core.interactive_plots import contracts
        b = _make_bundle(run)
        qname = (b.raw or {}).get("coords", {}).get("qubit", ["q"])[0]

        click = contracts.freq_increment_targets(b, qname, axis="x",
                                                 axis_scale=1e9, resonator=True)
        if click is None:
            pytest.skip("RF_at_run not recoverable in this run")
        # the node's own numbers
        fr = (run.fit_results or {}).get(qname) or {}
        shift = fr.get("frequency_shift")
        expected_abs = fr.get("resonator_frequency")   # RF_run + shift
        if shift is None or expected_abs is None:
            pytest.skip("fit_results lack shift fields (older analysis gen)")
        rf_run = contracts.rf_at_run(b, qname, resonator=True)
        assert rf_run == pytest.approx(expected_abs - shift, rel=1e-9), \
            "RF_at_run recovered from the dataset must equal fit's anchor"
        # simulate clicking the fitted dip (absolute GHz axis)
        clicked_ghz = expected_abs / 1e9
        f01_frozen = contracts.frozen_value(b, f"qubits.{qname}.resonator.f_01")
        tgt = next(t for t in click["targets"]
                   if t["path"].endswith("resonator.f_01"))
        staged = tgt["scale"] * clicked_ghz + tgt["offset"]
        assert staged == pytest.approx(f01_frozen + shift, rel=1e-9), \
            "baked affine must reproduce the node's += semantics"

    def test_ramsey_vs_flux_delta_reproduces_node_update(self):
        """23: fit says flux_offset (a delta); the node does offset += delta.
        Clicking the fitted vertex through the baked affine must land on
        offset_pre + delta — with offset_pre patches-aware."""
        folder = _find_run([
            (_LabC_ARCHIVE, "#*ramsey_vs_flux_calibration*"),
            (_LabC_TOP, "#*ramsey_vs_flux_calibration*"),
            (_LabA, "#*ramsey_vs_flux_calibration*"),
        ])
        if folder is None:
            pytest.skip("no ramsey_vs_flux run")
        run = _bundle_for(folder)
        if run is None:
            pytest.skip("run not indexed")
        from quam_state_manager.core.interactive_plots import contracts
        b = _make_bundle(run)
        qname = (b.raw or {}).get("coords", {}).get("qubit", ["q"])[0]
        click = contracts.flux_delta_targets(b, qname, axis="y")
        if click is None:
            pytest.skip("pre-update offset not recoverable")
        fr = (run.fit_results or {}).get(qname) or {}
        delta = fr.get("flux_offset")
        if delta is None:
            pytest.skip("fit_results lack flux_offset")
        tgt = click["targets"][0]
        staged = tgt["scale"] * delta + tgt["offset"]
        # Cross-check against node.json patches when the update was applied.
        import json as _json
        node = _json.loads((folder / "node.json").read_text())
        patches = node.get("patches") or []
        applied = [p for p in patches
                   if str(p.get("path", "")).endswith(("_offset",))]
        if applied:
            assert staged == pytest.approx(float(applied[0]["value"]), rel=1e-6), \
                "click at the fitted vertex must reproduce the node's write"
        else:
            # No patch → snapshot IS pre-update; staged = snapshot + delta.
            snap = contracts.frozen_value(b, tgt["path"])
            assert staged == pytest.approx(snap + delta, rel=1e-9)

    def test_power_rabi_mv_axis_is_absolute(self):
        """11 err-amp: the mV axis (ds_raw.full_amp) already carries the
        run-time amplitude — opt_amp = full_amp at the fitted prefactor."""
        folder = _find_run([
            (_LabA, "#*power_rabi*"),
            (_LabC_TOP, "#*power_rabi*"),
        ])
        if folder is None:
            pytest.skip("no power_rabi run")
        import h5py
        fr_p = folder / "ds_fit.h5"
        raw_p = folder / "ds_raw.h5"
        if not (fr_p.exists() and raw_p.exists()):
            pytest.skip("missing h5")
        with h5py.File(fr_p, "r") as f:
            if "opt_amp" not in f or "opt_amp_prefactor" not in f:
                pytest.skip("older power_rabi fit layout")
            opt_amp = float(np.asarray(f["opt_amp"]).ravel()[0])
            opt_pref = float(np.asarray(f["opt_amp_prefactor"]).ravel()[0])
        with h5py.File(raw_p, "r") as f:
            if "full_amp" not in f or "amp_prefactor" not in f:
                pytest.skip("no full_amp axis")
            full_amp = np.asarray(f["full_amp"]).reshape(-1, np.asarray(f["full_amp"]).shape[-1])[0]
            prefs = np.asarray(f["amp_prefactor"])
        if not np.isfinite(opt_amp):
            pytest.skip("failed fit")
        idx = int(np.argmin(np.abs(prefs - opt_pref)))
        # clicking the optimum column on the ABSOLUTE axis reproduces opt_amp
        assert full_amp[idx] == pytest.approx(opt_amp, rel=1e-6), \
            "full_amp axis is baked with the run-time amplitude (A_pre)"


# ──────────────────────────────────────────────────────────────────────────
# Extended-node goldens (2nd batch: 02w/06/09/13/15a/15b) — clickable payloads
# built by the REAL recipes, clicked at the fit optimum, compared against the
# node's own node.json patches. Runs pinned from the anatomy extraction.
# ──────────────────────────────────────────────────────────────────────────

def _clickable_for(folder: Path, key_prefix: str):
    run = _bundle_for(folder)
    if run is None:
        return None, None
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    menu = list_interactive_figures(run)
    key = next((m["key"] for m in menu
                if m["key"].startswith(key_prefix) and m["available"]
                and not m.get("static")), None)
    if key is None:
        return run, None
    fig = build_interactive_figure(run, key)
    return run, (fig or {}).get("clickable")


def _patches(folder: Path) -> list:
    import json as _json
    try:
        return _json.loads((folder / "node.json").read_text()).get("patches") or []
    except Exception:
        return []


def _eval_target(t, clicked):
    return t.get("scale", 1) * clicked + t.get("offset", 0)


@pytest.mark.skipif(not _LabC_ARCHIVE.is_dir(), reason="LabC archive not present")
class TestExtendedGoldens:

    def test_09_flux_delta_click_reproduces_patch(self):
        """LabC #220: joint 0.07203367 → 0.06961581 with idle_offset −0.00241786.
        Clicking the fitted vertex (a DELTA) through the recipe's baked target
        must reproduce the node's write — the old recipe staged −0.0024 ABSOLUTE
        (the P0 bug this pins)."""
        folder = _LabC_ARCHIVE / "2026-06-03" / "#220_1Q_09_qubit_spectroscopy_vs_flux_204227"
        if not folder.is_dir():
            pytest.skip("golden run missing")
        run, clk = _clickable_for(folder, "amplitude")
        assert clk is not None, "pinned golden must produce a clickable"
        flux_ts = [t for t in clk["targets"] if "_offset" in t["path"]]
        assert flux_ts, "flux target missing"
        t = flux_ts[0]
        applied = [p for p in _patches(folder)
                   if str(p.get("path", "")).endswith("_offset")]
        assert applied, "golden run lost its patch?"
        old, new = float(applied[0]["old"]), float(applied[0]["value"])
        delta = new - old                       # == ds_fit idle_offset
        staged = _eval_target(t, delta)
        assert staged == pytest.approx(new, abs=1e-9), \
            f"flux click must be old+Δ ({old}+{delta}), got {staged}"
        assert t.get("offset") == pytest.approx(old, abs=1e-9), \
            "baked offset must be the PRE-update (patches.old) value"

    def test_06_f01_increment_and_flux_absolute(self):
        """LabC #212: RF += freq_shift (increment) while flux sweet spot is an
        ABSOLUTE assign — the two semantics in ONE figure."""
        folder = _LabC_ARCHIVE / "2026-06-03" / "#212_1Q_06_resonator_spectroscopy_vs_flux_203012"
        if not folder.is_dir():
            pytest.skip("golden run missing")
        run, clk = _clickable_for(folder, "amplitude")
        assert clk is not None, "pinned golden must produce a clickable"
        patches = _patches(folder)
        # flux: absolute — staged(clicked=new) == new, offset == 0
        flux_t = next((t for t in clk["targets"] if "_offset" in t["path"]), None)
        assert flux_t is not None and flux_t.get("offset", 0) == 0.0
        # f_01: increment — offset == f01_pre − RF_at_run
        f01_t = next((t for t in clk["targets"] if t["path"].endswith(".f_01")), None)
        if f01_t is None:
            pytest.skip("f_01 target unavailable (no RF anchor)")
        f01_p = [p for p in patches if str(p.get("path", "")).endswith("/resonator/f_01")]
        rf_p = [p for p in patches if str(p.get("path", "")).endswith("/resonator/RF_frequency")]
        if not (f01_p and rf_p):
            pytest.skip("golden lacks freq patches")
        # find the patched qubit matching the built figure's qubit
        qname = clk.get("qubit") or ""
        f01_p = [p for p in f01_p if f"/{qname}/" in p["path"]] or f01_p
        rf_p = [p for p in rf_p if f"/{qname}/" in p["path"]] or rf_p
        f01_old, f01_new = float(f01_p[0]["old"]), float(f01_p[0]["value"])
        rf_old, rf_new = float(rf_p[0]["old"]), float(rf_p[0]["value"])
        shift = rf_new - rf_old
        clicked_ghz = (rf_old + shift) / 1e9    # click the fitted sweet-spot freq
        staged = _eval_target(f01_t, clicked_ghz)
        assert staged == pytest.approx(f01_old + shift, rel=1e-12), \
            "f_01 must move by the SHIFT, not be overwritten with the click"

    def test_13_drag_absolute_alpha_axis(self):
        """LabC #101: patch old 0.0 → −1.08 with alpha_setpoint=1.0 — the
        prefactor≠absolute trap. The recipe clicks the PERSISTED absolute alpha
        axis (scale 1) so the staged value == the node's write directly; the
        prefactor-only fallback must be VIEW-ONLY."""
        folder = _LabC_ARCHIVE / "2026-06-03" / "#101_1Q_13_drag_calibration_180_minus_180_142608"
        if not folder.is_dir():
            pytest.skip("golden run missing")
        run, clk = _clickable_for(folder, "amplitude")
        if clk is None:
            run, clk = _clickable_for(folder, "averaged")
        assert clk is not None, "pinned golden must produce a clickable"
        t = clk["targets"][0]
        assert t["path"].endswith(".alpha") and t.get("scale") == 1
        applied = [p for p in _patches(folder) if p["path"].endswith("/alpha")]
        assert applied, "golden lost its alpha patch?"
        new = float(applied[0]["value"])
        assert _eval_target(t, new) == pytest.approx(new, rel=1e-12)

    def test_15a_absolute_freq_assign(self):
        """LabC #111: RF/f_01 assigned the absolute optimum; the recipe's ×1e9
        contract on the persisted full_freq axis is faithful."""
        folder = _LabC_ARCHIVE / "2026-06-03" / "#111_1Q_15a_readout_frequency_optimization_144345"
        if not folder.is_dir():
            hits = sorted(_LabC_ARCHIVE.rglob("#111_*readout_frequency*"))
            folder = hits[0] if hits else folder
        if not folder.is_dir():
            pytest.skip("golden run missing")
        run, clk = _clickable_for(folder, "distances")
        if clk is None:
            run, clk = _clickable_for(folder, "iq_abs")
        assert clk is not None, "pinned golden must produce a clickable"
        applied = [p for p in _patches(folder)
                   if p["path"].endswith("/RF_frequency")]
        if not applied:
            pytest.skip("golden lacks freq patch")
        new = float(applied[0]["value"])
        t = next(x for x in clk["targets"] if x["path"].endswith("RF_frequency"))
        assert _eval_target(t, new / 1e9) == pytest.approx(new, rel=1e-12)

    def test_15b_absolute_amp_assign(self):
        """LabC #110: readout amplitude assigned the absolute optimum (V axis
        persisted) — scale-1 contract faithful."""
        folder = _LabC_ARCHIVE / "2026-06-03" / "#110_1Q_15b_readout_power_optimization_144257"
        if not folder.is_dir():
            hits = sorted(_LabC_ARCHIVE.rglob("#110_*readout_power*"))
            folder = hits[0] if hits else folder
        if not folder.is_dir():
            pytest.skip("golden run missing")
        run, clk = _clickable_for(folder, "power_amplitude")
        if clk is None:
            run, clk = _clickable_for(folder, "amplitude")
        assert clk is not None, "pinned golden must produce a clickable"
        applied = [p for p in _patches(folder)
                   if p["path"].endswith("readout/amplitude")]
        if not applied:
            pytest.skip("golden lacks amp patch")
        new = float(applied[0]["value"])
        t = next(x for x in clk["targets"] if x["path"].endswith(".amplitude"))
        assert _eval_target(t, new) == pytest.approx(new, rel=1e-12)


@pytest.mark.skipif(not _LabA.is_dir(), reason="LabA archive not present")
def test_wide_pyloop_absolute_assign():
    """LabA #354 (wide python-loop): new == ds_fit f0 exactly; the recipe's
    ×1e9 absolute contract is faithful and the registry now routes it.
    (LabA-gated: this golden lives in the LabA archive, not LabC.)"""
    folder = _LabA / "2026-05-30" / "#354_1Q_03_resonator_spectroscopy_wide_python_loop_200526"
    if not folder.is_dir():
        pytest.skip("golden run missing")
    run, clk = _clickable_for(folder, "amplitude")
    assert clk is not None, "pinned golden must produce a clickable"
    t = next((x for x in clk["targets"] if x["path"].endswith(".f_01")), None)
    assert t is not None and t.get("scale") == 1e9 and not t.get("offset")
    applied = [p for p in _patches(folder) if p["path"].endswith("/f_01")]
    if applied:
        new = float(applied[0]["value"])
        assert _eval_target(t, new / 1e9) == pytest.approx(new, rel=1e-12)


class TestAuditRegressions:
    """Final-audit findings pinned as tests."""

    def test_standalone_ef_run_is_view_only(self):
        """P0: standalone '28_qubit_spectroscopy_e_to_f' routes to the qubit-spec
        recipe via the tier-2 normalizer — the EF gate must catch BOTH spellings
        (clicking the E→F peak into f_01 is wrong by the anharmonicity)."""
        folder = Path("<dataset-root>/example_lab_cr")
        if not folder.is_dir():
            pytest.skip("LabA_CR archive absent")
        from quam_state_manager.core.dataset import DatasetStore
        from quam_state_manager.core.interactive_plots import (
            build_interactive_figure, list_interactive_figures)
        st = DatasetStore(folder)
        run = next((r for r in st.runs.values()
                    if "e_to_f" in (r.experiment_name or "")), None)
        if run is None:
            pytest.skip("no EF run")
        menu = list_interactive_figures(run)
        for m in menu:
            if not m["available"] or m.get("static"):
                continue
            fig = build_interactive_figure(run, m["key"])
            assert (fig or {}).get("clickable") is None, \
                f"EF figure {m['key']} must be view-only"

    def test_bar_view_offers_no_candidates(self):
        """P1: a per-entity bar view (no sweep axis, x_dim=None) must offer NO
        staging candidates — the chip would stage the MEASURED value."""
        from quam_state_manager.core.click_targets import candidates_for
        assert candidates_for("12_ramsey", None, None, "qubit") == []
        assert candidates_for("03_resonator_spectroscopy", None, None, "qubit") == []

    def test_no_t1_t2_node_targets(self):
        """P1: T2*/T1 are fit-derived and the time axis is ns-vs-seconds — the
        node tier must not offer them."""
        from quam_state_manager.core.click_targets import candidates_for
        assert candidates_for("12_ramsey", "idle_time", None, "qubit") == []
        assert candidates_for("25_T1", "idle_time", None, "qubit") == []

    def test_flux_targets_refuse_unknown_flux_point(self, tmp_path):
        """P3: no snapshot / unknown flux_point → None (never guess joint)."""
        from quam_state_manager.core.interactive_plots import contracts
        class _B:  # bundle stub with no quam_state
            quam_state = {}
            raw = None
            run = None
        assert contracts.flux_delta_targets(_B(), "q0") is None
        assert contracts.flux_absolute_targets(_B(), "q0") is None
