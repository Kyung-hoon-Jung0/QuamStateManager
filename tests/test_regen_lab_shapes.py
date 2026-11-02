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


def test_crosstalk_row_in_custom_object_follows_port_swap():
    import copy

    old = {"ports": {"analog_outputs": {"con1": {"5": {
        "1": _port(5, 1, crosstalk="#/correction/row"),
        "3": _port(5, 3, crosstalk={"1": .31})}}}},
        "qubits": {"q1": {"z": {"opx_output": "#/ports/analog_outputs/con1/5/1"}},
                   "q2": {"z": {"opx_output": "#/ports/analog_outputs/con1/5/3"}}},
        "correction": {"__class__": "custom.Correction", "row": {"3": .13, "1": .91}}}
    new = copy.deepcopy(old)
    new["qubits"]["q1"]["z"]["opx_output"] = "#/ports/analog_outputs/con1/5/3"
    new["qubits"]["q2"]["z"]["opx_output"] = "#/ports/analog_outputs/con1/5/1"
    result = merge_states(old, new)
    ports = result.merged["ports"]["analog_outputs"]["con1"]["5"]
    assert ports["3"]["crosstalk"] == {"1": .13, "3": .91}
    assert ports["1"]["crosstalk"] == {"3": .31}
    assert result.merged["correction"] == old["correction"]


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


    def test_a_link_the_source_agreed_with_follows_a_populate_change(self):
        """Review: a readout downconverter equal to the upconverter it shares an LO with.
        The user moved the LO in Populate; the builder's link must carry the new LO to
        the input too, never the stale source literal."""
        def st(down):
            return {"ports": {
                "mw_outputs": {"con1": {"1": {"1": {"upconverter_frequency": 7.0e9}}}},
                "mw_inputs": {"con1": {"1": {"1": {"downconverter_frequency": down}}}}}}
        new = st("#/ports/mw_outputs/con1/1/1/upconverter_frequency")
        new["ports"]["mw_outputs"]["con1"]["1"]["1"]["upconverter_frequency"] = 7.2e9
        r = merge_states(st(7.0e9), new,
                         protect_paths={"ports.mw_outputs.con1.1.1.upconverter_frequency"})
        inp = r.merged["ports"]["mw_inputs"]["con1"]["1"]["1"]
        assert inp["downconverter_frequency"] == "#/ports/mw_outputs/con1/1/1/upconverter_frequency"
        assert r.merged["ports"]["mw_outputs"]["con1"]["1"]["1"]["upconverter_frequency"] == 7.2e9
        assert r.stats.links_not_taken == []

    def test_restoring_many_forms_stays_fast_when_one_fails(self):
        import time
        ops = {f"op{i}": {"a": 1.0, "b": "#./a"} for i in range(300)}
        old = {"qubits": {"q": {"ops": {k: {"a": 1.0, "b": 1.0} for k in ops}}}}
        old["qubits"]["q"]["ops"]["op0"]["b"] = "#./zz"          # cannot land: refused
        t = time.perf_counter()
        r = merge_states(old, {"qubits": {"q": {"ops": ops}}})
        assert time.perf_counter() - t < 5
        assert len(r.stats.source_forms_restored) == 299


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


class TestARemovedQubitTakesOnlyItsOwnEntry:
    """Review #7: removing a qubit in the wizard dropped a lab's whole QDAC block,
    because one channel pointed at the removed qubit's wiring."""

    def _states(self):
        old = {"qubits": {"q1": {}, "q2": {}}, "dc_bias": {
            "__class__": "lab.dc.DCBias",
            "channels": {"q1": {"channel": "#/wiring/qubits/q1/z/dc_channel", "phi0_v": 4.9},
                         "q2": {"channel": "#/wiring/qubits/q2/z/dc_channel", "phi0_v": 5.1}}}}
        new = {"qubits": {"q1": {}}, "dc_bias": None}
        new_wiring = {"wiring": {"qubits": {"q1": {"z": {"dc_channel": 1}}}}}
        keep = {"lab.dc.DCBias": {"bases": [], "fields": ["channels"]}}
        return old, new, new_wiring, keep

    def test_the_other_qubits_keep_their_entries(self):
        old, new, nw, keep = self._states()
        r = merge_states(old, new, new_wiring=nw, keep_classes=keep, class_schemas={})
        db = r.merged["dc_bias"]
        assert db is not None and list(db["channels"]) == ["q1"]
        assert db["channels"]["q1"]["phi0_v"] == 4.9
        assert "dc_bias.channels.q2.phi0_v" in r.stats.residual_lost

    def test_a_miss_outside_a_removed_entry_still_drops_the_object(self):
        old, new, nw, keep = self._states()
        old["dc_bias"]["channels"]["q1"]["channel"] = "#/wiring/qubits/q1/z/nowhere"
        r = merge_states(old, new, new_wiring=nw, keep_classes=keep, class_schemas={})
        assert r.merged["dc_bias"] is None
