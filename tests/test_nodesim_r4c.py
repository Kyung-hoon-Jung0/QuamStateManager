"""R4C: a later instance close replaces the clicking window's renew offer answer (P3-1)."""
import time
from types import SimpleNamespace

from quam_state_manager.core import nodesim_click as click


def manager(tmp_path, notices):
    m = click.Manager(tmp_path / "inst", popen=None, probe=None, make_copy=None, remove_copy=None)
    m.pool = SimpleNamespace(notices=notices, secrets=[])
    return m


def offered(m, window, offer_at):
    """Put a finished warm click whose answer is the renew offer for pool key "k"."""
    key = click.window_key(window)
    rid = "r1"
    m.warm_jobs[rid] = dict(answer={**click.answer("renew_offer", expires_at="2026-01-01T11:01:00+00:00"),
                                    "id": rid},
                            done=True, window=key, seen=0, pool_key="k", offer_at=offer_at)
    m._mark_latest(key, rid)
    return rid


def test_close_after_renew_offer_replaces_the_closes_at_answer(tmp_path):
    """renew_offer, then instance_closed(why="limit") for the same key: the window shows the close line."""
    notices = {}
    m = manager(tmp_path, notices)
    t = time.time()
    rid = offered(m, "w1", t)
    assert m.status("w1", rid)["state"] == "renew_offer"
    notices["k"] = dict(state="instance_closed", why="limit", at=t + 1, cluster_hash="h")
    out = m.status("w1", rid)
    assert out["state"] == "instance_closed" and out["id"] == rid
    assert out["line"] == click.line("instance_closed", why="limit")
    assert "closes at" not in out["line"]


def test_close_before_the_offer_keeps_the_offer(tmp_path):
    """A close notice older than the offer (a previous instance) never hides the newer offer."""
    t = time.time()
    m = manager(tmp_path, {"k": dict(state="instance_closed", why="limit", at=t - 5, cluster_hash="h")})
    rid = offered(m, "w1", t)
    assert m.status("w1", rid)["state"] == "renew_offer"


def test_close_of_another_key_keeps_the_offer(tmp_path):
    """A close of another config's instance leaves this window's offer alone."""
    t = time.time()
    m = manager(tmp_path, {"other": dict(state="idle_closed", at=t + 1, cluster_hash="h")})
    rid = offered(m, "w1", t)
    assert m.status("w1", rid)["state"] == "renew_offer"
