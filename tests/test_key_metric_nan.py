"""F21 -- the Datasets KEY METRIC column never prints a NaN, and the plot-apply
popup's PREVIOUS value is shown whole.

* ``DatasetStore._extract_key_metric`` took the first ``isinstance(v, (int,
  float))`` fit value, which a float NaN passes: every failed
  ``06_resonator_spectroscopy_vs_flux`` run showed "nan" (and a failed T1
  would show "nan ns", a leading bool "1.0000"). The sibling
  ``_extract_sort_scalars`` already rejects bool/NaN/inf; the key metric now
  applies the same rule and reads blank ("-").
* The persisted store (docs/171) re-serves ``key_metric`` on warm starts, so
  ``_STORE_CACHE_V`` is bumped -- a v1 cache still carrying "nan" is a miss.
* ``.plot-apply-old-val`` cut a full-precision value with an ellipsis; it now
  wraps (and ``_setOldVal`` titles the slot, pinned in
  ``tests/ds_value_text_selfcheck.cjs``, driven below with the r2-31 copy pins).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import dataset as D
from quam_state_manager.core.dataset import DatasetStore, RunInfo

_ROOT = Path(__file__).resolve().parent.parent


def _run(exp: str, fit: dict) -> RunInfo:
    return RunInfo(run_id=1, experiment_name=exp, date="2026-09-06",
                   time="01:00:00", folder_path=Path("."), fit_results=fit)


class TestKeyMetricNeverNan:
    def test_the_lab_F_vs_flux_shape_reads_blank(self):
        # the reported run shape: no "frequency" key, the first numeric is NaN
        r = _run("06_resonator_spectroscopy_vs_flux",
                 {"q1": {"success": False, "resonator_frequency": float("nan"),
                         "ridge_coverage": 1.0}})
        assert DatasetStore._extract_key_metric(r) == ""

    def test_a_failed_headline_fit_reads_blank(self):
        for exp, fit in (("t1", {"T1": float("nan")}),
                         ("ramsey", {"T2_star": float("inf")}),
                         ("03_resonator_spectroscopy", {"frequency": float("nan"),
                                                        "fwhm": 1.2e6})):
            m = DatasetStore._extract_key_metric(_run(exp, {"q1": fit}))
            assert m == "", (exp, m)

    def test_a_bool_is_not_a_metric(self):
        m = DatasetStore._extract_key_metric(_run("x", {"q1": {"converged": True,
                                                               "chi2": 0.5}}))
        assert m != "1.0000"
        assert m == DatasetStore._extract_key_metric(_run("x", {"q1": {"chi2": 0.5}}))

    def test_finite_values_are_unchanged(self):
        assert DatasetStore._extract_key_metric(_run("t1", {"q1": {"T1": 8e-6}})) == "8.00 µs"
        assert DatasetStore._extract_key_metric(
            _run("x", {"q1": {"success": True, "a": 0.1}})) != ""

    def test_the_compact_row_carries_the_blank(self, tmp_path):
        run = tmp_path / "2026-09-06" / "#39_06_resonator_spectroscopy_vs_flux_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "06_resonator_spectroscopy_vs_flux", "status": "successful"},
            "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
            "id": 39, "parents": [], "created_at": "2026-09-06T01:00:00",
        }), encoding="utf-8")
        # the file on disk spells it NaN, exactly as the lab writes it
        (run / "data.json").write_text(
            '{"fit_results": {"q1": {"success": false, "resonator_frequency": NaN,'
            ' "ridge_coverage": 1.0}}}', encoding="utf-8")
        ds = DatasetStore(tmp_path)
        info = next(iter(ds.runs.values()))
        assert info.key_metric == ""
        rows = ds.list_runs_compact()
        assert rows and all(r["metric"] == "" for r in rows), rows


class TestTheCacheDoesNotReServeNan:
    def test_a_v1_cache_is_a_miss(self, tmp_path):
        assert D._STORE_CACHE_V >= 2
        root, cache = tmp_path / "data", tmp_path / "inst" / "workspace_cache"
        cache.parent.mkdir()
        run = root / "2026-09-06" / "#1_t1_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "t1", "status": "successful"},
            "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
            "id": 1, "parents": [], "created_at": "2026-09-06T01:00:00",
        }), encoding="utf-8")
        (run / "data.json").write_text('{"fit_results": {"q1": {"T1": NaN}}}',
                                       encoding="utf-8")
        s1 = DatasetStore(root, cache_dir=cache)
        assert s1.flush_store_cache()
        f = sorted(cache.glob("ds_*.json"))[0]
        raw = json.loads(f.read_text(encoding="utf-8"))
        # what the OLD code persisted: version 1, key_metric "nan"
        raw["v"] = 1
        for r in raw["runs"]:
            r["key_metric"] = "nan ns"
        f.write_text(json.dumps(raw), encoding="utf-8")
        s2 = DatasetStore(root, cache_dir=cache)
        assert s2.cache_hit_runs == 0
        assert next(iter(s2.runs.values())).key_metric == ""


def test_the_previous_value_wraps_instead_of_being_cut():
    css = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    m = re.search(r"\.plot-apply-old-val\s*\{([^}]*)\}", css)
    assert m, "the .plot-apply-old-val rule exists"
    body = m.group(1)
    assert "text-overflow" not in body and "nowrap" not in body, body
    assert "overflow-wrap: anywhere" in body, body


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_values_are_copied_and_shown_whole_selfcheck():
    """datasets-r2-31 (copy = the value only) + F21 (the previous value's
    title) against the REAL app.js: ``tests/ds_value_text_selfcheck.cjs``."""
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "ds_value_text_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.count("ok - ") >= 12, proc.stdout



class TestCoherenceKeyMetricHasAUnit:
    """A T1 node writes ``t1`` in ns to its fit results (it fits on its
    ``idle_time`` axis in ns) while the state stores seconds; the unit is
    decided from the run's own evidence, never from the magnitude."""

    # the shape of a real T1 run: fit t1 in ns, the saved T1 in s,
    # and an idle_time axis declared in ns
    T1_NS = 32535.733543954386
    T1_S = 3.2535733543954385e-05

    def _t1_run(self, tmp_path, *, saved=True, h5=False):
        d = tmp_path / "#9_25_T1"
        (d / "quam_state").mkdir(parents=True)
        if saved:
            (d / "quam_state" / "state.json").write_text(
                json.dumps({"qubits": {"qA1": {"T1": self.T1_S}}}), encoding="utf-8")
        if h5:
            h5py = pytest.importorskip("h5py")
            with h5py.File(d / "ds_fit.h5", "w") as f:
                f.create_dataset("qubit", data=[0])
                ds = f.create_dataset("idle_time", data=[16.0, 32.0])
                ds.attrs["units"] = "ns"
                ds.attrs["long_name"] = "idle time"
                tau = f.create_dataset("tau", data=[self.T1_NS])
                tau.attrs["units"] = "ns"
        r = _run("25_T1", {"qA1": {"t1": self.T1_NS, "t1_error": 1694.05, "success": True}})
        r.folder_path = d
        return r

    def test_saved_seconds_value_proves_the_fit_is_ns(self, tmp_path):
        assert DatasetStore._extract_key_metric(self._t1_run(tmp_path)) == "32.54 µs"

    def test_fit_axis_units_decide_when_nothing_was_saved(self, tmp_path):
        r = self._t1_run(tmp_path, saved=False, h5=True)
        assert DatasetStore._extract_key_metric(r) == "32.54 µs"

    def test_a_units_attribute_alone_is_not_evidence(self, tmp_path):
        """An echo fit file declares ``ns`` on T2_echo and stores 34863 ns,
        while data.json holds the same fit as 3.486e-05 s: only the value
        match decides, and it says seconds."""
        h5py = pytest.importorskip("h5py")
        d = tmp_path / "#10_26_echo"
        d.mkdir()
        with h5py.File(d / "ds_fit.h5", "w") as f:
            ax = f.create_dataset("idle_time", data=[16.0, 32.0])
            ax.attrs["units"] = "ns"
            v = f.create_dataset("T2_echo", data=[34863.26329943691])
            v.attrs["units"] = "ns"
        r = _run("26_echo", {"qA1": {"T2_echo": 3.486326329943691e-05, "success": True}})
        r.folder_path = d
        assert DatasetStore._extract_key_metric(r) == "34.86 µs"

    def test_no_evidence_shows_the_bare_number(self, tmp_path):
        r = self._t1_run(tmp_path, saved=False)
        assert DatasetStore._extract_key_metric(r) == "32535.7335"

    def test_seconds_stored_echo_reads_microseconds(self, tmp_path):
        d = tmp_path / "#10_26_echo"
        (d / "quam_state").mkdir(parents=True)
        (d / "quam_state" / "state.json").write_text(
            json.dumps({"qubits": {"qA1": {"T2echo": 3.486326329943691e-05}}}),
            encoding="utf-8")
        r = _run("26_echo", {"qA1": {"T2_echo": 3.486326329943691e-05, "success": True}})
        r.folder_path = d
        assert DatasetStore._extract_key_metric(r) == "34.86 µs"

    def test_a_non_coherence_key_stays_unitless(self):
        r = _run("99_other", {"qA1": {"tau_ratio": 12.5}})
        assert DatasetStore._extract_key_metric(r) == "12.5000"
