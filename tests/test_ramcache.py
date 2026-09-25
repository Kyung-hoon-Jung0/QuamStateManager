"""core/ramcache.KeyedMemo -- the P0 primitive (design ram_design.md §1.3).

Pinned: single-flight (two concurrent gets of one key -> one compute), a
mismatched key is never returned, Warming instead of an old value, the byte
budget (per memo and global, LRU across memos), the /debug/ram accounting
(total == sum of entry sizes), the lock rule, incremental computes, a Keyed
result stored under the token it truly describes, and shadow mode.
"""
from __future__ import annotations

import threading
import time

import pytest

from quam_state_manager.core import ramcache
from quam_state_manager.core.ramcache import (
    Keyed, KeyedMemo, LockHeldError, StaleCacheError, Warming,
)


@pytest.fixture
def memo():
    m = KeyedMemo("test.memo")
    yield m
    m.clear()


def test_a_hit_serves_the_stored_value_without_computing(memo):
    calls = []
    assert memo.get("s", 1, lambda: calls.append(1) or b"one") == b"one"
    assert memo.get("s", 1, lambda: calls.append(1) or b"other") == b"one"
    assert calls == [1]
    assert memo.hits == 1 and memo.misses == 1


def test_a_mismatched_key_is_never_returned(memo):
    memo.get("s", 1, lambda: b"at-1")
    assert memo.get("s", 2, lambda: b"at-2") == b"at-2"
    # and the old token is not resurrected by the slot's new entry
    assert memo.get("s", 1, lambda: b"at-1-again") == b"at-1-again"
    assert memo.peek("s") == (1, b"at-1-again")


def test_single_flight_two_concurrent_gets_compute_once(memo):
    started, release = threading.Event(), threading.Event()
    calls = []

    def compute():
        calls.append(1)
        started.set()
        release.wait(5)
        return b"v"

    out = []
    t1 = threading.Thread(target=lambda: out.append(memo.get("s", 1, compute)))
    t1.start()
    assert started.wait(5)
    t2 = threading.Thread(target=lambda: out.append(memo.get("s", 1, compute, wait_s=5)))
    t2.start()
    time.sleep(0.05)
    release.set()
    t1.join(5); t2.join(5)
    assert calls == [1], "the second caller computed instead of waiting"
    assert out == [b"v", b"v"]
    assert memo.waits == 1


def test_a_waiter_past_wait_s_gets_warming_never_an_old_value(memo):
    memo.get("s", 1, lambda: b"old")
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return b"new"

    t = threading.Thread(target=lambda: memo.get("s", 2, slow))
    t.start()
    assert started.wait(5)
    with pytest.raises(Warming):
        memo.get("s", 2, slow, wait_s=0.05)
    release.set()
    t.join(5)
    assert memo.get("s", 2, lambda: b"never") == b"new"


def test_an_error_reaches_the_waiters_and_is_not_cached(memo):
    started, release = threading.Event(), threading.Event()

    def boom():
        started.set()
        release.wait(5)
        raise ValueError("x")

    errs = []

    def run(**kw):
        try:
            memo.get("s", 1, boom, **kw)
        except ValueError as e:
            errs.append(e)

    t1 = threading.Thread(target=run)
    t1.start()
    assert started.wait(5)
    t2 = threading.Thread(target=lambda: run(wait_s=5))
    t2.start()
    time.sleep(0.05)
    release.set()
    t1.join(5); t2.join(5)
    assert len(errs) == 2
    assert memo.get("s", 1, lambda: b"ok") == b"ok"


def test_incremental_compute_receives_the_previous_value(memo):
    memo.get("s", 1, lambda prev: (prev, b"a")[1], incremental=True)
    seen = []
    assert memo.get("s", 2, lambda prev: seen.append(prev) or b"ab", incremental=True) == b"ab"
    assert seen == [b"a"]


def test_a_keyed_result_is_stored_under_the_token_it_describes(memo):
    # asked at token 1, but the source had already moved to 2
    assert memo.get("s", 1, lambda: Keyed(b"at-2", 2)) == b"at-2"
    assert memo.get("s", 2, lambda: b"recomputed") == b"at-2"       # a hit at 2
    assert memo.get("s", 1, lambda: b"at-1") == b"at-1"              # a miss at 1


def test_the_per_memo_byte_bound_evicts_lru_but_never_the_new_entry():
    m = KeyedMemo("test.bounded", max_bytes=10)
    try:
        m.get("a", 1, lambda: b"12345")
        m.get("b", 1, lambda: b"12345")
        m.get("a", 1, lambda: b"xxxxx")          # touch a -> b is the LRU
        m.get("c", 1, lambda: b"123")
        assert m.peek("b") is None and m.peek("a") is not None and m.peek("c") is not None
        assert m.bytes == 8
        big = m.get("d", 1, lambda: b"x" * 50)   # larger than the memo: served, not kept
        assert big == b"x" * 50 and m.peek("d") is None and m.oversize == 1
    finally:
        m.clear()


def test_max_entries_bound():
    m = KeyedMemo("test.entries", max_entries=2)
    try:
        for s in "abc":
            m.get(s, 1, lambda: b"1")
        assert m.peek("a") is None and len(m._entries) == 2
    finally:
        m.clear()


def test_the_global_budget_evicts_lru_across_memos(monkeypatch):
    monkeypatch.setenv("SM_RAM_BUDGET_MB", str(3000 / (1024 * 1024)))   # 3,000 bytes
    for other in list(ramcache._MEMOS):      # entries other tests left behind
        other.clear()
    m1, m2 = KeyedMemo("test.g1"), KeyedMemo("test.g2")
    try:
        m1.get("a", 1, lambda: b"x" * 1000)
        m2.get("b", 1, lambda: b"x" * 1000)
        m1.get("c", 1, lambda: b"x" * 1000)
        m2.get("d", 1, lambda: b"x" * 1000)      # over 3,000: the oldest (m1 "a") goes
        assert ramcache._TOTAL[0] <= ramcache.budget_bytes()
        assert m1.peek("a") is None
        assert m2.peek("d") is not None, "the entry being inserted was evicted"
    finally:
        m1.clear(); m2.clear()


def test_debug_ram_totals_equal_the_sum_of_entry_sizes():
    m = KeyedMemo("test.acct", max_bytes=100)
    try:
        for i in range(30):
            m.get(i % 7, i, lambda i=i: b"x" * (i % 13 + 1))
        snap = ramcache.snapshot()
        assert snap["total_bytes"] == snap["entry_bytes_sum"]
        mine = next(x for x in snap["memos"] if x["name"] == "test.acct")
        assert mine["bytes"] == mine["entry_bytes_sum"] <= 100
    finally:
        m.clear()
    snap = ramcache.snapshot()
    assert snap["total_bytes"] == snap["entry_bytes_sum"]


def test_get_refuses_while_the_caller_holds_a_forbidden_lock(memo):
    lk = threading.RLock()
    with lk:
        with pytest.raises(LockHeldError):
            memo.get("s", 1, lambda: b"v", forbid_held=[lk])
    assert memo.get("s", 1, lambda: b"v", forbid_held=[lk]) == b"v"
    # another thread holding it is not the caller holding it
    got = threading.Event()
    done = threading.Event()

    def holder():
        with lk:
            got.set()
            done.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert got.wait(5)
    try:
        assert memo.get("s", 1, lambda: b"v", forbid_held=[lk]) == b"v"
    finally:
        done.set()
        t.join(5)


def test_shadow_mode_catches_a_stale_hit(memo, monkeypatch):
    monkeypatch.setenv("SM_RAM_VERIFY", "1")
    box = {"v": b"a"}
    memo.get("s", 1, lambda: box["v"])
    box["v"] = b"b"                 # the source changed but the token did not
    with pytest.raises(StaleCacheError):
        memo.get("s", 1, lambda: box["v"])


def test_shadow_mode_checks_incremental_results_too(memo, monkeypatch):
    monkeypatch.setenv("SM_RAM_VERIFY", "1")
    memo.get("s", 1, lambda prev: b"a", incremental=True)
    # a buggy incremental step: derives from prev but a cold build disagrees
    with pytest.raises(StaleCacheError):
        memo.get("s", 2, lambda prev: (prev or b"") + b"!" if prev is not None else b"cold",
                 incremental=True)
