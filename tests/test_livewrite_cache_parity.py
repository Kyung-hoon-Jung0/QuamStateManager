"""w7/livewrite P4: every cached diff answer equals a cold recompute.

``/state/live-diff`` (its whole JSON body) and the drift view are now served
from RAM keyed on content tokens (the store's identity + mutation counter +
change-log length, the live bytes' SHA-256, the sync point). The risk of any
such cache is a stale answer, so this drives a randomized sequence of the
events that can move either side -- cell edits, applies, outside writes
(including same-size rewrites that put the old mtime back), pulls -- and after
every event compares the warm answer with the answer computed after every
cache involved was dropped.
"""
import json
import os
import random
from pathlib import Path

import pytest

from quam_state_manager.core import doc_cache, json_pieces
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def _state() -> dict:
    return {
        "qubits": {
            f"q{i}": {"T1": 1e-5 * (i + 1), "f_01": 4.8e9 + i * 1e8, "chi": 1.0 + i,
                      "xy": {"operations": {"x180": {"amplitude": 0.1, "length": 40}}}}
            for i in range(4)},
        "active_qubit_names": ["q0", "q1", "q2", "q3"],
    }


@pytest.fixture
def chip(tmp_path):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_state(), indent=4), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}, indent=4), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    return c, folder


def _drop_caches():
    for memo in (routes._LIVE_DIFF_BODY, routes._WL_ENTRIES, routes._BL_ENTRIES,
                 doc_cache.PARSED, doc_cache.CANON, json_pieces.PIECES, json_pieces.DOCS):
        memo.clear()


def _outside_write(folder: Path, r: random.Random):
    if r.random() < 0.3:
        # a wiring-only outside write: the state bytes do not move, so only
        # the wiring digest in the cache tokens can notice it
        w = folder / "wiring.json"
        wd = json.loads(w.read_bytes())
        wd.setdefault("wiring", {})["port_hint"] = r.randrange(1000)
        w.write_text(json.dumps(wd, indent=4), encoding="utf-8")
        return
    p = folder / "state.json"
    st = json.loads(p.read_bytes())
    q = f"q{r.randrange(4)}"
    if r.random() < 0.5:
        st["qubits"][q]["chi"] = round(r.uniform(1.0, 9.0), 3)      # same width
        old = os.stat(p)
        p.write_text(json.dumps(st, indent=4), encoding="utf-8")
        if r.random() < 0.5:
            os.utime(p, ns=(old.st_atime_ns, old.st_mtime_ns))       # mtime put back
    else:
        st["qubits"][q]["T1"] = r.random() * 1e-4
        p.write_text(json.dumps(st, indent=4), encoding="utf-8")


_URLS = ("/state/live-diff", "/state/live-diff?with_live=1", "/state/drift/view")


def _answers(c):
    out = []
    for u in _URLS:
        a = c.get(u)
        b = c.get(u)            # the same question again: served from RAM
        out.append((u, a.status_code, a.get_data()))
        out.append((u, b.status_code, b.get_data()))
    return out


@pytest.mark.parametrize("seed", range(12))
def test_warm_answers_equal_cold_ones_across_random_events(chip, seed):
    c, folder = chip
    r = random.Random(seed)
    for step in range(14):
        ev = r.random()
        if ev < 0.4:
            q = f"q{r.randrange(4)}"
            c.post("/field/edit", data={"dot_path": f"qubits.{q}.chi",
                                        "value": str(round(r.uniform(1.0, 9.0), 3))})
        elif ev < 0.6:
            _outside_write(folder, r)
        elif ev < 0.75:
            c.post("/state/apply-to-live", data={"force": "1"})
        elif ev < 0.9:
            c.post("/state/sync", data={"mode": "discard", "force": "1"})
        # (else: nothing moved -- the repeat must hit and still be right)
        warm = _answers(c)
        # the inner entry memos answer on their OWN tokens once the body memo
        # in front of them is gone
        routes._LIVE_DIFF_BODY.clear()
        inner = _answers(c)
        _drop_caches()
        cold = _answers(c)
        assert warm == cold, (seed, step, ev)
        assert inner == cold, (seed, step, ev)
        assert all(code == 200 for _u, code, _b in warm), warm


def test_an_unchanged_repeat_is_a_hit_for_both_live_diff_shapes(chip):
    """The Explorer asks with_live=1 and the status surfaces ask without it;
    the two shapes must not evict each other (one shared slot made every
    alternating poll a full recompute)."""
    c, _folder = chip
    for u in ("/state/live-diff", "/state/live-diff?with_live=1"):
        assert c.get(u).status_code == 200
    h0 = routes._LIVE_DIFF_BODY.hits
    for u in ("/state/live-diff", "/state/live-diff?with_live=1") * 2:
        assert c.get(u).status_code == 200
    assert routes._LIVE_DIFF_BODY.hits - h0 == 4
