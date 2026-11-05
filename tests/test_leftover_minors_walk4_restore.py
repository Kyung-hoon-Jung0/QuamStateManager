import hashlib
import threading
import pytest
from quam_state_manager.core import safe_io, working_copy, type_policy, hub
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.saver import Saver
from quam_state_manager.core.autofit.writer import ChipHandle, RealWriter


@pytest.mark.parametrize("node_value", [7153065262.0, 7153065300.0])
def test_review_restore_preserves_json_type_and_bytes(tmp_path, node_value):
    live = tmp_path / "live"
    live.mkdir()
    original = 7153065262
    safe_io.write_state_wiring(live, {"qubits": {"q1": {"f_01": original}}}, {})
    digest = hashlib.md5((live / "state.json").read_bytes()).hexdigest()
    wc = working_copy.create(tmp_path / "instance", live)
    store = QuamStore(wc.working_folder)
    mod = Modifier(store)
    handle = ChipHandle(store=store, modifier=mod, saver=Saver(store), wc=wc,
                        build_lock=threading.RLock(), live_path=str(live), reconcile=lambda: None)
    mod.set_value("qubits.q1.f_01", node_value, coerce=False)
    handle.saver.save()
    working_copy.apply_to_live(wc, record=hub.unrecorded("synthetic node save"))
    store.type_policy = type_policy.TypePolicy({}, {"qubits.q1.f_01": {"type": "real"}})
    result = RealWriter(handle).restore_values([
        {"path": "qubits.q1.f_01", "value": original, "expect": node_value}], label="plan-end restore")
    assert result["ok"], result
    assert type(safe_io.read_state_wiring(live)[0]["qubits"]["q1"]["f_01"]) is int
    assert hashlib.md5((live / "state.json").read_bytes()).hexdigest() == digest



def test_review_tracks_a_type_only_node_write():
    from types import SimpleNamespace
    from quam_state_manager.core.autofit.engine import PlanEngine
    recorded = {}
    engine = SimpleNamespace(writer=object(), _leaves=lambda: {"qubits.q1.f_01": 100.0},
        _record_preplan=lambda p, v: recorded.update({p: v}), _note_written=lambda *a: None)
    delta = PlanEngine._step_delta(engine, {"qubits.q1.f_01": 100}, ["q1"])
    assert len(delta) == 1
    assert type(recorded["qubits.q1.f_01"]) is int


def test_review_restore_keeps_a_type_only_edit_after_the_plan(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    safe_io.write_state_wiring(live, {"qubits": {"q1": {"f_01": 5}}}, {})
    wc = working_copy.create(tmp_path / "instance", live)
    store = QuamStore(wc.working_folder)
    mod = Modifier(store)
    handle = ChipHandle(store=store, modifier=mod, saver=Saver(store), wc=wc,
                        build_lock=threading.RLock(), live_path=str(live), reconcile=lambda: None)
    # The plan wrote int 5; a subsequent human edit retyped it as float 5.0.
    mod.set_value("qubits.q1.f_01", 5.0, coerce=False)
    handle.saver.save()
    working_copy.apply_to_live(wc, record=hub.unrecorded("synthetic human edit"))
    before = (live / "state.json").read_bytes()
    result = RealWriter(handle).restore_values([
        {"path": "qubits.q1.f_01", "value": 4, "expect": 5}], label="plan-end restore")
    assert result["ok"], result
    assert result["conflicts"] == [{"path": "qubits.q1.f_01",
        "reason": "changed after the plan wrote it (current=5.0)"}]
    assert type(store.state["qubits"]["q1"]["f_01"]) is float
    assert (live / "state.json").read_bytes() == before
