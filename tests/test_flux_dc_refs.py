"""flux_dc: references anywhere on the way, tunable couplers, a pair's
mutual flux bias, and the default flux point."""
import copy
import math

import pytest

from quam_state_manager.core import flux_dc
from quam_state_manager.core.flux_dc import element_dc_levels, line_dc_levels


def _levels(merged, element, port=0.0):
    return element_dc_levels(merged, element, port)


# ---- references --------------------------------------------------------

def test_alias_qubit_entry_is_followed():
    m = {"qubits": {"real": {"z": {"flux_point": "joint", "joint_offset": 0.3}},
                    "alias": "#/qubits/real"}}
    levels, why = _levels(m, "alias.z")
    assert why is None
    assert levels == [("channel offset", 0.0), ("joint flux-point offset", 0.3)]
    assert levels == _levels(m, "real.z")[0]


def test_alias_of_alias_two_hops():
    m = {"qubits": {"real": {"z": {"flux_point": "joint", "joint_offset": 0.3}},
                    "mid": "#/qubits/real", "alias": "#/qubits/mid"}}
    levels, why = _levels(m, "alias.z")
    assert why is None and ("joint flux-point offset", 0.3) in levels


def test_reference_in_the_middle_and_at_the_leaf():
    m = {"lines": {"zz": {"flux_point": "joint", "joint_offset": "#/values/j"}},
         "values": {"j": 0.25},
         "qubits": {"q": {"z": "#/lines/zz"}}}
    levels, why = _levels(m, "q.z")
    assert why is None and ("joint flux-point offset", 0.25) in levels


@pytest.mark.parametrize("qubits", [
    {"alias": "#/qubits/nowhere"},
    {"a": "#/qubits/b", "b": "#/qubits/a"},
])
def test_dangling_or_cyclic_qubit_reference_is_not_checked(qubits):
    levels, why = _levels({"qubits": qubits}, next(iter(qubits)) + ".z")
    assert levels is None
    assert "qubits." in why and "could not be followed" in why


def test_gate_check_and_spectator_paths_agree_on_alias():
    m = {"qubits": {"real": {"z": {"flux_point": "joint", "joint_offset": 0.3}},
                    "alias": "#/qubits/real"}}
    real = flux_dc.landed_path(m, ("qubits", "alias"))
    assert real == ("qubits", "real")
    assert line_dc_levels(m, real + ("z",), 0.0, "channel offset") == _levels(m, "alias.z")


# ---- default flux point ------------------------------------------------

def test_absent_flux_point_reads_independent():
    m = {"qubits": {"q": {"z": {"independent_offset": 0.45}}}}
    levels, why = _levels(m, "q.z")
    assert why is None
    assert ("independent flux-point offset", 0.45) in levels


def test_none_flux_point_reads_the_class_default():
    m = {"qubits": {"q": {"z": {"flux_point": None, "independent_offset": 0.2,
                                "joint_offset": 0.4}}}}
    levels, why = _levels(m, "q.z")
    assert why is None
    assert levels == [("channel offset", 0.0), ("independent flux-point offset", 0.2)]


def test_absent_flux_point_is_refused_by_the_range_check():
    """port 0 V, independent offset 0.45 V, level 0.1 V, range 0.5 V: 0.55 V clips."""
    from quam_state_manager.core import gate_check
    m = {"qubits": {"q": {"z": {"independent_offset": 0.45}}}}
    info = {"element": "q.z", "operation": "p", "port_kind": "lf", "offset": 0.0,
            "port": ("con1", 1), "output_mode": "direct", "waveform": {"samples": [0.1] * 4}}
    res = gate_check._range_check([info], {"rows": [_Row("q.z", "p", 1.0)]}, False, m)
    assert res["status"] == "fail", res
    assert "independent flux-point offset" in res["message"]


class _Row:
    def __init__(self, element, operation, scale):
        self.element, self.operation, self.scale = element, operation, scale


# ---- couplers ----------------------------------------------------------

def _coupler_chip(**coupler):
    c = {"id": "coupler_a_b", "operations": {"p": {}}}
    c.update(coupler)
    return {"qubits": {"a": {}, "b": {}},
            "qubit_pairs": {"a-b": {"qubit_control": "#/qubits/a",
                                    "qubit_target": "#/qubits/b", "coupler": c}}}


def test_coupler_levels_are_read():
    m = _coupler_chip(flux_point="on", decouple_offset=0.1, interaction_offset=0.4)
    levels, why = _levels(m, "coupler_a_b")
    assert why is None
    assert levels == [("channel offset", 0.0), ("coupler decouple offset", 0.1),
                      ("coupler interaction offset", 0.4)]


def test_coupler_without_id_is_found_by_pair_name():
    m = _coupler_chip(flux_point="off", decouple_offset=0.2)
    del m["qubit_pairs"]["a-b"]["coupler"]["id"]
    levels, why = _levels(m, "a-b.coupler")
    assert why is None and ("coupler decouple offset", 0.2) in levels


def test_coupler_with_no_stored_offset_is_not_checked():
    levels, why = _levels(_coupler_chip(flux_point="off"), "coupler_a_b")
    assert levels is None and "decouple_offset" in why


@pytest.mark.parametrize("bad", [float("nan"), math.inf, True, "0.2"])
def test_coupler_bad_offset_is_refused_naming_the_leaf(bad):
    levels, why = _levels(_coupler_chip(flux_point="off", decouple_offset=bad), "coupler_a_b")
    assert levels is None
    assert "qubit_pairs.a-b.coupler.decouple_offset" in why


def test_coupler_unknown_flux_point_is_refused():
    levels, why = _levels(_coupler_chip(flux_point="joint", decouple_offset=0.0), "coupler_a_b")
    assert levels is None and "flux_point" in why


def test_coupler_clipping_case_fails_the_gate_check():
    from quam_state_manager.core import gate_check
    m = _coupler_chip(flux_point="off", decouple_offset=0.4)
    info = {"element": "coupler_a_b", "operation": "p", "port_kind": "lf", "offset": 0.0,
            "port": ("con1", 2), "output_mode": "direct", "waveform": {"samples": [0.3] * 4}}
    res = gate_check._range_check([info], {"rows": [_Row("coupler_a_b", "p", 1.0)]}, False, m)
    assert res["status"] == "fail", res
    assert "coupler decouple offset" in res["message"]


# ---- mutual flux bias --------------------------------------------------

def _mutual_chip(bias):
    return {"qubits": {"a": {"z": {"flux_point": "joint", "joint_offset": 0.0}},
                       "b": {"z": {"flux_point": "joint", "joint_offset": 0.0}}},
            "qubit_pairs": {"a-b": {"qubit_control": "#/qubits/a",
                                    "qubit_target": "#/qubits/b",
                                    "mutual_flux_bias": bias}}}


def test_mutual_flux_bias_is_a_level_of_each_qubit():
    m = _mutual_chip([0.1, -0.2])
    assert ("mutual flux bias of a-b", 0.1) in _levels(m, "a.z")[0]
    assert ("mutual flux bias of a-b", -0.2) in _levels(m, "b.z")[0]


@pytest.mark.parametrize("bias", [[float("nan"), 0], [True, 0], ["x", 0], [0.1], 0.3])
def test_mutual_flux_bias_bad_value_is_refused(bias):
    levels, why = _levels(_mutual_chip(bias), "a.z")
    assert levels is None and "qubit_pairs.a-b.mutual_flux_bias" in why


# ---- unchanged output for a plain chip ---------------------------------

PLAIN = {"qubits": {"q1": {"z": {"flux_point": "joint", "joint_offset": 0.12,
                                 "independent_offset": 0.3}},
                    "q2": {"z": {"flux_point": "zero"}},
                    "q3": {"xy": {}}},
         "qubit_pairs": {"q1-2": {"qubit_control": "#/qubits/q1",
                                  "qubit_target": "#/qubits/q2"}}}


def test_plain_chip_output_is_unchanged():
    m = copy.deepcopy(PLAIN)
    out = [repr(_levels(m, e, 0.05)) for e in ("q1.z", "q2.z", "q3.z", "q1.xy", "q1-2.coupler")]
    assert out == [
        "([('channel offset', 0.05), ('joint flux-point offset', 0.12)], None)",
        "([('channel offset', 0.05), ('zero flux-point offset', 0.0)], None)",
        "([('channel offset', 0.05)], None)",
        "([('channel offset', 0.05)], None)",
        "([('channel offset', 0.05)], None)",
    ]
    assert m == PLAIN
