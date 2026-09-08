"""Customer rule 2026-09-09: "SM 전반에서 순서에 관한 것은 반드시 이렇게" --
101 < 1009 < 1010 < 1011, q2 < q10.

The sweep's own adversarial critic found these sites still ordering displayed
numbers as text after the seven area branches landed. Each pin drives the real
function with a fixture a STRING sort gets wrong.
"""

from __future__ import annotations

import json

import pytest

from quam_state_manager.core.loader import natural_key


def _wrong_under_string_sort(got: list[str]) -> None:
    assert got != sorted(got), f"the fixture must be one a string sort gets wrong: {got}"


def _chip(folder, weights):
    folder.mkdir(parents=True, exist_ok=True)
    state = {"qubits": {"q1": {"resonator": {"operations": {"readout": {
        "weights_imag": {str(i): v for i, v in weights.items()}}}}}}}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    return folder


class TestTheAgentsStagedWrites:
    """core/agent_runs.diff_states -- the leaf writes a calibration node made:
    the list the cockpit stages, a human approves, and MAX_WRITES truncates.
    A second live-diff implementation the differ.py fix never reached."""

    def test_paths_count_as_numbers(self, tmp_path):
        from quam_state_manager.core import agent_runs
        idx = [2, 10, 99, 101, 1009, 1010, 1011]
        before = _chip(tmp_path / "before", {i: 0.0 for i in idx})
        after = _chip(tmp_path / "after", {i: float(i) for i in idx})
        writes, _capped = agent_runs.diff_states(before, after)
        tail = "weights_imag."
        got = [w["path"].split(tail)[1] for w in writes if tail in w["path"]]
        assert got == [str(i) for i in idx], got
        _wrong_under_string_sort(got)


class TestTheCalibrationLogTimeline:
    """core/story._timeline and ._digest key the per-target strip. On a q10 lab
    the Calibration log listed q10 between q1 and q2."""

    @staticmethod
    def _cards():
        return [{"kind": "run", "run_id": 1, "family": "power_rabi", "family_label": "Power Rabi",
                 "node": "11_power_rabi", "family_short": "Rabi", "outcome": "ok", "gate": None,
                 "author": "human", "targets": ["q1", "q2", "q9", "q10", "q11", "q1-q10", "q1-q2"]}]

    def test_targets_are_natural(self):
        from quam_state_manager.core import story
        for fn in (story._timeline, story._digest):
            got = list(fn(self._cards()))
            assert got == ["q1", "q1-q2", "q1-q10", "q2", "q9", "q10", "q11"], (fn.__name__, got)
            _wrong_under_string_sort(got)


class TestTheQubitGridColumns:
    """core/qubit_columns._order_key -- the sibling of pair_columns, whose same
    tie-break the sweep fixed while this one kept the raw string."""

    def test_the_within_band_tie_break_is_natural(self):
        from quam_state_manager.core import qubit_columns as qc
        cols = [{"tmpl": f"extras.grp.confusion_{n}q", "section_key": "extras"} for n in (3, 10, 4)]
        ordered = sorted(cols, key=lambda c: qc._order_key(c, {}))
        assert [c["tmpl"] for c in ordered] == [
            "extras.grp.confusion_3q", "extras.grp.confusion_4q", "extras.grp.confusion_10q"]
        assert not isinstance(qc._order_key(cols[0], {})[1], str), "the tie-break is a natural key, not the raw string"


class TestTheFspPlan:
    """core/mw_fem -- the compensation plan a person reads before accepting it:
    one row per amplitude, ordered by channel then operation."""

    def test_channels_and_operations_are_natural(self):
        from quam_state_manager.core import mw_fem
        # the shared-port shape the real plan walks: two channels POINT at one
        # mw_output port, each carrying operations whose names end in a number
        ops = {f"x{n}": {"amplitude": 0.1} for n in (2, 10, 101, 1009)}
        merged = {
            "qubits": {q: {"xy": {"opx_output": f"#/wiring/qubits/{q}/xy/opx_output",
                                  "operations": dict(ops)}} for q in ("q2", "q10")},
            "wiring": {"qubits": {q: {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/1"}}
                                  for q in ("q2", "q10")}},
            "ports": {"mw_outputs": {"con1": {"1": {"1": {
                "band": 1, "full_scale_power_dbm": 0, "upconverter_frequency": 5.0e9}}}}},
        }
        plan = mw_fem.fsp_compensation_plan(merged, "ports.mw_outputs.con1.1.1.full_scale_power_dbm", -6)
        assert plan, "the fixture must produce a plan"
        paths = [a["path"] for a in plan["amps"]]
        chans = [p.split(".operations.")[0] for p in paths]
        assert chans.index("qubits.q2.xy") < chans.index("qubits.q10.xy"), chans
        names = [p.split(".operations.")[1].split(".")[0] for p in paths if p.startswith("qubits.q2.xy")]
        assert names == ["x2", "x10", "x101", "x1009"], names
        _wrong_under_string_sort(names)


class TestTheNodeLibrary:
    """core/node_scan -- the Experiment Runner's node list, by file name. Safe
    under the zero-padded qualibrate convention, wrong the moment a lab is not."""

    def test_an_unpadded_library_lists_in_order(self, tmp_path):
        from quam_state_manager.core import node_scan
        for name in ("2_res.py", "10_rabi.py", "1_tof.py", "10b_rabi_chevron.py"):
            (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
        got = [i.name for i in node_scan.scan_folder(tmp_path)]
        assert got == ["1_tof", "2_res", "10_rabi", "10b_rabi_chevron"], got
        _wrong_under_string_sort(got)


class TestTheNotesAndPorts:
    def test_touched_subjects_are_natural(self):
        from quam_state_manager.core import entity_notes
        subjects = ["qubits.q10", "qubits.q2", "qubits.q1", "qubits.q9"]
        got = entity_notes.touches(subjects, [f"qubits.{q}.f_01" for q in ("q1", "q2", "q9", "q10")])
        assert got == ["qubits.q1", "qubits.q2", "qubits.q9", "qubits.q10"], got
        _wrong_under_string_sort(got)

    # NOT a bug, measured: port_csv's controller keys are dict[int, set[int]]
    # (port_csv.py:164, `con = int(row["QM chassis"])`), so the plain sorted()
    # there is already numeric. The sweep's critic read them as "con1".."con10"
    # strings and was wrong; the change was reverted rather than left as a
    # no-op that costs an import.
