"""diff_cache: a drift diff may stand in for a snapshot diff only when equal.

w7/livewrite (P4). The drift poll's baseline->live diff (flat state+wiring
merge, no ignored keys) is shared with the pre-apply backup snapshot's
prior->new diff (QuamStore deep merge, ``Differ``'s default ignored keys),
keyed on both contents' hashes. Pinned: whenever the share happens, the
snapshot gets exactly what its own diff would have returned; when the two
merges differ (a top-level key in both files) nothing is shared.
"""
import json
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
    if collide:
        assert shared is None, "a colliding chip's flat diff was shared"
    else:
        assert shared is not None
        assert _rows(shared) == _rows(ref), seed
