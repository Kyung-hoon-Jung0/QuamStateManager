"""diff_cache: a drift diff may stand in for a snapshot diff only when equal.

w7/livewrite (P4). The drift poll's baseline->live diff (flat state+wiring
merge, no ignored keys) is shared with the pre-apply backup snapshot's
prior->new diff (QuamStore deep merge, ``Differ``'s default ignored keys),
keyed on both contents' hashes. Pinned: whenever the share happens, the
snapshot gets exactly what its own diff would have returned; when the two
merges differ (a top-level key in both files) nothing is shared.
"""
import json
import os
import random

import pytest

from quam_state_manager.core import diff_cache, doc_cache
from quam_state_manager.core.differ import _DEFAULT_IGNORE, Differ
from quam_state_manager.core.history import _canonical_hash_of
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.web import routes


def _tree(r, d):
    if d <= 0 or r.random() < 0.3:
        return r.choice([1, 1.0, True, None, "s", r.random(), [], {}])
    return {f"k{i}": _tree(r, d - 1) for i in range(r.randint(1, 3))} | (
        {"__class__": r.choice(["a.B", "a.C"])} if r.random() < 0.4 else {})


def _pair(state, wiring):
    sb, wb = json.dumps(state).encode(), json.dumps(wiring).encode()
    p = doc_cache.PairRead(None, None, sb, wb, doc_cache.digest(sb), doc_cache.digest(wb))
    p.content_hash()                       # the status poll has hashed it
    return p


def _rows(entries):
    return [(e.dot_path, json.dumps(e.old_value), json.dumps(e.new_value), e.change_type)
            for e in entries]


@pytest.mark.parametrize("seed", range(60))
def test_a_shared_drift_diff_is_the_snapshot_diff(seed):
    r = random.Random(seed)
    collide = seed % 3 == 0
    a_s = {"qubits": {"q1": _tree(r, 3)}, "extras": _tree(r, 2)}
    a_w = {"wiring": _tree(r, 2)} | ({"qubits": _tree(r, 2)} if collide else {})
    b_s = json.loads(json.dumps(a_s))
    b_s["qubits"]["k_new"] = _tree(r, 2)
    b_s["extras"] = _tree(r, 2)
    b_w = json.loads(json.dumps(a_w))
    diff_cache.PAIRS.clear()
    base = {"state": a_s, "wiring": a_w, "state_hash": _canonical_hash_of(a_s, a_w),
            "captured_utc": "t"}
    pair = _pair(b_s, b_w)
    # what the drift poll computes and offers
    ents = Differ().diff((a_s, a_w), (b_s, b_w), ignore_keys=set())
    routes._share_content_diff(base, pair, ents)
    shared = diff_cache.lookup(base["state_hash"], _canonical_hash_of(b_s, b_w), _DEFAULT_IGNORE)
    ref = Differ().diff(QuamStore.from_dicts(a_s, a_w), QuamStore.from_dicts(b_s, b_w))
    # the same diff named by the live side's raw byte digests (what a
    # snapshot capture of exactly these bytes holds)
    shared_raw = diff_cache.lookup(
        base["state_hash"], diff_cache.raw_key(pair.state_digest, pair.wiring_digest),
        _DEFAULT_IGNORE)
    if collide:
        assert shared is None, "a colliding chip's flat diff was shared"
        assert shared_raw is None, "a colliding chip's flat diff was shared (raw key)"
    else:
        assert shared is not None
        assert _rows(shared) == _rows(ref), seed
        assert shared_raw is not None
        assert _rows(shared_raw) == _rows(ref), seed


def _write(folder, state, wiring):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")


@pytest.mark.parametrize("seed", range(20))
def test_state_folder_diff_equals_differ_and_follows_a_rewrite(tmp_path, seed):
    """/prev-state-diff's memo: the same entries as Differ over the folders,
    and a folder rewritten since (new mtime) is diffed afresh."""
    from quam_state_manager.core.history import diff_state_folders
    r = random.Random(seed)
    a, b = tmp_path / "a" / "quam_state", tmp_path / "b" / "quam_state"
    a_s = {"qubits": {"q1": _tree(r, 3)}, "extras": _tree(r, 2)}
    w = {"wiring": _tree(r, 2)} | ({"qubits": _tree(r, 2)} if seed % 4 == 0 else {})
    b_s = json.loads(json.dumps(a_s))
    b_s["qubits"]["q2"] = _tree(r, 2)
    _write(a, a_s, w)
    _write(b, b_s, w)
    ref = Differ().diff(a, b)
    assert _rows(diff_state_folders(a, b)) == _rows(ref)
    assert _rows(diff_state_folders(a, b)) == _rows(ref)          # the repeat
    b_s["qubits"]["q1"] = {"changed": seed}
    _write(b, b_s, w)
    st = (b / "state.json").stat()
    os.utime(b / "state.json", ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert _rows(diff_state_folders(a, b)) == _rows(Differ().diff(a, b))


@pytest.mark.parametrize("which", ["state", "wiring"])
def test_state_folder_diff_follows_a_same_size_mtime_restored_rewrite(tmp_path, which):
    """Verifier D2: a stat key -- (mtime_ns, size) -- served the old diff after
    a same-size rewrite with the mtime put back. The memo is content-keyed, so
    warm must equal cold after any byte change, whatever the stat says."""
    from quam_state_manager.core.history import diff_state_folders
    a, b = tmp_path / "a", tmp_path / "b"
    s = {"qubits": {"q1": {"chi": -350000.0, "T1": 1e-05}}}
    w = {"wiring": {"q1": {"xy": "#/ports/1"}}}
    _write(a, s, w)
    _write(b, s, w)
    assert diff_state_folders(a, b) == []
    p = b / f"{which}.json"
    raw, st = p.read_bytes(), p.stat()
    new = raw.replace(b"-350000.0", b"-350001.0") if which == "state" else raw.replace(b"ports/1", b"ports/2")
    assert new != raw and len(new) == len(raw)
    p.write_bytes(new)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert p.stat().st_mtime_ns == st.st_mtime_ns and p.stat().st_size == st.st_size
    assert _rows(diff_state_folders(a, b)) == _rows(Differ().diff(a, b)) != []


def _pair_of(folder):
    sb = (folder / "state.json").read_bytes()
    wb = (folder / "wiring.json").read_bytes()
    return doc_cache.PairRead(None, None, sb, wb, doc_cache.digest(sb), doc_cache.digest(wb))


@pytest.mark.parametrize("seed", range(12))
def test_a_capture_takes_the_polls_diff_by_its_bytes_and_only_for_those_bytes(tmp_path, seed):
    """Take live: the drift poll offered baseline->live keyed on the live
    BYTES (the live side's canonical hash was never computed); the snapshot
    capture of exactly those bytes takes it -- and equals its own diff. A
    capture of other bytes (here: the same state.json, another wiring.json)
    must not take it."""
    from quam_state_manager.core.history import _diff_snapshot_dirs
    r = random.Random(seed)
    prior, snap = tmp_path / "prior", tmp_path / "snap"
    a_s = {"qubits": {"q1": _tree(r, 3)}, "extras": _tree(r, 2)}
    a_w = {"wiring": _tree(r, 2)}
    b_s = json.loads(json.dumps(a_s))
    b_s["qubits"]["k_new"] = _tree(r, 2)
    b_w = {"wiring": {"moved": seed}} if seed % 2 else json.loads(json.dumps(a_w))
    _write(prior, a_s, a_w)
    _write(snap, b_s, b_w)
    diff_cache.PAIRS.clear()
    base = {"state": a_s, "wiring": a_w, "state_hash": _canonical_hash_of(a_s, a_w)}
    pb = _pair_of(snap)
    ents = Differ().diff((a_s, a_w), (pb.state, pb.wiring), ignore_keys=set())
    routes._share_content_diff(base, pb, ents)
    h0 = diff_cache.PAIRS.hits
    got = _diff_snapshot_dirs(prior, snap, b_pair=pb, a_hash=base["state_hash"])
    assert diff_cache.PAIRS.hits > h0, "the capture recomputed a diff the poll had taken"
    assert _rows(got) == _rows(Differ().diff(prior, snap)), seed
    # other bytes: the wiring moved after the poll -- no share, still right
    b_w2 = {"wiring": {"moved_again": seed}}
    snap2 = tmp_path / "snap2"
    _write(snap2, b_s, b_w2)
    pb2 = _pair_of(snap2)
    h1 = diff_cache.PAIRS.hits
    got2 = _diff_snapshot_dirs(prior, snap2, b_pair=pb2, a_hash=base["state_hash"])
    assert diff_cache.PAIRS.hits == h1, "a capture of other bytes took the poll's diff"
    assert _rows(got2) == _rows(Differ().diff(prior, snap2)), seed
