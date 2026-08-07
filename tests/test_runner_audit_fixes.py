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
