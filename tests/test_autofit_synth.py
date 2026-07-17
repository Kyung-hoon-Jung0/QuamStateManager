"""Autofit synth generator — reader-compat goldens (docs/56 §6.1).

The whole autofit verification strategy rests on one claim: a synthetic run is
**indistinguishable from a real QUAlibrate run** to every SM reader. These
tests pin that claim per family: DatasetStore parses it (name/targets/fit_
results/outcomes/flags), fit_targets maps it, ndview builds a cube off the
DIMENSION_LIST h5, and the patches follow the contracts patches-first shape
(old = pre-update, quam_state snapshot = post-update).

Field lists are pinned against real archive runs captured 2026-07-17 (docs/56
§6.1) — e.g. qubit-spec ds_raw {I,Q,IQ_abs,phase,detuning,full_freq,qubit}.
"""
from __future__ import annotations

import json
from pathlib import Path

import h5py
import pytest

from quam_state_manager.core.autofit import synth
from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.core.fit_targets import resolve_fit_targets
from quam_state_manager.core import ndview


@pytest.fixture
def chip():
    return synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)


def _store_run(root: Path):
    store = DatasetStore(root)
    assert store.runs, "DatasetStore found no runs in the synth root"
    return store, store.runs[max(store.runs)]


ALL_FAMILIES = list(synth.GENERATORS)


class TestReaderCompat:
    @pytest.mark.parametrize("node_name", ALL_FAMILIES)
    def test_dataset_store_parses_every_family(self, tmp_path, chip, node_name):
        _, kind = synth.GENERATORS[node_name]
        targets = ["qA2-qA1"] if kind == "qubit_pairs" else ["qA1", "qA2"]
        sr = synth.synth_run(node_name, chip, targets, tmp_path, 101, seed=1)
        store, run = _store_run(tmp_path)

        assert run.experiment_name == node_name
        assert run.run_id == 101
        assert run.status == "finished"
        assert run.has_ds_raw and run.has_ds_fit and run.has_quam_state
        assert run.run_duration_s and run.run_duration_s > 0
        if kind == "qubit_pairs":
            assert run.qubit_pairs == targets
            # member qubits are folded in for filtering (dataset.py contract)
            assert set(run.qubits) == {"qA2", "qA1"}
        else:
            assert run.qubits == targets
        for t in targets:
            assert run.outcomes.get(t) == "successful"
            assert isinstance(run.fit_results.get(t), dict)
        assert sr.fit_results == run.fit_results

    def test_fit_targets_maps_the_mapped_families(self, tmp_path, chip):
        cases = {
            "08_qubit_spectroscopy": ("frequency", "qubits.qA1.f_01"),
            "11_power_rabi": ("opt_amp", "qubits.qA1.xy.operations.x180.amplitude"),
            "16_iq_blobs": ("iw_angle",
                            "qubits.qA1.resonator.operations.readout.integration_weights_angle"),
            "15a_readout_frequency_optimization": ("optimal_frequency",
                                                   "qubits.qA1.resonator.f_01"),
        }
        for i, (node, (key, path)) in enumerate(cases.items()):
            root = tmp_path / node
            synth.synth_run(node, chip, ["qA1"], root, 200 + i, seed=2)
            _, run = _store_run(root)
            targets = resolve_fit_targets(run)
            assert key in targets.get("qA1", {}), (node, targets)
            assert targets["qA1"][key]["path"] == path
            assert targets["qA1"][key]["value"] == run.fit_results["qA1"][key]

    @pytest.mark.parametrize("node_name", ALL_FAMILIES)
    def test_ndview_builds_a_classified_cube(self, tmp_path, chip, node_name):
        _, kind = synth.GENERATORS[node_name]
        targets = ["qA2-qA1"] if kind == "qubit_pairs" else ["qA1"]
        sr = synth.synth_run(node_name, chip, targets, tmp_path, 300, seed=3)
        raw = sr.folder / "ds_raw.h5"
        with h5py.File(raw) as f:
            var = next(k for k in f.keys()
                       if isinstance(f[k], h5py.Dataset) and f[k].ndim >= 2)
        cube = ndview.build_cube(raw, var)
        assert cube["var"] == var
        # dims resolved BY NAME via DIMENSION_LIST — the axis-truth contract
        dim_names = [d["name"] for d in cube["dims"]]
        assert dim_names[0] in ("qubit", "qubit_pair")

    def test_qubit_spec_ds_raw_field_list_pinned(self, tmp_path, chip):
        sr = synth.synth_run("08_qubit_spectroscopy", chip, ["qA1", "qA2"],
                             tmp_path, 400, seed=4)
        with h5py.File(sr.folder / "ds_raw.h5") as f:
            names = set(f.keys())
        # the real-archive field list (docs/56 §6.1)
        assert {"I", "Q", "IQ_abs", "phase", "detuning", "full_freq",
                "qubit"} <= names


class TestPatchesContract:
    def test_patches_old_is_pre_update_and_snapshot_is_post(self, tmp_path, chip):
        pre = chip.get("qubits.qA1.f_01")
        sr = synth.synth_run("08_qubit_spectroscopy", chip, ["qA1"], tmp_path,
                             500, seed=5)
        patch = next(p for p in sr.patches
                     if p["path"] == "/quam/qubits/qA1/f_01")
        assert patch["old"] == pre
        assert patch["value"] == sr.fit_results["qA1"]["frequency"]
        # the sim chip moved (record_state_updates mirror)
        assert chip.get("qubits.qA1.f_01") == patch["value"]
        # and the run's quam_state snapshot is POST-update (patches-first rule)
        snap = json.loads((sr.folder / "quam_state" / "state.json")
                          .read_text(encoding="utf-8"))
        assert snap["qubits"]["qA1"]["f_01"] == patch["value"]

    def test_apply_updates_false_emits_no_patches(self, tmp_path, chip):
        pre = chip.get("qubits.qA1.f_01")
        sr = synth.synth_run("08_qubit_spectroscopy", chip, ["qA1"], tmp_path,
                             501, seed=5, apply_updates=False)
        assert sr.patches == []
        assert chip.get("qubits.qA1.f_01") == pre

    def test_ramsey_patch_is_subtractive(self, tmp_path, chip):
        pre = chip.get("qubits.qA1.f_01")
        sr = synth.synth_run("12_ramsey", chip, ["qA1"], tmp_path, 502, seed=5)
        off = sr.fit_results["qA1"]["freq_offset"]
        patch = next(p for p in sr.patches
                     if p["path"] == "/quam/qubits/qA1/f_01")
        assert patch["value"] == pytest.approx(pre - off)

    def test_chevron_length_patch_is_ceil4(self, tmp_path, chip):
        sr = synth.synth_run("31_chevron_11_02", chip, ["qA2-qA1"], tmp_path,
                             503, seed=5)
        patch = next(p for p in sr.patches if p["path"].endswith("/length"))
        assert patch["value"] % 4 == 0


class TestGroundTruthSemantics:
    """The corruption modes must actually corrupt (and clean runs be clean) —
    otherwise the gate false-accept ledger measures nothing."""

    def test_clean_claim_is_near_truth(self, tmp_path, chip):
        t = chip.qubits["qA1"]
        sr = synth.synth_run("08_qubit_spectroscopy", chip, ["qA1"], tmp_path,
                             600, seed=6)
        assert abs(sr.fit_results["qA1"]["frequency"] - t.f_01) < t.q_fwhm / 4

    def test_wrong_peak_claim_sits_on_the_sidelobe(self, tmp_path, chip):
        t = chip.qubits["qA1"]
        sr = synth.synth_run("08_qubit_spectroscopy", chip, ["qA1"], tmp_path,
                             601, seed=6, corrupt="wrong_peak",
                             params={"frequency_span_in_mhz": 80})
        claimed = sr.fit_results["qA1"]["frequency"]
        assert abs(claimed - t.f_01) > 4 * t.q_fwhm            # far from truth
        assert sr.fit_results["qA1"]["success"] is True         # yet "successful"
        # ...and the raw data really contains the true peak too (G3's evidence)
        with h5py.File(sr.folder / "ds_raw.h5") as f:
            iq = f["IQ_abs"][0]
            freqs = f["full_freq"][0]
        import numpy as np
        peak_f = freqs[int(np.argmax(iq))]
        assert abs(peak_f - t.f_01) < 2 * t.q_fwhm

    def test_noisy_gets_cleaner_with_more_shots(self, tmp_path, chip):
        import numpy as np
        rs = []
        for shots, rid in ((100, 610), (6400, 611)):
            sr = synth.synth_run("25_T1", chip, ["qA1"], tmp_path, rid, seed=6,
                                 corrupt="noisy", params={"num_shots": shots})
            with h5py.File(sr.folder / "ds_raw.h5") as f:
                y = f["I"][0]
            # residual roughness ~ high-frequency diff amplitude
            rs.append(float(np.std(np.diff(y))))
        assert rs[1] < rs[0] / 3

    def test_out_of_band_t1_is_absurd(self, tmp_path, chip):
        sr = synth.synth_run("25_T1", chip, ["qA1"], tmp_path, 620, seed=6,
                             corrupt="out_of_band")
        assert sr.fit_results["qA1"]["t1"] < 0

    def test_deterministic_under_seed(self, tmp_path, chip):
        chip2 = synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)
        a = synth.synth_run("12_ramsey", chip, ["qA1"], tmp_path / "a", 630, seed=9)
        b = synth.synth_run("12_ramsey", chip2, ["qA1"], tmp_path / "b", 630, seed=9)
        assert a.fit_results == b.fit_results
