"""Four defects the constant audit found and independently reproduced (docs/78 §22).

Each was surfaced by measuring a shipped constant against the archives and then
adversarially re-checked. These four were confirmed by the verifier AND are
small enough to fix without re-deriving anything — the band re-calibrations the
same audit proposes are NOT here, because the verifier's own condition for
touching them (splitting the error-amplification and e->f populations out of
their host families) has not been met.
"""
from __future__ import annotations

import numpy as np
import pytest

from quam_state_manager.core.autofit import action_space, gates, stoploss
from quam_state_manager.core.autofit.families import FeatureCheck


class TestSanitizeNoLongerContradictsItself:
    """`reduced_schema` refuses to expose an unclassified key and
    `validate_proposal` rejects one — but `sanitize` let it through, and
    `sanitize` is the function on the real backend path. Two halves of one
    policy disagreeing meant the stricter half was decorative."""

    def test_an_unclassified_key_is_dropped(self):
        clean, dropped = action_space.sanitize({"some_new_knob": 3})
        assert clean == {}
        assert dropped and dropped[0]["class"] == "unknown"

    def test_the_drop_carries_its_reason(self):
        _, dropped = action_space.sanitize({"mystery": 1})
        assert "unclassified" in dropped[0]["reason"]

    def test_class_a_and_b_still_pass(self):
        clean, dropped = action_space.sanitize(
            {"num_shots": 400, "use_state_discrimination": True})
        assert clean == {"num_shots": 400, "use_state_discrimination": True}
        assert dropped == []

    def test_reserved_and_frozen_are_still_dropped_for_their_own_reasons(self):
        _, dropped = action_space.sanitize(
            {"load_data_id": 7, "line_attenuation_in_db": 3})
        kinds = {d["key"]: d["class"] for d in dropped}
        assert kinds == {"load_data_id": "reserved",
                         "line_attenuation_in_db": "frozen"}

    def test_sanitize_and_the_schema_now_agree_on_every_class(self):
        """The property that was violated: what sanitize keeps is exactly what
        a schema built from the same keys would accept."""
        keys = ["num_shots", "load_data_id", "line_attenuation_in_db",
                "reset_type", "an_unheard_of_knob"]
        clean, _ = action_space.sanitize({k: 1 for k in keys})
        schema = action_space.reduced_schema(
            {"properties": {k: {"type": "number"} for k in keys}},
            "qubit_spectroscopy")
        exposed = set(schema["properties"])
        # class B is proposed separately, so it is the one legitimate
        # difference; nothing UNKNOWN may sit on either side
        assert "an_unheard_of_knob" not in clean
        assert "an_unheard_of_knob" not in exposed


class TestTheRawDataGateReadsBothFormats:
    """G3 is the check that can tell a fit which missed the feature from one
    which found it. It opened `ds_raw.h5` with raw h5py, so every run from an
    env that writes NetCDF-classic under that name answered "unreadable" —
    not a degraded check, no check, silently (docs/67)."""

    @staticmethod
    def _write_netcdf(path, names, y, axis):
        nc = pytest.importorskip("scipy.io")
        f = nc.netcdf_file(str(path), "w")
        f.createDimension("qubit", len(names))
        f.createDimension("nchar", max(len(n) for n in names))
        f.createDimension("sweep", y.shape[1])
        v = f.createVariable("qubit", "c", ("qubit", "nchar"))
        for i, n in enumerate(names):
            v[i, :len(n)] = list(n.ljust(v.shape[1]))[:v.shape[1]]
        d = f.createVariable("I", "d", ("qubit", "sweep"))
        d[:] = y
        a = f.createVariable("detuning", "d", ("sweep",))
        a[:] = axis
        f.close()

    def test_a_netcdf_classic_ds_raw_is_read_not_refused(self, tmp_path):
        y = np.vstack([np.linspace(0, 1, 16), np.linspace(1, 0, 16)])
        axis = np.linspace(-5, 5, 16)
        p = tmp_path / "ds_raw.h5"
        self._write_netcdf(p, ["qA1", "qA2"], y, axis)
        fc = FeatureCheck(var="I", axis_var="detuning", mode="peak")
        got = gates._read_target_trace(p, fc, "qA2", "qubits")
        assert not isinstance(got, str), got
        ax, row = got
        assert row.shape == (16,) and ax.shape == (16,)
        assert row[0] == pytest.approx(1.0)      # the SECOND row, not the first

    def test_an_hdf5_ds_raw_still_reads_identically(self, tmp_path):
        h5py = pytest.importorskip("h5py")
        y = np.vstack([np.linspace(0, 1, 16), np.linspace(1, 0, 16)])
        axis = np.linspace(-5, 5, 16)
        p = tmp_path / "ds_raw.h5"
        with h5py.File(p, "w") as f:
            f["qubit"] = np.array([b"qA1", b"qA2"])
            f["I"] = y
            f["detuning"] = axis
        fc = FeatureCheck(var="I", axis_var="detuning", mode="peak")
        ax, row = gates._read_target_trace(p, fc, "qA2", "qubits")
        assert row[0] == pytest.approx(1.0) and ax.shape == (16,)

    def test_garbage_still_answers_with_a_string_not_an_exception(self, tmp_path):
        p = tmp_path / "ds_raw.h5"
        p.write_bytes(b"not a data file at all")
        fc = FeatureCheck(var="I", axis_var="detuning", mode="peak")
        got = gates._read_target_trace(p, fc, "qA1", "qubits")
        assert isinstance(got, str) and "unreadable" in got

    def test_a_missing_target_is_reported_by_name(self, tmp_path):
        y = np.zeros((2, 8))
        p = tmp_path / "ds_raw.h5"
        self._write_netcdf(p, ["qA1", "qA2"], y, np.arange(8.0))
        fc = FeatureCheck(var="I", axis_var="detuning", mode="peak")
        got = gates._read_target_trace(p, fc, "qZ9", "qubits")
        assert isinstance(got, str) and "qZ9" in got


class TestTraceVarAndPairCoordEquivalents:
    """The CQT corpus exposed three recording-convention renames that left
    whole families' G3 stuck at unverifiable (docs/127): state-discriminated
    runs save the fitted trace as 'state' with no I/Q at all (423 targets),
    the current coupler-node generation names the PAIR dim plain 'qubit'
    (85 targets), and this generation's readout-freq-opt saves the |g>-|e>
    distance 'D' instead of 'snr' (44 targets). Each fallback is a verified
    rename of the SAME physical trace — never a different quantity."""

    @staticmethod
    def _h5(path, **arrays):
        h5py = pytest.importorskip("h5py")
        with h5py.File(path, "w") as f:
            for k, v in arrays.items():
                f[k] = v

    def test_state_var_serves_an_I_family(self, tmp_path):
        p = tmp_path / "ds_raw.h5"
        self._h5(p, qubit=np.array([b"q18"]),
                 state=np.sin(np.linspace(0, 6.28, 32))[None, :],
                 amp_prefactor=np.linspace(0.9, 1.1, 32))
        fc = FeatureCheck(var="I", axis_var="amp_prefactor", mode="span")
        got = gates._read_target_trace(p, fc, "q18", "qubits")
        assert not isinstance(got, str), got
        _, row = got
        assert row.shape == (32,)

    def test_D_serves_the_snr_peak_check(self, tmp_path):
        p = tmp_path / "ds_raw.h5"
        y = np.zeros((1, 16)); y[0, 5] = 1.0
        self._h5(p, qubit=np.array([b"q1"]), D=y,
                 detuning=np.linspace(-8e6, 8e6, 16))
        fc = FeatureCheck(var="snr", axis_var="detuning", mode="peak")
        ax, row = gates._read_target_trace(p, fc, "q1", "qubits")
        assert row[5] == pytest.approx(1.0) and ax.shape == (16,)

    def test_pair_family_reads_the_renamed_qubit_coord(self, tmp_path):
        # coupler cubes: dim named 'qubit', VALUES are pair names
        p = tmp_path / "ds_raw.h5"
        self._h5(p, qubit=np.array([b"q2-5"]),
                 IQ_abs=np.random.default_rng(0).normal(size=(1, 8, 8)),
                 flux_bias=np.linspace(-0.1, 0.1, 8))
        fc = FeatureCheck(var="IQ_abs", axis_var="flux_bias", mode="span")
        got = gates._read_target_trace(p, fc, "q2-5", "qubit_pairs")
        assert not isinstance(got, str), got

    def test_a_true_qubit_pair_coord_still_wins(self, tmp_path):
        # both dims present -> qubit_pair preferred; the 'qubit' coord here
        # carries MEMBER names, so reading it would mis-index the pair row
        p = tmp_path / "ds_raw.h5"
        self._h5(p, qubit_pair=np.array([b"q2-5"]),
                 qubit=np.array([b"q2", b"q5"]),
                 state_moving=np.zeros((1, 8)),
                 time=np.arange(8.0))
        fc = FeatureCheck(var="state_target", axis_var="time", mode="span")
        got = gates._read_target_trace(p, fc, "q2-5", "qubit_pairs")
        assert not isinstance(got, str), got

    def test_no_equivalent_still_answers_the_honest_error(self, tmp_path):
        p = tmp_path / "ds_raw.h5"
        self._h5(p, qubit=np.array([b"q1"]),
                 something_else=np.zeros((1, 8)), detuning=np.arange(8.0))
        fc = FeatureCheck(var="IQ_abs", axis_var="detuning", mode="peak")
        got = gates._read_target_trace(p, fc, "q1", "qubits")
        assert isinstance(got, str) and "IQ_abs" in got

    def test_identity_check_survives_the_coord_fallback(self, tmp_path):
        # pair family, renamed coord, but the coord does NOT carry this pair
        p = tmp_path / "ds_raw.h5"
        self._h5(p, qubit=np.array([b"q1-4"]),
                 IQ_abs=np.zeros((1, 8, 8)), flux_bias=np.arange(8.0))
        fc = FeatureCheck(var="IQ_abs", axis_var="flux_bias", mode="span")
        got = gates._read_target_trace(p, fc, "q2-5", "qubit_pairs")
        assert isinstance(got, str) and "q2-5" in got


class TestThreeZoneFeatureCheck:
    """A family that declares a lower ``z_min`` buys a MIDDLE zone, not a
    lower localization bar (docs/127). On the CQT chip corroborated qubit-spec
    claims carry prominence z down to 2.35 — below the module floor of 5 —
    but between the floors a global SEARCH is unreliable both ways (max-of-N
    on a flat window already reads z≈3.3, and claim-vs-argmax turned 91
    corroborated-good claims into wrong_peak). In the middle zone the check
    TESTS the claim's own region instead: averaging over ±tol shrinks point
    noise by √n, so a real feature at the claim survives and a noise window
    provably fails — the no-signal corruption stays a hard fail. A family
    declaring nothing keeps the one-floor behavior byte-identically."""

    @staticmethod
    def _trace(tmp_path, peak_height, wide=False, n=64):
        h5py = pytest.importorskip("h5py")
        # alternating ±1 noise → point-noise ≈ 2·1.4826/√2 ≈ 2.1, so
        # z ≈ peak_height/2.1 — deterministic by construction
        y = np.array([1.0 if i % 2 else -1.0 for i in range(n)])
        if wide:
            y[n // 2 - 3: n // 2 + 4] = peak_height   # spans the ±tol window
        else:
            y[n // 2] = peak_height
        axis = np.linspace(4.0e9, 5.0e9, n)
        p = tmp_path / "ds_raw.h5"
        with h5py.File(p, "w") as f:
            f["qubit"] = np.array([b"q1"])
            f["IQ_abs"] = y[None, :]
            f["full_freq"] = axis[None, :]
        return p, float(axis[n // 2])

    def _fc(self, z_min=None):
        return FeatureCheck(var="IQ_abs", axis_var="full_freq", mode="peak",
                            claim_key="frequency", tol_fwhm=0.0,
                            fallback_tol=5e7, z_min=z_min)

    def test_provably_empty_is_still_no_signal(self, tmp_path):
        p, at = self._trace(tmp_path, peak_height=1.0)     # z ≈ 0.5
        status, detail = gates._feature_check(
            p, self._fc(z_min=2.0), "q1", "qubits", {"frequency": at}, None)
        assert status == "no_signal", detail

    def test_weak_real_feature_at_the_claim_passes(self, tmp_path):
        p, at = self._trace(tmp_path, peak_height=9.0, wide=True)  # z mid-zone
        status, detail = gates._feature_check(
            p, self._fc(z_min=2.0), "q1", "qubits", {"frequency": at}, None)
        assert status == "ok" and "claim region carries" in detail, detail

    def test_weak_window_with_claim_on_flat_ground_fails(self, tmp_path):
        p, at = self._trace(tmp_path, peak_height=9.0, wide=True)
        status, detail = gates._feature_check(
            p, self._fc(z_min=2.0), "q1", "qubits", {"frequency": 4.2e9}, None)
        assert status == "no_signal" and "claim region" in detail, detail

    def test_strong_feature_still_localizes_both_ways(self, tmp_path):
        p, at = self._trace(tmp_path, peak_height=30.0)    # z ≈ 14
        fc = self._fc(z_min=2.0)
        status, _ = gates._feature_check(
            p, fc, "q1", "qubits", {"frequency": at}, None)
        assert status == "ok"
        status, _ = gates._feature_check(
            p, fc, "q1", "qubits", {"frequency": at + 3e8}, None)
        assert status == "wrong_peak"

    def test_undeclared_family_keeps_the_one_floor(self, tmp_path):
        p, at = self._trace(tmp_path, peak_height=7.0)     # z ≈ 3.3 < 5
        status, detail = gates._feature_check(
            p, self._fc(z_min=None), "q1", "qubits", {"frequency": at}, None)
        assert status == "no_signal", "no z_min ⇒ byte-identical legacy"

    def test_presence_probe_keeps_the_module_floor(self, tmp_path):
        # a probe with no claim to test must never call a noise maximum
        # "feature present" just because the family lowered its empty-floor
        p, _at = self._trace(tmp_path, peak_height=7.0)    # z ≈ 3.3
        present, detail, _hint = gates._presence_probe(
            p, self._fc(z_min=2.0), "q1", "qubits")
        assert present is False, detail

    def test_qubit_spectroscopy_declares_the_corpus_floor(self):
        from quam_state_manager.core.autofit import families as fam_mod
        fam = fam_mod.family_for("08_qubit_spectroscopy")
        assert fam.feature_check.z_min == 2.0


class TestAdjudication40Bands:
    """The 40-target adjudication's band decisions (docs/134 §2): every
    node-successful gate-fail of four families was adjudicated by
    corroboration + figure, and the bands re-derived from the labels.
    Config pins first; then the edge-pinned-CLAIM rule, which is what makes
    the rfo middle zone safe — a monotone ramp's edge region always carries a
    large smoothed deviation, so without it the claim-region test silently
    accepts exactly the truncated-window runs the zone exists to catch."""

    def test_res_spec_coarse_tolerance_is_13_fwhm(self):
        from quam_state_manager.core.autofit import families as fam_mod
        fam = fam_mod.family_for("03_resonator_spectroscopy")
        assert fam.feature_check.tol_fwhm == 13.0

    def test_rfo_declares_the_corpus_floor(self):
        from quam_state_manager.core.autofit import families as fam_mod
        fam = fam_mod.family_for("14_readout_frequency_optimization")
        assert fam.feature_check.z_min == 2.0

    def test_qs_vs_coupler_uses_the_2d_spectral_floor(self):
        from quam_state_manager.core.autofit import families as fam_mod
        fam = fam_mod.family_for("10_qubit_spectroscopy_vs_coupler_flux")
        assert fam.feature_check.spectral_min == 4.5

    def test_res_vs_coupler_keeps_the_module_floor(self):
        # deliberate NO-change: the real corpus shows a 36→79 gap above the
        # floor of 50, and any floor re-admitting the false alarm at 16
        # re-admits the true catch at 36 (docs/134 §2)
        from quam_state_manager.core.autofit import families as fam_mod
        fam = fam_mod.family_for("07_resonator_spectroscopy_vs_coupler_flux")
        assert fam.feature_check.spectral_min is None

    # ---- the edge-pinned-claim rule ------------------------------------
    @staticmethod
    def _h5(tmp_path, y):
        h5py = pytest.importorskip("h5py")
        n = y.size
        axis = np.linspace(4.0e9, 5.0e9, n)
        p = tmp_path / "ds_raw.h5"
        with h5py.File(p, "w") as f:
            f["qubit"] = np.array([b"q1"])
            f["IQ_abs"] = y[None, :]
            f["full_freq"] = axis[None, :]
        return p, axis

    def _fc(self):
        return FeatureCheck(var="IQ_abs", axis_var="full_freq", mode="peak",
                            claim_key="frequency", tol_fwhm=0.0,
                            fallback_tol=5e7, z_min=2.0)

    @staticmethod
    def _ramp(n=64, height=10.0):
        # alternating ±1 noise + monotone rise: point-prominence z lands in
        # the middle zone while the smoothed curve rises into the right edge
        y = np.array([1.0 if i % 2 else -1.0 for i in range(n)])
        return y + np.linspace(0.0, height, n)

    def test_edge_pinned_claim_on_a_ramp_is_not_bracketed(self, tmp_path):
        # the #62/#623 shape: no extremum inside the window, claim at the
        # boundary maximum — refuse with out_of_band (rides the widen ladder)
        p, axis = self._h5(tmp_path, self._ramp())
        status, detail = gates._feature_check(
            p, self._fc(), "q1", "qubits", {"frequency": float(axis[-2])}, None)
        assert status == "out_of_band" and "not bracketed" in detail, detail

    def test_interior_claim_never_triggers_the_edge_rule(self, tmp_path):
        # same ramp, interior claim: the edge rule must stay out of it — the
        # verdict is whatever the claim-region test says (here: no feature)
        p, axis = self._h5(tmp_path, self._ramp())
        status, detail = gates._feature_check(
            p, self._fc(), "q1", "qubits",
            {"frequency": float(axis[len(axis) // 2])}, None)
        assert status != "out_of_band", detail

    def test_bracketed_apex_near_the_edge_still_passes(self, tmp_path):
        # the counter-shape that killed rule v1: a real feature whose apex
        # sits INSIDE the window near an edge, with a visible turnover —
        # the boundary sample is well below the apex, so the ramp test
        # cannot fire and the claim region carries the resolved feature
        n = 64
        y = np.array([1.0 if i % 2 else -1.0 for i in range(n)], dtype=float)
        idx = np.arange(n)
        y = y + 12.0 * np.exp(-((idx - 6) / 2.5) ** 2)
        p, axis = self._h5(tmp_path, y)
        status, detail = gates._feature_check(
            p, self._fc(), "q1", "qubits", {"frequency": float(axis[6])}, None)
        assert status == "ok", detail


class TestSandboxRevertWalksLists:
    """295 of 1,755 real CQT revert targets died as dict lookups: iq_blobs and
    readout-power patches touch LIST elements (confusion_matrix/0/0), and the
    sandbox fix walked every dotted segment as a dict key. The walk is now
    structural — a digit segment indexes a list PARENT and stays a dict key
    for number-keyed dicts — the modifier's own rule. After the fix the
    full-archive harness verifies 1,755/1,755 targets byte-exact."""

    def test_confusion_matrix_patch_reverts(self, tmp_path):
        import json as _json
        from quam_state_manager.core.autofit.replay import _sandbox_fix
        state = {"qubits": {"q2": {"resonator": {
            "confusion_matrix": [[0.9, 0.1], [0.2, 0.8]],
            "operations": {"readout": {"threshold": 1.5e-5}}}}}}
        (tmp_path / "state.json").write_text(_json.dumps(state),
                                             encoding="utf-8")
        patches = [
            {"op": "replace",
             "path": "/quam/qubits/q2/resonator/confusion_matrix/0/1",
             "old": 0.4845, "value": 0.1},
            {"op": "replace",
             "path": "/quam/qubits/q2/resonator/operations/readout/threshold",
             "old": 2.0e-5, "value": 1.5e-5},
        ]
        res = _sandbox_fix(tmp_path, None, "q2", patches, {}, "revert")
        assert res["ok"], res
        after = _json.loads((tmp_path / "state.json").read_text("utf-8"))
        assert after["qubits"]["q2"]["resonator"]["confusion_matrix"][0][1] \
            == 0.4845
        assert after["qubits"]["q2"]["resonator"]["confusion_matrix"][0][0] \
            == 0.9, "untouched cells must stay untouched"
        assert after["qubits"]["q2"]["resonator"]["operations"]["readout"][
            "threshold"] == 2.0e-5

    def test_an_unwalkable_path_answers_ok_false_not_a_raise(self, tmp_path):
        import json as _json
        from quam_state_manager.core.autofit.replay import _sandbox_fix
        (tmp_path / "state.json").write_text(
            _json.dumps({"a": [1, 2]}), encoding="utf-8")
        patches = [{"op": "replace", "path": "/quam/a/notanindex",
                    "old": 5, "value": 6}]
        res = _sandbox_fix(tmp_path, None, "q1", patches, {}, "revert")
        assert res.get("ok") is False


class TestMetricTrendBestIsTheRunningMaximum:
    """`best` advanced only on a value that CLEARED the noise floor, so it
    reported the last value that happened to jump rather than the best seen —
    a number that is neither, under a name that promises one."""

    def test_best_tracks_the_maximum_even_when_gains_are_small(self):
        hist = [{"r2": 0.90}, {"r2": 0.95}, {"r2": 0.91}]
        out = stoploss.metric_trend(hist)
        assert out["best"]["r2"] == pytest.approx(0.95)

    def test_a_sub_threshold_gain_is_still_not_progress(self):
        """The fix must not turn noise into learning — `moved`/`improving`
        keep the noise floor."""
        out = stoploss.metric_trend([{"r2": 0.90}, {"r2": 0.91}])
        assert out["moved"] == [] and out["improving"] is False

    def test_a_real_gain_is_still_progress(self):
        out = stoploss.metric_trend([{"peak_snr": 5.0}, {"peak_snr": 9.0}])
        assert out["improving"] is True and "peak_snr" in out["moved"]


class TestTheJudgeFailsToItsSafeDefault:
    """A provider answering with an unexpected payload SHAPE raises
    AttributeError/TypeError/IndexError, and an exception escaping the call
    path would take down the plan instead of abstaining."""

    @pytest.mark.parametrize("exc", [AttributeError, TypeError, IndexError])
    def test_a_shape_error_abstains_rather_than_raising(self, exc):
        from quam_state_manager.core.autofit import auditor as A

        a = A.Auditor({"provider": "fake", "max_calls_per_plan": 5})

        def boom(_bundle):
            raise exc("provider returned an unexpected shape")

        a.fake = boom
        v = a.audit({"context": {}, "system": "", "images_b64": []})
        assert v.verdict == "abstain"
        # and the number-free asks answer their own safe default
        assert a.signature({"context": {}}).signature == "unclear"
        assert a.compare({"context": {}}).comparison == "same"
        assert a.triage({"context": {}}).state == "unreadable"
