"""w7/livewrite: the lazily-built search index answers like a fresh build.

Every wholesale content change (pull, restore, load) used to rebuild the
SearchIndex on the request; it is now built on first use from the store as
it is then, and patched by the modifier's incremental hooks afterwards. The
old eager index was a build of the pulled content patched by every edit since,
so the contract is: whatever mix of edits happened before and after the first
search, the lazy index answers every query exactly as a fresh build of the
current content does.
"""
import random

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.search_index import LazySearchIndex, SearchIndex

QUERIES = ("q1", "f_01", "T1 q2", "amplitude", "x180 length", "zz_new", "4.8", "q3 | q0")


def _store():
    state = {"qubits": {f"q{i}": {"f_01": 4.8e9 + i, "T1": 1e-5 * (i + 1), "name": f"q{i}",
                                  "xy": {"operations": {"x180": {"amplitude": 0.1 * i, "length": 40}}}}
                        for i in range(4)}}
    return QuamStore.from_dicts(state, {"wiring": {"q0": {"xy": "#/ports/1"}}})


def _answer(index, q):
    return [(r.dot_path, r.value_str, r.category, r.score) for r in index.search(q, limit=200)]


@pytest.mark.parametrize("seed", range(20))
def test_lazy_index_equals_a_fresh_build_after_random_edits(seed):
    r = random.Random(seed)
    store = _store()
    lazy = LazySearchIndex(store, set(store.wiring.keys()))
    store.search_index = lazy
    mod = Modifier(store)
    first_use = r.randrange(0, 8)
    for step in range(8):
        if step == first_use:
            _answer(lazy, "q1")                         # builds it now
        q = f"q{r.randrange(4)}"
        op = r.random()
        if op < 0.6:
            mod.set_value(f"qubits.{q}.T1", r.random() * 1e-4)
        elif op < 0.8:
            try:
                mod.create_subtree(f"qubits.{q}.zz_new", {"a": 1.5, "b": "x"}, enforce=False)
            except KeyError:
                pass
        else:
            try:
                mod.delete_subtree(f"qubits.{q}.zz_new")
            except KeyError:
                pass
    fresh = SearchIndex.build(store.merged, wiring_keys=set(store.wiring.keys()))
    for qq in QUERIES:
        assert _answer(lazy, qq) == _answer(fresh, qq), (seed, qq)
