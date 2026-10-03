"""Shared SQLite readers must finish before retirement closes their handle."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path

import pytest

from quam_state_manager.core import chip_trends_ram as CTR
from quam_state_manager.core import leaf_index as LI
from quam_state_manager.core import param_history_ram as PHR


class _EntryLock:
    """Expose a contending retire operation without scheduling sleeps."""

    def __init__(self, retiring):
        self.lock = threading.Lock()
        self.retiring = retiring

    def __enter__(self):
        if not self.lock.acquire(blocking=False):
            self.retiring.set()
            self.lock.acquire()
        return self

    def __exit__(self, *_):
        self.lock.release()


class _Connection:
    """Never enters SQLite C code: even a broken close is safe to test."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.retiring = threading.Event()
        self.closed = threading.Event()
        self.block = False
        self.fail = False
        self.active = False
        self.closed_in_use = False

    def execute(self, sql):
        if self.closed.is_set():
            raise sqlite3.ProgrammingError("closed connection")
        if self.block:
            self.active = True
            self.entered.set()
            try:
                assert self.release.wait(5), "reader was never released"
                if self.fail:
                    raise sqlite3.OperationalError("injected read failure")
            finally:
                self.active = False
        return self

    def fetchone(self):
        return (7,)

    def close(self):
        self.closed_in_use |= self.active
        self.closed.set()
        self.retiring.set()


class _Threads:
    def __init__(self):
        self.threads = []
        self.errors = []
        self.results = []

    def start(self, fn):
        def run():
            try:
                self.results.append(fn())
            except BaseException as exc:
                self.errors.append(exc)
        t = threading.Thread(target=run, daemon=True)
        self.threads.append(t)
        t.start()

    def join(self, timeout=4):
        deadline = time.monotonic() + timeout
        for t in self.threads:
            t.join(max(0, deadline - time.monotonic()))
        assert not any(t.is_alive() for t in self.threads), "connection lock deadlock"
        assert not self.errors, self.errors


@pytest.fixture
def fake_pool(monkeypatch):
    PHR.close_all()
    CTR.close_all()
    # A deadlocking mutation cannot poison the next test's pool locks.
    for module in (PHR, CTR):
        monkeypatch.setattr(module, "_CONNS", OrderedDict())
        monkeypatch.setattr(module, "_CONN_LOCK", threading.Lock())
    identities = {}
    made = []

    def connect(*_, **__):
        c = _Connection()
        made.append(c)
        return c

    monkeypatch.setattr(PHR.sqlite3, "connect", connect)
    monkeypatch.setattr(PHR, "_file_identity", lambda p: identities.get(str(p), (1,)))
    monkeypatch.setattr(PHR, "_truncate_wal", lambda *_: True)
    yield identities, made
    for c in made:
        c.release.set()


@pytest.mark.parametrize("retirement", ["replacement", "eviction", "shutdown"])
def test_param_history_retirement_waits_for_execute(fake_pool, monkeypatch, tmp_path, retirement):
    identities, made = fake_pool
    path = tmp_path / "index.sqlite"
    before = PHR.data_version(path)
    c = made[0]
    ent = PHR._CONNS[str(path)]
    PHR._CONNS[str(path)] = (*ent[:2], _EntryLock(c.retiring), *ent[3:])
    c.block = True
    threads = _Threads()
    threads.start(lambda: PHR.data_version(path))
    try:
        assert c.entered.wait(2)
        if retirement == "replacement":
            identities[str(path)] = (2,)
            retire = lambda: PHR.data_version(path)
        elif retirement == "eviction":
            monkeypatch.setattr(PHR, "_CONNS_MAX", 1)
            retire = lambda: PHR.data_version(tmp_path / "other.sqlite")
        else:
            retire = PHR.close_all
        threads.start(retire)
        assert c.retiring.wait(2), "retirement never reached the held connection"
        assert not c.closed_in_use, "closed while executing"
        assert not c.closed.is_set(), "close did not wait for the reader"
        acquired = PHR._CONN_LOCK.acquire(timeout=1)
        assert acquired, "pool lock held while waiting for the entry lock"
        PHR._CONN_LOCK.release()
    finally:
        c.release.set()
        threads.join()
    assert c.closed.is_set()
    assert before in threads.results, "in-flight reader lost its version"
    if retirement == "replacement":
        after = PHR.data_version(path)
        assert after[0] == (2,) and after[1] != before[1]


def test_failed_replacement_retires_the_old_connection(fake_pool, monkeypatch, tmp_path):
    identities, made = fake_pool
    path = tmp_path / "index.sqlite"
    PHR.data_version(path)
    identities[str(path)] = (2,)

    def unavailable(*_, **__):
        raise sqlite3.OperationalError("index unavailable")

    monkeypatch.setattr(PHR.sqlite3, "connect", unavailable)
    assert PHR.data_version(path) == ("unreadable", (2,))
    assert str(path) not in PHR._CONNS
    assert made[0].closed.is_set()


def test_read_error_and_eviction_do_not_deadlock(fake_pool, monkeypatch, tmp_path):
    _, made = fake_pool
    path = tmp_path / "index.sqlite"
    PHR.data_version(path)
    c = made[0]
    ent = PHR._CONNS[str(path)]
    PHR._CONNS[str(path)] = (*ent[:2], _EntryLock(c.retiring), *ent[3:])
    c.block = c.fail = True
    monkeypatch.setattr(PHR, "_CONNS_MAX", 1)
    threads = _Threads()
    threads.start(lambda: PHR.data_version(path))
    try:
        assert c.entered.wait(2)
        threads.start(lambda: PHR.data_version(tmp_path / "other.sqlite"))
        assert c.retiring.wait(2)
    finally:
        c.release.set()
        threads.join()
    assert ("unreadable", (1,)) in threads.results
    assert c.closed.is_set() and not c.closed_in_use
    assert str(path) not in PHR._CONNS


@pytest.mark.parametrize("retirement", ["direct", "eviction", "shutdown"])
def test_trends_retirement_waits_for_execute(fake_pool, monkeypatch, tmp_path, retirement):
    _, made = fake_pool
    hist = tmp_path / "history"
    hist.mkdir()
    (hist / "index.sqlite").touch()
    ic = CTR._conn_for(hist)
    c = made[0]
    ic.lock = _EntryLock(c.retiring)
    c.block = True
    threads = _Threads()
    threads.start(lambda: ic.run(lambda conn: conn.execute("SELECT 1").fetchone()))
    try:
        assert c.entered.wait(2)
        if retirement == "direct":
            retire = ic.close
        elif retirement == "eviction":
            monkeypatch.setattr(CTR, "_MAX_CONNS", 1)
            other = tmp_path / "other"
            other.mkdir()
            (other / "index.sqlite").touch()
            retire = lambda: CTR._conn_for(other)
        else:
            retire = CTR.close_all
        threads.start(retire)
        assert c.retiring.wait(2)
        assert not c.closed_in_use and not c.closed.is_set(), "closed while executing"
        acquired = CTR._CONN_LOCK.acquire(timeout=1)
        assert acquired, "trends pool lock held during retirement"
        CTR._CONN_LOCK.release()
    finally:
        c.release.set()
        threads.join()
    assert c.closed.is_set() and (7,) in threads.results


def test_warm_version_reuses_connection_and_replacement_changes_generation(tmp_path, monkeypatch):
    path = tmp_path / "index.sqlite"
    _make_index(path)
    PHR.close_all()
    real_connect = sqlite3.connect
    opens = []

    def connect(*args, **kwargs):
        opens.append(args)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(PHR.sqlite3, "connect", connect)
    try:
        first = PHR.data_version(path)
        for _ in range(10):
            assert PHR.data_version(path) == first
        assert len(opens) == 1
        writer = real_connect(str(path), isolation_level=None)
        try:
            writer.execute("UPDATE leaf_cp SET value = value + 1")
        finally:
            writer.close()
        committed = PHR.data_version(path)
        assert committed[:2] == first[:2] and committed[2] != first[2]
        # Force an observed identity change without a platform-specific open-file rename.
        monkeypatch.setattr(PHR, "_file_identity", lambda _: ("replaced",))
        replaced = PHR.data_version(path)
        assert replaced[0] == ("replaced",) and replaced[1] != first[1]
        assert len(opens) == 2
    finally:
        PHR.close_all()


def _make_index(path):
    c = sqlite3.connect(str(path), isolation_level=None)
    try:
        LI.ensure_schema(c)
        LI.ingest_snapshot(c, ts="2026-10-03T00:00:00Z", trigger="manual",
                           run_id=None, experiment=None, folder=None,
                           state={"qubits": {"q1": {"T1": 1.0}}}, wiring={})
    finally:
        c.close()


class _History:
    """Only the manager interface needed by hist_token and leaf_search."""

    def __init__(self, index):
        self.index = index
        self._lock = threading.Lock()
        self._chip_dir_version = {}

    def _history_dir(self, _):
        return self.index.parent

    def list_snapshots(self, _):
        return []

    def _open_index(self, _):
        return sqlite3.connect(str(self.index), isolation_level=None, timeout=0)

    def leaf_search(self, _, query, *, limit):
        c = self._open_index(None)
        try:
            return LI.search_paths(c, query, limit=limit)
        finally:
            c.close()


def test_shared_connection_replacement_and_lru_stress(tmp_path):
    """Real SQLite reads, physical index replacements and LRU churn together."""
    index = tmp_path / "index.sqlite"
    _make_index(index)
    others = [tmp_path / f"other_{i}.sqlite" for i in range(PHR._CONNS_MAX + 3)]
    for p in others:
        _make_index(p)
    hm = _History(index)
    expected = hm.leaf_search(None, "T1", limit=5)
    assert expected
    PHR.close_all()
    start = threading.Barrier(6)
    threads = _Threads()
    replacements = []
    overlap = []
    counts = [0] * 4
    versions = []

    def reader(n):
        start.wait(timeout=5)
        for _ in range(80):
            if n == 0:
                token = PHR.data_version(index)
                assert len(token) == 3 or token[0] in ("absent", "unreadable")
                if len(token) == 3:
                    versions.append(token)
            elif n == 1:
                assert PHR.hist_token(hm, index)[0] == str(index.parent)
            else:
                assert PHR.leaf_search(hm, index, "T1", limit=5) == expected
            counts[n] += 1
            time.sleep(0.001)

    def evict():
        start.wait(timeout=5)
        for _ in range(25):
            for p in others:
                token = PHR.data_version(p)
                # Shutdown or eviction can retire a borrowed entry before its
                # lock is acquired; that closed-handle error is a safe marker.
                assert len(token) == 3 or token[0] == "unreadable"
            with PHR._CONN_LOCK:
                assert len(PHR._CONNS) <= PHR._CONNS_MAX

    def replace():
        start.wait(timeout=5)
        for i in range(12):
            new = tmp_path / f"replacement_{i}.sqlite"
            _make_index(new)
            # Windows refuses to rename an index while SQLite owns its handle.
            # Retire the pool, then retry if a reader reopened it before replace.
            for _ in range(400):
                PHR.close_all()
                try:
                    os.replace(new, index)
                except PermissionError:
                    time.sleep(0.001)
                    continue
                replacements.append(PHR._file_identity(index))
                overlap.append(any(n < 80 for n in counts))
                break
            else:
                raise AssertionError("index replacement never acquired the file")

    try:
        for n in range(4):
            threads.start(lambda n=n: reader(n))
        threads.start(evict)
        threads.start(replace)
        threads.join(timeout=15)
        assert counts == [80] * 4
        assert versions and any(overlap), "readers never overlapped index replacement"
        assert len(replacements) == 12 and len(set(replacements)) == 12
        assert PHR.leaf_search(hm, index, "T1", limit=5) == expected
        with PHR._CONN_LOCK:
            assert len(PHR._CONNS) <= PHR._CONNS_MAX
    finally:
        PHR.close_all()
