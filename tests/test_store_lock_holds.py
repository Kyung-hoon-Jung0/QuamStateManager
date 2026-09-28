"""w8/locks: no request waits on the store lock for a whole cold build or a save.

Measured on big30x (the w7 final verification, lockwatch2): the first Live-Edit
grid build held ``store._lock`` 3.2-3.8 s per grid, the cold PulseIndex build
(chip open, structural pull) 1.3-2.2 s, and ``Saver.save`` 0.26-0.7 s -- every
other request (a drift poll, a search, an edit) queued behind them. Now, with
the ``activity`` family the lint already uses:

* a grid build and a cold PulseIndex build run through
  ``activity.single_flight``: the builder holds the lock but hands it to another
  request at its checkpoints, and a build the chip moved under starts again,
  so what is stored is always the cold result of the content it names;
* the chip-open decision (``_chip_needs_generated_config``) builds on a daemon
  thread that PARKS while a request is in flight, into the context's own
  index, which a request needing the rows takes over;
* ``Saver.save`` holds the lock for a marshal snapshot and for the swap; the
  ``.bak`` copies, the render and the ``.tmp`` writes run with it free, and
  the swap happens only if nothing but later EDITS moved the store (they stay
  pending, as when they waited for a single-hold save).

Facts, not timings: each pin observes the lock held by someone else while the
build is provably unfinished, or reads the bytes / rows / cells back.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import activity, pulse_index as PI, safe_io
from quam_state_manager.core import saver as SV
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from tests import _ram_chip


@pytest.fixture(autouse=True)
def _quiet_activity():
    def reset():
        activity.end()
        with activity._LOCK:
            activity._STATE.update(inflight=0, last=0.0)
        activity._init_maps()
        activity._init_flights()
    reset()
    yield
    reset()


def _store(n: int = 6, seed: int = 1) -> QuamStore:
    s, w = _ram_chip.build(n, seed)
    return QuamStore.from_dicts(s, w)


def _folder_store(tmp_path: Path, n: int = 4) -> QuamStore:
    s, w = _ram_chip.build(n, 3)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "state.json").write_text(json.dumps(s, indent=4), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(w, indent=4), encoding="utf-8")
    return QuamStore(tmp_path)


def _in_request(path: str, fn, out: dict, key: str = "r") -> threading.Thread:
    def run():
        activity.begin(path)
        try:
            out[key] = fn()
        except BaseException as exc:          # noqa: BLE001 -- surfaced by the pin
            out["error"] = exc
        finally:
            activity.end()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _grab_while(store, still_running, timeout: float = 10.0):
    """Another request takes the store lock; returns what *still_running()*
    said while it held it."""
    activity.begin("/field/edit")
    try:
        assert store._lock.acquire(timeout=timeout), "the builder never let go of the lock"
        try:
            return still_running()
        finally:
            store._lock.release()
    finally:
        activity.end()


# ------------------------------------------------------------ Live-Edit grids
@pytest.fixture
def slow_cells(monkeypatch):
    """A qubit grid whose every cell takes ~3 ms; counts the cells built."""
    from quam_state_manager.web import routes as R
    real = R._qubit_cell_for
    seen = {"n": 0}
    entered = threading.Event()

    def cell(*a, **k):
        seen["n"] += 1
        entered.set()
        time.sleep(0.003)
        return real(*a, **k)

    monkeypatch.setattr(R, "_qubit_cell_for", cell)
    monkeypatch.setattr(R, "_GRID_TICK", 4)
    return R, seen, entered


def _cells(grid: dict) -> list:
    return [(r["id"], r["cells"]) for r in grid["rows"]]


def test_a_cold_grid_build_hands_the_store_lock_to_another_request(slow_cells):
    R, seen, entered = slow_cells
    st = _store()
    ctx: dict = {}
    out: dict = {}
    t = _in_request("/bulk", lambda: R._bulk_grid_entry(st, set(), R._modified_map_of(st), ctx), out)
    assert entered.wait(10)
    at_grab = _grab_while(st, lambda: seen["n"])
    t.join(60)
    assert not t.is_alive() and "error" not in out, out.get("error")
    total = seen["n"]
    # the FACT: another request held the lock while cells were still to build
    assert at_grab < total, "the grid build held the store lock to its end"
    cold = R._qubit_bulk_grid(st, set(), R._modified_map_of(st))
    assert _cells(out["r"]["grid"]) == _cells(cold)


def test_a_grid_built_while_the_chip_moved_is_the_cold_grid_of_the_new_content(slow_cells):
    """An edit that lands in a hand-over restarts the build: every cell -- and
    the 'modified since load' marker of the edited one, which the caller's
    change-log snapshot predates -- is what a cold render of the new content
    shows, and the entry names the content it was built from."""
    R, seen, entered = slow_cells
    st = _store()
    ctx: dict = {}
    out: dict = {}
    t = _in_request("/bulk", lambda: R._bulk_grid_entry(st, set(), R._modified_map_of(st), ctx), out)
    assert entered.wait(10)

    def edit():
        Modifier(st).set_value("qubits.q1.T1", 9.75e-05)
        return seen["n"]
    _grab_while(st, edit)
    t.join(60)
    assert not t.is_alive() and "error" not in out, out.get("error")
    ent = out["r"]
    assert ent["seq"] == st.mutation_seq
    cold = R._qubit_bulk_grid(st, set(), R._modified_map_of(st))
    assert _cells(ent["grid"]) == _cells(cold)
    t1 = [c for rid, cs in _cells(ent["grid"]) if rid == "q1" for c in cs
          if c.get("resolved_path") == "qubits.q1.T1"]
    assert t1 and t1[0]["modified"] and "9.75e-05" in t1[0]["display"], t1


# ------------------------------------------------------------ PulseIndex
@pytest.fixture
def slow_rows(monkeypatch):
    """Pulse rows that take ~4 ms each; counts the rows built."""
    real = PI._row_for_pulse
    seen = {"n": 0}
    entered = threading.Event()

    def row(*a, **k):
        seen["n"] += 1
        entered.set()
        time.sleep(0.004)
        return real(*a, **k)

    monkeypatch.setattr(PI, "_row_for_pulse", row)
    monkeypatch.setattr(PI, "_TICK_ROWS", 2)
    return seen, entered


def test_a_cold_pulse_index_build_hands_the_store_lock_to_another_request(slow_rows):
    seen, entered = slow_rows
    st = _store()
    idx = PI.PulseIndex(st)
    out: dict = {}
    t = _in_request("/pulses", idx.rows, out)
    assert entered.wait(10)
    at_grab = _grab_while(st, lambda: seen["n"])
    t.join(60)
    assert not t.is_alive() and "error" not in out, out.get("error")
    assert at_grab < seen["n"], "the pulse-index build held the store lock to its end"
    assert out["r"] == PI.list_pulses(st.merged)
    assert idx.stats["cold"] == 1


def test_pulse_rows_built_while_the_chip_moved_are_the_rows_of_the_new_content(slow_rows):
    seen, entered = slow_rows
    st = _store()
    idx = PI.PulseIndex(st)
    out: dict = {}
    t = _in_request("/pulses", idx.rows, out)
    assert entered.wait(10)
    _grab_while(st, lambda: Modifier(st).delete_subtree("qubits.q3.xy.operations.saturation"))
    t.join(60)
    assert not t.is_alive() and "error" not in out, out.get("error")
    paths = [r["path"] for r in out["r"]]
    assert "qubits.q3.xy.operations.saturation" not in paths
    assert out["r"] == PI.list_pulses(st.merged)
    assert idx.stamp()[0] == st.mutation_seq


def test_the_open_decision_parks_while_a_request_runs_and_fills_the_context_index(slow_rows):
    """The chip-open probe (``_decide``) is a daemon thread: while a request is
    in flight its walk stands still and the lock is free; the rows it builds
    are the ones the Pulses page then reads (one walk, not two)."""
    from quam_state_manager.web import routes as R
    seen, entered = slow_rows
    st = _store(12)
    ctx: dict = {"store": st}
    res: dict = {}

    def decide():
        res["v"] = R._chip_needs_generated_config(st, ctx, background=True)
    bg = threading.Thread(target=decide, daemon=True)
    bg.start()
    assert entered.wait(10)                       # a quiet server: the walk runs
    activity.begin("/bulk")                       # the user's page arrives
    try:
        time.sleep(0.05)                          # ... and the walk parks at its next tick
        n0 = seen["n"]
        assert st._lock.acquire(timeout=2), "a parked build still holds the lock"
        st._lock.release()
        time.sleep(0.3)
        assert seen["n"] == n0, "the background walk kept going while a request ran"
        assert bg.is_alive()
    finally:
        activity.end()
    bg.join(60)
    assert not bg.is_alive()
    idx = ctx["pulse_index"]
    assert idx.stats["cold"] == 1
    rows = idx.rows()
    assert idx.stats["cold"] == 1, "the page walked the chip again"
    assert rows == PI.list_pulses(st.merged)
    assert res["v"] == any(not r.get("known") and not r.get("is_alias") for r in rows)


def test_a_request_needing_the_rows_takes_over_a_parked_background_build(slow_rows):
    from quam_state_manager.web import routes as R
    seen, entered = slow_rows
    st = _store(12)
    ctx: dict = {"store": st}
    bg = threading.Thread(target=lambda: R._chip_needs_generated_config(
        st, ctx, background=True), daemon=True)
    bg.start()
    assert entered.wait(10)
    activity.begin("/pulses")                     # the Pulses page, in flight
    try:
        time.sleep(0.05)                          # the background walk parks
        n0 = seen["n"]
        rows = ctx["pulse_index"].rows()          # ... and this request needs its rows
        assert seen["n"] > n0, "the parked walk never resumed for the request waiting on it"
    finally:
        activity.end()
    bg.join(60)
    assert not bg.is_alive()
    # ONE walk's rows in all: the request took the parked walk over rather
    # than starting its own beside it
    assert seen["n"] == len(rows), (seen["n"], len(rows))
    assert ctx["pulse_index"].stats["cold"] == 1, "two walks of one chip"
    assert rows == PI.list_pulses(st.merged)




# ------------------------------------------------------------ Saver
def _disk_state(folder: Path) -> dict:
    return json.loads((folder / "state.json").read_text(encoding="utf-8"))


def test_save_does_its_disk_io_and_render_with_the_store_lock_free(tmp_path, monkeypatch):
    """The .bak copies, the rotation, the render and the .tmp writes: none
    under the store lock -- another request takes it in the middle."""
    st = _folder_store(tmp_path / "chip")
    held: list = []
    other: list = []
    real_b, real_r, real_w = SV.Saver._backup, SV.Saver._rotate_backups, safe_io._write_tmp_json

    def grab():
        ok = st._lock.acquire(timeout=5)
        if ok:
            st._lock.release()
        other.append(ok)

    def backup(path, stamp):
        held.append(("backup", st._lock._is_owned()))
        return real_b(path, stamp)

    def rotate(self, path):
        held.append(("rotate", st._lock._is_owned()))
        return real_r(self, path)

    def write_tmp(path, data, **k):
        held.append(("render+tmp", st._lock._is_owned()))
        t = threading.Thread(target=grab)
        t.start()
        t.join(10)
        return real_w(path, data, **k)

    monkeypatch.setattr(SV.Saver, "_backup", staticmethod(backup))
    monkeypatch.setattr(SV.Saver, "_rotate_backups", rotate)
    monkeypatch.setattr(safe_io, "_write_tmp_json", write_tmp)
    Modifier(st).set_value("qubits.q1.T1", 3.3e-05)
    SV.Saver(st).save()
    assert [k for k, _ in held] == ["backup", "backup", "rotate", "rotate", "render+tmp", "render+tmp"]
    assert not any(h for _, h in held), held
    assert other == [True, True], "another request could not take the store lock mid-save"
    assert _disk_state(tmp_path / "chip")["qubits"]["q1"]["T1"] == 3.3e-05
    assert st.change_log == []
    assert list((tmp_path / "chip").glob("state.json.bak.*"))


def test_an_edit_landing_while_the_bytes_are_written_stays_pending(tmp_path, monkeypatch):
    """What a single-hold save did to an edit that arrived mid-save: made it
    wait, so it landed AFTER the save -- in memory and in the log, not in the
    file. The snapshot save keeps exactly that: the file is the content before
    the edit, and only the entries the save stood for are cleared."""
    st = _folder_store(tmp_path / "chip")
    real_w = safe_io._write_tmp_json
    calls = {"n": 0}

    def write_tmp(path, data, **k):
        calls["n"] += 1
        if calls["n"] == 1:          # the lock is free: an edit lands now
            Modifier(st).set_value("qubits.q2.T1", 7.7e-05)
        return real_w(path, data, **k)

    monkeypatch.setattr(safe_io, "_write_tmp_json", write_tmp)
    Modifier(st).set_value("qubits.q1.T1", 3.3e-05)
    SV.Saver(st).save()
    on_disk = _disk_state(tmp_path / "chip")
    assert on_disk["qubits"]["q1"]["T1"] == 3.3e-05
    old_q2 = QuamStore(tmp_path / "chip").state["qubits"]["q2"]["T1"]
    assert old_q2 != 7.7e-05 and on_disk["qubits"]["q2"]["T1"] == old_q2
    assert [e.dot_path for e in st.change_log] == ["qubits.q2.T1"], "the late edit is not pending"
    assert st.change_log[0].old_value == old_q2 and st.state["qubits"]["q2"]["T1"] == 7.7e-05
    assert calls["n"] == 2, "the save rendered again instead of keeping the edit pending"
    assert not list((tmp_path / "chip").glob("*.tmp")), "a stale temp file was left"


def test_an_undo_landing_while_the_bytes_are_written_is_never_written_back(tmp_path, monkeypatch):
    """An undo (or discard, or reload) is not an edit that could wait: the
    snapshot no longer describes memory, so it is dropped and taken again --
    the file then equals memory and the log is clean, never a file holding a
    value memory no longer has with nothing pending to say so."""
    st = _folder_store(tmp_path / "chip")
    m = Modifier(st)
    m.set_value("qubits.q1.T1", 3.3e-05)
    m.set_value("qubits.q2.T1", 7.7e-05)
    old_q2 = QuamStore(tmp_path / "chip").state["qubits"]["q2"]["T1"]
    real_w = safe_io._write_tmp_json
    calls = {"n": 0}

    def write_tmp(path, data, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            m.discard(len(st.change_log) - 1)          # q2's edit, undone
        return real_w(path, data, **k)

    monkeypatch.setattr(safe_io, "_write_tmp_json", write_tmp)
    SV.Saver(st).save()
    on_disk = _disk_state(tmp_path / "chip")
    assert on_disk["qubits"]["q2"]["T1"] == old_q2 == st.state["qubits"]["q2"]["T1"]
    assert on_disk["qubits"]["q1"]["T1"] == 3.3e-05
    assert st.change_log == []
    assert not list((tmp_path / "chip").glob("*.tmp"))


@pytest.mark.parametrize("fmt", ["fresh", "sm", "foreign"])
def test_saved_bytes_are_the_single_hold_writers_bytes(tmp_path, fmt):
    """The bytes are exactly what ``safe_io.atomic_write_json`` (the writer the
    save used before) puts in a folder holding the same pre-image."""
    s, w = _ram_chip.build(4, 5)
    chip, ref = tmp_path / "chip", tmp_path / "ref"
    for d in (chip, ref):
        d.mkdir()
        if fmt == "sm":
            (d / "state.json").write_text(json.dumps(s, indent=4), encoding="utf-8")
            (d / "wiring.json").write_text(json.dumps(w, indent=4), encoding="utf-8")
        elif fmt == "foreign":         # 2-space, CRLF, no trailing newline
            (d / "state.json").write_bytes(json.dumps(s, indent=2).replace("\n", "\r\n").encode())
            (d / "wiring.json").write_bytes(json.dumps(w, indent=2).replace("\n", "\r\n").encode())
    st = QuamStore.from_dicts(s, w)
    st.folder_path = chip
    Modifier(st).set_value("qubits.q1.T1", 1.25e-05)
    Modifier(st).set_value("qubits.q2.extras.note_id", "0042")
    Modifier(st).set_value("qubits.q3.T1", float("nan"), coerce=False, enforce=False)
    SV.Saver(st).save()
    safe_io.atomic_write_json(ref / "state.json", st.state)
    safe_io.atomic_write_json(ref / "wiring.json", st.wiring)
    assert (chip / "state.json").read_bytes() == (ref / "state.json").read_bytes()
    assert (chip / "wiring.json").read_bytes() == (ref / "wiring.json").read_bytes()


def test_a_document_marshal_refuses_is_saved_by_the_single_hold_road(tmp_path):
    from collections import OrderedDict
    st = _folder_store(tmp_path / "chip")
    st.state["qubits"]["q1"]["extras"] = OrderedDict(b=1, a=2)   # json writes it; marshal does not
    Modifier(st).set_value("qubits.q2.T1", 7.7e-05)
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "state.json").write_bytes((tmp_path / "chip" / "state.json").read_bytes())
    SV.Saver(st).save()
    safe_io.atomic_write_json(ref / "state.json", st.state)
    assert (tmp_path / "chip" / "state.json").read_bytes() == (ref / "state.json").read_bytes()
    assert st.change_log == []


def test_a_caller_holding_the_store_lock_saves_under_it(tmp_path):
    """Its hold is not the saver's to give up, and nothing may wait on a save
    lock under it: the single-hold save, same bytes."""
    st = _folder_store(tmp_path / "chip")
    Modifier(st).set_value("qubits.q1.T1", 3.3e-05)
    out: dict = {}

    def inside():
        with st._lock:
            return SV.Saver(st).save()
    t = _in_request("/save", inside, out)
    t.join(30)
    assert not t.is_alive() and "error" not in out, out.get("error")
    assert _disk_state(tmp_path / "chip")["qubits"]["q1"]["T1"] == 3.3e-05
    assert st.change_log == []
