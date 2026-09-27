"""RAM P10 cold-open follow-up: the chip prewarm also warms what the first
Chip Status visit waits on -- the chip's pointer cache, its lint and its
env-schema analysis -- off the request thread.

Measured on big30x (19.4 MB state, in-process): the first GET /topology after
a cold load was 5.0 s (lint_state 6-9 s cold, incl. ~15.6k pointer
resolutions); with the prewarm done it was 0.77 s.

Staleness is the global risk: every warmed answer lands in the SAME
seq-keyed memo the request path reads, so it must equal a cold compute, and an
edit racing the warm must never leave a stale entry behind.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import chip_park, diagnostics, loader, path_match
from quam_state_manager.core import state_env_validate
from quam_state_manager.core.loader import QuamStore, warm_pointer_cache
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.pointer_resolver import resolve_pointer
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def _make_chip(folder: Path, n: int = 4) -> Path:
    """A chip WITH pointers: relative (#../), absolute (#/) and chained."""
    folder.mkdir(parents=True, exist_ok=True)
    qubits = {}
    for i in range(1, n + 1):
        q = f"q{i}"
        qubits[q] = {
            "id": q, "f_01": 6.1e9 + i, "T1": 1.0e-5,
            "xy": {"RF_frequency": f"#/qubits/{q}/f_01",
                   "operations": {
                       "x180": {"amplitude": 0.1 * i, "length": 40},
                       "x90": {"amplitude": "#../x180/amplitude",
                               "length": "#../x180/length"},
                       "y90": {"amplitude": "#../x90/amplitude"}}},
        }
    state = {"qubits": qubits, "qubit_pairs": {},
             "active_qubit_names": list(qubits)}
    wiring = {"wiring": {"qubits": {q: {"xy": {"opx_output": "#/ports/mw/1"}}
                                    for q in qubits}},
              "network": {"host": "10.0.0.1"}}
    (folder / "state.json").write_text(json.dumps(state, indent=4))
    (folder / "wiring.json").write_text(json.dumps(wiring, indent=4))
    return folder


def _wait(pred, timeout=20.0):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _cold_entries(store) -> dict:
    """Every cached (pointer, path) resolved from scratch on the store's
    CURRENT content, uncached."""
    return {k: resolve_pointer(store.merged, k[0], k[1]) for k in store._pointer_cache}


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_inst"))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    routes._quam_cache.clear()
    chip_park.PARKED.clear()
    chip_park.CONTENT_HASH.clear()
    yield
    routes._quam_cache.clear()
    chip_park.PARKED.clear()
    chip_park.CONTENT_HASH.clear()


def test_opening_a_chip_warms_its_lint_and_pointers_without_a_request(app, tmp_path):
    """No page asks for diagnostics; the open alone leaves the chip linted
    (at its current counter) with a full pointer cache -- and the warmed lint
    is exactly a cold lint of the same files."""
    chip = _make_chip(tmp_path / "A")
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    ctx = routes._quam_cache[path_match.fs_key(chip.resolve())]
    store = ctx["store"]

    def linted():
        hit = diagnostics._lint_state_cache.get(store)
        return hit is not None and hit[0] == store.mutation_seq

    assert _wait(linted), "the opened chip was never linted in the background"
    n_ptr = sum(1 for _dp, v, _pt in loader._walk(store.merged)
                if loader.is_pointer(v) and not loader.is_self_ref(v))
    assert n_ptr >= 5 * 4
    assert len(store._pointer_cache) >= n_ptr
    assert dict(store._pointer_cache) == _cold_entries(store)
    cold = QuamStore(Path(ctx["working_copy"].working_folder))
    assert ([f.as_dict() for f in diagnostics._lint_state_cache[store][1]]
            == [f.as_dict() for f in diagnostics.lint_state(cold)])


def test_an_edit_racing_the_pointer_warm_leaves_no_stale_entry(tmp_path):
    """The edit lands between two warm chunks and changes a pointer TARGET
    (x180 amplitude, read through x90 and y90). Every cache entry afterwards
    must be the cold resolution of the edited content."""
    store = QuamStore(_make_chip(tmp_path / "R", n=6))
    mod = Modifier(store)
    calls = []

    def pace():
        calls.append(1)
        if len(calls) == 2:
            mod.set_value("qubits.q3.xy.operations.x180.amplitude", 0.777)
            # a foreground read re-fills the cache for the NEW content
            store.resolve_pointer("#../x180/amplitude",
                                  ("qubits", "q3", "xy", "operations", "x90", "amplitude"))

    done = warm_pointer_cache(store, pace, chunk=1)
    assert len(calls) >= 2
    assert done is False                   # the moved counter stopped the warm
    assert store._pointer_cache            # ... after real work on both sides
    assert dict(store._pointer_cache) == _cold_entries(store)
    got = store.resolve_pointer("#../x90/amplitude",
                                ("qubits", "q3", "xy", "operations", "y90", "amplitude"))
    assert got == 0.777


def test_a_warm_pass_is_not_repeated_at_the_same_content(tmp_path, monkeypatch):
    store = QuamStore(_make_chip(tmp_path / "W"))
    assert warm_pointer_cache(store) is True
    walks = []
    real = loader._walk
    monkeypatch.setattr(loader, "_walk", lambda *a, **k: walks.append(1) or real(*a, **k))
    assert warm_pointer_cache(store) is True
    assert walks == []
    Modifier(store).set_value("qubits.q1.T1", 2.0e-5)
    assert warm_pointer_cache(store) is True
    assert walks, "an edit must make the next warm walk again"


def test_a_request_waiting_on_the_background_lint_takes_its_result(tmp_path, monkeypatch):
    """The prewarm lints holding the store lock. A request that arrives
    meanwhile waits on that lock -- and must then take the finished result,
    not lint the chip a second time (6-9 s on big30x)."""
    store = QuamStore(_make_chip(tmp_path / "L"))
    real = diagnostics._lint_state_uncached
    runs, entered, go = [], threading.Event(), threading.Event()

    def slow(s):
        runs.append(1)
        entered.set()
        go.wait(10)
        return real(s)

    monkeypatch.setattr(diagnostics, "_lint_state_uncached", slow)
    out = {}
    bg = threading.Thread(target=lambda: out.setdefault("bg", diagnostics.lint_state(store)))
    bg.start()
    assert entered.wait(10)
    fg = threading.Thread(target=lambda: out.setdefault("fg", diagnostics.lint_state(store)))
    fg.start()
    time.sleep(0.2)                        # fg is now blocked on the store lock
    go.set()
    bg.join(10)
    fg.join(10)
    assert len(runs) == 1, "the waiting request linted the chip again"
    assert [f.as_dict() for f in out["fg"]] == [f.as_dict() for f in out["bg"]]


def test_a_request_waiting_on_the_background_env_analysis_takes_its_result(tmp_path, monkeypatch):
    store = QuamStore(_make_chip(tmp_path / "E"))
    manifest = {"versions": {"quam": "0.0.test"}, "classes": {}}
    real = state_env_validate.analyze_state
    runs, entered, go = [], threading.Event(), threading.Event()

    def slow(state, m):
        runs.append(1)
        entered.set()
        go.wait(10)
        return real(state, m)

    monkeypatch.setattr(state_env_validate, "analyze_state", slow)
    out = {}
    bg = threading.Thread(target=lambda: out.setdefault(
        "bg", state_env_validate.analysis_for_store(store, manifest)))
    bg.start()
    assert entered.wait(10)
    fg = threading.Thread(target=lambda: out.setdefault(
        "fg", state_env_validate.analysis_for_store(store, manifest)))
    fg.start()
    time.sleep(0.2)
    go.set()
    bg.join(10)
    fg.join(10)
    assert len(runs) == 1, "the waiting request analysed the chip again"
    assert out["fg"] is out["bg"]
