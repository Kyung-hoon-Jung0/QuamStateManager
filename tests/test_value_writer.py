"""Which run WROTE a value -- core.value_writer, one fixture per mismatch class.

Customer report 2026-09-29 (KRISS_CZ chip): *"In Chip Status Trends, one IRB
point says its run is a flux short distortion experiment -- how can that be?
T1 points also point to runs that are not T1."* Measured on that chip's own
history: 157 of 219 run-bearing T1/T2/IRB change points named a run that did
not write the value. Each class below is one of the real mechanisms, rebuilt
as a tiny dataset (run folders = node.json + quam_state/state.json):

A. an EXTERNAL snapshot that ``_enrich_run_fields`` linked, by content hash,
   to a run that finished AFTER the value was on disk (IRB q1-2 cz_SNZ
   0.92328 @ 20260914_005952 -> "#3087 20 Flux short"; wrote it: #3078 37b);
B. an experiment snapshot of an unrelated node whose save CARRIED a value an
   uncaptured run wrote, provable by that run's own patches (T1 q2 @
   20260908_083223 -> "#401 24 AllXY"; wrote it: #394 25_T1);
C. the same without patches: the family run whose save introduced it;
D. successful family RERUNS that did not update the state carry the value --
   the writer is the one whose save introduced it (IRB q3-4 cz_SNZ: fifteen
   37b reruns carried #2653's value);
E. a value no run of the family wrote (rounded T1s saved first by a TWPA
   run) -> "captured", never a confident run;
F. a family run whose recorded patches do not include THIS qubit (12_ramsey
   on q3 captured q1/q2/q4/q5 T2ramsey changes) -> not the writer;
G. a family run whose outcome for this qubit FAILED -> not the writer;
H. the captured run did write it -> the captured run;
I. a leaf with no family vocabulary: named only when its save introduced it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import value_writer as vw

T1 = "qubits.q1.T1"
IRB = "qubit_pairs.q3-4.macros.cz_SNZ.fidelity.InterleavedRB"


@pytest.fixture(autouse=True)
def _fresh():
    vw.clear_caches()
    yield
    vw.clear_caches()


class Lab:
    """A dataset root: ``<root>/<date>/#<id>_<name>_<hhmmss>``."""

    def __init__(self, root: Path):
        self.root = root
        self.state: dict = {"qubits": {"q1": {"T1": 1e-5, "f_01": 5e9},
                                       "q2": {"T1": 2e-5}},
                            "qubit_pairs": {"q3-4": {"macros": {"cz_SNZ": {
                                "fidelity": {"InterleavedRB": 0.90}}}}}}

    def set(self, dot: str, value):
        d = self.state
        segs = dot.split(".")
        for s in segs[:-1]:
            d = d.setdefault(s, {})
        d[segs[-1]] = value

    def run(self, rid: int, name: str, *, end: str = "2026-09-14T00:00:00+00:00",
            qubits=None, pairs=None, operation=None, outcomes=None,
            patches=None, writes: dict | None = None) -> Path:
        for k, v in (writes or {}).items():
            self.set(k, v)
        f = self.root / "2026-09-14" / f"#{rid}_{name}_000000"
        (f / "quam_state").mkdir(parents=True)
        model = {}
        if qubits is not None:
            model["qubits"] = qubits
        if pairs is not None:
            model["qubit_pairs"] = pairs
        if operation is not None:
            model["operation"] = operation
        node = {"metadata": {"name": name, "run_end": end, "status": "finished"},
                "data": {"parameters": {"model": model}, "outcomes": outcomes or {}},
                "id": rid}
        if patches is not None:
            node["patches"] = patches
        (f / "node.json").write_text(json.dumps(node), encoding="utf-8")
        (f / "quam_state" / "state.json").write_text(json.dumps(self.state),
                                                     encoding="utf-8")
        return f


def _ask(leaf, value, ts, run_folder: Path, rid: int, node: str, prev=None):
    return vw.attribute(leaf, value, ts,
                        {"run": rid, "node": node, "folder": str(run_folder)},
                        prev, lambda f: Path(f) if f and Path(f).is_dir() else None)


# Snapshot ts is UTC (history._now_ts); all runs below end 2026-09-14T00:00Z
# unless the class needs otherwise.
TS = "20260914_010000_000"
PREV = "20260913_000000_000"


def test_A_an_enriched_external_snapshot_never_names_a_run_that_ended_after_it(tmp_path):
    lab = Lab(tmp_path)
    lab.run(9, "12_ramsey")
    lab.run(10, "37b_two_qubit_interleaved_cz_rb", pairs=["q3-4"], operation="cz_SNZ",
            outcomes={"q3-4": "successful"}, writes={IRB: 0.98})
    late = lab.run(11, "20_qubit_flux_short_distortion", qubits=["q1"],
                   end="2026-09-14T01:05:00+00:00")        # 5 min AFTER the snapshot
    a = _ask(IRB, 0.98, TS, late, 11, "20_qubit_flux_short_distortion", PREV)
    assert a["verdict"] == "other" and a["run"] == 10, a


def test_B_a_carried_value_is_traced_to_the_run_whose_patches_wrote_it(tmp_path):
    lab = Lab(tmp_path)
    lab.run(393, "25_T1", qubits=["q1"], outcomes={"q1": "successful"},
            patches=[{"op": "replace", "path": "/quam/qubits/q1/T1", "value": 9.6e-6}],
            writes={T1: 9.6e-6})
    cap = lab.run(401, "24_all_xy", qubits=["q1"])
    a = _ask(T1, 9.6e-6, TS, cap, 401, "24_all_xy", PREV)
    assert a == {"verdict": "other", "run": 393, "node": "25_T1",
                 "folder": str(tmp_path / "2026-09-14" / "#393_25_T1_000000"),
                 "how": "proven"}


def test_C_without_patches_the_family_run_that_introduced_it(tmp_path):
    lab = Lab(tmp_path)
    lab.run(390, "11_power_rabi", qubits=["q1"])
    lab.run(394, "25_T1", qubits=["q1"], outcomes={"q1": "successful"},
            writes={T1: 9.6e-6})
    cap = lab.run(401, "24_all_xy", qubits=["q1"])
    a = _ask(T1, 9.6e-6, TS, cap, 401, "24_all_xy", PREV)
    assert a["verdict"] == "other" and a["run"] == 394 and a["how"] == "consistent"


def test_D_successful_reruns_that_did_not_update_are_carriers(tmp_path):
    lab = Lab(tmp_path)
    lab.run(2640, "12_ramsey", qubits=["q1"])
    lab.run(2653, "37b_two_qubit_interleaved_cz_rb", pairs=["q3-4"], operation="cz_SNZ",
            outcomes={"q3-4": "successful"}, writes={IRB: 0.9858})
    for rid in (2657, 2661, 2665):                       # reruns, no state update
        lab.run(rid, "37b_two_qubit_interleaved_cz_rb", pairs=["q3-4"],
                operation="cz_SNZ", outcomes={"q3-4": "successful"})
    last = lab.run(2669, "37b_two_qubit_interleaved_cz_rb", pairs=["q3-4"],
                   operation="cz_SNZ", outcomes={"q3-4": "successful"})
    # captured BY a carrier of the right family: still not named
    a = _ask(IRB, 0.9858, TS, last, 2669, "37b_two_qubit_interleaved_cz_rb", PREV)
    assert a["verdict"] == "other" and a["run"] == 2653, a
    # ...and captured by an unrelated node later on
    cap = lab.run(3087, "20_qubit_flux_short_distortion", qubits=["q1"])
    a = _ask(IRB, 0.9858, TS, cap, 3087, "20_qubit_flux_short_distortion", PREV)
    assert a["verdict"] == "other" and a["run"] == 2653, a


def test_E_a_value_no_family_run_wrote_is_only_captured(tmp_path):
    lab = Lab(tmp_path)
    lab.run(2350, "25_T1", qubits=["q1"], outcomes={"q1": "successful"},
            writes={T1: 1.1e-5})
    lab.run(2354, "04d_twpa_fine_tuning", writes={T1: 7.866e-6})   # an edit, carried
    cap = lab.run(2359, "15b_readout_weights_optimization", qubits=["q1"])
    a = _ask(T1, 7.866e-6, TS, cap, 2359, "15b_readout_weights_optimization", PREV)
    assert a == {"verdict": "captured"}


def test_F_recorded_patches_that_skip_this_qubit_exclude_the_run(tmp_path):
    lab = Lab(tmp_path)
    lab.run(2310, "04d_twpa_fine_tuning", writes={"qubits.q1.T2ramsey": 1.68e-5})
    cap = lab.run(2320, "12_ramsey", qubits=["q3"], outcomes={"q3": "successful"},
                  patches=[{"op": "replace", "path": "/quam/qubits/q3/T2ramsey",
                            "value": 7.0e-6}])
    a = _ask("qubits.q1.T2ramsey", 1.68e-5, TS, cap, 2320, "12_ramsey", PREV)
    assert a == {"verdict": "captured"}


def test_G_a_failed_outcome_for_this_qubit_is_not_a_write(tmp_path):
    lab = Lab(tmp_path)
    lab.run(1560, "11_power_rabi", writes={T1: 9.6e-6})
    cap = lab.run(1572, "25b_T1_vs_flux", qubits=["q1"], outcomes={"q1": "failed"})
    a = _ask(T1, 9.6e-6, TS, cap, 1572, "25b_T1_vs_flux", PREV)
    assert a == {"verdict": "captured"}


def test_H_the_captured_run_that_wrote_it_is_itself(tmp_path):
    lab = Lab(tmp_path)
    lab.run(335, "11_power_rabi")
    cap = lab.run(336, "25_T1", qubits=["q1"], outcomes={"q1": "successful"},
                  patches=[{"op": "replace", "path": "/quam/qubits/q1/T1", "value": 1.03e-5}],
                  writes={T1: 1.03e-5})
    assert _ask(T1, 1.03e-5, TS, cap, 336, "25_T1", PREV)["verdict"] == "captured-run"
    # without patches, the family run whose save introduced it
    cap2 = lab.run(337, "25_T1", qubits=["q1"], outcomes={"q1": "successful"},
                   writes={T1: 9.7e-6})
    assert _ask(T1, 9.7e-6, TS, cap2, 337, "25_T1", PREV) == {
        "verdict": "captured-run", "how": "consistent"}


def test_the_oldest_run_on_record_is_never_proven_by_an_introduction(tmp_path):
    """With no earlier run to compare against, "its save introduced the
    value" cannot be shown -- only patches could prove it."""
    lab = Lab(tmp_path)
    lab.run(1, "25_T1", qubits=["q1"], outcomes={"q1": "successful"}, writes={T1: 4e-5})
    cap = lab.run(2, "24_all_xy", qubits=["q1"])
    assert _ask(T1, 4e-5, TS, cap, 2, "24_all_xy", PREV) == {"verdict": "captured"}


def test_I_a_leaf_with_no_vocabulary_is_named_only_when_its_save_introduced_it(tmp_path):
    leaf = "qubits.q1.f_01"
    lab = Lab(tmp_path)
    lab.run(1, "04d_twpa_fine_tuning")
    r = lab.run(2, "08_qubit_spectroscopy", qubits=["q1"], writes={leaf: 5.1e9})
    assert _ask(leaf, 5.1e9, TS, r, 2, "08_qubit_spectroscopy", PREV)["verdict"] == "captured-run"
    carrier = lab.run(3, "24_all_xy", qubits=["q1"])
    assert _ask(leaf, 5.1e9, TS, carrier, 3, "24_all_xy", PREV) == {"verdict": "captured"}


def test_a_family_run_on_another_CZ_variant_is_not_the_writer(tmp_path):
    lab = Lab(tmp_path)
    lab.run(5, "30b_cz_interaction_point_planner", writes={IRB: 0.97})
    cap = lab.run(6, "37b_two_qubit_interleaved_cz_rb", pairs=["q3-4"], operation="cz_GNZ",
                  outcomes={"q3-4": "successful"})
    assert _ask(IRB, 0.97, TS, cap, 6, "37b_two_qubit_interleaved_cz_rb", PREV) == {
        "verdict": "captured"}


def test_an_unresolvable_folder_or_no_run_is_unverifiable_not_captured(tmp_path):
    assert vw.attribute(T1, 1e-5, TS, {"run": 9, "node": "x", "folder": str(tmp_path / "gone")},
                        None, lambda f: None) == {"verdict": "unverifiable"}
    assert vw.attribute(T1, 1e-5, TS, {"run": None, "node": None, "folder": None},
                        None, lambda f: None) == {"verdict": "unverifiable"}


def test_the_search_stops_at_the_previous_change_point(tmp_path):
    """A family run that SAVED before the previous change point cannot have
    written a value that point did not hold."""
    lab = Lab(tmp_path)
    lab.run(99, "12_ramsey", end="2026-09-09T00:00:00+00:00")
    lab.run(100, "25_T1", qubits=["q1"], outcomes={"q1": "successful"},
            end="2026-09-10T00:00:00+00:00", writes={T1: 3e-5})
    cap = lab.run(200, "24_all_xy", qubits=["q1"])
    a = _ask(T1, 3e-5, TS, cap, 200, "24_all_xy", prev="20260913_000000_000")
    assert a == {"verdict": "captured"}
    vw.clear_caches()
    a = _ask(T1, 3e-5, TS, cap, 200, "24_all_xy", prev="20260901_000000_000")
    assert a["verdict"] == "other" and a["run"] == 100


def test_an_e_f_ramsey_is_not_a_T2ramsey_writer():
    from quam_state_manager.core.story import node_label
    assert vw.node_family("29b_ramsey_ef") == "Ramsey ef"
    assert vw.node_family("12_ramsey") == "Ramsey"
    assert vw.node_family("29b_ramsey_ef") not in vw.families_for("qubits.q1.T2ramsey")
    assert node_label("29b_ramsey_ef") == "29b Ramsey ef"


def test_results_are_memoised_and_run_folders_read_once(tmp_path, monkeypatch):
    lab = Lab(tmp_path)
    lab.run(393, "12_ramsey")
    lab.run(394, "25_T1", qubits=["q1"], outcomes={"q1": "successful"}, writes={T1: 9.6e-6})
    cap = lab.run(401, "24_all_xy", qubits=["q1"])
    calls = []
    real = vw.attribute
    monkeypatch.setattr(vw, "attribute", lambda *a, **k: calls.append(1) or real(*a, **k))
    res = lambda f: Path(f) if f and Path(f).is_dir() else None  # noqa: E731
    cap_d = {"run": 401, "node": "24_all_xy", "folder": str(cap)}
    first = vw.attribute_cached(T1, 9.6e-6, TS, cap_d, PREV, res)
    again = vw.attribute_cached(T1, 9.6e-6, TS, cap_d, PREV, res)
    assert first == again and first["run"] == 394 and len(calls) == 1


# ---- each guard on its own (mutation-checked: each fixture below goes red
# when exactly that guard is removed; the introducer check alone would let
# every one of them name the captured run). An out-of-band edit between two
# runs is what makes the captured run's save "introduce" a value it never
# measured.

def test_guard_time_a_family_run_that_saved_after_the_snapshot(tmp_path):
    lab = Lab(tmp_path)
    lab.run(20, "12_ramsey")
    lab.set(T1, 5e-5)                                      # the external edit
    late = lab.run(21, "25_T1", qubits=["q1"], outcomes={"q1": "successful"},
                   end="2026-09-14T01:05:00+00:00")        # saved 5 min AFTER TS
    assert _ask(T1, 5e-5, TS, late, 21, "25_T1", PREV) == {"verdict": "captured"}


def test_guard_patches_a_family_run_whose_patches_skip_this_leaf(tmp_path):
    leaf = "qubits.q1.T2ramsey"
    lab = Lab(tmp_path)
    lab.run(2310, "11_power_rabi")
    lab.set(leaf, 1.68e-5)                                 # the external edit
    cap = lab.run(2320, "12_ramsey", qubits=["q1", "q3"],
                  outcomes={"q1": "successful", "q3": "successful"},
                  patches=[{"op": "replace", "path": "/quam/qubits/q3/T2ramsey",
                            "value": 7.0e-6}])
    assert _ask(leaf, 1.68e-5, TS, cap, 2320, "12_ramsey", PREV) == {"verdict": "captured"}


def test_guard_outcome_a_family_run_that_failed_on_this_qubit(tmp_path):
    lab = Lab(tmp_path)
    lab.run(1571, "11_power_rabi")
    lab.set(T1, 9.6e-6)                                    # the external edit
    cap = lab.run(1572, "25b_T1_vs_flux", qubits=["q1"], outcomes={"q1": "failed"})
    assert _ask(T1, 9.6e-6, TS, cap, 1572, "25b_T1_vs_flux", PREV) == {"verdict": "captured"}


def test_guard_operation_an_IRB_run_on_another_CZ_variant(tmp_path):
    lab = Lab(tmp_path)
    lab.run(5, "11_power_rabi")
    lab.set(IRB, 0.97)                                     # the external edit
    cap = lab.run(6, "37b_two_qubit_interleaved_cz_rb", pairs=["q3-4"], operation="cz_GNZ",
                  outcomes={"q3-4": "successful"})
    assert _ask(IRB, 0.97, TS, cap, 6, "37b_two_qubit_interleaved_cz_rb", PREV) == {
        "verdict": "captured"}


def test_guard_vocabulary_a_run_of_another_family_whose_save_introduced_it(tmp_path):
    lab = Lab(tmp_path)
    lab.run(3086, "11_power_rabi")
    lab.set(T1, 1.29e-5)                                   # the external edit
    cap = lab.run(3087, "20_qubit_flux_short_distortion", qubits=["q1"],
                  outcomes={"q1": "successful"})
    assert _ask(T1, 1.29e-5, TS, cap, 3087, "20_qubit_flux_short_distortion", PREV) == {
        "verdict": "captured"}


def test_a_qubit_gate_fidelity_has_the_1Q_RB_vocabulary_a_pair_one_does_not(tmp_path):
    """KRISS_CZ: #1485 27 1Q RB ran on q1 only (its patches say so) and
    captured q2's gate_fidelity.averaged, which #402 -- a 1Q RB run on q2 --
    wrote."""
    assert vw.families_for("qubits.q2.gate_fidelity.averaged") == frozenset({"1Q RB", "1Q IRB"})
    assert vw.families_for("qubit_pairs.q1-2.macros.cz.fidelity.averaged") is None
    leaf = "qubits.q2.gate_fidelity.averaged"
    lab = Lab(tmp_path)
    lab.run(401, "24_all_xy")
    lab.run(402, "27_single_qubit_randomized_benchmarking", qubits=["q2"],
            outcomes={"q2": "successful"}, writes={leaf: 0.998})
    cap = lab.run(1485, "27_single_qubit_randomized_benchmarking", qubits=["q1"],
                  outcomes={"q1": "successful"},
                  patches=[{"op": "replace", "path": "/quam/qubits/q1/gate_fidelity/averaged",
                            "value": 0.9975}])
    a = _ask(leaf, 0.998, TS, cap, 1485, "27_single_qubit_randomized_benchmarking", PREV)
    assert a["verdict"] == "other" and a["run"] == 402
