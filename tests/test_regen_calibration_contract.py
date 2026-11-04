"""A rebuild with no structural edit reads the source's calibration back, leaf for leaf.

Found re-generating a calibrated 21-qubit chip with no edits at all (2026-11-05): the
merge hands every pointer/object mismatch to the rebuild, so
- a pair macro's INLINE calibrated flux pulse became a link to a freshly built z op
  holding builder defaults (amplitude, smoothing, padding, even its class);
- ``resonator.operations.readout`` (an alias of the calibrated ``readout_square``)
  became a default readout pulse;
- ``-x90``'s detuning link was re-aimed from x90's detuning to x180's;
- the orphaned legacy ops whose links the first loss broke were deleted as
  "redundant", and the report said nothing changed.

The second phase of the source-form pass puts these forms back, and
``calibration_check`` makes the rebuild verify its own contract.
"""
import copy
import json
import os
import shutil
from pathlib import Path

import pytest

from quam_state_manager.core import regen_merge
from quam_state_manager.core.regen_merge import calibration_check, merge_states

Z = "#/qubits/q2/z/operations"


def _source() -> dict:
    """Shaped like the real chip: inline CZ macro pulses + legacy z ops linking into
    them, a readout alias, an -x90 detuning link to x90's."""
    return {
        "__class__": "lab.Quam",
        "qubits": {
            "q1": {"xy": {"operations": {
                       "x180_DragCosine": {"detuning": -18692.0, "length": 40},
                       "x90_DragCosine": {"detuning": 0, "length": 40},
                       "-x90_DragCosine": {"detuning": "#../x90_DragCosine/detuning",
                                           "length": 40}}},
                   "resonator": {"operations": {
                       "readout": "#./readout_square",
                       "readout_square": {"__class__": "quam.SquareReadoutPulse",
                                          "length": 1120, "amplitude": 0.0154,
                                          "threshold": 3.9e-4}}}},
            "q2": {"z": {"operations": {
                "cz_cosine_bipolar_pulse_q1": {
                    "__class__": "quam.CosineBipolarPulse", "amplitude": 0.5,
                    "length": "#/qubit_pairs/q2-q1/macros/cz_bipolar/flux_pulse_qubit/length",
                    "smoothing_length":
                        "#/qubit_pairs/q2-q1/macros/cz_bipolar/flux_pulse_qubit/smoothing_length"}}}},
        },
        "qubit_pairs": {"q2-q1": {
            "qubit_control": "#/qubits/q2", "qubit_target": "#/qubits/q1",
            "macros": {"cz_bipolar": {"flux_pulse_qubit": {
                "__class__": "quam.CosineBipolarPulse", "id": "cz_cosine_bipolar_pulse_q1",
                "amplitude": 0.209, "length": 56, "smoothing_length": 4,
                "post_zero_padding_length": 0}}}}},
    }


def _rebuild() -> dict:
    """What the builder writes for the same structure."""
    return {
        "__class__": "lab.Quam",
        "qubits": {
            "q1": {"xy": {"operations": {
                       "x180_DragCosine": {"detuning": 0.0, "length": 40},
                       "x90_DragCosine": {"detuning": 0.0, "length": 40},
                       "-x90_DragCosine": {"detuning": "#../x180_DragCosine/detuning",
                                           "length": 40}}},
                   "resonator": {"operations": {
                       "readout": {"__class__": "quam.SquareReadoutPulse",
                                   "length": 2000, "amplitude": 0.01, "threshold": 0.0}}}},
            "q2": {"z": {"operations": {
                "cz_bipolar_flux_pulse_q2_q1": {
                    "__class__": "quam.CosineBipolarPulse", "id": "cz_bipolar_flux_pulse_q2_q1",
                    "amplitude": 0.2094, "length": 56, "smoothing_length": 20,
                    "post_zero_padding_length": 20}}}},
        },
        "qubit_pairs": {"q2-q1": {
            "qubit_control": "#/qubits/q2", "qubit_target": "#/qubits/q1",
            "macros": {"cz_bipolar": {"flux_pulse_qubit": f"{Z}/cz_bipolar_flux_pulse_q2_q1"}}}},
    }


def _get(d, dot):
    for k in dot.split("."):
        d = d[k]
    return d


class TestANoEditRebuildReadsTheSource:
    def test_zero_resolved_differences(self):
        old = _source()
        r = merge_states(copy.deepcopy(old), _rebuild())
        assert calibration_check(old, None, r.merged, None) == []

    def test_the_inline_cz_pulse_comes_back(self):
        r = merge_states(_source(), _rebuild())
        fp = _get(r.merged, "qubit_pairs.q2-q1.macros.cz_bipolar.flux_pulse_qubit")
        assert isinstance(fp, dict) and fp["amplitude"] == 0.209
        assert fp["smoothing_length"] == 4 and fp["post_zero_padding_length"] == 0
        assert "qubit_pairs.q2-q1.macros.cz_bipolar.flux_pulse_qubit" in \
            r.stats.calibration_restored

    def test_the_readout_alias_comes_back(self):
        r = merge_states(_source(), _rebuild())
        assert _get(r.merged, "qubits.q1.resonator.operations.readout") == "#./readout_square"

    def test_the_minus_x90_detuning_follows_x90_again(self):
        r = merge_states(_source(), _rebuild())
        assert _get(r.merged, "qubits.q1.xy.operations.-x90_DragCosine.detuning") == \
            "#../x90_DragCosine/detuning"

    def test_a_legacy_op_whose_links_worked_is_not_pruned(self):
        r = merge_states(_source(), _rebuild())
        assert r.stats.pruned_ops == []
        assert "cz_cosine_bipolar_pulse_q1" in _get(r.merged, "qubits.q2.z.operations")

    def test_the_rebuilds_own_op_stays_as_an_addition(self):
        r = merge_states(_source(), _rebuild())
        assert "cz_bipolar_flux_pulse_q2_q1" in _get(r.merged, "qubits.q2.z.operations")


class TestTheUsersEditsAndCablingStand:
    def test_a_populate_edit_on_the_macro_keeps_the_rebuilds_value(self):
        p = "qubit_pairs.q2-q1.macros.cz_bipolar.flux_pulse_qubit.amplitude"
        r = merge_states(_source(), _rebuild(), protect_paths={p})
        assert not isinstance(_get(r.merged, "qubit_pairs.q2-q1.macros.cz_bipolar."
                                   "flux_pulse_qubit"), dict)

    def test_a_moved_port_is_never_put_back(self):
        ports = {"mw_outputs": {"con1": {"1": {"2": {"delay": 5}, "9": {"delay": 0}}}}}
        old = {"ports": copy.deepcopy(ports),
               "qubits": {"q1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"}}}}
        new = {"ports": copy.deepcopy(ports),
               "qubits": {"q1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/9"}}}}
        r = merge_states(old, new)
        assert r.merged["qubits"]["q1"]["xy"]["opx_output"] == "#/ports/mw_outputs/con1/1/9"


class TestAFormComesBackOnlyWhereNothingGetsWorse:
    def test_a_form_that_would_lose_a_value_stays_out_of_its_group(self):
        from quam_state_manager.core.regen_merge import _restore_source_forms
        # x reads 5 either way today; its source link now lands on a held 9.
        # d's link is the one that brings a value back.
        old = {"v": {"x": "#/t/a", "d": "#/v/e", "e": 1}, "t": {"a": 5}}
        merged = {"v": {"x": 5, "d": "#/v/f", "e": 1, "f": 2}, "t": {"a": 9}}
        restored: list = []
        _restore_source_forms(merged, old, None, {"t.a"}, restored)
        assert merged["v"]["x"] == 5
        assert merged["v"]["d"] == "#/v/e"
        assert restored == ["v.d"]

    def test_a_form_that_would_drop_a_source_leaf_is_refused(self):
        from quam_state_manager.core.regen_merge import _restore_source_forms
        old = {"v": {"x": "#/t/a", "d": "#/v/e", "e": 1}, "t": {"a": {"k": 5}}}
        merged = {"v": {"x": {"k": 5}, "d": "#/v/f", "e": 1, "f": 2}, "t": {"a": 3}}
        restored: list = []
        _restore_source_forms(merged, old, None, {"t.a"}, restored)
        assert merged["v"]["x"] == {"k": 5}
        assert restored == ["v.d"]


    def test_a_legacy_op_is_not_deleted_where_its_links_worked_in_the_source(self):
        # the macro is held by a wizard edit, so the inline form cannot come back and
        # the legacy op's links dangle: it is reported, never deleted as redundant
        p = "qubit_pairs.q2-q1.macros.cz_bipolar.flux_pulse_qubit"
        r = merge_states(_source(), _rebuild(), protect_paths={p})
        assert r.stats.pruned_ops == []
        assert "cz_cosine_bipolar_pulse_q1" in _get(r.merged, "qubits.q2.z.operations")
        assert any(d.startswith("qubits.q2.z.operations.cz_cosine_bipolar_pulse_q1")
                   for d in r.stats.dangling_grafts)


class TestTheCheckNamesEveryDifference:
    def test_it_reports_the_old_loss(self, monkeypatch):
        # with the second phase off, the check sees exactly the three losses
        orig = regen_merge._restore_source_forms
        monkeypatch.setattr(regen_merge, "_restore_source_forms",
                            lambda m, o, w, p, c=None, ow=None: orig(m, o, w, p))
        old = _source()
        r = merge_states(copy.deepcopy(old), _rebuild())
        paths = {d["path"] for d in calibration_check(old, None, r.merged, None)}
        assert "qubits.q1.resonator.operations.readout.length" in paths
        assert "qubits.q1.xy.operations.-x90_DragCosine.detuning" in paths
        assert "qubit_pairs.q2-q1.macros.cz_bipolar.flux_pulse_qubit.smoothing_length" in paths

    def test_skipped_paths_are_not_reported(self):
        old, new = _source(), copy.deepcopy(_source())
        new["qubits"]["q1"]["xy"]["operations"]["x180_DragCosine"]["detuning"] = 1.0
        assert [d["path"] for d in calibration_check(old, None, new, None)] == \
            ["qubits.q1.xy.operations.x180_DragCosine.detuning"]
        assert calibration_check(old, None, new, None,
                                 skip={"qubits.q1.xy.operations.x180_DragCosine"}) == []

    def test_a_vanished_leaf_is_reported(self):
        old, new = _source(), copy.deepcopy(_source())
        del new["qubits"]["q1"]["resonator"]["operations"]["readout_square"]["threshold"]
        d = calibration_check(old, None, new, None)
        assert {"path": "qubits.q1.resonator.operations.readout_square.threshold",
                "old": 3.9e-4, "new": None} in d

    def test_a_source_property_link_is_not_a_stored_value(self):
        old = {"q": {"a": {"f": "#./inferred_f"}}}
        assert calibration_check(old, None, {"q": {"a": {}}}, None) == []


# The real chip copy: read-only inputs, the merge runs on a scratch copy of the
# rebuild the evidence folder kept. Skipped where that folder is not on disk.
_STUDY = Path(os.environ.get("SM_REGEN_STUDY",
                             r"D:\work_laptop\study\2026-11-05_regen-append-qubit"))
_FRESH = Path(os.environ.get("SM_REGEN_FRESH", ""))


@pytest.mark.skipif(not ((_STUDY / "chip" / "state.json").is_file()
                         and (_FRESH / "state.json").is_file()),
                    reason="real chip copy / its fresh rebuild not on this machine")
def test_the_real_chip_rebuild_reads_the_source(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(_STUDY / "chip", src)
    fresh = tmp_path / "fresh"
    shutil.copytree(_FRESH, fresh)
    read = lambda d, f: json.loads((d / f).read_text(encoding="utf-8"))  # noqa: E731
    os_, ow = read(src, "state.json"), read(src, "wiring.json")
    ns, nw = read(fresh, "state.json"), read(fresh, "wiring.json")
    r = merge_states(copy.deepcopy(os_), ns, old_wiring=ow, new_wiring=nw)
    skip = {f"{p}.__class__" for p, _, _ in r.stats.class_changed}
    skip |= set(r.stats.schema_dropped)
    assert calibration_check(os_, ow, r.merged, nw, skip=skip) == []
    assert r.stats.pruned_ops == []
