"""core/pulse_refs: a pulse field's reference read the way quam reads it.

quam resolves a reference on the OBJECT: a key the target does not store is
its class's default, and a name that is no field can be a runtime property
the class computes. These pin each answer the reader gives, on a synthetic
chip (no env). v1.3.0, findings PU-01..08.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core import pulse_catalog, pulse_refs
from quam_state_manager.core.pulse_refs import LAB_REF_KEY, read

CB = "quam.components.pulses._CosineBipolarPulse"
SQ = "quam.components.pulses.SquarePulse"
LAB = "labpkg.flux.NZPulse"
SLOT = "qubit_pairs.p.macros.cz.flux_pulse_qubit"


def _chip():
    return {
        "qubits": {"q1": {"z": {"operations": {
            "mirror": {"__class__": LAB, "length": "#./inferred_length",
                       "amplitude": f"#/{SLOT.replace('.', '/')}/amplitude",
                       "unipolar": f"#/{SLOT.replace('.', '/')}/unipolar",
                       "tail_length": f"#/{SLOT.replace('.', '/')}/tail_length"},
            "cb": {"__class__": CB, "length": "#./inferred_total_length",
                   "amplitude": 0.25, "flat_length": 40,
                   "smoothing_length": "#/qubit_pairs/p/macros/cb/flux_pulse_qubit/length"},
            "sq": {"__class__": SQ, "amplitude": 0.1,
                   "length": "#/qubit_pairs/p/macros/cb/flux_pulse_qubit/length",
                   "axis_angle": "#/qubit_pairs/p/macros/cb/flux_pulse_qubit/axis_angle"},
            "dangling": {"__class__": SQ, "amplitude": 0.1,
                         "length": "#/qubits/q9/z/operations/x/length"},
            "deep": {"__class__": SQ, "amplitude": "#../../../../q2/amp", "length": 8},
        }}}, "q2": {"amp": 0.33}},
        "qubit_pairs": {"p": {"macros": {
            "cz": {"flux_pulse_qubit": {
                "__class__": LAB, "length": "#./inferred_length", "amplitude": 0.2,
                "tail_length": "#../flux_pulse_target/lobe3_length"},
                "flux_pulse_target": {"__class__": LAB, "length": "#./inferred_length",
                                      "amplitude": -0.1}},
            "cb": {"flux_pulse_qubit": {
                "__class__": CB, "length": "#./inferred_total_length",
                "amplitude": 0.2, "flat_length": 40, "smoothing_length": 6,
                "post_zero_padding_length": 0}},
        }}},
    }


@pytest.fixture(autouse=True)
def _no_probe():
    pulse_catalog.apply_chip_classes(None)
    pulse_catalog.apply_env_overlay(None)
    yield
    pulse_catalog.apply_chip_classes(None)
    pulse_catalog.apply_env_overlay(None)


def _op(name):
    return ["qubits", "q1", "z", "operations", name]


def test_a_stored_chain_is_followed_to_its_literal():
    r = read(_chip(), _op("mirror"), _chip()["qubits"]["q1"]["z"]["operations"]["mirror"]["amplitude"])
    assert (r.ok, r.how, r.value, r.target) == (True, "stored", 0.2, SLOT + ".amplitude")


def test_another_pulses_runtime_length_is_its_computed_value():
    # PU-03/PU-07: smoothing_length -> the slot's length -> #./inferred_total_length
    m = _chip()
    r = read(m, _op("cb"), m["qubits"]["q1"]["z"]["operations"]["cb"]["smoothing_length"])
    assert r.ok and r.how == "computed"
    assert r.value == 48                 # ceil((40 + 6 + 0) / 4) * 4
    assert r.target.endswith("cb.flux_pulse_qubit.inferred_total_length")
    # the whole slot is a read: a write to any of its fields moves the value
    assert "qubit_pairs.p.macros.cb.flux_pulse_qubit" in r.reads


def test_a_key_the_target_does_not_store_is_the_catalog_default():
    m = _chip()
    r = read(m, _op("sq"), m["qubits"]["q1"]["z"]["operations"]["sq"]["axis_angle"])
    assert (r.ok, r.how, r.value) == (True, "default", None)
    assert r.target == "qubit_pairs.p.macros.cb.flux_pulse_qubit.axis_angle"


def test_the_envs_own_default_decides_for_a_lab_class():
    m = _chip()
    pulse_catalog.apply_chip_classes({LAB: {
        "importable": True, "canonical": LAB, "bases": ["quam.components.pulses.Pulse"],
        "fields": {"unipolar": {"has_default": True, "default": False, "default_repr": None},
                   "odd": {"has_default": True, "default": None, "default_repr": "<object>"}}}})
    r = read(m, _op("mirror"), m["qubits"]["q1"]["z"]["operations"]["mirror"]["unipolar"])
    assert (r.ok, r.how, r.value) == (True, "default", False)
    # a default the probe could not serialise is UNKNOWN, never a guessed None
    assert pulse_refs.class_default(LAB, "odd") == (False, None)


def test_a_lab_property_is_a_question_for_the_targets_own_class():
    # PU-01: tail_length -> the target slot's lobe3_length, a property only
    # the lab's class computes -- never the raw string
    m = _chip()
    slot = m["qubit_pairs"]["p"]["macros"]["cz"]["flux_pulse_qubit"]
    r = read(m, SLOT.split("."), slot["tail_length"], lab=True)
    assert not r.ok and r.how == "lab"
    assert r.lab["qclass"] == LAB and r.lab["attr"] == "lobe3_length"
    assert r.lab["params"]["amplitude"] == -0.1
    assert r.lab["params"]["length"] == "#./inferred_length"   # its own, for it
    params, err = pulse_refs.lab_params(m, SLOT.split("."), slot)
    assert err is None
    assert params["tail_length"] == {LAB_REF_KEY: r.lab}
    assert params["length"] == "#./inferred_length"


def test_the_pulses_own_runtime_property_stays_its_own():
    m = _chip()
    r = read(m, _op("cb"), "#./inferred_total_length")
    assert not r.ok and r.how == "self"


def test_a_missing_object_says_so():
    m = _chip()
    r = read(m, _op("dangling"), m["qubits"]["q1"]["z"]["operations"]["dangling"]["length"])
    assert not r.ok and r.how == "unknown"
    assert "qubits.q9" in r.reason and "does not exist" in r.reason
    params, err = pulse_refs.lab_params(m, _op("dangling"),
                                        m["qubits"]["q1"]["z"]["operations"]["dangling"])
    assert err and err.startswith("length:")


def test_a_multi_level_reference_is_refused_with_the_reason():
    """One ``../`` step, as SM's pointer index follows (docs/248; no real
    chip writes ``#../../``): the reader and the index that refreshes rows on
    a write must never disagree, so the form is named, not half-followed."""
    m = _chip()
    r = read(m, _op("deep"), "#../../../../q2/amp")
    assert not r.ok and r.how == "unknown"
    assert "one ../ step" in r.reason
    r = read(m, _op("deep"), "#../sq/amplitude")
    assert (r.ok, r.value) == (True, 0.1)


def test_a_loop_is_named_not_followed_forever():
    m = {"a": {"x": "#../b/y"}, "b": {"y": "#../a/x"}}
    r = read(m, ["a"], "#../b/y")
    assert not r.ok and "loop" in r.reason


def test_a_value_is_never_the_raw_reference():
    m = _chip()
    for name, body in m["qubits"]["q1"]["z"]["operations"].items():
        for k, v in body.items():
            r = read(m, _op(name), v, lab=True)
            assert not pulse_refs.is_ref(r.value), (name, k, r)


def test_a_write_in_a_pulse_reaches_every_row_that_reads_it_whole(tmp_path, monkeypatch):
    """The Pulses index updates rows INCREMENTALLY on a value write. A row
    whose length is another pulse's COMPUTED length reads that pulse whole:
    a write to the slot's flat_length must move it too (served == cold)."""
    import json as _json

    from quam_state_manager.core import pulse_index as pi
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.core.modifier import Modifier
    monkeypatch.delenv("SM_RAM_VERIFY", raising=False)
    (tmp_path / "state.json").write_text(_json.dumps(_chip()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(_json.dumps({"network": {}}), encoding="utf-8")
    store = QuamStore(str(tmp_path))
    mod = Modifier(store)
    idx = pi.PulseIndex(store)
    row = "qubits.q1.z.operations.cb"
    assert idx.row(row)["length"] == 88            # ceil((40 + 48 + 0) / 4) * 4
    mod.set_value("qubit_pairs.p.macros.cb.flux_pulse_qubit.flat_length", 44)
    assert idx.stats["incremental"] >= 0
    assert idx.row(row)["length"] == 92            # smoothing -> 52
    assert idx.rows() == pi.list_pulses(store.merged)
    assert idx.stats["incremental"] >= 1           # the incremental road ran
