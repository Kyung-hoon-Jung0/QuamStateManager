"""w7 fq-sync P2: a pull that moved values is patched into the store IN PLACE.

On big30x a Pull & apply wrote the live chip in 2-3 s but answered after
13-19 s: the pull's ``store.reload()`` rebuilt the store, which every
store_revs token reads as "everything changed", so the crash-value advisory
right after the write re-linted the WHOLE chip (3.7-4.7 s), and so did the
first apply after a Take live. ``QuamStore.reload`` now writes the changed
scalar leaves into the store's own documents when that is all the files
differ by -- one plain ``set`` event each, exactly what ``Modifier.set_value``
does, which every incremental cache already follows -- and rebuilds as before
otherwise.

The contract that makes this safe on the LIVE WRITE path: whichever road the
reload takes, the store is JSON-identical to the files afterwards (every
later save serializes it). Each "not plain" case below is a way the two
documents can compare ``==`` and still dump differently.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core import diagnostics, json_diff, loader, store_revs
from quam_state_manager.core.loader import QuamStore, file_digest, merge_state_wiring
from quam_state_manager.core.modifier import Modifier


def _state():
    qubits = {}
    for i in range(1, 5):
        q = f"q{i}"
        qubits[q] = {
            "id": q, "f_01": 6.1e9 + i, "T1": 1.0e-5, "chi": -350000.0,
            "n": 3, "flag": True, "neg0": 0.0, "none": None,
            "cal": [0.5 * i, 1.5 * i, 2.5],
            "xy": {"RF_frequency": f"#/qubits/{q}/f_01",
                   "operations": {"x180": {"amplitude": 0.1 * i, "length": 40},
                                  "x90": {"amplitude": "#../x180/amplitude",
                                          "length": "#../x180/length"}}},
        }
    return {"__class__": "quam.Root", "top_scalar": 1.25, "qubits": qubits,
            "qubit_pairs": {"q1-2": {"id": "q1-2", "fid": 0.99}},
            "active_qubit_names": list(qubits)}


def _wiring():
    return {"wiring": {"qubits": {f"q{i}": {"xy": {"opx_output": "#/ports/mw/1"}}
                                  for i in range(1, 5)}},
            "network": {"host": "10.0.0.1", "port": 9510}}


def _write(folder: Path, state, wiring) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state, indent=4), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring, indent=4), encoding="utf-8")


def _files(folder: Path):
    return (json.loads((folder / "state.json").read_text(encoding="utf-8")),
            json.loads((folder / "wiring.json").read_text(encoding="utf-8")))


def _assert_is_the_files(store: QuamStore, folder: Path) -> None:
    s, w = _files(folder)
    assert json.dumps(store.state) == json.dumps(s)
    assert json.dumps(store.wiring) == json.dumps(w)
    assert json.dumps(store.merged) == json.dumps(merge_state_wiring(s, w))
    sb = (folder / "state.json").read_bytes()
    wb = (folder / "wiring.json").read_bytes()
    assert store.file_digest == file_digest(sb, wb)
    assert store.loaded_seq == store.mutation_seq and not store.change_log


@pytest.fixture
def chip(tmp_path):
    folder = tmp_path / "chip"
    _write(folder, _state(), _wiring())
    return folder


def test_a_values_only_reload_patches_the_same_documents(chip):
    store = QuamStore(chip)
    st, wi, me = store.state, store.wiring, store.merged
    s, w = _files(chip)
    s["qubits"]["q2"]["T1"] = 2.2e-5            # a leaf deep in a qubit
    s["qubits"]["q3"]["cal"][1] = 9.75          # a list element
    s["top_scalar"] = 3.5                       # a top-level scalar (merged is its own dict)
    w["network"]["port"] = 9600                 # a wiring leaf
    _write(chip, s, w)
    seq0 = store.mutation_seq
    assert store.reload() is True
    assert store.state is st and store.wiring is wi and store.merged is me
    assert store.merged["top_scalar"] == 3.5 and store.state["top_scalar"] == 3.5
    assert store.mutation_seq == seq0 + 4
    _assert_is_the_files(store, chip)


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda s, w: s["qubits"]["q1"].__setitem__("new_key", 1), id="key-added"),
    pytest.param(lambda s, w: s["qubits"]["q1"].pop("T1"), id="key-removed"),
    pytest.param(lambda s, w: s.__setitem__("qubits", dict(reversed(list(s["qubits"].items())))),
                 id="key-order"),
    pytest.param(lambda s, w: s["qubits"]["q1"]["cal"].append(1.0), id="list-resized"),
    pytest.param(lambda s, w: s["qubits"]["q1"]["xy"].__setitem__("RF_frequency", "#/qubits/q2/f_01"),
                 id="pointer-moved"),
    pytest.param(lambda s, w: s.__setitem__("__class__", "quam.Other"), id="class-changed"),
    pytest.param(lambda s, w: s["qubits"]["q1"].__setitem__("cal", {"a": 1}), id="list-to-dict"),
    pytest.param(lambda s, w: s["qubits"]["q1"].__setitem__("T1", {"v": 1}), id="leaf-to-dict"),
    pytest.param(lambda s, w: s["qubits"]["q1"].__setitem__("chi", "#/qubits/q1/f_01"),
                 id="value-to-pointer"),
])
def test_anything_but_plain_values_rebuilds_and_both_roads_end_at_the_files(chip, mutate):
    store = QuamStore(chip)
    st = store.state
    s, w = _files(chip)
    mutate(s, w)
    _write(chip, s, w)
    in_place = store.reload()
    # the road: a value-only-equal type/sign/order change is NOT plain
    assert in_place is False
    assert store.state is not st
    _assert_is_the_files(store, chip)


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda s: s["qubits"]["q1"].__setitem__("n", 3.0), id="int-to-equal-float"),
    pytest.param(lambda s: s["qubits"]["q1"].__setitem__("flag", 1), id="bool-to-equal-int"),
    pytest.param(lambda s: s["qubits"]["q1"].__setitem__("neg0", -0.0), id="zero-sign"),
    pytest.param(lambda s: s["qubits"]["q1"]["cal"].__setitem__(0, 1), id="float-to-equal-int-in-list"),
    pytest.param(lambda s: s["qubits"]["q1"].__setitem__("none", 0.5), id="null-to-value"),
])
def test_a_value_equal_under_eq_but_not_in_json_is_still_written(chip, mutate):
    """``3 == 3.0``, ``True == 1``, ``0.0 == -0.0`` -- yet each dumps
    differently. A patch that compared with ``==`` would skip them and a later
    save would write the OLD spelling over the files' new one."""
    store = QuamStore(chip)
    st = store.state
    s, w = _files(chip)
    mutate(s)
    _write(chip, s, w)
    assert store.reload() is True
    assert store.state is st
    _assert_is_the_files(store, chip)


def test_a_dotted_key_on_a_changed_path_rebuilds(tmp_path):
    folder = tmp_path / "c"
    s = _state()
    s["qubits"]["q1"]["a.b"] = 1.0
    _write(folder, s, _wiring())
    store = QuamStore(folder)
    s["qubits"]["q1"]["a.b"] = 2.0
    _write(folder, s, _wiring())
    assert store.reload() is False       # "qubits.q1.a.b" would name the wrong chunk
    _assert_is_the_files(store, folder)


def test_a_colliding_top_level_key_rebuilds(tmp_path):
    """wiring.json with top-level ``qubits`` deep-merges into a COPY in merged;
    a write into the source would miss it."""
    folder = tmp_path / "c"
    w = _wiring()
    w["qubits"] = {"q1": {"z": {"opx_output": "#/ports/lf/1"}}}
    _write(folder, _state(), w)
    store = QuamStore(folder)
    s, w2 = _files(folder)
    s["qubits"]["q2"]["T1"] = 7.0e-5
    _write(folder, s, w2)
    assert store.reload() is False
    _assert_is_the_files(store, folder)


def test_only_the_changed_chunks_move(chip):
    store = QuamStore(chip)
    toks = {q: store_revs.chunk_token(store, "qubits", q) for q in ("q1", "q2", "q3")}
    struct = store_revs.struct_token(store)
    seq0 = store_revs.seq_token(store)
    s, w = _files(chip)
    s["qubits"]["q2"]["T1"] = 3.3e-5
    _write(chip, s, w)
    assert store.reload() is True
    assert store_revs.chunk_token(store, "qubits", "q1") == toks["q1"]
    assert store_revs.chunk_token(store, "qubits", "q3") == toks["q3"]
    assert store_revs.chunk_token(store, "qubits", "q2") != toks["q2"]
    assert store_revs.struct_token(store) == struct
    ev = store_revs.changes_since(store, seq0[1])
    assert [(p, plain) for _s, p, plain, _t in ev] == [("qubits.q2.T1", True)]
    assert store_revs.plain_olds_since(store, seq0[1]) == [("qubits.q2.T1", 1.0e-5)]


def test_the_lint_after_an_in_place_reload_is_incremental_and_equals_a_cold_lint(chip, monkeypatch):
    store = QuamStore(chip)
    diagnostics.lint_state(store)
    walks = []
    real = QuamStore._validate_pointers
    monkeypatch.setattr(QuamStore, "_validate_pointers",
                        lambda self: (walks.append(1), real(self))[1])
    s, w = _files(chip)
    s["qubits"]["q2"]["xy"]["operations"]["x180"]["amplitude"] = 5.0   # a DAC-range error
    s["qubits"]["q3"]["T1"] = float("nan")                              # a non-finite value
    _write(chip, s, w)
    assert store.reload() is True
    warm = diagnostics.lint_state(store)
    # the chip-wide pointer walk (structure-keyed) was not repeated: a plain
    # reload leaves the structure token alone
    assert walks == []
    cold = diagnostics._lint_state_uncached(QuamStore(chip), incremental=False)
    assert [f.as_dict() for f in warm] == [f.as_dict() for f in cold]
    assert any(f.severity == "error" for f in warm)


def test_an_unapplied_edit_the_files_also_hold_is_announced(chip):
    """The files hold the value the user typed (live got it some other way):
    no value changes, but the edit leaves the change log -- its row must be
    told, or a grid keeps marking it "modified"."""
    store = QuamStore(chip)
    Modifier(store).set_value("qubits.q1.chi", -1234.0)
    tok = store_revs.chunk_token(store, "qubits", "q1")
    s, w = _files(chip)
    s["qubits"]["q1"]["chi"] = -1234.0
    _write(chip, s, w)
    assert store.reload() is True
    assert store_revs.chunk_token(store, "qubits", "q1") != tok
    _assert_is_the_files(store, chip)
    assert loader.is_pristine(store)


def test_an_unchanged_reload_still_advances_the_counter_and_moves_nothing_else(chip):
    store = QuamStore(chip)
    tok = store_revs.chunk_token(store, "qubits", "q1")
    struct = store_revs.struct_token(store)
    seq0 = store.mutation_seq
    assert store.reload() is True
    assert store.mutation_seq == seq0 + 1          # the redo stack / ETags key on it
    assert store_revs.chunk_token(store, "qubits", "q1") == tok
    assert store_revs.struct_token(store) == struct
    ev = store_revs.changes_since(store, seq0)
    assert store_revs.plain_paths(ev) == []        # a touch writes no path
    assert store_revs.plain_olds_since(store, seq0) == []


@pytest.mark.parametrize("files_have_it", [False, True])
def test_a_pending_structural_edit_rebuilds(chip, files_have_it):
    """A created subtree leaves no plain event to stand for its change-log
    entry -- even when the files hold the very same subtree (no difference
    for the patch to see), the reload rebuilds."""
    store = QuamStore(chip)
    Modifier(store).create_subtree("qubits.q1.extra", {"a": 1})
    if files_have_it:
        s, w = _files(chip)
        s["qubits"]["q1"]["extra"] = {"a": 1}
        _write(chip, s, w)
    st = store.state
    assert store.reload() is False
    assert store.state is not st
    _assert_is_the_files(store, chip)


def test_shadow_mode_checks_the_patch_against_the_files(chip, monkeypatch):
    monkeypatch.setenv("SM_RAM_VERIFY", "1")
    store = QuamStore(chip)
    s, w = _files(chip)
    s["qubits"]["q4"]["chi"] = -1.0
    _write(chip, s, w)
    assert store.reload() is True                  # no StaleCacheError


def test_the_search_index_follows_an_in_place_reload(chip):
    from quam_state_manager.core.search_index import LazySearchIndex
    store = QuamStore(chip)
    idx = LazySearchIndex(store)
    store.search_index = idx
    idx.get()
    s, w = _files(chip)
    s["qubits"]["q2"]["T1"] = 4.4e-5
    _write(chip, s, w)
    assert store.reload() is True
    hits = [e for e in idx.search("q2 T1", limit=50) if e.dot_path == "qubits.q2.T1"]
    assert hits and hits[0].raw_value == 4.4e-5


# --------------------------------------------------------------------------
# _sync_patch: the pull's response names what moved, from the store's record
# --------------------------------------------------------------------------

def _reference_patch(before_doc, after_doc, over_cap):
    """The two-document answer the pull gave before the reload patched in place."""
    from quam_state_manager.core import leaf_patch
    from quam_state_manager.web import routes as R
    if over_cap:
        found, structural = leaf_patch.leaf_changes(before_doc, after_doc, max_changes=R._SYNC_PATCH_CAP)
    else:
        b, _ = json_diff.flatten(before_doc)
        a, _ = json_diff.flatten(after_doc)
        structural = any(p not in a for p in b) or any(p not in b for p in a)
        found = [(p, v) for p, v in a.items() if p in b and b[p] != v]
    out = []
    for p, v in found:
        e = R._revert_entry_payload(p, v)
        e["value"] = v
        out.append(e)
    return {"changes": out, "structural": structural}


@pytest.mark.parametrize("over_cap", [False, True], ids=["flatten-order", "document-order"])
def test_the_pull_patch_is_the_two_document_answer(chip, tmp_path, monkeypatch, over_cap):
    from quam_state_manager.web import routes as R
    from quam_state_manager.web.app import create_app
    if over_cap:
        monkeypatch.setattr(json_diff, "WALK_CAP", 20)
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    store = QuamStore(chip)
    ctx = {"store": store}
    m = Modifier(store)
    m.set_value("qubits.q1.chi", -2222.0)           # an unapplied edit
    with app.test_request_context("/"):
        before = R._leaf_snapshot(ctx, lazy=True)
        before_copy = copy.deepcopy(store.merged)
        s, w = _files(chip)
        for q, v in (("q3", 7.1e-5), ("q1", 8.8e-5), ("q4", 1.9e-5)):
            s["qubits"][q]["T1"] = v                 # three live changes, out of order
        s["qubits"]["q2"]["cal"][2] = 0.125
        _write(chip, s, w)
        assert store.reload() is True               # also drops the edit (files lack it)
        m.set_value("qubits.q1.chi", -2222.0)       # ...which the replay puts back
        m.set_value("qubits.q2.chi", -9.0)          # and one more edit
        m.set_value("qubits.q2.chi", -350000.0)     # ...back to where it was: no change
        got = R._sync_patch(ctx, before)
        want = _reference_patch(before_copy, store.merged, over_cap)
    assert got == want
    assert [c["value"] for c in got["changes"]]      # not vacuous
    assert got["structural"] is False


def test_a_structural_write_after_the_snapshot_is_structural(chip, tmp_path):
    from quam_state_manager.web import routes as R
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    store = QuamStore(chip)
    ctx = {"store": store}
    with app.test_request_context("/"):
        before = R._leaf_snapshot(ctx, lazy=True)
        Modifier(store).create_subtree("qubits.q1.extra", {"a": 1})
        assert R._sync_patch(ctx, before) == {"changes": [], "structural": True}
