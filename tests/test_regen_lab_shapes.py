"""A re-generate keeps what a lab built around the builder's shape.

Found re-generating real lab chips in their own envs (2026-11-02):
- a lab that calibrated x90's DRAG alpha apart from x180's lost it: the builder links
  x90.alpha to x180.alpha, and a link always won;
- a lab's own QDAC block (a top-level object, its `dc_channel` per flux line in the
  wiring, its trigger outputs on OPX digital ports) came back null;
- calibrated spare outputs on a FEM the spec still declares, but no rebuilt line uses,
  were dropped.
"""
from quam_state_manager.core.regen_merge import (
    _carryable_ports, graft_wiring_values, merge_states)

XY = "qubits.q1.xy.operations"


def _drag(alpha_x90):
    return {"qubits": {"q1": {"xy": {"operations": {
        "x180": {"alpha": -0.62, "length": 16},
        "x90": {"alpha": alpha_x90, "length": "#../x180/length"}}}}}}


class TestALabsOwnLiteralBeatsTheBuildersLink:
    def test_a_separately_calibrated_value_is_kept(self):
        new = _drag("#../x180/alpha")
        r = merge_states(_drag(-0.58), new)
        assert r.merged["qubits"]["q1"]["xy"]["operations"]["x90"]["alpha"] == -0.58
        assert r.stats.links_not_taken == [f"{XY}.x90.alpha"]

    def test_an_equal_value_is_no_calibration_to_keep(self):
        """Same value: nothing is lost either way; the source's own form (its literal)
        comes back through the form pass, not as a link not taken."""
        r = merge_states(_drag(-0.62), _drag("#../x180/alpha"))
        assert r.merged["qubits"]["q1"]["xy"]["operations"]["x90"]["alpha"] == -0.62
        assert r.stats.links_not_taken == []
        assert f"{XY}.x90.alpha" in r.stats.source_forms_restored

    def test_a_populate_edit_keeps_the_link(self):
        r = merge_states(_drag(-0.58), _drag("#../x180/alpha"), protect_paths={f"{XY}.x90.alpha"})
        assert r.merged["qubits"]["q1"]["xy"]["operations"]["x90"]["alpha"] == "#../x180/alpha"


class TestWiringValues:
    def test_a_plain_lab_value_under_a_rebuilt_line_is_carried(self):
        old = {"wiring": {"qubits": {"q1": {"z": {"opx_output": "#/ports/a/con1/5/1", "dc_channel": 3}},
                                     "q2": {"z": {"opx_output": "#/ports/a/con1/5/2", "dc_channel": 4}}}}}
        new = {"wiring": {"qubits": {"q1": {"z": {"opx_output": "#/ports/a/con1/5/2"}}, "q2": {}}}}
        got = graft_wiring_values(old, new)
        assert got == ["wiring.qubits.q1.z.dc_channel"]
        # the rebuild's cabling stands; a line the rebuild did not make brings nothing back
        assert new["wiring"]["qubits"]["q1"]["z"] == {"opx_output": "#/ports/a/con1/5/2", "dc_channel": 3}
        assert new["wiring"]["qubits"]["q2"] == {}

    def test_a_pointer_is_never_carried(self):
        old = {"wiring": {"qubits": {"q1": {"z": {"opx_output": "#/x", "extra": "#/ports/a/con1/5/7"}}}}}
        new = {"wiring": {"qubits": {"q1": {"z": {"opx_output": "#/y"}}}}}
        assert graft_wiring_values(old, new) == []


def _port(fem, port, **kw):
    return {"__class__": "quam.components.ports.Port", "controller_id": "con1", "fem_id": fem,
            "port_id": port, **kw}


class TestPortsKeptForTheLab:
    def test_a_port_only_a_lab_top_level_object_uses_is_carried(self):
        old = {"ports": {"digital_outputs": {"con1": {"1": {"1": _port(1, 1)}}},
                         "analog_outputs": {"con1": {"1": {"1": _port(1, 1)}}}},
               "dc_bias": {"__class__": "lab.dc.DCBias",
                           "trig": {"opx_output": "#/ports/digital_outputs/con1/1/1"}}}
        new = {"ports": {"analog_outputs": {"con1": {"1": {"1": _port(1, 1)}}}}, "dc_bias": None}
        assert _carryable_ports(old, new) == {"ports.digital_outputs.con1.1.1"}

    def test_a_stock_object_still_disqualifies_the_port(self):
        old = {"ports": {"digital_outputs": {"con1": {"1": {"1": _port(1, 1)}}},
                         "analog_outputs": {"con1": {"1": {"1": _port(1, 1)}}}},
               "dc_bias": {"__class__": "quam.components.Thing",
                           "trig": {"opx_output": "#/ports/digital_outputs/con1/1/1"}}}
        new = {"ports": {"analog_outputs": {"con1": {"1": {"1": _port(1, 1)}}}}, "dc_bias": None}
        assert _carryable_ports(old, new) == set()

    def test_a_spare_output_on_a_declared_fem_is_carried(self):
        old = {"ports": {"analog_outputs": {"con1": {"5": {"1": _port(5, 1)},
                                                     "7": {"1": _port(7, 1, delay=21)}}}}}
        new = {"ports": {"analog_outputs": {"con1": {"5": {"1": _port(5, 1)}}}}}
        assert _carryable_ports(old, new) == set()          # no declaration: as before
        assert _carryable_ports(old, new, declared_fems={("con1", "7"): "lf"}) == {
            "ports.analog_outputs.con1.7.1"}
        # an analog port is never put on an MW FEM
        assert _carryable_ports(old, new, declared_fems={("con1", "7"): "mw"}) == set()
