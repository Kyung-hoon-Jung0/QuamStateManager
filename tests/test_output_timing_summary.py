"""The build result's output timing table is read from the built chip."""

from quam_state_manager.core import output_timing
from tests.test_regen_mw_band_shift import _chip, _mw


def test_shared_feedline_lists_the_port_once_and_no_shift():
    state, wiring = _chip(xy_bands=(1, 1), ro_band=3)
    s = output_timing.summary(state, wiring)
    assert [m["port"] for m in s["mw"]] == [
        "ports/mw_outputs/con1/1/1", "ports/mw_outputs/con1/1/2", "ports/mw_outputs/con1/1/8"]
    assert s["tof"] == []


def test_a_delayed_readout_lists_each_resonator_and_a_typed_one_as_typed():
    state, wiring = _chip(xy_bands=(2, 2), ro_band=3)
    state["ports"]["mw_outputs"]["con1"]["1"]["8"]["delay"] = 20
    s = output_timing.summary(state, wiring, tof_typed={"q2"})
    assert s["tof"] == [{"qubit": "q1", "shift": 20, "value": 32, "moved": True},
                        {"qubit": "q2", "shift": 20, "value": 32, "moved": False}]
    s = output_timing.summary(state, wiring, tof_moved=[])
    assert s["tof"] == [] and s["tof_unchanged"] == ["q1", "q2"]
    s = output_timing.summary(state, wiring, tof_unrecorded={"q1", "q2"})
    assert s["tof"] == [] and s["tof_unrecorded"] == ["q1", "q2"]


def test_flux_ports_and_unwired_ports():
    state, wiring = _chip()
    state["ports"]["mw_outputs"]["con1"]["1"]["7"] = _mw(7, 2)      # no element uses it
    s = output_timing.summary(state, wiring)
    assert "ports/mw_outputs/con1/1/7" not in {m["port"] for m in s["mw"]}
    assert [(f["of"], f["delay"]) for f in s["flux"]] == [("q1", 61), ("q2", 63), ("q1-2", 61)]
    assert output_timing.summary({}, {}) is None


def test_typed_tof_reads_the_populate_step():
    spec = {"populate": {"resonator": {"q1": {"time_of_flight": 300}, "q2": {"RF_freq": 7e9}}}}
    # no record of the prefill: the value's source is unknown
    assert output_timing.tof_provenance(spec) == (set(), {"q1"})
    spec["populate"]["options"] = {"tof_defaults": {}}
    assert output_timing.typed_tof(spec) == {"q1"}
    assert output_timing.tof_provenance(None) == (set(), set())


def test_typed_tof_treats_a_prefilled_default_as_a_default():
    rows = {"q1": {"time_of_flight": 28}, "q2": {"time_of_flight": 300},
            "q3": {"time_of_flight": 28}}
    spec = {"populate": {"options": {"tof_defaults": {"q1": 28, "q2": 28}}, "resonator": rows}}
    # q1 still holds its default; q2 was changed; q3 has no record (typed or preset)
    assert output_timing.tof_provenance(spec) == ({"q2", "q3"}, set())
    # no record at all (older draft, emitted script, agent API): every value is unrecorded
    assert output_timing.tof_provenance({"populate": {"resonator": rows}}) == (set(), {"q1", "q2", "q3"})


def test_typed_tof_is_the_build_rule():
    from tests.test_run_build_delay import _load_helpers
    mod = _load_helpers()
    for spec in ({"populate": {"options": {"tof_defaults": {"q1": 28}},
                               "resonator": {"q1": {"time_of_flight": 28},
                                             "q2": {"time_of_flight": 28}}}},
                 {"populate": {"resonator": {"q1": {"time_of_flight": 5}}}}, None, {}):
        typed, unrec = output_timing.tof_provenance(spec)
        assert (typed, unrec) == mod._tof_provenance(spec)
        assert typed | unrec == mod._user_set_tof(spec)
    assert output_timing.summary(*_chip())["note"] == output_timing.LATENCY_NOTE
