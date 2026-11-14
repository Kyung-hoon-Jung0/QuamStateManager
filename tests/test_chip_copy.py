"""Throwaway chip copies: before / after folders, the sequence value, no side effects."""
import hashlib
import json
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import chip_copy, gate_check
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier


def _chip(tmp_path: Path) -> Path:
    folder = tmp_path / "chip"
    folder.mkdir()
    state = {"__class__": "pkg.Root",
             "qubits": {"q1": {"z": {"operations": {"const": {"length": 16, "amplitude": 0.1}}}},
                        "q2": {"z": {"operations": {}}}},
             "qubit_pairs": {"q1-2": {"macros": {}}}}
    wiring = {"wiring": {"qubits": {"q1": {"z": {"opx_output": "#/ports/a"}}}},
              "network": {"host": "localhost"}}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return folder


def _digest(folder: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


CREATES = [("qubit_pairs.q1-2.macros.cz_new", {"flux_pulse_qubit": "#/qubits/q1/z/operations/p"}),
           ("qubits.q1.z.operations.p", {"length": 20, "amplitude": 0.2}),
           ("wiring.qubits.q2", {"z": {"opx_output": "#/ports/b"}})]


def test_before_has_unsaved_edit_after_has_edit_and_creates(tmp_path):
    folder = _chip(tmp_path)
    store = QuamStore(folder)
    Modifier(store).set_value("qubits.q1.z.operations.const.amplitude", 0.3)
    log_before = list(store.change_log)
    seq0 = store.mutation_seq
    files0 = _digest(folder)
    mem0 = json.dumps([store.state, store.wiring], sort_keys=True)
    copies = chip_copy.make_chip_copies(store, CREATES)
    try:
        assert copies.seq == seq0
        b_state = json.loads((copies.before / "state.json").read_text(encoding="utf-8"))
        b_wiring = json.loads((copies.before / "wiring.json").read_text(encoding="utf-8"))
        a_state = json.loads((copies.after / "state.json").read_text(encoding="utf-8"))
        a_wiring = json.loads((copies.after / "wiring.json").read_text(encoding="utf-8"))
        assert b_state["qubits"]["q1"]["z"]["operations"]["const"]["amplitude"] == 0.3
        assert "cz_new" not in b_state["qubit_pairs"]["q1-2"]["macros"]
        assert a_state["qubits"]["q1"]["z"]["operations"]["const"]["amplitude"] == 0.3
        assert a_state["qubit_pairs"]["q1-2"]["macros"]["cz_new"] == CREATES[0][1]
        assert a_state["qubits"]["q1"]["z"]["operations"]["p"] == CREATES[1][1]
        # the wiring create lands in wiring.json, by the store's own split
        assert a_wiring["wiring"]["qubits"]["q2"] == CREATES[2][1]
        assert "wiring" not in a_state and "q2" not in b_wiring["wiring"]["qubits"]
        assert sorted(p.name for p in copies.before.iterdir()) == ["state.json", "wiring.json"]
    finally:
        chip_copy.remove_chip_copies(copies)
    assert not copies.root.exists()
    assert store.change_log == log_before and store.mutation_seq == seq0
    assert json.dumps([store.state, store.wiring], sort_keys=True) == mem0
    assert _digest(folder) == files0


def test_failed_write_leaves_no_folder(tmp_path, monkeypatch):
    store = QuamStore(_chip(tmp_path))
    made = []
    real = chip_copy.tempfile.mkdtemp

    def spy(**kw):
        made.append(Path(real(**kw)))
        return str(made[-1])
    calls = {"n": 0}
    real_write = chip_copy.write_chip_folder

    def failing(folder, state, wiring):
        calls["n"] += 1
        if calls["n"] == 2:
            (Path(folder) / "state.json").write_text("{", encoding="utf-8")
            raise OSError("disk full")
        real_write(folder, state, wiring)
    monkeypatch.setattr(chip_copy.tempfile, "mkdtemp", spy)
    monkeypatch.setattr(chip_copy, "write_chip_folder", failing)
    with pytest.raises(OSError):
        chip_copy.make_chip_copies(store, CREATES)
    assert made and not made[0].exists()


def test_store_at_seq_false_after_change(tmp_path):
    store = QuamStore(_chip(tmp_path))
    copies = chip_copy.make_chip_copies(store, [])
    try:
        assert chip_copy.store_at_seq(store, copies.seq)
        Modifier(store).set_value("qubits.q1.z.operations.const.length", 24)
        assert not chip_copy.store_at_seq(store, copies.seq)
    finally:
        chip_copy.remove_chip_copies(copies)


def test_lock_not_held_while_the_lab_check_runs(tmp_path):
    """While the runner works another thread takes the store lock at once,
    and the runner sees temp folders, never the chip folder."""
    folder = _chip(tmp_path)
    store = QuamStore(folder)
    seen = {}

    def runner(py, before, after, macros, **kw):
        got = []

        def other():
            ok = store._lock.acquire(timeout=2)
            got.append(ok)
            if ok:
                store._lock.release()
        t = threading.Thread(target=other)
        t.start()
        t.join(5)
        seen.update(lock_free=bool(got and got[0]), before=before, after=after,
                    exist=Path(before).is_dir() and Path(after).is_dir())
        return {"status": "crash", "error": "stop here", "data": None}
    res = gate_check.check_gates(store, "python.exe", [("q1-2", "cz_new")], CREATES,
                                 runner=runner)
    assert seen["lock_free"] and seen["exist"]
    for p in (seen["before"], seen["after"]):
        assert Path(folder).resolve() not in Path(p).resolve().parents
        assert not Path(p).exists(), "cleaned after the check"
    assert res["status"] == "crash" and res["seq"] == store.mutation_seq


def _after_docs(copies):
    return (json.loads((copies.after / "state.json").read_text(encoding="utf-8")),
            json.loads((copies.after / "wiring.json").read_text(encoding="utf-8")))


def test_after_equals_a_real_write_through_the_modifier(tmp_path):
    store = QuamStore(_chip(tmp_path))
    Modifier(store).set_value("qubits.q1.z.operations.const.amplitude", 0.3)
    copies = chip_copy.make_chip_copies(store, CREATES)
    try:
        a_state, a_wiring = _after_docs(copies)
    finally:
        chip_copy.remove_chip_copies(copies)
    (tmp_path / "scratch").mkdir()
    scratch = QuamStore(_chip(tmp_path / "scratch"))
    mod = Modifier(scratch)
    mod.set_value("qubits.q1.z.operations.const.amplitude", 0.3)
    for path, value in CREATES:
        mod.create_subtree(path, value)
    assert scratch.state == a_state and scratch.wiring == a_wiring


@pytest.mark.parametrize("path", [
    "qubits.q1.z.operations.const.length.extra",      # scalar parent
    "qubits.q9.z.operations.p",                       # missing parent
    "qubits.q1.z.operations.const",                   # existing target
    "qubit_pairs.q1-2.macros.0",                      # list-index path
])
def test_refused_create_raises_named_and_makes_no_folder(tmp_path, monkeypatch, path):
    store = QuamStore(_chip(tmp_path))
    store.state["qubit_pairs"]["q1-2"]["macros"] = ["kept"]
    store._merge()
    made = []
    real = chip_copy.tempfile.mkdtemp
    monkeypatch.setattr(chip_copy.tempfile, "mkdtemp",
                        lambda **kw: made.append(real(**kw)) or made[-1])
    with pytest.raises(chip_copy.CreateError) as info:
        chip_copy.make_chip_copies(store, [(path, {"x": 1})])
    assert info.value.path == path and path in str(info.value)
    assert made == []
    assert store.state["qubit_pairs"]["q1-2"]["macros"] == ["kept"]


def test_base_exception_between_the_two_writes_leaves_no_folder(tmp_path, monkeypatch):
    store = QuamStore(_chip(tmp_path))
    made = []
    real = chip_copy.tempfile.mkdtemp
    monkeypatch.setattr(chip_copy.tempfile, "mkdtemp",
                        lambda **kw: made.append(Path(real(**kw))) or str(made[-1]))
    real_write = chip_copy.write_chip_folder
    calls = []

    class Stop(BaseException):
        pass

    def write(folder, s, w):
        calls.append(folder)
        if len(calls) == 2:
            raise Stop()
        real_write(folder, s, w)
    monkeypatch.setattr(chip_copy, "write_chip_folder", write)
    with pytest.raises(Stop):
        chip_copy.make_chip_copies(store, [])
    assert made and not made[0].exists()


def test_nan_and_infinity_written_like_the_chip_writer(tmp_path):
    """Compared with ``safe_io.atomic_write_json`` (the writer of the chip's
    own files): both emit the JSON extensions NaN / Infinity, read back alike."""
    from quam_state_manager.core import safe_io
    doc = {"a": float("nan"), "b": float("inf"), "c": -float("inf"), "d": 1.5}
    chip_copy.write_chip_folder(tmp_path, doc, {})
    safe_io.atomic_write_json(tmp_path / "ref.json", doc)
    mine = (tmp_path / "state.json").read_text(encoding="utf-8")
    ref = (tmp_path / "ref.json").read_text(encoding="utf-8")
    for token in ("NaN", "Infinity", "-Infinity"):
        assert token in mine and token in ref
    assert repr(json.loads(mine)) == repr(json.loads(ref))
