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
    # a Re-generate with no source to compare: nothing says the delay was there before
    s = output_timing.summary(state, wiring, tof_moved=[])
    assert s["tof"] == [] and s["tof_check"] == ["q1", "q2"] and "tof_unchanged" not in s
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


def test_regenerate_compares_the_readout_delay_with_the_source_chip():
    import copy
    state, wiring = _chip(xy_bands=(2, 2), ro_band=3)
    state["ports"]["mw_outputs"]["con1"]["1"]["8"]["delay"] = 20
    src = copy.deepcopy((state, wiring))
    s = output_timing.summary(state, wiring, tof_moved=[], source=src)
    assert s["tof_unchanged"] == ["q1", "q2"] and "tof_check" not in s
    src[0]["ports"]["mw_outputs"]["con1"]["1"]["8"]["delay"] = 0
    del src[0]["qubits"]["q2"]
    s = output_timing.summary(state, wiring, tof_moved=["q1"], source=src)
    assert [t["qubit"] for t in s["tof"]] == ["q1"] and s["tof_check"] == ["q2"]
    assert "tof_unchanged" not in s


def test_an_output_used_only_from_a_list_names_its_element():
    state, wiring = _chip()
    state["ports"]["analog_outputs"]["con1"]["3"]["5"] = {
        "controller_id": "con1", "fem_id": 3, "port_id": 5, "delay": 0}
    state["extras"] = {"pump": {"lines": [{"opx_output": "#/ports/analog_outputs/con1/3/5"}]}}
    s = output_timing.summary(state, wiring)
    row = [f for f in s["flux"] if f["port"] == "ports/analog_outputs/con1/3/5"]
    assert row and row[0]["of"] == "pump/lines/0" and row[0]["kind"] == "lf", s["flux"]


def test_a_source_delay_stored_as_text_is_the_same_delay():
    import copy
    state, wiring = _chip(xy_bands=(2, 2), ro_band=3)
    state["ports"]["mw_outputs"]["con1"]["1"]["8"]["delay"] = 20
    src = copy.deepcopy((state, wiring))
    src[0]["ports"]["mw_outputs"]["con1"]["1"]["8"]["delay"] = "20"
    s = output_timing.summary(state, wiring, tof_moved=[], source=src)
    assert s["tof_unchanged"] == ["q1", "q2"] and "tof_check" not in s


def test_a_readout_delay_moved_back_to_zero_is_listed_and_unchanged_zero_is_not():
    import copy
    state, wiring = _chip(xy_bands=(2, 2), ro_band=3)
    src = copy.deepcopy((state, wiring))
    s = output_timing.summary(state, wiring, tof_moved=[], source=src)
    assert s["tof"] == [] and "tof_check" not in s and "tof_unchanged" not in s
    src[0]["ports"]["mw_outputs"]["con1"]["1"]["8"]["delay"] = 20
    s = output_timing.summary(state, wiring, tof_moved=["q1"], source=src)
    assert [(t["qubit"], t["shift"]) for t in s["tof"]] == [("q1", 0)]
    assert s["tof_check"] == ["q2"]
    assert output_timing.summary(state, wiring)["tof"] == []      # fresh build, delay 0


def test_a_qubit_new_on_the_rebuilt_chip_with_no_delay_is_not_listed():
    import copy
    state, wiring = _chip(xy_bands=(2, 2), ro_band=3)
    src = copy.deepcopy((state, wiring))
    del src[0]["qubits"]["q2"]
    s = output_timing.summary(state, wiring, tof_moved=[], source=src)
    assert s["tof"] == [] and "tof_check" not in s, s


def test_a_moved_readout_whose_delay_stays_zero_is_listed():
    import copy
    state, wiring = _chip(xy_bands=(2, 2), ro_band=3)
    src = copy.deepcopy((state, wiring))
    s = output_timing.summary(state, wiring, tof_moved=["q1"], source=src)
    assert [(t["qubit"], t["moved"]) for t in s["tof"]] == [("q1", True)], s


def test_a_moved_time_of_flight_carries_its_old_value_from_a_mapping():
    import copy
    state, wiring = _chip(xy_bands=(2, 2), ro_band=3)
    src = copy.deepcopy((state, wiring))
    src[0]["ports"]["mw_outputs"]["con1"]["1"]["8"]["delay"] = 20
    state["ports"]["mw_outputs"]["con1"]["1"]["8"]["delay"] = 40
    s = output_timing.summary(state, wiring, tof_moved={"q1": 12, "q2": True}, source=src)
    by = {t["qubit"]: t for t in s["tof"]}
    assert by["q1"]["from"] == 12 and by["q1"]["shift"] == 40
    assert "from" not in by["q2"]                       # bool is not a number
    s = output_timing.summary(state, wiring, tof_moved={"q1": "12", "q2": None}, source=src)
    assert all("from" not in t for t in s["tof"]) and len(s["tof"]) == 2
    s = output_timing.summary(state, wiring, tof_moved=["q1", "q2"], source=src)
    assert all("from" not in t for t in s["tof"]) and len(s["tof"]) == 2
